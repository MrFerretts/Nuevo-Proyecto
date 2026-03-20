"""Servicio para obtener datos de xG real desde Understat (gratis).

Understat proporciona estadísticas avanzadas (xG, xGA, xPts) de las
top 6 ligas europeas, extraídas de los datos de tiros reales.

Ligas soportadas: Premier League, La Liga, Serie A, Bundesliga, Ligue 1
"""

import json
import logging
import re
import time

import aiohttp

logger = logging.getLogger(__name__)

# Mapeo de league_id interno → nombre en Understat
UNDERSTAT_LEAGUES = {
    39: "EPL",           # Premier League
    140: "La_liga",      # La Liga
    135: "Serie_A",      # Serie A
    78: "Bundesliga",    # Bundesliga
    61: "Ligue_1",       # Ligue 1
}

# Caché de datos xG por equipo: {"team_name_lower": {"xg": ..., "xga": ..., "ts": ...}}
_xg_cache: dict[str, dict] = {}
_XG_CACHE_TTL = 3600  # 1 hora

# Mapeo de nombres comunes a nombres de Understat
_TEAM_NAME_MAP = {
    # Premier League
    "arsenal fc": "Arsenal",
    "aston villa fc": "Aston Villa",
    "afc bournemouth": "Bournemouth",
    "brentford fc": "Brentford",
    "brighton & hove albion fc": "Brighton",
    "brighton and hove albion": "Brighton",
    "burnley fc": "Burnley",
    "chelsea fc": "Chelsea",
    "crystal palace fc": "Crystal Palace",
    "everton fc": "Everton",
    "fulham fc": "Fulham",
    "ipswich town fc": "Ipswich",
    "leicester city fc": "Leicester",
    "liverpool fc": "Liverpool",
    "luton town fc": "Luton",
    "manchester city fc": "Manchester City",
    "manchester united fc": "Manchester United",
    "newcastle united fc": "Newcastle United",
    "nottingham forest fc": "Nottingham Forest",
    "sheffield united fc": "Sheffield United",
    "tottenham hotspur fc": "Tottenham",
    "west ham united fc": "West Ham",
    "wolverhampton wanderers fc": "Wolverhampton Wanderers",
    # La Liga
    "fc barcelona": "Barcelona",
    "real madrid cf": "Real Madrid",
    "club atlético de madrid": "Atletico Madrid",
    "atletico madrid": "Atletico Madrid",
    "real sociedad de fútbol": "Real Sociedad",
    "real betis balompié": "Real Betis",
    "villarreal cf": "Villarreal",
    "sevilla fc": "Sevilla",
    "athletic club": "Athletic Club",
    "rc celta de vigo": "Celta Vigo",
    "rcd mallorca": "Mallorca",
    "getafe cf": "Getafe",
    "girona fc": "Girona",
    "ud las palmas": "Las Palmas",
    "deportivo alavés": "Alaves",
    "ca osasuna": "Osasuna",
    "rayo vallecano de madrid": "Rayo Vallecano",
    "valencia cf": "Valencia",
    "real valladolid cf": "Valladolid",
    # Serie A
    "ssc napoli": "Napoli",
    "fc internazionale milano": "Inter",
    "inter milan": "Inter",
    "ac milan": "Milan",
    "juventus fc": "Juventus",
    "as roma": "Roma",
    "ss lazio": "Lazio",
    "atalanta bc": "Atalanta",
    "acf fiorentina": "Fiorentina",
    "bologna fc 1909": "Bologna",
    "torino fc": "Torino",
    "ac monza": "Monza",
    "us lecce": "Lecce",
    "us sassuolo calcio": "Sassuolo",
    "genoa cfc": "Genoa",
    "hellas verona fc": "Verona",
    "cagliari calcio": "Cagliari",
    "frosinone calcio": "Frosinone",
    "empoli fc": "Empoli",
    "udinese calcio": "Udinese",
    "us salernitana 1919": "Salernitana",
    "como 1907": "Como",
    "parma calcio 1913": "Parma",
    "venezia fc": "Venezia",
    # Bundesliga
    "fc bayern münchen": "Bayern Munich",
    "bayern munich": "Bayern Munich",
    "borussia dortmund": "Borussia Dortmund",
    "bayer 04 leverkusen": "Bayer Leverkusen",
    "rb leipzig": "RB Leipzig",
    "vfb stuttgart": "Stuttgart",
    "eintracht frankfurt": "Eintracht Frankfurt",
    "sc freiburg": "Freiburg",
    "tsg 1899 hoffenheim": "Hoffenheim",
    "1. fc union berlin": "Union Berlin",
    "vfl wolfsburg": "Wolfsburg",
    "borussia mönchengladbach": "Borussia M.Gladbach",
    "1. fsv mainz 05": "Mainz 05",
    "fc augsburg": "Augsburg",
    "sv werder bremen": "Werder Bremen",
    "1. fc heidenheim 1846": "Heidenheim",
    "1. fc köln": "FC Cologne",
    "sv darmstadt 98": "Darmstadt",
    # Ligue 1
    "paris saint-germain fc": "Paris Saint Germain",
    "paris saint germain": "Paris Saint Germain",
    "olympique de marseille": "Marseille",
    "as monaco fc": "Monaco",
    "losc lille": "Lille",
    "stade rennais fc 1901": "Rennes",
    "rc lens": "Lens",
    "olympique lyonnais": "Lyon",
    "ogc nice": "Nice",
    "rc strasbourg alsace": "Strasbourg",
    "fc nantes": "Nantes",
    "toulouse fc": "Toulouse",
    "stade de reims": "Reims",
    "montpellier hsc": "Montpellier",
    "stade brestois 29": "Brest",
    "le havre ac": "Le Havre",
    "fc lorient": "Lorient",
    "clermont foot 63": "Clermont Foot",
}


