"""Motor de análisis de apuestas deportivas.

Analiza partidos usando múltiples factores estadísticos para encontrar
apuestas con valor (value bets) donde la probabilidad real estimada
es mayor que la que implican las cuotas de las casas.
"""

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
    market: str           # "1X2", "Over/Under", "BTTS"
    pick: str             # "Home Win", "Over 2.5", "BTTS Yes"
    estimated_prob: float # Probabilidad estimada (0-1)
    implied_prob: float   # Probabilidad implícita de la cuota (0-1)
    odds: float           # Cuota del mercado
    value: float          # Edge = estimated - implied (positivo = valor)
    confidence: str       # baja, media, alta, muy_alta
    stake: int            # 1-5 recomendado
    reasoning: list       # Lista de razones


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


def estimate_probabilities(home: TeamAnalysis, away: TeamAnalysis, h2h: dict) -> dict:
    """Estima probabilidades usando un modelo ponderado de múltiples factores.

    No es un modelo estadístico perfecto, pero combina varios indicadores
    para dar una estimación razonada.
    """
    # Factor 1: Forma reciente (peso 30%)
    form_home = home.form_score / 100
    form_away = away.form_score / 100

    # Factor 2: Posición en liga (peso 15%)
    if home.league_position > 0 and away.league_position > 0:
        max_pos = max(home.league_position, away.league_position, 20)
        pos_home = 1 - (home.league_position / (max_pos + 1))
        pos_away = 1 - (away.league_position / (max_pos + 1))
    else:
        pos_home = 0.5
        pos_away = 0.5

    # Factor 3: Ventaja de local (peso 10%) - estadísticamente ~46% home, 27% draw, 27% away
    home_advantage = 0.12

    # Factor 4: H2H (peso 15%)
    h2h_total = h2h["home_wins"] + h2h["away_wins"] + h2h["draws"]
    if h2h_total > 0:
        h2h_home = h2h["home_wins"] / h2h_total
        h2h_away = h2h["away_wins"] / h2h_total
        h2h_draw = h2h["draws"] / h2h_total
    else:
        h2h_home = 0.45
        h2h_away = 0.27
        h2h_draw = 0.28

    # Factor 5: Capacidad goleadora vs solidez defensiva (peso 30%)
    home_attack = home.goals_scored_avg / max(home.goals_scored_avg + away.goals_scored_avg, 0.1)
    home_defense = away.goals_conceded_avg / max(home.goals_conceded_avg + away.goals_conceded_avg, 0.1)
    away_attack = away.goals_scored_avg / max(home.goals_scored_avg + away.goals_scored_avg, 0.1)
    away_defense = home.goals_conceded_avg / max(home.goals_conceded_avg + away.goals_conceded_avg, 0.1)

    attack_def_home = (home_attack + home_defense) / 2
    attack_def_away = (away_attack + away_defense) / 2

    # Combinar factores con pesos
    raw_home = (
        form_home * 0.30
        + pos_home * 0.15
        + home_advantage
        + h2h_home * 0.15
        + attack_def_home * 0.30
    )
    raw_away = (
        form_away * 0.30
        + pos_away * 0.15
        + h2h_away * 0.15
        + attack_def_away * 0.30
    )
    raw_draw = (
        (1 - abs(form_home - form_away)) * 0.20
        + h2h_draw * 0.15
        + 0.25 * 0.15  # base draw prob
    )

    # Normalizar a probabilidades
    total = raw_home + raw_away + raw_draw
    home_prob = raw_home / total
    away_prob = raw_away / total
    draw_prob = raw_draw / total

    # Over 2.5 - basado en promedios de goles y tendencias
    expected_goals = home.goals_scored_avg + away.goals_scored_avg
    # Usar Poisson simplificado: P(total > 2.5) basado en media esperada
    over25_base = (home.over25_pct + away.over25_pct) / 200
    h2h_over25 = 1 if h2h["avg_goals"] > 2.5 else 0
    over25_prob = over25_base * 0.6 + (expected_goals / 5) * 0.25 + h2h_over25 * 0.15
    over25_prob = max(0.15, min(0.85, over25_prob))

    # BTTS
    btts_base = (home.btts_pct + away.btts_pct) / 200
    h2h_btts = h2h["btts_pct"] / 100
    btts_prob = btts_base * 0.6 + h2h_btts * 0.2 + (1 - home.clean_sheets_pct / 100) * 0.1 + (1 - away.clean_sheets_pct / 100) * 0.1
    btts_prob = max(0.15, min(0.85, btts_prob))

    return {
        "home_win": home_prob,
        "draw": draw_prob,
        "away_win": away_prob,
        "over25": over25_prob,
        "btts": btts_prob,
        "expected_goals": expected_goals,
    }


