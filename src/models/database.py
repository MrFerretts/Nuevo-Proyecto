import aiosqlite
from src.config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                is_vip INTEGER DEFAULT 0,
                vip_expires TEXT,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL,
                match_name TEXT NOT NULL,
                prediction TEXT NOT NULL,
                odds REAL,
                stake INTEGER DEFAULT 1,
                confidence TEXT DEFAULT 'media',
                is_vip INTEGER DEFAULT 0,
                result TEXT DEFAULT 'pending',
                profit REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                months INTEGER DEFAULT 1,
                approved_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_name TEXT NOT NULL,
                league TEXT NOT NULL,
                match_date TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                home_win_prob REAL DEFAULT 0,
                draw_prob REAL DEFAULT 0,
                away_win_prob REAL DEFAULT 0,
                over25_prob REAL DEFAULT 0,
                btts_prob REAL DEFAULT 0,
                home_xg REAL DEFAULT 0,
                away_xg REAL DEFAULT 0,
                predicted_market TEXT,
                predicted_pick TEXT,
                predicted_odds REAL DEFAULT 0,
                predicted_edge REAL DEFAULT 0,
                predicted_confidence TEXT,
                actual_result TEXT,
                actual_home_goals INTEGER,
                actual_away_goals INTEGER,
                was_correct INTEGER,
                profit REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                fd_match_id INTEGER,
                league_id INTEGER DEFAULT 0
            )
        """)
        # Migración: agregar columnas nuevas a tablas existentes
        for col, coltype in [("fd_match_id", "INTEGER"), ("league_id", "INTEGER DEFAULT 0")]:
            try:
                await db.execute(f"ALTER TABLE predictions ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # Ya existe
        await db.commit()


async def add_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name),
        )
        await db.commit()


async def set_vip(user_id: int, expires: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_vip = 1, vip_expires = ? WHERE user_id = ?",
            (expires, user_id),
        )
        await db.commit()


async def remove_vip(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_vip = 0, vip_expires = NULL WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()


async def get_vip_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE is_vip = 1") as cursor:
            return await cursor.fetchall()


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            return await cursor.fetchall()


async def add_tip(sport: str, match_name: str, prediction: str, odds: float,
                  stake: int, confidence: str, is_vip: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO tips (sport, match_name, prediction, odds, stake, confidence, is_vip)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sport, match_name, prediction, odds, stake, confidence, is_vip),
        )
        await db.commit()
        return cursor.lastrowid


async def update_tip_result(tip_id: int, result: str, profit: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tips SET result = ?, profit = ? WHERE id = ?",
            (result, profit, tip_id),
        )
        await db.commit()


async def get_pending_tips():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tips WHERE result = 'pending' ORDER BY created_at DESC") as cursor:
            return await cursor.fetchall()


async def get_stats(days: int = 30):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN result = 'void' THEN 1 ELSE 0 END) as voids,
                SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(profit) as total_profit
               FROM tips
               WHERE created_at >= datetime('now', ? || ' days')""",
            (f"-{days}",),
        ) as cursor:
            return await cursor.fetchone()


