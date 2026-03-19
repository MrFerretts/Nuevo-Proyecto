"""Handlers para el tracker de bankroll.

Comandos:
  /bankroll          - Ver estado del bankroll y ROI
  /apostar           - Registrar una apuesta
  /misapuestas       - Ver historial de apuestas
  /resultado_apuesta - Resolver una apuesta (win/loss/void)
  /kelly             - Calcular Kelly para una cuota
  /setbankroll       - Establecer bankroll inicial
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

from src.models.database import (
    get_or_create_bankroll, set_bankroll, add_user_bet,
    resolve_user_bet, get_user_bets, get_user_pending_bets,
    get_user_bet_stats,
)
from src.services.bankroll_service import kelly_criterion, calculate_stake, format_kelly_suggestion

logger = logging.getLogger(__name__)

# Conversation states para /apostar
BET_MATCH, BET_PICK, BET_ODDS, BET_STAKE = range(100, 104)


async def bankroll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el estado actual del bankroll con métricas de rendimiento."""
    user_id = update.effective_user.id
    br = await get_or_create_bankroll(user_id)
    stats = await get_user_bet_stats(user_id)

    initial = br["initial_bankroll"]
    current = br["current_bankroll"]
    pnl = current - initial
    roi = (pnl / initial * 100) if initial > 0 else 0

    total = stats.get("total", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    pending = stats.get("pending", 0)
    total_profit = stats.get("total_profit", 0) or 0
    total_staked = stats.get("total_staked", 0) or 0
    avg_odds = stats.get("avg_odds", 0) or 0
    best_win = stats.get("best_win", 0) or 0
    worst_loss = stats.get("worst_loss", 0) or 0

    resolved = wins + losses + (stats.get("voids", 0) or 0)
    winrate = (wins / resolved * 100) if resolved > 0 else 0
    yield_pct = (total_profit / total_staked * 100) if total_staked > 0 else 0

    # Emoji de tendencia
    if roi > 5:
        trend = "📈"
    elif roi < -5:
        trend = "📉"
    else:
        trend = "➡️"

    pnl_sign = "+" if pnl >= 0 else ""
    profit_sign = "+" if total_profit >= 0 else ""

    text = (
        f"💼 *MI BANKROLL* {trend}\n"
        f"{'═' * 28}\n\n"
        f"💰 *Capital:* ${current:.2f}\n"
        f"📊 *P&L:* {pnl_sign}${pnl:.2f} ({pnl_sign}{roi:.1f}%)\n"
        f"{'─' * 28}\n"
        f"📋 *Apuestas:* {total} total ({pending} pendientes)\n"
        f"✅ Ganadas: {wins} | ❌ Perdidas: {losses}\n"
        f"🎯 *Winrate:* {winrate:.0f}%\n"
        f"📈 *Yield:* {profit_sign}{yield_pct:.1f}%\n"
        f"💵 *Profit:* {profit_sign}${total_profit:.2f}\n"
        f"📊 *Cuota promedio:* {avg_odds:.2f}\n"
        f"🏆 *Mejor:* +${best_win:.2f} | 💀 *Peor:* ${worst_loss:.2f}\n"
        f"{'─' * 28}\n"
        f"_Usa /apostar para registrar una apuesta_\n"
        f"_Usa /kelly <cuota> <prob> para calcular stake_"
    )

    await update.message.reply_text(text, parse_mode="Markdown")


async def set_bankroll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Establece el bankroll inicial. Uso: /setbankroll <monto>"""
    if not context.args:
        await update.message.reply_text(
            "💼 Uso: `/setbankroll 500`\n"
            "Esto establece tu bankroll inicial a $500",
            parse_mode="Markdown",
        )
        return

    try:
        amount = float(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Ingresa un monto válido. Ejemplo: `/setbankroll 500`", parse_mode="Markdown")
        return

    user_id = update.effective_user.id
    await get_or_create_bankroll(user_id)
    await set_bankroll(user_id, amount)

    await update.message.reply_text(
        f"✅ Bankroll establecido en *${amount:.2f}*\n\n"
        f"A partir de ahora el bot trackeará tus apuestas y ROI.",
        parse_mode="Markdown",
    )


async def kelly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calcula Kelly Criterion. Uso: /kelly <cuota> <probabilidad%>"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "📊 *Kelly Criterion*\n\n"
            "Calcula cuánto apostar de forma óptima.\n\n"
            "Uso: `/kelly <cuota> <probabilidad>`\n"
            "Ejemplo: `/kelly 2.10 55`\n"
            "_(cuota 2.10, probabilidad estimada 55%)_",
            parse_mode="Markdown",
        )
        return

    try:
        odds = float(context.args[0])
        prob = float(context.args[1])
        if prob > 1:
            prob = prob / 100  # Convertir de % a decimal
        if odds <= 1 or prob <= 0 or prob >= 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Formato: `/kelly 2.10 55`", parse_mode="Markdown")
        return

    user_id = update.effective_user.id
    br = await get_or_create_bankroll(user_id)

    text = format_kelly_suggestion(br["current_bankroll"], odds, prob)
    await update.message.reply_text(text, parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════
# CONVERSACIÓN: REGISTRAR APUESTA
# ══════════════════════════════════════════════════════════════

async def bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el registro de una apuesta. /apostar"""
    user_id = update.effective_user.id
    br = await get_or_create_bankroll(user_id)

    if br["current_bankroll"] <= 0:
        await update.message.reply_text(
            "💀 Tu bankroll está en $0. Usa `/setbankroll <monto>` para reiniciar.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["bet_data"] = {"bankroll": br["current_bankroll"]}

    await update.message.reply_text(
        f"📝 *REGISTRAR APUESTA*\n"
        f"💰 Bankroll actual: *${br['current_bankroll']:.2f}*\n\n"
        f"Escribe el *partido* (ej: Barcelona vs Real Madrid):",
        parse_mode="Markdown",
    )
    return BET_MATCH


async def bet_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el nombre del partido."""
    context.user_data["bet_data"]["match"] = update.message.text

    await update.message.reply_text(
        "🎯 ¿Cuál es tu *pick/apuesta*?\n"
        "(ej: Over 2.5, Victoria Local, BTTS Sí, etc.):",
        parse_mode="Markdown",
    )
    return BET_PICK


async def bet_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el pick."""
    context.user_data["bet_data"]["pick"] = update.message.text

    await update.message.reply_text(
        "💹 ¿A qué *cuota*? (ej: 1.85):",
        parse_mode="Markdown",
    )
    return BET_ODDS


async def bet_odds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe la cuota y sugiere stake con Kelly."""
    try:
        odds = float(update.message.text.replace(",", "."))
        if odds <= 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Cuota inválida. Ejemplo: `1.85`", parse_mode="Markdown")
        return BET_ODDS

    context.user_data["bet_data"]["odds"] = odds
    bankroll = context.user_data["bet_data"]["bankroll"]

    # Verificar si hay análisis previo con probabilidad estimada
    last_analysis = context.user_data.get("last_analysis", "")
    estimated_prob = 0

    # Intentar obtener probabilidad del contexto de análisis
    # Sugerencia de Kelly si hay probabilidad
    kelly_text = ""
    if estimated_prob > 0:
        kelly_text = "\n" + format_kelly_suggestion(bankroll, odds, estimated_prob) + "\n"

    # Botones de stake rápido
    stakes = [
        round(bankroll * 0.01, 2),  # 1%
        round(bankroll * 0.02, 2),  # 2%
        round(bankroll * 0.03, 2),  # 3%
        round(bankroll * 0.05, 2),  # 5%
    ]
    keyboard = [
        [
            InlineKeyboardButton(f"1% (${stakes[0]:.0f})", callback_data=f"betstake_{stakes[0]}"),
            InlineKeyboardButton(f"2% (${stakes[1]:.0f})", callback_data=f"betstake_{stakes[1]}"),
        ],
        [
            InlineKeyboardButton(f"3% (${stakes[2]:.0f})", callback_data=f"betstake_{stakes[2]}"),
            InlineKeyboardButton(f"5% (${stakes[3]:.0f})", callback_data=f"betstake_{stakes[3]}"),
        ],
    ]

    await update.message.reply_text(
        f"💰 ¿Cuánto vas a apostar?\n"
        f"Bankroll: *${bankroll:.2f}*\n"
        f"{kelly_text}\n"
        f"Elige un porcentaje o escribe el monto:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return BET_STAKE


async def bet_stake_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el stake desde un botón."""
    query = update.callback_query
    await query.answer()
    stake = float(query.data.replace("betstake_", ""))
    return await _save_bet(query.message, context, stake, edit=True)


async def bet_stake_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el stake como texto."""
    try:
        stake = float(update.message.text.replace(",", ".").replace("$", ""))
        if stake <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Monto inválido. Ejemplo: `25` o `25.50`", parse_mode="Markdown")
        return BET_STAKE

    return await _save_bet(update.message, context, stake, edit=False)


async def _save_bet(message, context, stake: float, edit: bool = False):
    """Guarda la apuesta en la base de datos."""
    data = context.user_data["bet_data"]
    user_id = message.chat.id

    if stake > data["bankroll"]:
        text = f"❌ No puedes apostar ${stake:.2f}, tu bankroll es ${data['bankroll']:.2f}"
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return BET_STAKE

    bet_id = await add_user_bet(
        user_id=user_id,
        match_name=data["match"],
        league="",
        pick=data["pick"],
        odds=data["odds"],
        stake=stake,
    )

    br = await get_or_create_bankroll(user_id)
    potential = stake * (data["odds"] - 1)

    text = (
        f"✅ *APUESTA REGISTRADA* (#{bet_id})\n"
        f"{'─' * 28}\n"
        f"⚽ {data['match']}\n"
        f"🎯 {data['pick']} @ {data['odds']:.2f}\n"
        f"💰 Stake: ${stake:.2f}\n"
        f"🎁 Ganancia potencial: +${potential:.2f}\n"
        f"{'─' * 28}\n"
        f"💼 Bankroll restante: *${br['current_bankroll']:.2f}*\n\n"
        f"_Cuando termine el partido usa:_\n"
        f"`/resultado_apuesta {bet_id} win` ✅\n"
        f"`/resultado_apuesta {bet_id} loss` ❌\n"
        f"`/resultado_apuesta {bet_id} void` ↩️"
    )

    if edit:
        await message.edit_text(text, parse_mode="Markdown")
    else:
        await message.reply_text(text, parse_mode="Markdown")

    return ConversationHandler.END


async def cancel_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela el registro de apuesta."""
    await update.message.reply_text("❌ Registro de apuesta cancelado.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════
# RESOLVER APUESTAS Y VER HISTORIAL
# ══════════════════════════════════════════════════════════════

async def resolve_bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resuelve una apuesta. Uso: /resultado_apuesta <id> <win|loss|void>"""
    if len(context.args) < 2:
        # Mostrar apuestas pendientes
        user_id = update.effective_user.id
        pending = await get_user_pending_bets(user_id)
        if not pending:
            await update.message.reply_text("📭 No tienes apuestas pendientes.")
            return

        text = "📋 *APUESTAS PENDIENTES*\n\n"
        for bet in pending:
            text += (
                f"*#{bet['id']}* - {bet['match_name']}\n"
                f"  🎯 {bet['pick']} @ {bet['odds']:.2f} | 💰 ${bet['stake']:.2f}\n\n"
            )
        text += "_Usa: `/resultado_apuesta <id> win/loss/void`_"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    try:
        bet_id = int(context.args[0])
        result = context.args[1].lower()
        if result not in ("win", "loss", "void"):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Uso: `/resultado_apuesta 1 win`\n"
            "Opciones: `win`, `loss`, `void`",
            parse_mode="Markdown",
        )
        return

    res = await resolve_user_bet(bet_id, result)
    if not res:
        await update.message.reply_text("❌ Apuesta no encontrada.")
        return

    bet = res["bet"]
    profit = res["profit"]
    user_id = update.effective_user.id
    br = await get_or_create_bankroll(user_id)

    emoji = {"win": "✅🎉", "loss": "❌", "void": "↩️"}.get(result, "")
    profit_text = f"+${profit:.2f}" if profit >= 0 else f"-${abs(profit):.2f}"

    text = (
        f"{emoji} *APUESTA #{bet_id} - {result.upper()}*\n"
        f"{'─' * 28}\n"
        f"⚽ {bet['match_name']}\n"
        f"🎯 {bet['pick']} @ {bet['odds']:.2f}\n"
        f"💰 Stake: ${bet['stake']:.2f}\n"
        f"📊 P&L: *{profit_text}*\n"
        f"{'─' * 28}\n"
        f"💼 Bankroll: *${br['current_bankroll']:.2f}*"
    )

    await update.message.reply_text(text, parse_mode="Markdown")


async def my_bets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el historial de apuestas del usuario."""
    user_id = update.effective_user.id
    bets = await get_user_bets(user_id, limit=15)

    if not bets:
        await update.message.reply_text(
            "📭 No tienes apuestas registradas.\n"
            "Usa /apostar para registrar tu primera apuesta.",
        )
        return

    text = "📋 *MIS APUESTAS*\n\n"
    for bet in bets:
        result_emoji = {
            "win": "✅", "loss": "❌", "void": "↩️", "pending": "⏳"
        }.get(bet["result"], "❓")

        profit_text = ""
        if bet["result"] != "pending":
            p = bet["profit"] or 0
            profit_text = f" → {'+' if p >= 0 else ''}{p:.2f}"

        text += (
            f"{result_emoji} *#{bet['id']}* {bet['match_name']}\n"
            f"  {bet['pick']} @ {bet['odds']:.2f} | ${bet['stake']:.2f}{profit_text}\n\n"
        )

    text += "_/bankroll para ver tu resumen completo_"
    await update.message.reply_text(text, parse_mode="Markdown")
