from datetime import datetime


CONFIDENCE_EMOJI = {
    "baja": "🟡",
    "media": "🟠",
    "alta": "🔴",
    "muy_alta": "💎",
}

RESULT_EMOJI = {
    "win": "✅",
    "loss": "❌",
    "void": "⚪",
    "pending": "⏳",
}


def format_tip(tip, show_result: bool = False) -> str:
    """Formatea un tip para mostrar en Telegram."""
    conf_emoji = CONFIDENCE_EMOJI.get(tip["confidence"], "🟠")
    vip_badge = "👑 VIP" if tip["is_vip"] else "🆓 FREE"
    stake_bar = "⭐" * tip["stake"]

    lines = [
        f"{'─' * 20}",
        f"{vip_badge} | {conf_emoji} Confianza: {tip['confidence'].upper()}",
        f"",
        f"🏅 *{tip['sport']}*",
        f"🏟 *{tip['match_name']}*",
        f"",
        f"💡 Predicción: *{tip['prediction']}*",
        f"📊 Cuota: *{tip['odds']}*",
        f"💰 Stake: {stake_bar} ({tip['stake']}/5)",
    ]

    if show_result:
        result_emoji = RESULT_EMOJI.get(tip["result"], "⏳")
        lines.append(f"📋 Resultado: {result_emoji} {tip['result'].upper()}")
        if tip["result"] == "win":
            lines.append(f"💵 Profit: +{tip['profit']:.2f}u")
        elif tip["result"] == "loss":
            lines.append(f"💸 Pérdida: {tip['profit']:.2f}u")

    lines.append(f"{'─' * 20}")
    return "\n".join(lines)


def format_stats(stats) -> str:
    """Formatea las estadísticas del canal."""
    total = stats["total"] or 0
    wins = stats["wins"] or 0
    losses = stats["losses"] or 0
    voids = stats["voids"] or 0
    pending = stats["pending"] or 0
    profit = stats["total_profit"] or 0
    decided = wins + losses
    winrate = (wins / decided * 100) if decided > 0 else 0

    profit_emoji = "📈" if profit >= 0 else "📉"

    lines = [
        "📊 *ESTADÍSTICAS DEL CANAL*",
        f"{'─' * 25}",
        "",
        f"📋 Total tips: *{total}*",
        f"✅ Ganados: *{wins}*",
        f"❌ Perdidos: *{losses}*",
        f"⚪ Nulos: *{voids}*",
        f"⏳ Pendientes: *{pending}*",
        "",
        f"🎯 Win Rate: *{winrate:.1f}%*",
        f"{profit_emoji} Profit: *{profit:+.2f} unidades*",
        f"{'─' * 25}",
    ]
    return "\n".join(lines)
