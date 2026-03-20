"""Handlers para análisis de partidos y búsqueda de oportunidades.

Usa football-data.org como fuente primaria (datos actuales, gratis 10 req/min)
y API-Football como fallback para ligas no cubiertas.
"""

import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from src.config import ADMIN_ID, FOOTBALL_API_KEY, FOOTBALL_DATA_API_KEY, ODDS_API_KEY, GROQ_API_KEY
from src.services.stats_service import FootballStatsService, LEAGUE_IDS, LEAGUE_NAMES, LEAGUE_TO_ODDS_SPORT
from src.services.football_data_service import FootballDataService, COMPETITION_MAP
from src.services.odds_service import get_upcoming_games, get_match_odds
from src.models.database import save_prediction
from src.services.analysis_engine import (
    TeamAnalysis, analyze_form, analyze_goals, analyze_h2h,
    find_team_in_standings, estimate_probabilities, find_value_bets,
    format_analysis_report, calculate_league_averages_from_standings,
)
from src.services.ai_analysis_service import AIAnalysisService

logger = logging.getLogger(__name__)


def _get_current_season() -> int:
    """Calcula la temporada actual de fútbol."""
    now = datetime.now()
    return now.year - 1 if now.month <= 6 else now.year


def _calc_rest_days(last_match_date: str) -> int:
    """Calcula días de descanso desde el último partido."""
    if not last_match_date:
        return -1
    try:
        last = datetime.fromisoformat(last_match_date.replace("Z", "+00:00"))
        now = datetime.now(last.tzinfo) if last.tzinfo else datetime.now()
        return (now - last).days
    except Exception:
        return -1


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ No tienes permisos de administrador.")
            return
        return await func(update, context)
    return wrapper


# Conversation states
SELECT_LEAGUE, SELECT_MATCH = range(2)

stats_service = None
fd_service = None
ai_service = None


def get_ai_service() -> AIAnalysisService | None:
    """Obtiene AIAnalysisService si hay API key configurada."""
    global ai_service
    if ai_service is None and GROQ_API_KEY:
        ai_service = AIAnalysisService(GROQ_API_KEY)
        logger.info("AI Analysis Service inicializado (Groq)")
    return ai_service


def get_stats_service() -> FootballStatsService:
    global stats_service
    if stats_service is None:
        stats_service = FootballStatsService(FOOTBALL_API_KEY)
    return stats_service


def get_fd_service():
    """Obtiene FootballDataService si hay API key configurada."""
    global fd_service
    if fd_service is None and FOOTBALL_DATA_API_KEY:
        logger.info(f"Inicializando FootballDataService (key length={len(FOOTBALL_DATA_API_KEY)}, starts={FOOTBALL_DATA_API_KEY[:4]}...)")
        fd_service = FootballDataService(FOOTBALL_DATA_API_KEY)
    elif not FOOTBALL_DATA_API_KEY:
        logger.warning("FOOTBALL_DATA_API_KEY no está configurada - football-data.org no disponible")
    return fd_service


def can_use_fd(league_id: int) -> bool:
    """Verifica si football-data.org soporta esta liga en el plan gratuito."""
    return FOOTBALL_DATA_API_KEY and COMPETITION_MAP.get(league_id) is not None


async def _save_analysis_prediction(
    home_name: str, away_name: str, league_id: int,
    match_date: str, probs: dict, suggestions: list,
    fd_match_id: int = None,
):
    """Guarda la predicción principal para tracking de precisión."""
    try:
        league_name = LEAGUE_NAMES.get(league_id, "Desconocida")
        match_name = f"{home_name} vs {away_name}"

        # Guardar la mejor sugerencia (si hay)
        suggestion_data = None
        if suggestions:
            s = suggestions[0]
            suggestion_data = {
                "market": s.market,
                "pick": s.pick,
                "odds": s.odds,
                "edge": s.value,
                "confidence": s.confidence,
            }

        pred_id = await save_prediction(
            match_name=match_name,
            league=league_name,
            match_date=match_date[:10] if match_date else "",
            home_team=home_name,
            away_team=away_name,
            probs=probs,
            suggestion=suggestion_data,
            fd_match_id=fd_match_id,
            league_id=league_id,
        )
        logger.info(f"Predicción guardada: ID={pred_id} - {match_name} (fd_match_id={fd_match_id})")
    except Exception as e:
        logger.warning(f"Error guardando predicción: {e}")


# Cache de equipos por nombre → fd team_id (se llena al buscar en standings)
_fd_team_cache: dict[str, int] = {}


async def _try_fd_team_data(fd, home_name: str, away_name: str,
                            home_id: int, away_id: int) -> tuple[list, list, int | None, int | None]:
    """Obtiene datos de equipos desde football-data.org buscando en standings de ligas top.

    Los equipos de Europa League juegan en ligas domésticas que SÍ están en el
    plan gratuito. Buscamos el equipo por nombre en los standings de las 5 grandes
    ligas + Champions, y una vez encontrado su fd_id, obtenemos sus partidos.

    Returns: (home_matches, away_matches, fd_home_id, fd_away_id)
    """
    home_matches = []
    away_matches = []
    fd_home_id = None
    fd_away_id = None

    try:
        # Buscar equipos en standings de ligas disponibles
        fd_home_id = await _find_fd_team_in_leagues(fd, home_name)
        fd_away_id = await _find_fd_team_in_leagues(fd, away_name)

        if fd_home_id:
            home_matches = await fd.get_team_matches(fd_home_id, limit=15)
            logger.info(f"FD team data: {home_name} (fd_id={fd_home_id}) → {len(home_matches)} partidos")
        else:
            logger.warning(f"FD: no se encontró '{home_name}' en ninguna liga disponible")

        if fd_away_id:
            away_matches = await fd.get_team_matches(fd_away_id, limit=15)
            logger.info(f"FD team data: {away_name} (fd_id={fd_away_id}) → {len(away_matches)} partidos")
        else:
            logger.warning(f"FD: no se encontró '{away_name}' en ninguna liga disponible")

    except Exception as e:
        logger.warning(f"Error buscando team data en FD: {e}")

    return home_matches, away_matches, fd_home_id, fd_away_id


