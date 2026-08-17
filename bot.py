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
ARGENTINA = pytz.timezone("America/Argentina/Buenos_Aires")

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

# Nombres viejos (versión en español) que hay que seguir limpiando
# para que no queden acumulados en quienes los tenían asignados
LEGACY_ROLE_NAMES = [
    "🔵 Descente",
    "🩵 Jugador",
    "🟡 Muñones",
    "🔴 Aborto",
]

# Cada cuántas batallas nuevas se recalcula el rol en base al rendimiento reciente
UMBRAL_BATALLAS_RECIENTES = 100

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

def guardar_jugador(discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO jugadores
        (discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion,
         baseline_fecha, battles_baseline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT baseline_fecha FROM jugadores WHERE discord_id = ? AND guild_id = ?), NULL),
                COALESCE((SELECT battles_baseline FROM jugadores WHERE discord_id = ? AND guild_id = ?), 0))
    """, (discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol, datetime.now(ARGENTINA).strftime("%Y-%m-%d %H:%M"),
          discord_id, guild_id, discord_id, guild_id))
    conn.commit()
    conn.close()

def obtener_jugadores_por_guild(guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol_actual, ultima_actualizacion FROM jugadores WHERE guild_id = ?", (guild_id,))
    jugadores = c.fetchall()
    conn.close()
    return jugadores

def guardar_snapshot(account_id, tanks):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    fecha = datetime.now(ARGENTINA).strftime("%Y-%m-%d")
    for tank in tanks:
        s = tank["all"]
        c.execute("""
            INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (account_id, fecha, tank["tank_id"], s["battles"], s["damage_dealt"],
              s["wins"], s["frags"], s["spotted"], s["dropped_capture_points"]))
    conn.commit()
    conn.close()

def obtener_snapshot_anterior(account_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT fecha FROM snapshots
        WHERE account_id = ?
        ORDER BY fecha DESC LIMIT 2
    """, (account_id,))
    fechas = c.fetchall()
    if len(fechas) < 2:
        conn.close()
        return None
    fecha_anterior = fechas[1][0]
    c.execute("""
        SELECT tank_id, battles, damage, wins, frags, spotted, defense
        FROM snapshots WHERE account_id = ? AND fecha = ?
    """, (account_id, fecha_anterior))
    snapshot = {row[0]: row[1:] for row in c.fetchall()}
    conn.close()
    return snapshot

def obtener_snapshot_por_fecha(account_id, fecha):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        SELECT tank_id, battles, damage, wins, frags, spotted, defense
        FROM snapshots WHERE account_id = ? AND fecha = ?
    """, (account_id, fecha))
    snapshot = {row[0]: row[1:] for row in c.fetchall()}
    conn.close()
    return snapshot

