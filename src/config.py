import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FREE_CHANNEL_ID = int(os.getenv("FREE_CHANNEL_ID", "0"))
VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
VIP_PRICE = float(os.getenv("VIP_PRICE", "9.99"))
PAYMENT_LINK = os.getenv("PAYMENT_LINK", "")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data.db")

# Deportes disponibles en The Odds API
SPORTS = {
    "soccer": "soccer_epl",
    "futbol_esp": "soccer_spain_la_liga",
    "futbol_champions": "soccer_uefa_champions_league",
    # Selecciones nacionales
    "mundial": "soccer_fifa_world_cup",
    "amistosos": "soccer_international_friendlies",
    "clasif_conmebol": "soccer_fifa_world_cup_qualifier_conmebol",
    "clasif_uefa": "soccer_fifa_world_cup_qualifier_uefa",
    "clasif_concacaf": "soccer_fifa_world_cup_qualifier_concacaf",
    "clasif_caf": "soccer_fifa_world_cup_qualifier_caf",
    "clasif_afc": "soccer_fifa_world_cup_qualifier_afc",
    "clasif_ofc": "soccer_fifa_world_cup_qualifier_ofc",
    # Otros deportes
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "mlb": "baseball_mlb",
    "tenis": "tennis_atp_french_open",
}

SPORT_NAMES = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_uefa_champions_league": "Champions League",
    # Selecciones nacionales
    "soccer_fifa_world_cup": "Copa del Mundo 2026",
    "soccer_international_friendlies": "Amistosos Internacionales",
    "soccer_fifa_world_cup_qualifier_conmebol": "Clasificatorias CONMEBOL",
    "soccer_fifa_world_cup_qualifier_uefa": "Clasificatorias UEFA",
    "soccer_fifa_world_cup_qualifier_concacaf": "Clasificatorias CONCACAF",
    "soccer_fifa_world_cup_qualifier_caf": "Clasificatorias CAF",
    "soccer_fifa_world_cup_qualifier_afc": "Clasificatorias AFC",
    "soccer_fifa_world_cup_qualifier_ofc": "Clasificatorias OFC",
    # Otros deportes
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL",
    "baseball_mlb": "MLB",
    "tennis_atp_french_open": "Tenis ATP",
}
