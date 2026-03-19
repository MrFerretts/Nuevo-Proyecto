import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import BOT_TOKEN, ADMIN_ID, GROQ_API_KEY, FOOTBALL_DATA_API_KEY, FOOTBALL_API_KEY, ODDS_API_KEY
from src.models.database import init_db
from src.handlers.user_handlers import (
    start, help_command, stats_command, tips_command,
    games_command, vip_info, button_callback, chat_command,
)
from src.handlers.admin_handlers import (
    new_tip, tip_sport, tip_match, tip_prediction,
    tip_odds, tip_stake, tip_confidence, tip_vip_choice,
    cancel_tip, set_result, add_vip_user, remove_vip_user,
    broadcast, admin_panel,
    resolve_prediction_command, precision_command, pending_predictions_command,
    SPORT, MATCH, PREDICTION, ODDS, STAKE, CONFIDENCE, VIP_CHOICE,
)
from src.handlers.analysis_handlers import (
    analyze_command, select_league, select_match,
    opportunities_command, cancel_analysis,
    SELECT_LEAGUE, SELECT_MATCH,
)
from src.handlers.bankroll_handlers import (
    bankroll_command, set_bankroll_command, kelly_command,
    bet_command, bet_match, bet_pick, bet_odds,
    bet_stake_button, bet_stake_text, cancel_bet,
    resolve_bet_command, my_bets_command,
    BET_MATCH, BET_PICK, BET_ODDS, BET_STAKE,
)
from src.services.scheduler_service import check_expired_vips, auto_resolve_predictions

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main():
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN no configurado. Copia .env.example a .env y configúralo.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler para crear tips
    tip_conv = ConversationHandler(
        entry_points=[CommandHandler("newtip", new_tip)],
        states={
            SPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tip_sport)],
            MATCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, tip_match)],
            PREDICTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, tip_prediction)],
            ODDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, tip_odds)],
            STAKE: [CallbackQueryHandler(tip_stake, pattern=r"^stake_")],
            CONFIDENCE: [CallbackQueryHandler(tip_confidence, pattern=r"^conf_")],
            VIP_CHOICE: [CallbackQueryHandler(tip_vip_choice, pattern=r"^tipvip_")],
        },
        fallbacks=[CommandHandler("cancelar", cancel_tip)],
    )

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", help_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("estadisticas", stats_command))
    app.add_handler(CommandHandler("tips", tips_command))
    app.add_handler(CommandHandler("partidos", games_command))
    app.add_handler(CommandHandler("vip", vip_info))
    app.add_handler(CommandHandler("chat", chat_command))

    # Bankroll commands
    app.add_handler(CommandHandler("bankroll", bankroll_command))
    app.add_handler(CommandHandler("setbankroll", set_bankroll_command))
    app.add_handler(CommandHandler("kelly", kelly_command))
    app.add_handler(CommandHandler("misapuestas", my_bets_command))
    app.add_handler(CommandHandler("resultado_apuesta", resolve_bet_command))

    # Conversation handler para registrar apuestas
    bet_conv = ConversationHandler(
        entry_points=[CommandHandler("apostar", bet_command)],
        states={
            BET_MATCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_match)],
            BET_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_pick)],
            BET_ODDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, bet_odds)],
            BET_STAKE: [
                CallbackQueryHandler(bet_stake_button, pattern=r"^betstake_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bet_stake_text),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancel_bet)],
    )
    app.add_handler(bet_conv)

    # Conversation handler para análisis
    analysis_conv = ConversationHandler(
        entry_points=[CommandHandler("analizar", analyze_command)],
        states={
            SELECT_LEAGUE: [CallbackQueryHandler(select_league, pattern=r"^league_")],
            SELECT_MATCH: [CallbackQueryHandler(select_match, pattern=r"^fixture_")],
        },
        fallbacks=[CommandHandler("cancelar", cancel_analysis)],
    )

    # Admin commands
    app.add_handler(tip_conv)
    app.add_handler(analysis_conv)
    app.add_handler(CommandHandler("oportunidades", opportunities_command))
    app.add_handler(CommandHandler("resultado", set_result))
    app.add_handler(CommandHandler("addvip", add_vip_user))
    app.add_handler(CommandHandler("removevip", remove_vip_user))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("resolver", resolve_prediction_command))
    app.add_handler(CommandHandler("precision", precision_command))
    app.add_handler(CommandHandler("pendientes", pending_predictions_command))
    app.add_handler(CommandHandler("admin", admin_panel))

    # Callback queries (botones inline)
    app.add_handler(CallbackQueryHandler(button_callback))

    # Inicializar base de datos
    asyncio.get_event_loop().run_until_complete(init_db())

    # Scheduler para tareas periódicas
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_expired_vips,
        "cron",
        hour=9,
        minute=0,
        args=[app.bot],
    )
    # Auto-resolver predicciones cada 2 horas
    scheduler.add_job(
        auto_resolve_predictions,
        "interval",
        hours=2,
        args=[app.bot],
        id="auto_resolve",
        name="Auto-resolver predicciones",
    )
    scheduler.start()
    logger.info("⏰ Scheduler: VIP check (9:00 diario) + Auto-resolve (cada 2h)")

    # Log de diagnóstico de API keys
    logger.info("═══ DIAGNÓSTICO DE API KEYS ═══")
    logger.info(f"  GROQ_API_KEY: {'✅ configurada (' + GROQ_API_KEY[:8] + '...)' if GROQ_API_KEY else '❌ NO CONFIGURADA'}")
    logger.info(f"  FOOTBALL_DATA_API_KEY: {'✅' if FOOTBALL_DATA_API_KEY else '❌'}")
    logger.info(f"  FOOTBALL_API_KEY: {'✅' if FOOTBALL_API_KEY else '❌'}")
    logger.info(f"  ODDS_API_KEY: {'✅' if ODDS_API_KEY else '❌'}")
    logger.info("═══════════════════════════════")

    logger.info("🤖 Bot iniciado!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
