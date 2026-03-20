"""Router conversacional con IA.

Recibe cualquier mensaje de texto y lo clasifica con Groq (modelo 8B rápido)
para ejecutar la acción correcta sin necesidad de /comandos.

Ejemplos:
  "qué hay bueno para hoy" → busca oportunidades
  "meto 50 al over del barca" → registra apuesta
  "gané la del liverpool" → resuelve apuesta
  "cómo va mi bankroll" → muestra bankroll
  "arma un parlay barca + liverpool" → crea combinada
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
        # Sin IA, no podemos rutear - ignorar mensajes sueltos
        return

    ai = AIAnalysisService(GROQ_API_KEY)
    result = await ai.classify_intent(user_text)

    if not result or "intent" not in result:
        # Fallback: tratar como chat general
        result = {"intent": "chat", "params": user_text}

    intent = result.get("intent", "chat")
    params = result.get("params", user_text) or user_text

    logger.info(f"Router: '{user_text[:50]}' → intent={intent}")

    # Dispatch por intención
    await _dispatch(intent, params, user_text, update, context)


async def _dispatch(
    intent: str,
    params: str,
    original_text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Ejecuta el handler correspondiente a la intención detectada."""

    # Importar aquí para evitar imports circulares
    from src.handlers.bankroll_handlers import (
        bet_command, parlay_command, resolve_bet_command,
        bankroll_command, my_bets_command, rendimiento_command,
    )
    from src.handlers.analysis_handlers import (
        analyze_command, opportunities_command,
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
        "opportunities": opportunities_command,
        "help": help_command,
    }

    handler = dispatch_map.get(intent)

    if handler:
        await handler(update, context)
        return

    if intent == "analyze":
        # Análisis usa ConversationHandler pero el path con args es self-contained
        context.args = params.split() if params else []
        await analyze_command(update, context)
        return

    # Default: chat general
    context.args = original_text.split()
    await chat_command(update, context)
