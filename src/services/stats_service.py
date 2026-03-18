import aiohttp
from typing import Optional


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

    async def _get(self, endpoint: str, params: dict) -> Optional[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/{endpoint}",
                headers=self.headers,
                params=params,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("response", [])

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
        """Historial de enfrentamientos directos."""
        params = {"h2h": f"{team1_id}-{team2_id}", "last": last}
        results = await self._get("fixtures/headtohead", params)
        return results or []

    async def get_team_form(self, team_id: int, last: int = 10) -> list:
        """Últimos partidos de un equipo."""
        params = {"team": team_id, "last": last}
        results = await self._get("fixtures", params)
        return results or []

    async def get_standings(self, league_id: int, season: int) -> list:
        """Clasificación de una liga."""
        params = {"league": league_id, "season": season}
        results = await self._get("standings", params)
        if results and len(results) > 0:
            standings = results[0].get("league", {}).get("standings", [])
            return standings[0] if standings else []
        return []

    async def get_fixture_predictions(self, fixture_id: int) -> Optional[dict]:
        """Predicciones de API-Football para un partido (incluye probabilidades)."""
        params = {"fixture": fixture_id}
        results = await self._get("predictions", params)
        return results[0] if results else None

    async def get_upcoming_fixtures(self, league_id: int, season: int, next_n: int = 10) -> list:
        """Próximos partidos de una liga."""
        params = {"league": league_id, "season": season, "next": next_n}
        results = await self._get("fixtures", params)
        return results or []

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