def find_value_bets(probs: dict, odds: dict) -> list[BetSuggestion]:
    """Encuentra apuestas con valor comparando probabilidades estimadas vs cuotas.

    Una apuesta tiene valor cuando nuestra probabilidad estimada > probabilidad implícita de la cuota.
    """
    suggestions = []

    markets = [
        ("1X2", "Victoria Local", probs["home_win"], odds.get("home", 0)),
        ("1X2", "Empate", probs["draw"], odds.get("draw", 0)),
        ("1X2", "Victoria Visitante", probs["away_win"], odds.get("away", 0)),
        ("Over/Under", "Over 2.5 Goles", probs["over25"], odds.get("over25", 0)),
        ("Over/Under", "Under 2.5 Goles", 1 - probs["over25"], odds.get("under25", 0)),
        ("BTTS", "Ambos Marcan - Sí", probs["btts"], odds.get("btts_yes", 0)),
        ("BTTS", "Ambos Marcan - No", 1 - probs["btts"], odds.get("btts_no", 0)),
    ]

    for market, pick, est_prob, market_odds in markets:
        if market_odds <= 1.0:
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

            reasoning = _build_reasoning(market, pick, est_prob, implied_prob, value, market_odds)

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
                     value: float, odds: float) -> list[str]:
    """Construye las razones detrás de una sugerencia."""
    reasons = []
    reasons.append(f"Probabilidad estimada: {est_prob:.1%} vs implícita: {implied_prob:.1%}")
    reasons.append(f"Edge detectado: +{value:.1%}")

    if value > 0.15:
        reasons.append("Valor MUY ALTO - posible ineficiencia del mercado")
    elif value > 0.10:
        reasons.append("Valor ALTO - buena oportunidad")

    if odds >= 2.0 and odds <= 3.0:
        reasons.append("Cuota en rango óptimo (2.0-3.0)")
    elif odds < 1.5:
        reasons.append("Cuota baja - considerar en combinada")

    return reasons


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

    h2h_total = h2h["home_wins"] + h2h["away_wins"] + h2h["draws"]
    if h2h_total > 0:
        lines.extend([
            "",
            f"🔄 *H2H (últimos {h2h_total} partidos)*",
            f"🏠 Victorias {home.name}: {h2h['home_wins']}",
            f"🤝 Empates: {h2h['draws']}",
            f"✈️ Victorias {away.name}: {h2h['away_wins']}",
            f"⚽ Promedio goles: {h2h['avg_goals']:.1f} | BTTS: {h2h['btts_pct']:.0f}%",
        ])

    lines.extend([
        "",
        f"{'═' * 28}",
        "",
        f"🎯 *PROBABILIDADES ESTIMADAS*",
        f"🏠 Victoria Local: *{probs['home_win']:.1%}*",
        f"🤝 Empate: *{probs['draw']:.1%}*",
        f"✈️ Victoria Visitante: *{probs['away_win']:.1%}*",
        f"⚽ Over 2.5: *{probs['over25']:.1%}*",
        f"⚽ BTTS: *{probs['btts']:.1%}*",
        f"📊 Goles esperados: *{probs['expected_goals']:.1f}*",
    ])

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
