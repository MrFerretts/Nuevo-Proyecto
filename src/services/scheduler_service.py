import logging
from datetime import datetime, timedelta

from src.config import ADMIN_ID, FOOTBALL_DATA_API_KEY
from src.models.database import (
    get_vip_users, remove_vip,
    get_pending_predictions, resolve_prediction,
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
