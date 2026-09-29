import os
import re
import json
import asyncio
import tempfile
import io
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands
from gtts import gTTS
import imageio_ffmpeg

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1554300458409922590"))
SECRET = os.environ["VOICE_WORKER_SECRET"]
PORT = int(os.environ.get("PORT", "10000"))
DEFAULT_INTRO_VOICE_CHANNEL_ID = int(os.environ.get("INTRO_VOICE_CHANNEL_ID", "1554300458976411733"))

INTRO_TEXT = (
    "Welcome to Meta. Meta is a coding learning website where you can learn programming, "
    "practice with guided lessons and hints, build projects, track your progress, and use "
    "an AI coach to help you improve. You can learn web development, game development, "
    "Minecraft modding, and more."
)
CONFIG_MARKER = "[META_INTRO_CONFIG]"
CONFIG_RE = re.compile(r"\\[META_INTRO_CONFIG\\]\\s+(\\{.*\\})")

intents = discord.Intents.none()
intents.guilds = True
intents.voice_states = True
intents.members = True

intro_config = {"voice_channel_id": DEFAULT_INTRO_VOICE_CHANNEL_ID, "role_id": None, "remove_role_id": None, "join_role_id": None, "ticket_category_id": None, "ticket_staff_role_id": None, "ticket_log_channel_id": None, "staff_review_channel_id": None, "staff_accept_role_id": None, "message_channel_id": None, "message_id": None}
intro_queue = asyncio.Queue()
audio_lock = asyncio.Lock()
queue_task = None
keepalive_task = None


class VoiceBot(commands.Bot):
    async def setup_hook(self):
        guild_obj = discord.Object(id=GUILD_ID)
        try:
            self.add_view(TicketPanelView())
            self.add_view(TicketControlsView())
            self.add_view(StaffApplicationPanelView())
            self.add_view(StaffApplicationDMView())
            self.add_view(StaffApplicationReviewView())
            await self.tree.sync(guild=guild_obj)
            print("Voice worker slash commands synced", flush=True)
        except Exception as exc:
            print("VOICE_COMMAND_SYNC_ERROR", repr(exc), flush=True)


client = VoiceBot(command_prefix="!", intents=intents)


def config_payload():
    return {
        "voice_channel_id": intro_config["voice_channel_id"],
        "role_id": intro_config["role_id"],
        "remove_role_id": intro_config["remove_role_id"],
        "join_role_id": intro_config["join_role_id"],
        "ticket_category_id": intro_config.get("ticket_category_id"),
        "ticket_staff_role_id": intro_config.get("ticket_staff_role_id"),
        "ticket_log_channel_id": intro_config.get("ticket_log_channel_id"),
        "staff_review_channel_id": intro_config.get("staff_review_channel_id"),
        "staff_accept_role_id": intro_config.get("staff_accept_role_id"),
    }


async def load_config(guild: discord.Guild):
    # Persist settings in a bot-authored Discord message so they survive Render restarts.
    # Search a deeper history and choose the newest valid config across all readable channels.
    newest = None

    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not perms.view_channel or not perms.read_message_history:
            continue

        try:
            async for message in channel.history(limit=500):
                if message.author.id != client.user.id:
                    continue

                match = CONFIG_RE.search(message.content or "")
                if not match:
                    continue

                try:
                    data = json.loads(match.group(1))
                except Exception:
                    continue

                if newest is None or message.created_at > newest["created_at"]:
                    newest = {
                        "created_at": message.created_at,
                        "channel_id": channel.id,
                        "message_id": message.id,
                        "data": data,
                    }

                # History is newest-first, so the first config in this channel is enough.
                break

        except Exception as exc:
            print(f"CONFIG_SCAN_ERROR channel={channel.id}: {exc!r}", flush=True)

    if newest is None:
        print("No saved intro config found; using defaults", flush=True)
        return

    data = newest["data"]
    intro_config.update({
        "voice_channel_id": int(data["voice_channel_id"]) if data.get("voice_channel_id") else DEFAULT_INTRO_VOICE_CHANNEL_ID,
        "role_id": int(data["role_id"]) if data.get("role_id") else None,
        "remove_role_id": int(data["remove_role_id"]) if data.get("remove_role_id") else None,
        "join_role_id": int(data["join_role_id"]) if data.get("join_role_id") else None,
        "ticket_category_id": int(data["ticket_category_id"]) if data.get("ticket_category_id") else None,
        "ticket_staff_role_id": int(data["ticket_staff_role_id"]) if data.get("ticket_staff_role_id") else None,
        "ticket_log_channel_id": int(data["ticket_log_channel_id"]) if data.get("ticket_log_channel_id") else None,
        "staff_review_channel_id": int(data["staff_review_channel_id"]) if data.get("staff_review_channel_id") else None,
        "staff_accept_role_id": int(data["staff_accept_role_id"]) if data.get("staff_accept_role_id") else None,
        "message_channel_id": newest["channel_id"],
        "message_id": newest["message_id"],
    })
    print(f"Loaded saved intro config: {config_payload()}", flush=True)


