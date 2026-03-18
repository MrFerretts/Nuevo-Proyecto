import logging
from datetime import datetime, timedelta

import aiohttp
from typing import Optional


logger = logging.getLogger(__name__)

API_BASE = "https://v3.football.api-sports.io"


class FootballStatsService:
    """Servicio de estadísticas de fútbol usando API-Football (api-sports.io).

    Plan gratuito: 100 requests/día. Suficiente para analizar ~20 partidos/día.
    Registro: https://www.api-football.com/
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "x-apisports-key": api_key,
        }

    async def _get(self, endpoint: str, params: dict) -> Optional[list]:
        async with aiohttp.ClientSession() as session:
            url = f"{API_BASE}/{endpoint}"
            logger.info(f"API Request: {url} params={params}")
            async with session.get(
                url,
                headers=self.headers,
                params=params,
            ) as resp:
                if resp.status != 200:
                    logger.error(f"API Error: status={resp.status} for {endpoint}")
                    return None
                data = await resp.json()
                errors = data.get("errors", {})
                if errors:
                    logger.error(f"API Errors: {errors} for {endpoint} params={params}")
                    return None  # No devolver datos vacíos si hay error de API
                results = data.get("response", [])
                logger.info(f"API Response: {endpoint} returned {len(results) if isinstance(results, list) else 'dict'} results")
                return results

    async def get_team_id(self, team_name: str, country: str = "") -> Optional[int]:
        """Busca el ID de un equipo por nombre."""
        params = {"search": team_name}
        results = await self._get("teams", params)
        if not results:
            return None
        # Intentar match exacto primero, luego parcial
        for team in results:
            info = team.get("team", {})
            if info.get("name", "").lower() == team_name.lower():
                return info["id"]
        return results[0]["team"]["id"] if results else None

    async def get_team_stats(self, team_id: int, league_id: int, season: int) -> Optional[dict]:
        """Estadísticas completas de un equipo en una liga/temporada."""
        params = {"team": team_id, "league": league_id, "season": season}
        results = await self._get("teams/statistics", params)
        return results if results else None

    async def get_head_to_head(self, team1_id: int, team2_id: int, last: int = 10) -> list:
        """Historial de enfrentamientos directos.

        Intenta con 'last', si falla usa season 2024 como fallback.
        """
        params = {"h2h": f"{team1_id}-{team2_id}", "last": last}
        results = await self._get("fixtures/headtohead", params)
        if results:
            return results

        # Fallback: usar season permitida en plan gratuito
        for season in [2024, 2023]:
            params = {"h2h": f"{team1_id}-{team2_id}", "season": season}
            results = await self._get("fixtures/headtohead", params)
            if results:
                logger.info(f"H2H fallback worked with season={season}")
                return results[-last:]  # últimos N partidos
        return []

    async def get_team_form(self, team_id: int, last: int = 10) -> list:
        """Últimos partidos de un equipo.

        Intenta con 'last', si falla usa season 2024 como fallback.
        """
        params = {"team": team_id, "last": last}
        results = await self._get("fixtures", params)
        if results:
            return results

        # Fallback: usar season permitida en plan gratuito
        for season in [2024, 2023]:
            params = {"team": team_id, "season": season}
            results = await self._get("fixtures", params)
            if results:
                logger.info(f"Team form fallback worked with season={season} ({len(results)} fixtures)")
                return results[-last:]  # últimos N partidos de esa temporada
        return []

    async def get_standings(self, league_id: int, season: int) -> list:
        """Clasificación de una liga. Intenta season actual, luego fallback a 2024."""
        for s in [season, 2024, 2023]:
            params = {"league": league_id, "season": s}
            results = await self._get("standings", params)
            if results and len(results) > 0:
                standings = results[0].get("league", {}).get("standings", [])
                if standings:
                    logger.info(f"Standings found for league {league_id} season {s}")
                    return standings[0]
        return []

    async def get_fixture_predictions(self, fixture_id: int) -> Optional[dict]:
        """Predicciones de API-Football para un partido (incluye probabilidades)."""
        params = {"fixture": fixture_id}
        results = await self._get("predictions", params)
        return results[0] if results else None

    async def get_upcoming_fixtures(self, league_id: int, season: int = None, next_n: int = 10, odds_api_key: str = "") -> list:
        """Próximos partidos de una liga usando The Odds API.

        El plan gratuito de API-Football no permite acceder a temporadas actuales,
        así que usamos The Odds API para obtener los próximos partidos y los
        transformamos al formato esperado por el resto del código.
        """
        from src.services.odds_service import get_upcoming_games

        sport_key = LEAGUE_TO_ODDS_SPORT.get(league_id)
        if not sport_key:
            logger.warning(f"No Odds API sport_key mapping for league_id={league_id}")
            return []

        logger.info(f"Fetching upcoming fixtures from Odds API: sport_key={sport_key}")
        games = await get_upcoming_games(sport_key, limit=next_n)

        if not games:
            logger.warning(f"No games returned from Odds API for {sport_key}")
            return []

        # Transformar formato Odds API → formato API-Football para compatibilidad
        fixtures = []
        for game in games:
            home_name = game.get("home_team", "?")
            away_name = game.get("away_team", "?")

            # Buscar team IDs en API-Football (el endpoint de búsqueda sí funciona en plan free)
            home_id = await self.get_team_id(home_name)
            away_id = await self.get_team_id(away_name)

            fixture = {
                "fixture": {
                    "id": game.get("id", ""),
                    "date": game.get("commence_time", ""),
                },
                "teams": {
                    "home": {"id": home_id, "name": home_name},
                    "away": {"id": away_id, "name": away_name},
                },
                "_odds_data": game.get("bookmakers", []),
            }
            fixtures.append(fixture)

        logger.info(f"Transformed {len(fixtures)} fixtures from Odds API for league {league_id}")
        return fixtures

    async def get_injuries(self, team_id: int) -> list:
        """Lesiones actuales de un equipo."""
        params = {"team": team_id}
        results = await self._get("injuries", params)
        return results or []


# IDs de ligas populares en API-Football
LEAGUE_IDS = {
    "premier_league": 39,
    "la_liga": 140,
    "serie_a": 135,
    "bundesliga": 78,
    "ligue_1": 61,
    "champions_league": 2,
    "europa_league": 3,
    "liga_mx": 262,
    "mls": 253,
    "copa_libertadores": 13,
}

LEAGUE_NAMES = {
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    78: "Bundesliga",
    61: "Ligue 1",
    2: "Champions League",
    3: "Europa League",
    262: "Liga MX",
    253: "MLS",
    13: "Copa Libertadores",
}

# Mapeo de league_id (API-Football) a sport_key (The Odds API)
LEAGUE_TO_ODDS_SPORT = {
    39: "soccer_epl",
    140: "soccer_spain_la_liga",
    135: "soccer_italy_serie_a",
    78: "soccer_germany_bundesliga",
    61: "soccer_france_ligue_one",
    2: "soccer_uefa_champs_league",
    3: "soccer_uefa_europa_league",
    262: "soccer_mexico_ligamx",
    253: "soccer_usa_mls",
    13: "soccer_conmebol_copa_libertadores",
}
