# 🎮 TankTracker — Bot de Discord para World of Tanks

Bot de Discord que rastrea el rendimiento (WN8) de jugadores de **World of Tanks**, asigna roles automáticos según su nivel, y envía reportes periódicos de estadísticas al servidor. Desarrollado en Python con `discord.py`, desplegado 24/7 en Render.

## ✨ Funcionalidades

- **Vinculación de cuentas**: los miembros del servidor pueden vincular su cuenta de World of Tanks y obtener automáticamente un rol según su desempeño (WN8).
- **Roles automáticos por rendimiento**: 7 niveles distintos (Carry, Decent, Player, Bot, Tomato, Cancer, Bad), con colores diferenciados, actualizados diariamente.
- **Cálculo de WN8 propio**: implementación del algoritmo oficial de WN8 (Weighted Efficiency Rating), calculado en tiempo real a partir de la API pública de Wargaming.
- **Estadísticas recientes vs. históricas**: además del WN8 general, calcula el rendimiento reciente comparando snapshots guardados en base de datos.
- **Reportes automáticos configurables**: cada servidor puede elegir un canal y una frecuencia (en días) para recibir reportes automáticos con las estadísticas de todos los miembros vinculados.
- **Mensaje de bienvenida automático**: al agregar el bot a un nuevo servidor, envía una guía con todos los comandos disponibles.
- **Actualización diaria automática**: tarea programada (scheduler) que recalcula estadísticas y roles de todos los jugadores todos los días a las 7am (hora Argentina).

## 🖼️ Capturas
<img width="447" height="423" alt="Sin títasdafglo" src="https://github.com/user-attachments/assets/b1a2e5ea-344c-4c74-8608-9c86b0a9575c" />
<img width="501" height="248" alt="Sin título" src="https://github.com/user-attachments/assets/1cffbaf6-d431-4880-aced-60d82aac3bb5" />
<img width="495" height="529" alt="Sin títuloasd" src="https://github.com/user-attachments/assets/b20315f0-026d-4b5c-8252-f7a7c1c9b6ca" />

**Consulta de estadísticas (`/stats`):**

Muestra WN8 general, WN8 reciente, rango asignado y link al perfil completo en Tomato.gg.

**Mensaje de bienvenida al agregar el bot:**

Explica automáticamente cómo funciona el bot y todos los comandos disponibles, tanto para usuarios como para administradores.

**Comandos disponibles:**

| Comando | Descripción |
|---|---|
| `/link <username>` | Vincula tu cuenta de WoT y obtené tu rol de rendimiento |
| `/stats <username>` | Consulta el WN8 de cualquier jugador |
| `/players` | Lista todos los jugadores vinculados en el servidor |
| `/setup_channel <canal> <días>` | *(admin)* Configura canal y frecuencia de reportes automáticos |
| `/test_report` | *(admin)* Envía un reporte de prueba inmediato |
| `/help` | Muestra la guía completa del bot |

## 🛠️ Tecnologías utilizadas

- **Python 3** + `discord.py` (slash commands con `app_commands`)
- **SQLite** para persistencia de jugadores y snapshots históricos
- **aiohttp** para consumo asíncrono de la API de Wargaming
- **APScheduler** para tareas programadas (actualización diaria)
- **aiohttp.web** como servidor HTTP mínimo, para mantener el servicio activo 24/7 en Render
- **python-dotenv** para gestión segura de variables de entorno (tokens y API keys nunca quedan en el código)
- Desplegado en **Render** con auto-deploy conectado a GitHub

## ⚙️ Instalación local

```bash
git clone https://github.com/Martinncho17/BOT-DISCORD.git
cd BOT-DISCORD
pip install -r requirements.txt
```

Crear un archivo `.env` en la raíz del proyecto con:
```
DISCORD_TOKEN=tu_token_de_discord
WG_API_KEY=tu_api_key_de_wargaming
```

Ejecutar:
```bash
python bot.py
```

## 📌 Notas de arquitectura

El bot calcula el WN8 de forma independiente (sin depender de servicios de terceros para el número final), consumiendo directamente los valores esperados (`expected values`) publicados por XVM y la API oficial de estadísticas de Wargaming. Esto permite tener control total sobre el cálculo y la posibilidad de extenderlo (por ejemplo, WN8 por tanque específico, comparativas entre jugadores, etc.).

---

*Proyecto desarrollado como parte de portfolio de desarrollo backend/bots para Discord.*
