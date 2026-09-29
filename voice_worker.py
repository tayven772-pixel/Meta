import os
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

intents = discord.Intents.none()
intents.guilds = True
intents.voice_states = True
class VoiceBot(commands.Bot):
    async def setup_hook(self):
        guild_obj = discord.Object(id=GUILD_ID)
        try:
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            print("Voice worker slash commands synced", flush=True)
        except Exception as exc:
            print("VOICE_COMMAND_SYNC_ERROR", repr(exc), flush=True)

client = VoiceBot(command_prefix="!", intents=intents)

async def play_intro(channel_id: int):
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        raise RuntimeError("Target guild not found")

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.VoiceChannel):
        raise RuntimeError("Voice channel not found")

    voice = guild.voice_client
    if voice and voice.channel.id != channel.id:
        await voice.move_to(channel)
    elif not voice:
        voice = await channel.connect(timeout=20, reconnect=False)

    text = "Welcome to Meta. Meta is a coding learning website where you can learn programming, practice with guided lessons and hints, build projects, track your progress, and use an AI coach to help you improve. You can learn web development, game development, Minecraft modding, and more."
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name

    try:
        await asyncio.to_thread(gTTS(text=text, lang="en").save, path)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        source = discord.FFmpegPCMAudio(path, executable=ffmpeg)

        done = asyncio.Event()
        error_holder = {"error": None}

        def after(error):
            error_holder["error"] = error
            client.loop.call_soon_threadsafe(done.set)

        voice.play(source, after=after)
        await asyncio.wait_for(done.wait(), timeout=30)
        if error_holder["error"]:
            raise error_holder["error"]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
        if guild.voice_client:
            await guild.voice_client.disconnect(force=True)

async def health(request):
    return web.json_response({
        "ok": True,
        "ready": client.is_ready(),
        "bot": str(client.user) if client.user else None,
        "guildId": str(GUILD_ID),
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
    if interaction.guild is None or interaction.guild.id != GUILD_ID:
        await interaction.response.send_message("This command only works in the Meta server.", ephemeral=True)
        return

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    voice_channel = member.voice.channel if member and member.voice and member.voice.channel else None

    if not isinstance(voice_channel, discord.VoiceChannel):
        await interaction.response.send_message(
            "Join a voice channel first, then run /intro again.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await play_intro(voice_channel.id)
        await interaction.followup.send("Voice intro played.", ephemeral=True)
    except Exception as exc:
        print("INTRO_COMMAND_ERROR", repr(exc), flush=True)
        await interaction.followup.send(f"I could not play the voice intro: {exc}", ephemeral=True)

@client.event
async def on_ready():
    print(f"Voice worker logged in as {client.user}", flush=True)
    guild = client.get_guild(GUILD_ID)
    print(f"Voice worker guild: {guild}", flush=True)

async def main():
    await start_http()
    await client.start(TOKEN)

asyncio.run(main())