async def _find_fd_team_in_leagues(fd, team_name: str) -> int | None:
    """Busca un equipo por nombre en los standings de todas las ligas disponibles.

    Los equipos de Europa League siempre juegan en una liga doméstica que
    football-data.org sí tiene en el plan gratuito (PL, PD, SA, BL1, FL1, etc.).
    """
    name_lower = team_name.lower()

    # Revisar cache primero
    if name_lower in _fd_team_cache:
        return _fd_team_cache[name_lower]

    # Ligas a buscar (las del plan gratis con más equipos europeos)
    search_leagues = ["PL", "PD", "SA", "BL1", "FL1", "DED", "PPL", "CL"]

    for comp_code in search_leagues:
        try:
            standings = await fd.get_standings(comp_code)
            if not standings:
                continue

            for entry in standings:
                team_data = entry.get("team", {})
                fd_name = team_data.get("name", "").lower()
                fd_short = team_data.get("shortName", "").lower()
                fd_tla = team_data.get("tla", "").lower()
                fd_id = team_data.get("id")

                if not fd_id:
                    continue

                # Cachear todos los equipos que vemos
                _fd_team_cache[fd_name] = fd_id
                if fd_short:
                    _fd_team_cache[fd_short] = fd_id

                # Verificar match con el equipo buscado
                if _team_name_matches(name_lower, fd_name, fd_short, fd_tla):
                    _fd_team_cache[name_lower] = fd_id
                    logger.info(f"FD team found: '{team_name}' → id={fd_id} (en {comp_code}, nombre FD='{team_data.get('name')}')")
                    return fd_id

        except Exception as e:
            logger.warning(f"Error buscando en standings de {comp_code}: {e}")
            continue

    logger.warning(f"FD team NOT found: '{team_name}' en ninguna liga")
    return None


