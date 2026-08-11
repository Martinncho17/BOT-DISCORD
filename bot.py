import os
import discord
from discord.ext import commands
import aiohttp
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WG_API_KEY = os.getenv("WG_API_KEY")

# Intents necesarios
intents = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=intents)

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

# --- EVENTO ON_READY CON SINCRONIZACIÓN DE SLASH COMMANDS ---
@bot.event
async def on_ready():
    print(f"Bot conectado exitosamente como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizados {len(synced)} comandos de barra (Slash Commands).")
    except Exception as e:
        print(f"Error al sincronizar comandos: {e}")
    
    await start_web_server()

# --- COMANDO DE BARRA /stats ---
@bot.tree.command(name="stats", description="Obtiene las estadísticas de un jugador en World of Tanks (Servidor NA).")
async def stats(interaction: discord.Interaction, nickname: str):
    await interaction.response.defer() # Evita el error 'La aplicación no ha respondido'

    if not WG_API_KEY:
        await interaction.followup.send("Error: No se ha configurado la API Key de Wargaming.")
        return

    url_search = f"https://api.worldoftanks.com/wot/account/list/?application_id={WG_API_KEY}&search={nickname}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url_search) as resp:
            if resp.status != 200:
                await interaction.followup.send("Error de conexión con la API de Wargaming.")
                return
            data = await resp.json()

        if data.get("status") != "ok" or not data.get("data"):
            await interaction.followup.send(f"Jugador `{nickname}` no encontrado.")
            return

        account_id = data["data"][0]["account_id"]
        exact_name = data["data"][0]["nickname"]

        url_info = f"https://api.worldoftanks.com/wot/account/info/?application_id={WG_API_KEY}&account_id={account_id}"
        async with session.get(url_info) as resp:
            data_info = await resp.json()

        if data_info.get("status") != "ok" or str(account_id) not in data_info["data"]:
            await interaction.followup.send("No se pudieron obtener las estadísticas del jugador.")
            return

        player_data = data_info["data"][str(account_id)]
        statistics = player_data["statistics"]["all"]

        battles = statistics["battles"]
        wins = statistics["wins"]
        win_rate = (wins / battles * 100) if battles > 0 else 0
        damage_dealt = statistics["damage_dealt"]
        avg_damage = (damage_dealt / battles) if battles > 0 else 0

        embed = discord.Embed(
            title=f"Estadísticas de {exact_name}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Batallas", value=f"{battles:,}", inline=True)
        embed.add_field(name="Victoria %", value=f"{win_rate:.2f}%", inline=True)
        embed.add_field(name="Daño Promedio", value=f"{avg_damage:.0f}", inline=False)
        embed.set_footer(text="Datos provistos por Wargaming API")

        await interaction.followup.send(embed=embed)

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("Error: No se encontró el DISCORD_TOKEN en las variables de entorno")