async def save_config(interaction: discord.Interaction):
    payload = json.dumps(config_payload(), separators=(",", ":"))
    content = (
        f"{CONFIG_MARKER} {payload}\n"
        "Meta intro configuration. Admins can change this with /introconfig."
    )

    existing_channel_id = intro_config.get("message_channel_id")
    existing_message_id = intro_config.get("message_id")
    if existing_channel_id and existing_message_id:
        existing_channel = interaction.guild.get_channel(existing_channel_id)
        if isinstance(existing_channel, discord.TextChannel):
            try:
                message = await existing_channel.fetch_message(existing_message_id)
                await message.edit(content=content)
                return
            except Exception:
                pass

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError("Run /introconfig in a text channel.")

    message = await channel.send(content)
    intro_config["message_channel_id"] = channel.id
    intro_config["message_id"] = message.id


async def ensure_intro_audio():
    path = os.path.join(tempfile.gettempdir(), "meta_intro.mp3")
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        await asyncio.to_thread(gTTS(text=INTRO_TEXT, lang="en").save, path)
    return path


async def ensure_voice_connection(guild: discord.Guild):
    channel_id = intro_config.get("voice_channel_id")
    if not channel_id:
        return None

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.VoiceChannel):
        return None

    voice = guild.voice_client
    if voice and voice.is_connected():
        if voice.channel.id != channel.id:
            await voice.move_to(channel)
        return voice

    if voice:
        try:
            await voice.disconnect(force=True)
        except Exception:
            pass

    print(f"Connecting permanently to intro VC {channel.name} ({channel.id})", flush=True)
    return await channel.connect(timeout=30, reconnect=True, self_deaf=True)


async def play_intro(channel_id: int):
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        raise RuntimeError("Target guild not found")

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.VoiceChannel):
        raise RuntimeError("Voice channel not found")

    async with audio_lock:
        voice = guild.voice_client
        if not voice or not voice.is_connected():
            voice = await channel.connect(timeout=30, reconnect=True, self_deaf=True)
        elif voice.channel.id != channel.id:
            await voice.move_to(channel)

        path = await ensure_intro_audio()
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        source = discord.FFmpegPCMAudio(path, executable=ffmpeg)

        done = asyncio.Event()
        error_holder = {"error": None}

        def after(error):
            error_holder["error"] = error
            client.loop.call_soon_threadsafe(done.set)

        voice.play(source, after=after)
        await asyncio.wait_for(done.wait(), timeout=45)
        if error_holder["error"]:
            raise error_holder["error"]


