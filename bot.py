import os, asyncio
import aiohttp 
import discord
import re
import time
import aiosqlite
import aiohttp
import json
import webbrowser
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from aiohttp import web
from pathlib import Path
from datetime import timedelta
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
TOKEN = os.getenv("DISCORD_TOKEN")

print("SPOTIFY_REDIRECT_URI =", os.getenv("SPOTIFY_REDIRECT_URI"))
print("SPOTIPY_REDIRECT_URI =", os.getenv("SPOTIPY_REDIRECT_URI"))


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

#---prefix command---
@bot.command()
async def ping(ctx):
    """Replies with ping + latency."""
    await ctx.send(f"Ping! {round(bot.latency * 1000)}ms")

#---slash command---
@bot.tree.command(name="hello", description="Say Hello")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hey {interaction.user.mention} 👋")

# ---auto-reply---
_last_xp: dict[int, float] = {}
XP_AMOUNT = 15
XP_COOLDOWN = 30

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    #---XP award iwth cooldown---
    now = time.time()
    last = _last_xp.get(message.author.id, 0)
    if now - last >= XP_COOLDOWN and message.content.strip():
        _last_xp[message.author.id] = now
        new_xp, new_lvl, ding = await add_xp(message.author.id, XP_AMOUNT)
        if ding:
            try:
                await message.channel.send(f"🎉 {message.author.mention} leveled up to **Level {new_lvl}**!")
            except discord.Forbidden:
                pass
    
    if message.content.lower().startswith("hi bot"):
        await message.channel.send("Howya doin!")

    #important: let commands still work
    await bot.process_commands(message)

#---shutdown discord---
@bot.command()
async def shutdown(ctx):
    if ctx.author.id == 1195379328372965520:
        await ctx.send("Shutting down...")
        await bot.close()
    else:
        await ctx.send("Nah, you don't have permission.")


#---Reminders/timer---

DURATION_RE = re.compile(
    r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?",
    re.IGNORECASE,
)

def parse_duration(s: str) -> int:
    s = s.strip()
    m = DURATION_RE.fullmatch(s)
    if not m:
        raise ValueError("Use formats like '10m', '1h30m', '45s', '1d2h'")
    d, h, m_, s_ = (int(x) if x else 0 for x in m.groups())
    total = d*86400 + h*3600 + m_*60 + s_
    if total <= 0:
        raise ValueError("Duration must be more than 0 seconds")
    return total

running_reminders: dict[int, list[asyncio.Task]] = {}

@bot.tree.command(name="remindme", description="DMs you after a duration with a message")
async def remindme(interaction: discord.Interaction, duration: str, message: str):
    try:
        seconds = parse_duration(duration)
    except ValueError as e:
        await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        return
    
    
    td = str(timedelta(seconds=seconds))
    await interaction.response.send_message(
        f"⏰ Okay {interaction.user.mention} - I'll remind you in **{td}**: \"{message}\".", 
        ephemeral=True,
    )
    
    async def _job():
        try:
            await asyncio.sleep(seconds)
            #Try DM first
            try:
                await interaction.user.send(f"⏰ Reminder: {message}")
            except discord.Forbidden:
                await interaction.channel.send(f"{interaction.user.mention} ⏰ Reminder: {message}")
        finally:
            #cleanup finished task
            tasks = running_reminders.get(interaction.user.id, [])
            running_reminders[interaction.user.id] = [t for t in tasks if not t.done()]
    
    task = asyncio.create_task(_job())

    running_reminders.setdefault(interaction.user.id, []).append(task)

@bot.tree.command(name="timer", description="Simple timer (seconds)")
async def timer(interaction: discord.Interaction, seconds: int):
    if seconds <= 0:
        await interaction.response.send_messsage("❌ Seconds must be > 0.", ephemeral= True)
        return
    await interaction.response.send_message(f"⏳ Timer started for **{seconds}**s.", ephemeral=True)

    async def _job():
        await asyncio.sleep(seconds)
        try:
            await interaction.user.send(f"⏳ Timer Done ({seconds}s)!")
        except discord.Forbidden:
            await interaction.channel.send(f"{interaction.user.mention} ⏳ Timer done ({seconds}s)!")
    
    asyncio.create_task(_job())