def _team_name_matches(search: str, name: str, short: str, tla: str) -> bool:
    """Verifica si un nombre de equipo buscado coincide con datos de football-data.org.

    Maneja casos como:
    - "Athletic Club" vs "Athletic Club"
    - "AS Roma" vs "Roma"
    - "Tottenham Hotspur" vs "Tottenham"
    - "Man United" vs "Manchester United"
    """
    if search == name or search == short:
        return True
    # Contenido parcial (ambas direcciones)
    if len(search) >= 4 and (search in name or name in search):
        return True
    if short and len(short) >= 3 and (search in short or short in search):
        return True
    # Palabras clave significativas
    noise = {"fc", "cf", "sc", "ac", "as", "ss", "de", "la", "el", "cd", "ud", "rc", "sd", "1.", "real", "sporting", "club", "united", "city"}
    search_words = set(search.split()) - noise
    name_words = set(name.split()) - noise
    if search_words and name_words:
        overlap = search_words & name_words
        if overlap and any(len(w) >= 4 for w in overlap):
            return True
    return False


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el análisis de un partido. Acepta texto directo o menú.

    Ejemplos:
      /analizar                    → muestra menú de ligas
      /analizar barca vs sevilla   → busca el partido directamente
      /analizar el del liverpool   → busca partido del Liverpool
    """
    if not FOOTBALL_API_KEY and not FOOTBALL_DATA_API_KEY:
        await update.message.reply_text(
            "❌ *Necesitas al menos una API key de estadísticas*\n\n"
            "Opciones (ambas gratuitas):\n"
            "1. football-data.org (10 req/min): https://www.football-data.org/\n"
            "2. api-football.com (100 req/día): https://www.api-football.com/\n\n"
            "Añade FOOTBALL\\_DATA\\_API\\_KEY o FOOTBALL\\_API\\_KEY al .env",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    user_text = " ".join(context.args) if context.args else ""

    # Si el usuario escribió algo, intentar buscar el partido directamente con IA
    if user_text and GROQ_API_KEY:
        msg = await update.message.reply_text("🔬 Buscando partido...")

        # Cargar partidos de todas las ligas
        all_matches = await _load_all_upcoming_matches()

        if not all_matches:
            await msg.edit_text("❌ No se pudieron obtener los próximos partidos. Intenta con el menú: /analizar")
            return ConversationHandler.END

        ai = AIAnalysisService(GROQ_API_KEY)
        parsed = await ai.parse_match_query(user_text, all_matches)

        if parsed and "match_index" in parsed:
            match_info = None
            for m in all_matches:
                if m["index"] == parsed["match_index"]:
                    match_info = m
                    break

            if match_info:
                league_id = match_info["league_id"]
                fixture = match_info["fixture_data"]
                use_fd = match_info.get("use_fd", False)
                season = _get_current_season()

                context.user_data["analysis_league_id"] = league_id
                context.user_data["analysis_season"] = season
                context.user_data["analysis_use_fd"] = use_fd

                home = fixture.get("teams", {}).get("home", {}).get("name", "?")
                away = fixture.get("teams", {}).get("away", {}).get("name", "?")
                await msg.edit_text(f"🔬 Analizando *{home} vs {away}*...", parse_mode="Markdown")

                try:
                    if use_fd and can_use_fd(league_id):
                        report, bet_suggestions = await run_fd_analysis(fixture, league_id)
                    else:
                        report, bet_suggestions = await run_full_analysis(fixture, league_id, season)

                    context.user_data["last_analysis"] = report

                    if len(report) > 4096:
                        parts = [report[i:i+4096] for i in range(0, len(report), 4096)]
                        await msg.edit_text(parts[0], parse_mode="Markdown")
                        for part in parts[1:]:
                            await update.message.reply_text(part, parse_mode="Markdown")
                    else:
                        await msg.edit_text(report, parse_mode="Markdown")

                    await _send_quick_bet_buttons(update.message, context, bet_suggestions)
                except Exception as e:
                    logger.error(f"Analysis error: {e}", exc_info=True)
                    await msg.edit_text(f"❌ Error en el análisis: {e}")

                return ConversationHandler.END

        # No encontró el partido, mostrar menú normal
        await msg.edit_text("🤔 No encontré ese partido. Te muestro las ligas disponibles:")
        # Fall through al menú de ligas...

    # Menú de ligas (comportamiento original)
    keyboard = []
    row = []
    for key, name in sorted(LEAGUE_NAMES.items(), key=lambda x: x[1]):
        row.append(InlineKeyboardButton(name, callback_data=f"league_{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    if not user_text:
        header = (
            "🔬 *ANALIZAR PARTIDO*\n\n"
            "Puedes escribir directo:\n"
            "`/analizar barca vs sevilla`\n"
            "`/analizar el del liverpool`\n\n"
            "O selecciona la liga:"
        )
    else:
        header = "🔬 *ANALIZAR PARTIDO*\n\nSelecciona la liga:"

    await update.message.reply_text(
        header,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SELECT_LEAGUE


async def _load_all_upcoming_matches() -> list:
    """Carga próximos partidos de todas las ligas para búsqueda con IA."""
    all_matches = []
    idx = 0

    for league_id, league_name in LEAGUE_NAMES.items():
        try:
            if can_use_fd(league_id):
                fd = get_fd_service()
                comp_code = COMPETITION_MAP[league_id]
                fd_matches = await fd.get_upcoming_matches(comp_code, limit=5)
                if fd_matches:
                    fixtures = _convert_fd_fixtures(fd_matches)
                    for fx in fixtures:
                        home = fx.get("teams", {}).get("home", {}).get("name", "?")
                        away = fx.get("teams", {}).get("away", {}).get("name", "?")
                        all_matches.append({
                            "index": idx,
                            "home": home,
                            "away": away,
                            "league_id": league_id,
                            "league_name": league_name,
                            "fixture_data": fx,
                            "use_fd": True,
                        })
                        idx += 1
        except Exception as e:
            logger.warning(f"Error cargando partidos de {league_name}: {e}")
            continue

    return all_matches


async def select_league(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    league_id = int(query.data.replace("league_", ""))
    context.user_data["analysis_league_id"] = league_id

    await query.edit_message_text("⏳ Buscando próximos partidos...")

    season = datetime.now().year
    if datetime.now().month <= 6:
        season -= 1

    fixtures = []
    use_fd = False

    # Intentar football-data.org primero (datos actuales, más requests gratis)
    if can_use_fd(league_id):
        fd = get_fd_service()
        comp_code = COMPETITION_MAP[league_id]
        fd_matches = await fd.get_upcoming_matches(comp_code, limit=10)
        if fd_matches:
            use_fd = True
            fixtures = _convert_fd_fixtures(fd_matches)
            logger.info(f"Using football-data.org for league {league_id} ({comp_code}): {len(fixtures)} fixtures")

    # Fallback a API-Football / Odds API
    if not fixtures and FOOTBALL_API_KEY:
        service = get_stats_service()
        fixtures = await service.get_upcoming_fixtures(league_id, season, next_n=10)

    if not fixtures:
        await query.edit_message_text(
            f"❌ No hay próximos partidos en {LEAGUE_NAMES.get(league_id, 'esta liga')}."
        )
        return ConversationHandler.END

    context.user_data["analysis_fixtures"] = fixtures
    context.user_data["analysis_season"] = season
    context.user_data["analysis_use_fd"] = use_fd

    keyboard = []
    for i, fx in enumerate(fixtures):
        home = fx.get("teams", {}).get("home", {}).get("name", "?")
        away = fx.get("teams", {}).get("away", {}).get("name", "?")
        date = fx.get("fixture", {}).get("date", "")[:10]
        keyboard.append([InlineKeyboardButton(
            f"{home} vs {away} ({date})",
            callback_data=f"fixture_{i}",
        )])

    source = "football-data.org" if use_fd else "API-Football"
    await query.edit_message_text(
        f"🏟 *Próximos partidos - {LEAGUE_NAMES.get(league_id, '')}*\n"
        f"📡 Fuente: {source}\n\n"
        "Selecciona el partido a analizar:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SELECT_MATCH


def _convert_fd_fixtures(fd_matches: list) -> list:
    """Convierte partidos de football-data.org al formato interno."""
    fixtures = []
    for m in fd_matches:
        home_team = m.get("homeTeam", {})
        away_team = m.get("awayTeam", {})
        fixtures.append({
            "fixture": {
                "id": m.get("id", ""),
                "date": m.get("utcDate", ""),
            },
            "teams": {
                "home": {"id": home_team.get("id"), "name": home_team.get("name", "?")},
                "away": {"id": away_team.get("id"), "name": away_team.get("name", "?")},
            },
            "_source": "football-data.org",
            "_fd_match_id": m.get("id"),
        })
    return fixtures


import json as _json


async def _send_quick_bet_buttons(message, context, bet_suggestions: list):
    """Envía botones de apuesta rápida después de un análisis."""
    if not bet_suggestions:
        return

    # Guardar sugerencias en user_data para el callback
    context.user_data["quick_bets"] = bet_suggestions

    keyboard = []
    for i, s in enumerate(bet_suggestions):
        label = f"💰 {s['pick']} @ {s['odds']:.2f}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"quickbet_{i}")])

    await message.reply_text(
        "⚡ *¿Quieres apostar?*\n"
        "_Selecciona una apuesta o usa /apostar para personalizar:_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def quick_bet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el tap en un botón de apuesta rápida del análisis."""
    query = update.callback_query
    await query.answer()

    idx = int(query.data.replace("quickbet_", ""))
    bets = context.user_data.get("quick_bets", [])

    if idx >= len(bets):
        await query.edit_message_text("❌ Apuesta no disponible.")
        return

    bet = bets[idx]
    # Guardar como confirm_bet para reusar el flujo de confirmación
    from src.models.database import get_or_create_bankroll
    user_id = query.from_user.id
    br = await get_or_create_bankroll(user_id)
    bankroll = br["current_bankroll"]

    # Calcular stake sugerido (2% del bankroll)
    suggested_stake = round(bankroll * 0.02, 2)

    stakes = [
        round(bankroll * 0.01, 2),
        round(bankroll * 0.02, 2),
        round(bankroll * 0.03, 2),
        round(bankroll * 0.05, 2),
    ]

    # Guardar datos de la apuesta
    context.user_data["quickbet_data"] = {
        "match": bet["match"],
        "pick": bet["pick"],
        "odds": bet["odds"],
    }

    keyboard = [
        [
            InlineKeyboardButton(f"1% (${stakes[0]:.0f})", callback_data=f"qbstake_{stakes[0]}"),
            InlineKeyboardButton(f"2% (${stakes[1]:.0f})", callback_data=f"qbstake_{stakes[1]}"),
        ],
        [
            InlineKeyboardButton(f"3% (${stakes[2]:.0f})", callback_data=f"qbstake_{stakes[2]}"),
            InlineKeyboardButton(f"5% (${stakes[3]:.0f})", callback_data=f"qbstake_{stakes[3]}"),
        ],
        [InlineKeyboardButton("❌ No apostar", callback_data="qbstake_cancel")],
    ]

    await query.edit_message_text(
        f"💰 *{bet['match']}*\n"
        f"🎯 {bet['pick']} @ {bet['odds']:.2f}\n"
        f"💼 Bankroll: ${bankroll:.2f}\n\n"
        f"¿Cuánto apuestas?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def quick_bet_stake_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirma la apuesta rápida con el stake seleccionado."""
    query = update.callback_query
    await query.answer()

    if query.data == "qbstake_cancel":
        await query.edit_message_text("👍 No se registró apuesta.")
        return

    stake = float(query.data.replace("qbstake_", ""))
    data = context.user_data.pop("quickbet_data", None)

    if not data:
        await query.edit_message_text("❌ Datos de apuesta no encontrados.")
        return

    from src.models.database import add_user_bet, get_or_create_bankroll
    user_id = query.from_user.id

    br = await get_or_create_bankroll(user_id)
    if stake > br["current_bankroll"]:
        await query.edit_message_text(f"❌ Bankroll insuficiente (${br['current_bankroll']:.2f})")
        return

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

    await query.edit_message_text(
        f"✅ *APUESTA REGISTRADA* (#{bet_id})\n"
        f"{'─' * 28}\n"
        f"⚽ {data['match']}\n"
        f"🎯 {data['pick']} @ {data['odds']:.2f}\n"
        f"💰 Stake: ${stake:.2f}\n"
        f"🎁 Ganancia potencial: +${potential:.2f}\n"
        f"{'─' * 28}\n"
        f"💼 Bankroll: *${br['current_bankroll']:.2f}*\n\n"
        f"`/res gané la #{bet_id}` o `/res perdí la #{bet_id}`",
        parse_mode="Markdown",
    )


async def select_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.replace("fixture_", ""))

    fixtures = context.user_data.get("analysis_fixtures", [])
    if idx >= len(fixtures):
        await query.edit_message_text("❌ Partido no encontrado.")
        return ConversationHandler.END

    fixture = fixtures[idx]
    league_id = context.user_data["analysis_league_id"]
    season = context.user_data["analysis_season"]
    use_fd = context.user_data.get("analysis_use_fd", False)

    await query.edit_message_text("🔬 Analizando partido... Esto puede tomar unos segundos.")

    try:
        if use_fd and can_use_fd(league_id):
            report, bet_suggestions = await run_fd_analysis(fixture, league_id)
        else:
            report, bet_suggestions = await run_full_analysis(fixture, league_id, season)
        logger.info(f"Análisis completado: {len(report)} chars")
        context.user_data["last_analysis"] = report

        if len(report) > 4096:
            parts = [report[i:i+4096] for i in range(0, len(report), 4096)]
            await query.edit_message_text(parts[0], parse_mode="Markdown")
            for part in parts[1:]:
                await query.message.reply_text(part, parse_mode="Markdown")
        else:
            await query.edit_message_text(report, parse_mode="Markdown")

        # Mostrar botones de apuesta rápida si hay value bets
        await _send_quick_bet_buttons(query.message, context, bet_suggestions)

    except Exception as e:
        logger.error(f"Analysis error: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Error en el análisis: {e}")

    return ConversationHandler.END


