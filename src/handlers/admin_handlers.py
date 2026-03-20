from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from src.config import ADMIN_ID, FREE_CHANNEL_ID, VIP_CHANNEL_ID, VIP_PRICE
from src.models.database import (
    add_tip, update_tip_result, get_pending_tips, get_stats,
    set_vip, remove_vip, get_all_users, get_vip_users,
    add_payment, get_total_revenue,
    get_pending_predictions, resolve_prediction, get_prediction_accuracy,
)
from src.utils.formatters import format_tip, format_stats


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ No tienes permisos de administrador.")
            return
        return await func(update, context)
    return wrapper


# Conversation states for tip creation
SPORT, MATCH, PREDICTION, ODDS, STAKE, CONFIDENCE, VIP_CHOICE = range(7)


@admin_only
async def new_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia la creación de un nuevo tip."""
    await update.message.reply_text(
        "📝 *NUEVO TIP*\n\n"
        "🏅 ¿Qué deporte?\n"
        "Escribe el nombre (ej: Fútbol, NBA, Tenis, NFL...)",
        parse_mode="Markdown",
    )
    return SPORT


async def tip_sport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tip_sport"] = update.message.text
    await update.message.reply_text(
        "🏟 ¿Qué partido?\n"
        "Escribe el nombre del partido (ej: Real Madrid vs Barcelona)",
    )
    return MATCH


async def tip_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tip_match"] = update.message.text
    await update.message.reply_text(
        "💡 ¿Cuál es tu predicción?\n"
        "Escribe la predicción (ej: Over 2.5 goles, Victoria Local, Handicap -1.5...)",
    )
    return PREDICTION


async def tip_prediction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tip_prediction"] = update.message.text
    await update.message.reply_text(
        "📊 ¿Cuota? (número decimal, ej: 1.85)",
    )
    return ODDS


async def tip_odds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        odds = float(update.message.text.replace(",", "."))
        context.user_data["tip_odds"] = odds
    except ValueError:
        await update.message.reply_text("❌ Introduce un número válido (ej: 1.85)")
        return ODDS

    keyboard = [[
        InlineKeyboardButton(f"{'⭐' * i} ({i})", callback_data=f"stake_{i}")
        for i in range(1, 6)
    ]]
    await update.message.reply_text(
        "💰 ¿Stake? (1-5)",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return STAKE


async def tip_stake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    stake = int(query.data.replace("stake_", ""))
    context.user_data["tip_stake"] = stake

    keyboard = [
        [InlineKeyboardButton("🟡 Baja", callback_data="conf_baja"),
         InlineKeyboardButton("🟠 Media", callback_data="conf_media")],
        [InlineKeyboardButton("🔴 Alta", callback_data="conf_alta"),
         InlineKeyboardButton("💎 Muy Alta", callback_data="conf_muy_alta")],
    ]
    await query.edit_message_text(
        "🎯 ¿Nivel de confianza?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CONFIDENCE


async def tip_confidence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    confidence = query.data.replace("conf_", "")
    context.user_data["tip_confidence"] = confidence

    keyboard = [
        [InlineKeyboardButton("🆓 FREE (canal público)", callback_data="tipvip_0")],
        [InlineKeyboardButton("👑 VIP (canal privado)", callback_data="tipvip_1")],
    ]
    await query.edit_message_text(
        "📢 ¿Dónde publicar?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return VIP_CHOICE


async def tip_vip_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    is_vip = int(query.data.replace("tipvip_", ""))
    data = context.user_data

    tip_id = await add_tip(
        sport=data["tip_sport"],
        match_name=data["tip_match"],
        prediction=data["tip_prediction"],
        odds=data["tip_odds"],
        stake=data["tip_stake"],
        confidence=data["tip_confidence"],
        is_vip=is_vip,
    )

    tip_data = {
        "sport": data["tip_sport"],
        "match_name": data["tip_match"],
        "prediction": data["tip_prediction"],
        "odds": data["tip_odds"],
        "stake": data["tip_stake"],
        "confidence": data["tip_confidence"],
        "is_vip": is_vip,
        "result": "pending",
        "profit": 0,
    }

    formatted = format_tip(tip_data)
    channel_id = VIP_CHANNEL_ID if is_vip else FREE_CHANNEL_ID

    # Publicar en canal si está configurado
    if channel_id:
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=formatted,
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(
                f"⚠️ Tip guardado (ID: {tip_id}) pero no se pudo publicar en el canal: {e}"
            )
            return ConversationHandler.END

    await query.edit_message_text(
        f"✅ *Tip #{tip_id} publicado!*\n\n{formatted}\n\n"
        f"Usa /resultado {tip_id} win/loss/void para actualizar.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Creación de tip cancelada.")
    return ConversationHandler.END


@admin_only
async def set_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actualiza el resultado de un tip. Uso: /resultado <id> <win|loss|void>"""
    args = context.args
    if not args or len(args) < 2:
        # Mostrar tips pendientes
        pending = await get_pending_tips()
        if not pending:
            await update.message.reply_text("📭 No hay tips pendientes.")
            return

        text = "⏳ *TIPS PENDIENTES*\n\n"
        for tip in pending:
            text += f"• ID *{tip['id']}*: {tip['match_name']} - {tip['prediction']} @{tip['odds']}\n"
        text += "\n📝 Uso: `/resultado <id> <win|loss|void>`"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    tip_id = int(args[0])
    result = args[1].lower()

    if result not in ("win", "loss", "void"):
        await update.message.reply_text("❌ Resultado debe ser: win, loss, o void")
        return

    # Calcular profit (simplificado: stake * (odds - 1) para win, -stake para loss)
    from src.models.database import get_recent_tips
    # We need to get the specific tip
    import aiosqlite
    from src.config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tips WHERE id = ?", (tip_id,)) as cursor:
            tip = await cursor.fetchone()

    if not tip:
        await update.message.reply_text(f"❌ Tip #{tip_id} no encontrado.")
        return

    if result == "win":
        profit = tip["stake"] * (tip["odds"] - 1)
    elif result == "loss":
        profit = -tip["stake"]
    else:
        profit = 0

    await update_tip_result(tip_id, result, profit)

    emoji = {"win": "✅", "loss": "❌", "void": "⚪"}[result]
    text = (
        f"{emoji} *Tip #{tip_id} actualizado*\n\n"
        f"🏟 {tip['match_name']}\n"
        f"💡 {tip['prediction']} @{tip['odds']}\n"
        f"📋 Resultado: *{result.upper()}*\n"
        f"💰 Profit: *{profit:+.2f}u*"
    )

    # Publicar resultado en canal
    channel_id = VIP_CHANNEL_ID if tip["is_vip"] else FREE_CHANNEL_ID
    if channel_id:
        try:
            await context.bot.send_message(
                chat_id=channel_id, text=text, parse_mode="Markdown"
            )
        except Exception:
            pass

    await update.message.reply_text(text, parse_mode="Markdown")


