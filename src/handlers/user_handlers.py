import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.config import VIP_PRICE, PAYMENT_LINK, SPORTS, GROQ_API_KEY
from src.models.database import add_user, get_user, get_stats, get_recent_tips
from src.services.odds_service import get_upcoming_games, format_games_list
from src.services.ai_analysis_service import AIAnalysisService
from src.utils.formatters import format_tip, format_stats

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await add_user(user.id, user.username or "", user.first_name or "")

    keyboard = [
        [InlineKeyboardButton("🔬 Analizar Partido", callback_data="analizar"),
         InlineKeyboardButton("🔍 Oportunidades", callback_data="oportunidades")],
        [InlineKeyboardButton("💼 Mi Bankroll", callback_data="bankroll"),
         InlineKeyboardButton("🏟 Partidos", callback_data="games")],
        [InlineKeyboardButton("📋 Últimos Tips", callback_data="tips"),
         InlineKeyboardButton("👑 VIP", callback_data="vip_info")],
    ]

    await update.message.reply_text(
        f"👋 ¡Hola *{user.first_name}*!\n\n"
        f"Bienvenido al *Bot de Apuestas Deportivas* 🏆\n\n"
        f"Aquí recibirás tips de apuestas con análisis detallado.\n\n"
        f"📌 *Comandos principales:*\n"
        f"/analizar - Analizar un partido específico\n"
        f"/oportunidades - Mejores apuestas del día\n"
        f"/partidos - Ver próximos partidos\n"
        f"/stats - Estadísticas del canal\n"
        f"/ayuda - Ver todos los comandos\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 *COMANDOS DISPONIBLES*\n\n"
        "🔬 *Análisis:*\n"
        "/analizar - Análisis completo de un partido\n"
        "/oportunidades - Escanear ligas buscando value bets\n"
        "/chat - Pregunta lo que quieras a la IA\n\n"
        "💼 *Bankroll:*\n"
        "/bankroll - Ver tu bankroll y ROI\n"
        "/apostar - Registrar apuesta (lenguaje natural)\n"
        "/misapuestas - Historial de apuestas\n"
        "/resultado\\_apuesta - Resolver apuesta (win/loss)\n"
        "/rendimiento - Gráficas y stats avanzadas\n"
        "/setbankroll - Establecer bankroll inicial\n\n"
        "👤 *General:*\n"
        "/start - Iniciar el bot\n"
        "/stats - Estadísticas del canal\n"
        "/tips - Últimos 10 tips\n"
        "/partidos - Próximos partidos\n"
        "/vip - Info VIP\n"
        "/ayuda - Este mensaje\n\n"
        "🔧 *Admin:*\n"
        "/newtip - Crear nuevo tip\n"
        "/resultado - Actualizar resultado de tip\n"
        "/admin - Panel de administración\n",
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

    if query.data == "bankroll":
        await query.edit_message_text(
            "💼 Usa /bankroll para ver tu estado financiero.\n\n"
            "Comandos útiles:\n"
            "• /setbankroll 500 - Establecer bankroll\n"
            "• /apostar - Registrar apuesta\n"
            "• /kelly 2.10 55 - Calcular stake óptimo"
        )
        return

    elif query.data == "analizar":
        await query.edit_message_text(
            "🔬 Usa el comando /analizar para analizar un partido.\n\n"
            "Te mostrará las ligas disponibles, seleccionas una, "
            "luego el partido, y recibirás un análisis completo con apuestas de valor."
        )
        return

    elif query.data == "oportunidades":
        await query.edit_message_text(
            "🔍 Usa el comando /oportunidades para escanear todas las ligas.\n\n"
            "El bot analizará los próximos partidos de las principales ligas "
            "y te mostrará las mejores apuestas con valor del día."
        )
        return

    elif query.data == "stats":
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


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite al usuario hacer preguntas libres a la IA. Uso: /chat <pregunta>"""
    if not GROQ_API_KEY:
        await update.message.reply_text(
            "❌ La IA no está configurada. Falta GROQ\\_API\\_KEY en .env",
            parse_mode="Markdown",
        )
        return

    user_text = " ".join(context.args) if context.args else ""
    if not user_text:
        await update.message.reply_text(
            "🤖 *Chat con IA*\n\n"
            "Escribe tu pregunta después del comando.\n"
            "Ejemplo: `/chat ¿Cómo va el Barcelona esta temporada?`",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("🤖 Pensando...")

    try:
        ai = AIAnalysisService(GROQ_API_KEY)
        last_analysis = context.user_data.get("last_analysis", "")
        response = await ai.chat(user_text, last_analysis=last_analysis)

        if response:
            await msg.edit_text(f"🤖 *IA*\n\n{response}", parse_mode="Markdown")
        else:
            await msg.edit_text("❌ No pude obtener una respuesta. Intenta de nuevo.")
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await msg.edit_text("❌ Error al procesar tu pregunta. Intenta de nuevo.")
