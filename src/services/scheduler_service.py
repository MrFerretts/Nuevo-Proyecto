import logging
from datetime import datetime, timedelta

import aiosqlite

from src.config import ADMIN_ID, FOOTBALL_DATA_API_KEY, ODDS_API_KEY, DB_PATH
from src.models.database import (
    get_vip_users, remove_vip,
    get_pending_predictions, resolve_prediction,
    resolve_user_bet, save_odds_snapshot,
)

logger = logging.getLogger(__name__)


async def check_expired_vips(bot):
    """Revisa y remueve VIPs expirados. Se ejecuta diariamente."""
    vips = await get_vip_users()
    today = datetime.now().strftime("%Y-%m-%d")

    for user in vips:
        if user["vip_expires"] and user["vip_expires"] < today:
            await remove_vip(user["user_id"])
            try:
                await bot.send_message(
                    chat_id=user["user_id"],
                    text="⏰ Tu suscripción VIP ha expirado.\n\n"
                         "Usa /vip para renovar y seguir recibiendo tips exclusivos. 👑",
                )
            except Exception:
                pass


async def auto_resolve_predictions(bot):
    """Auto-resuelve predicciones pendientes consultando resultados reales.

    Busca predicciones con fd_match_id y consulta football-data.org
    para ver si el partido ya terminó. Si terminó, resuelve la predicción.

    Se ejecuta cada 2 horas automáticamente.
    """
    if not FOOTBALL_DATA_API_KEY:
        logger.info("Auto-resolve: sin FOOTBALL_DATA_API_KEY, saltando")
        return

    from src.services.football_data_service import FootballDataService

    fd = FootballDataService(FOOTBALL_DATA_API_KEY)
    pending = await get_pending_predictions(limit=50)

    if not pending:
        logger.info("Auto-resolve: no hay predicciones pendientes")
        return

    logger.info(f"Auto-resolve: revisando {len(pending)} predicciones pendientes...")

    resolved_count = 0
    resolved_details = []

    for pred in pending:
        fd_match_id = pred["fd_match_id"] if "fd_match_id" in pred.keys() else None

        if not fd_match_id:
            # Sin fd_match_id, intentar buscar por nombre en partidos recientes
            # Solo resolver si ya pasó la fecha del partido
            match_date = pred["match_date"]
            if match_date:
                try:
                    match_dt = datetime.strptime(match_date[:10], "%Y-%m-%d")
                    # Si el partido aún no ha ocurrido, skip
                    if match_dt.date() >= datetime.now().date():
                        continue
                except ValueError:
                    continue
            else:
                continue

            # Buscar por nombre de equipos en partidos terminados recientes
            result = await _try_resolve_by_name(fd, pred)
            if result:
                home_goals, away_goals = result
                await resolve_prediction(pred["id"], home_goals, away_goals)
                resolved_count += 1
                was_correct = _check_if_correct(pred, home_goals, away_goals)
                emoji = "✅" if was_correct else "❌"
                resolved_details.append(
                    f"{emoji} {pred['match_name']}: {home_goals}-{away_goals}"
                    f" | {pred['predicted_pick'] or 'sin pick'}"
                )
            continue

        # Con fd_match_id: consultar directamente
        try:
            match_data = await fd.get_match_by_id(fd_match_id)
            if not match_data:
                continue

            status = match_data.get("status")
            if status != "FINISHED":
                continue

            score = match_data.get("score", {}).get("fullTime", {})
            home_goals = score.get("home")
            away_goals = score.get("away")

            if home_goals is None or away_goals is None:
                continue

            await resolve_prediction(pred["id"], home_goals, away_goals)
            resolved_count += 1

            was_correct = _check_if_correct(pred, home_goals, away_goals)
            emoji = "✅" if was_correct else "❌"
            resolved_details.append(
                f"{emoji} {pred['match_name']}: {home_goals}-{away_goals}"
                f" | {pred['predicted_pick'] or 'sin pick'}"
            )
            logger.info(f"Auto-resolved: {pred['match_name']} → {home_goals}-{away_goals}")

        except Exception as e:
            logger.warning(f"Auto-resolve error para match_id={fd_match_id}: {e}")
            continue

    # Notificar al admin si hubo resoluciones
    if resolved_count > 0 and bot and ADMIN_ID:
        correct = sum(1 for d in resolved_details if d.startswith("✅"))
        lines = [
            f"🤖 *AUTO-RESOLUCIÓN*",
            f"",
            f"Se resolvieron *{resolved_count}* predicciones automáticamente:",
            f"✅ Aciertos: *{correct}* | ❌ Fallos: *{resolved_count - correct}*",
            f"",
        ]
        for detail in resolved_details[:15]:
            lines.append(detail)

        if resolved_count > 15:
            lines.append(f"... y {resolved_count - 15} más")

        lines.append(f"\nUsa /precision para ver métricas completas.")

        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text="\n".join(lines),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Error notificando auto-resolve al admin: {e}")

    logger.info(f"Auto-resolve completado: {resolved_count} predicciones resueltas")