@admin_only
async def add_vip_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Añade un usuario VIP. Uso: /addvip <user_id> [meses]"""
    args = context.args
    if not args:
        await update.message.reply_text("📝 Uso: `/addvip <user_id> [meses]`", parse_mode="Markdown")
        return

    user_id = int(args[0])
    months = int(args[1]) if len(args) > 1 else 1
    expires = (datetime.now() + timedelta(days=30 * months)).strftime("%Y-%m-%d")

    await set_vip(user_id, expires)
    await add_payment(user_id, VIP_PRICE * months, months, update.effective_user.id)

    # Intentar invitar al canal VIP
    if VIP_CHANNEL_ID:
        try:
            invite = await context.bot.create_chat_invite_link(
                VIP_CHANNEL_ID, member_limit=1
            )
            await context.bot.send_message(
                chat_id=user_id,
                text=f"👑 ¡Tu suscripción VIP ha sido activada!\n\n"
                     f"📅 Válida hasta: *{expires}*\n\n"
                     f"🔗 Únete al canal VIP: {invite.invite_link}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ VIP activado pero no se pudo enviar invite: {e}")

    await update.message.reply_text(
        f"✅ Usuario *{user_id}* ahora es VIP hasta *{expires}*",
        parse_mode="Markdown",
    )


@admin_only
async def remove_vip_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remueve VIP. Uso: /removevip <user_id>"""
    args = context.args
    if not args:
        await update.message.reply_text("📝 Uso: `/removevip <user_id>`", parse_mode="Markdown")
        return

    user_id = int(args[0])
    await remove_vip(user_id)

    # Kickear del canal VIP
    if VIP_CHANNEL_ID:
        try:
            await context.bot.ban_chat_member(VIP_CHANNEL_ID, user_id)
            await context.bot.unban_chat_member(VIP_CHANNEL_ID, user_id)
        except Exception:
            pass

    await update.message.reply_text(f"✅ VIP removido para usuario *{user_id}*", parse_mode="Markdown")


