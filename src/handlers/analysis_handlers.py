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


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el análisis de un partido. Uso: /analizar"""
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

    keyboard = []
    row = []
    for key, name in sorted(LEAGUE_NAMES.items(), key=lambda x: x[1]):
        row.append(InlineKeyboardButton(name, callback_data=f"league_{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await update.message.reply_text(
        "🔬 *ANALIZAR PARTIDO*\n\n"
        "Selecciona la liga:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SELECT_LEAGUE


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
            report = await run_fd_analysis(fixture, league_id)
        else:
            report = await run_full_analysis(fixture, league_id, season)
        logger.info(f"Análisis completado: {len(report)} chars")
        # Telegram limita mensajes a 4096 chars
        if len(report) > 4096:
            parts = [report[i:i+4096] for i in range(0, len(report), 4096)]
            await query.edit_message_text(parts[0], parse_mode="Markdown")
            for part in parts[1:]:
                await query.message.reply_text(part, parse_mode="Markdown")
        else:
            await query.edit_message_text(report, parse_mode="Markdown")
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

    # Lesiones (desde API-Football si disponible)
    home_injuries, away_injuries = await _fetch_injuries(home_id, away_id)
    if home_injuries:
        home_analysis.injuries = home_injuries
        home_analysis.injuries_count = len(home_injuries)
    if away_injuries:
        away_analysis.injuries = away_injuries
        away_analysis.injuries_count = len(away_injuries)

    # Probabilidades (ahora con Poisson + ajustes + promedios dinámicos)
    probs = estimate_probabilities(home_analysis, away_analysis, h2h,
                                   league_id=league_id, standings=standings)

    # Cuotas del mercado (usando el sport_key correcto para la liga)
    odds = await _get_odds_for_match(league_id, fixture, home_name, away_name)

    # Value bets
    suggestions = find_value_bets(probs, odds)

    report = format_analysis_report(home_analysis, away_analysis, h2h, probs, suggestions)

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

    return report


async def run_full_analysis(fixture: dict, league_id: int, season: int) -> str:
    """Análisis usando API-Football (fallback)."""
    service = get_stats_service()

    home_info = fixture.get("teams", {}).get("home", {})
    away_info = fixture.get("teams", {}).get("away", {})
    home_id = home_info.get("id")
    away_id = away_info.get("id")
    home_name = home_info.get("name", "Local")
    away_name = away_info.get("name", "Visitante")

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

    return report


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


async def cancel_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Análisis cancelado.")
    return ConversationHandler.END