def _build_team_analysis(name: str, stats: dict, standing: dict) -> TeamAnalysis:
    """Construye un TeamAnalysis desde stats de football-data.org."""
    return TeamAnalysis(
        name=name,
        form_score=stats["form_score"],
        form_detail=stats["form_detail"],
        goals_scored_avg=stats["goals_scored_avg"],
        goals_conceded_avg=stats["goals_conceded_avg"],
        over25_pct=stats["over25_pct"],
        btts_pct=stats["btts_pct"],
        clean_sheets_pct=stats["clean_sheet_pct"],
        league_position=standing["position"],
        points=standing["points"],
        # Nuevos campos
        home_goals_scored_avg=stats.get("home_goals_scored_avg", 0),
        home_goals_conceded_avg=stats.get("home_goals_conceded_avg", 0),
        away_goals_scored_avg=stats.get("away_goals_scored_avg", 0),
        away_goals_conceded_avg=stats.get("away_goals_conceded_avg", 0),
        streak=stats.get("streak", "?"),
        matches_played=stats.get("matches_played", 0),
        wins=stats.get("wins", 0),
        draws=stats.get("draws", 0),
        losses=stats.get("losses", 0),
        avg_total_goals=stats.get("avg_total_goals", 0),
        over15_pct=stats.get("over15_pct", 0),
        over35_pct=stats.get("over35_pct", 0),
        home_win_pct=stats.get("home_win_pct", 0),
        away_win_pct=stats.get("away_win_pct", 0),
    )


async def _fetch_injuries(home_id: int, away_id: int) -> tuple[list, list]:
    """Intenta obtener lesiones desde API-Football si está disponible."""
    if not FOOTBALL_API_KEY:
        return [], []

    try:
        service = get_stats_service()
        home_injuries_raw = await service.get_injuries(home_id)
        away_injuries_raw = await service.get_injuries(away_id)

        home_injuries = []
        for inj in home_injuries_raw[:5]:
            player = inj.get("player", {})
            name = player.get("name", "?")
            reason = player.get("reason", "")
            home_injuries.append(f"{name} ({reason})" if reason else name)

        away_injuries = []
        for inj in away_injuries_raw[:5]:
            player = inj.get("player", {})
            name = player.get("name", "?")
            reason = player.get("reason", "")
            away_injuries.append(f"{name} ({reason})" if reason else name)

        return home_injuries, away_injuries
    except Exception as e:
        logger.warning(f"Error obteniendo lesiones: {e}")
        return [], []


