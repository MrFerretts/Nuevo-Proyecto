"""Handlers para el tracker de bankroll.

Comandos:
  /bankroll          - Ver estado del bankroll y ROI
  /apostar           - Registrar apuesta con lenguaje natural
  /misapuestas       - Ver historial de apuestas
  /resultado_apuesta - Resolver una apuesta (win/loss/void)
  /setbankroll       - Establecer bankroll inicial
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.config import GROQ_API_KEY
from src.models.database import (
    get_or_create_bankroll, set_bankroll, add_user_bet,
    resolve_user_bet, get_user_bets, get_user_pending_bets,
    get_user_bet_stats,
)
from src.services.ai_analysis_service import AIAnalysisService

logger = logging.getLogger(__name__)


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
        f"_Usa /apostar para registrar una apuesta_"
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


# ══════════════════════════════════════════════════════════════
# REGISTRAR APUESTA CON LENGUAJE NATURAL
# ══════════════════════════════════════════════════════════════

async def bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra una apuesta con lenguaje natural.

    Ejemplos:
      /apostar le meto 50 al man u over 1.5, paga x3
      /apostar 30 al barca gana, cuota 1.85
      /apostar over 2.5 en el liverpool arsenal, 20 dolares a 2.10
    """
    if not GROQ_API_KEY:
        await update.message.reply_text("❌ Falta GROQ\\_API\\_KEY para usar esta función.", parse_mode="Markdown")
        return

    user_text = " ".join(context.args) if context.args else ""
    if not user_text:
        await update.message.reply_text(
            "📝 *REGISTRAR APUESTA*\n\n"
            "Escríbelo como quieras, la IA lo entiende:\n\n"
            "`/apostar le meto 50 al man u over 1.5, paga x3`\n"
            "`/apostar 30 al barca gana, cuota 1.85`\n"
            "`/apostar btts en liverpool arsenal, 20 a 1.90`\n\n"
            "_La IA detecta el partido, tu apuesta, la cuota y el monto._",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id
    br = await get_or_create_bankroll(user_id)

    if br["current_bankroll"] <= 0:
        await update.message.reply_text(
            "💀 Tu bankroll está en $0. Usa `/setbankroll <monto>` para reiniciar.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("🤖 Entendiendo tu apuesta...")

    # Usar Groq para parsear el lenguaje natural
    ai = AIAnalysisService(GROQ_API_KEY)
    last_analysis = context.user_data.get("last_analysis", "")
    parsed = await ai.parse_bet(user_text, last_analysis=last_analysis)

    if not parsed or "error" in parsed:
        error_msg = parsed.get("error", "No entendí tu apuesta") if parsed else "Error de conexión con IA"
        await msg.edit_text(
            f"❌ {error_msg}\n\n"
            "Intenta algo como:\n"
            "`/apostar 50 al man u over 1.5, paga x3`",
            parse_mode="Markdown",
        )
        return

    match_name = parsed.get("match", "")
    pick = parsed.get("pick", "")
    odds = parsed.get("odds")
    stake = parsed.get("stake")

    # Validar campos obligatorios
    if not pick:
        await msg.edit_text("❌ No pude identificar qué quieres apostar. Intenta de nuevo.")
        return

    # Pedir datos faltantes
    missing = []
    if not match_name:
        missing.append("partido")
    if not odds:
        missing.append("cuota")
    if not stake:
        missing.append("monto")

    if missing:
        # Guardar lo que tenemos y pedir lo que falta
        context.user_data["pending_bet"] = parsed
        await msg.edit_text(
            f"🤖 Entendí esto:\n"
            f"{'⚽ ' + match_name if match_name else ''}"
            f"{'🎯 ' + pick if pick else ''}\n"
            f"{'💹 Cuota: ' + str(odds) if odds else ''}"
            f"{'💰 Stake: $' + str(stake) if stake else ''}\n\n"
            f"❓ Me falta: *{', '.join(missing)}*\n"
            f"Intenta de nuevo con todos los datos.",
            parse_mode="Markdown",
        )
        return

    # Validar odds y stake
    try:
        odds = float(odds)
        stake = float(stake)
        if odds <= 1 or stake <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await msg.edit_text("❌ La cuota o el monto no son válidos. Intenta de nuevo.")
        return

    if stake > br["current_bankroll"]:
        await msg.edit_text(
            f"❌ No puedes apostar ${stake:.2f}, tu bankroll es ${br['current_bankroll']:.2f}",
        )
        return

    # Guardar datos para confirmación
    context.user_data["confirm_bet"] = {
        "match": match_name,
        "pick": pick,
        "odds": odds,
        "stake": stake,
    }

    potential = stake * (odds - 1)
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="confirmbet_yes"),
            InlineKeyboardButton("❌ Cancelar", callback_data="confirmbet_no"),
        ]
    ]

    await msg.edit_text(
        f"📝 *¿Confirmas esta apuesta?*\n"
        f"{'─' * 28}\n"
        f"⚽ {match_name}\n"
        f"🎯 {pick}\n"
        f"💹 Cuota: {odds:.2f}\n"
        f"💰 Stake: ${stake:.2f}\n"
        f"🎁 Ganancia potencial: +${potential:.2f}\n"
        f"{'─' * 28}\n"
        f"💼 Bankroll: ${br['current_bankroll']:.2f}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def confirm_bet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la confirmación o cancelación de una apuesta."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirmbet_no":
        context.user_data.pop("confirm_bet", None)
        await query.edit_message_text("❌ Apuesta cancelada.")
        return

    # confirmbet_yes
    data = context.user_data.pop("confirm_bet", None)
    if not data:
        await query.edit_message_text("❌ No hay apuesta pendiente.")
        return

    user_id = query.from_user.id

    bet_id = await add_user_bet(
        user_id=user_id,
        match_name=data["match"],
        league="",
        pick=data["pick"],
        odds=data["odds"],
        stake=data["stake"],
    )

    br = await get_or_create_bankroll(user_id)
    potential = data["stake"] * (data["odds"] - 1)

    await query.edit_message_text(
        f"✅ *APUESTA REGISTRADA* (#{bet_id})\n"
        f"{'─' * 28}\n"
        f"⚽ {data['match']}\n"
        f"🎯 {data['pick']} @ {data['odds']:.2f}\n"
        f"💰 Stake: ${data['stake']:.2f}\n"
        f"🎁 Ganancia potencial: +${potential:.2f}\n"
        f"{'─' * 28}\n"
        f"💼 Bankroll restante: *${br['current_bankroll']:.2f}*\n\n"
        f"_Cuando termine el partido usa:_\n"
        f"`/resultado_apuesta {bet_id} win` ✅\n"
        f"`/resultado_apuesta {bet_id} loss` ❌\n"
        f"`/resultado_apuesta {bet_id} void` ↩️",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════
# RESOLVER APUESTAS Y VER HISTORIAL
# ══════════════════════════════════════════════════════════════

async def resolve_bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resuelve una apuesta. Uso: /resultado_apuesta <id> <win|loss|void>"""
    if len(context.args) < 2:
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
