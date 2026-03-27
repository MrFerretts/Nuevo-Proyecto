"""Backtester de value bets.

Evalúa retroactivamente: "si hubiera apostado todas las sugerencias
que cumplan cierto filtro (edge mínimo, confianza, mercado), ¿cuál
hubiera sido el ROI?"

Esto valida si el edge detectado por el modelo es real o un artefacto.
"""

import logging
from dataclasses import dataclass

import aiosqlite

from src.config import DB_PATH

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Resultado de un backtest."""
    total_bets: int = 0
    wins: int = 0
    losses: int = 0
    total_staked: float = 0.0    # Unidades apostadas
    total_profit: float = 0.0    # P/L neto
    roi: float = 0.0             # ROI %
    avg_odds: float = 0.0
    avg_edge: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0    # Peor racha negativa acumulada
    best_streak: int = 0
    worst_streak: int = 0
    # Desglose
    by_confidence: dict = None
    by_market: dict = None
    by_edge_bucket: dict = None
    # Filtros aplicados
    filters_desc: str = ""


async def run_backtest(
    min_edge: float = 0.0,
    confidence: str = "",       # "baja", "media", "alta", "muy_alta" o "" para todas
    market: str = "",           # "1X2", "Over/Under 2.5", etc. o "" para todos
    days: int = 90,
    stake_mode: str = "flat",   # "flat" (1u siempre) o "kelly" (usar stake sugerido)
) -> BacktestResult:
    """Ejecuta backtest sobre predicciones resueltas.

    Args:
        min_edge: Edge mínimo para incluir la apuesta (0.05 = 5%)
        confidence: Filtrar por nivel de confianza
        market: Filtrar por tipo de mercado
        days: Ventana de días hacia atrás
        stake_mode: "flat" = 1 unidad, "kelly" = usa el stake del modelo (1-5u)

    Returns: BacktestResult con todas las métricas
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        query = """
            SELECT * FROM predictions
            WHERE resolved_at IS NOT NULL
            AND created_at >= datetime('now', ? || ' days')
        """
        params = [f"-{days}"]

        if confidence:
            query += " AND predicted_confidence = ?"
            params.append(confidence)

        if market:
            query += " AND predicted_market LIKE ?"
            params.append(f"%{market}%")

        if min_edge > 0:
            query += " AND predicted_edge >= ?"
            params.append(min_edge)

        query += " ORDER BY created_at ASC"

        async with db.execute(query, params) as cursor:
            predictions = await cursor.fetchall()

    if not predictions:
        return BacktestResult(filters_desc=_describe_filters(min_edge, confidence, market, days, stake_mode))

    # Simular apuestas
    bets = []
    running_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    current_streak = 0
    best_streak = 0
    worst_streak = 0

    by_confidence = {}
    by_market = {}
    by_edge_bucket = {"0-3%": _bucket(), "3-5%": _bucket(), "5-7%": _bucket(),
                      "7-10%": _bucket(), "10%+": _bucket()}

    for p in predictions:
        edge = p["predicted_edge"] or 0
        odds = p["predicted_odds"] or 0
        was_correct = p["was_correct"]
        conf = p["predicted_confidence"] or "sin_confianza"
        mkt = p["predicted_market"] or "sin_mercado"

        if odds <= 1:
            continue

        # Stake
        if stake_mode == "kelly":
            # Usar el stake que sugirió el modelo (1-5 unidades)
            # Deducimos del edge: baja=1u, media=2u, alta=3u, muy_alta=5u
            stake = {"baja": 1, "media": 2, "alta": 3, "muy_alta": 5}.get(conf, 1)
        else:
            stake = 1

        # P/L
        if was_correct:
            profit = (odds - 1) * stake
            current_streak = max(1, current_streak + 1) if current_streak >= 0 else 1
        else:
            profit = -stake
            current_streak = min(-1, current_streak - 1) if current_streak <= 0 else -1

        best_streak = max(best_streak, current_streak)
        worst_streak = min(worst_streak, current_streak)

        running_pnl += profit
        peak_pnl = max(peak_pnl, running_pnl)
        dd = peak_pnl - running_pnl
        max_dd = max(max_dd, dd)

        bets.append({
            "match": p["match_name"],
            "pick": p["predicted_pick"],
            "odds": odds,
            "edge": edge,
            "confidence": conf,
            "market": mkt,
            "was_correct": was_correct,
            "profit": profit,
            "stake": stake,
        })

        # Agrupar por confianza
        if conf not in by_confidence:
            by_confidence[conf] = _bucket()
        _add_to_bucket(by_confidence[conf], was_correct, profit, stake, odds, edge)

        # Agrupar por mercado
        if mkt not in by_market:
            by_market[mkt] = _bucket()
        _add_to_bucket(by_market[mkt], was_correct, profit, stake, odds, edge)

        # Agrupar por edge bucket
        if edge >= 0.10:
            bucket_key = "10%+"
        elif edge >= 0.07:
            bucket_key = "7-10%"
        elif edge >= 0.05:
            bucket_key = "5-7%"
        elif edge >= 0.03:
            bucket_key = "3-5%"
        else:
            bucket_key = "0-3%"
        _add_to_bucket(by_edge_bucket[bucket_key], was_correct, profit, stake, odds, edge)

    # Calcular métricas finales
    total_bets = len(bets)
    wins = sum(1 for b in bets if b["was_correct"])
    total_staked = sum(b["stake"] for b in bets)
    total_profit = sum(b["profit"] for b in bets)

    # Finalizar sub-buckets
    for bucket_dict in [by_confidence, by_market, by_edge_bucket]:
        for k, v in bucket_dict.items():
            _finalize_bucket(v)

    return BacktestResult(
        total_bets=total_bets,
        wins=wins,
        losses=total_bets - wins,
        total_staked=total_staked,
        total_profit=total_profit,
        roi=(total_profit / total_staked * 100) if total_staked else 0,
        avg_odds=sum(b["odds"] for b in bets) / total_bets if total_bets else 0,
        avg_edge=sum(b["edge"] for b in bets) / total_bets if total_bets else 0,
        win_rate=wins / total_bets if total_bets else 0,
        max_drawdown=max_dd,
        best_streak=best_streak,
        worst_streak=abs(worst_streak),
        by_confidence=by_confidence,
        by_market=by_market,
        by_edge_bucket=by_edge_bucket,
        filters_desc=_describe_filters(min_edge, confidence, market, days, stake_mode),
    )