async def _try_resolve_by_name(fd, pred) -> tuple[int, int] | None:
    """Intenta resolver una predicción buscando el partido por nombre de equipos.

    Busca partidos recientes finalizados y compara nombres de equipos.
    """
    from src.services.football_data_service import COMPETITION_MAP

    home_team = pred["home_team"].lower()
    away_team = pred["away_team"].lower()
    league_id = pred["league_id"] if "league_id" in pred.keys() else 0

    # Determinar competition code
    comp_code = COMPETITION_MAP.get(league_id) if league_id else None
    if not comp_code:
        return None

    # Buscar partidos terminados en los últimos 3 días
    date_from = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    date_to = datetime.now().strftime("%Y-%m-%d")

    try:
        matches = await fd.get_finished_matches(comp_code, date_from, date_to)
    except Exception:
        return None

    for m in matches:
        m_home = m.get("homeTeam", {}).get("name", "").lower()
        m_away = m.get("awayTeam", {}).get("name", "").lower()

        # Fuzzy match: verificar si los nombres contienen partes relevantes
        home_match = (home_team in m_home or m_home in home_team or
                      _name_overlap(home_team, m_home))
        away_match = (away_team in m_away or m_away in away_team or
                      _name_overlap(away_team, m_away))

        if home_match and away_match:
            score = m.get("score", {}).get("fullTime", {})
            h = score.get("home")
            a = score.get("away")
            if h is not None and a is not None:
                return h, a

    return None


def _name_overlap(name1: str, name2: str) -> bool:
    """Verifica si dos nombres de equipo se solapan significativamente."""
    words1 = set(name1.split())
    words2 = set(name2.split())
    # Eliminar palabras comunes cortas
    common_short = {"fc", "cf", "sc", "ac", "as", "ss", "de", "la", "el", "cd", "ud", "rc", "sd"}
    words1 = words1 - common_short
    words2 = words2 - common_short
    if not words1 or not words2:
        return False
    overlap = words1 & words2
    return len(overlap) >= 1


def _check_if_correct(pred, home_goals: int, away_goals: int) -> bool:
    """Verifica si la predicción fue correcta dado el resultado."""
    pick = pred["predicted_pick"] if pred["predicted_pick"] else ""
    if not pick:
        return False

    if home_goals > away_goals:
        actual = "home_win"
    elif home_goals < away_goals:
        actual = "away_win"
    else:
        actual = "draw"

    total = home_goals + away_goals

    correct_map = {
        "Victoria Local": actual == "home_win",
        "Empate": actual == "draw",
        "Victoria Visitante": actual == "away_win",
        "Over 1.5 Goles": total > 1.5,
        "Under 1.5 Goles": total < 1.5,
        "Over 2.5 Goles": total > 2.5,
        "Under 2.5 Goles": total < 2.5,
        "Over 3.5 Goles": total > 3.5,
        "Under 3.5 Goles": total < 3.5,
        "Ambos Marcan - Sí": home_goals > 0 and away_goals > 0,
        "Ambos Marcan - No": home_goals == 0 or away_goals == 0,
        "Local o Empate (1X)": actual in ("home_win", "draw"),
        "Visitante o Empate (X2)": actual in ("away_win", "draw"),
        "Local o Visitante (12)": actual in ("home_win", "away_win"),
    }
    return correct_map.get(pick, False)


