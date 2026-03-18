"""Motor de análisis de apuestas deportivas.

Analiza partidos usando múltiples factores estadísticos para encontrar
apuestas con valor (value bets) donde la probabilidad real estimada
es mayor que la que implican las cuotas de las casas.

Incluye modelo de Poisson para estimación de goles y resultados exactos.
"""

import math
from dataclasses import dataclass, field
from typing import Optional


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
    # Nuevos campos de contexto
    home_goals_scored_avg: float = 0.0   # Promedio goles a favor en casa
    home_goals_conceded_avg: float = 0.0 # Promedio goles en contra en casa
    away_goals_scored_avg: float = 0.0   # Promedio goles a favor fuera
    away_goals_conceded_avg: float = 0.0 # Promedio goles en contra fuera
    streak: str = ""                     # Racha actual (ej: "3W", "2L")
    matches_played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    avg_total_goals: float = 0.0         # Promedio de goles totales por partido
    over15_pct: float = 0.0              # % partidos con +1.5 goles
    over35_pct: float = 0.0              # % partidos con +3.5 goles
    injuries: list = field(default_factory=list)  # Lista de lesionados


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

def analyze_form(fixtures: list, team_id: int) -> tuple[float, str]:
    """Analiza la forma reciente de un equipo.

    Returns: (score 0-100, detail string like "WWDLW")
    """
    if not fixtures:
        return 50.0, "?"

    results = []
    points = 0
    max_points = 0

    for fx in fixtures[:10]:  # Últimos 10 partidos
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        home_id = teams.get("home", {}).get("id")
        is_home = home_id == team_id
        home_goals = goals.get("home", 0) or 0
        away_goals = goals.get("away", 0) or 0

        max_points += 3
        if is_home:
            if home_goals > away_goals:
                results.append("W")
                points += 3
            elif home_goals == away_goals:
                results.append("D")
                points += 1
            else:
                results.append("L")
        else:
            if away_goals > home_goals:
                results.append("W")
                points += 3
            elif away_goals == home_goals:
                results.append("D")
                points += 1
            else:
                results.append("L")

    # Dar más peso a partidos recientes
    weighted_score = 0
    weights = [1.5, 1.4, 1.3, 1.2, 1.1, 1.0, 0.9, 0.8, 0.7, 0.6]
    total_weight = 0
    for i, r in enumerate(results):
        w = weights[i] if i < len(weights) else 0.5
        total_weight += w
        if r == "W":
            weighted_score += 3 * w
        elif r == "D":
            weighted_score += 1 * w

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
    """Calcula goles esperados (xG) para cada equipo usando fuerza de ataque/defensa.

    Método: Compara la fuerza de ataque y defensa de cada equipo contra
    el promedio de la liga para estimar goles esperados.

    Args:
        home: Análisis del equipo local
        away: Análisis del equipo visitante
        h2h: Estadísticas H2H
        league_avg_home_goals: Promedio de goles de locales en la liga (default europeo)
        league_avg_away_goals: Promedio de goles de visitantes en la liga

    Returns: (home_xg, away_xg)
    """
    # Fuerza de ataque = goles marcados del equipo / promedio de la liga
    # Fuerza de defensa = goles recibidos del equipo / promedio de la liga
    home_attack = home.goals_scored_avg / league_avg_home_goals if league_avg_home_goals > 0 else 1.0
    home_defense = home.goals_conceded_avg / league_avg_away_goals if league_avg_away_goals > 0 else 1.0
    away_attack = away.goals_scored_avg / league_avg_away_goals if league_avg_away_goals > 0 else 1.0
    away_defense = away.goals_conceded_avg / league_avg_home_goals if league_avg_home_goals > 0 else 1.0

    # xG = fuerza ataque del equipo × fuerza defensa del rival × promedio liga
    home_xg = home_attack * away_defense * league_avg_home_goals
    away_xg = away_attack * home_defense * league_avg_away_goals

    # Ajuste por H2H si hay suficientes datos (>= 3 partidos)
    h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)
    if h2h_total >= 3 and h2h.get("avg_goals", 0) > 0:
        h2h_avg = h2h["avg_goals"]
        current_total = home_xg + away_xg
        if current_total > 0:
            # Ajustar 15% hacia el promedio H2H
            h2h_factor = h2h_avg / current_total
            adjustment = 0.15
            home_xg *= (1 - adjustment) + (adjustment * h2h_factor)
            away_xg *= (1 - adjustment) + (adjustment * h2h_factor)

    # Ajuste por rendimiento local/visitante específico
    if home.home_goals_scored_avg > 0 and home.matches_played >= 5:
        home_local_factor = home.home_goals_scored_avg / max(home.goals_scored_avg, 0.1)
        home_xg *= (0.7 + 0.3 * home_local_factor)  # Mezclar con factor local

    if away.away_goals_scored_avg > 0 and away.matches_played >= 5:
        away_visit_factor = away.away_goals_scored_avg / max(away.goals_scored_avg, 0.1)
        away_xg *= (0.7 + 0.3 * away_visit_factor)

    # Clamp a valores razonables
    home_xg = max(0.3, min(4.5, home_xg))
    away_xg = max(0.2, min(4.0, away_xg))

    return home_xg, away_xg


# ══════════════════════════════════════════════════════════════════
# ESTIMACIÓN DE PROBABILIDADES (modelo mejorado)
# ══════════════════════════════════════════════════════════════════

