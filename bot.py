import os
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import sqlite3
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from aiohttp import web

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WG_API_KEY = os.getenv("WG_API_KEY")
TIMEZONE = pytz.timezone("America/Argentina/Buenos_Aires")

# Standardized WN8 Scale & Roles
WN8_ROLES = [
    (2450, "🟣 Carry",    0x9B59B6),
    (1900, "🔵 Decent",   0x2980B9),
    (1600, "🩵 Player",   0x5DADE2),
    (1250, "🟢 Bot",      0x27AE60),
    (600,  "🟡 Tomato",   0xF1C40F),
    (300,  "🟠 Cancer",   0xE67E22),
    (0,    "🔴 Bad",      0xE74C3C)
]

# Legacy role names to clean up during role syncs
LEGACY_ROLE_NAMES = [
    "🔵 Descente",
    "🩵 Jugador",
    "🟡 Muñones",
    "🔴 Aborto",
]

# Number of new battles before recalculating role based on recent performance
RECENT_BATTLES_THRESHOLD = 100

# --- DATABASE MANAGEMENT ---
def init_db():
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS jugadores_v2 (
            discord_id INTEGER,
            guild_id INTEGER,
            wot_username TEXT,
            account_id INTEGER,
            wn8_overall INTEGER,
            wn8_reciente INTEGER,
            rol_actual TEXT,
            ultima_actualizacion TEXT,
            PRIMARY KEY (discord_id, guild_id)
        )
    """)

    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jugadores'")
    table_exists = c.fetchone()

    if table_exists:
        c.execute("""
            INSERT OR IGNORE INTO jugadores_v2 (discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion)
            SELECT discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion
            FROM jugadores
        """)
        c.execute("DROP TABLE jugadores")

    c.execute("ALTER TABLE jugadores_v2 RENAME TO jugadores")

    try:
        c.execute("ALTER TABLE jugadores ADD COLUMN baseline_fecha TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE jugadores ADD COLUMN battles_baseline INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            account_id INTEGER,
            fecha TEXT,
            tank_id INTEGER,
            battles INTEGER,
            damage INTEGER,
            wins INTEGER,
            frags INTEGER,
            spotted INTEGER,
            defense INTEGER,
            PRIMARY KEY (account_id, fecha, tank_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            intervalo_dias INTEGER DEFAULT 3,
            ultima_publicacion TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_player(discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_recent, role):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO jugadores
        (discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion,
         baseline_fecha, battles_baseline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT baseline_fecha FROM jugadores WHERE discord_id = ? AND guild_id = ?), NULL),
                COALESCE((SELECT battles_baseline FROM jugadores WHERE discord_id = ? AND guild_id = ?), 0))
    """, (discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_recent, role, datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M"),
          discord_id, guild_id, discord_id, guild_id))
    conn.commit()
    conn.close()

