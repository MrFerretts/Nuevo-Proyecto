import aiohttp
from src.config import ODDS_API_KEY, SPORT_NAMES


API_BASE = "https://api.the-odds-api.com/v4"


async def get_upcoming_games(sport_key: str, limit: int = 10):
    """Obtiene los próximos partidos con sus cuotas."""
    url = f"{API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data[:limit]


async def get_available_sports():
    """Lista todos los deportes disponibles."""
    url = f"{API_BASE}/sports/"
    params = {"apiKey": ODDS_API_KEY}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            return await resp.json()


def format_game(game: dict) -> str:
    """Formatea un partido para mostrar en Telegram."""
    home = game.get("home_team", "?")
    away = game.get("away_team", "?")
    sport = SPORT_NAMES.get(game.get("sport_key", ""), game.get("sport_title", ""))
    commence = game.get("commence_time", "")[:16].replace("T", " ")

    lines = [f"🏟 *{home} vs {away}*", f"🏅 {sport}", f"📅 {commence} UTC"]

    bookmakers = game.get("bookmakers", [])
    if bookmakers:
        best = bookmakers[0]
        markets = best.get("markets", [])
        if markets:
            outcomes = markets[0].get("outcomes", [])
            odds_str = " | ".join(
                f"{o['name']}: *{o['price']}*" for o in outcomes
            )
            lines.append(f"📊 {odds_str}")
            lines.append(f"🏢 {best['title']}")

    return "\n".join(lines)


def format_games_list(games: list) -> str:
    """Formatea una lista de partidos."""
    if not games:
        return "❌ No hay partidos disponibles en este momento."
    return "\n\n─────────────\n\n".join(format_game(g) for g in games)