def estimate_probabilities(home: TeamAnalysis, away: TeamAnalysis, h2h: dict) -> dict:
    """Estima probabilidades combinando Poisson con ajustes contextuales.

    1. Calcula xG con fuerza de ataque/defensa
    2. Genera probabilidades base con Poisson
    3. Ajusta con forma reciente, posición y H2H
    4. Normaliza y devuelve probabilidades finales
    """
    # Paso 1: Calcular goles esperados
    home_xg, away_xg = calculate_expected_goals(home, away, h2h)

    # Paso 2: Probabilidades Poisson (base)
    poisson = poisson_match_probs(home_xg, away_xg)

    # Paso 3: Ajustes contextuales sobre las probabilidades 1X2
    p_home = poisson["home_win"]
    p_draw = poisson["draw"]
    p_away = poisson["away_win"]

    # Ajuste por forma reciente (máximo ±5% shift)
    form_diff = (home.form_score - away.form_score) / 100  # -1 a +1
    form_shift = form_diff * 0.05
    p_home += form_shift
    p_away -= form_shift

    # Ajuste por posición en liga (máximo ±3% shift)
    if home.league_position > 0 and away.league_position > 0:
        max_pos = max(home.league_position, away.league_position, 20)
        pos_diff = (away.league_position - home.league_position) / max_pos
        pos_shift = pos_diff * 0.03
        p_home += pos_shift
        p_away -= pos_shift

    # Ajuste por H2H (máximo ±4% shift)
    h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)
    if h2h_total >= 3:
        h2h_home_rate = h2h["home_wins"] / h2h_total
        h2h_away_rate = h2h["away_wins"] / h2h_total
        h2h_shift = (h2h_home_rate - h2h_away_rate) * 0.04
        p_home += h2h_shift
        p_away -= h2h_shift

    # Ajuste por lesiones (cada lesión clave reduce ~1.5%)
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

    # Clamp y normalizar
    p_home = max(0.05, p_home)
    p_draw = max(0.05, p_draw)
    p_away = max(0.05, p_away)
    total = p_home + p_draw + p_away
    p_home /= total
    p_draw /= total
    p_away /= total

    return {
        "home_win": p_home,
        "draw": p_draw,
        "away_win": p_away,
        "over15": poisson["over15"],
        "over25": poisson["over25"],
        "over35": poisson["over35"],
        "btts": poisson["btts_yes"],
        "home_or_draw": p_home + p_draw,
        "away_or_draw": p_away + p_draw,
        "home_or_away": p_home + p_away,
        "exact_scores": poisson["exact_scores"],
        "expected_goals": home_xg + away_xg,
        "home_xg": home_xg,
        "away_xg": away_xg,
    }


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
            continue  # Sin cuota válida

        implied_prob = 1 / market_odds
        value = est_prob - implied_prob

        if value > 0.03:  # Al menos 3% de edge
            # Calcular confianza y stake
            if value > 0.15:
                confidence = "muy_alta"
                stake = 5
            elif value > 0.10:
                confidence = "alta"
                stake = 4
            elif value > 0.07:
                confidence = "media"
                stake = 3
            else:
                confidence = "baja"
                stake = 2

            # Limitar stake si la cuota es muy alta (más riesgo)
            if market_odds > 3.5:
                stake = max(1, stake - 1)

            reasoning = _build_reasoning(market, pick, est_prob, implied_prob, value, market_odds, probs)

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


def _build_reasoning(market: str, pick: str, est_prob: float, implied_prob: float,
                     value: float, odds: float, probs: dict = None) -> list[str]:
    """Construye las razones detrás de una sugerencia."""
    reasons = []
    reasons.append(f"Probabilidad estimada: {est_prob:.1%} vs implícita: {implied_prob:.1%}")
    reasons.append(f"Edge detectado: +{value:.1%}")

    if value > 0.15:
        reasons.append("Valor MUY ALTO - posible ineficiencia del mercado")
    elif value > 0.10:
        reasons.append("Valor ALTO - buena oportunidad")

    # Razonamiento basado en xG
    if probs and "home_xg" in probs:
        total_xg = probs["home_xg"] + probs["away_xg"]
        if "Over" in pick:
            reasons.append(f"xG total esperado: {total_xg:.2f}")
        elif "BTTS" in pick or "Ambos" in pick:
            reasons.append(f"xG local: {probs['home_xg']:.2f} | xG visitante: {probs['away_xg']:.2f}")

    if odds >= 2.0 and odds <= 3.0:
        reasons.append("Cuota en rango óptimo (2.0-3.0)")
    elif odds < 1.5:
        reasons.append("Cuota baja - considerar en combinada")

    return reasons


# ══════════════════════════════════════════════════════════════════
# FORMATO DEL REPORTE
# ══════════════════════════════════════════════════════════════════

def format_analysis_report(
    home: TeamAnalysis,
    away: TeamAnalysis,
    h2h: dict,
    probs: dict,
    suggestions: list[BetSuggestion],
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

    lines.extend([
        "",
        f"{'═' * 28}",
        "",
        f"🎯 *PROBABILIDADES ESTIMADAS (Poisson)*",
        f"🏠 Victoria Local: *{probs['home_win']:.1%}*",
        f"🤝 Empate: *{probs['draw']:.1%}*",
        f"✈️ Victoria Visitante: *{probs['away_win']:.1%}*",
        "",
        f"⚽ Over 1.5: {probs.get('over15', 0):.0%} | Over 2.5: {probs.get('over25', 0):.0%} | Over 3.5: {probs.get('over35', 0):.0%}",
        f"⚽ BTTS: *{probs.get('btts', 0):.1%}*",
        f"📊 xG: *{probs.get('home_xg', 0):.2f}* - *{probs.get('away_xg', 0):.2f}* (Total: *{probs.get('expected_goals', 0):.2f}*)",
    ])

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
            lines.extend([
                "",
                f"{emoji} *{i}. {s.pick}*",
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

    return "\n".join(lines)
