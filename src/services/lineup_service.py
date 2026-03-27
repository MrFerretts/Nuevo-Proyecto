"""Servicio de alineaciones pre-partido.

Fuentes:
1. football-data.org — endpoint /matches/{id} incluye lineups para partidos
   con estado TIMED/IN_PLAY (generalmente ~1h antes del kickoff).
2. API-Football — endpoint /fixtures/lineups con lineup confirmado.

Sin alineaciones, los xG son promedios de temporada.
Con alineaciones, se puede ajustar por jugadores clave ausentes/presentes.
"""

import logging
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class MatchLineup:
    """Alineación de un equipo para un partido específico."""
    team_name: str
    formation: str = ""
    starting_xi: list = field(default_factory=list)  # [{"name": ..., "position": ..., "number": ...}]
    bench: list = field(default_factory=list)
    coach: str = ""
    available: bool = False  # True si la alineación está confirmada


@dataclass
class MatchLineups:
    """Alineaciones de ambos equipos."""
    home: MatchLineup = None
    away: MatchLineup = None
    source: str = ""  # "football-data.org", "api-football"

    @property
    def available(self) -> bool:
        return bool(self.home and self.home.available and self.away and self.away.available)


async def get_lineups_fd(match_id: int, api_key: str) -> MatchLineups | None:
    """Obtiene alineaciones desde football-data.org.

    El endpoint /matches/{id} incluye lineups cuando el partido
    está en estado TIMED (próximo a jugarse) o IN_PLAY.
    """
    if not api_key or not match_id:
        return None

    url = f"https://api.football-data.org/v4/matches/{match_id}"
    headers = {"X-Auth-Token": api_key}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.debug(f"Lineups FD: status {resp.status} for match {match_id}")
                    return None
                data = await resp.json()

        home_lineup_raw = data.get("homeTeam", {}).get("lineup", [])
        away_lineup_raw = data.get("awayTeam", {}).get("lineup", [])

        if not home_lineup_raw and not away_lineup_raw:
            logger.info(f"Lineups FD: no lineup data yet for match {match_id}")
            return None

        home_team_name = data.get("homeTeam", {}).get("name", "Local")
        away_team_name = data.get("awayTeam", {}).get("name", "Visitante")
        home_formation = data.get("homeTeam", {}).get("formation", "")
        away_formation = data.get("awayTeam", {}).get("formation", "")
        home_coach = data.get("homeTeam", {}).get("coach", {}).get("name", "")
        away_coach = data.get("awayTeam", {}).get("coach", {}).get("name", "")

        home_bench_raw = data.get("homeTeam", {}).get("bench", [])
        away_bench_raw = data.get("awayTeam", {}).get("bench", [])

        def _parse_players(raw: list) -> list:
            return [
                {
                    "name": p.get("name", "?"),
                    "position": p.get("position", ""),
                    "number": p.get("shirtNumber", 0),
                }
                for p in raw
            ]

        lineups = MatchLineups(
            home=MatchLineup(
                team_name=home_team_name,
                formation=home_formation,
                starting_xi=_parse_players(home_lineup_raw),
                bench=_parse_players(home_bench_raw),
                coach=home_coach,
                available=len(home_lineup_raw) >= 11,
            ),
            away=MatchLineup(
                team_name=away_team_name,
                formation=away_formation,
                starting_xi=_parse_players(away_lineup_raw),
                bench=_parse_players(away_bench_raw),
                coach=away_coach,
                available=len(away_lineup_raw) >= 11,
            ),
            source="football-data.org",
        )

        logger.info(f"Lineups FD: {home_team_name} ({home_formation}, {len(home_lineup_raw)} players) vs "
                     f"{away_team_name} ({away_formation}, {len(away_lineup_raw)} players)")
        return lineups

    except Exception as e:
        logger.warning(f"Lineups FD error: {e}")
        return None


