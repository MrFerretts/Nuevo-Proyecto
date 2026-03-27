"""Motor de análisis de apuestas deportivas.

Analiza partidos usando múltiples factores estadísticos para encontrar
apuestas con valor (value bets) donde la probabilidad real estimada
es mayor que la que implican las cuotas de las casas.

Incluye modelo de Poisson para estimación de goles y resultados exactos.
"""

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TeamAnalysis:
    name: str
    form_score: float = 0.0          # 0-100 basado en últimos partidos
    form_detail: str = ""             # WLWWW etc.
    goals_scored_avg: float = 0.0     # Goles a favor por partido
    goals_conceded_avg: float = 0.0   # Goles en contra por partido
    home_win_pct: float = 0.0         # % victorias en casa
    away_win_pct: float = 0.0         # % victorias fuera
    clean_sheets_pct: float = 0.0     # % porterías a cero
    btts_pct: float = 0.0             # % ambos marcan
    over25_pct: float = 0.0           # % partidos con +2.5 goles
    league_position: int = 0
    points: int = 0
    injuries_count: int = 0
    # Campos de contexto
    home_goals_scored_avg: float = 0.0
    home_goals_conceded_avg: float = 0.0
    away_goals_scored_avg: float = 0.0
    away_goals_conceded_avg: float = 0.0
    streak: str = ""
    matches_played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    avg_total_goals: float = 0.0
    over15_pct: float = 0.0
    over35_pct: float = 0.0
    injuries: list = field(default_factory=list)
    # === NUEVOS CAMPOS v2 ===
    real_xg: float = 0.0              # xG real de Understat (por partido)
    real_xga: float = 0.0             # xGA real de Understat (por partido)
    rest_days: int = -1               # Días desde último partido (-1 = desconocido)
    last_match_date: str = ""         # Fecha del último partido
    # === CAMPOS FBref (StatsBomb) v3 ===
    possession_pct: float = 0.0       # Posesión real
    progressive_passes_p90: float = 0.0   # Pases progresivos por 90
    progressive_carries_p90: float = 0.0  # Carries progresivos por 90
    sca_p90: float = 0.0             # Shot-Creating Actions por 90
    gca_p90: float = 0.0             # Goal-Creating Actions por 90
    pressures_p90: float = 0.0       # Presiones por 90
    pressure_success_pct: float = 0.0 # % éxito en presiones
    tackles_won_p90: float = 0.0     # Tackles ganados por 90
    interceptions_p90: float = 0.0   # Intercepciones por 90
    shots_p90: float = 0.0           # Tiros por 90
    shots_on_target_pct: float = 0.0 # % tiros a puerta
    fbref_xg: float = 0.0            # xG de StatsBomb (cross-ref con Understat)
    fbref_xga: float = 0.0           # xGA de StatsBomb


@dataclass
class MatchAnalysis:
    home_team: TeamAnalysis
    away_team: TeamAnalysis
    h2h_home_wins: int = 0
    h2h_away_wins: int = 0
    h2h_draws: int = 0
    h2h_avg_goals: float = 0.0
    h2h_btts_pct: float = 0.0

    # Probabilidades estimadas
    home_win_prob: float = 0.0
    draw_prob: float = 0.0
    away_win_prob: float = 0.0
    over25_prob: float = 0.0
    btts_prob: float = 0.0

    # Cuotas del mercado
    home_odds: float = 0.0
    draw_odds: float = 0.0
    away_odds: float = 0.0

    # Sugerencias de apuesta
    suggestions: list = field(default_factory=list)


@dataclass
class BetSuggestion:
    market: str           # "1X2", "Over/Under", "BTTS", "Doble Oportunidad", "Handicap"
    pick: str             # "Home Win", "Over 2.5", "BTTS Yes"
    estimated_prob: float # Probabilidad estimada (0-1)
    implied_prob: float   # Probabilidad implícita de la cuota (0-1)
    odds: float           # Cuota del mercado
    value: float          # Edge = estimated - implied (positivo = valor)
    confidence: str       # baja, media, alta, muy_alta
    stake: int            # 1-5 recomendado
    reasoning: list       # Lista de razones
    composite_score: int = 0  # Score compuesto 0-100 (se calcula post-análisis)


# ══════════════════════════════════════════════════════════════════
# PROMEDIOS DE LIGA DINÁMICOS
# ══════════════════════════════════════════════════════════════════

# Promedios históricos por liga (goles por partido: local, visitante)
# Fuente: promedios reales de las últimas 5 temporadas
LEAGUE_AVERAGES = {
    # Liga ID → (avg_home_goals, avg_away_goals)
    39:  (1.55, 1.18),  # Premier League (alta)
    140: (1.40, 1.08),  # La Liga (más táctica)
    135: (1.38, 1.05),  # Serie A (defensiva)
    78:  (1.65, 1.30),  # Bundesliga (más goles)
    61:  (1.42, 1.05),  # Ligue 1
    2:   (1.50, 1.15),  # Champions League
    3:   (1.40, 1.10),  # Europa League
    262: (1.35, 1.00),  # Liga MX
    253: (1.48, 1.20),  # MLS
    13:  (1.38, 1.05),  # Copa Libertadores
}

# Valor por defecto si la liga no está mapeada
DEFAULT_LEAGUE_AVG = (1.45, 1.15)


def get_league_averages(league_id: int = 0) -> tuple[float, float]:
    """Obtiene promedios de goles por liga. Usa datos estáticos como base."""
    return LEAGUE_AVERAGES.get(league_id, DEFAULT_LEAGUE_AVG)


def calculate_league_averages_from_standings(standings: list) -> tuple[float, float]:
    """Calcula promedios reales de la liga desde la tabla de clasificación.

    Si hay datos suficientes en los standings (goles/partidos), calcula
    el promedio real. Si no, retorna None para usar el estático.
    """
    if not standings or len(standings) < 4:
        return None, None

    total_home_goals = 0
    total_away_goals = 0
    total_matches = 0

    for entry in standings:
        played = entry.get("playedGames", 0) or entry.get("played", 0)
        goals_for = entry.get("goalsFor", 0)
        goals_against = entry.get("goalsAgainst", 0)

        # football-data.org tiene home/away splits en standings
        home_data = entry.get("home", {})
        away_data = entry.get("away", {})

        if home_data and home_data.get("goalsFor") is not None:
            total_home_goals += home_data.get("goalsFor", 0)
            total_away_goals += away_data.get("goalsFor", 0)
            home_played = home_data.get("playedGames", 0) or home_data.get("played", 0)
            total_matches += home_played
        elif played > 0 and goals_for > 0:
            # Sin splits, estimar: ~55% de goles se meten en casa
            total_home_goals += goals_for * 0.55
            total_away_goals += goals_for * 0.45
            total_matches += played // 2

    if total_matches < 20:
        return None, None

    avg_home = total_home_goals / total_matches
    avg_away = total_away_goals / total_matches

    # Sanity check
    avg_home = max(0.8, min(2.5, avg_home))
    avg_away = max(0.5, min(2.0, avg_away))

    logger.info(f"Promedios dinámicos calculados: home={avg_home:.2f}, away={avg_away:.2f} (de {total_matches} partidos)")
    return avg_home, avg_away


# ══════════════════════════════════════════════════════════════════
# MODELO DE POISSON
# ══════════════════════════════════════════════════════════════════

