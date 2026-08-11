import os
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import sqlite3
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz
from dotenv import load_dotenv
from aiohttp import web

# Cargar variables de entorno (.env en local, Environment Variables en Render)
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WG_API_KEY = os.getenv("WG_API_KEY")
ARGENTINA = pytz.timezone("America/Argentina/Buenos_Aires")

WN8_ROLES = [
    (2450, "🟣 Carry",    0x9B59B6),
    (1900, "🔵 Descente", 0x2980B9),
    (1600, "🩵 Jugador",   0x5DADE2),
    (1250, "🟢 Bot",      0x27AE60),
    (600,  "🟡 Muñones",  0xF1C40F),
    (300,  "🟠 Cancer",   0xE67E22),
    (0,    "🔴 Aborto",   0xE74C3C)
]

# Base de datos
def init_db():
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jugadores (
            discord_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            wot_username TEXT,
            account_id INTEGER,
            wn8_overall INTEGER,
            wn8_reciente INTEGER,
            rol_actual TEXT,
            ultima_actualizacion TEXT
        )
    """)
    # Tabla de snapshots para calcular WN8 reciente
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
    conn.commit()
    conn.close()

def guardar_jugador(discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO jugadores 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol, datetime.now(ARGENTINA).strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def obtener_todos_jugadores():
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT * FROM jugadores")
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

# --- SERVIDOR WEB PARA RENDER Y UPTIMEROBOT ---
async def handle(request):
    return web.Response(text="Bot de World of Tanks activo 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Servidor web escuchando en el puerto {port}")

# Intents y bot
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

def get_rol_info(wn8):
    for minimo, nombre, color in WN8_ROLES:
        if wn8 >= minimo:
            return nombre, color
    return "🔴 Bajo", 0xE74C3C

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

        # Guardar snapshot del día
        guardar_snapshot(account_id, tanks)

        # Calcular WN8 reciente con snapshot anterior
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

async def actualizar_jugador(guild, discord_id, wot_username, account_id, rol_actual):
    try:
        wn8_overall, wn8_reciente, _, _, _ = await fetch_wn8(wot_username)
        nuevo_rol, color = get_rol_info(wn8_overall)

        if nuevo_rol != rol_actual:
            member = guild.get_member(discord_id)
            if member:
                for _, nombre, _ in WN8_ROLES:
                    r = discord.utils.get(guild.roles, name=nombre)
                    if r and r in member.roles:
                        await member.remove_roles(r)
                rol = discord.utils.get(guild.roles, name=nuevo_rol)
                if not rol:
                    rol = await guild.create_role(name=nuevo_rol, color=discord.Color(color))
                await member.add_roles(rol)

        guardar_jugador(discord_id, guild.id, wot_username, account_id, wn8_overall, wn8_reciente, nuevo_rol)
        print(f"✅ Actualizado: {wot_username} | WN8: {wn8_overall} | Rol: {nuevo_rol}")
        await asyncio.sleep(3)
    except Exception as e:
        print(f"❌ Error actualizando {wot_username}: {e}")

async def tarea_actualizacion():
    print(f"🔄 Iniciando actualización diaria - {datetime.now(ARGENTINA).strftime('%Y-%m-%d %H:%M')}")
    jugadores = obtener_todos_jugadores()
    for jugador in jugadores:
        discord_id, guild_id, wot_username, account_id, _, _, rol_actual, _ = jugador
        guild = bot.get_guild(guild_id)
        if guild:
            await actualizar_jugador(guild, discord_id, wot_username, account_id, rol_actual)
    print(f"✅ Actualización completada - {len(jugadores)} jugadores procesados")

@tree.command(name="vincular", description="Vinculá tu cuenta de WoT y obtené tu rol de WN8")
@app_commands.describe(username="Tu nombre de usuario en World of Tanks")
async def vincular(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    wn8_overall, wn8_reciente, account_id, found_name, _ = await fetch_wn8(username)

    if wn8_overall is None:
        embed = discord.Embed(title="❌ Jugador no encontrado", description=f"No encontré ningún jugador con el nombre **{username}**.", color=0xE74C3C)
        await interaction.followup.send(embed=embed)
        return

    rol_nombre, color = get_rol_info(wn8_overall)
    guild = interaction.guild
    rol = discord.utils.get(guild.roles, name=rol_nombre)
    if not rol:
        rol = await guild.create_role(name=rol_nombre, color=discord.Color(color))

    member = interaction.user
    for _, nombre, _ in WN8_ROLES:
        r = discord.utils.get(guild.roles, name=nombre)
        if r and r in member.roles:
            await member.remove_roles(r)
    await member.add_roles(rol)

    guardar_jugador(member.id, guild.id, found_name, account_id, wn8_overall, wn8_reciente, rol_nombre)

    embed = discord.Embed(title="🎮 Cuenta Vinculada", description="Tu cuenta de World of Tanks fue vinculada exitosamente.", color=color)
    embed.add_field(name="👤 Jugador", value=f"`{found_name}`", inline=True)
    embed.add_field(name="⚔️ WN8 Overall", value=f"`{wn8_overall}`", inline=True)
    embed.add_field(name="🔥 WN8 Reciente", value=f"`{wn8_reciente}`", inline=True)
    embed.add_field(name="🏅 Categoría", value=rol_nombre, inline=False)
    embed.add_field(name="🔗 Tomato.gg", value=f"[Ver perfil completo](https://tomato.gg/stats/{found_name}-{account_id}/NA)", inline=False)
    embed.set_footer(text="WoT Stats Bot • Se actualiza todos los días a las 7am")
    await interaction.followup.send(embed=embed)

@tree.command(name="stats", description="Consultá el WN8 de cualquier jugador de WoT")
@app_commands.describe(username="Nombre de usuario en World of Tanks")
async def stats(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    wn8_overall, wn8_reciente, account_id, found_name, _ = await fetch_wn8(username)

    if wn8_overall is None:
        embed = discord.Embed(title="❌ Jugador no encontrado", description=f"No encontré ningún jugador con el nombre **{username}**.", color=0xE74C3C)
        await interaction.followup.send(embed=embed)
        return

    rol_nombre, color = get_rol_info(wn8_overall)
    embed = discord.Embed(title=f"📊 Stats de {found_name}", color=color)
    embed.add_field(name="⚔️ WN8 Overall", value=f"`{wn8_overall}`", inline=True)
    embed.add_field(name="🔥 WN8 Reciente", value=f"`{wn8_reciente}`", inline=True)
    embed.add_field(name="🏅 Categoría", value=rol_nombre, inline=True)
    embed.add_field(name="🔗 Tomato.gg", value=f"[Ver perfil completo](https://tomato.gg/stats/{found_name}-{account_id}/NA)", inline=False)
    embed.set_footer(text="WoT Stats Bot • Calculado con valores esperados de XVM")
    await interaction.followup.send(embed=embed)

@tree.command(name="jugadores", description="Ver todos los jugadores vinculados y sus WN8")
async def jugadores(interaction: discord.Interaction):
    jugadores_list = obtener_todos_jugadores()
    if not jugadores_list:
        await interaction.response.send_message("No hay jugadores vinculados todavía.")
        return
    embed = discord.Embed(title="🎮 Jugadores Vinculados", color=0x2980B9)
    for j in jugadores_list:
        _, _, wot_username, _, wn8_overall, wn8_reciente, rol_actual, ultima_act = j
        embed.add_field(
            name=f"{rol_actual} {wot_username}",
            value=f"WN8: `{wn8_overall}` | Reciente: `{wn8_reciente}`\nÚltima actualización: {ultima_act}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_ready():
    init_db()
    await tree.sync()
    print(f"✅ Bot conectado como {bot.user}")
    print(f"✅ Servidores: {len(bot.guilds)}")

    scheduler = AsyncIOScheduler(timezone=ARGENTINA)
    scheduler.add_job(tarea_actualizacion, "cron", hour=7, minute=0)
    scheduler.start()
    print("✅ Actualización automática programada para las 7:00am (Argentina)")

    await start_web_server()

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("❌ Error: No se encontró DISCORD_TOKEN en las variables de entorno.")