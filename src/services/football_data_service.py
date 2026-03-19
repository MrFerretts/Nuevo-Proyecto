"""Servicio de estadísticas usando football-data.org (datos ACTUALES de temporada).

Plan gratuito: 10 req/min, 12 competiciones top (Premier, La Liga, Serie A,
Bundesliga, Ligue 1, Champions League, etc.).

Registro: https://www.football-data.org/client/register
"""

import logging
from datetime import datetime, timedelta

import aiohttp

logger = logging.getLogger(__name__)

API_BASE = "https://api.football-data.org/v4"

# Mapeo league_id interno → competition code de football-data.org
COMPETITION_MAP = {
    39: "PL",      # Premier League
    140: "PD",     # La Liga (Primera División)
    135: "SA",     # Serie A
    78: "BL1",     # Bundesliga
    61: "FL1",     # Ligue 1
    2: "CL",       # Champions League
    3: "EC",       # Europa Conference (Europa League no está en free)
    262: None,     # Liga MX - no disponible en free
    253: None,     # MLS - no disponible en free
    13: None,      # Copa Libertadores - no disponible en free
}

# Competiciones disponibles en plan gratuito
FREE_COMPETITIONS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL": "Champions League",
    "ELC": "Championship",
    "DED": "Eredivisie",
    "PPL": "Primeira Liga",
    "BSA": "Serie A Brasil",
    "WC": "World Cup",
    "EC": "European Championship",
}


