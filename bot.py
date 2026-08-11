import discord
from discord.ext import commands
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WG_API_KEY = os.getenv("WG_API_KEY")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot conectado exitosamente como {bot.user}")

@bot.command()
async def stats(ctx, nickname: str):
    """Obtiene las estadísticas de un jugador en World of Tanks (Servidor NA)."""
    if not WG_API_KEY:
        await ctx.send("Error: No se ha configurado la API Key de Wargaming.")
        return

    url_search = f"https://api.worldoftanks.com/wot/account/list/?application_id={WG_API_KEY}&search={nickname}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url_search) as resp:
            if resp.status != 200:
                await ctx.send("Error de conexión con la API de Wargaming.")
                return
            data = await resp.json()

        if data.get("status") != "ok" or not data.get("data"):
            await ctx.send(f"Jugador `{nickname}` no encontrado.")
            return

        account_id = data["data"][0]["account_id"]
        exact_name = data["data"][0]["nickname"]

        url_info = f"https://api.worldoftanks.com/wot/account/info/?application_id={WG_API_KEY}&account_id={account_id}"
        async with session.get(url_info) as resp:
            data_info = await resp.json()

        if data_info.get("status") != "ok" or str(account_id) not in data_info["data"]:
            await ctx.send("No se pudieron obtener las estadísticas del jugador.")
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

        await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def sync(ctx):
    await ctx.send("Sincronización realizada correctamente.")

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("Error: No se encontró el DISCORD_TOKEN en las variables de entorno")