async def grant_completion_role(guild: discord.Guild, member_id: int):
    role_id = intro_config.get("role_id")
    channel_id = intro_config.get("voice_channel_id")
    if not role_id or not channel_id:
        return

    member = guild.get_member(member_id)
    if member is None:
        try:
            member = await guild.fetch_member(member_id)
        except Exception:
            return

    # Treat "listened to the whole thing" as staying in the configured VC for the full playback.
    if not member.voice or not member.voice.channel or member.voice.channel.id != channel_id:
        print(f"No role for {member}: left intro VC before audio finished", flush=True)
        return

    role = guild.get_role(role_id)
    if role is None:
        print(f"Configured intro role {role_id} no longer exists", flush=True)
        return

    remove_role_id = intro_config.get("remove_role_id")
    remove_role = guild.get_role(remove_role_id) if remove_role_id else None

    try:
        if remove_role and remove_role in member.roles:
            await member.remove_roles(remove_role, reason="Completed the full Meta voice intro")
            print(f"Removed intro role {remove_role.name} from {member}", flush=True)

        if role not in member.roles:
            await member.add_roles(role, reason="Completed the full Meta voice intro")
            print(f"Granted intro role {role.name} to {member}", flush=True)

        if member.voice and member.voice.channel and member.voice.channel.id == channel_id:
            await member.move_to(None, reason="Completed the full Meta voice intro")
            print(f"Disconnected {member} after intro completion", flush=True)
    except Exception as exc:
        print(f"ROLE_OR_DISCONNECT_ERROR member={member_id} give={role_id} remove={remove_role_id}: {exc!r}", flush=True)


async def intro_queue_worker():
    while True:
        member_id = await intro_queue.get()
        try:
            guild = client.get_guild(GUILD_ID)
            channel_id = intro_config.get("voice_channel_id")
            if guild and channel_id:
                member = guild.get_member(member_id)
                if member and not member.bot and member.voice and member.voice.channel and member.voice.channel.id == channel_id:
                    print(f"Playing automatic intro for {member}", flush=True)
                    await play_intro(channel_id)
                    await grant_completion_role(guild, member_id)
        except Exception as exc:
            print(f"AUTO_INTRO_ERROR member={member_id}: {exc!r}", flush=True)
        finally:
            intro_queue.task_done()


async def voice_keepalive_worker():
    while True:
        try:
            guild = client.get_guild(GUILD_ID)
            if guild and intro_config.get("voice_channel_id"):
                await ensure_voice_connection(guild)
        except Exception as exc:
            print(f"VOICE_KEEPALIVE_ERROR: {exc!r}", flush=True)
        await asyncio.sleep(20)


async def health(request):
    guild = client.get_guild(GUILD_ID)
    voice = guild.voice_client if guild else None
    return web.json_response({
        "ok": True,
        "ready": client.is_ready(),
        "bot": str(client.user) if client.user else None,
        "guildId": str(GUILD_ID),
        "voiceConnected": bool(voice and voice.is_connected()),
        "voiceChannelId": str(voice.channel.id) if voice and voice.is_connected() else None,
        "configuredRoleId": str(intro_config["role_id"]) if intro_config["role_id"] else None,
        "configuredRemoveRoleId": str(intro_config["remove_role_id"]) if intro_config["remove_role_id"] else None,
        "configuredJoinRoleId": str(intro_config["join_role_id"]) if intro_config["join_role_id"] else None,
    })


async def intro(request):
    if request.headers.get("x-voice-secret") != SECRET:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    body = await request.json()
    channel_id = int(body["channelId"])

    try:
        await play_intro(channel_id)
        return web.json_response({"ok": True, "played": True, "channelId": str(channel_id)})
    except Exception as exc:
        print("VOICE_WORKER_ERROR", repr(exc), flush=True)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def start_http():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/intro", intro)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Voice worker health server listening on {PORT}", flush=True)



def safe_ticket_name(value: str):
    cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:70] or "member"


