from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.config import VIP_PRICE, PAYMENT_LINK, SPORTS
from src.models.database import add_user, get_user, get_stats, get_recent_tips
from src.services.odds_service import get_upcoming_games, format_games_list
from src.utils.formatters import format_tip, format_stats


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(user.id, user.username or "", user.first_name or "")

    keyboard = [
        [InlineKeyboardButton("📊 Estadísticas", callback_data="stats"),
         InlineKeyboardButton("🏟 Partidos", callback_data="games")],
        [InlineKeyboardButton("📋 Últimos Tips", callback_data="tips"),
         InlineKeyboardButton("👑 VIP", callback_data="vip_info")],
    ]

    await update.message.reply_text(
        f"👋 ¡Hola *{user.first_name}*!\n\n"
        f"Bienvenido al *Bot de Apuestas Deportivas* 🏆\n\n"
        f"Aquí recibirás tips de apuestas con análisis detallado.\n\n"
        f"📌 *Comandos disponibles:*\n"
        f"/stats - Ver estadísticas del canal\n"
        f"/tips - Ver últimos tips\n"
        f"/partidos - Ver próximos partidos\n"
        f"/vip - Info sobre suscripción VIP\n"
        f"/ayuda - Ver todos los comandos\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 *COMANDOS DISPONIBLES*\n\n"
        "👤 *Usuarios:*\n"
        "/start - Iniciar el bot\n"
        "/stats - Estadísticas del canal\n"
        "/tips - Últimos 10 tips\n"
        "/partidos - Próximos partidos\n"
        "/vip - Info VIP\n"
        "/ayuda - Este mensaje\n",
        parse_mode="Markdown",
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await get_stats(30)
    text = format_stats(stats)
    await update.message.reply_text(text, parse_mode="Markdown")


async def tips_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = await get_recent_tips(10)
    if not tips:
        await update.message.reply_text("📭 Aún no hay tips publicados.")
        return

    text = "📋 *ÚLTIMOS TIPS*\n\n"
    for tip in tips:
        text += format_tip(dict(tip), show_result=True) + "\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(name.replace("_", " ").title(), callback_data=f"sport_{key}")]
        for name, key in SPORTS.items()
    ]
    await update.message.reply_text(
        "🏟 *Selecciona un deporte:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def vip_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    is_vip = user and user["is_vip"]

    if is_vip:
        text = (
            f"👑 *Ya eres miembro VIP!*\n\n"
            f"Tu suscripción expira: *{user['vip_expires']}*\n\n"
            f"¡Gracias por tu confianza!"
        )
    else:
        text = (
            f"👑 *SUSCRIPCIÓN VIP*\n\n"
            f"💰 Precio: *${VIP_PRICE}/mes*\n\n"
            f"✨ *Beneficios VIP:*\n"
            f"• Tips exclusivos de alta confianza\n"
            f"• Acceso al canal VIP privado\n"
            f"• Análisis detallados pre-partido\n"
            f"• Soporte directo con el tipster\n\n"
            f"📩 Para suscribirte:\n"
            f"1. Realiza el pago aquí: {PAYMENT_LINK}\n"
            f"2. Envía el comprobante al admin\n"
            f"3. ¡Listo! Se activará tu VIP"
        )

    await update.message.reply_text(text, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "stats":
        stats = await get_stats(30)
        await query.edit_message_text(format_stats(stats), parse_mode="Markdown")

    elif query.data == "tips":
        tips = await get_recent_tips(10)
        if not tips:
            await query.edit_message_text("📭 Aún no hay tips publicados.")
            return
        text = "📋 *ÚLTIMOS TIPS*\n\n"
        for tip in tips:
            text += format_tip(dict(tip), show_result=True) + "\n\n"
        await query.edit_message_text(text, parse_mode="Markdown")

    elif query.data == "games":
        keyboard = [
            [InlineKeyboardButton(name.replace("_", " ").title(), callback_data=f"sport_{key}")]
            for name, key in SPORTS.items()
        ]
        await query.edit_message_text(
            "🏟 *Selecciona un deporte:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    elif query.data.startswith("sport_"):
        sport_key = query.data.replace("sport_", "")
        await query.edit_message_text("⏳ Buscando partidos...")
        games = await get_upcoming_games(sport_key, limit=5)
        text = format_games_list(games) if games else "❌ No se pudieron obtener los partidos."
        await query.edit_message_text(text, parse_mode="Markdown")

    elif query.data == "vip_info":
        user = await get_user(query.from_user.id)
        is_vip = user and user["is_vip"]
        if is_vip:
            text = f"👑 *Ya eres VIP!* Expira: {user['vip_expires']}"
        else:
            text = (
                f"👑 *SUSCRIPCIÓN VIP* - ${VIP_PRICE}/mes\n\n"
                f"📩 Paga aquí: {PAYMENT_LINK}\n"
                f"Luego envía comprobante al admin."
            )
        await query.edit_message_text(text, parse_mode="Markdown")
