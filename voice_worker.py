import os
import asyncio
import tempfile
from aiohttp import web
import discord
from gtts import gTTS
import imageio_ffmpeg

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "1554300458409922590"))
SECRET = os.environ["VOICE_WORKER_SECRET"]
PORT = int(os.environ.get("PORT", "10000"))

intents = discord.Intents.none()
intents.guilds = True
intents.voice_states = True
client = discord.Client(intents=intents)

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

    text = "Welcome to Meta. Meta Support is online. Use slash help to see everything I can do."
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

@client.event
async def on_ready():
    print(f"Voice worker logged in as {client.user}", flush=True)
    guild = client.get_guild(GUILD_ID)
    print(f"Voice worker guild: {guild}", flush=True)

async def main():
    await start_http()
    await client.start(TOKEN)

asyncio.run(main())