async def auto_resolve_user_bets(bot):
    """Auto-resuelve apuestas de usuarios consultando resultados reales.

    Busca apuestas pendientes, intenta encontrar el resultado real del partido
    en football-data.org, y resuelve automáticamente. Notifica al usuario.

    Se ejecuta cada 2 horas.
    """
    if not FOOTBALL_DATA_API_KEY:
        return

    from src.services.football_data_service import FootballDataService, COMPETITION_MAP

    fd = FootballDataService(FOOTBALL_DATA_API_KEY)

    # Obtener todas las apuestas pendientes de todos los usuarios
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM user_bets WHERE result = 'pending'
               AND created_at <= datetime('now', '-2 hours')
               ORDER BY created_at ASC LIMIT 50"""
        ) as cursor:
            pending_bets = [dict(r) for r in await cursor.fetchall()]

    if not pending_bets:
        return

    logger.info(f"Auto-resolve user bets: {len(pending_bets)} pendientes")

    # Obtener partidos terminados recientes (últimos 3 días) de todas las ligas
    date_from = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    date_to = datetime.now().strftime("%Y-%m-%d")

    all_finished = []
    for league_id, comp_code in COMPETITION_MAP.items():
        if not comp_code:
            continue
        try:
            matches = await fd.get_finished_matches(comp_code, date_from, date_to)
            all_finished.extend(matches)
        except Exception as e:
            logger.warning(f"Error obteniendo finalizados de {comp_code}: {e}")

    if not all_finished:
        return

    resolved_count = 0

    for bet in pending_bets:
        match_name = bet["match_name"].lower()

        # Buscar el partido en los resultados
        for m in all_finished:
            m_home = m.get("homeTeam", {}).get("name", "").lower()
            m_away = m.get("awayTeam", {}).get("name", "").lower()

            # Match por nombre
            if not (_name_overlap(match_name, f"{m_home} {m_away}")):
                continue

            score = m.get("score", {}).get("fullTime", {})
            home_goals = score.get("home")
            away_goals = score.get("away")

            if home_goals is None or away_goals is None:
                continue

            # Determinar si la apuesta ganó
            result = _evaluate_user_bet(bet["pick"], home_goals, away_goals)
            if not result:
                continue

            res = await resolve_user_bet(bet["id"], result)
            if res:
                resolved_count += 1
                profit = res["profit"]
                sign = "+" if profit >= 0 else ""
                emoji = "✅🎉" if result == "win" else "❌"

                # Notificar al usuario
                try:
                    await bot.send_message(
                        chat_id=bet["user_id"],
                        text=(
                            f"{emoji} *APUESTA #{bet['id']} AUTO-RESUELTA*\n"
                            f"{'─' * 28}\n"
                            f"⚽ {bet['match_name']} ({home_goals}-{away_goals})\n"
                            f"🎯 {bet['pick']} @ {bet['odds']:.2f}\n"
                            f"📊 P&L: *{sign}${profit:.2f}*\n"
                            f"{'─' * 28}\n"
                            f"_Resultado detectado automáticamente_"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

            break  # Ya encontró el partido, pasar a la siguiente apuesta

    if resolved_count > 0:
        logger.info(f"Auto-resolve user bets: {resolved_count} apuestas resueltas")


def _evaluate_user_bet(pick: str, home_goals: int, away_goals: int) -> str | None:
    """Evalúa si una apuesta del usuario ganó o perdió.

    Returns: "win", "loss", o None si no puede determinar.
    """
    pick_lower = pick.lower()
    total = home_goals + away_goals

    # Mapeo de picks comunes
    checks = {
        # Victoria
        "victoria local": home_goals > away_goals,
        "gana local": home_goals > away_goals,
        "gana el local": home_goals > away_goals,
        "victoria visitante": home_goals < away_goals,
        "gana visitante": home_goals < away_goals,
        "gana el visitante": home_goals < away_goals,
        "empate": home_goals == away_goals,
        # Over/Under
        "over 0.5": total > 0.5,
        "over 1.5": total > 1.5,
        "over 2.5": total > 2.5,
        "over 3.5": total > 3.5,
        "under 0.5": total < 0.5,
        "under 1.5": total < 1.5,
        "under 2.5": total < 2.5,
        "under 3.5": total < 3.5,
        # BTTS
        "btts": home_goals > 0 and away_goals > 0,
        "ambos marcan": home_goals > 0 and away_goals > 0,
        "btts sí": home_goals > 0 and away_goals > 0,
        "btts no": home_goals == 0 or away_goals == 0,
        "ambos marcan - sí": home_goals > 0 and away_goals > 0,
        "ambos marcan - no": home_goals == 0 or away_goals == 0,
        # Doble oportunidad
        "1x": home_goals >= away_goals,
        "local o empate": home_goals >= away_goals,
        "x2": home_goals <= away_goals,
        "visitante o empate": home_goals <= away_goals,
        "12": home_goals != away_goals,
        "local o visitante": home_goals != away_goals,
    }

    for key, is_win in checks.items():
        if key in pick_lower:
            return "win" if is_win else "loss"

    # Si no se pudo evaluar, no resolver automáticamente
    return None


# ══════════════════════════════════════════════════════════════════
# ODDS SNAPSHOT (cada 3 horas, para line movement)
# ══════════════════════════════════════════════════════════════════

# Mapeo de liga → sport_key de The Odds API
_LEAGUE_SPORT_KEYS = {
    39: "soccer_epl",
    140: "soccer_spain_la_liga",
    135: "soccer_italy_serie_a",
    78: "soccer_germany_bundesliga",
    61: "soccer_france_ligue_one",
    2: "soccer_uefa_champs_league",
    3: "soccer_uefa_europa_league",
    262: "soccer_mexico_ligamx",
    253: "soccer_usa_mls",
}


async def periodic_odds_snapshot(bot):
    """Guarda snapshot de odds para TODOS los partidos próximos.

    Esto permite detectar line movement (movimiento de cuotas)
    cuando se analiza un partido: si las odds bajan, el dinero
    inteligente entró por ese lado.

    Se ejecuta cada 3 horas. Usa ~5-8 requests de The Odds API por ejecución.
    """
    if not ODDS_API_KEY:
        return

    from src.services.odds_service import get_upcoming_games

    total_saved = 0

    for league_id, sport_key in _LEAGUE_SPORT_KEYS.items():
        try:
            games = await get_upcoming_games(sport_key, limit=15, markets="h2h,totals")
            if not games:
                continue

            for game in games:
                home = game.get("home_team", "")
                away = game.get("away_team", "")
                match_name = f"{home} vs {away}"

                # Extraer odds del primer bookmaker disponible
                odds = _extract_best_odds(game)
                if odds:
                    await save_odds_snapshot(match_name, league_id, odds)
                    total_saved += 1

        except Exception as e:
            logger.warning(f"Odds snapshot error para {sport_key}: {e}")

    if total_saved > 0:
        logger.info(f"Odds snapshot: {total_saved} partidos guardados")


def _extract_best_odds(game: dict) -> dict | None:
    """Extrae las mejores odds promedio de los bookmakers disponibles."""
    bookmakers = game.get("bookmakers", [])
    if not bookmakers:
        return None

    odds = {}

    for bm in bookmakers:
        for market in bm.get("markets", []):
            key = market.get("key")
            outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}

            if key == "h2h":
                home_team = game.get("home_team", "")
                away_team = game.get("away_team", "")
                if home_team in outcomes:
                    odds.setdefault("home_all", []).append(outcomes[home_team])
                if away_team in outcomes:
                    odds.setdefault("away_all", []).append(outcomes[away_team])
                if "Draw" in outcomes:
                    odds.setdefault("draw_all", []).append(outcomes["Draw"])
            elif key == "totals":
                if "Over" in outcomes:
                    odds.setdefault("over25_all", []).append(outcomes["Over"])
                if "Under" in outcomes:
                    odds.setdefault("under25_all", []).append(outcomes["Under"])

    if not odds.get("home_all"):
        return None

    return {
        "home": sum(odds.get("home_all", [0])) / len(odds["home_all"]),
        "draw": sum(odds.get("draw_all", [0])) / max(len(odds.get("draw_all", [1])), 1),
        "away": sum(odds.get("away_all", [0])) / max(len(odds.get("away_all", [1])), 1),
        "over25": sum(odds.get("over25_all", [0])) / max(len(odds.get("over25_all", [1])), 1),
        "under25": sum(odds.get("under25_all", [0])) / max(len(odds.get("under25_all", [1])), 1),
    }


# ══════════════════════════════════════════════════════════════════
# AUTO-CALIBRACIÓN (después de cada auto-resolve, ajusta confianza)
# ══════════════════════════════════════════════════════════════════

async def run_calibration_check(bot):
    """Analiza accuracy del modelo y genera reporte de calibración.

    Compara probabilidades predichas vs resultados reales para cada
    nivel de confianza y mercado. Si detecta desviaciones significativas,
    notifica al admin con recomendaciones.

    Se ejecuta diariamente a las 10:00.
    """
    from src.models.database import get_prediction_accuracy

    accuracy = await get_prediction_accuracy(days=30)

    if accuracy["total"] < 10:
        return  # No hay suficientes datos

    lines = [
        "📊 *REPORTE DE CALIBRACIÓN*",
        f"_Últimos 30 días: {accuracy['total']} predicciones_",
        "",
        f"🎯 Accuracy global: *{accuracy['accuracy']:.1%}*",
        f"💰 Profit: *{accuracy['profit']:+.1f}u* | ROI: *{accuracy['roi']:+.1f}%*",
        "",
    ]

    # Análisis por nivel de confianza
    alerts = []
    by_conf = accuracy.get("by_confidence", {})
    if by_conf:
        lines.append("📋 *POR CONFIANZA:*")
        for conf, data in sorted(by_conf.items(), key=lambda x: x[1]["total"], reverse=True):
            acc = data["accuracy"]
            n = data["total"]
            profit = data["profit"]
            emoji = "✅" if profit > 0 else "❌"
            lines.append(f"  {emoji} {conf}: {acc:.0%} ({n} picks, {profit:+.1f}u)")

            # Alertas de calibración
            if conf == "muy_alta" and acc < 0.55 and n >= 5:
                alerts.append(f"⚠️ 'muy_alta' solo acierta {acc:.0%} — umbral demasiado bajo")
            if conf == "alta" and acc < 0.50 and n >= 5:
                alerts.append(f"⚠️ 'alta' solo acierta {acc:.0%} — revisar edge mínimo")
            if conf == "baja" and acc > 0.60 and n >= 5:
                alerts.append(f"💡 'baja' acierta {acc:.0%} — podría subir de confianza")

    # Análisis por mercado
    by_market = accuracy.get("by_market", {})
    if by_market:
        lines.extend(["", "📋 *POR MERCADO:*"])
        for market, data in sorted(by_market.items(), key=lambda x: x[1]["total"], reverse=True):
            acc = data["accuracy"]
            n = data["total"]
            profit = data["profit"]
            emoji = "✅" if profit > 0 else "❌"
            lines.append(f"  {emoji} {market}: {acc:.0%} ({n}, {profit:+.1f}u)")

            if acc < 0.40 and n >= 5:
                alerts.append(f"⚠️ Mercado '{market}' con {acc:.0%} accuracy — considerar desactivar")

    # Per-market accuracy (learning system)
    per_market = accuracy.get("per_market", {})
    if per_market:
        lines.extend(["", "🧠 *APRENDIZAJE POR MERCADO:*"])
        for mkt, data in per_market.items():
            n = data["total"]
            c = data["correct"]
            acc = c / n if n > 0 else 0
            emoji = "✅" if acc >= 0.55 else ("🟡" if acc >= 0.45 else "❌")
            label = {"1x2": "Resultado 1X2", "over25": "Over/Under 2.5", "btts": "BTTS"}.get(mkt, mkt)
            lines.append(f"  {emoji} {label}: *{acc:.0%}* ({c}/{n} aciertos)")

    # Calibración
    calibration = accuracy.get("calibration", {})
    if calibration:
        lines.extend(["", "📋 *CALIBRACIÓN (predicho vs real):*"])
        for bucket, data in sorted(calibration.items()):
            pred = data["predicted"]
            actual = data["actual"]
            n = data["count"]
            diff = abs(pred - actual)
            emoji = "✅" if diff < 0.05 else ("🟡" if diff < 0.10 else "🔴")
            lines.append(f"  {emoji} {bucket}: pred={pred:.0%} real={actual:.0%} (n={n})")

            if diff > 0.10 and n >= 5:
                direction = "sobreestima" if pred > actual else "subestima"
                alerts.append(f"🔴 Rango {bucket}: modelo {direction} por {diff:.0%}")

    # Alertas y recomendaciones
    if alerts:
        lines.extend(["", "🚨 *ALERTAS:*"])
        for alert in alerts:
            lines.append(f"  {alert}")

    if not alerts:
        lines.extend(["", "✅ *Modelo bien calibrado.*"])

    # Notificar al admin
    if bot and ADMIN_ID:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text="\n".join(lines),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Error enviando calibración: {e}")

    logger.info(f"Calibration check completado: {accuracy['total']} predicciones, {accuracy['accuracy']:.1%} accuracy")
