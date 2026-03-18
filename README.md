# Bot de Apuestas Deportivas - Telegram

Bot de Telegram para gestionar un canal de tips de apuestas deportivas con modelo de suscripción VIP.

## Funcionalidades

### Para usuarios
- `/start` - Menú principal con botones interactivos
- `/stats` - Estadísticas del canal (win rate, profit)
- `/tips` - Últimos 10 tips publicados con resultados
- `/partidos` - Próximos partidos con cuotas en vivo
- `/vip` - Información sobre suscripción VIP

### Análisis inteligente (admin)
- `/analizar` - Análisis completo de un partido con estadísticas y value bets
- `/oportunidades` - Escanea automáticamente múltiples ligas buscando apuestas con valor

**El motor de análisis evalúa:**
- Forma reciente (últimos 10 partidos, ponderados por recencia)
- Promedios de goles a favor/en contra
- Tendencias: Over 2.5, BTTS, porterías a cero
- Enfrentamientos directos (H2H)
- Posición en la clasificación
- Comparación de probabilidades estimadas vs cuotas del mercado
- Detección de **value bets** (apuestas donde la probabilidad real supera a la cuota)

### Gestión de canal (admin)
- `/newtip` - Crear nuevo tip (guiado paso a paso)
- `/resultado <id> <win|loss|void>` - Actualizar resultado de un tip
- `/addvip <user_id> [meses]` - Activar VIP para un usuario
- `/removevip <user_id>` - Desactivar VIP
- `/broadcast <mensaje>` - Enviar mensaje a todos los usuarios
- `/admin` - Panel de administración

### Automatizaciones
- Publicación automática de tips en canales (free/VIP)
- Tracking de estadísticas y profit
- Expiración automática de VIPs
- Consulta de cuotas en tiempo real (The Odds API)
- Análisis estadístico de partidos (API-Football)

## Setup

### 1. Crear el bot en Telegram
1. Habla con [@BotFather](https://t.me/BotFather) en Telegram
2. Usa `/newbot` y sigue las instrucciones
3. Copia el token que te da

### 2. Crear los canales
1. Crea un canal público para tips FREE
2. Crea un canal privado para tips VIP
3. Añade tu bot como administrador en ambos canales
4. Obtén los IDs de los canales (usa [@userinfobot](https://t.me/userinfobot))

### 3. Obtener API Keys
1. **The Odds API** (cuotas en vivo): Regístrate en [the-odds-api.com](https://the-odds-api.com/) - Gratis: 500 requests/mes
2. **football-data.org** (estadísticas actuales, RECOMENDADO): Regístrate en [football-data.org](https://www.football-data.org/) - Gratis: 10 req/min, 12 ligas top
3. **API-Football** (estadísticas, fallback): Regístrate en [api-football.com](https://www.api-football.com/) - Gratis: 100 requests/día

### 4. Configurar el proyecto
```bash
# Clonar el repositorio
git clone <tu-repo>
cd Nuevo-Proyecto

# Crear entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Edita .env con tus datos
```

### 5. Ejecutar
```bash
python main.py
```

## Modelo de negocio

1. **Canal FREE**: Publica 1-2 tips diarios para atraer audiencia
2. **Canal VIP** ($9.99/mes): Tips exclusivos de alta confianza, análisis detallados
3. **Crecimiento**: Comparte estadísticas públicas para demostrar resultados

## Despliegue en servidor (producción)

Para mantener el bot corriendo 24/7, puedes usar un VPS barato ($5/mes en DigitalOcean, Hetzner, etc.):

```bash
# Usando systemd
sudo nano /etc/systemd/system/betting-bot.service
```

```ini
[Unit]
Description=Betting Bot Telegram
After=network.target

[Service]
User=tu_usuario
WorkingDirectory=/ruta/al/proyecto
ExecStart=/ruta/al/proyecto/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable betting-bot
sudo systemctl start betting-bot
```

## Estructura del proyecto

```
├── main.py                    # Punto de entrada
├── requirements.txt           # Dependencias
├── .env.example              # Variables de entorno (plantilla)
└── src/
    ├── config.py             # Configuración
    ├── handlers/
    │   ├── user_handlers.py     # Comandos de usuario
    │   ├── admin_handlers.py    # Comandos de admin
    │   └── analysis_handlers.py # Análisis de partidos
    ├── models/
    │   └── database.py          # Base de datos SQLite
    ├── services/
    │   ├── odds_service.py      # API de cuotas deportivas
    │   ├── stats_service.py     # API de estadísticas (API-Football)
    │   ├── analysis_engine.py   # Motor de análisis y value betting
    │   └── scheduler_service.py # Tareas programadas
    └── utils/
        └── formatters.py     # Formateo de mensajes
```
