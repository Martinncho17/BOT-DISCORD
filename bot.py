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

# Cargar variables de entorno
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

# --- BASE DE DATOS ---
def init_db():
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jugadores (
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (discord_id, guild_id, wot_username, account_id, wn8_overall, wn8_reciente, rol, datetime.now(ARGENTINA).strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def obtener_jugadores_por_guild(guild_id):
    conn = sqlite3.connect("wot_stats.db")
    c = conn.cursor()
    c.execute("SELECT * FROM jugadores WHERE guild_id = ?", (guild_id,))
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

# --- SERVIDOR WEB ---
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

# --- INTENTS Y BOT ---
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
        print(f"✅ Actualizado: {wot_username} (Guild: {guild.name}) | WN8: {wn8_overall}")
        await asyncio.sleep(3)
    except Exception as e:
        print(f"❌ Error actualizando {wot_username}: {e}")

# Helper para construir el embed del reporte
def construir_embed_reporte(guild, jugadores_list, es_prueba=False):
    titulo = f"📢 Reporte de Estadísticas - {guild.name}"
    if es_prueba:
        titulo += " (Modo Prueba)"

    embed = discord.Embed(
        title=titulo,
        description="Estado actualizado de los miembros vinculados en este servidor.",
        color=0x2980B9
    )
    for j in jugadores_list:
        _, _, wot_username, _, wn8_overall, wn8_reciente, rol_actual, ultima_act = j
        embed.add_field(
            name=f"{rol_actual} {wot_username}",
            value=f"WN8 Global: `{wn8_overall}` | Reciente: `{wn8_reciente}`\nÚltima act: {ultima_act}",
            inline=False
        )
    embed.set_footer(text="WoT Stats Bot • Actualizaciones automáticas")
    return embed

def construir_embed_bienvenida():
    embed = discord.Embed(
        title="👋 ¡Gracias por añadir TankTracker a tu servidor!",
        description="Soy un bot especializado en registrar y rastrear el rendimiento (WN8) de los jugadores de **World of Tanks** en tu comunidad.",
        color=0x2ECC71
    )
    embed.add_field(
        name="🛠️ ¿Cómo funciona el bot?",
        value="• Permite a cada miembro vincular su cuenta de World of Tanks.\n"
              "• Asigna automáticamente un **Rol con Color según el WN8** del usuario.\n"
              "• Mantiene las estadísticas siempre actualizadas de forma diaria.\n"
              "• Envía reportes automáticos periódicos al canal que elijas.",
        inline=False
    )
    embed.add_field(
        name="📜 Comandos disponibles para todos",
        value="`/vincular <usuario>` • Vincula tu cuenta de WoT y te da tu rol asignado.\n"
              "`/stats <usuario>` • Consulta las estadísticas de cualquier jugador.\n"
              "`/jugadores` • Muestra la lista con el WN8 de todos los miembros vinculados en este servidor.",
        inline=False
    )
    embed.add_field(
        name="⚙️ Configuración para Administradores",
        value="`/configurar_canal <canal> <dias>` • Elige en qué canal publicar el reporte y cada cuántos días (ej: cada 3 días).\n"
              "`/probar_reporte` • Manda un reporte de prueba inmediato para verificar cómo queda.",
        inline=False
    )
    embed.set_footer(text="TankTracker Bot • Mantenido 24/7")
    return embed

async def tarea_actualizacion():
    print(f"🔄 Iniciando ciclo diario - {datetime.now(ARGENTINA).strftime('%Y-%m-%d %H:%M')}")
    configs = obtener_todas_configs()
    hoy_dt = datetime.now(ARGENTINA).date()

    for guild_id, channel_id, intervalo, ultima_pub in configs:
        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        jugadores = obtener_jugadores_por_guild(guild_id)
        for j in jugadores:
            d_id, g_id, wot_name, acc_id, _, _, r_act, _ = j
            await actualizar_jugador(guild, d_id, wot_name, acc_id, r_act)

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

    print("✅ Ciclo de actualización completado.")

# --- EVENTOS Y COMANDOS DISCORD ---
@bot.event
async def on_guild_join(guild: discord.Guild):
    """Mensaje que se envía cuando el bot entra a un servidor por primera vez."""
    embed = construir_embed_bienvenida()
    
    # Intenta enviar el mensaje en el primer canal de texto donde tenga permiso de escribir
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            await channel.send(embed=embed)
            break

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
    embed.set_footer(text="WoT Stats Bot • Actualizado automáticamente")
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

@tree.command(name="jugadores", description="Ver los jugadores vinculados en este servidor y sus WN8")
async def jugadores(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Este comando solo se puede usar en un servidor.")
        return

    jugadores_list = obtener_jugadores_por_guild(interaction.guild.id)
    if not jugadores_list:
        await interaction.response.send_message("No hay jugadores vinculados en este servidor todavía.")
        return

    embed = discord.Embed(title=f"🎮 Jugadores Vinculados - {interaction.guild.name}", color=0x2980B9)
    for j in jugadores_list:
        _, _, wot_username, _, wn8_overall, wn8_reciente, rol_actual, ultima_act = j
        embed.add_field(
            name=f"{rol_actual} {wot_username}",
            value=f"WN8: `{wn8_overall}` | Reciente: `{wn8_reciente}`\nÚltima actualización: {ultima_act}",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@tree.command(name="configurar_canal", description="Elige el canal y la frecuencia en días para enviar el reporte automático")
@app_commands.describe(
    canal="Canal de texto donde se publicará el reporte automático de este servidor",
    dias="Cada cuántos días se enviará el reporte automático (Ejemplo: 1, 3, 7 días)"
)
@app_commands.checks.has_permissions(administrator=True)
async def configurar_canal(interaction: discord.Interaction, canal: discord.TextChannel, dias: int):
    if dias < 1:
        await interaction.response.send_message("El intervalo de días debe ser al menos 1 día.", ephemeral=True)
        return

    guardar_config_guild(interaction.guild.id, canal.id, dias)
    embed = discord.Embed(
        title="⚙️ Configuración Guardada",
        description=f"✅ **Canal asignado:** {canal.mention}\n"
                    f"📅 **Frecuencia:** Cada **{dias}** días se publicará un reporte automático con las estadísticas actualizadas de todos los miembros vinculados en este servidor.\n\n"
                    f"💡 *Puedes usar `/probar_reporte` para verificar la publicación.*",
        color=0x2ECC71
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="probar_reporte", description="Manda un reporte de prueba inmediato para verificar el funcionamiento")
@app_commands.checks.has_permissions(administrator=True)
async def probar_reporte(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Este comando solo funciona dentro de un servidor.")
        return

    await interaction.response.defer()

    jugadores_list = obtener_jugadores_por_guild(interaction.guild.id)
    if not jugadores_list:
        await interaction.followup.send("⚠️ No hay jugadores vinculados en este servidor para generar un reporte de prueba.")
        return

    config = obtener_config_guild(interaction.guild.id)
    canal_destino = interaction.channel
    if config and config[0]:
        c = interaction.guild.get_channel(config[0])
        if c:
            canal_destino = c

    embed = construir_embed_reporte(interaction.guild, jugadores_list, es_prueba=True)
    await canal_destino.send(embed=embed)

    if canal_destino != interaction.channel:
        await interaction.followup.send(f"✅ Reporte de prueba enviado con éxito en {canal_destino.mention}")
    else:
        await interaction.followup.send("✅ Reporte de prueba enviado en este canal.")

@tree.command(name="ayuda", description="Muestra la información de bienvenida, comandos y guía de configuración")
async def ayuda(interaction: discord.Interaction):
    embed = construir_embed_bienvenida()
    await interaction.response.send_message(embed=embed)

@on_ready := bot.event
async def on_ready():
    init_db()
    await tree.sync()
    print(f"✅ Bot conectado como {bot.user}")
    print(f"✅ Servidores en los que está activo: {len(bot.guilds)}")

    scheduler = AsyncIOScheduler(timezone=ARGENTINA)
    scheduler.add_job(tarea_actualizacion, "cron", hour=7, minute=0)
    scheduler.start()
    print("✅ Actualización automática diaria lista (7:00am Argentina).")

    await start_web_server()

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("❌ Error: No se encontró DISCORD_TOKEN en las variables de entorno.")