async def run_fd_analysis(fixture: dict, league_id: int) -> str:
    """Análisis usando football-data.org (datos actuales de temporada)."""
    fd = get_fd_service()
    comp_code = COMPETITION_MAP[league_id]

    home_info = fixture.get("teams", {}).get("home", {})
    away_info = fixture.get("teams", {}).get("away", {})
    home_id = home_info.get("id")
    away_id = away_info.get("id")
    home_name = home_info.get("name", "Local")
    away_name = away_info.get("name", "Visitante")

    logger.info(f"run_fd_analysis: {home_name} (id={home_id}) vs {away_name} (id={away_id}), comp={comp_code}")

    # Obtener datos desde football-data.org
    home_matches = await fd.get_team_matches(home_id, limit=15) if home_id else []
    away_matches = await fd.get_team_matches(away_id, limit=15) if away_id else []
    standings = await fd.get_standings(comp_code)

    logger.info(f"run_fd_analysis: home_matches={len(home_matches)}, away_matches={len(away_matches)}, standings={len(standings)}")

    # Si no hay datos de partidos, intentar fallback con API-Football
    if not home_matches and not away_matches:
        logger.warning(f"football-data.org no devolvió partidos. Intentando fallback con API-Football...")
        if FOOTBALL_API_KEY:
            return await run_full_analysis(fixture, league_id, _get_current_season())

    # H2H: usar match_id si disponible
    h2h_data = {}
    h2h_matches = []
    fd_match_id = fixture.get("_fd_match_id")
    if fd_match_id:
        result = await fd.get_head_to_head(fd_match_id, limit=10)
        if isinstance(result, tuple) and len(result) == 2:
            h2h_data, h2h_matches = result
        else:
            logger.warning(f"H2H devolvió resultado inesperado: {type(result)}")

    # Calcular estadísticas con el servicio de football-data.org
    home_stats = fd.calc_team_stats(home_matches, home_id)
    away_stats = fd.calc_team_stats(away_matches, away_id)
    h2h = fd.calc_h2h_stats(h2h_matches, home_id) if h2h_matches else {
        "home_wins": 0, "away_wins": 0, "draws": 0,
        "avg_goals": 0, "btts_pct": 0,
    }

    # Clasificación
    home_standing = fd.find_in_standings(standings, home_id)
    away_standing = fd.find_in_standings(standings, away_id)

    # Construir TeamAnalysis con todos los campos
    home_analysis = _build_team_analysis(home_name, home_stats, home_standing)
    away_analysis = _build_team_analysis(away_name, away_stats, away_standing)

    # === NUEVAS MEJORAS v2 ===

    # xG real de Understat
    try:
        from src.services.understat_service import get_team_xg
        home_xg_data = await get_team_xg(home_name, league_id)
        away_xg_data = await get_team_xg(away_name, league_id)
        if home_xg_data:
            home_analysis.real_xg = home_xg_data["xg"]
            home_analysis.real_xga = home_xg_data["xga"]
            logger.info(f"Understat xG: {home_name} → xG={home_xg_data['xg']:.2f}, xGA={home_xg_data['xga']:.2f}")
        if away_xg_data:
            away_analysis.real_xg = away_xg_data["xg"]
            away_analysis.real_xga = away_xg_data["xga"]
            logger.info(f"Understat xG: {away_name} → xG={away_xg_data['xg']:.2f}, xGA={away_xg_data['xga']:.2f}")
    except Exception as e:
        logger.warning(f"Understat xG no disponible: {e}")

    # Días de descanso (desde último partido)
    if home_matches:
        home_analysis.last_match_date = home_matches[0].get("utcDate", "")
        home_analysis.rest_days = _calc_rest_days(home_analysis.last_match_date)
    if away_matches:
        away_analysis.last_match_date = away_matches[0].get("utcDate", "")
        away_analysis.rest_days = _calc_rest_days(away_analysis.last_match_date)

    # Lesiones (desde API-Football si disponible)
    home_injuries, away_injuries = await _fetch_injuries(home_id, away_id)
    if home_injuries:
        home_analysis.injuries = home_injuries
        home_analysis.injuries_count = len(home_injuries)
    if away_injuries:
        away_analysis.injuries = away_injuries
        away_analysis.injuries_count = len(away_injuries)

    # Cuotas del mercado PRIMERO (para market anchor)
    odds = await _get_odds_for_match(league_id, fixture, home_name, away_name)

    # Guardar snapshot de odds para tracking de line movement
    try:
        from src.models.database import save_odds_snapshot
        await save_odds_snapshot(f"{home_name} vs {away_name}", league_id, odds)
    except Exception:
        pass

    # Probabilidades (v2: con market anchor + xG real + rest days)
    probs = estimate_probabilities(home_analysis, away_analysis, h2h,
                                   league_id=league_id, standings=standings,
                                   market_odds=odds)

    # Value bets (v2: con Kelly Criterion)
    suggestions = find_value_bets(probs, odds)

    # Line movement: obtener historial de odds si existe
    try:
        from src.models.database import get_odds_history
        odds_hist = await get_odds_history(f"{home_name} vs {away_name}")
    except Exception:
        odds_hist = None

    report = format_analysis_report(home_analysis, away_analysis, h2h, probs, suggestions,
                                    odds_history=odds_hist)

    # Guardar predicción para tracking (con fd_match_id para auto-resolución)
    match_date = fixture.get("fixture", {}).get("date", "")
    fd_match_id = fixture.get("_fd_match_id")
    await _save_analysis_prediction(home_name, away_name, league_id, match_date, probs, suggestions, fd_match_id=fd_match_id)

    # Análisis con IA (si está configurado)
    ai = get_ai_service()
    logger.info(f"AI service disponible: {ai is not None}, GROQ_API_KEY configurada: {bool(GROQ_API_KEY)}")
    if ai:
        league_name = LEAGUE_NAMES.get(league_id, "")
        ai_text = await ai.generate_ai_analysis(
            home_analysis, away_analysis, h2h, probs, suggestions, league_name
        )
        if ai_text:
            report += f"\n\n{'═' * 28}\n\n{ai_text}"
        else:
            logger.warning("AI analysis retornó None - revisa logs de Groq arriba")
    else:
        logger.info("Saltando análisis IA (no hay GROQ_API_KEY o servicio no inicializado)")

    # Guardar metadata para botones de apuesta rápida
    match_name = f"{home_name} vs {away_name}"
    bet_suggestions = []
    for s in suggestions[:3]:
        bet_suggestions.append({
            "match": match_name,
            "pick": s.pick,
            "odds": round(s.odds, 2),
        })

    return report, bet_suggestions