def obtener_baseline(discord_id, guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT baseline_fecha, battles_baseline FROM jugadores WHERE discord_id = ? AND guild_id = ?", (discord_id, guild_id))
    res = c.fetchone()
    conn.close()
    return res

def actualizar_baseline(discord_id, guild_id, fecha, battles):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("UPDATE jugadores SET baseline_fecha = ?, battles_baseline = ? WHERE discord_id = ? AND guild_id = ?", (fecha, battles, discord_id, guild_id))
    conn.commit()
    conn.close()

def guardar_config_guild(guild_id, channel_id, intervalo_dias):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO guild_config (guild_id, channel_id, intervalo_dias)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            intervalo_dias = excluded.intervalo_dias
    """, (guild_id, channel_id, intervalo_dias))
    conn.commit()
    conn.close()

def obtener_config_guild(guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT channel_id, intervalo_dias, ultima_publicacion FROM guild_config WHERE guild_id = ?", (guild_id,))
    res = c.fetchone()
    conn.close()
    return res

def actualizar_fecha_publicacion(guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    hoy = datetime.now(ARGENTINA).strftime("%Y-%m-%d")
    c.execute("UPDATE guild_config SET ultima_publicacion = ? WHERE guild_id = ?", (hoy, guild_id))
    conn.commit()
    conn.close()

def obtener_todas_configs():
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

def get_rol_info(wn8):
    for minimo, nombre, color in WN8_ROLES:
        if wn8 >= minimo:
            return nombre, color
    return "🔴 Bad", 0xE74C3C

async def fetch_expected_values(session):
    url = "https://static.modxvm.com/wn8-data-exp/json/wn8exp.json"
    async with session.get(url) as r:
        data = await r.json(content_type=None)
        return {tank["IDNum"]: tank for tank in data["data"]}

def calcular_wn8(tanks_list, expected):
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
        wn8_overall = calcular_wn8(tanks, expected)

        guardar_snapshot(account_id, tanks)

        snapshot_ant = obtener_snapshot_anterior(account_id)
        if snapshot_ant:
            tanks_diff = []
            for tank in tanks:
                tid = tank["tank_id"]
                if tid in snapshot_ant:
                    ant = snapshot_ant[tid]
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
            wn8_reciente = calcular_wn8(tanks_diff, expected) if tanks_diff else wn8_overall
        else:
            wn8_reciente = wn8_overall

        return wn8_overall, wn8_reciente, account_id, found_name, tanks

async def calcular_wn8_para_rol(discord_id, guild_id, account_id, tanks, wn8_overall):
    """
    Decide qué WN8 usar para asignar el rol:
    - Si el jugador todavía no jugó UMBRAL_BATALLAS_RECIENTES batallas nuevas
      desde su último "punto de partida", se usa el WN8 Overall (estable).
    - Si ya jugó esa cantidad o más, se calcula el WN8 solo de esa ventana
      de batallas nuevas, se usa ese para el rol, y se resetea el punto de partida.
    """
    if not tanks:
        return wn8_overall

    battles_actuales = sum(t["all"]["battles"] for t in tanks)
    baseline = obtener_baseline(discord_id, guild_id)
    hoy = datetime.now(ARGENTINA).strftime("%Y-%m-%d")

    if not baseline or not baseline[0]:
        actualizar_baseline(discord_id, guild_id, hoy, battles_actuales)
        return wn8_overall

    baseline_fecha, battles_baseline = baseline
    battles_desde_baseline = battles_actuales - (battles_baseline or 0)

    if battles_desde_baseline < UMBRAL_BATALLAS_RECIENTES:
        return wn8_overall

    snapshot_base = obtener_snapshot_por_fecha(account_id, baseline_fecha)
    if not snapshot_base:
        actualizar_baseline(discord_id, guild_id, hoy, battles_actuales)
        return wn8_overall

    tanks_ventana = []
    for tank in tanks:
        tid = tank["tank_id"]
        if tid in snapshot_base:
            base = snapshot_base[tid]
            diff_battles = tank["all"]["battles"] - base[0]
            if diff_battles > 0:
                tanks_ventana.append({
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
            tanks_ventana.append(tank)

    async with aiohttp.ClientSession() as session:
        expected = await fetch_expected_values(session)
    wn8_ventana = calcular_wn8(tanks_ventana, expected) if tanks_ventana else wn8_overall

    actualizar_baseline(discord_id, guild_id, hoy, battles_actuales)
    return wn8_ventana

async def actualizar_jugador_y_roles(guild, discord_id, wot_username, account_id):
    """Refreshes player stats and automatically updates WN8 roles in background."""
    try:
        wn8_overall, wn8_reciente, _, _, tanks = await fetch_wn8(wot_username)
        if wn8_overall is None:
            return

        wn8_para_rol = await calcular_wn8_para_rol(discord_id, guild.id, account_id, tanks, wn8_overall)
        nuevo_rol_nombre, color = get_rol_info(wn8_para_rol)
        member = guild.get_member(discord_id)

        if member:
            nombres_a_limpiar = [nombre for _, nombre, _ in WN8_ROLES] + LEGACY_ROLE_NAMES
            roles_a_quitar = [r for r in member.roles if r.name in nombres_a_limpiar]
            if roles_a_quitar:
                await member.remove_roles(*roles_a_quitar)

            rol_actualizado = discord.utils.get(guild.roles, name=nuevo_rol_nombre)
            if not rol_actualizado:
                rol_actualizado = await guild.create_role(name=nuevo_rol_nombre, color=discord.Color(color))
            await member.add_roles(rol_actualizado)

        guardar_jugador(discord_id, guild.id, wot_username, account_id, wn8_overall, wn8_reciente, nuevo_rol_nombre)
        print(f"🔄 [Auto] Updated role for {wot_username} in {guild.name}: {nuevo_rol_nombre}")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"❌ Error updating {wot_username}: {e}")

def construir_embed_reporte(guild, jugadores_list, es_prueba=False):
    titulo = f"📢 Statistics Report - {guild.name}"
    if es_prueba:
        titulo += " (Test Mode)"

    embed = discord.Embed(
        title=titulo,
        description="Updated status of linked members in this server.",
        color=0x2980B9
    )
    for j in jugadores_list:
        _, _, wot_username, _, wn8_overall, wn8_reciente, rol_actual, ultima_act = j
        embed.add_field(
            name=f"{rol_actual} {wot_username}",
            value=f"Overall WN8: `{wn8_overall}` | Recent: `{wn8_reciente}`\nLast update: {ultima_act}",
            inline=False
        )
    embed.set_footer(text="WoT Stats Bot • Automatically updated roles and stats")
    return embed

def construir_embed_bienvenida():
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
              "`/stats <username>` • Search WN8 stats for any World of Tanks player.\n"
              "`/players` • Display all linked members in this server.",
        inline=False
    )
    embed.add_field(
        name="⚙️ Admin Configuration",
        value="`/setup_channel <channel> <days>` • Set the channel and frequency (in days) for automatic reports.\n"
              "`/test_report` • Send an immediate test report to verify the output.\n"
              "`/cleanup_roles` • One-time cleanup of duplicated/legacy WN8 roles.",
        inline=False
    )
    embed.set_footer(text="TankTracker Bot • Maintained 24/7")
    return embed

async def tarea_actualizacion():
    print(f"🔄 Starting daily automated cycle - {datetime.now(ARGENTINA).strftime('%Y-%m-%d %H:%M')}")
    configs = obtener_todas_configs()
    hoy_dt = datetime.now(ARGENTINA).date()

    for guild_id, channel_id, intervalo, ultima_pub in configs:
        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        jugadores = obtener_jugadores_por_guild(guild_id)
        for j in jugadores:
            d_id, g_id, wot_name, acc_id, _, _, _, _ = j
            await actualizar_jugador_y_roles(guild, d_id, wot_name, acc_id)

        debe_publicar = False
        if not ultima_pub:
            debe_publicar = True
        else:
            ultima_dt = datetime.strptime(ultima_pub, "%Y-%m-%d").date()
            if (hoy_dt - ultima_dt).days >= intervalo:
                debe_publicar = True

        if debe_publicar and channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                jugadores_actualizados = obtener_jugadores_por_guild(guild_id)
                if jugadores_actualizados:
                    embed = construir_embed_reporte(guild, jugadores_actualizados)
                    await channel.send(embed=embed)
                    actualizar_fecha_publicacion(guild_id)

    print("✅ Automated daily cycle and role sync completed.")

@bot.event
async def on_guild_join(guild: discord.Guild):
    embed = construir_embed_bienvenida()
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            await channel.send(embed=embed)
            break

@tree.command(name="link", description="Link your WoT account and get your WN8 performance role")
@app_commands.describe(username="Your World of Tanks username")
async def link(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    wn8_overall, wn8_reciente, account_id, found_name, tanks = await fetch_wn8(username)

    if wn8_overall is None:
        embed = discord.Embed(title="❌ Player Not Found", description=f"Could not find any player named **{username}**.", color=0xE74C3C)
        await interaction.followup.send(embed=embed)
        return

    guild = interaction.guild
    member = interaction.user

    wn8_para_rol = await calcular_wn8_para_rol(member.id, guild.id, account_id, tanks, wn8_overall)
    rol_nombre, color = get_rol_info(wn8_para_rol)

    nombres_a_limpiar = [nombre for _, nombre, _ in WN8_ROLES] + LEGACY_ROLE_NAMES
    roles_a_quitar = [r for r in member.roles if r.name in nombres_a_limpiar]
    if roles_a_quitar:
        await member.remove_roles(*roles_a_quitar)

    rol = discord.utils.get(guild.roles, name=rol_nombre)
    if not rol:
        rol = await guild.create_role(name=rol_nombre, color=discord.Color(color))
    await member.add_roles(rol)

    guardar_jugador(member.id, guild.id, found_name, account_id, wn8_overall, wn8_reciente, rol_nombre)

    embed = discord.Embed(title="🎮 Account Linked Successfully", description="Your World of Tanks account has been linked.", color=color)
    embed.add_field(name="👤 Player", value=f"`{found_name}`", inline=True)
    embed.add_field(name="⚔️ Overall WN8", value=f"`{wn8_overall}`", inline=True)
    embed.add_field(name="🔥 Recent WN8", value=f"`{wn8_reciente}`", inline=True)
    embed.add_field(name="🏅 Rank", value=rol_nombre, inline=False)
    embed.add_field(name="🔗 Tomato.gg", value=f"[View Full Profile](https://tomato.gg/stats/{found_name}-{account_id}/NA)", inline=False)
    embed.set_footer(text="WoT Stats Bot • Updated automatically")
    await interaction.followup.send(embed=embed)

@tree.command(name="stats", description="Check WN8 stats for any World of Tanks player")
@app_commands.describe(username="World of Tanks in-game username")
async def stats(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    wn8_overall, wn8_reciente, account_id, found_name, _ = await fetch_wn8(username)

    if wn8_overall is None:
        embed = discord.Embed(title="❌ Player Not Found", description=f"Could not find any player named **{username}**.", color=0xE74C3C)
        await interaction.followup.send(embed=embed)
        return

    rol_nombre, color = get_rol_info(wn8_overall)
    embed = discord.Embed(title=f"📊 Stats for {found_name}", color=color)
    embed.add_field(name="⚔️ Overall WN8", value=f"`{wn8_overall}`", inline=True)
    embed.add_field(name="🔥 Recent WN8", value=f"`{wn8_reciente}`", inline=True)
    embed.add_field(name="🏅 Rank", value=rol_nombre, inline=True)
    embed.add_field(name="🔗 Tomato.gg", value=f"[View Full Profile](https://tomato.gg/stats/{found_name}-{account_id}/NA)", inline=False)
    embed.set_footer(text="WoT Stats Bot • Calculated using XVM expected values")
    await interaction.followup.send(embed=embed)

@tree.command(name="players", description="View all linked World of Tanks players in this server")
async def players(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.")
        return

    jugadores_list = obtener_jugadores_por_guild(interaction.guild.id)
    if not jugadores_list:
        await interaction.response.send_message("There are no linked players in this server yet.")
        return

    embed = discord.Embed(title=f"🎮 Linked Players - {interaction.guild.name}", color=0x2980B9)
    for j in jugadores_list:
        _, _, wot_username, _, wn8_overall, wn8_reciente, rol_actual, ultima_act = j
        embed.add_field(
            name=f"{rol_actual} {wot_username}",
            value=f"WN8: `{wn8_overall}` | Recent: `{wn8_reciente}`\nLast updated: {ultima_act}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@tree.command(name="setup_channel", description="Configure the channel and frequency (in days) for automatic stats reports")
@app_commands.describe(
    canal="Text channel where automatic reports will be posted",
    dias="Interval in days between reports (e.g. 1, 3, 7 days)"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_channel(interaction: discord.Interaction, canal: discord.TextChannel, dias: int):
    if dias < 1:
        await interaction.response.send_message("Days interval must be at least 1 day.", ephemeral=True)
        return

    config_actual = obtener_config_guild(interaction.guild.id)

    if config_actual:
        canal_actual_id, dias_actuales, _ = config_actual
        if canal_actual_id == canal.id and dias_actuales == dias:
            embed = discord.Embed(
                title="⚠️ Duplicate Configuration",
                description=f"Channel {canal.mention} is **already set** to receive reports every **{dias}** days.\n"
                            f"No changes were made.",
                color=0xF1C40F
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    guardar_config_guild(interaction.guild.id, canal.id, dias)
    embed = discord.Embed(
        title="⚙️ Configuration Saved",
        description=f"✅ **Assigned Channel:** {canal.mention}\n"
                    f"📅 **Frequency:** Every **{dias}** days an automatic report will be posted with updated stats for all linked members.\n\n"
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

    jugadores_list = obtener_jugadores_por_guild(interaction.guild.id)
    if not jugadores_list:
        await interaction.followup.send("⚠️ No linked players found in this server to generate a test report.")
        return

    config = obtener_config_guild(interaction.guild.id)
    canal_destino = interaction.channel
    if config and config[0]:
        c = interaction.guild.get_channel(config[0])
        if c:
            canal_destino = c

    try:
        embed = construir_embed_reporte(interaction.guild, jugadores_list, es_prueba=True)
        await canal_destino.send(embed=embed)

        if canal_destino != interaction.channel:
            await interaction.followup.send(f"✅ Test report sent successfully to {canal_destino.mention}")
        else:
            await interaction.followup.send("✅ Test report sent in this channel.")
    except discord.errors.Forbidden:
        await interaction.followup.send(f"❌ **Permission Error:** The bot lacks permissions to send messages in {canal_destino.mention}. Please grant 'View Channel' and 'Send Messages' permissions.")

@tree.command(name="cleanup_roles", description="[Admin] Elimina roles WN8 duplicados o viejos de todos los miembros")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_roles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    limpiados = 0

    for member in guild.members:
        roles_legacy_del_miembro = [r for r in member.roles if r.name in LEGACY_ROLE_NAMES]
        if roles_legacy_del_miembro:
            await member.remove_roles(*roles_legacy_del_miembro)
            limpiados += 1

    roles_borrados = 0
    for nombre_viejo in LEGACY_ROLE_NAMES:
        rol = discord.utils.get(guild.roles, name=nombre_viejo)
        if rol:
            await rol.delete(reason="Limpieza de roles legacy")
            roles_borrados += 1

    await interaction.followup.send(
        f"✅ Limpieza completada. {limpiados} miembros tenían roles viejos, {roles_borrados} roles legacy eliminados del servidor.",
        ephemeral=True
    )

@tree.command(name="help", description="Show bot information, commands list, and admin guide")
async def help(interaction: discord.Interaction):
    embed = construir_embed_bienvenida()
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_ready():
    init_db()
    synced = await tree.sync()
    print(f"✅ Synced {len(synced)} slash commands.")
    print(f"✅ Bot connected as {bot.user}")
    print(f"✅ Active in {len(bot.guilds)} servers.")

    scheduler = AsyncIOScheduler(timezone=ARGENTINA)
    scheduler.add_job(tarea_actualizacion, "cron", hour=7, minute=0)
    scheduler.start()
    print("✅ Daily automatic update scheduled.")

    await start_web_server()

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("❌ Error: DISCORD_TOKEN not found in environment variables.")