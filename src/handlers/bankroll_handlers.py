"""Handlers para el tracker de bankroll.

Comandos:
  /bankroll          - Ver estado del bankroll y ROI
  /apostar           - Registrar apuesta con lenguaje natural
  /misapuestas       - Ver historial de apuestas
  /resultado_apuesta - Resolver una apuesta (win/loss/void)
  /rendimiento       - Gráficas y stats avanzadas
  /setbankroll       - Establecer bankroll inicial
"""

import io
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.config import GROQ_API_KEY
from src.models.database import (
    get_or_create_bankroll, set_bankroll, add_user_bet,
    resolve_user_bet, get_user_bets, get_user_pending_bets,
    get_user_bet_stats, get_user_bets_timeline, get_user_streak,
    get_user_stats_by_pick, get_user_weekly_stats,
)
from src.services.chart_service import (
    generate_pnl_chart, generate_weekly_chart, generate_pick_stats_chart,
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
# PARLAY / COMBINADA
# ══════════════════════════════════════════════════════════════

async def parlay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra una apuesta combinada (parlay) con lenguaje natural.

    Ejemplos:
      /parlay barca gana x1.85 + liverpool over 2.5 x2.10, le meto 50
      /parlay man u gana 1.50 y btts en juve milan 1.80, $30
    """
    if not GROQ_API_KEY:
        await update.message.reply_text("❌ Falta GROQ\\_API\\_KEY.", parse_mode="Markdown")
        return

    user_text = " ".join(context.args) if context.args else ""
    if not user_text:
        await update.message.reply_text(
            "🎰 *PARLAY / COMBINADA*\n\n"
            "Escribe tus picks separados por `+` o `y`:\n\n"
            "`/parlay barca gana x1.85 + liverpool over 2.5 x2.10, le meto 50`\n"
            "`/parlay man u gana 1.50 y btts juve milan 1.80, $30`\n\n"
            "_La IA detecta cada pata, calcula la cuota combinada y registra todo._",
            parse_mode="Markdown",
        )
        return

    user_id = update.effective_user.id
    br = await get_or_create_bankroll(user_id)

    if br["current_bankroll"] <= 0:
        await update.message.reply_text(
            "💀 Bankroll en $0. Usa `/setbankroll <monto>` para reiniciar.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("🤖 Armando tu combinada...")

    ai = AIAnalysisService(GROQ_API_KEY)
    last_analysis = context.user_data.get("last_analysis", "")
    parsed = await ai.parse_parlay(user_text, last_analysis=last_analysis)

    if not parsed or "error" in parsed:
        error = parsed.get("error", "No entendí") if parsed else "Error de conexión"
        await msg.edit_text(
            f"❌ {error}\n\nEjemplo:\n"
            "`/parlay barca gana x1.85 + liverpool over 2.5 x2.10, 50`",
            parse_mode="Markdown",
        )
        return

    legs = parsed.get("legs", [])
    stake = parsed.get("stake")

    if len(legs) < 2:
        await msg.edit_text(
            "❌ Un parlay necesita al menos 2 picks.\n"
            "Sepáralos con `+` o `y`.",
            parse_mode="Markdown",
        )
        return

    # Validar cuotas
    for leg in legs:
        try:
            leg["odds"] = float(leg["odds"])
            if leg["odds"] <= 1:
                raise ValueError
        except (ValueError, TypeError):
            await msg.edit_text(f"❌ Cuota inválida en: {leg.get('pick', '?')}")
            return

    # Calcular cuota combinada
    combined_odds = 1.0
    for leg in legs:
        combined_odds *= leg["odds"]
    combined_odds = round(combined_odds, 2)

    # Guardar para confirmación
    context.user_data["confirm_parlay"] = {
        "legs": legs,
        "combined_odds": combined_odds,
        "stake": stake,
    }

    # Mostrar resumen
    legs_text = ""
    for i, leg in enumerate(legs, 1):
        legs_text += f"  {i}. {leg.get('match', '?')} → {leg['pick']} @ {leg['odds']:.2f}\n"

    if stake:
        try:
            stake = float(stake)
            potential = stake * (combined_odds - 1)
            stake_text = (
                f"\n💰 Stake: ${stake:.2f}\n"
                f"🎁 Ganancia potencial: +${potential:.2f}"
            )
        except (ValueError, TypeError):
            stake = None
            stake_text = ""
    else:
        stake_text = ""

    keyboard = []
    if stake and stake > 0:
        keyboard.append([
            InlineKeyboardButton("✅ Confirmar", callback_data="confirmparlay_yes"),
            InlineKeyboardButton("❌ Cancelar", callback_data="confirmparlay_no"),
        ])
    else:
        # Necesita stake - mostrar botones de %
        stakes = [
            round(br["current_bankroll"] * 0.01, 2),
            round(br["current_bankroll"] * 0.02, 2),
            round(br["current_bankroll"] * 0.03, 2),
            round(br["current_bankroll"] * 0.05, 2),
        ]
        keyboard = [
            [
                InlineKeyboardButton(f"1% (${stakes[0]:.0f})", callback_data=f"parlaystake_{stakes[0]}"),
                InlineKeyboardButton(f"2% (${stakes[1]:.0f})", callback_data=f"parlaystake_{stakes[1]}"),
            ],
            [
                InlineKeyboardButton(f"3% (${stakes[2]:.0f})", callback_data=f"parlaystake_{stakes[2]}"),
                InlineKeyboardButton(f"5% (${stakes[3]:.0f})", callback_data=f"parlaystake_{stakes[3]}"),
            ],
            [InlineKeyboardButton("❌ Cancelar", callback_data="confirmparlay_no")],
        ]

    await msg.edit_text(
        f"🎰 *PARLAY ({len(legs)} picks)*\n"
        f"{'─' * 28}\n"
        f"{legs_text}"
        f"{'─' * 28}\n"
        f"💹 Cuota combinada: *{combined_odds:.2f}*"
        f"{stake_text}\n"
        f"💼 Bankroll: ${br['current_bankroll']:.2f}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def parlay_stake_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe stake del parlay y muestra confirmación."""
    query = update.callback_query
    await query.answer()

    stake = float(query.data.replace("parlaystake_", ""))
    parlay = context.user_data.get("confirm_parlay")
    if not parlay:
        await query.edit_message_text("❌ No hay parlay pendiente.")
        return

    parlay["stake"] = stake

    legs_text = ""
    for i, leg in enumerate(parlay["legs"], 1):
        legs_text += f"  {i}. {leg.get('match', '?')} → {leg['pick']} @ {leg['odds']:.2f}\n"

    potential = stake * (parlay["combined_odds"] - 1)
    keyboard = [[
        InlineKeyboardButton("✅ Confirmar", callback_data="confirmparlay_yes"),
        InlineKeyboardButton("❌ Cancelar", callback_data="confirmparlay_no"),
    ]]

    await query.edit_message_text(
        f"🎰 *PARLAY ({len(parlay['legs'])} picks)*\n"
        f"{'─' * 28}\n"
        f"{legs_text}"
        f"{'─' * 28}\n"
        f"💹 Cuota combinada: *{parlay['combined_odds']:.2f}*\n"
        f"💰 Stake: ${stake:.2f}\n"
        f"🎁 Ganancia potencial: +${potential:.2f}\n\n"
        f"¿Confirmas?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def confirm_parlay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirma o cancela el parlay."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirmparlay_no":
        context.user_data.pop("confirm_parlay", None)
        await query.edit_message_text("❌ Parlay cancelado.")
        return

    parlay = context.user_data.pop("confirm_parlay", None)
    if not parlay:
        await query.edit_message_text("❌ No hay parlay pendiente.")
        return

    legs = parlay["legs"]
    stake = float(parlay["stake"])
    combined_odds = parlay["combined_odds"]
    user_id = query.from_user.id

    br = await get_or_create_bankroll(user_id)
    if stake > br["current_bankroll"]:
        await query.edit_message_text(f"❌ Bankroll insuficiente (${br['current_bankroll']:.2f})")
        return

    # Construir nombre y pick del parlay
    match_parts = [leg.get("match", "?") for leg in legs]
    match_name = " + ".join(match_parts)
    pick_parts = [f"{leg['pick']} @ {leg['odds']:.2f}" for leg in legs]
    pick = "PARLAY: " + " | ".join(pick_parts)

    bet_id = await add_user_bet(
        user_id=user_id,
        match_name=match_name[:200],  # Limitar longitud
        league="parlay",
        pick=pick[:200],
        odds=combined_odds,
        stake=stake,
    )

    br = await get_or_create_bankroll(user_id)
    potential = stake * (combined_odds - 1)

    legs_text = ""
    for i, leg in enumerate(legs, 1):
        legs_text += f"  {i}. {leg.get('match', '?')} → {leg['pick']} @ {leg['odds']:.2f}\n"

    await query.edit_message_text(
        f"✅ *PARLAY REGISTRADO* (#{bet_id})\n"
        f"{'─' * 28}\n"
        f"{legs_text}"
        f"{'─' * 28}\n"
        f"💹 Cuota combinada: *{combined_odds:.2f}*\n"
        f"💰 Stake: ${stake:.2f}\n"
        f"🎁 Ganancia potencial: +${potential:.2f}\n"
        f"💼 Bankroll: *${br['current_bankroll']:.2f}*\n\n"
        f"`/res gané el parlay #{bet_id}` ✅\n"
        f"`/res perdí el parlay #{bet_id}` ❌",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════
# RENDIMIENTO - GRÁFICAS Y STATS AVANZADAS
# ══════════════════════════════════════════════════════════════

async def rendimiento_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra gráficas de rendimiento y estadísticas avanzadas."""
    user_id = update.effective_user.id
    br = await get_or_create_bankroll(user_id)
    stats = await get_user_bet_stats(user_id)

    resolved = (stats.get("wins", 0) or 0) + (stats.get("losses", 0) or 0) + (stats.get("voids", 0) or 0)
    if resolved < 1:
        await update.message.reply_text(
            "📭 Necesitas al menos 1 apuesta resuelta para ver tu rendimiento.\n"
            "Usa /apostar para registrar y /resultado\\_apuesta para resolver.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("📊 Generando tu reporte de rendimiento...")

    # Obtener todos los datos en paralelo (son queries independientes)
    streak = await get_user_streak(user_id)
    pick_stats = await get_user_stats_by_pick(user_id)
    weekly = await get_user_weekly_stats(user_id)
    timeline = await get_user_bets_timeline(user_id)

    # ── Texto de stats avanzadas ──
    streak_emoji = "🔥" if streak["current_type"] == "win" else "💀"
    streak_text = (
        f"{streak_emoji} *Racha actual:* {streak['current']} {streak['current_type']}s seguidos\n"
        f"🏆 *Mejor racha:* {streak['best_win']} wins | "
        f"📉 *Peor racha:* {streak['worst_loss']} losses"
    )

    # Stats por tipo de apuesta (top 5)
    pick_text = ""
    if pick_stats:
        pick_text = "\n\n📋 *POR TIPO DE APUESTA:*\n"
        for s in pick_stats[:5]:
            total = s["total"]
            w = s["wins"] or 0
            wr = (w / total * 100) if total > 0 else 0
            p = s["profit"] or 0
            sign = "+" if p >= 0 else ""
            emoji = "✅" if p >= 0 else "❌"
            pick_text += f"{emoji} {s['pick']}: {w}/{total} ({wr:.0f}%) → {sign}${p:.2f}\n"

    # Weekly summary (últimas 4 semanas)
    week_text = ""
    if weekly:
        week_text = "\n\n📅 *ÚLTIMAS SEMANAS:*\n"
        for w in weekly[-4:]:
            p = w["profit"] or 0
            sign = "+" if p >= 0 else ""
            emoji = "📈" if p >= 0 else "📉"
            wr = (w["wins"] / w["bets"] * 100) if w["bets"] > 0 else 0
            week_text += f"{emoji} {w['week']}: {sign}${p:.2f} ({w['bets']} apuestas, {wr:.0f}% wr)\n"

    text = (
        f"📊 *REPORTE DE RENDIMIENTO*\n"
        f"{'═' * 28}\n\n"
        f"{streak_text}"
        f"{pick_text}"
        f"{week_text}"
    )

    await msg.edit_text(text, parse_mode="Markdown")

    # ── Generar y enviar gráficas ──
    charts_sent = 0

    # 1. Gráfica de evolución del bankroll
    pnl_chart = generate_pnl_chart(timeline, br["initial_bankroll"])
    if pnl_chart:
        await update.message.reply_photo(
            photo=io.BytesIO(pnl_chart),
            caption="📈 Evolución del Bankroll",
        )
        charts_sent += 1

    # 2. Gráfica semanal
    if weekly and len(weekly) >= 2:
        weekly_chart = generate_weekly_chart(weekly)
        if weekly_chart:
            await update.message.reply_photo(
                photo=io.BytesIO(weekly_chart),
                caption="📅 P&L Semanal",
            )
            charts_sent += 1

    # 3. Gráfica por tipo de apuesta
    if pick_stats and len(pick_stats) >= 2:
        pick_chart = generate_pick_stats_chart(pick_stats)
        if pick_chart:
            await update.message.reply_photo(
                photo=io.BytesIO(pick_chart),
                caption="🎯 Rendimiento por Tipo de Apuesta",
            )
            charts_sent += 1

    if charts_sent == 0:
        await update.message.reply_text(
            "_Necesitas más apuestas resueltas para generar gráficas._",
            parse_mode="Markdown",
        )


# ══════════════════════════════════════════════════════════════
# RESOLVER APUESTAS Y VER HISTORIAL
# ══════════════════════════════════════════════════════════════

async def resolve_bet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resuelve apuestas con lenguaje natural o formato clásico.

    Ejemplos naturales:
      /resultado gané la del barca
      /resultado perdí el over del liverpool
      /resultado se canceló la del juve

    Formato clásico también funciona:
      /resultado 3 win
    """
    user_id = update.effective_user.id
    user_text = " ".join(context.args) if context.args else ""

    if not user_text:
        # Sin argumentos: mostrar pendientes
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
        text += (
            "_Dime el resultado como quieras:_\n"
            "`/resultado gané la del barca`\n"
            "`/resultado perdí el over del liverpool`\n"
            "`/resultado 3 win`"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    # Intentar formato clásico primero: /resultado <id> <win|loss|void>
    if len(context.args) == 2:
        try:
            bet_id = int(context.args[0])
            result = context.args[1].lower()
            if result in ("win", "loss", "void"):
                await _resolve_and_reply(update, bet_id, result)
                return
        except ValueError:
            pass  # No es formato clásico, intentar con IA

    # Lenguaje natural con Groq
    pending = await get_user_pending_bets(user_id)
    if not pending:
        await update.message.reply_text("📭 No tienes apuestas pendientes.")
        return

    if not GROQ_API_KEY:
        await update.message.reply_text(
            "❌ Usa el formato: `/resultado <id> win/loss/void`",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("🤖 Entendiendo...")

    ai = AIAnalysisService(GROQ_API_KEY)
    parsed = await ai.parse_result(user_text, pending)

    if not parsed or "error" in parsed:
        error = parsed.get("error", "No entendí") if parsed else "Error de conexión"
        await msg.edit_text(
            f"❌ {error}\n\n"
            f"Apuestas pendientes:\n" +
            "\n".join(f"  #{b['id']}: {b['match_name']} - {b['pick']}" for b in pending) +
            "\n\n_Intenta: `/resultado gané la del barca`_",
            parse_mode="Markdown",
        )
        return

    # Resolver múltiples apuestas
    if "bets" in parsed:
        results_text = ""
        for item in parsed["bets"]:
            bet_id = item.get("bet_id")
            result = item.get("result", "").lower()
            if bet_id and result in ("win", "loss", "void"):
                res = await resolve_user_bet(bet_id, result)
                if res:
                    p = res["profit"]
                    sign = "+" if p >= 0 else ""
                    emoji = {"win": "✅", "loss": "❌", "void": "↩️"}.get(result, "")
                    results_text += f"{emoji} #{bet_id} {res['bet']['match_name']}: {sign}${p:.2f}\n"

        br = await get_or_create_bankroll(user_id)
        await msg.edit_text(
            f"📊 *RESULTADOS ACTUALIZADOS*\n{'─' * 28}\n{results_text}\n"
            f"💼 Bankroll: *${br['current_bankroll']:.2f}*",
            parse_mode="Markdown",
        )
        return

    # Resolver una sola apuesta
    bet_id = parsed.get("bet_id")
    result = (parsed.get("result") or "").lower()

    if not bet_id or result not in ("win", "loss", "void"):
        await msg.edit_text("❌ No pude determinar el resultado. Intenta de nuevo.")
        return

    await msg.delete()
    await _resolve_and_reply(update, bet_id, result)


async def _resolve_and_reply(update: Update, bet_id: int, result: str):
    """Resuelve una apuesta y envía la respuesta."""
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