def _bucket() -> dict:
    return {"total": 0, "wins": 0, "profit": 0.0, "staked": 0.0,
            "sum_odds": 0.0, "sum_edge": 0.0}


def _add_to_bucket(b: dict, was_correct, profit, stake, odds, edge):
    b["total"] += 1
    if was_correct:
        b["wins"] += 1
    b["profit"] += profit
    b["staked"] += stake
    b["sum_odds"] += odds
    b["sum_edge"] += edge


def _finalize_bucket(b: dict):
    n = b["total"]
    if n:
        b["win_rate"] = b["wins"] / n
        b["roi"] = (b["profit"] / b["staked"] * 100) if b["staked"] else 0
        b["avg_odds"] = b["sum_odds"] / n
        b["avg_edge"] = b["sum_edge"] / n
    else:
        b["win_rate"] = 0
        b["roi"] = 0
        b["avg_odds"] = 0
        b["avg_edge"] = 0


def _describe_filters(min_edge, confidence, market, days, stake_mode) -> str:
    parts = [f"{days} días"]
    if min_edge > 0:
        parts.append(f"edge>={min_edge:.0%}")
    if confidence:
        parts.append(f"confianza={confidence}")
    if market:
        parts.append(f"mercado={market}")
    parts.append(f"stake={stake_mode}")
    return " | ".join(parts)


