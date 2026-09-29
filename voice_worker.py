import os
import re
import json
import asyncio
import tempfile
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

intro_config = {"voice_channel_id": DEFAULT_INTRO_VOICE_CHANNEL_ID, "role_id": None, "remove_role_id": None, "join_role_id": None, "message_channel_id": None, "message_id": None}
intro_queue = asyncio.Queue()
audio_lock = asyncio.Lock()
queue_task = None
keepalive_task = None


class VoiceBot(commands.Bot):
    async def setup_hook(self):
        guild_obj = discord.Object(id=GUILD_ID)
        try:
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
    }


async def load_config(guild: discord.Guild):
    # Search recent messages for the most recent config marker written by this bot.
    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not perms.view_channel or not perms.read_message_history:
            continue
        try:
            async for message in channel.history(limit=50):
                if message.author.id != client.user.id:
                    continue
                match = CONFIG_RE.search(message.content or "")
                if not match:
                    continue
                data = json.loads(match.group(1))
                intro_config.update({
                    "voice_channel_id": int(data["voice_channel_id"]) if data.get("voice_channel_id") else None,
                    "role_id": int(data["role_id"]) if data.get("role_id") else None,
                    "remove_role_id": int(data["remove_role_id"]) if data.get("remove_role_id") else None,
                    "join_role_id": int(data["join_role_id"]) if data.get("join_role_id") else None,
                    "message_channel_id": channel.id,
                    "message_id": message.id,
                })
                print(f"Loaded intro config: {config_payload()}", flush=True)
                return
        except Exception as exc:
            print(f"CONFIG_SCAN_ERROR channel={channel.id}: {exc!r}", flush=True)


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
    except Exception as exc:
        print(f"ROLE_UPDATE_ERROR member={member_id} give={role_id} remove={remove_role_id}: {exc!r}", flush=True)


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
