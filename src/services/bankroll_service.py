"""Servicio de bankroll management con Kelly Criterion.

Calcula stakes óptimos basados en la fórmula de Kelly:
  kelly% = (bp - q) / b
  donde:
    b = odds decimales - 1 (ganancia neta por unidad)
    p = probabilidad estimada de ganar
    q = 1 - p (probabilidad de perder)

Se usa "fractional Kelly" (25%-50%) para reducir varianza.
"""

import logging

logger = logging.getLogger(__name__)

# Fracción de Kelly a usar (0.25 = cuarto Kelly, más conservador)
KELLY_FRACTION = 0.25
# Stake mínimo y máximo como % del bankroll
MIN_STAKE_PCT = 0.01  # 1%
MAX_STAKE_PCT = 0.05  # 5%


def kelly_criterion(odds: float, estimated_prob: float) -> dict:
    """Calcula el stake óptimo según Kelly Criterion.

    Args:
        odds: cuota decimal (ej: 2.10)
        estimated_prob: probabilidad estimada de ganar (0-1)

    Returns: dict con kelly_pct, fractional_pct, edge, recomendación
    """
    if odds <= 1 or estimated_prob <= 0 or estimated_prob >= 1:
        return {
            "full_kelly_pct": 0,
            "fractional_kelly_pct": 0,
            "recommended_pct": 0,
            "edge": 0,
            "expected_value": 0,
            "recommendation": "NO APOSTAR",
            "risk_level": "none",
        }

    b = odds - 1  # ganancia neta por unidad apostada
    p = estimated_prob
    q = 1 - p

    # Kelly completo
    kelly = (b * p - q) / b

    # Edge (valor esperado)
    ev = (p * b) - q  # EV por unidad apostada
    edge = ev / 1  # = EV como porcentaje del stake

    if kelly <= 0:
        return {
            "full_kelly_pct": 0,
            "fractional_kelly_pct": 0,
            "recommended_pct": 0,
            "edge": edge,
            "expected_value": ev,
            "recommendation": "NO APOSTAR - Sin valor",
            "risk_level": "none",
        }

    # Fractional Kelly
    fractional = kelly * KELLY_FRACTION

    # Limitar al rango permitido
    recommended = max(MIN_STAKE_PCT, min(fractional, MAX_STAKE_PCT))

    # Nivel de riesgo
    if recommended <= 0.02:
        risk_level = "bajo"
    elif recommended <= 0.035:
        risk_level = "medio"
    else:
        risk_level = "alto"

    # Recomendación textual
    if edge > 0.15:
        recommendation = f"VALOR ALTO - Kelly recomienda {recommended:.1%} del bankroll"
    elif edge > 0.08:
        recommendation = f"BUEN VALOR - Kelly recomienda {recommended:.1%} del bankroll"
    elif edge > 0.03:
        recommendation = f"VALOR MODERADO - Kelly recomienda {recommended:.1%} del bankroll"
    else:
        recommendation = f"VALOR BAJO - Mínimo: {recommended:.1%} del bankroll"

    return {
        "full_kelly_pct": kelly,
        "fractional_kelly_pct": fractional,
        "recommended_pct": recommended,
        "edge": edge,
        "expected_value": ev,
        "recommendation": recommendation,
        "risk_level": risk_level,
    }


def calculate_stake(bankroll: float, odds: float, estimated_prob: float) -> dict:
    """Calcula el monto concreto a apostar dado un bankroll.

    Returns: dict con stake_amount, kelly info, y resumen
    """
    kelly = kelly_criterion(odds, estimated_prob)

    stake_amount = round(bankroll * kelly["recommended_pct"], 2)

    return {
        **kelly,
        "bankroll": bankroll,
        "stake_amount": stake_amount,
    }


def format_kelly_suggestion(bankroll: float, odds: float, estimated_prob: float) -> str:
    """Formatea la sugerencia de Kelly para mostrar en Telegram."""
    info = calculate_stake(bankroll, odds, estimated_prob)

    if info["recommended_pct"] == 0:
        return "❌ Kelly dice: *NO APOSTAR* - No hay valor en esta cuota."

    risk_emoji = {"bajo": "🟢", "medio": "🟡", "alto": "🔴"}.get(info["risk_level"], "⚪")

    return (
        f"📊 *KELLY CRITERION*\n"
        f"├ Edge: +{info['edge']:.1%}\n"
        f"├ Kelly completo: {info['full_kelly_pct']:.1%}\n"
        f"├ Kelly fraccionado ({KELLY_FRACTION:.0%}): {info['fractional_kelly_pct']:.1%}\n"
        f"├ {risk_emoji} Riesgo: {info['risk_level'].upper()}\n"
        f"├ Bankroll: ${info['bankroll']:.2f}\n"
        f"└ 💰 *Apostar: ${info['stake_amount']:.2f}* ({info['recommended_pct']:.1%})\n\n"
        f"_{info['recommendation']}_"
    )