def _poisson_prob(lam: float, k: int) -> float:
    """Probabilidad de Poisson: P(X=k) dado lambda."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_match_probs(home_xg: float, away_xg: float, max_goals: int = 7) -> dict:
    """Calcula probabilidades de resultado usando distribución de Poisson.

    Genera una matriz de probabilidades para cada combinación de goles
    y deriva probabilidades de 1X2, Over/Under, BTTS, etc.

    Args:
        home_xg: Goles esperados del equipo local
        away_xg: Goles esperados del equipo visitante
        max_goals: Máximo de goles a considerar por equipo

    Returns: dict con probabilidades de todos los mercados
    """
    # Construir matriz de probabilidades
    matrix = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            matrix[(h, a)] = _poisson_prob(home_xg, h) * _poisson_prob(away_xg, a)

    # 1X2
    home_win = sum(p for (h, a), p in matrix.items() if h > a)
    draw = sum(p for (h, a), p in matrix.items() if h == a)
    away_win = sum(p for (h, a), p in matrix.items() if h < a)

    # Over/Under
    over15 = sum(p for (h, a), p in matrix.items() if h + a > 1.5)
    over25 = sum(p for (h, a), p in matrix.items() if h + a > 2.5)
    over35 = sum(p for (h, a), p in matrix.items() if h + a > 3.5)

    # BTTS
    btts_yes = sum(p for (h, a), p in matrix.items() if h > 0 and a > 0)

    # Doble oportunidad
    home_or_draw = home_win + draw
    away_or_draw = away_win + draw
    home_or_away = home_win + away_win

    # Resultados exactos más probables (top 5)
    exact_scores = sorted(matrix.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "over15": over15,
        "under15": 1 - over15,
        "over25": over25,
        "under25": 1 - over25,
        "over35": over35,
        "under35": 1 - over35,
        "btts_yes": btts_yes,
        "btts_no": 1 - btts_yes,
        "home_or_draw": home_or_draw,
        "away_or_draw": away_or_draw,
        "home_or_away": home_or_away,
        "exact_scores": exact_scores,
        "home_xg": home_xg,
        "away_xg": away_xg,
    }


# ══════════════════════════════════════════════════════════════════
# FUNCIONES DE ANÁLISIS LEGACY (para API-Football fallback)
# ══════════════════════════════════════════════════════════════════

def _exponential_decay_weights(n: int, half_life: int = 4) -> list[float]:
    """Genera pesos con decay exponencial.

    half_life: después de cuántos partidos el peso se reduce a la mitad.
    Partido 0 (más reciente) = peso 1.0, partido half_life = peso 0.5, etc.
    """
    return [math.exp(-0.693 * i / half_life) for i in range(n)]


def analyze_form(fixtures: list, team_id: int) -> tuple[float, str]:
    """Analiza la forma reciente de un equipo con decay exponencial.

    Los partidos más recientes pesan exponencialmente más.
    half_life=4: el partido de hace 4 jornadas pesa la mitad del último.

    Returns: (score 0-100, detail string like "WWDLW")
    """
    if not fixtures:
        return 50.0, "?"

    results = []

    for fx in fixtures[:10]:  # Últimos 10 partidos
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        home_id = teams.get("home", {}).get("id")
        is_home = home_id == team_id
        home_goals = goals.get("home", 0) or 0
        away_goals = goals.get("away", 0) or 0

        if is_home:
            if home_goals > away_goals:
                results.append("W")
            elif home_goals == away_goals:
                results.append("D")
            else:
                results.append("L")
        else:
            if away_goals > home_goals:
                results.append("W")
            elif away_goals == home_goals:
                results.append("D")
            else:
                results.append("L")

    # Decay exponencial: últimos partidos pesan MUCHO más
    weights = _exponential_decay_weights(len(results), half_life=4)
    weighted_score = 0
    total_weight = sum(weights)

    for i, r in enumerate(results):
        if r == "W":
            weighted_score += 3 * weights[i]
        elif r == "D":
            weighted_score += 1 * weights[i]

    score = (weighted_score / (total_weight * 3) * 100) if total_weight > 0 else 50
    detail = "".join(results[:5])  # Mostrar últimos 5
    return score, detail


def analyze_goals(fixtures: list, team_id: int) -> dict:
    """Analiza estadísticas de goles."""
    if not fixtures:
        return {"scored_avg": 0, "conceded_avg": 0, "over25_pct": 0, "btts_pct": 0, "clean_sheet_pct": 0}

    scored = []
    conceded = []
    over25 = 0
    btts = 0
    clean_sheets = 0
    n = len(fixtures)

    for fx in fixtures:
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        home_id = teams.get("home", {}).get("id")
        home_goals = goals.get("home", 0) or 0
        away_goals = goals.get("away", 0) or 0
        total_goals = home_goals + away_goals

        is_home = home_id == team_id
        team_scored = home_goals if is_home else away_goals
        team_conceded = away_goals if is_home else home_goals

        scored.append(team_scored)
        conceded.append(team_conceded)

        if total_goals > 2.5:
            over25 += 1
        if home_goals > 0 and away_goals > 0:
            btts += 1
        if team_conceded == 0:
            clean_sheets += 1

    return {
        "scored_avg": sum(scored) / n if n else 0,
        "conceded_avg": sum(conceded) / n if n else 0,
        "over25_pct": (over25 / n * 100) if n else 0,
        "btts_pct": (btts / n * 100) if n else 0,
        "clean_sheet_pct": (clean_sheets / n * 100) if n else 0,
    }


def analyze_h2h(fixtures: list, home_team_id: int) -> dict:
    """Analiza el historial de enfrentamientos directos."""
    if not fixtures:
        return {"home_wins": 0, "away_wins": 0, "draws": 0, "avg_goals": 0, "btts_pct": 0}

    home_wins = 0
    away_wins = 0
    draws = 0
    total_goals_list = []
    btts = 0

    for fx in fixtures:
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        fx_home_id = teams.get("home", {}).get("id")
        home_goals = goals.get("home", 0) or 0
        away_goals = goals.get("away", 0) or 0

        total_goals_list.append(home_goals + away_goals)
        if home_goals > 0 and away_goals > 0:
            btts += 1

        # Determinar quién ganó relativo al equipo que analizamos como "home"
        if fx_home_id == home_team_id:
            if home_goals > away_goals:
                home_wins += 1
            elif home_goals < away_goals:
                away_wins += 1
            else:
                draws += 1
        else:
            if away_goals > home_goals:
                home_wins += 1
            elif away_goals < home_goals:
                away_wins += 1
            else:
                draws += 1

    n = len(fixtures)
    return {
        "home_wins": home_wins,
        "away_wins": away_wins,
        "draws": draws,
        "avg_goals": sum(total_goals_list) / n if n else 0,
        "btts_pct": (btts / n * 100) if n else 0,
    }


def find_team_in_standings(standings: list, team_id: int) -> dict:
    """Encuentra un equipo en la clasificación."""
    for entry in standings:
        if entry.get("team", {}).get("id") == team_id:
            return {
                "position": entry.get("rank", 0),
                "points": entry.get("points", 0),
                "home": entry.get("home", {}),
                "away": entry.get("away", {}),
            }
    return {"position": 0, "points": 0, "home": {}, "away": {}}


# ══════════════════════════════════════════════════════════════════
# CÁLCULO DE GOLES ESPERADOS (xG simplificado)
# ══════════════════════════════════════════════════════════════════

def calculate_expected_goals(home: TeamAnalysis, away: TeamAnalysis, h2h: dict,
                              league_avg_home_goals: float = 1.45,
                              league_avg_away_goals: float = 1.15) -> tuple[float, float]:
    """Calcula goles esperados (xG) para cada equipo.

    Usa xG real de Understat cuando está disponible (60% peso),
    combinado con el modelo de fuerza ataque/defensa (40% peso).
    Si no hay xG real, usa solo el modelo clásico.
    """
    # === Modelo clásico: fuerza de ataque/defensa ===
    home_attack = home.goals_scored_avg / league_avg_home_goals if league_avg_home_goals > 0 else 1.0
    home_defense = home.goals_conceded_avg / league_avg_away_goals if league_avg_away_goals > 0 else 1.0
    away_attack = away.goals_scored_avg / league_avg_away_goals if league_avg_away_goals > 0 else 1.0
    away_defense = away.goals_conceded_avg / league_avg_home_goals if league_avg_home_goals > 0 else 1.0

    classic_home_xg = home_attack * away_defense * league_avg_home_goals
    classic_away_xg = away_attack * home_defense * league_avg_away_goals

    # === xG real de Understat y/o FBref (si disponibles) ===
    has_understat_xg = home.real_xg > 0 and away.real_xg > 0
    has_fbref_xg = home.fbref_xg > 0 and away.fbref_xg > 0

    if has_understat_xg or has_fbref_xg:
        # Promedio de xG disponibles (Understat + FBref = más robusto)
        xg_sources_home = []
        xg_sources_away = []

        if has_understat_xg:
            xg_sources_home.append(home.real_xg * (away.real_xga / max(league_avg_home_goals, 0.5)))
            xg_sources_away.append(away.real_xg * (home.real_xga / max(league_avg_away_goals, 0.5)))

        if has_fbref_xg:
            xg_sources_home.append(home.fbref_xg * (away.fbref_xga / max(league_avg_home_goals, 0.5)))
            xg_sources_away.append(away.fbref_xg * (home.fbref_xga / max(league_avg_away_goals, 0.5)))

        real_home_xg = sum(xg_sources_home) / len(xg_sources_home)
        real_away_xg = sum(xg_sources_away) / len(xg_sources_away)

        # Blend: 60% xG real, 40% modelo clásico
        home_xg = 0.6 * real_home_xg + 0.4 * classic_home_xg
        away_xg = 0.6 * real_away_xg + 0.4 * classic_away_xg
        src = "Understat+FBref" if (has_understat_xg and has_fbref_xg) else ("Understat" if has_understat_xg else "FBref")
        logger.info(f"xG blend ({src}): real({real_home_xg:.2f}-{real_away_xg:.2f}) + classic({classic_home_xg:.2f}-{classic_away_xg:.2f}) = {home_xg:.2f}-{away_xg:.2f}")
    else:
        home_xg = classic_home_xg
        away_xg = classic_away_xg

    # Ajuste por H2H (>= 3 partidos, peso reducido: 10%)
    h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)
    if h2h_total >= 3 and h2h.get("avg_goals", 0) > 0:
        h2h_avg = h2h["avg_goals"]
        current_total = home_xg + away_xg
        if current_total > 0:
            h2h_factor = h2h_avg / current_total
            adjustment = 0.10  # Reducido de 0.15 a 0.10
            home_xg *= (1 - adjustment) + (adjustment * h2h_factor)
            away_xg *= (1 - adjustment) + (adjustment * h2h_factor)

    # Ajuste por rendimiento local/visitante
    if home.home_goals_scored_avg > 0 and home.matches_played >= 5:
        home_local_factor = home.home_goals_scored_avg / max(home.goals_scored_avg, 0.1)
        home_xg *= (0.7 + 0.3 * home_local_factor)

    if away.away_goals_scored_avg > 0 and away.matches_played >= 5:
        away_visit_factor = away.away_goals_scored_avg / max(away.goals_scored_avg, 0.1)
        away_xg *= (0.7 + 0.3 * away_visit_factor)

    # === Ajuste por descanso (días entre partidos) ===
    home_xg, away_xg = _adjust_for_rest(home, away, home_xg, away_xg)

    # Clamp
    home_xg = max(0.3, min(4.5, home_xg))
    away_xg = max(0.2, min(4.0, away_xg))

    return home_xg, away_xg


def _calc_tactical_score(team: TeamAnalysis) -> float:
    """Calcula un score táctico compuesto usando datos de FBref.

    Combina acciones creativas, pressing y acciones progresivas.
    Retorna 0 si no hay datos de FBref.
    """
    if team.sca_p90 <= 0 and team.progressive_passes_p90 <= 0:
        return 0.0

    # Pesos: SCA y progressive actions son los mejores predictores
    score = 0.0
    weights = 0.0

    if team.sca_p90 > 0:
        score += team.sca_p90 * 3.0  # SCA ~25-35 per90 para equipos top
        weights += 3.0
    if team.gca_p90 > 0:
        score += team.gca_p90 * 10.0  # GCA ~3-5 per90
        weights += 10.0
    if team.progressive_passes_p90 > 0:
        score += team.progressive_passes_p90 * 1.0  # ~40-60 per90
        weights += 1.0
    if team.progressive_carries_p90 > 0:
        score += team.progressive_carries_p90 * 1.5  # ~30-50 per90
        weights += 1.5
    if team.shots_p90 > 0:
        score += team.shots_p90 * 2.0  # ~12-18 per90
        weights += 2.0

    return score / weights if weights > 0 else 0.0


def _adjust_for_rest(home: TeamAnalysis, away: TeamAnalysis,
                     home_xg: float, away_xg: float) -> tuple[float, float]:
    """Ajusta xG por diferencia en días de descanso.

    Investigación muestra que equipos con <3 días de descanso rinden ~5-8% menos,
    mientras que >6 días no tiene beneficio adicional vs 4-5 días.
    """
    if home.rest_days < 0 or away.rest_days < 0:
        return home_xg, away_xg

    def rest_factor(days: int) -> float:
        if days <= 2:
            return 0.93  # -7% (partido cada 2 días, fatiga severa)
        elif days == 3:
            return 0.97  # -3% (turnaround rápido)
        elif days <= 5:
            return 1.00  # Normal
        elif days <= 7:
            return 1.01  # Leve beneficio
        else:
            return 1.00  # >7 días puede significar falta de ritmo

    home_rest = rest_factor(home.rest_days)
    away_rest = rest_factor(away.rest_days)

    if home_rest != 1.0 or away_rest != 1.0:
        logger.info(f"Rest adjustment: home={home.rest_days}d ({home_rest:.2f}x), away={away.rest_days}d ({away_rest:.2f}x)")

    return home_xg * home_rest, away_xg * away_rest


# ══════════════════════════════════════════════════════════════════
# ESTIMACIÓN DE PROBABILIDADES (modelo mejorado)
# ══════════════════════════════════════════════════════════════════

def estimate_probabilities(home: TeamAnalysis, away: TeamAnalysis, h2h: dict,
                           league_id: int = 0, standings: list = None,
                           market_odds: dict = None,
                           calibration: dict = None) -> dict:
    """Estima probabilidades combinando Poisson con ajustes contextuales.

    NUEVO v2: Si hay odds del mercado, las usa como ancla (el mercado es ~96%
    eficiente en ligas top). El modelo solo AJUSTA sobre el mercado, no intenta
    superarlo desde cero. Esto es cómo trabajan los profesionales.

    NUEVO v3: Si hay datos de calibración histórica, aplica correcciones de sesgo.
    Si el modelo sobreestima Over 2.5 por un 8%, reduce esa probabilidad en 8%.

    1. Calcula xG con fuerza de ataque/defensa (+ xG real si disponible)
    2. Genera probabilidades base con Poisson
    3. Si hay odds del mercado → blend 55% mercado + 45% modelo
    4. Ajusta con forma, posición, H2H, lesiones, descanso
    5. Aplica correcciones de calibración históricas
    6. Normaliza y devuelve
    """
    # Paso 1: Promedios de liga
    avg_home, avg_away = DEFAULT_LEAGUE_AVG
    if standings:
        dyn_home, dyn_away = calculate_league_averages_from_standings(standings)
        if dyn_home is not None:
            avg_home, avg_away = dyn_home, dyn_away
            logger.info(f"Usando promedios DINÁMICOS: home={avg_home:.2f}, away={avg_away:.2f}")
        else:
            avg_home, avg_away = get_league_averages(league_id)
    elif league_id:
        avg_home, avg_away = get_league_averages(league_id)

    # Paso 2: xG y Poisson
    home_xg, away_xg = calculate_expected_goals(home, away, h2h, avg_home, avg_away)
    poisson = poisson_match_probs(home_xg, away_xg)

    p_home = poisson["home_win"]
    p_draw = poisson["draw"]
    p_away = poisson["away_win"]

    # Paso 3: MARKET ANCHOR - blend con odds del mercado si disponibles
    # El mercado es más eficiente que cualquier modelo para ligas top.
    # Nuestro valor está en los AJUSTES que el mercado no captura bien.
    if market_odds and market_odds.get("home", 0) > 1 and market_odds.get("away", 0) > 1:
        market_home = _remove_overround(market_odds, "home")
        market_draw = _remove_overround(market_odds, "draw")
        market_away = _remove_overround(market_odds, "away")

        if market_home > 0 and market_away > 0:
            # Blend: 55% mercado, 45% nuestro modelo
            p_home = 0.55 * market_home + 0.45 * p_home
            p_draw = 0.55 * market_draw + 0.45 * p_draw
            p_away = 0.55 * market_away + 0.45 * p_away
            logger.info(f"Market anchor: market({market_home:.1%}/{market_draw:.1%}/{market_away:.1%}) "
                        f"+ model({poisson['home_win']:.1%}/{poisson['draw']:.1%}/{poisson['away_win']:.1%}) "
                        f"= blend({p_home:.1%}/{p_draw:.1%}/{p_away:.1%})")

    # Paso 4: Ajustes contextuales (reducidos porque el market anchor ya captura mucho)
    adjustment_weight = 0.6 if market_odds else 1.0  # Ajustes menores si ya tenemos mercado

    # Forma reciente (máx ±4%)
    form_diff = (home.form_score - away.form_score) / 100
    form_shift = form_diff * 0.04 * adjustment_weight
    p_home += form_shift
    p_away -= form_shift

    # Posición en liga (máx ±2.5%)
    if home.league_position > 0 and away.league_position > 0:
        max_pos = max(home.league_position, away.league_position, 20)
        pos_diff = (away.league_position - home.league_position) / max_pos
        pos_shift = pos_diff * 0.025 * adjustment_weight
        p_home += pos_shift
        p_away -= pos_shift

    # H2H (máx ±3%)
    h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)
    if h2h_total >= 3:
        h2h_home_rate = h2h["home_wins"] / h2h_total
        h2h_away_rate = h2h["away_wins"] / h2h_total
        h2h_shift = (h2h_home_rate - h2h_away_rate) * 0.03 * adjustment_weight
        p_home += h2h_shift
        p_away -= h2h_shift

    # Tactical edge (FBref advanced stats, máx ±3%)
    # Equipos con más SCA, progressive actions y pressing tienen ventaja táctica
    home_tactical = _calc_tactical_score(home)
    away_tactical = _calc_tactical_score(away)
    if home_tactical > 0 and away_tactical > 0:
        tactical_diff = (home_tactical - away_tactical) / max(home_tactical, away_tactical)
        tactical_shift = tactical_diff * 0.03 * adjustment_weight
        p_home += tactical_shift
        p_away -= tactical_shift

    # Lesiones (1.5% por lesionado, máx 8%)
    if home.injuries_count > 0:
        injury_penalty = min(home.injuries_count * 0.015, 0.08)
        p_home -= injury_penalty
        p_away += injury_penalty * 0.5
        p_draw += injury_penalty * 0.5
    if away.injuries_count > 0:
        injury_penalty = min(away.injuries_count * 0.015, 0.08)
        p_away -= injury_penalty
        p_home += injury_penalty * 0.5
        p_draw += injury_penalty * 0.5

    # Normalizar
    p_home = max(0.03, p_home)
    p_draw = max(0.03, p_draw)
    p_away = max(0.03, p_away)
    total = p_home + p_draw + p_away
    p_home /= total
    p_draw /= total
    p_away /= total

    # Paso 5: Calibración — ajustar según historial de aciertos/errores
    p_over25 = poisson["over25"]
    p_btts = poisson["btts_yes"]
    calibration_applied = []

    if calibration and calibration.get("sample_size", 0) >= 10:
        # Over 2.5: corregir sesgo
        over25_cal = calibration.get("over25")
        if over25_cal and over25_cal.get("total", 0) >= 8:
            bias = over25_cal["bias"]
            if abs(bias) > 0.03:  # Solo corregir si el sesgo es significativo (>3%)
                # Corrección gradual: 50% del sesgo (para no sobrecompensar)
                correction = bias * 0.5
                p_over25 = max(0.05, min(0.95, p_over25 - correction))
                calibration_applied.append(f"Over2.5 bias={bias:+.1%} → corrección={-correction:+.1%}")
                logger.info(f"Calibración Over2.5: bias={bias:+.3f}, corrección={-correction:+.3f}")

        # BTTS: corregir sesgo
        btts_cal = calibration.get("btts")
        if btts_cal and btts_cal.get("total", 0) >= 8:
            bias = btts_cal["bias"]
            if abs(bias) > 0.03:
                correction = bias * 0.5
                p_btts = max(0.05, min(0.95, p_btts - correction))
                calibration_applied.append(f"BTTS bias={bias:+.1%} → corrección={-correction:+.1%}")
                logger.info(f"Calibración BTTS: bias={bias:+.3f}, corrección={-correction:+.3f}")

        # xG: corregir sesgo en goles esperados
        xg_cal = calibration.get("xg")
        if xg_cal and xg_cal.get("total", 0) >= 8:
            xg_bias = xg_cal["bias"]
            if abs(xg_bias) > 0.2:  # Solo si sobreestima/subestima >0.2 goles
                # Ajustar xG proporcionalmente
                total_xg = home_xg + away_xg
                if total_xg > 0:
                    correction_factor = 1 - (xg_bias * 0.3 / total_xg)  # 30% del sesgo
                    correction_factor = max(0.8, min(1.2, correction_factor))
                    home_xg *= correction_factor
                    away_xg *= correction_factor
                    calibration_applied.append(f"xG bias={xg_bias:+.2f} goles → factor={correction_factor:.2f}")
                    logger.info(f"Calibración xG: bias={xg_bias:+.2f}, factor={correction_factor:.3f}")

    if calibration_applied:
        logger.info(f"Calibraciones aplicadas: {'; '.join(calibration_applied)}")

    return {
        "home_win": p_home,
        "draw": p_draw,
        "away_win": p_away,
        "over15": poisson["over15"],
        "over25": p_over25,
        "over35": poisson["over35"],
        "btts": p_btts,
        "home_or_draw": p_home + p_draw,
        "away_or_draw": p_away + p_draw,
        "home_or_away": p_home + p_away,
        "exact_scores": poisson["exact_scores"],
        "expected_goals": home_xg + away_xg,
        "home_xg": home_xg,
        "away_xg": away_xg,
        "has_real_xg": home.real_xg > 0 and away.real_xg > 0,
        "has_fbref": home.sca_p90 > 0 and away.sca_p90 > 0,
        "has_market_anchor": bool(market_odds and market_odds.get("home", 0) > 1),
        "calibration_applied": calibration_applied,
    }


def _remove_overround(odds: dict, outcome: str) -> float:
    """Convierte odds a probabilidad real eliminando el margen del bookmaker.

    Usa el método proporcional: divide prob implícita entre la suma total.
    """
    home_odds = odds.get("home", 0)
    draw_odds = odds.get("draw", 0)
    away_odds = odds.get("away", 0)

    if home_odds <= 1 or draw_odds <= 1 or away_odds <= 1:
        return 0

    total_implied = (1 / home_odds) + (1 / draw_odds) + (1 / away_odds)
    if total_implied <= 0:
        return 0

    implied = 1 / odds.get(outcome, 1)
    return implied / total_implied


# ══════════════════════════════════════════════════════════════════
# DETECCIÓN DE VALUE BETS (ampliado con más mercados)
# ══════════════════════════════════════════════════════════════════

def find_value_bets(probs: dict, odds: dict) -> list[BetSuggestion]:
    """Encuentra apuestas con valor comparando probabilidades estimadas vs cuotas.

    Mercados soportados:
    - 1X2 (Victoria Local / Empate / Victoria Visitante)
    - Over/Under 1.5, 2.5, 3.5
    - BTTS Sí/No
    - Doble Oportunidad (1X, X2, 12)
    """
    suggestions = []

    markets = [
        # 1X2
        ("1X2", "Victoria Local", probs["home_win"], odds.get("home", 0)),
        ("1X2", "Empate", probs["draw"], odds.get("draw", 0)),
        ("1X2", "Victoria Visitante", probs["away_win"], odds.get("away", 0)),
        # Over/Under
        ("Over/Under", "Over 1.5 Goles", probs.get("over15", 0), odds.get("over15", 0)),
        ("Over/Under", "Under 1.5 Goles", 1 - probs.get("over15", 1), odds.get("under15", 0)),
        ("Over/Under", "Over 2.5 Goles", probs.get("over25", 0), odds.get("over25", 0)),
        ("Over/Under", "Under 2.5 Goles", 1 - probs.get("over25", 1), odds.get("under25", 0)),
        ("Over/Under", "Over 3.5 Goles", probs.get("over35", 0), odds.get("over35", 0)),
        ("Over/Under", "Under 3.5 Goles", 1 - probs.get("over35", 1), odds.get("under35", 0)),
        # BTTS
        ("BTTS", "Ambos Marcan - Sí", probs.get("btts", 0), odds.get("btts_yes", 0)),
        ("BTTS", "Ambos Marcan - No", 1 - probs.get("btts", 1), odds.get("btts_no", 0)),
        # Doble Oportunidad
        ("Doble Oportunidad", "Local o Empate (1X)", probs.get("home_or_draw", 0), odds.get("home_or_draw", 0)),
        ("Doble Oportunidad", "Visitante o Empate (X2)", probs.get("away_or_draw", 0), odds.get("away_or_draw", 0)),
        ("Doble Oportunidad", "Local o Visitante (12)", probs.get("home_or_away", 0), odds.get("home_or_away", 0)),
    ]

    for market, pick, est_prob, market_odds in markets:
        if market_odds <= 1.0 or est_prob <= 0:
            continue

        implied_prob = 1 / market_odds
        value = est_prob - implied_prob

        # Edge mínimo: 5% (subido de 3% para reducir falsos positivos)
        if value > 0.05:
            # Kelly Criterion: fracción óptima del bankroll
            # f* = (bp - q) / b donde b=odds-1, p=prob ganar, q=1-p
            b = market_odds - 1
            kelly_fraction = (b * est_prob - (1 - est_prob)) / b if b > 0 else 0
            kelly_fraction = max(0, min(0.25, kelly_fraction))  # Cap al 25%

            # Confianza y stake basados en Kelly + edge
            if kelly_fraction > 0.10:
                confidence = "muy_alta"
                stake = 5
            elif kelly_fraction > 0.06:
                confidence = "alta"
                stake = 4
            elif kelly_fraction > 0.03:
                confidence = "media"
                stake = 3
            else:
                confidence = "baja"
                stake = 2

            # Reducir stake para cuotas altas (más varianza)
            if market_odds > 3.5:
                stake = max(1, stake - 1)

            # Reducir confianza si no tenemos market anchor o xG real
            has_market = probs.get("has_market_anchor", False)
            has_xg = probs.get("has_real_xg", False)
            has_fbref = probs.get("has_fbref", False)
            if not has_market and not has_xg and not has_fbref:
                stake = max(1, stake - 1)
                if confidence == "muy_alta":
                    confidence = "alta"

            reasoning = _build_reasoning(market, pick, est_prob, implied_prob,
                                         value, market_odds, probs, kelly_fraction)

            suggestions.append(BetSuggestion(
                market=market,
                pick=pick,
                estimated_prob=est_prob,
                implied_prob=implied_prob,
                odds=market_odds,
                value=value,
                confidence=confidence,
                stake=stake,
                reasoning=reasoning,
            ))

    # Ordenar por valor descendente
    suggestions.sort(key=lambda x: x.value, reverse=True)
    return suggestions


# ══════════════════════════════════════════════════════════════════
# SCORE DE CONFIANZA COMPUESTO (0-100)
# ══════════════════════════════════════════════════════════════════

def calculate_composite_scores(
    suggestions: list,
    probs: dict,
    brain_data: dict = None,
    odds_history: list = None,
    data_quality_score: int = 0,
) -> list:
    """Calcula el score compuesto para cada BetSuggestion.

    Fórmula:
      Score = (edge × 30%) + (calidad_datos × 25%) + (conviction_IA × 20%)
            + (calibración_histórica × 15%) + (line_movement × 10%)

    Cada componente se normaliza a 0-100 antes de ponderar.

    Thresholds:
      >= 70  → APOSTAR
      50-69  → WATCHLIST
      < 50   → PASAR

    Args:
        suggestions: lista de BetSuggestion a puntuar
        probs: dict de probabilidades (incluye has_real_xg, etc.)
        brain_data: output del cerebro IA (conviction, evaluaciones)
        odds_history: historial de odds para line movement
        data_quality_score: score 0-100 del semáforo de calidad

    Returns: las mismas suggestions con composite_score actualizado
    """
    if not suggestions:
        return suggestions

    # Pre-calcular componentes compartidos

    # 2. Calidad de datos (0-100) — viene del semáforo
    dq_score = min(100, max(0, data_quality_score))

    # 3. Conviction IA (0-100) — de brain_data
    ia_conviction_raw = 5  # default neutral
    ia_evaluations = {}
    if brain_data:
        ia_conviction_raw = brain_data.get("best_bet", {}).get("conviction", 5)
        # Map evaluations by pick name for per-suggestion lookup
        for ev in brain_data.get("value_bets_evaluation", []):
            pick_name = ev.get("pick", "").lower()
            ia_evaluations[pick_name] = ev

    # 4. Calibración histórica (0-100)
    cal_applied = probs.get("calibration_applied", [])
    # Si hay calibración activa, el modelo se ha corregido → más confiable
    # Sin calibración con pocas muestras → menos confiable
    if cal_applied:
        cal_score = 80  # Calibración activa = bueno
    elif probs.get("has_market_anchor"):
        cal_score = 60  # Market anchor compensa falta de calibración propia
    else:
        cal_score = 30  # Sin calibración ni market anchor

    # 5. Line movement (0-100)
    lm_score = _score_line_movement(odds_history)

    # Calcular por sugerencia
    for s in suggestions:
        # 1. Edge (0-100): normalizar edge [0%, 20%+] → [0, 100]
        edge_pct = s.value  # ya es decimal (0.05 = 5%)
        edge_score = min(100, (edge_pct / 0.20) * 100)

        # 3b. Conviction IA per-suggestion
        suggestion_conviction = ia_conviction_raw * 10  # 0-10 → 0-100

        # Buscar evaluación específica de la IA para este pick
        ia_penalty = 0  # Penalización directa si IA rechaza
        pick_lower = s.pick.lower()
        for ev_key, ev_data in ia_evaluations.items():
            if ev_key in pick_lower or pick_lower in ev_key:
                verdict = ev_data.get("verdict", "")
                if verdict == "CONFIRMAR":
                    suggestion_conviction = min(100, suggestion_conviction + 20)
                elif verdict == "RECHAZAR":
                    suggestion_conviction = max(0, suggestion_conviction - 40)
                    ia_penalty = 15  # Penalty extra: IA dice NO
                elif verdict == "PRECAUCIÓN":
                    suggestion_conviction = max(0, suggestion_conviction - 15)
                    ia_penalty = 5
                break

        # 5b. Line movement per-pick: ¿la línea se mueve a favor de este pick?
        pick_lm = lm_score
        if odds_history and len(odds_history) >= 2:
            pick_lm = _score_line_movement_for_pick(odds_history, s.pick)

        # Composite score con pesos
        score = (
            edge_score * 0.30 +
            dq_score * 0.25 +
            suggestion_conviction * 0.20 +
            cal_score * 0.15 +
            pick_lm * 0.10
        ) - ia_penalty  # Penalización directa por rechazo IA

        s.composite_score = int(min(100, max(0, round(score))))

    return suggestions


def _score_line_movement(odds_history: list) -> int:
    """Puntúa el line movement general (0-100).

    50 = neutral (sin movimiento o datos insuficientes)
    >50 = movimiento detectado (dinero entrando)
    <50 = movimiento contrario
    """
    if not odds_history or len(odds_history) < 2:
        return 50  # Neutral

    first = odds_history[0]
    last = odds_history[-1]

    total_shift = 0
    for key in ["home_odds", "draw_odds", "away_odds"]:
        old = first.get(key, 0)
        new = last.get(key, 0)
        if old > 1 and new > 1:
            shift = abs((1 / new) - (1 / old))
            total_shift += shift

    # Normalizar: 0 shift = 50, >0.15 total shift = 80+
    return min(90, int(50 + total_shift * 200))


def _score_line_movement_for_pick(odds_history: list, pick: str) -> int:
    """Puntúa si el line movement favorece un pick específico.

    Si la cuota del pick BAJA (dinero entrando) → score alto.
    Si SUBE (dinero saliendo) → score bajo.
    """
    if not odds_history or len(odds_history) < 2:
        return 50

    first = odds_history[0]
    last = odds_history[-1]

    pick_lower = pick.lower()

    # Mapear pick a odds key
    if "local" in pick_lower or "home" in pick_lower or "1x" in pick_lower:
        key = "home_odds"
    elif "visitante" in pick_lower or "away" in pick_lower or "x2" in pick_lower:
        key = "away_odds"
    elif "empate" in pick_lower or "draw" in pick_lower:
        key = "draw_odds"
    elif "over" in pick_lower:
        return 50  # Totals no suelen tener line movement tracking
    elif "under" in pick_lower:
        return 50
    else:
        return 50

    old = first.get(key, 0)
    new = last.get(key, 0)
    if old <= 1 or new <= 1:
        return 50

    # Cuota bajó = dinero entra = favorable
    shift = (1 / new) - (1 / old)
    # shift > 0 = cuota bajó = favorable, shift < 0 = cuota subió = desfavorable
    # Normalizar: ±0.10 shift = ±30 points
    return int(min(90, max(10, 50 + shift * 300)))


def format_composite_score(score: int) -> str:
    """Formatea el score compuesto para display."""
    if score >= 70:
        bar = "🟩" * (score // 10) + "⬜" * (10 - score // 10)
        label = "APOSTAR"
        emoji = "✅"
    elif score >= 50:
        bar = "🟨" * (score // 10) + "⬜" * (10 - score // 10)
        label = "WATCHLIST"
        emoji = "👀"
    else:
        bar = "🟥" * (score // 10) + "⬜" * (10 - score // 10)
        label = "PASAR"
        emoji = "⛔"
    return f"{emoji} *{score}/100* {bar} → *{label}*"


def _build_reasoning(market: str, pick: str, est_prob: float, implied_prob: float,
                     value: float, odds: float, probs: dict = None,
                     kelly: float = 0) -> list[str]:
    """Construye las razones detrás de una sugerencia."""
    reasons = []
    reasons.append(f"Prob estimada: {est_prob:.1%} vs mercado: {implied_prob:.1%}")
    reasons.append(f"Edge: +{value:.1%} | Kelly: {kelly:.1%} del bankroll")

    # Calidad de datos
    if probs:
        data_quality = []
        if probs.get("has_real_xg"):
            data_quality.append("xG real")
        if probs.get("has_fbref"):
            data_quality.append("FBref/StatsBomb")
        if probs.get("has_market_anchor"):
            data_quality.append("anclado al mercado")
        if data_quality:
            reasons.append(f"Datos: {', '.join(data_quality)}")
        else:
            reasons.append("⚠️ Sin xG real ni odds del mercado - fiabilidad reducida")

    if probs and "home_xg" in probs:
        total_xg = probs["home_xg"] + probs["away_xg"]
        if "Over" in pick or "Under" in pick:
            reasons.append(f"xG total: {total_xg:.2f}")
        elif "BTTS" in pick or "Ambos" in pick:
            reasons.append(f"xG: {probs['home_xg']:.2f} vs {probs['away_xg']:.2f}")

    if odds >= 2.0 and odds <= 3.0:
        reasons.append("Cuota en rango óptimo (2.0-3.0)")
    elif odds < 1.5:
        reasons.append("Cuota baja - considerar en combinada")

    return reasons


# ══════════════════════════════════════════════════════════════════
# FORMATO DEL REPORTE
# ══════════════════════════════════════════════════════════════════

def analyze_line_movement(odds_history: list) -> list[str]:
    """Analiza movimiento de cuotas para detectar dinero inteligente.

    Si tenemos 2+ snapshots, compara las odds iniciales vs las más recientes.
    Un movimiento de >5% en la implied probability es significativo.

    Returns: líneas de texto para el reporte
    """
    if not odds_history or len(odds_history) < 2:
        return []

    first = odds_history[0]
    last = odds_history[-1]
    hours_diff = 0
    try:
        t1 = datetime.fromisoformat(first["snapshot_at"])
        t2 = datetime.fromisoformat(last["snapshot_at"])
        hours_diff = (t2 - t1).total_seconds() / 3600
    except Exception:
        pass

    if hours_diff < 1:
        return []

    lines = []
    movements = []

    for label, key in [("Local", "home_odds"), ("Empate", "draw_odds"), ("Visitante", "away_odds")]:
        old_odds = first.get(key, 0)
        new_odds = last.get(key, 0)
        if old_odds > 1 and new_odds > 1:
            old_implied = 1 / old_odds
            new_implied = 1 / new_odds
            shift = new_implied - old_implied

            if abs(shift) > 0.03:  # >3% cambio en implied prob
                direction = "⬇️" if new_odds < old_odds else "⬆️"
                action = "Dinero ENTRANDO" if new_odds < old_odds else "Dinero SALIENDO"
                movements.append(
                    f"  {direction} {label}: {old_odds:.2f} → {new_odds:.2f} ({action})"
                )

    if movements:
        lines.append("")
        lines.append(f"📈 *LINE MOVEMENT* _(últimas {hours_diff:.0f}h)_")
        lines.extend(movements)
        # Detectar steam move (movimiento fuerte unidireccional)
        home_old = first.get("home_odds", 0)
        home_new = last.get("home_odds", 0)
        away_old = first.get("away_odds", 0)
        away_new = last.get("away_odds", 0)
        if home_old > 1 and home_new > 1:
            home_shift = (1 / home_new) - (1 / home_old)
            if home_shift > 0.06:
                lines.append("  🔥 *STEAM MOVE en LOCAL* — dinero inteligente fuerte")
            elif home_shift < -0.06:
                lines.append("  🔥 *STEAM MOVE en VISITANTE* — dinero inteligente fuerte")

    return lines


def _build_data_quality(
    home: TeamAnalysis, away: TeamAnalysis, h2h: dict,
    probs: dict, odds_history: list = None,
) -> dict:
    """Construye el semáforo de calidad de datos del análisis.

    Evalúa cada fuente de datos y muestra al usuario qué tan fiable
    es el análisis basándose en qué datos reales están disponibles.

    Returns: {"lines": [...], "summary": str, "score": int (0-100)}
    """
    checks = []
    score = 0
    max_score = 0

    # 1. Forma actualizada (partidos recientes reales)
    max_score += 25
    home_has_form = home.matches_played >= 5 and home.form_detail != "?"
    away_has_form = away.matches_played >= 5 and away.form_detail != "?"

    # Detectar datos obsoletos (último partido > 30 días)
    stale = False
    if home.last_match_date or away.last_match_date:
        try:
            for lmd in [home.last_match_date, away.last_match_date]:
                if lmd:
                    last_dt = datetime.fromisoformat(lmd.replace("Z", "+00:00"))
                    days_ago = (datetime.now(last_dt.tzinfo) - last_dt).days if last_dt.tzinfo else (datetime.now() - last_dt).days
                    if days_ago > 30:
                        stale = True
        except Exception:
            pass

    if home_has_form and away_has_form and not stale:
        checks.append(("Forma actualizada", "green", f"{home.matches_played}+{away.matches_played} partidos"))
        score += 25
    elif home_has_form and away_has_form and stale:
        checks.append(("Forma desactualizada", "yellow", f"datos de hace >30 días"))
        score += 15
    elif home_has_form or away_has_form:
        n = home.matches_played + away.matches_played
        checks.append(("Forma parcial", "yellow", f"solo {n} partidos"))
        score += 12
    else:
        checks.append(("Forma reciente", "red", "sin datos de partidos"))

    # 2. xG real (Understat)
    max_score += 25
    if probs.get("has_real_xg"):
        checks.append(("xG real (Understat)", "green", f"{home.real_xg:.2f} / {away.real_xg:.2f}"))
        score += 25
    elif home.real_xg > 0 or away.real_xg > 0:
        checks.append(("xG parcial", "yellow", "solo 1 equipo"))
        score += 10
    else:
        checks.append(("xG real", "red", "no disponible — usando promedios"))

    # 3. FBref / StatsBomb
    max_score += 20
    if probs.get("has_fbref"):
        checks.append(("FBref/StatsBomb", "green", f"SCA {home.sca_p90:.1f} vs {away.sca_p90:.1f}"))
        score += 20
    elif home.sca_p90 > 0 or away.sca_p90 > 0:
        checks.append(("FBref parcial", "yellow", "solo 1 equipo"))
        score += 8
    else:
        checks.append(("FBref avanzados", "red", "sin pressing/SCA/progresión"))

    # 4. Odds del mercado (market anchor)
    max_score += 20
    if probs.get("has_market_anchor"):
        checks.append(("Odds del mercado", "green", "market anchor activo (55/45)"))
        score += 20
    else:
        checks.append(("Odds del mercado", "red", "sin cuotas — modelo puro"))

    # 5. H2H
    max_score += 5
    h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)
    if h2h_total >= 3:
        checks.append(("H2H histórico", "green", f"{h2h_total} enfrentamientos"))
        score += 5
    elif h2h_total > 0:
        checks.append(("H2H limitado", "yellow", f"solo {h2h_total}"))
        score += 2
    else:
        checks.append(("H2H", "red", "sin historial directo"))

    # 6. Calibración (learning)
    max_score += 5
    cal = probs.get("calibration_applied", [])
    if cal:
        checks.append(("Calibración IA", "green", f"{len(cal)} correcciones"))
        score += 5
    else:
        checks.append(("Calibración", "yellow", "sin correcciones (pocas muestras)"))

    # Build semáforo visual
    pct = (score / max_score * 100) if max_score else 0
    if pct >= 80:
        grade = "ALTA"
        grade_emoji = "🟢"
    elif pct >= 50:
        grade = "MEDIA"
        grade_emoji = "🟡"
    else:
        grade = "BAJA"
        grade_emoji = "🔴"

    color_map = {"green": "✅", "yellow": "🟡", "red": "❌"}

    lines = [f"🚦 *CALIDAD DE DATOS: {grade_emoji} {grade}* ({pct:.0f}%)"]
    for label, color, detail in checks:
        emoji = color_map[color]
        lines.append(f"  {emoji} {label} — _{detail}_")

    # Advertencia si calidad baja
    if pct < 50:
        lines.append("")
        lines.append("  ⚠️ _Análisis con datos limitados. Confía más en las odds del mercado._")

    summary_parts = [label for label, color, _ in checks if color == "green"]
    summary = " + ".join(summary_parts) if summary_parts else "Solo modelo básico"

    return {"lines": lines, "summary": summary, "score": score}


def format_analysis_report(
    home: TeamAnalysis,
    away: TeamAnalysis,
    h2h: dict,
    probs: dict,
    suggestions: list[BetSuggestion],
    odds_history: list = None,
) -> str:
    """Genera un reporte completo de análisis formateado para Telegram."""
    # Detectar si los datos están vacíos (API falló)
    data_missing = (home.form_detail == "?" and away.form_detail == "?" and
                    home.goals_scored_avg == 0 and away.goals_scored_avg == 0)

    lines = [
        f"🔬 *ANÁLISIS COMPLETO*",
        f"🏟 *{home.name} vs {away.name}*",
        "",
    ]

    if data_missing:
        lines.extend([
            "⚠️ *ATENCIÓN: No se pudieron obtener estadísticas reales.*",
            "Posibles causas:",
            "• API key no configurada o inválida",
            "• Rate limit excedido (10 req/min)",
            "• Liga no disponible en plan gratuito",
            "",
            "Revisa los logs de Railway para más detalles.",
            "",
        ])

    lines.extend([
        f"{'═' * 28}",
        "",
        f"📊 *FORMA RECIENTE*",
        f"🏠 {home.name}: {home.form_detail} ({home.form_score:.0f}/100)",
        f"✈️ {away.name}: {away.form_detail} ({away.form_score:.0f}/100)",
    ])

    # Rachas
    if home.streak and home.streak != "?" and away.streak and away.streak != "?":
        lines.append(f"🔥 Rachas: {home.name} {home.streak} | {away.name} {away.streak}")

    lines.extend([
        "",
        f"⚽ *GOLES (promedio por partido)*",
        f"🏠 {home.name}: {home.goals_scored_avg:.1f} a favor | {home.goals_conceded_avg:.1f} en contra",
        f"✈️ {away.name}: {away.goals_scored_avg:.1f} a favor | {away.goals_conceded_avg:.1f} en contra",
        "",
        f"📈 *TENDENCIAS*",
        f"🏠 {home.name}: Over 2.5 en {home.over25_pct:.0f}% | BTTS {home.btts_pct:.0f}% | CS {home.clean_sheets_pct:.0f}%",
        f"✈️ {away.name}: Over 2.5 en {away.over25_pct:.0f}% | BTTS {away.btts_pct:.0f}% | CS {away.clean_sheets_pct:.0f}%",
    ])

    if home.league_position > 0:
        lines.extend([
            "",
            f"🏆 *CLASIFICACIÓN*",
            f"🏠 {home.name}: {home.league_position}º ({home.points} pts)",
            f"✈️ {away.name}: {away.league_position}º ({away.points} pts)",
        ])

    h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)
    if h2h_total > 0:
        lines.extend([
            "",
            f"🔄 *H2H (últimos {h2h_total} partidos)*",
            f"🏠 Victorias {home.name}: {h2h['home_wins']}",
            f"🤝 Empates: {h2h['draws']}",
            f"✈️ Victorias {away.name}: {h2h['away_wins']}",
            f"⚽ Promedio goles: {h2h['avg_goals']:.1f} | BTTS: {h2h['btts_pct']:.0f}%",
        ])

    # Lesiones
    if home.injuries or away.injuries:
        lines.extend(["", "🏥 *LESIONES/BAJAS*"])
        if home.injuries:
            injured_names = ", ".join(home.injuries[:5])
            lines.append(f"🏠 {home.name}: {injured_names}")
        if away.injuries:
            injured_names = ", ".join(away.injuries[:5])
            lines.append(f"✈️ {away.name}: {injured_names}")

    # ══ SEMÁFORO DE CALIDAD DE DATOS ══
    dq = _build_data_quality(home, away, h2h, probs, odds_history)
    lines.extend(["", f"{'═' * 28}", ""])
    lines.extend(dq["lines"])
    lines.append("")

    data_quality = dq["summary"]

    # xG real de Understat si disponible
    if home.real_xg > 0 or away.real_xg > 0:
        lines.extend([
            "",
            f"📊 *xG REAL (Understat)*",
            f"🏠 {home.name}: xG {home.real_xg:.2f} | xGA {home.real_xga:.2f}",
            f"✈️ {away.name}: xG {away.real_xg:.2f} | xGA {away.real_xga:.2f}",
        ])

    # FBref advanced stats
    if home.sca_p90 > 0 or away.sca_p90 > 0:
        lines.extend([
            "",
            f"🔬 *STATS AVANZADOS (FBref/StatsBomb)*",
        ])
        # Creatividad
        if home.sca_p90 > 0:
            lines.append(f"🏠 {home.name}: SCA {home.sca_p90:.1f}/90 | GCA {home.gca_p90:.1f}/90 | Tiros {home.shots_p90:.1f}/90 ({home.shots_on_target_pct:.0f}% a puerta)")
        if away.sca_p90 > 0:
            lines.append(f"✈️ {away.name}: SCA {away.sca_p90:.1f}/90 | GCA {away.gca_p90:.1f}/90 | Tiros {away.shots_p90:.1f}/90 ({away.shots_on_target_pct:.0f}% a puerta)")
        # Progresión
        if home.progressive_passes_p90 > 0 or away.progressive_passes_p90 > 0:
            lines.append(f"📈 Prog pases: {home.name} {home.progressive_passes_p90:.1f}/90 | {away.name} {away.progressive_passes_p90:.1f}/90")
            lines.append(f"📈 Prog carries: {home.name} {home.progressive_carries_p90:.1f}/90 | {away.name} {away.progressive_carries_p90:.1f}/90")
        # Defensa
        if home.tackles_won_p90 > 0 or away.tackles_won_p90 > 0:
            lines.append(f"🛡 Tackles: {home.name} {home.tackles_won_p90:.1f}/90 | {away.name} {away.tackles_won_p90:.1f}/90")
            lines.append(f"🛡 Intercep: {home.name} {home.interceptions_p90:.1f}/90 | {away.name} {away.interceptions_p90:.1f}/90")
        # Posesión
        if home.possession_pct > 0:
            lines.append(f"⚽ Posesión: {home.name} {home.possession_pct:.0f}% | {away.name} {away.possession_pct:.0f}%")

    # Descanso
    if home.rest_days >= 0 or away.rest_days >= 0:
        rest_parts = []
        if home.rest_days >= 0:
            rest_emoji = "🔴" if home.rest_days <= 3 else "🟢"
            rest_parts.append(f"{rest_emoji} {home.name}: {home.rest_days}d")
        if away.rest_days >= 0:
            rest_emoji = "🔴" if away.rest_days <= 3 else "🟢"
            rest_parts.append(f"{rest_emoji} {away.name}: {away.rest_days}d")
        lines.extend(["", f"⏱ *DESCANSO*", " | ".join(rest_parts)])

    lines.extend([
        "",
        f"{'═' * 28}",
        "",
        f"🎯 *PROBABILIDADES ESTIMADAS*",
        f"📡 _{data_quality}_",
        f"🏠 Victoria Local: *{probs['home_win']:.1%}*",
        f"🤝 Empate: *{probs['draw']:.1%}*",
        f"✈️ Victoria Visitante: *{probs['away_win']:.1%}*",
        "",
        f"⚽ Over 1.5: {probs.get('over15', 0):.0%} | Over 2.5: {probs.get('over25', 0):.0%} | Over 3.5: {probs.get('over35', 0):.0%}",
        f"⚽ BTTS: *{probs.get('btts', 0):.1%}*",
        f"📊 xG estimado: *{probs.get('home_xg', 0):.2f}* - *{probs.get('away_xg', 0):.2f}* (Total: *{probs.get('expected_goals', 0):.2f}*)",
    ])

    # Mostrar correcciones de calibración aplicadas
    cal_applied = probs.get("calibration_applied", [])
    if cal_applied:
        lines.extend(["", "🧪 *CALIBRACIÓN APLICADA*"])
        for cal_note in cal_applied:
            lines.append(f"   📐 {cal_note}")

    # Resultados exactos más probables
    exact_scores = probs.get("exact_scores", [])
    if exact_scores:
        scores_str = " | ".join([f"{h}-{a} ({p:.0%})" for (h, a), p in exact_scores[:3]])
        lines.append(f"🎲 Marcadores: {scores_str}")

    if suggestions:
        lines.extend([
            "",
            f"{'═' * 28}",
            "",
            f"💡 *APUESTAS CON VALOR DETECTADAS*",
        ])
        confidence_emoji = {"baja": "🟡", "media": "🟠", "alta": "🔴", "muy_alta": "💎"}

        for i, s in enumerate(suggestions[:5], 1):
            emoji = confidence_emoji.get(s.confidence, "🟠")
            stake_stars = "⭐" * s.stake

            # Composite score con veredicto
            if s.composite_score > 0:
                score_line = f"   🔢 {format_composite_score(s.composite_score)}"
            else:
                score_line = None

            lines.extend([
                "",
                f"{emoji} *{i}. {s.pick}*",
            ])
            if score_line:
                lines.append(score_line)
            lines.extend([
                f"   📊 Cuota: *{s.odds:.2f}* | Valor: *+{s.value:.1%}*",
                f"   💰 Stake: {stake_stars} ({s.stake}/5)",
                f"   🎯 Confianza: *{s.confidence.upper()}*",
            ])
            for reason in s.reasoning:
                lines.append(f"   • {reason}")
    else:
        lines.extend([
            "",
            "⚠️ *No se encontraron apuestas con valor claro en este partido.*",
            "Las cuotas del mercado parecen ajustadas a las probabilidades reales.",
        ])

    # Line movement (si hay historial de odds)
    if odds_history:
        movement_lines = analyze_line_movement(odds_history)
        if movement_lines:
            lines.extend(movement_lines)

    return "\n".join(lines)
