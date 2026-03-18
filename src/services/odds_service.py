import logging

import aiohttp
from src.config import ODDS_API_KEY, SPORT_NAMES


logger = logging.getLogger(__name__)

API_BASE = "https://api.the-odds-api.com/v4"


async def get_upcoming_games(sport_key: str, limit: int = 10, markets: str = "h2h"):
    """Obtiene los próximos partidos con sus cuotas.

    Args:
        sport_key: Identificador del deporte/liga en The Odds API
        limit: Máximo de partidos a devolver
        markets: Mercados a solicitar (h2h, totals, btts, spreads)
    """
    url = f"{API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": markets,
        "oddsFormat": "decimal",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"Odds API: status {resp.status} for {sport_key} markets={markets}")
                    return None
                data = await resp.json()
                return data[:limit]
    except Exception as e:
        logger.error(f"Odds API error: {e}")
        return None


async def get_match_odds(sport_key: str, home_name: str, away_name: str) -> dict:
    """Obtiene cuotas completas para un partido específico.

    Busca el partido correcto verificando que AMBOS equipos coincidan,
    y asigna las cuotas correctamente a home/away sin importar el orden
    de la API.

    Args:
        sport_key: Identificador del deporte/liga
        home_name: Nombre del equipo local (de football-data.org)
        away_name: Nombre del equipo visitante

    Returns: dict con cuotas para todos los mercados
    """
    odds_result = _empty_odds()

    if not ODDS_API_KEY:
        return odds_result

    # Paso 1: Obtener h2h (1X2)
    games_h2h = await get_upcoming_games(sport_key, limit=30, markets="h2h")

    if not games_h2h:
        logger.warning(f"No games from Odds API for {sport_key}")
        return odds_result

    # Paso 2: Encontrar el partido correcto (AMBOS equipos deben coincidir)
    matched_game = _find_matching_game(games_h2h, home_name, away_name)

    if not matched_game:
        logger.warning(f"No odds match for {home_name} vs {away_name} in {sport_key}")
        return odds_result

    game, our_home_is_api_home = matched_game

    # Paso 3: Extraer cuotas h2h con mapeo correcto
    _extract_h2h_odds(game, home_name, away_name, our_home_is_api_home, odds_result)

    # Paso 4: Obtener totals (Over/Under) en una segunda llamada
    games_totals = await get_upcoming_games(sport_key, limit=30, markets="totals")
    if games_totals:
        totals_game = _find_matching_game(games_totals, home_name, away_name)
        if totals_game:
            _extract_totals_odds(totals_game[0], odds_result)

    logger.info(
        f"Odds matched for {home_name} vs {away_name}: "
        f"home={odds_result['home']:.2f} draw={odds_result['draw']:.2f} "
        f"away={odds_result['away']:.2f} o25={odds_result['over25']:.2f}"
    )

    return odds_result


def _normalize_name(name: str) -> str:
    """Normaliza un nombre de equipo para comparación.

    Elimina sufijos comunes (FC, CF, SC, SK, BC, etc.) y caracteres especiales.
    """
    import re
    name = name.lower().strip()
    # Eliminar sufijos comunes de clubes
    suffixes = r'\b(fc|cf|sc|sk|bc|ac|as|ss|rc|cd|ud|sd|rcd|afc|ssc|bsc|fk|nk|pk|rsc)\b'
    name = re.sub(suffixes, '', name)
    # Eliminar caracteres especiales y espacios extra
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _fuzzy_match(name1: str, name2: str) -> bool:
    """Compara dos nombres de equipo de forma flexible.

    Verifica si uno contiene al otro, o si comparten palabras clave significativas.
    """
    n1 = _normalize_name(name1)
    n2 = _normalize_name(name2)

    # Match exacto después de normalizar
    if n1 == n2:
        return True

    # Uno contiene al otro
    if n1 in n2 or n2 in n1:
        return True

    # Comparar palabras significativas (ignorar palabras cortas)
    words1 = {w for w in n1.split() if len(w) >= 3}
    words2 = {w for w in n2.split() if len(w) >= 3}

    if not words1 or not words2:
        return False

    # Si comparten al menos una palabra significativa de 4+ chars
    common = words1 & words2
    if any(len(w) >= 4 for w in common):
        return True

    return False


def _find_matching_game(games: list, home_name: str, away_name: str):
    """Encuentra el partido correcto verificando que AMBOS equipos coincidan.

    Returns: (game_dict, our_home_is_api_home: bool) o None si no hay match.
    """
    for game in games:
        api_home = game.get("home_team", "")
        api_away = game.get("away_team", "")

        # Caso 1: Nuestro home = API home, nuestro away = API away
        if _fuzzy_match(home_name, api_home) and _fuzzy_match(away_name, api_away):
            return game, True

        # Caso 2: Equipos invertidos (API tiene home/away al revés)
        if _fuzzy_match(home_name, api_away) and _fuzzy_match(away_name, api_home):
            return game, False

    return None


def _extract_h2h_odds(game: dict, home_name: str, away_name: str,
                       our_home_is_api_home: bool, odds_result: dict):
    """Extrae cuotas 1X2 mapeando correctamente a nuestro home/away.

    Usa el MEJOR precio entre todos los bookmakers para cada outcome.
    """
    for bk in game.get("bookmakers", []):
        for market in bk.get("markets", []):
            if market["key"] != "h2h":
                continue
            for o in market.get("outcomes", []):
                if o["name"] == "Draw":
                    odds_result["draw"] = max(odds_result["draw"], o["price"])
                    continue

                # Determinar si este outcome es nuestro home o away
                outcome_is_api_home = _fuzzy_match(o["name"], game.get("home_team", ""))

                if our_home_is_api_home:
                    # Orden normal: API home = nuestro home
                    if outcome_is_api_home:
                        odds_result["home"] = max(odds_result["home"], o["price"])
                    else:
                        odds_result["away"] = max(odds_result["away"], o["price"])
                else:
                    # Orden invertido: API home = nuestro away
                    if outcome_is_api_home:
                        odds_result["away"] = max(odds_result["away"], o["price"])
                    else:
                        odds_result["home"] = max(odds_result["home"], o["price"])


def _extract_totals_odds(game: dict, odds_result: dict):
    """Extrae cuotas de Over/Under de todos los puntos disponibles."""
    for bk in game.get("bookmakers", []):
        for market in bk.get("markets", []):
            if market["key"] != "totals":
                continue
            for o in market.get("outcomes", []):
                point = o.get("point", 2.5)
                price = o.get("price", 0)
                if o["name"] == "Over":
                    if point == 1.5:
                        odds_result["over15"] = max(odds_result["over15"], price)
                    elif point == 2.5:
                        odds_result["over25"] = max(odds_result["over25"], price)
                    elif point == 3.5:
                        odds_result["over35"] = max(odds_result["over35"], price)
                elif o["name"] == "Under":
                    if point == 1.5:
                        odds_result["under15"] = max(odds_result["under15"], price)
                    elif point == 2.5:
                        odds_result["under25"] = max(odds_result["under25"], price)
                    elif point == 3.5:
                        odds_result["under35"] = max(odds_result["under35"], price)


def _empty_odds() -> dict:
    """Devuelve un dict de cuotas vacío con todos los mercados."""
    return {
        "home": 0, "draw": 0, "away": 0,
        "over15": 0, "under15": 0,
        "over25": 0, "under25": 0,
        "over35": 0, "under35": 0,
        "btts_yes": 0, "btts_no": 0,
        "home_or_draw": 0, "away_or_draw": 0, "home_or_away": 0,
    }


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
