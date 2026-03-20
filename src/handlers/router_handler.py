"""Router conversacional con IA.

Recibe cualquier mensaje de texto y lo clasifica con Groq (modelo 8B rápido)
para ejecutar la acción correcta sin necesidad de /comandos.

Ejemplos:
  "qué hay bueno para hoy" → busca oportunidades
  "meto 50 al over del barca" → registra apuesta
  "gané la del liverpool" → resuelve apuesta
  "cómo va mi bankroll" → muestra bankroll
  "arma un parlay barca + liverpool" → crea combinada
  "partidos de la premier" → lista partidos próximos
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.config import GROQ_API_KEY
from src.services.ai_analysis_service import AIAnalysisService

logger = logging.getLogger(__name__)


async def router_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all handler que clasifica la intención y delega al handler correcto."""
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    if not GROQ_API_KEY:
        return

    try:
        ai = AIAnalysisService(GROQ_API_KEY)
        result = await ai.classify_intent(user_text)

        if not result or "intent" not in result:
            result = {"intent": "chat", "params": user_text}

        intent = result.get("intent", "chat")
        params = result.get("params", user_text) or user_text

        logger.info(f"Router: '{user_text[:50]}' → intent={intent}, params='{params[:50]}'")

        await _dispatch(intent, params, user_text, update, context)

    except Exception as e:
        logger.error(f"Router: error procesando '{user_text[:50]}': {e}", exc_info=True)
        await update.message.reply_text(
            f"⚠️ Error procesando tu mensaje. Intenta de nuevo o usa un /comando.\n"
            f"_Detalle: {str(e)[:100]}_",
            parse_mode="Markdown",
        )


async def _dispatch(
    intent: str,
    params: str,
    original_text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Ejecuta el handler correspondiente a la intención detectada."""

    from src.handlers.bankroll_handlers import (
        bet_command, parlay_command, resolve_bet_command,
        bankroll_command, my_bets_command, rendimiento_command,
    )
    from src.handlers.analysis_handlers import (
        analyze_command, opportunities_command, matches_command,
    )
    from src.handlers.user_handlers import help_command, chat_command

    # Setear context.args para que los handlers lean los parámetros
    context.args = params.split() if params else []

    dispatch_map = {
        "bet": bet_command,
        "parlay": parlay_command,
        "resolve": resolve_bet_command,
        "bankroll": bankroll_command,
        "my_bets": my_bets_command,
        "stats": rendimiento_command,
        "matches": matches_command,
        "opportunities": opportunities_command,
        "help": help_command,
    }

    handler = dispatch_map.get(intent)

    if handler:
        await handler(update, context)
        return

    if intent == "analyze":
        context.args = params.split() if params else []
        await analyze_command(update, context)
        return

    # Default: chat general con el texto original completo
    context.args = original_text.split()
    await chat_command(update, context)
