import os
from threading import Thread
import discord
from discord.ext import commands
from flask import Flask

# Servidor Flask para que Render mantenga el puerto abierto
app = Flask('')


@app.route('/')
def home():
  return '¡Bot WoT Online 24/7!'


def run():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port)


def keep_alive():
  t = Thread(target=run)
  t.daemon = True
  t.start()


keep_alive()  # Inicia el servidor webimport discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import sqlite3
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz

DISCORD_TOKEN = "MTUzNDMwMjUwNDQzMzc1MDAxNg.GcNaCf.j2sHicwXHrbhmWGHT2ZQWBuPqXHUTedW-RPjYY"  
WG_API_KEY = "9249050aea31a710ece75e1f49fa70d9"
ARGENTINA = pytz.timezone("America/Argentina/Buenos_Aires")

WN8_ROLES = [
    (2450, "🟣 Carry",    0x9B59B6),
    (1900, "🔵 Descente", 0x2980B9),
    (1600, "🩵 Jugador",   0x5DADE2),
    (1250, "🟢 Bot",     0x27AE60),
    (600,  "🟡 Muñones",  0xF1C40F),
    (300,  "🟠 Cancer",   0xE67E22),
    (0,    "🔴 Aborto",      0xE74C3C)
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
            if not data["data"]:
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
    print("📋 Comandos registrados:")
    for cmd in tree.get_commands():
        print(f"   /{cmd.name}")
    print(f"✅ Servidores: {len(bot.guilds)}")

    scheduler = AsyncIOScheduler(timezone=ARGENTINA)
    scheduler.add_job(tarea_actualizacion, "cron", hour=7, minute=0)
    scheduler.start()
    print("✅ Actualización automática programada para las 7:00am (Argentina)")

# ============================================================
# COMANDO DE PRUEBA - SOLO KICKEA A UNA PERSONA
# ============================================================
@tree.command(name="probar", description="PRUEBA: Kickea a un solo usuario y le manda DM")
@app_commands.describe(usuario="El usuario para probar", link="Link de invitación")
async def probar(interaction: discord.Interaction, usuario: discord.Member, link: str):
    
    # Solo administradores
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Solo administradores", ephemeral=True)
        return
    
    await interaction.response.send_message(f"🧪 Iniciando prueba con {usuario.name}...", ephemeral=True)
    
    # PASO 1: Intentar mandar DM
    print(f"📨 Intentando mandar DM a {usuario.name}...")
    try:
        embed = discord.Embed(
            title="👋 Mensaje de prueba",
            description=f"Este es un mensaje de prueba del servidor **{interaction.guild.name}**.\n\n"
                       f"Link de invitación: **{link}**",
            color=0x2980B9
        )
        await usuario.send(embed=embed)
        print(f"✅ DM enviado a {usuario.name}")
        await interaction.followup.send(f"✅ DM enviado a {usuario.name}", ephemeral=True)
    except Exception as e:
        print(f"❌ No se pudo mandar DM a {usuario.name}: {e}")
        await interaction.followup.send(f"❌ No se pudo mandar DM a {usuario.name} (pero igual se intentará kickear)", ephemeral=True)
    
    # PASO 2: Intentar kickear
    print(f"👢 Intentando kickear a {usuario.name}...")
    try:
        await usuario.kick(reason="Prueba de kick masivo")
        print(f"✅ Kickeado: {usuario.name}")
        await interaction.followup.send(f"👢 {usuario.name} fue kickeado exitosamente", ephemeral=True)
    except Exception as e:
        print(f"❌ No se pudo kickear a {usuario.name}: {e}")
        await interaction.followup.send(f"❌ Error al kickear a {usuario.name}: {e}", ephemeral=True)


# ============================================================
# COMANDO KICK MASIVO (MODIFICADO CON PRINTS)
# ============================================================
@tree.command(name="kickmasivo", description="Manda DM con invitación y kickea a todos los miembros")
@app_commands.describe(invitacion="El link de invitación permanente del servidor")
async def kick_masivo(interaction: discord.Interaction, invitacion: str):
    
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Solo el administrador puede usar este comando.", ephemeral=True)
        return

    await interaction.response.send_message("⏳ Iniciando proceso de kick masivo...\nMirá la consola para ver el progreso.", ephemeral=True)
    
    guild = interaction.guild
    kickeados = 0
    fallidos = 0
    sin_dm = 0
    
    print("=" * 50)
    print(f"🚀 INICIANDO KICK MASIVO - Servidor: {guild.name}")
    print(f"👥 Total de miembros: {len(guild.members)}")
    print("=" * 50)

    for member in guild.members:
        if member.bot:
            print(f"🤖 Saltando bot: {member.name}")
            continue
        if member == interaction.user:
            print(f"👑 Saltando a vos mismo: {member.name}")
            continue
        
        # Intentar mandar DM
        print(f"📨 [{member.name}] Intentando mandar DM...")
        try:
            embed = discord.Embed(
                title="👋 ¡Hasta pronto!",
                description=f"El servidor **{guild.name}** está pasando por una reestructuración.\n\n"
                           f"¡Podés volver a unirte cuando quieras usando este link!\n\n"
                           f"🔗 **{invitacion}**",
                color=0x2980B9
            )
            embed.set_footer(text="Nos vemos adentro 🎮")
            await member.send(embed=embed)
            print(f"   ✅ DM enviado a {member.name}")
        except Exception as e:
            print(f"   ❌ No se pudo mandar DM a {member.name}: {e}")
            sin_dm += 1

        # Intentar kickear
        print(f"👢 [{member.name}] Intentando kickear...")
        try:
            await member.kick(reason="Reestructuración del servidor")
            print(f"   ✅ Kickeado: {member.name}")
            kickeados += 1
        except Exception as e:
            print(f"   ❌ Error al kickear a {member.name}: {e}")
            fallidos += 1

        await asyncio.sleep(1)  # Esperar 1 segundo entre cada kick

    print("=" * 50)
    print(f"🏁 KICK MASIVO COMPLETADO")
    print(f"   👢 Kickeados: {kickeados}")
    print(f"   📵 Sin DM: {sin_dm}")
    print(f"   ❌ Fallidos: {fallidos}")
    print("=" * 50)

    await interaction.followup.send(
        f"✅ Proceso completado:\n"
        f"👢 Kickeados: `{kickeados}`\n"
        f"📵 Sin DM (igual kickeados): `{sin_dm}`\n"
        f"❌ Fallidos: `{fallidos}`",
        ephemeral=True
    )
@tree.command(name="sync", description="Sincronizar comandos")
async def sync(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Solo admin", ephemeral=True)
        return
    await tree.sync()
    await interaction.response.send_message("✅ Comandos sincronizados", ephemeral=True)
@tree.command(name="limpiar", description="Borra todos los mensajes de este canal")
async def limpiar(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Solo admin", ephemeral=True)
        return
    
    await interaction.response.send_message("🧹 Limpiando canal...", ephemeral=True)
    await interaction.channel.purge(limit=None)
    await interaction.followup.send("✅ Canal limpio", ephemeral=True)
@tree.command(name="quedan", description="Muestra quiénes quedan en el servidor y sus roles")
async def quedan(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Solo admin", ephemeral=True)
        return
    
    await interaction.response.send_message("🔍 Revisando miembros...", ephemeral=True)
    
    guild = interaction.guild
    miembros = []
    
    for member in guild.members:
        if member.bot:
            continue
        if member == interaction.user:
            continue
        
        roles = [rol.name for rol in member.roles if rol.name != "@everyone"]
        rol_mas_alto = member.top_role.name if member.top_role.name != "@everyone" else "Sin rol"
        
        miembros.append(f"👤 {member.name} | Rol más alto: {rol_mas_alto}")
    
    if not miembros:
        await interaction.followup.send("✅ No queda nadie más que vos y los bots.", ephemeral=True)
        return
    
    mensaje = f"📋 Quedan {len(miembros)} miembros:\n\n" + "\n".join(miembros)
    
    if len(mensaje) > 2000:
        await interaction.followup.send(f"📋 Quedan {len(miembros)} miembros. Revisá la consola.", ephemeral=True)
        print(f"\n📋 MIEMBROS RESTANTES ({len(miembros)}):")
        for m in miembros:
            print(m)
    else:
        await interaction.followup.send(mensaje, ephemeral=True)
bot.run(DISCORD_TOKEN)