async def run_full_analysis(fixture: dict, league_id: int, season: int) -> str:
    """Análisis usando API-Football (fallback).

    Para ligas sin cobertura en football-data.org (Europa League, Liga MX, etc.),
    intenta primero obtener datos de equipos desde football-data.org por team name,
    y si no, usa API-Football con fallback a temporadas anteriores.
    """
    service = get_stats_service()

    home_info = fixture.get("teams", {}).get("home", {})
    away_info = fixture.get("teams", {}).get("away", {})
    home_id = home_info.get("id")
    away_id = away_info.get("id")
    home_name = home_info.get("name", "Local")
    away_name = away_info.get("name", "Visitante")

    # Estrategia: intentar football-data.org por equipo (funciona para cualquier
    # equipo europeo aunque la competición no esté en plan free)
    fd = get_fd_service()
    used_fd = False
    if fd:
        fd_home_matches, fd_away_matches, fd_home_id, fd_away_id = await _try_fd_team_data(fd, home_name, away_name, home_id, away_id)
        if fd_home_matches or fd_away_matches:
            used_fd = True
            logger.info(f"Europa League fix: usando football-data.org por equipo "
                        f"(home={len(fd_home_matches)}, away={len(fd_away_matches)} partidos)")

            # IMPORTANTE: usar fd_home_id/fd_away_id (de football-data.org), NO home_id/away_id (de API-Football)
            # Si se usa el ID incorrecto, calc_team_stats invierte local/visitante
            home_stats = fd.calc_team_stats(fd_home_matches, fd_home_id) if fd_home_matches else fd._empty_stats()
            away_stats = fd.calc_team_stats(fd_away_matches, fd_away_id) if fd_away_matches else fd._empty_stats()

            h2h = {"home_wins": 0, "away_wins": 0, "draws": 0, "avg_goals": 0, "btts_pct": 0}

            home_analysis = _build_team_analysis(home_name, home_stats,
                                                  {"position": 0, "points": 0})
            away_analysis = _build_team_analysis(away_name, away_stats,
                                                  {"position": 0, "points": 0})

    if not used_fd:
        # Fallback original: API-Football
        home_form_fixtures = await service.get_team_form(home_id, last=10) if home_id else []
        away_form_fixtures = await service.get_team_form(away_id, last=10) if away_id else []
        h2h_fixtures = await service.get_head_to_head(home_id, away_id, last=10) if (home_id and away_id) else []
        standings = await service.get_standings(league_id, season)

        home_form_score, home_form_detail = analyze_form(home_form_fixtures, home_id)
        away_form_score, away_form_detail = analyze_form(away_form_fixtures, away_id)
        home_goals = analyze_goals(home_form_fixtures, home_id)
        away_goals = analyze_goals(away_form_fixtures, away_id)
        h2h = analyze_h2h(h2h_fixtures, home_id)
        home_standing = find_team_in_standings(standings, home_id)
        away_standing = find_team_in_standings(standings, away_id)

        home_analysis = TeamAnalysis(
            name=home_name,
            form_score=home_form_score,
            form_detail=home_form_detail,
            goals_scored_avg=home_goals["scored_avg"],
            goals_conceded_avg=home_goals["conceded_avg"],
            over25_pct=home_goals["over25_pct"],
            btts_pct=home_goals["btts_pct"],
            clean_sheets_pct=home_goals["clean_sheet_pct"],
            league_position=home_standing["position"],
            points=home_standing["points"],
        )

        away_analysis = TeamAnalysis(
            name=away_name,
            form_score=away_form_score,
            form_detail=away_form_detail,
            goals_scored_avg=away_goals["scored_avg"],
            goals_conceded_avg=away_goals["conceded_avg"],
            over25_pct=away_goals["over25_pct"],
            btts_pct=away_goals["btts_pct"],
            clean_sheets_pct=away_goals["clean_sheet_pct"],
            league_position=away_standing["position"],
            points=away_standing["points"],
        )

    probs = estimate_probabilities(home_analysis, away_analysis, h2h,
                                   league_id=league_id)

    odds = _extract_embedded_odds(fixture, home_name, away_name)
    if not any(v > 0 for v in odds.values()):
        odds = await _get_odds_for_match(league_id, fixture, home_name, away_name)

    suggestions = find_value_bets(probs, odds)

    report = format_analysis_report(home_analysis, away_analysis, h2h, probs, suggestions)

    # Guardar predicción para tracking
    match_date = fixture.get("fixture", {}).get("date", "")
    await _save_analysis_prediction(home_name, away_name, league_id, match_date, probs, suggestions)

    # Análisis con IA (si está configurado)
    ai = get_ai_service()
    logger.info(f"[fullback] AI service disponible: {ai is not None}")
    if ai:
        league_name = LEAGUE_NAMES.get(league_id, "")
        ai_text = await ai.generate_ai_analysis(
            home_analysis, away_analysis, h2h, probs, suggestions, league_name
        )
        if ai_text:
            report += f"\n\n{'═' * 28}\n\n{ai_text}"
        else:
            logger.warning("[fullback] AI analysis retornó None")

    # Metadata para botones de apuesta rápida
    match_name = f"{home_name} vs {away_name}"
    bet_suggestions = []
    for s in suggestions[:3]:
        bet_suggestions.append({
            "match": match_name,
            "pick": s.pick,
            "odds": round(s.odds, 2),
        })

    return report, bet_suggestions


def _extract_embedded_odds(fixture: dict, home_name: str, away_name: str) -> dict:
    """Extrae cuotas del _odds_data embebido desde The Odds API.

    Usa fuzzy matching para asignar correctamente las cuotas a home/away.
    """
    from src.services.odds_service import _empty_odds, _fuzzy_match

    odds_result = _empty_odds()
    bookmakers = fixture.get("_odds_data", [])
    if not bookmakers:
        return odds_result

    for bk in bookmakers:
        for market in bk.get("markets", []):
            if market["key"] == "h2h":
                for o in market.get("outcomes", []):
                    if o["name"] == "Draw":
                        odds_result["draw"] = max(odds_result["draw"], o["price"])
                    elif _fuzzy_match(o["name"], home_name):
                        odds_result["home"] = max(odds_result["home"], o["price"])
                    elif _fuzzy_match(o["name"], away_name):
                        odds_result["away"] = max(odds_result["away"], o["price"])
            elif market["key"] == "totals":
                for o in market.get("outcomes", []):
                    point = o.get("point", 2.5)
                    price = o.get("price", 0)
                    if o["name"] == "Over":
                        if point == 1.5:
                            odds_result["over15"] = max(odds_result["over15"], price)
                        elif point == 2.5:
                            odds_result["over25"] = max(odds_result["over25"], price)
                        elif point == 3.5:
                            odds_result["over35"] = max(odds_result["over35"], price)
                    elif o["name"] == "Under":
                        if point == 1.5:
                            odds_result["under15"] = max(odds_result["under15"], price)
                        elif point == 2.5:
                            odds_result["under25"] = max(odds_result["under25"], price)
                        elif point == 3.5:
                            odds_result["under35"] = max(odds_result["under35"], price)
    return odds_result