async def get_lineups_api_football(fixture_id, api_key: str) -> MatchLineups | None:
    """Obtiene alineaciones desde API-Football (api-sports.io).

    Endpoint: /fixtures/lineups?fixture={id}
    Disponible ~1h antes del kickoff.
    """
    if not api_key or not fixture_id:
        return None

    url = "https://v3.football.api-sports.io/fixtures/lineups"
    headers = {"x-apisports-key": api_key}
    params = {"fixture": fixture_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        results = data.get("response", [])
        if len(results) < 2:
            return None

        def _parse_team(team_data: dict) -> MatchLineup:
            team_info = team_data.get("team", {})
            formation = team_data.get("formation", "")
            coach_info = team_data.get("coach", {})
            start_xi = team_data.get("startXI", [])
            subs = team_data.get("substitutes", [])

            players = [
                {
                    "name": p.get("player", {}).get("name", "?"),
                    "position": p.get("player", {}).get("pos", ""),
                    "number": p.get("player", {}).get("number", 0),
                }
                for p in start_xi
            ]
            bench = [
                {
                    "name": p.get("player", {}).get("name", "?"),
                    "position": p.get("player", {}).get("pos", ""),
                    "number": p.get("player", {}).get("number", 0),
                }
                for p in subs
            ]

            return MatchLineup(
                team_name=team_info.get("name", "?"),
                formation=formation,
                starting_xi=players,
                bench=bench,
                coach=coach_info.get("name", ""),
                available=len(players) >= 11,
            )

        lineups = MatchLineups(
            home=_parse_team(results[0]),
            away=_parse_team(results[1]),
            source="api-football",
        )
        logger.info(f"Lineups API-Football: {lineups.home.team_name} ({lineups.home.formation}) vs "
                     f"{lineups.away.team_name} ({lineups.away.formation})")
        return lineups

    except Exception as e:
        logger.warning(f"Lineups API-Football error: {e}")
        return None


async def get_match_lineups(
    fd_match_id: int = None,
    api_football_fixture_id=None,
    fd_api_key: str = "",
    football_api_key: str = "",
) -> MatchLineups | None:
    """Intenta obtener alineaciones de cualquier fuente disponible.

    Prioridad: football-data.org > API-Football
    """
    # 1. football-data.org
    if fd_match_id and fd_api_key:
        lineups = await get_lineups_fd(fd_match_id, fd_api_key)
        if lineups and lineups.available:
            return lineups

    # 2. API-Football
    if api_football_fixture_id and football_api_key:
        lineups = await get_lineups_api_football(api_football_fixture_id, football_api_key)
        if lineups and lineups.available:
            return lineups

    return None


def format_lineups(lineups: MatchLineups) -> str:
    """Formatea alineaciones para Telegram."""
    if not lineups or not lineups.available:
        return ""

    lines = [
        "",
        "📋 *ALINEACIONES CONFIRMADAS*",
        f"📡 _Fuente: {lineups.source}_",
    ]

    for team_lineup, emoji in [(lineups.home, "🏠"), (lineups.away, "✈️")]:
        if not team_lineup or not team_lineup.available:
            continue

        formation_str = f" ({team_lineup.formation})" if team_lineup.formation else ""
        lines.append(f"\n{emoji} *{team_lineup.team_name}*{formation_str}")

        if team_lineup.coach:
            lines.append(f"  👔 DT: {team_lineup.coach}")

        # Titular XI por posición
        by_pos = {"Goalkeeper": [], "Defender": [], "Midfielder": [], "Forward": [],
                  "G": [], "D": [], "M": [], "F": [], "": []}
        for p in team_lineup.starting_xi:
            pos = p.get("position", "")
            if pos in by_pos:
                by_pos[pos].append(p)
            else:
                by_pos[""].append(p)

        pos_labels = [
            (["Goalkeeper", "G"], "🧤"),
            (["Defender", "D"], "🛡"),
            (["Midfielder", "M"], "🎯"),
            (["Forward", "F"], "⚡"),
        ]

        for pos_keys, pos_emoji in pos_labels:
            players = []
            for pk in pos_keys:
                players.extend(by_pos.get(pk, []))
            if players:
                names = ", ".join(p["name"] for p in players)
                lines.append(f"  {pos_emoji} {names}")

        # Si no hay posiciones, listar todos
        if not any(by_pos.get(pk) for pks, _ in pos_labels for pk in pks):
            names = ", ".join(p["name"] for p in team_lineup.starting_xi[:11])
            lines.append(f"  ⚽ {names}")

    return "\n".join(lines)


def assess_lineup_impact(lineups: MatchLineups) -> dict:
    """Evalúa el impacto de las alineaciones en el análisis.

    Retorna metadata que puede usarse para ajustar probabilidades.
    Esto es un placeholder — se puede mejorar con datos de jugadores específicos.
    """
    if not lineups or not lineups.available:
        return {"available": False}

    return {
        "available": True,
        "home_formation": lineups.home.formation if lineups.home else "",
        "away_formation": lineups.away.formation if lineups.away else "",
        "home_xi_count": len(lineups.home.starting_xi) if lineups.home else 0,
        "away_xi_count": len(lineups.away.starting_xi) if lineups.away else 0,
        "source": lineups.source,
    }