async def get_recent_tips(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tips ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cursor:
            return await cursor.fetchall()


async def add_payment(user_id: int, amount: float, months: int, approved_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (user_id, amount, months, approved_by) VALUES (?, ?, ?, ?)",
            (user_id, amount, months, approved_by),
        )
        await db.commit()


async def get_total_revenue():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COALESCE(SUM(amount), 0) FROM payments") as cursor:
            row = await cursor.fetchone()
            return row[0]


# ══════════════════════════════════════════════════════════════════
# TRACKING DE PREDICCIONES
# ══════════════════════════════════════════════════════════════════

async def save_prediction(match_name: str, league: str, match_date: str,
                          home_team: str, away_team: str, probs: dict,
                          suggestion: dict = None,
                          fd_match_id: int = None, league_id: int = 0) -> int:
    """Guarda una predicción para tracking de precisión.

    Args:
        match_name: "Team A vs Team B"
        league: nombre de la liga
        match_date: fecha del partido
        home_team, away_team: nombres
        probs: dict con probabilidades estimadas
        suggestion: dict con la apuesta sugerida (opcional)
        fd_match_id: ID del partido en football-data.org (para auto-resolución)
        league_id: ID interno de la liga

    Returns: ID de la predicción guardada
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO predictions
               (match_name, league, match_date, home_team, away_team,
                home_win_prob, draw_prob, away_win_prob, over25_prob, btts_prob,
                home_xg, away_xg,
                predicted_market, predicted_pick, predicted_odds,
                predicted_edge, predicted_confidence,
                fd_match_id, league_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                match_name, league, match_date, home_team, away_team,
                probs.get("home_win", 0), probs.get("draw", 0), probs.get("away_win", 0),
                probs.get("over25", 0), probs.get("btts", 0),
                probs.get("home_xg", 0), probs.get("away_xg", 0),
                suggestion.get("market", "") if suggestion else "",
                suggestion.get("pick", "") if suggestion else "",
                suggestion.get("odds", 0) if suggestion else 0,
                suggestion.get("edge", 0) if suggestion else 0,
                suggestion.get("confidence", "") if suggestion else "",
                fd_match_id, league_id,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def resolve_prediction(prediction_id: int, home_goals: int, away_goals: int):
    """Resuelve una predicción con el resultado real y calcula si acertó."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Obtener la predicción
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM predictions WHERE id = ?", (prediction_id,)
        ) as cursor:
            pred = await cursor.fetchone()

        if not pred:
            return

        # Determinar resultado real
        if home_goals > away_goals:
            actual_result = "home_win"
        elif home_goals < away_goals:
            actual_result = "away_win"
        else:
            actual_result = "draw"

        total_goals = home_goals + away_goals

        # Verificar si la predicción principal fue correcta
        was_correct = 0
        profit = 0.0
        pick = pred["predicted_pick"]
        odds = pred["predicted_odds"]

        if pick:
            # Evaluar según el tipo de apuesta
            correct_map = {
                "Victoria Local": actual_result == "home_win",
                "Empate": actual_result == "draw",
                "Victoria Visitante": actual_result == "away_win",
                "Over 1.5 Goles": total_goals > 1.5,
                "Under 1.5 Goles": total_goals < 1.5,
                "Over 2.5 Goles": total_goals > 2.5,
                "Under 2.5 Goles": total_goals < 2.5,
                "Over 3.5 Goles": total_goals > 3.5,
                "Under 3.5 Goles": total_goals < 3.5,
                "Ambos Marcan - Sí": home_goals > 0 and away_goals > 0,
                "Ambos Marcan - No": home_goals == 0 or away_goals == 0,
                "Local o Empate (1X)": actual_result in ("home_win", "draw"),
                "Visitante o Empate (X2)": actual_result in ("away_win", "draw"),
                "Local o Visitante (12)": actual_result in ("home_win", "away_win"),
            }
            was_correct = 1 if correct_map.get(pick, False) else 0
            profit = (odds - 1) if was_correct and odds > 0 else (-1.0 if odds > 0 else 0)

        await db.execute(
            """UPDATE predictions SET
               actual_result = ?, actual_home_goals = ?, actual_away_goals = ?,
               was_correct = ?, profit = ?, resolved_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (actual_result, home_goals, away_goals, was_correct, profit, prediction_id),
        )
        await db.commit()


async def get_prediction_accuracy(days: int = 30) -> dict:
    """Obtiene métricas de precisión de las predicciones.

    Returns: dict con métricas de accuracy, profit, calibración, etc.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Predicciones resueltas en los últimos N días
        async with db.execute(
            """SELECT * FROM predictions
               WHERE resolved_at IS NOT NULL
               AND created_at >= datetime('now', ? || ' days')
               ORDER BY created_at DESC""",
            (f"-{days}",),
        ) as cursor:
            predictions = await cursor.fetchall()

        if not predictions:
            return {
                "total": 0, "resolved": 0, "correct": 0,
                "accuracy": 0, "profit": 0, "roi": 0,
                "by_confidence": {}, "by_market": {},
                "calibration": {},
            }

        total = len(predictions)
        correct = sum(1 for p in predictions if p["was_correct"])
        total_profit = sum(p["profit"] for p in predictions if p["profit"] is not None)

        # Accuracy por nivel de confianza
        by_confidence = {}
        for p in predictions:
            conf = p["predicted_confidence"] or "sin_confianza"
            if conf not in by_confidence:
                by_confidence[conf] = {"total": 0, "correct": 0, "profit": 0}
            by_confidence[conf]["total"] += 1
            if p["was_correct"]:
                by_confidence[conf]["correct"] += 1
            by_confidence[conf]["profit"] += p["profit"] or 0

        for conf in by_confidence:
            t = by_confidence[conf]["total"]
            by_confidence[conf]["accuracy"] = by_confidence[conf]["correct"] / t if t else 0

        # Accuracy por tipo de mercado
        by_market = {}
        for p in predictions:
            market = p["predicted_market"] or "sin_mercado"
            if market not in by_market:
                by_market[market] = {"total": 0, "correct": 0, "profit": 0}
            by_market[market]["total"] += 1
            if p["was_correct"]:
                by_market[market]["correct"] += 1
            by_market[market]["profit"] += p["profit"] or 0

        for m in by_market:
            t = by_market[m]["total"]
            by_market[m]["accuracy"] = by_market[m]["correct"] / t if t else 0

        # Calibración: ¿las probabilidades estimadas coinciden con la realidad?
        calibration = {}
        for bucket in [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]:
            bucket_preds = [
                p for p in predictions
                if p["predicted_edge"] is not None
                and bucket[0] <= p["home_win_prob"] < bucket[1]
            ]
            if bucket_preds:
                bucket_label = f"{bucket[0]:.0%}-{bucket[1]:.0%}"
                actual_rate = sum(1 for p in bucket_preds if p["actual_result"] == "home_win") / len(bucket_preds)
                avg_predicted = sum(p["home_win_prob"] for p in bucket_preds) / len(bucket_preds)
                calibration[bucket_label] = {
                    "predicted": avg_predicted,
                    "actual": actual_rate,
                    "count": len(bucket_preds),
                }

        # Pendientes
        async with db.execute(
            "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NULL"
        ) as cursor:
            pending = (await cursor.fetchone())[0]

        return {
            "total": total,
            "pending": pending,
            "resolved": total,
            "correct": correct,
            "accuracy": correct / total if total else 0,
            "profit": total_profit,
            "roi": total_profit / total * 100 if total else 0,
            "by_confidence": by_confidence,
            "by_market": by_market,
            "calibration": calibration,
        }


async def get_pending_predictions(limit: int = 20) -> list:
    """Obtiene predicciones pendientes de resolver."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM predictions
               WHERE resolved_at IS NULL
               ORDER BY match_date ASC LIMIT ?""",
            (limit,),
        ) as cursor:
            return await cursor.fetchall()