@bot.tree.command(name="reminders", description="List your active reminders (this session)")
async def reminders(interaction: discord.Interaction):
    tasks = running_reminders.get(interaction.user.id, [])
    live = [t for t in tasks if not t.done()]
    await interaction.response.send_message(
        f"⏳ You have **{len(live)}** active reminder(s) this session", 
        ephemeral=True,
    )

#---Bulk Clear---
@bot.tree.command(name="clear", description="Bulk delete recent messages in this channel")
@app_commands.describe(amount="Number of messages to delete (max 100)")
async def clear(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You don’t have permission to manage messages.", ephemeral=True)
        return
    bot_member = interaction.guild.me  # the bot as a Member
    if not interaction.channel.permissions_for(bot_member).manage_messages:
        await interaction.response.send_message("❌ I’m missing **Manage Messages** in this channel.", ephemeral=True)
        return

    if amount <= 0 or amount > 100:
        await interaction.response.send_message("❌ Amount must be between **1** and **100**.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        deleted = await interaction.channel.purge(limit=amount)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don’t have permission to delete messages here.", ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Discord error: {e}", ephemeral=True)
        return

    count = len(deleted)
    await interaction.followup.send(f"🧹 Deleted **{count}** messages in {interaction.channel.mention}.", ephemeral=True)

    try:
        msg = await interaction.channel.send(f"🧹 {interaction.user.mention} deleted **{count}** messages.")
        await asyncio.sleep(5)
        await msg.delete()
    except discord.Forbidden:
        pass

#---XP Levels---
def xp_to_level(xp: int) -> int:
    return int((xp **0.5) // 10)  # 10k xp = level 10

async def add_xp(user_id: int, amount: int) -> tuple[int, int, bool]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT xp FROM xp WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        old_xp = row[0] if row else 0
        old_lvl = xp_to_level(old_xp)

        new_xp = old_xp + amount
        new_lvl = xp_to_level(new_xp)

        if row:
            await db.execute("UPDATE xp SET xp=? WHERE user_id=?", (new_xp, user_id))
        else:
            await db.execute("INSERT INTO xp(user_id, xp) VALUES(?,?)", (user_id, new_xp))
        await db.commit()

    return new_xp, new_lvl, (new_lvl > old_lvl)

#---Rank---
@bot.tree.command(name="rank", description="Show your XP and level")
async def rank(interaction: discord.Interaction, user: discord.User | None = None):
    user = user or interaction.user
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT xp FROM xp WHERE user_id=?", (user.id,))
        row = await cur.fetchone()
    xp = row[0] if row else 0
    lvl = xp_to_level(xp)
    await interaction.response.send_message(f"🏅 {user.mention} — **Level {lvl}**, **{xp} XP**", ephemeral=False)

#---Leaderboard---
@bot.tree.command(name="leaderboard", description="Top 10 by XP")
async def leaderboard(interaction: discord.Interaction):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, xp FROM xp ORDER BY xp DESC LIMIT 10")
        rows = await cur.fetchall()
    
    lines = []
    for i, (uid, xp) in enumerate(rows, start=1):
        lvl = xp_to_level(xp)
        user = interaction.guild.get_member(uid) or await interaction.client.fetch_user(uid)
        name = user.mention if isinstance(user, discord.Member) else f"<@{uid}>"
        lines.append(f"**{i}.** {name} - L{lvl} * {xp} XP")

    msg = "\n".join(lines) if lines else "No data yet."
    await interaction.response.send_message(f"📜 **Leaderboard**\n{msg}")

#---Weather API---
OWM_API_KEY = os.getenv("OWM_API_KEY")
OWM_UNITS = os.getenv("OWM_UNITS", "metric")

@bot.tree.command(name="weather", description="Get current weather by city")
@app_commands.describe(city="e.g. Dublin, London, Paris")
async def weather(interaction: discord.Interaction, city: str):
    if not OWM_API_KEY:
        await interaction.response.send_message("❌ OWM_API_KEY missing in .env", ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True, ephemeral=False)

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": OWM_API_KEY,
        "units": OWM_UNITS,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status == 404:
                    await interaction.followup.send(f"🤔 Couldn't find a place called **{city}**.")
                    return
                if resp.status != 200:
                    txt = await resp.text()
                    await interaction.followup.send(f"⚠️ OWM error {resp.status}: {txt[:200]}")
                    return
                data = await resp.json()
    except Exception as e:
        await interaction.followup.send(f"⚠️ Request failed: {e}")
        return
    
    #parse
    name = data.get("name", city)
    sysc = data.get("sys", {})
    country = sysc.get("country", "")
    main = data.get("main", {})
    weather0 = (data.get("weather") or [{}])[0]
    wind = data.get("wind", {})
    temp = main.get("temp")
    feels = main.get("feels_like")
    desc = weather0.get("description", "n/a").title()
    humidity = main.get("humidity")
    speed = wind.get("speed")

    unit_temp = "°C" if OWM_UNITS == "metric" else ("°F" if OWM_UNITS == "imperial" else "K")
    unit_wind = "m/s" if OWM_UNITS != "imperial" else "mph"

    embed = discord.Embed(
        title=f"🌤️ {name}, {country}",
        description=f"**{desc}**",
        color=discord.Color.blurple(),
    )
    if temp is not None:
        embed.add_field(name="Temp", value=f"{temp:.1f}{unit_temp}")
    if feels is not None:
        embed.add_field(name="Feels Like", value=f"{feels:.1f}{unit_temp}")
    if humidity is not None:
        embed.add_field(name="Humidity", value=f"{humidity}%")
    if speed is not None:
        embed.add_field(name="Wind", value=f"{speed} {unit_wind}")
    embed.set_footer(text=f"Units: {OWM_UNITS}")

    await interaction.followup.send(embed=embed)

#---Spotify---

SPOTIFY_SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"
SPOTIFY_CACHE = str(Path(__file__).with_name("spotify_cache.json"))

def _spotify_oauth():
    return SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
        scope=SPOTIFY_SCOPES,
        cache_path=SPOTIFY_CACHE,
        open_browser=False,  
        show_dialog=True,  
    )

async def _spotify_callback_server(oauth: SpotifyOAuth):
    """
    Tiny local HTTP server to receive /callback and finish the OAuth flow.
    Shuts itself down once tokens are saved to cache.
    """
    got_token = asyncio.Event()

    async def handle_callback(request):
        code = request.rel_url.query.get("code")
        if not code:
            return web.Response(text="Missing ?code", status=400)
        oauth.get_access_token(code, as_dict=False)
        got_token.set()
        return web.Response(text="✅ Spotify linked. You can close this tab.")

    app = web.Application()
    app.router.add_get("/callback", handle_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8888)
    await site.start()

    try:
        try:
            await asyncio.wait_for(got_token.wait(), timeout=120)
        except asyncio.TimeoutError:
            pass
    finally:
        await runner.cleanup()

def _spotify_client() -> Spotify | None:
    oauth = _spotify_oauth()
    token = oauth.get_cached_token()
    if not token:
        return None
    return Spotify(auth_manager=oauth)  

# ----- /spotify login -----
@bot.tree.command(name="slog", description="Link your Spotify (owner-only)")
async def spotify_login(interaction: discord.Interaction):
    if interaction.user.id != 1195379328372965520:
        await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        return

    oauth = _spotify_oauth()
    auth_url = oauth.get_authorize_url()

    await interaction.response.send_message(
        "🔗 Opening Spotify login in your browser… If it doesn't open, click this link:\n" + auth_url,
        ephemeral=True,
    )
    try:
        webbrowser.open(auth_url)
    except:
        pass

    await _spotify_callback_server(oauth)

    if oauth.get_cached_token():
        await interaction.followup.send("✅ Spotify linked. You can now use /spotify_play, /spotify_pause, etc.", ephemeral=True)
    else:
        await interaction.followup.send("⚠️ Login timed out or failed. Try again.", ephemeral=True)

# ----- basic controls -----
@bot.tree.command(name="play", description="Resume playback")
async def spotify_play(interaction: discord.Interaction):
    sp = _spotify_client()
    if not sp:
        await interaction.response.send_message("❌ Not linked. Use /spotify_login first (owner only).", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        sp.start_playback()
        await interaction.followup.send("▶️ Playing.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)

@bot.tree.command(name="pause", description="Pause playback")
async def spotify_pause(interaction: discord.Interaction):
    sp = _spotify_client()
    if not sp:
        await interaction.response.send_message("❌ Not linked. Use /spotify_login first.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        sp.pause_playback()
        await interaction.followup.send("⏸️ Paused.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)

@bot.tree.command(name="next", description="Next track")
async def spotify_next(interaction: discord.Interaction):
    sp = _spotify_client()
    if not sp:
        await interaction.response.send_message("❌ Not linked. Use /spotify_login first.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        sp.next_track()
        await interaction.followup.send("⏭️ Skipped.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)

@bot.tree.command(name="current", description="Show current track")
async def spotify_current(interaction: discord.Interaction):
    sp = _spotify_client()
    if not sp:
        await interaction.response.send_message("❌ Not linked. Use /spotify_login first.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        cur = sp.current_user_playing_track()
        if not cur or not cur.get("item"):
            await interaction.followup.send("ℹ️ Nothing playing.", ephemeral=True)
            return
        item = cur["item"]
        artists = ", ".join(a["name"] for a in item["artists"])
        name = item["name"]
        url = item["external_urls"]["spotify"]
        await interaction.followup.send(f"🎵 **{name}** — {artists}\n<{url}>")
    except Exception as e:
        await interaction.followup.send(f"⚠️ {e}", ephemeral=True)


#---Llama via Ollama (/ask)---
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

def _chunk(text: str, limit: int = 1900):
    if not text:
        return ["(empty response)"]
    out, cur = [], []
    length = 0
    for part in text.split("\n"):
        if length + len(part) + 1 > limit:
            out.append("\n".join(cur))
            cur, length = [part], len(part)
        else:
            cur.append(part); length += len(part) + 1
    
    if cur:
        out.append("\n".join(cur))
    return out

@bot.tree.command(name="ask", description="Ask a local AI (LLaMA via Ollama)")
async def ask(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer(thinking=True)

    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": "You are a concise, helpful assistant inside a Discord bot."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=600) as resp:
                data = await resp.json()
        text = (data.get("message") or {}).get("content") or "No response."
        for part in _chunk(text):
            await interaction.followup.send(part)
    except Exception as e:
        await interaction.followup.send(f"Ollama error: {e}")


DB_PATH = Path(__file__).with_name("xp.sqlite3")

@bot.event
async def on_ready():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
          CREATE TABLE IF NOT EXISTS xp (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER NOT NULL DEFAULT 0
          )
        """)
        await db.commit()

    gid = os.getenv("GUILD_ID")
    if gid:
        synced = await bot.tree.sync(guild=discord.Object(id=int(gid)))
        print(f"Synced {len(synced)} cmds to guild {gid}")
    else:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global cmds")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


bot.run(TOKEN)



#cd "C:\Users\Ben LeBolloch\OneDrive\school\Projects\discordBot1"
#Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#.\.venv\Scripts\python.exe bot.py
#.\.venv\Scripts\Activate.ps1