def format_backtest(result: BacktestResult) -> str:
    """Formatea resultado del backtest para Telegram."""
    if result.total_bets == 0:
        return (
            f"📊 *BACKTEST*\n"
            f"Filtros: _{result.filters_desc}_\n\n"
            f"No hay predicciones resueltas que cumplan estos filtros.\n"
            f"Necesitas más análisis resueltos para un backtest significativo."
        )

    lines = [
        f"📊 *BACKTEST — SIMULACIÓN HISTÓRICA*",
        f"🔍 _{result.filters_desc}_",
        "",
        f"{'═' * 28}",
        "",
        f"📈 *RESUMEN*",
        f"  Apuestas: *{result.total_bets}*",
        f"  Ganadas: *{result.wins}* ({result.win_rate:.0%})",
        f"  Perdidas: *{result.losses}*",
        f"  Unidades apostadas: *{result.total_staked:.1f}u*",
        "",
        f"💰 *RESULTADO*",
        f"  P/L neto: *{result.total_profit:+.2f}u*",
        f"  ROI: *{result.roi:+.1f}%*",
        f"  Odds promedio: *{result.avg_odds:.2f}*",
        f"  Edge promedio: *{result.avg_edge:.1%}*",
        "",
        f"📉 *RIESGO*",
        f"  Max drawdown: *{result.max_drawdown:.2f}u*",
        f"  Mejor racha: *{result.best_streak}W*",
        f"  Peor racha: *{result.worst_streak}L*",
    ]

    # Interpretación automática
    if result.total_bets >= 20:
        if result.roi > 5:
            lines.extend(["", "✅ *El edge parece REAL.* ROI positivo con muestra suficiente."])
        elif result.roi > 0:
            lines.extend(["", "🟡 *Edge marginal.* ROI positivo pero necesita más muestra para confirmar."])
        elif result.roi > -5:
            lines.extend(["", "🟠 *Sin edge claro.* Resultado cercano a breakeven."])
        else:
            lines.extend(["", "❌ *Edge NO confirmado.* El modelo pierde dinero con estos filtros."])
    else:
        lines.extend(["", f"⚠️ _Muestra pequeña ({result.total_bets} apuestas). Mínimo 30+ para conclusiones fiables._"])

    # Desglose por confianza
    if result.by_confidence:
        lines.extend(["", "📊 *POR CONFIANZA:*"])
        conf_order = ["baja", "media", "alta", "muy_alta"]
        for conf in conf_order:
            data = result.by_confidence.get(conf)
            if data and data["total"] > 0:
                emoji = {"baja": "🟡", "media": "🟠", "alta": "🔴", "muy_alta": "💎"}.get(conf, "⚪")
                lines.append(
                    f"  {emoji} {conf}: {data['wins']}/{data['total']} "
                    f"({data['win_rate']:.0%}) | ROI: *{data['roi']:+.1f}%* | "
                    f"P/L: {data['profit']:+.2f}u"
                )

    # Desglose por edge bucket
    if result.by_edge_bucket:
        lines.extend(["", "📊 *POR EDGE DETECTADO:*"])
        for bucket_key in ["0-3%", "3-5%", "5-7%", "7-10%", "10%+"]:
            data = result.by_edge_bucket.get(bucket_key)
            if data and data["total"] > 0:
                verdict = "✅" if data["roi"] > 0 else "❌"
                lines.append(
                    f"  {verdict} Edge {bucket_key}: {data['wins']}/{data['total']} "
                    f"({data['win_rate']:.0%}) | ROI: *{data['roi']:+.1f}%*"
                )

    # Desglose por mercado
    if result.by_market:
        lines.extend(["", "📊 *POR MERCADO:*"])
        sorted_markets = sorted(result.by_market.items(), key=lambda x: x[1]["total"], reverse=True)
        for mkt, data in sorted_markets:
            if data["total"] > 0:
                verdict = "✅" if data["roi"] > 0 else "❌"
                lines.append(
                    f"  {verdict} {mkt}: {data['wins']}/{data['total']} "
                    f"({data['win_rate']:.0%}) | ROI: *{data['roi']:+.1f}%*"
                )

    return "\n".join(lines)
