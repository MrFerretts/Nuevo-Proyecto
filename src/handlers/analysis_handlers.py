"""Handlers para análisis de partidos y búsqueda de oportunidades."""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from src.config import ADMIN_ID, FOOTBALL_API_KEY, ODDS_API_KEY
from src.services.stats_service import FootballStatsService, LEAGUE_IDS, LEAGUE_NAMES
from src.services.odds_service import get_upcoming_games
from src.services.analysis_engine import (
    TeamAnalysis, analyze_form, analyze_goals, analyze_h2h,
    find_team_in_standings, estimate_probabilities, find_value_bets,
    format_analysis_report,
)


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


def get_stats_service() -> FootballStatsService:
    global stats_service
    if stats_service is None:
        stats_service = FootballStatsService(FOOTBALL_API_KEY)
    return stats_service


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el análisis de un partido. Uso: /analizar"""
    if not FOOTBALL_API_KEY:
        await update.message.reply_text(
            "❌ *FOOTBALL\\_API\\_KEY no configurada*\n\n"
            "Necesitas una API key de api-football.com (gratis: 100 req/día)\n"
            "Regístrate en: https://www.api-football.com/\n"
            "Luego añádela al .env",
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

    service = get_stats_service()
    season = datetime.now().year
    # Si estamos en enero-junio, la temporada europea empezó el año anterior
    if datetime.now().month <= 6:
        season -= 1

    fixtures = await service.get_upcoming_fixtures(league_id, season, next_n=10)
    if not fixtures:
        await query.edit_message_text(
            f"❌ No hay próximos partidos en {LEAGUE_NAMES.get(league_id, 'esta liga')}."
        )
        return ConversationHandler.END

    context.user_data["analysis_fixtures"] = fixtures
    context.user_data["analysis_season"] = season

    keyboard = []
    for i, fx in enumerate(fixtures):
        home = fx.get("teams", {}).get("home", {}).get("name", "?")
        away = fx.get("teams", {}).get("away", {}).get("name", "?")
        date = fx.get("fixture", {}).get("date", "")[:10]
        keyboard.append([InlineKeyboardButton(
            f"{home} vs {away} ({date})",
            callback_data=f"fixture_{i}",
        )])

    await query.edit_message_text(
        f"🏟 *Próximos partidos - {LEAGUE_NAMES.get(league_id, '')}*\n\n"
        "Selecciona el partido a analizar:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SELECT_MATCH


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

    await query.edit_message_text("🔬 Analizando partido... Esto puede tomar unos segundos.")

    try:
        report = await run_full_analysis(fixture, league_id, season)
        # Telegram limita mensajes a 4096 chars
        if len(report) > 4096:
            parts = [report[i:i+4096] for i in range(0, len(report), 4096)]
            await query.edit_message_text(parts[0], parse_mode="Markdown")
            for part in parts[1:]:
                await query.message.reply_text(part, parse_mode="Markdown")
        else:
            await query.edit_message_text(report, parse_mode="Markdown")
    except Exception as e:
        await query.edit_message_text(f"❌ Error en el análisis: {e}")

    return ConversationHandler.END


async def run_full_analysis(fixture: dict, league_id: int, season: int) -> str:
    """Ejecuta el análisis completo de un partido."""
    service = get_stats_service()

    home_info = fixture.get("teams", {}).get("home", {})
    away_info = fixture.get("teams", {}).get("away", {})
    home_id = home_info.get("id")
    away_id = away_info.get("id")
    home_name = home_info.get("name", "Local")
    away_name = away_info.get("name", "Visitante")

    # Recoger datos (los endpoints con 'last' funcionan en plan gratuito)
    home_form_fixtures = await service.get_team_form(home_id, last=10) if home_id else []
    away_form_fixtures = await service.get_team_form(away_id, last=10) if away_id else []
    h2h_fixtures = await service.get_head_to_head(home_id, away_id, last=10) if (home_id and away_id) else []
    standings = await service.get_standings(league_id, season)

    # Analizar forma
    home_form_score, home_form_detail = analyze_form(home_form_fixtures, home_id)
    away_form_score, away_form_detail = analyze_form(away_form_fixtures, away_id)

    # Analizar goles
    home_goals = analyze_goals(home_form_fixtures, home_id)
    away_goals = analyze_goals(away_form_fixtures, away_id)

    # Analizar H2H
    h2h = analyze_h2h(h2h_fixtures, home_id)

    # Clasificación
    home_standing = find_team_in_standings(standings, home_id)
    away_standing = find_team_in_standings(standings, away_id)

    # Construir TeamAnalysis
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

    # Estimar probabilidades
    probs = estimate_probabilities(home_analysis, away_analysis, h2h)

    # Obtener cuotas: primero de _odds_data embebido, luego buscar en The Odds API
    odds = _extract_embedded_odds(fixture, home_name)
    if not any(v > 0 for v in odds.values()):
        odds = await _get_market_odds(fixture, home_name, away_name)

    # Encontrar value bets
    suggestions = find_value_bets(probs, odds)

    # Generar reporte
    return format_analysis_report(home_analysis, away_analysis, h2h, probs, suggestions)


def _extract_embedded_odds(fixture: dict, home_name: str) -> dict:
    """Extrae cuotas del _odds_data embebido desde The Odds API."""
    odds_result = {"home": 0, "draw": 0, "away": 0, "over25": 0, "under25": 0, "btts_yes": 0, "btts_no": 0}
    bookmakers = fixture.get("_odds_data", [])
    if not bookmakers:
        return odds_result

    for bk in bookmakers:
        for market in bk.get("markets", []):
            if market["key"] == "h2h":
                for o in market.get("outcomes", []):
                    if o["name"].lower() in home_name.lower() or home_name.lower() in o["name"].lower():
                        odds_result["home"] = max(odds_result["home"], o["price"])
                    elif o["name"] == "Draw":
                        odds_result["draw"] = max(odds_result["draw"], o["price"])
                    else:
                        odds_result["away"] = max(odds_result["away"], o["price"])
            elif market["key"] == "totals":
                for o in market.get("outcomes", []):
                    if o["name"] == "Over":
                        odds_result["over25"] = max(odds_result["over25"], o["price"])
                    elif o["name"] == "Under":
                        odds_result["under25"] = max(odds_result["under25"], o["price"])
    return odds_result


async def _get_market_odds(fixture: dict, home_name: str, away_name: str) -> dict:
    """Intenta obtener cuotas del mercado para el partido."""
    odds_result = {"home": 0, "draw": 0, "away": 0, "over25": 0, "under25": 0, "btts_yes": 0, "btts_no": 0}

    if not ODDS_API_KEY:
        return odds_result

    # Mapear liga a sport_key de The Odds API
    sport_keys = [
        "soccer_epl", "soccer_spain_la_liga", "soccer_uefa_champions_league",
        "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_france_ligue_one",
    ]

    for sport_key in sport_keys:
        games = await get_upcoming_games(sport_key, limit=20)
        if not games:
            continue

        for game in games:
            game_home = game.get("home_team", "").lower()
            game_away = game.get("away_team", "").lower()

            # Match fuzzy
            if (home_name.lower() in game_home or game_home in home_name.lower() or
                    away_name.lower() in game_away or game_away in away_name.lower()):

                bookmakers = game.get("bookmakers", [])
                if bookmakers:
                    for bk in bookmakers:
                        for market in bk.get("markets", []):
                            if market["key"] == "h2h":
                                for o in market.get("outcomes", []):
                                    if o["name"].lower() in game_home:
                                        odds_result["home"] = max(odds_result["home"], o["price"])
                                    elif o["name"] == "Draw":
                                        odds_result["draw"] = max(odds_result["draw"], o["price"])
                                    else:
                                        odds_result["away"] = max(odds_result["away"], o["price"])
                            elif market["key"] == "totals":
                                for o in market.get("outcomes", []):
                                    if o["name"] == "Over":
                                        odds_result["over25"] = max(odds_result["over25"], o["price"])
                                    elif o["name"] == "Under":
                                        odds_result["under25"] = max(odds_result["under25"], o["price"])
                    return odds_result

    return odds_result


async def opportunities_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Escanea múltiples ligas buscando oportunidades automáticamente."""
    if not FOOTBALL_API_KEY:
        await update.message.reply_text(
            "❌ Configura FOOTBALL\\_API\\_KEY en .env primero.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("🔍 Escaneando ligas en busca de oportunidades... Esto puede tomar 1-2 minutos.")

    service = get_stats_service()
    season = datetime.now().year
    if datetime.now().month <= 6:
        season -= 1

    all_suggestions = []

    for league_name, league_id in LEAGUE_IDS.items():
        try:
            fixtures = await service.get_upcoming_fixtures(league_id, season, next_n=3)
            if not fixtures:
                continue

            standings = await service.get_standings(league_id, season)

            for fixture in fixtures[:2]:  # Analizar top 2 por liga para no gastar requests
                home_info = fixture.get("teams", {}).get("home", {})
                away_info = fixture.get("teams", {}).get("away", {})
                home_id = home_info.get("id")
                away_id = away_info.get("id")

                home_form = await service.get_team_form(home_id, last=7)
                away_form = await service.get_team_form(away_id, last=7)
                h2h_fixtures = await service.get_head_to_head(home_id, away_id, last=5)

                home_form_score, home_detail = analyze_form(home_form, home_id)
                away_form_score, away_detail = analyze_form(away_form, away_id)
                home_goals = analyze_goals(home_form, home_id)
                away_goals = analyze_goals(away_form, away_id)
                h2h = analyze_h2h(h2h_fixtures, home_id)
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

                probs = estimate_probabilities(home_analysis, away_analysis, h2h)
                odds = await _get_market_odds(fixture, home_info.get("name", ""), away_info.get("name", ""))
                suggestions = find_value_bets(probs, odds)

                for s in suggestions:
                    all_suggestions.append({
                        "league": LEAGUE_NAMES.get(league_id, league_name),
                        "match": f"{home_info.get('name', '?')} vs {away_info.get('name', '?')}",
                        "date": fixture.get("fixture", {}).get("date", "")[:10],
                        "suggestion": s,
                    })
        except Exception:
            continue  # Skip liga si falla

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