def delete_player(discord_id, guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("DELETE FROM jugadores WHERE discord_id = ? AND guild_id = ?", (discord_id, guild_id))
    rows_affected = c.rowcount
    conn.commit()
    conn.close()
    return rows_affected > 0

def delete_player_admin(target_id, guild_id, wot_username=None):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    if wot_username:
        c.execute("DELETE FROM jugadores WHERE guild_id = ? AND LOWER(wot_username) = LOWER(?)", (guild_id, wot_username))
    else:
        c.execute("DELETE FROM jugadores WHERE discord_id = ? AND guild_id = ?", (target_id, guild_id))
    rows_affected = c.rowcount
    conn.commit()
    conn.close()
    return rows_affected > 0

def get_players_by_guild(guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion FROM jugadores WHERE guild_id = ?", (guild_id,))
    players = c.fetchall()
    conn.close()
    return players

def get_player_progress(discord_id, guild_id, wot_username=None):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    if wot_username:
        c.execute("""
            SELECT wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion, baseline_fecha, battles_baseline 
            FROM jugadores 
            WHERE guild_id = ? AND LOWER(wot_username) = LOWER(?)
        """, (guild_id, wot_username))
    else:
        c.execute("""
            SELECT wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion, baseline_fecha, battles_baseline 
            FROM jugadores 
            WHERE guild_id = ? AND discord_id = ?
        """, (guild_id, discord_id))
    res = c.fetchone()
    conn.close()
    return res

def save_snapshot(account_id, tanks):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    date_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    for tank in tanks:
        s = tank["all"]
        c.execute("""
            INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (account_id, date_str, tank["tank_id"], s["battles"], s["damage_dealt"],
              s["wins"], s["frags"], s["spotted"], s["dropped_capture_points"]))
    conn.commit()
    conn.close()

def get_previous_snapshot(account_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT fecha FROM snapshots
        WHERE account_id = ?
        ORDER BY fecha DESC LIMIT 2
    """, (account_id,))
    dates = c.fetchall()
    if len(dates) < 2:
        conn.close()
        return None
    previous_date = dates[1][0]
    c.execute("""
        SELECT tank_id, battles, damage, wins, frags, spotted, defense
        FROM snapshots WHERE account_id = ? AND fecha = ?
    """, (account_id, previous_date))
    snapshot = {row[0]: row[1:] for row in c.fetchall()}
    conn.close()
    return snapshot

def get_snapshot_by_date(account_id, date_str):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        SELECT tank_id, battles, damage, wins, frags, spotted, defense
        FROM snapshots WHERE account_id = ? AND fecha = ?
    """, (account_id, date_str))
    snapshot = {row[0]: row[1:] for row in c.fetchall()}
    conn.close()
    return snapshot

def get_baseline(discord_id, guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT baseline_fecha, battles_baseline FROM jugadores WHERE discord_id = ? AND guild_id = ?", (discord_id, guild_id))
    res = c.fetchone()
    conn.close()
    return res

def update_baseline(discord_id, guild_id, date_str, battles):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("UPDATE jugadores SET baseline_fecha = ?, battles_baseline = ? WHERE discord_id = ? AND guild_id = ?", (date_str, battles, discord_id, guild_id))
    conn.commit()
    conn.close()

def save_guild_config(guild_id, channel_id, interval_days):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO guild_config (guild_id, channel_id, intervalo_dias)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            intervalo_dias = excluded.intervalo_dias
    """, (guild_id, channel_id, interval_days))
    conn.commit()
    conn.close()

def get_guild_config(guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT channel_id, intervalo_dias, ultima_publicacion FROM guild_config WHERE guild_id = ?", (guild_id,))
    res = c.fetchone()
    conn.close()
    return res

def update_publish_date(guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    c.execute("UPDATE guild_config SET ultima_publicacion = ? WHERE guild_id = ?", (today, guild_id))
    conn.commit()
    conn.close()

def get_all_configs():
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT guild_id, channel_id, intervalo_dias, ultima_publicacion FROM guild_config")
    configs = c.fetchall()
    conn.close()
    return configs

# --- WEB SERVER FOR 24/7 UPTIME ---
async def handle(request):
    return web.Response(text="World of Tanks Bot Active 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server listening on port {port}")

# --- INTENTS AND BOT SETUP ---
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

def get_role_info(wn8):
    for minimum, name, color in WN8_ROLES:
        if wn8 >= minimum:
            return name, color
    return "🔴 Bad", 0xE74C3C

async def fetch_expected_values(session):
    url = "https://static.modxvm.com/wn8-data-exp/json/wn8exp.json"
    async with session.get(url) as r:
        data = await r.json(content_type=None)
        return {tank["IDNum"]: tank for tank in data["data"]}

def calculate_wn8(tanks_list, expected):
    total_battles = 0
    total_wn8 = 0
    for tank in tanks_list:
        tank_id = tank["tank_id"] if isinstance(tank, dict) else tank[0]
        if isinstance(tank, dict):
            s = tank["all"]
            battles = s["battles"]
            damage = s["damage_dealt"]
            wins = s["wins"]
            frags = s["frags"]
            spotted = s["spotted"]
            defense = s["dropped_capture_points"]
        else:
            battles, damage, wins, frags, spotted, defense = tank[1], tank[2], tank[3], tank[4], tank[5], tank[6]

        if battles == 0 or tank_id not in expected:
            continue
        exp = expected[tank_id]
        r_dmg  = (damage / battles) / exp["expDamage"]
        r_spot = (spotted / battles) / exp["expSpot"]
        r_frag = (frags / battles) / exp["expFrag"]
        r_def  = (defense / battles) / exp["expDef"]
        r_win  = ((wins / battles) * 100) / exp["expWinRate"]
        r_dmg_c  = max(0, (r_dmg  - 0.22) / (1 - 0.22))
        r_spot_c = max(0, (r_spot - 0.38) / (1 - 0.38))
        r_frag_c = max(0, (r_frag - 0.12) / (1 - 0.12))
        r_def_c  = max(0, (r_def  - 0.10) / (1 - 0.10))
        r_win_c  = max(0, (r_win  - 0.71) / (1 - 0.71))
        wn8_tank = (980 * r_dmg_c) + (210 * r_dmg_c * r_frag_c) + (155 * r_frag_c * r_spot_c) + (75 * r_def_c * r_frag_c) + (145 * min(1.8, r_win_c))
        total_wn8 += wn8_tank * battles
        total_battles += battles
    if total_battles == 0:
        return 0
    return int(total_wn8 / total_battles)

async def fetch_wn8(username):
    async with aiohttp.ClientSession() as session:
        url = f"https://api.worldoftanks.com/wot/account/list/?application_id={WG_API_KEY}&search={username}"
        async with session.get(url) as r:
            data = await r.json()
            if not data.get("data"):
                return None, None, None, None, None
            account_id = data["data"][0]["account_id"]
            found_name = data["data"][0]["nickname"]

        url2 = f"https://api.worldoftanks.com/wot/tanks/stats/?application_id={WG_API_KEY}&account_id={account_id}&fields=tank_id,all"
        async with session.get(url2) as r2:
            tank_data = await r2.json()
            raw = tank_data.get("data", {})
            if raw is None:
                tanks = []
            else:
                tanks = raw.get(str(account_id)) or []
        if not tanks:
            return 0, 0, account_id, found_name, []

        expected = await fetch_expected_values(session)
        wn8_overall = calculate_wn8(tanks, expected)

        save_snapshot(account_id, tanks)

        prev_snapshot = get_previous_snapshot(account_id)
        if prev_snapshot:
            tanks_diff = []
            for tank in tanks:
                tid = tank["tank_id"]
                if tid in prev_snapshot:
                    ant = prev_snapshot[tid]
                    diff_battles = tank["all"]["battles"] - ant[0]
                    if diff_battles > 0:
                        tanks_diff.append({
                            "tank_id": tid,
                            "all": {
                                "battles": diff_battles,
                                "damage_dealt": tank["all"]["damage_dealt"] - ant[1],
                                "wins": tank["all"]["wins"] - ant[2],
                                "frags": tank["all"]["frags"] - ant[3],
                                "spotted": tank["all"]["spotted"] - ant[4],
                                "dropped_capture_points": tank["all"]["dropped_capture_points"] - ant[5]
                            }
                        })
                else:
                    tanks_diff.append(tank)
            wn8_recent = calculate_wn8(tanks_diff, expected) if tanks_diff else wn8_overall
        else:
            wn8_recent = wn8_overall

        return wn8_overall, wn8_recent, account_id, found_name, tanks

async def calculate_wn8_for_role(discord_id, guild_id, account_id, tanks, wn8_overall):
    if not tanks:
        return wn8_overall

    current_battles = sum(t["all"]["battles"] for t in tanks)
    baseline = get_baseline(discord_id, guild_id)
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    if not baseline or not baseline[0]:
        update_baseline(discord_id, guild_id, today, current_battles)
        return wn8_overall

    baseline_date, battles_baseline = baseline
    battles_since_baseline = current_battles - (battles_baseline or 0)

    if battles_since_baseline < RECENT_BATTLES_THRESHOLD:
        return wn8_overall

    base_snapshot = get_snapshot_by_date(account_id, baseline_date)
    if not base_snapshot:
        update_baseline(discord_id, guild_id, today, current_battles)
        return wn8_overall

    tanks_window = []
    for tank in tanks:
        tid = tank["tank_id"]
        if tid in base_snapshot:
            base = base_snapshot[tid]
            diff_battles = tank["all"]["battles"] - base[0]
            if diff_battles > 0:
                tanks_window.append({
                    "tank_id": tid,
                    "all": {
                        "battles": diff_battles,
                        "damage_dealt": tank["all"]["damage_dealt"] - base[1],
                        "wins": tank["all"]["wins"] - base[2],
                        "frags": tank["all"]["frags"] - base[3],
                        "spotted": tank["all"]["spotted"] - base[4],
                        "dropped_capture_points": tank["all"]["dropped_capture_points"] - base[5]
                    }
                })
        else:
            tanks_window.append(tank)

    async with aiohttp.ClientSession() as session:
        expected = await fetch_expected_values(session)
    wn8_window = calculate_wn8(tanks_window, expected) if tanks_window else wn8_overall

    update_baseline(discord_id, guild_id, today, current_battles)
    return wn8_window

async def update_player_and_roles(guild, discord_id, wot_username, account_id):
    try:
        wn8_overall, wn8_recent, _, _, tanks = await fetch_wn8(wot_username)
        if wn8_overall is None:
            return

        wn8_for_role = await calculate_wn8_for_role(discord_id, guild.id, account_id, tanks, wn8_overall)
        new_role_name, color = get_role_info(wn8_for_role)
        member = guild.get_member(discord_id)

        if member:
            names_to_clean = [name for _, name, _ in WN8_ROLES] + LEGACY_ROLE_NAMES
            roles_to_remove = [r for r in member.roles if r.name in names_to_clean]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)

            updated_role = discord.utils.get(guild.roles, name=new_role_name)
            if not updated_role:
                updated_role = await guild.create_role(name=new_role_name, color=discord.Color(color))
            await member.add_roles(updated_role)

        save_player(discord_id, guild.id, wot_username, account_id, wn8_overall, wn8_recent, new_role_name)
        print(f"🔄 [Auto] Updated role for {wot_username} in {guild.name}: {new_role_name}")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"❌ Error updating {wot_username}: {e}")

def build_report_embed(guild, players_list, is_test=False):
    title = f"📢 Statistics Report - {guild.name}"
    if is_test:
        title += " (Test Mode)"

    embed = discord.Embed(
        title=title,
        description="Updated status of linked members in this server.",
        color=0x2980B9
    )
    for p in players_list:
        _, _, wot_username, _, wn8_overall, wn8_recent, current_role, last_update = p
        embed.add_field(
            name=f"{current_role} {wot_username}",
            value=f"Overall WN8: `{wn8_overall}` | Recent: `{wn8_recent}`\nLast update: {last_update}",
            inline=False
        )
    embed.set_footer(text="WoT Stats Bot • Automatically updated roles and stats")
    return embed

def build_welcome_embed():
    embed = discord.Embed(
        title="👋 Thank you for adding TankTracker to your server!",
        description="I am a Discord bot designed to track **World of Tanks** performance (WN8) and manage clan statistics.",
        color=0x2ECC71
    )
    embed.add_field(
        name="🛠️ How does it work?",
        value="• Members can link their World of Tanks account.\n"
              "• Automatically assigns and updates **WN8 Roles**.\n"
              "• Role updates use your overall WN8, and switch to your last 100 battles once you've played that many.\n"
              "• Sends periodic automated stats reports to your configured channel.",
        inline=False
    )
    embed.add_field(
        name="📜 Commands for Everyone",
        value="`/link <username>` • Link your WoT account and receive your WN8 role.\n"
              "`/unlink` • Unlink your registered WoT account and remove associated roles.\n"
              "`/player [username]` • Check 100-battle threshold progress and stats.\n"
              "`/stats <username>` • Search WN8 stats for any World of Tanks player.\n"
              "`/players` • Display all linked members in this server.",
        inline=False
    )
    embed.add_field(
        name="⚙️ Admin Configuration",
        value="`/setup_channel <channel> <days>` • Set the channel and frequency (in days) for automatic reports.\n"
              "`/unlink_user [member] [wot_username]` • Unlink a specific player's account from the server.\n"
              "`/test_report` • Send an immediate test report to verify the output.\n"
              "`/cleanup_roles` • One-time cleanup of duplicated or legacy WN8 roles.",
        inline=False
    )
    embed.set_footer(text="TankTracker Bot • Maintained 24/7")
    return embed

async def update_task():
    print(f"🔄 Starting daily automated cycle - {datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M')}")
    configs = get_all_configs()
    today_dt = datetime.now(TIMEZONE).date()

    for guild_id, channel_id, interval, last_pub in configs:
        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        players = get_players_by_guild(guild_id)
        for p in players:
            d_id, g_id, wot_name, acc_id, _, _, _, _ = p
            await update_player_and_roles(guild, d_id, wot_name, acc_id)

        should_publish = False
        if not last_pub:
            should_publish = True
        else:
            last_dt = datetime.strptime(last_pub, "%Y-%m-%d").date()
            if (today_dt - last_dt).days >= interval:
                should_publish = True

        if should_publish and channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                updated_players = get_players_by_guild(guild_id)
                if updated_players:
                    embed = build_report_embed(guild, updated_players)
                    await channel.send(embed=embed)
                    update_publish_date(guild_id)

    print("✅ Automated daily cycle and role sync completed.")

@bot.event
async def on_guild_join(guild: discord.Guild):
    embed = build_welcome_embed()
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            await channel.send(embed=embed)
            break

@tree.command(name="link", description="Link your WoT account and get your WN8 performance role")
@app_commands.describe(username="Your World of Tanks username")
async def link(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    wn8_overall, wn8_recent, account_id, found_name, tanks = await fetch_wn8(username)

    if wn8_overall is None:
        embed = discord.Embed(title="❌ Player Not Found", description=f"Could not find any player named **{username}**.", color=0xE74C3C)
        await interaction.followup.send(embed=embed)
        return

    guild = interaction.guild
    member = interaction.user

    wn8_for_role = await calculate_wn8_for_role(member.id, guild.id, account_id, tanks, wn8_overall)
    role_name, color = get_role_info(wn8_for_role)

    names_to_clean = [name for _, name, _ in WN8_ROLES] + LEGACY_ROLE_NAMES
    roles_to_remove = [r for r in member.roles if r.name in names_to_clean]
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove)

    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        role = await guild.create_role(name=role_name, color=discord.Color(color))
    await member.add_roles(role)

    save_player(member.id, guild.id, found_name, account_id, wn8_overall, wn8_recent, role_name)

    embed = discord.Embed(title="🎮 Account Linked Successfully", description="Your World of Tanks account has been linked.", color=color)
    embed.add_field(name="👤 Player", value=f"`{found_name}`", inline=True)
    embed.add_field(name="⚔️ Overall WN8", value=f"`{wn8_overall}`", inline=True)
    embed.add_field(name="🔥 Recent WN8", value=f"`{wn8_recent}`", inline=True)
    embed.add_field(name="🏅 Rank", value=role_name, inline=False)
    embed.add_field(name="🔗 Tomato.gg", value=f"[View Full Profile](https://tomato.gg/stats/{found_name}-{account_id}/NA)", inline=False)
    embed.set_footer(text="WoT Stats Bot • Updated automatically")
    await interaction.followup.send(embed=embed)

@tree.command(name="unlink", description="Unlink your World of Tanks account from this server")
async def unlink(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()
    
    guild = interaction.guild
    member = interaction.user

    success = delete_player(member.id, guild.id)

    if not success:
        embed = discord.Embed(
            title="⚠️ No Linked Account",
            description="You do not have a registered account in this server.",
            color=0xF1C40F
        )
        await interaction.followup.send(embed=embed)
        return

    names_to_clean = [name for _, name, _ in WN8_ROLES] + LEGACY_ROLE_NAMES
    roles_to_remove = [r for r in member.roles if r.name in names_to_clean]
    if roles_to_remove:
        try:
            await member.remove_roles(*roles_to_remove)
        except discord.errors.Forbidden:
            pass

    embed = discord.Embed(
        title="🔗 Account Unlinked",
        description="Your World of Tanks account has been successfully unlinked and performance roles removed.",
        color=0x2ECC71
    )
    embed.set_footer(text="WoT Stats Bot • Unlinked successfully")
    await interaction.followup.send(embed=embed)

@tree.command(name="unlink_user", description="[Admin] Unlink a World of Tanks account from a specific member")
@app_commands.describe(
    member="The Discord member to unlink (optional if providing WoT username)",
    wot_username="The World of Tanks username to unlink (optional if selecting a member)"
)
@app_commands.checks.has_permissions(administrator=True)
async def unlink_user(interaction: discord.Interaction, member: discord.Member = None, wot_username: str = None):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    if not member and not wot_username:
        await interaction.response.send_message("⚠️ You must specify either a `@Member` or a `wot_username`.", ephemeral=True)
        return

    await interaction.response.defer()
    guild = interaction.guild

    target_id = member.id if member else None
    success = delete_player_admin(target_id, guild.id, wot_username)

    if not success:
        embed = discord.Embed(
            title="⚠️ Player Not Found",
            description="Could not find any linked account matching the provided criteria in this server.",
            color=0xF1C40F
        )
        await interaction.followup.send(embed=embed)
        return

    if member:
        names_to_clean = [name for _, name, _ in WN8_ROLES] + LEGACY_ROLE_NAMES
        roles_to_remove = [r for r in member.roles if r.name in names_to_clean]
        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove)
            except discord.errors.Forbidden:
                pass

    target_display = member.mention if member else f"`{wot_username}`"
    embed = discord.Embed(
        title="🔗 Account Unlinked by Admin",
        description=f"Successfully unlinked account for {target_display} and removed associated stats and roles.",
        color=0x2ECC71
    )
    await interaction.followup.send(embed=embed)

@tree.command(name="player", description="View progress towards WN8 role recalculation (100 battles threshold)")
@app_commands.describe(username="WoT username (optional if already linked)")
async def player(interaction: discord.Interaction, username: str = None):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
        return

    await interaction.response.defer()

    data = get_player_progress(interaction.user.id, interaction.guild.id, username)

    if not data:
        msg = f"Player `{username}` was not found in this server." if username else "You have not linked your account yet. Use `/link <nickname>` first."
        embed_error = discord.Embed(title="❌ Player Not Found", description=msg, color=0xE74C3C)
        await interaction.followup.send(embed=embed_error)
        return

    wot_username, account_id, wn8_overall, wn8_recent, current_role, last_update, baseline_date, battles_baseline = data

    _, _, _, _, tanks = await fetch_wn8(wot_username)
    total_battles = sum(t["all"]["battles"] for t in tanks) if tanks else 0
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")

    if not baseline_date or not battles_baseline or battles_baseline == 0 or battles_baseline > total_battles:
        update_baseline(interaction.user.id, interaction.guild.id, today, total_battles)
        battles_baseline = total_battles
        baseline_date = today

    battles_in_window = max(0, total_battles - battles_baseline)
    remaining = max(0, RECENT_BATTLES_THRESHOLD - battles_in_window)
    percentage = min(100, int((battles_in_window / RECENT_BATTLES_THRESHOLD) * 100))

    filled_blocks = int(percentage / 10)
    bar = "🟦" * filled_blocks + "⬜" * (10 - filled_blocks)

    role_name, color = get_role_info(wn8_overall)

    embed = discord.Embed(title=f"👤 Player Profile - {wot_username}", color=color)
    embed.add_field(name="🏅 Assigned Role", value=f"**{current_role}**", inline=True)
    embed.add_field(name="⚔️ Overall WN8", value=f"`{wn8_overall}`", inline=True)
    embed.add_field(name="🔥 Recent WN8", value=f"`{wn8_recent}`", inline=True)
    embed.add_field(name="💥 Total Battles", value=f"`{total_battles:,}`", inline=True)

    progress_info = (
        f"{bar} **{percentage}%**\n"
        f"• **Played in current window:** `{battles_in_window}` / `{RECENT_BATTLES_THRESHOLD}` battles\n"
    )
    if remaining > 0:
        progress_info += f"• **Remaining:** **{remaining}** battles to update role based on recent performance."
    else:
        progress_info += "• ⚡ **Ready to update!** You have completed over 100 battles. Your role will update during the daily report or upon using `/link`."

    embed.add_field(name="📊 Progress for New Role (100 Battles Window)", value=progress_info, inline=False)
    
    if baseline_date:
        embed.add_field(name="📅 Window Start Date", value=f"`{baseline_date}`\n(`{battles_baseline:,}` battles)", inline=True)
    embed.add_field(name="🕒 Last Data Sync", value=f"`{last_update}`", inline=True)

    embed.add_field(
        name="🔗 Links", 
        value=f"[View Profile on Tomato.gg](https://tomato.gg/stats/{wot_username}-{account_id}/NA)", 
        inline=False
    )
    embed.set_footer(text="TankTracker Bot • Dynamic WN8 Tracking")

    await interaction.followup.send(embed=embed)

@tree.command(name="stats", description="Check WN8 stats for any World of Tanks player")
@app_commands.describe(username="World of Tanks in-game username")
async def stats(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    wn8_overall, wn8_recent, account_id, found_name, _ = await fetch_wn8(username)

    if wn8_overall is None:
        embed = discord.Embed(title="❌ Player Not Found", description=f"Could not find any player named **{username}**.", color=0xE74C3C)
        await interaction.followup.send(embed=embed)
        return

    role_name, color = get_role_info(wn8_overall)
    embed = discord.Embed(title=f"📊 Stats for {found_name}", color=color)
    embed.add_field(name="⚔️ Overall WN8", value=f"`{wn8_overall}`", inline=True)
    embed.add_field(name="🔥 Recent WN8", value=f"`{wn8_recent}`", inline=True)
    embed.add_field(name="🏅 Rank", value=role_name, inline=True)
    embed.add_field(name="🔗 Tomato.gg", value=f"[View Full Profile](https://tomato.gg/stats/{found_name}-{account_id}/NA)", inline=False)
    embed.set_footer(text="WoT Stats Bot • Calculated using XVM expected values")
    await interaction.followup.send(embed=embed)

@tree.command(name="players", description="View all linked World of Tanks players in this server")
async def players(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.")
        return

    players_list = get_players_by_guild(interaction.guild.id)
    if not players_list:
        await interaction.response.send_message("There are no linked players in this server yet.")
        return

    embed = discord.Embed(title=f"🎮 Linked Players - {interaction.guild.name}", color=0x2980B9)
    for p in players_list:
        _, _, wot_username, _, wn8_overall, wn8_recent, current_role, last_update = p
        embed.add_field(
            name=f"{current_role} {wot_username}",
            value=f"WN8: `{wn8_overall}` | Recent: `{wn8_recent}`\nLast updated: {last_update}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@tree.command(name="setup_channel", description="Configure the channel and frequency (in days) for automatic stats reports")
@app_commands.describe(
    channel="Text channel where automatic reports will be posted",
    days="Interval in days between reports (e.g. 1, 3, 7 days)"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_channel(interaction: discord.Interaction, channel: discord.TextChannel, days: int):
    if days < 1:
        await interaction.response.send_message("Days interval must be at least 1 day.", ephemeral=True)
        return

    current_config = get_guild_config(interaction.guild.id)

    if current_config:
        current_channel_id, current_days, _ = current_config
        if current_channel_id == channel.id and current_days == days:
            embed = discord.Embed(
                title="⚠️ Duplicate Configuration",
                description=f"Channel {channel.mention} is **already set** to receive reports every **{days}** days.\n"
                            f"No changes were made.",
                color=0xF1C40F
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    save_guild_config(interaction.guild.id, channel.id, days)
    embed = discord.Embed(
        title="⚙️ Configuration Saved",
        description=f"✅ **Assigned Channel:** {channel.mention}\n"
                    f"📅 **Frequency:** Every **{days}** days an automatic report will be posted with updated stats for all linked members.\n\n"
                    f"💡 *You can use `/test_report` to verify the output.*",
        color=0x2ECC71
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="test_report", description="Send an immediate test report to check the output channel")
@app_commands.checks.has_permissions(administrator=True)
async def test_report(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command only works inside a server.")
        return

    await interaction.response.defer()

    players_list = get_players_by_guild(interaction.guild.id)
    if not players_list:
        await interaction.followup.send("⚠️ No linked players found in this server to generate a test report.")
        return

    config = get_guild_config(interaction.guild.id)
    target_channel = interaction.channel
    if config and config[0]:
        c = interaction.guild.get_channel(config[0])
        if c:
            target_channel = c

    try:
        embed = build_report_embed(interaction.guild, players_list, is_test=True)
        await target_channel.send(embed=embed)

        if target_channel != interaction.channel:
            await interaction.followup.send(f"✅ Test report sent successfully to {target_channel.mention}")
        else:
            await interaction.followup.send("✅ Test report sent in this channel.")
    except discord.errors.Forbidden:
        await interaction.followup.send(f"❌ **Permission Error:** The bot lacks permissions to send messages in {target_channel.mention}. Please grant 'View Channel' and 'Send Messages' permissions.")

@tree.command(name="cleanup_roles", description="[Admin] Remove duplicate or legacy WN8 roles from all server members")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_roles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    cleaned_members = 0

    for member in guild.members:
        member_legacy_roles = [r for r in member.roles if r.name in LEGACY_ROLE_NAMES]
        if member_legacy_roles:
            await member.remove_roles(*member_legacy_roles)
            cleaned_members += 1

    deleted_roles = 0
    for old_name in LEGACY_ROLE_NAMES:
        role = discord.utils.get(guild.roles, name=old_name)
        if role:
            await role.delete(reason="Cleanup legacy roles")
            deleted_roles += 1

    await interaction.followup.send(
        f"✅ Cleanup completed. {cleaned_members} members had legacy roles removed, {deleted_roles} legacy roles deleted from the server.",
        ephemeral=True
    )

@tree.command(name="help", description="Show bot information, commands list, and admin guide")
async def help(interaction: discord.Interaction):
    embed = build_welcome_embed()
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_ready():
    init_db()
    synced = await tree.sync()
    print(f"✅ Synced {len(synced)} slash commands.")
    print(f"✅ Bot connected as {bot.user}")
    print(f"✅ Active in {len(bot.guilds)} servers.")

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(update_task, "cron", hour=7, minute=0)
    scheduler.start()
    print("✅ Daily automatic update scheduled.")

    await start_web_server()

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("❌ Error: DISCORD_TOKEN not found in environment variables.")