async def _get_odds_for_match(league_id: int, fixture: dict, home_name: str, away_name: str) -> dict:
    """Obtiene cuotas usando el sport_key correcto para la liga.

    1. Determina el sport_key basándose en league_id
    2. Usa get_match_odds que verifica AMBOS equipos y asigna home/away correctamente
    3. Si no encuentra con la liga específica, intenta Champions League como fallback
    """
    from src.services.odds_service import _empty_odds

    if not ODDS_API_KEY:
        return _empty_odds()

    # Determinar sport_key correcto para esta liga
    sport_key = LEAGUE_TO_ODDS_SPORT.get(league_id)

    if sport_key:
        odds = await get_match_odds(sport_key, home_name, away_name)
        if any(v > 0 for v in odds.values()):
            return odds

    # Fallback: probar Champions League y Europa League si no se encontró
    fallback_keys = ["soccer_uefa_champs_league", "soccer_uefa_europa_league"]
    for fk in fallback_keys:
        if fk == sport_key:
            continue
        odds = await get_match_odds(fk, home_name, away_name)
        if any(v > 0 for v in odds.values()):
            return odds

    logger.warning(f"No se encontraron cuotas para {home_name} vs {away_name} (league_id={league_id})")
    return _empty_odds()


async def opportunities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Escanea múltiples ligas buscando oportunidades automáticamente."""
    if not FOOTBALL_API_KEY and not FOOTBALL_DATA_API_KEY:
        await update.message.reply_text(
            "❌ Configura FOOTBALL\\_DATA\\_API\\_KEY o FOOTBALL\\_API\\_KEY en .env primero.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("🔍 Escaneando ligas en busca de oportunidades... Esto puede tomar 1-2 minutos.")

    season = datetime.now().year
    if datetime.now().month <= 6:
        season -= 1

    all_suggestions = []

    for league_name, league_id in LEAGUE_IDS.items():
        try:
            use_fd = can_use_fd(league_id)

            if use_fd:
                # Usar football-data.org (datos actuales, más requests)
                fd = get_fd_service()
                comp_code = COMPETITION_MAP[league_id]
                fd_matches = await fd.get_upcoming_matches(comp_code, limit=3)
                fixtures = _convert_fd_fixtures(fd_matches) if fd_matches else []
                standings_fd = await fd.get_standings(comp_code) if fd_matches else []
            else:
                fixtures = []
                standings_fd = []

            # Fallback a API-Football si no hay datos de FD
            if not fixtures and FOOTBALL_API_KEY:
                service = get_stats_service()
                fixtures = await service.get_upcoming_fixtures(league_id, season, next_n=3)

            if not fixtures:
                continue

            for fixture in fixtures[:2]:
                home_info = fixture.get("teams", {}).get("home", {})
                away_info = fixture.get("teams", {}).get("away", {})
                home_id = home_info.get("id")
                away_id = away_info.get("id")

                if use_fd and standings_fd:
                    # Análisis con football-data.org
                    home_matches = await fd.get_team_matches(home_id, limit=10) if home_id else []
                    away_matches = await fd.get_team_matches(away_id, limit=10) if away_id else []

                    home_stats = fd.calc_team_stats(home_matches, home_id)
                    away_stats = fd.calc_team_stats(away_matches, away_id)

                    h2h = {"home_wins": 0, "away_wins": 0, "draws": 0, "avg_goals": 0, "btts_pct": 0}
                    fd_match_id = fixture.get("_fd_match_id")
                    if fd_match_id:
                        _, h2h_matches = await fd.get_head_to_head(fd_match_id, limit=5)
                        if h2h_matches:
                            h2h = fd.calc_h2h_stats(h2h_matches, home_id)

                    home_standing = fd.find_in_standings(standings_fd, home_id)
                    away_standing = fd.find_in_standings(standings_fd, away_id)

                    home_analysis = _build_team_analysis(home_info.get("name", "?"), home_stats, home_standing)
                    away_analysis = _build_team_analysis(away_info.get("name", "?"), away_stats, away_standing)
                else:
                    # Análisis con API-Football
                    service = get_stats_service()
                    home_form = await service.get_team_form(home_id, last=7)
                    away_form = await service.get_team_form(away_id, last=7)
                    h2h_fx = await service.get_head_to_head(home_id, away_id, last=5)
                    standings = await service.get_standings(league_id, season)

                    home_form_score, home_detail = analyze_form(home_form, home_id)
                    away_form_score, away_detail = analyze_form(away_form, away_id)
                    home_goals = analyze_goals(home_form, home_id)
                    away_goals = analyze_goals(away_form, away_id)
                    h2h = analyze_h2h(h2h_fx, home_id)
                    home_standing = find_team_in_standings(standings, home_id)
                    away_standing = find_team_in_standings(standings, away_id)

                    home_analysis = TeamAnalysis(
                        name=home_info.get("name", "?"),
                        form_score=home_form_score, form_detail=home_detail,
                        goals_scored_avg=home_goals["scored_avg"],
                        goals_conceded_avg=home_goals["conceded_avg"],
                        over25_pct=home_goals["over25_pct"],
                        btts_pct=home_goals["btts_pct"],
                        clean_sheets_pct=home_goals["clean_sheet_pct"],
                        league_position=home_standing["position"],
                        points=home_standing["points"],
                    )
                    away_analysis = TeamAnalysis(
                        name=away_info.get("name", "?"),
                        form_score=away_form_score, form_detail=away_detail,
                        goals_scored_avg=away_goals["scored_avg"],
                        goals_conceded_avg=away_goals["conceded_avg"],
                        over25_pct=away_goals["over25_pct"],
                        btts_pct=away_goals["btts_pct"],
                        clean_sheets_pct=away_goals["clean_sheet_pct"],
                        league_position=away_standing["position"],
                        points=away_standing["points"],
                    )

                probs = estimate_probabilities(home_analysis, away_analysis, h2h,
                                               league_id=league_id,
                                               standings=standings_fd if use_fd else None)
                odds = await _get_odds_for_match(league_id, fixture, home_info.get("name", ""), away_info.get("name", ""))
                suggestions = find_value_bets(probs, odds)

                for s in suggestions:
                    all_suggestions.append({
                        "league": LEAGUE_NAMES.get(league_id, league_name),
                        "match": f"{home_info.get('name', '?')} vs {away_info.get('name', '?')}",
                        "date": fixture.get("fixture", {}).get("date", "")[:10],
                        "suggestion": s,
                    })
        except Exception:
            continue

    if not all_suggestions:
        await update.message.reply_text(
            "📭 No se encontraron oportunidades claras en este momento.\n\n"
            "Esto puede pasar si:\n"
            "• Las cuotas están bien ajustadas\n"
            "• No hay suficientes datos de partidos próximos\n\n"
            "Intenta de nuevo más tarde o usa /analizar para un partido específico."
        )
        return

    # Ordenar por valor y mostrar top oportunidades
    all_suggestions.sort(key=lambda x: x["suggestion"].value, reverse=True)

    confidence_emoji = {"baja": "🟡", "media": "🟠", "alta": "🔴", "muy_alta": "💎"}
    lines = [
        "🔍 *OPORTUNIDADES DETECTADAS*",
        f"📅 Escaneadas {len(LEAGUE_IDS)} ligas",
        f"💡 {len(all_suggestions)} apuestas con valor encontradas",
        "",
        f"{'═' * 28}",
    ]

    for i, item in enumerate(all_suggestions[:10], 1):
        s = item["suggestion"]
        emoji = confidence_emoji.get(s.confidence, "🟠")
        stake_stars = "⭐" * s.stake
        lines.extend([
            "",
            f"{emoji} *{i}. {item['match']}*",
            f"   🏅 {item['league']} | 📅 {item['date']}",
            f"   💡 *{s.pick}*",
            f"   📊 Cuota: *{s.odds:.2f}* | Valor: *+{s.value:.1%}*",
            f"   💰 Stake: {stake_stars} | Confianza: *{s.confidence.upper()}*",
        ])
        for reason in s.reasoning[:2]:
            lines.append(f"   • {reason}")

    lines.extend([
        "",
        f"{'═' * 28}",
        "",
        "📌 Usa /analizar para ver el análisis completo de un partido específico.",
        "📌 Usa /newtip para publicar una de estas oportunidades como tip.",
    ])

    # Resumen IA de oportunidades
    ai = get_ai_service()
    if ai and all_suggestions:
        ai_summary = await ai.generate_ai_tips_summary(all_suggestions[:8])
        if ai_summary:
            lines.extend(["", f"{'═' * 28}", "", ai_summary])

    text = "\n".join(lines)
    if len(text) > 4096:
        parts = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for part in parts:
            await update.message.reply_text(part, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


# Aliases de ligas para reconocimiento por IA
_LEAGUE_ALIASES = {
    "premier": 39, "premier league": 39, "pl": 39, "epl": 39, "england": 39, "inglaterra": 39,
    "la liga": 140, "liga": 140, "españa": 140, "spain": 140, "laliga": 140, "liga española": 140,
    "serie a": 135, "seriea": 135, "italia": 135, "italy": 135,
    "bundesliga": 78, "buli": 78, "alemania": 78, "germany": 78,
    "ligue 1": 61, "ligue1": 61, "francia": 61, "france": 61,
    "champions": 2, "champions league": 2, "ucl": 2, "cl": 2,
}


def _resolve_league(text: str) -> tuple[int | None, str | None]:
    """Resuelve una liga a partir de texto libre."""
    text_lower = text.lower().strip()
    for alias, lid in _LEAGUE_ALIASES.items():
        if alias in text_lower:
            return lid, LEAGUE_NAMES.get(lid, alias)
    return None, None


async def matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista los próximos partidos de una liga o de todas."""
    text = " ".join(context.args) if context.args else ""

    league_id, league_name = _resolve_league(text)

    if league_id:
        leagues_to_check = [(league_id, league_name)]
    else:
        leagues_to_check = list(LEAGUE_NAMES.items())

    await update.message.reply_text("⏳ Buscando próximos partidos...")

    lines = []
    for lid, lname in leagues_to_check:
        try:
            if not can_use_fd(lid):
                continue
            fd = get_fd_service()
            comp_code = COMPETITION_MAP[lid]
            matches = await fd.get_upcoming_matches(comp_code, limit=10 if league_id else 5)
            if not matches:
                continue

            lines.append(f"\n🏆 *{lname}*")
            for m in matches:
                home = m.get("homeTeam", {}).get("name", "?")
                away = m.get("awayTeam", {}).get("name", "?")
                utc_date = m.get("utcDate", "")
                # Formatear fecha legible
                try:
                    dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
                    date_str = dt.strftime("%a %d/%m %H:%M")
                except Exception:
                    date_str = utc_date[:16] if utc_date else "?"
                lines.append(f"  ⚽ {home} vs {away} — {date_str}")
        except Exception as e:
            logger.warning(f"Error cargando partidos de {lname}: {e}")
            continue

    if not lines:
        await update.message.reply_text(
            "😕 No encontré partidos próximos. Intenta con una liga específica:\n"
            "_\"partidos de la premier\"_, _\"partidos champions\"_",
            parse_mode="Markdown",
        )
        return

    header = f"📅 *Próximos partidos — {league_name}*" if league_id else "📅 *Próximos partidos*"
    result = header + "\n" + "\n".join(lines)

    if len(result) > 4000:
        parts = [result[i:i + 4000] for i in range(0, len(result), 4000)]
        for part in parts:
            await update.message.reply_text(part, parse_mode="Markdown")
    else:
        await update.message.reply_text(result, parse_mode="Markdown")


async def cancel_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Análisis cancelado.")
    return ConversationHandler.END