async def create_ticket_channel(interaction: discord.Interaction, ticket_type: str, title: str, fields: list[tuple[str, str]]):
    guild = interaction.guild
    if guild is None:
        return

    category = guild.get_channel(intro_config.get("ticket_category_id")) if intro_config.get("ticket_category_id") else None
    if not isinstance(category, discord.CategoryChannel):
        category = interaction.channel.category if isinstance(interaction.channel, discord.TextChannel) else None

    staff_role = guild.get_role(intro_config.get("ticket_staff_role_id")) if intro_config.get("ticket_staff_role_id") else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)

    existing = discord.utils.find(
        lambda ch: isinstance(ch, discord.TextChannel) and ch.topic and f"ticket-owner:{interaction.user.id}" in ch.topic,
        guild.text_channels
    )
    if existing:
        await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
        return

    channel = await guild.create_text_channel(
        name=f"{ticket_type}-{safe_ticket_name(interaction.user.display_name)}",
        category=category,
        overwrites=overwrites,
        topic=f"ticket-owner:{interaction.user.id} type:{ticket_type}",
        reason=f"Meta Support ticket created by {interaction.user}",
    )

    embed = discord.Embed(
        title=title,
        description=f"Ticket opened by {interaction.user.mention}. A staff member will help you here.",
        color=discord.Color.blurple(),
    )
    for name, value in fields:
        embed.add_field(name=name, value=value or "Not provided", inline=False)
    embed.set_footer(text="Meta Support • Use the buttons below to manage this ticket")

    await channel.send(
        content=(staff_role.mention if staff_role else None),
        embed=embed,
        view=TicketControlsView(),
        allowed_mentions=discord.AllowedMentions(roles=True, users=False),
    )
    await interaction.response.send_message(f"Your ticket was created: {channel.mention}", ephemeral=True)