@admin_only
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía un mensaje a todos los usuarios. Uso: /broadcast <mensaje>"""
    if not context.args:
        await update.message.reply_text("📝 Uso: `/broadcast <mensaje>`", parse_mode="Markdown")
        return

    message = " ".join(context.args)
    users = await get_all_users()
    sent = 0
    failed = 0

    for user in users:
        try:
            await context.bot.send_message(chat_id=user["user_id"], text=message, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 Broadcast completado\n✅ Enviados: {sent}\n❌ Fallidos: {failed}"
    )


@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panel de administración."""
    stats = await get_stats(30)
    users = await get_all_users()
    vips = await get_vip_users()
    revenue = await get_total_revenue()

    text = (
        "🔧 *PANEL DE ADMINISTRACIÓN*\n\n"
        f"👥 Usuarios totales: *{len(users)}*\n"
        f"👑 Usuarios VIP: *{len(vips)}*\n"
        f"💰 Ingresos totales: *${revenue:.2f}*\n\n"
        f"{format_stats(stats)}\n\n"
        "📌 *Comandos Admin:*\n"
        "/analizar - Análisis completo de un partido\n"
        "/oportunidades - Escanear ligas por value bets\n"
        "/newtip - Crear nuevo tip\n"
        "/resultado - Actualizar resultado\n"
        "/resolver `<id> <home_goals>-<away_goals>` - Resolver predicción\n"
        "/precision - Ver precisión del modelo\n"
        "/pendientes - Predicciones sin resolver\n"
        "/addvip - Añadir VIP\n"
        "/removevip - Quitar VIP\n"
        "/broadcast - Mensaje masivo\n"
        "/admin - Este panel\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@admin_only
async def resolve_prediction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resuelve una predicción con el resultado real.

    Uso: /resolver <id> <goles_local>-<goles_visitante>
    Ejemplo: /resolver 5 2-1
    """
    args = context.args
    if not args or len(args) < 2:
        # Mostrar predicciones pendientes
        pending = await get_pending_predictions(10)
        if not pending:
            await update.message.reply_text("No hay predicciones pendientes de resolver.")
            return

        lines = ["📋 *PREDICCIONES PENDIENTES*\n"]
        for p in pending:
            lines.append(
                f"ID `{p['id']}`: {p['match_name']}\n"
                f"   📅 {p['match_date'] or '?'} | {p['league']}\n"
                f"   🎯 {p['predicted_pick'] or 'Sin pick'} @ {p['predicted_odds']:.2f}\n"
            )
        lines.append("\nUso: /resolver `<id>` `<local>-<visitante>`\nEjemplo: /resolver 5 2-1")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    try:
        pred_id = int(args[0])
        score_parts = args[1].split("-")
        home_goals = int(score_parts[0])
        away_goals = int(score_parts[1])
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Formato inválido.\nUso: /resolver `<id>` `<local>-<visitante>`\nEjemplo: /resolver 5 2-1",
            parse_mode="Markdown",
        )
        return

    await resolve_prediction(pred_id, home_goals, away_goals)
    await update.message.reply_text(
        f"Predicción #{pred_id} resuelta: {home_goals}-{away_goals}"
    )


async def precision_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra métricas de precisión del modelo — disponible para todos.

    Uso: /precision [días]
    """
    days = 30
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            pass

    accuracy = await get_prediction_accuracy(days)

    if accuracy["total"] == 0:
        await update.message.reply_text(
            "📊 No hay predicciones resueltas aún.\n\n"
            "Las predicciones se guardan automáticamente al usar /analizar.\n"
            "Los resultados se resuelven automáticamente cada 2 horas."
        )
        return

    lines = [
        f"📊 *PRECISIÓN DEL MODELO* (últimos {days} días)\n",
        f"{'═' * 28}",
        "",
        f"🎯 Predicciones resueltas: *{accuracy['resolved']}*",
        f"⏳ Pendientes: *{accuracy.get('pending', 0)}*",
        f"✅ Aciertos (pick): *{accuracy['correct']}* / {accuracy['resolved']}",
        f"📈 Accuracy (pick): *{accuracy['accuracy']:.1%}*",
        f"💰 Profit (unidades): *{accuracy['profit']:+.2f}*",
        f"📊 ROI: *{accuracy['roi']:+.1f}%*",
    ]

    # Per-market accuracy (learning system)
    per_market = accuracy.get("per_market", {})
    if per_market:
        lines.extend(["", "🧠 *APRENDIZAJE POR MERCADO:*"])
        for mkt in ["1x2", "over25", "btts"]:
            data = per_market.get(mkt)
            if data and data["total"] > 0:
                n = data["total"]
                c = data["correct"]
                acc = c / n
                emoji = "✅" if acc >= 0.55 else ("🟡" if acc >= 0.45 else "❌")
                label = {"1x2": "Resultado 1X2", "over25": "Over/Under 2.5", "btts": "BTTS"}.get(mkt, mkt)
                lines.append(f"  {emoji} {label}: *{acc:.0%}* ({c}/{n})")

    # Calibración bias
    from src.models.database import get_per_market_calibration
    cal = await get_per_market_calibration(days=days)
    if cal.get("sample_size", 0) >= 5:
        bias_lines = []
        for mkt_key, label in [("over25", "Over 2.5"), ("btts", "BTTS"), ("xg", "xG")]:
            mkt_data = cal.get(mkt_key)
            if mkt_data:
                if mkt_key == "xg":
                    bias = mkt_data.get("bias", 0)
                    if abs(bias) > 0.1:
                        direction = "sobreestima" if bias > 0 else "subestima"
                        bias_lines.append(f"  📐 {label}: {direction} por *{abs(bias):.2f}* goles")
                else:
                    bias = mkt_data.get("bias", 0)
                    if abs(bias) > 0.02:
                        direction = "sobreestima" if bias > 0 else "subestima"
                        bias_lines.append(f"  📐 {label}: {direction} por *{abs(bias):.1%}*")
        if bias_lines:
            lines.extend(["", "📐 *SESGO DETECTADO:*"] + bias_lines)

    # Por confianza
    if accuracy["by_confidence"]:
        lines.extend(["", "📊 *POR CONFIANZA:*"])
        for conf, data in sorted(accuracy["by_confidence"].items()):
            if data["total"] > 0:
                emoji = {"baja": "🟡", "media": "🟠", "alta": "🔴", "muy_alta": "💎"}.get(conf, "⚪")
                lines.append(
                    f"{emoji} {conf}: {data['correct']}/{data['total']} "
                    f"({data['accuracy']:.0%}) | P/L: {data['profit']:+.2f}"
                )

    # Por mercado (pick principal)
    if accuracy["by_market"]:
        lines.extend(["", "📊 *POR TIPO DE PICK:*"])
        for market, data in sorted(accuracy["by_market"].items(), key=lambda x: x[1]["total"], reverse=True):
            if data["total"] > 0 and market:
                lines.append(
                    f"  {market}: {data['correct']}/{data['total']} "
                    f"({data['accuracy']:.0%}) | P/L: {data['profit']:+.2f}"
                )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def pending_predictions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra predicciones pendientes de resolver."""
    pending = await get_pending_predictions(20)

    if not pending:
        await update.message.reply_text("No hay predicciones pendientes de resolver.")
        return

    lines = ["📋 *PREDICCIONES PENDIENTES*\n"]
    for p in pending:
        pick_text = f" → {p['predicted_pick']} @ {p['predicted_odds']:.2f}" if p["predicted_pick"] else ""
        lines.append(
            f"ID `{p['id']}`: *{p['match_name']}*\n"
            f"   📅 {p['match_date'] or '?'} | {p['league']}{pick_text}\n"
        )

    lines.append("\nUsa: /resolver `<id>` `<local>-<visitante>`")
    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4090] + "..."
    await update.message.reply_text(text, parse_mode="Markdown")