class FootballDataService:
    """Servicio principal de stats con datos ACTUALES via football-data.org."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"X-Auth-Token": api_key}

    async def _get(self, endpoint: str, params: dict = None):
        """Hace un GET a football-data.org."""
        if not self.api_key:
            logger.error("football-data.org: API KEY NO CONFIGURADA - todas las llamadas fallarán")
            return None
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{API_BASE}/{endpoint}"
                logger.info(f"football-data.org: GET {url} params={params}")
                async with session.get(url, headers=self.headers, params=params or {}) as resp:
                    if resp.status == 429:
                        logger.warning("football-data.org: Rate limit alcanzado (10 req/min)")
                        return None
                    if resp.status == 401:
                        logger.error("football-data.org: API KEY INVÁLIDA (401 Unauthorized)")
                        return None
                    if resp.status == 403:
                        logger.error(f"football-data.org: Acceso denegado (403) para {endpoint} - puede que esta liga no esté en el plan gratuito")
                        return None
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(f"football-data.org: {resp.status} - {text[:200]}")
                        return None
                    data = await resp.json()
                    logger.info(f"football-data.org: OK {endpoint} - keys={list(data.keys()) if isinstance(data, dict) else 'list'}")
                    return data
        except aiohttp.ClientError as e:
            logger.error(f"football-data.org: Error de conexión - {e}")
            return None
        except Exception as e:
            logger.error(f"football-data.org: Error inesperado - {e}")
            return None

    # ── Partidos de una competición ─────────────────────────────────

    async def get_team_matches(self, team_id: int, limit: int = 15) -> list:
        """Últimos partidos FINALIZADOS de un equipo (temporada actual)."""
        data = await self._get(
            f"teams/{team_id}/matches",
            {"status": "FINISHED", "limit": limit},
        )
        if not data:
            logger.warning(f"football-data.org: No se obtuvieron partidos para team_id={team_id}")
            return []
        matches = data.get("matches", [])
        # Ordenar de más reciente a más antiguo para que forma y pesos sean correctos
        matches.sort(key=lambda m: m.get("utcDate", ""), reverse=True)
        logger.info(f"football-data.org: {len(matches)} partidos encontrados para team_id={team_id}")
        return matches

    async def get_head_to_head(self, match_id: int, limit: int = 10) -> tuple:
        """Historial H2H de un partido específico.

        Returns: (aggregates dict, matches list)
        """
        data = await self._get(
            f"matches/{match_id}/head2head",
            {"limit": limit},
        )
        if not data:
            return {}, []
        return data.get("aggregates", {}), data.get("matches", [])

    async def get_standings(self, competition_code: str) -> list:
        """Clasificación actual de una competición."""
        data = await self._get(f"competitions/{competition_code}/standings")
        if not data:
            return []
        standings = data.get("standings", [])
        # Buscar la tabla TOTAL (no home/away)
        for s in standings:
            if s.get("type") == "TOTAL":
                return s.get("table", [])
        return standings[0].get("table", []) if standings else []

    async def get_upcoming_matches(self, competition_code: str, limit: int = 10) -> list:
        """Próximos partidos programados de una competición."""
        data = await self._get(
            f"competitions/{competition_code}/matches",
            {"status": "SCHEDULED,TIMED"},
        )
        if not data:
            return []
        matches = data.get("matches", [])
        return matches[:limit]

    async def get_competition_matches(self, competition_code: str, status: str = "FINISHED") -> list:
        """Todos los partidos de una competición con un estado dado."""
        data = await self._get(
            f"competitions/{competition_code}/matches",
            {"status": status},
        )
        if not data:
            return []
        return data.get("matches", [])

    async def get_scorers(self, competition_code: str, limit: int = 10) -> list:
        """Goleadores de una competición."""
        data = await self._get(
            f"competitions/{competition_code}/scorers",
            {"limit": limit},
        )
        if not data:
            return []
        return data.get("scorers", [])

    async def get_match_by_id(self, match_id: int) -> dict | None:
        """Obtiene un partido específico por su ID.

        Returns: dict con datos del partido o None si no se encuentra.
        """
        data = await self._get(f"matches/{match_id}")
        return data if data else None

    async def get_finished_matches(self, competition_code: str, date_from: str = None, date_to: str = None) -> list:
        """Obtiene partidos FINALIZADOS de una competición en un rango de fechas."""
        params = {"status": "FINISHED"}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to
        data = await self._get(f"competitions/{competition_code}/matches", params)
        if not data:
            return []
        return data.get("matches", [])

    # ── Cálculos estadísticos ───────────────────────────────────────

    def calc_team_stats(self, matches: list, team_id: int) -> dict:
        """Calcula estadísticas completas de un equipo desde sus partidos reales.

        Devuelve: forma, goles, BTTS%, Over 1.5/2.5/3.5%, clean sheets,
        rachas, splits local/visitante, etc.
        """
        if not matches:
            return self._empty_stats()

        results = []
        goals_scored = []
        goals_conceded = []
        over15_count = 0
        over25_count = 0
        over35_count = 0
        btts_count = 0
        clean_sheets = 0
        home_wins = 0
        home_matches = 0
        away_wins = 0
        away_matches = 0
        first_half_goals = 0
        # Splits local/visitante
        home_goals_scored = []
        home_goals_conceded = []
        away_goals_scored = []
        away_goals_conceded = []
        n = len(matches)

        for m in matches:
            score = m.get("score", {})
            ft = score.get("fullTime", {})
            ht = score.get("halfTime", {})
            home_goals = ft.get("home") or 0
            away_goals = ft.get("away") or 0
            total_goals = home_goals + away_goals
            ht_home = ht.get("home") or 0
            ht_away = ht.get("away") or 0

            is_home = m.get("homeTeam", {}).get("id") == team_id
            team_scored = home_goals if is_home else away_goals
            team_conceded = away_goals if is_home else home_goals

            goals_scored.append(team_scored)
            goals_conceded.append(team_conceded)

            if total_goals > 1.5:
                over15_count += 1
            if total_goals > 2.5:
                over25_count += 1
            if total_goals > 3.5:
                over35_count += 1
            if home_goals > 0 and away_goals > 0:
                btts_count += 1
            if team_conceded == 0:
                clean_sheets += 1

            first_half_goals += (ht_home + ht_away)

            if is_home:
                home_matches += 1
                home_goals_scored.append(team_scored)
                home_goals_conceded.append(team_conceded)
                if team_scored > team_conceded:
                    results.append("W")
                    home_wins += 1
                elif team_scored == team_conceded:
                    results.append("D")
                else:
                    results.append("L")
            else:
                away_matches += 1
                away_goals_scored.append(team_scored)
                away_goals_conceded.append(team_conceded)
                if team_scored > team_conceded:
                    results.append("W")
                    away_wins += 1
                elif team_scored == team_conceded:
                    results.append("D")
                else:
                    results.append("L")

        # Forma ponderada con decay exponencial (half_life=4 partidos)
        import math
        weights = [math.exp(-0.693 * i / 4) for i in range(len(results))]
        weighted_score = 0
        total_weight = sum(weights)
        for i, r in enumerate(results):
            if r == "W":
                weighted_score += 3 * weights[i]
            elif r == "D":
                weighted_score += 1 * weights[i]

        form_score = (weighted_score / (total_weight * 3) * 100) if total_weight > 0 else 50

        # Rachas
        current_streak = ""
        streak_count = 0
        if results:
            current_streak = results[0]
            streak_count = 1
            for r in results[1:]:
                if r == current_streak:
                    streak_count += 1
                else:
                    break

        return {
            "form_score": form_score,
            "form_detail": "".join(results[:5][::-1]),
            "goals_scored_avg": sum(goals_scored) / n if n else 0,
            "goals_conceded_avg": sum(goals_conceded) / n if n else 0,
            "over15_pct": (over15_count / n * 100) if n else 0,
            "over25_pct": (over25_count / n * 100) if n else 0,
            "over35_pct": (over35_count / n * 100) if n else 0,
            "btts_pct": (btts_count / n * 100) if n else 0,
            "clean_sheet_pct": (clean_sheets / n * 100) if n else 0,
            "home_win_pct": (home_wins / home_matches * 100) if home_matches else 0,
            "away_win_pct": (away_wins / away_matches * 100) if away_matches else 0,
            "avg_total_goals": sum(g1 + g2 for g1, g2 in zip(goals_scored, goals_conceded)) / n if n else 0,
            "avg_first_half_goals": first_half_goals / n if n else 0,
            "matches_played": n,
            "wins": results.count("W"),
            "draws": results.count("D"),
            "losses": results.count("L"),
            "streak": f"{streak_count}{current_streak}" if current_streak else "?",
            "goals_scored_total": sum(goals_scored),
            "goals_conceded_total": sum(goals_conceded),
            # Splits local/visitante
            "home_goals_scored_avg": sum(home_goals_scored) / home_matches if home_matches else 0,
            "home_goals_conceded_avg": sum(home_goals_conceded) / home_matches if home_matches else 0,
            "away_goals_scored_avg": sum(away_goals_scored) / away_matches if away_matches else 0,
            "away_goals_conceded_avg": sum(away_goals_conceded) / away_matches if away_matches else 0,
            "home_matches": home_matches,
            "away_matches": away_matches,
        }

    def calc_h2h_stats(self, matches: list, home_team_id: int) -> dict:
        """Calcula estadísticas H2H desde partidos reales."""
        if not matches:
            return {
                "home_wins": 0, "away_wins": 0, "draws": 0,
                "avg_goals": 0, "btts_pct": 0, "over25_pct": 0,
                "total_matches": 0,
            }

        home_wins = 0
        away_wins = 0
        draws = 0
        total_goals_list = []
        btts = 0
        over25 = 0

        for m in matches:
            score = m.get("score", {}).get("fullTime", {})
            h = score.get("home") or 0
            a = score.get("away") or 0
            total = h + a
            total_goals_list.append(total)

            if h > 0 and a > 0:
                btts += 1
            if total > 2.5:
                over25 += 1

            is_home = m.get("homeTeam", {}).get("id") == home_team_id
            if is_home:
                if h > a:
                    home_wins += 1
                elif h < a:
                    away_wins += 1
                else:
                    draws += 1
            else:
                if a > h:
                    home_wins += 1
                elif a < h:
                    away_wins += 1
                else:
                    draws += 1

        n = len(matches)
        return {
            "home_wins": home_wins,
            "away_wins": away_wins,
            "draws": draws,
            "avg_goals": sum(total_goals_list) / n if n else 0,
            "btts_pct": (btts / n * 100) if n else 0,
            "over25_pct": (over25 / n * 100) if n else 0,
            "total_matches": n,
        }

    def find_in_standings(self, standings: list, team_id: int) -> dict:
        """Busca un equipo en la clasificación."""
        for entry in standings:
            if entry.get("team", {}).get("id") == team_id:
                return {
                    "position": entry.get("position", 0),
                    "points": entry.get("points", 0),
                    "played": entry.get("playedGames", 0),
                    "won": entry.get("won", 0),
                    "draw": entry.get("draw", 0),
                    "lost": entry.get("lost", 0),
                    "goals_for": entry.get("goalsFor", 0),
                    "goals_against": entry.get("goalsAgainst", 0),
                    "goal_diff": entry.get("goalDifference", 0),
                    "form": entry.get("form", ""),
                }
        return {"position": 0, "points": 0, "played": 0, "won": 0, "draw": 0,
                "lost": 0, "goals_for": 0, "goals_against": 0, "goal_diff": 0, "form": ""}

    def _empty_stats(self):
        return {
            "form_score": 50, "form_detail": "?",
            "goals_scored_avg": 0, "goals_conceded_avg": 0,
            "over15_pct": 0, "over25_pct": 0, "over35_pct": 0,
            "btts_pct": 0, "clean_sheet_pct": 0,
            "home_win_pct": 0, "away_win_pct": 0,
            "avg_total_goals": 0, "avg_first_half_goals": 0,
            "matches_played": 0, "wins": 0, "draws": 0, "losses": 0,
            "streak": "?", "goals_scored_total": 0, "goals_conceded_total": 0,
            "home_goals_scored_avg": 0, "home_goals_conceded_avg": 0,
            "away_goals_scored_avg": 0, "away_goals_conceded_avg": 0,
            "home_matches": 0, "away_matches": 0,
        }