class GeneralSupportModal(discord.ui.Modal, title="General Support"):
    issue = discord.ui.TextInput(label="What do you need help with?", style=discord.TextStyle.paragraph, max_length=1000)
    extra = discord.ui.TextInput(label="Additional information", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket_channel(interaction, "support", "General Support", [
            ("Issue", str(self.issue)),
            ("Additional information", str(self.extra)),
        ])


class ReportMemberModal(discord.ui.Modal, title="Report a Member"):
    member = discord.ui.TextInput(label="Member username or ID", max_length=100)
    reason = discord.ui.TextInput(label="What happened?", style=discord.TextStyle.paragraph, max_length=1000)
    evidence = discord.ui.TextInput(label="Evidence / links", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket_channel(interaction, "report", "Report a Member", [
            ("Member", str(self.member)),
            ("Report", str(self.reason)),
            ("Evidence", str(self.evidence)),
        ])


class PartnershipModal(discord.ui.Modal, title="Partnership"):
    community = discord.ui.TextInput(label="Server/community name", max_length=100)
    invite = discord.ui.TextInput(label="Invite or community link", max_length=300)
    members = discord.ui.TextInput(label="Member count / community size", max_length=100)
    partnership = discord.ui.TextInput(label="What kind of partnership are you looking for?", style=discord.TextStyle.paragraph, max_length=1000)
    extra = discord.ui.TextInput(label="Additional information", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket_channel(interaction, "partnership", "Partnership", [
            ("Server/community name", str(self.community)),
            ("Invite or community link", str(self.invite)),
            ("Member count / community size", str(self.members)),
            ("Partnership request", str(self.partnership)),
            ("Additional information", str(self.extra)),
        ])


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Support", emoji="🛟", style=discord.ButtonStyle.primary, custom_id="meta_ticket_general")
    async def general(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GeneralSupportModal())

    @discord.ui.button(label="Report a Member", emoji="🛡️", style=discord.ButtonStyle.danger, custom_id="meta_ticket_report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReportMemberModal())

    @discord.ui.button(label="Partnership", emoji="🤝", style=discord.ButtonStyle.secondary, custom_id="meta_ticket_partnership")
    async def partnership(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PartnershipModal())


async def ticket_transcript(channel: discord.TextChannel):
    lines = []
    async for message in channel.history(limit=None, oldest_first=True):
        created = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = message.content or ""
        if message.attachments:
            content += " " + " ".join(a.url for a in message.attachments)
        lines.append(f"[{created}] {message.author} ({message.author.id}): {content}")
    return "\n".join(lines) or "No messages."


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="meta_ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        staff_role_id = intro_config.get("ticket_staff_role_id")
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if staff_role_id and member and all(r.id != staff_role_id for r in member.roles) and not member.guild_permissions.manage_channels:
            await interaction.response.send_message("Only support staff can claim tickets.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ {interaction.user.mention} claimed this ticket.")

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="meta_ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not channel.topic or "ticket-owner:" not in channel.topic:
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
            return

        owner_match = re.search(r"ticket-owner:(\d+)", channel.topic)
        owner_id = int(owner_match.group(1)) if owner_match else None
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        is_owner = owner_id == interaction.user.id
        is_staff = bool(member and (member.guild_permissions.manage_channels or (
            intro_config.get("ticket_staff_role_id") and any(r.id == intro_config["ticket_staff_role_id"] for r in member.roles)
        )))
        if not (is_owner or is_staff):
            await interaction.response.send_message("Only the ticket owner or support staff can close this ticket.", ephemeral=True)
            return

        await interaction.response.send_message("Closing ticket and saving transcript…", ephemeral=True)
        transcript = await ticket_transcript(channel)
        log_channel = interaction.guild.get_channel(intro_config.get("ticket_log_channel_id")) if intro_config.get("ticket_log_channel_id") else None
        if isinstance(log_channel, discord.TextChannel):
            data = io.BytesIO(transcript.encode("utf-8"))
            await log_channel.send(
                content=f"Transcript for **#{channel.name}** closed by {interaction.user.mention}",
                file=discord.File(data, filename=f"{channel.name}-transcript.txt"),
            )
        await asyncio.sleep(2)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")


@client.tree.command(name="ticketconfig", description="Configure the Meta ticket system")
@app_commands.describe(
    category="Category where ticket channels should be created",
    staff_role="Role allowed to view and manage tickets",
    log_channel="Channel where closed-ticket transcripts are sent",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ticketconfig_command(
    interaction: discord.Interaction,
    category: discord.CategoryChannel,
    staff_role: discord.Role,
    log_channel: discord.TextChannel,
):
    intro_config["ticket_category_id"] = category.id
    intro_config["ticket_staff_role_id"] = staff_role.id
    intro_config["ticket_log_channel_id"] = log_channel.id
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await save_config(interaction)
        await interaction.followup.send(
            f"Ticket system saved. Tickets go in **{category.name}**, staff role is **{staff_role.name}**, and transcripts go to {log_channel.mention}.",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(f"I couldn't save the ticket setup: {exc}", ephemeral=True)


@client.tree.command(name="ticketpanel", description="Post the Community Support ticket panel")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ticketpanel_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="💠 Community Support",
        description="Welcome to Meta community support. Choose the option that best matches what you need so the right staff members can help you privately.",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="🛟 General Support",
        value="Questions, server help, access issues, or anything that does not fit another category.",
        inline=False,
    )
    embed.add_field(
        name="🛡️ Report a Member",
        value="Privately report behavior that may break community rules. Include evidence whenever possible.",
        inline=False,
    )
    embed.add_field(
        name="🤝 Partnership",
        value="Propose a collaboration between Meta and another server, group, or community project.",
        inline=False,
    )
    embed.set_footer(text="Please choose one category and include as much detail as possible.")
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Community Support panel posted.", ephemeral=True)


@client.tree.command(name="help", description="Show Meta Support commands")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        "**Meta Support**\n"
        "/intro — play the website intro\n"
        "/introconfig — configure the intro VC and roles\n"
        "/ticketpanel — post the support ticket panel\n"
        "/ticketconfig — configure ticket category, staff role, and transcript log\n"
        "/serverstats — show live server stats",
        ephemeral=True,
    )


@client.tree.command(name="serverstats", description="Show live server stats")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def serverstats_command(interaction: discord.Interaction):
    guild = interaction.guild
    await interaction.response.send_message(
        f"**{guild.name}**\nMembers: **{guild.member_count}**\nChannels: **{len(guild.channels)}**\nRoles: **{len(guild.roles)}**"
    )



def application_user_id_from_message(message: discord.Message):
    if not message.embeds:
        return None
    footer = message.embeds[0].footer.text or ""
    match = re.search(r"Applicant ID: (\\d+)", footer)
    return int(match.group(1)) if match else None


class StaffApplicationModal(discord.ui.Modal, title="Staff Application"):
    age = discord.ui.TextInput(label="How old are you?", max_length=30)
    timezone = discord.ui.TextInput(label="What is your timezone?", max_length=80)
    experience = discord.ui.TextInput(
        label="Previous staff/moderation experience",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    reason = discord.ui.TextInput(
        label="Why do you want to be staff at Meta?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )
    activity = discord.ui.TextInput(
        label="How active can you be each week?",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = client.get_guild(GUILD_ID)
        if guild is None:
            await interaction.response.send_message("The Meta server is unavailable right now.", ephemeral=True)
            return

        review_id = intro_config.get("staff_review_channel_id")
        review_channel = guild.get_channel(review_id) if review_id else None
        if not isinstance(review_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Staff applications are not configured yet. Please contact an administrator.",
                ephemeral=True,
            )
            return

        # Prevent duplicate pending applications by scanning recent review messages.
        try:
            async for message in review_channel.history(limit=100):
                if message.author.id != client.user.id or not message.embeds:
                    continue
                if application_user_id_from_message(message) == interaction.user.id:
                    title = message.embeds[0].title or ""
                    if "Staff Application" in title and "ACCEPTED" not in title and "DENIED" not in title:
                        await interaction.response.send_message(
                            "You already have a staff application waiting for review.",
                            ephemeral=True,
                        )
                        return
        except Exception:
            pass

        embed = discord.Embed(
            title="📋 New Staff Application",
            description=f"Application submitted by {interaction.user.mention}",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Age", value=str(self.age), inline=False)
        embed.add_field(name="Timezone", value=str(self.timezone), inline=False)
        embed.add_field(name="Previous experience", value=str(self.experience), inline=False)
        embed.add_field(name="Why they want staff", value=str(self.reason), inline=False)
        embed.add_field(name="Weekly activity", value=str(self.activity), inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Applicant ID: {interaction.user.id}")

        await review_channel.send(embed=embed, view=StaffApplicationReviewView())
        await interaction.response.send_message(
            "✅ Your staff application was submitted. Staff will review it privately.",
            ephemeral=True,
        )



class StaffApplicationDMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Start Staff Application",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="meta_staff_dm_start",
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StaffApplicationModal())


class StaffApplicationPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Apply for Staff",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="meta_staff_apply",
    )
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        accept_role_id = intro_config.get("staff_accept_role_id")
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if accept_role_id and member and any(r.id == accept_role_id for r in member.roles):
            await interaction.response.send_message("You already have the configured staff role.", ephemeral=True)
            return

        dm_embed = discord.Embed(
            title="📝 Meta Staff Application",
            description=(
                "Your application is private. Press the button below to begin. "
                "Your answers will only be sent to the configured staff review channel."
            ),
            color=discord.Color.blurple(),
        )
        dm_embed.set_footer(text="Meta Staff Team")

        try:
            await interaction.user.send(embed=dm_embed, view=StaffApplicationDMView())
            await interaction.response.send_message(
                "📩 I sent the staff application to your DMs.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't DM you. Enable DMs from server members, then press **Apply for Staff** again.",
                ephemeral=True,
            )


class StaffApplicationReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def staff_allowed(self, interaction: discord.Interaction):
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if not member:
            return False
        ticket_staff_id = intro_config.get("ticket_staff_role_id")
        return member.guild_permissions.manage_guild or (
            ticket_staff_id and any(r.id == ticket_staff_id for r in member.roles)
        )

    @discord.ui.button(
        label="Accept",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="meta_staff_accept",
    )
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.staff_allowed(interaction):
            await interaction.response.send_message("Only staff can review applications.", ephemeral=True)
            return

        applicant_id = application_user_id_from_message(interaction.message)
        if not applicant_id:
            await interaction.response.send_message("I couldn't identify this applicant.", ephemeral=True)
            return

        try:
            applicant = interaction.guild.get_member(applicant_id) or await interaction.guild.fetch_member(applicant_id)
        except Exception:
            applicant = None

        role_id = intro_config.get("staff_accept_role_id")
        role = interaction.guild.get_role(role_id) if role_id else None
        if applicant and role:
            try:
                await applicant.add_roles(role, reason=f"Staff application accepted by {interaction.user}")
            except Exception as exc:
                await interaction.response.send_message(
                    f"I couldn't give the staff role: {exc}",
                    ephemeral=True,
                )
                return

        embed = interaction.message.embeds[0].copy()
        embed.title = "✅ Staff Application — ACCEPTED"
        embed.color = discord.Color.green()
        embed.add_field(name="Reviewed by", value=interaction.user.mention, inline=False)

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        if applicant:
            try:
                await applicant.send(
                    f"✅ Your staff application for **{interaction.guild.name}** was accepted."
                )
            except Exception:
                pass

    @discord.ui.button(
        label="Deny",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="meta_staff_deny",
    )
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.staff_allowed(interaction):
            await interaction.response.send_message("Only staff can review applications.", ephemeral=True)
            return

        applicant_id = application_user_id_from_message(interaction.message)
        if not applicant_id:
            await interaction.response.send_message("I couldn't identify this applicant.", ephemeral=True)
            return

        embed = interaction.message.embeds[0].copy()
        embed.title = "❌ Staff Application — DENIED"
        embed.color = discord.Color.red()
        embed.add_field(name="Reviewed by", value=interaction.user.mention, inline=False)

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        try:
            applicant = interaction.guild.get_member(applicant_id) or await interaction.guild.fetch_member(applicant_id)
            await applicant.send(
                f"Your staff application for **{interaction.guild.name}** was not accepted this time."
            )
        except Exception:
            pass


@client.tree.command(name="staffconfig", description="Configure staff application reviews and accepted role")
@app_commands.describe(
    review_channel="Private channel where staff applications are sent",
    accepted_role="Role given when an application is accepted",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def staffconfig_command(
    interaction: discord.Interaction,
    review_channel: discord.TextChannel,
    accepted_role: discord.Role,
):
    if accepted_role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "I can't give that role because it is at or above my highest role. Move it below **Meta Support**.",
            ephemeral=True,
        )
        return

    intro_config["staff_review_channel_id"] = review_channel.id
    intro_config["staff_accept_role_id"] = accepted_role.id

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await save_config(interaction)
        await interaction.followup.send(
            f"Staff applications saved. Reviews go to {review_channel.mention}, and accepted applicants receive **{accepted_role.name}**.",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(f"I couldn't save the staff application setup: {exc}", ephemeral=True)


@client.tree.command(name="staffpanel", description="Post the staff application panel")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def staffpanel_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📝 Staff Applications",
        description=(
            "Interested in helping the Meta community? Apply to join the staff team below.\n\n"
            "Please answer every question carefully and truthfully. Submitting an application "
            "does not guarantee acceptance."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Before applying",
        value=(
            "• Be respectful and mature\n"
            "• Be active in the community\n"
            "• Be willing to help members\n"
            "• Do not repeatedly ask staff for an application result"
        ),
        inline=False,
    )
    embed.set_footer(text="Meta Staff Team • Applications are reviewed privately")

    await interaction.channel.send(embed=embed, view=StaffApplicationPanelView())
    await interaction.response.send_message("Staff application panel posted.", ephemeral=True)


@client.tree.command(name="intro", description="Play the Meta voice intro")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def intro_command(interaction: discord.Interaction):
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    configured_channel_id = intro_config.get("voice_channel_id")

    voice_channel = member.voice.channel if member and member.voice and member.voice.channel else None
    if not isinstance(voice_channel, discord.VoiceChannel):
        await interaction.response.send_message("Join the intro voice channel first.", ephemeral=True)
        return

    if configured_channel_id and voice_channel.id != configured_channel_id:
        configured = interaction.guild.get_channel(configured_channel_id)
        name = configured.name if configured else "the configured intro VC"
        await interaction.response.send_message(f"Join **{name}** first.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await play_intro(voice_channel.id)
        await grant_completion_role(interaction.guild, interaction.user.id)
        await interaction.followup.send("Voice intro completed.", ephemeral=True)
    except Exception as exc:
        print("INTRO_COMMAND_ERROR", repr(exc), flush=True)
        await interaction.followup.send(f"I could not play the voice intro: {exc}", ephemeral=True)


@client.tree.command(name="introconfig", description="Configure intro VC and server join/completion roles")
@app_commands.describe(
    voice_channel="Voice channel Meta Support should stay in",
    role="Role given after a member stays for the full intro",
    remove_role="Optional role removed after a member finishes the intro",
    join_role="Optional role automatically given when someone joins the server",
)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def introconfig_command(
    interaction: discord.Interaction,
    voice_channel: discord.VoiceChannel,
    role: discord.Role,
    remove_role: discord.Role | None = None,
    join_role: discord.Role | None = None,
):
    await interaction.response.defer(ephemeral=True, thinking=True)

    me = interaction.guild.me
    if role >= me.top_role:
        await interaction.followup.send(
            "I can't give that role because it is at or above my highest role. Move the reward role below **Meta Support**.",
            ephemeral=True,
        )
        return

    if remove_role and remove_role >= me.top_role:
        await interaction.followup.send(
            "I can't remove that role because it is at or above my highest role. Move it below **Meta Support**.",
            ephemeral=True,
        )
        return

    if join_role and join_role >= me.top_role:
        await interaction.followup.send(
            "I can't give the join role because it is at or above my highest role. Move it below **Meta Support**.",
            ephemeral=True,
        )
        return

    intro_config["voice_channel_id"] = voice_channel.id
    intro_config["role_id"] = role.id
    intro_config["remove_role_id"] = remove_role.id if remove_role else None
    intro_config["join_role_id"] = join_role.id if join_role else None

    try:
        await save_config(interaction)
        await ensure_voice_connection(interaction.guild)
        remove_text = f" and remove **{remove_role.name}**" if remove_role else ""
        join_text = f" New members will receive **{join_role.name}** when they join." if join_role else ""
        await interaction.followup.send(
            f"Intro setup saved. I'll stay in **{voice_channel.name}**, give **{role.name}**{remove_text} after someone remains for the entire intro.{join_text}",
            ephemeral=True,
        )
    except Exception as exc:
        print("INTRO_CONFIG_ERROR", repr(exc), flush=True)
        await interaction.followup.send(f"I couldn't save the intro setup: {exc}", ephemeral=True)


@introconfig_command.error
async def introconfig_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("You need **Manage Server** to change the intro setup.", ephemeral=True)
        else:
            await interaction.response.send_message("You need **Manage Server** to change the intro setup.", ephemeral=True)
    else:
        print("INTRO_CONFIG_COMMAND_ERROR", repr(error), flush=True)



@client.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID or member.bot:
        return

    role_id = intro_config.get("join_role_id")
    if not role_id:
        return

    role = member.guild.get_role(role_id)
    if role is None:
        print(f"Configured join role {role_id} no longer exists", flush=True)
        return

    try:
        await member.add_roles(role, reason="Automatic Meta server join role")
        print(f"Granted join role {role.name} to {member}", flush=True)
    except Exception as exc:
        print(f"JOIN_ROLE_ERROR member={member.id} role={role_id}: {exc!r}", flush=True)


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    channel_id = intro_config.get("voice_channel_id")
    if not channel_id:
        return

    # If the bot gets moved/disconnected, the keepalive worker will put it back.
    if client.user and member.id == client.user.id:
        return

    joined_intro = (
        after.channel is not None
        and after.channel.id == channel_id
        and (before.channel is None or before.channel.id != channel_id)
    )
    if joined_intro and not member.bot:
        await intro_queue.put(member.id)


@client.event
async def on_ready():
    global queue_task, keepalive_task

    print(f"Voice worker logged in as {client.user}", flush=True)
    guild = client.get_guild(GUILD_ID)
    print(f"Voice worker guild: {guild}", flush=True)

    if guild:
        await load_config(guild)
        if intro_config.get("voice_channel_id"):
            try:
                await ensure_voice_connection(guild)
            except Exception as exc:
                print("INITIAL_VOICE_CONNECT_ERROR", repr(exc), flush=True)

    if queue_task is None or queue_task.done():
        queue_task = asyncio.create_task(intro_queue_worker())
    if keepalive_task is None or keepalive_task.done():
        keepalive_task = asyncio.create_task(voice_keepalive_worker())


async def main():
    await start_http()
    await client.start(TOKEN)


asyncio.run(main())