def _normalize_team_name(name: str) -> str:
    """Normaliza nombre de equipo para buscar en Understat."""
    return _TEAM_NAME_MAP.get(name.lower().strip(), name)


async def get_team_xg(team_name: str, league_id: int, season: int = None) -> dict | None:
    """Obtiene datos de xG real de un equipo desde Understat.

    Returns: {"xg": 1.85, "xga": 0.92, "xg_diff": 0.93, "matches": 25}
    o None si no se puede obtener.
    """
    understat_league = UNDERSTAT_LEAGUES.get(league_id)
    if not understat_league:
        return None

    normalized_name = _normalize_team_name(team_name)
    cache_key = f"{normalized_name}_{league_id}".lower()

    # Chequear caché
    cached = _xg_cache.get(cache_key)
    if cached and (time.time() - cached.get("ts", 0)) < _XG_CACHE_TTL:
        return cached

    if season is None:
        from datetime import datetime
        now = datetime.now()
        season = now.year if now.month >= 8 else now.year - 1

    try:
        url = f"https://understat.com/league/{understat_league}/{season}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Understat: HTTP {resp.status} para {url}")
                    return None
                html = await resp.text()

        # Understat embebe datos JSON en script tags
        match = re.search(r"teamsData\s*=\s*JSON\.parse\('(.+?)'\)", html)
        if not match:
            logger.warning("Understat: no se encontró teamsData en HTML")
            return None

        # Decodificar caracteres escapados
        raw = match.group(1).encode().decode("unicode_escape")
        teams_data = json.loads(raw)

        # Buscar el equipo
        for team_id, team_info in teams_data.items():
            title = team_info.get("title", "")
            if title.lower() == normalized_name.lower():
                history = team_info.get("history", [])
                if not history:
                    return None

                total_xg = sum(float(m.get("xG", 0)) for m in history)
                total_xga = sum(float(m.get("xGA", 0)) for m in history)
                n = len(history)

                result = {
                    "xg": total_xg / n if n else 0,
                    "xga": total_xga / n if n else 0,
                    "xg_diff": (total_xg - total_xga) / n if n else 0,
                    "matches": n,
                    "total_xg": total_xg,
                    "total_xga": total_xga,
                    "ts": time.time(),
                }

                _xg_cache[cache_key] = result
                logger.info(f"Understat: {normalized_name} → xG={result['xg']:.2f}, xGA={result['xga']:.2f} ({n} partidos)")
                return result

        logger.warning(f"Understat: equipo '{normalized_name}' no encontrado en {understat_league}")
        return None

    except Exception as e:
        logger.error(f"Understat: error obteniendo datos de {team_name}: {e}")
        return None


async def get_league_xg_averages(league_id: int, season: int = None) -> tuple[float, float] | None:
    """Calcula promedios de xG reales de la liga desde Understat.

    Returns: (avg_home_xg, avg_away_xg) o None si falla.
    """
    understat_league = UNDERSTAT_LEAGUES.get(league_id)
    if not understat_league:
        return None

    if season is None:
        from datetime import datetime
        now = datetime.now()
        season = now.year if now.month >= 8 else now.year - 1

    cache_key = f"league_avg_{league_id}_{season}"
    cached = _xg_cache.get(cache_key)
    if cached and (time.time() - cached.get("ts", 0)) < _XG_CACHE_TTL:
        return cached["home"], cached["away"]

    try:
        url = f"https://understat.com/league/{understat_league}/{season}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

        match = re.search(r"teamsData\s*=\s*JSON\.parse\('(.+?)'\)", html)
        if not match:
            return None

        raw = match.group(1).encode().decode("unicode_escape")
        teams_data = json.loads(raw)

        total_home_xg = 0
        total_away_xg = 0
        total_home_matches = 0
        total_away_matches = 0

        for team_id, team_info in teams_data.items():
            history = team_info.get("history", [])
            for m in history:
                is_home = m.get("h_a") == "h"
                xg = float(m.get("xG", 0))
                if is_home:
                    total_home_xg += xg
                    total_home_matches += 1
                else:
                    total_away_xg += xg
                    total_away_matches += 1

        if total_home_matches > 0 and total_away_matches > 0:
            avg_home = total_home_xg / total_home_matches
            avg_away = total_away_xg / total_away_matches
            _xg_cache[cache_key] = {"home": avg_home, "away": avg_away, "ts": time.time()}
            logger.info(f"Understat league avg: home_xG={avg_home:.2f}, away_xG={avg_away:.2f}")
            return avg_home, avg_away

    except Exception as e:
        logger.error(f"Understat: error calculando promedios de liga: {e}")

    return None
