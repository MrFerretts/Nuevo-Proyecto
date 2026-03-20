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
        for col, coltype in [
            ("fd_match_id", "INTEGER"),
            ("league_id", "INTEGER DEFAULT 0"),
            # Per-market correctness tracking (learning system)
            ("result_correct", "INTEGER"),      # 1X2 correct?
            ("over25_correct", "INTEGER"),       # Over 2.5 correct?
            ("btts_correct", "INTEGER"),         # BTTS correct?
            ("actual_total_goals", "INTEGER"),   # Total goals for quick queries
        ]:
            try:
                await db.execute(f"ALTER TABLE predictions ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # Ya existe

        # Tabla de bankroll / apuestas del usuario
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                match_name TEXT NOT NULL,
                league TEXT DEFAULT '',
                pick TEXT NOT NULL,
                odds REAL NOT NULL,
                stake REAL NOT NULL,
                estimated_prob REAL DEFAULT 0,
                edge REAL DEFAULT 0,
                kelly_stake REAL DEFAULT 0,
                result TEXT DEFAULT 'pending',
                profit REAL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_bankroll (
                user_id INTEGER PRIMARY KEY,
                initial_bankroll REAL DEFAULT 1000,
                current_bankroll REAL DEFAULT 1000,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Tabla de historial de odds para tracking de line movement
        await db.execute("""
            CREATE TABLE IF NOT EXISTS odds_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_name TEXT NOT NULL,
                league_id INTEGER DEFAULT 0,
                home_odds REAL DEFAULT 0,
                draw_odds REAL DEFAULT 0,
                away_odds REAL DEFAULT 0,
                over25_odds REAL DEFAULT 0,
                under25_odds REAL DEFAULT 0,
                btts_yes_odds REAL DEFAULT 0,
                snapshot_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.commit()


async def save_odds_snapshot(match_name: str, league_id: int, odds: dict):
    """Guarda una foto de las cuotas para tracking de line movement."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO odds_history
               (match_name, league_id, home_odds, draw_odds, away_odds,
                over25_odds, under25_odds, btts_yes_odds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                match_name, league_id,
                odds.get("home", 0), odds.get("draw", 0), odds.get("away", 0),
                odds.get("over25", 0), odds.get("under25", 0), odds.get("btts_yes", 0),
            ),
        )
        await db.commit()


async def get_odds_history(match_name: str) -> list:
    """Obtiene historial de odds de un partido para detectar line movement."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM odds_history
               WHERE match_name = ?
               ORDER BY snapshot_at ASC""",
            (match_name,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


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
    """Resuelve una predicción evaluando TODOS los mercados (no solo el pick principal).

    Evalúa: resultado 1X2, Over/Under 2.5, BTTS — independientemente del pick sugerido.
    Esto permite al modelo aprender de cada predicción y calibrarse con el tiempo.
    """
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

        # ── Evaluar CADA mercado independientemente ──

        # 1X2: ¿acertó el resultado?
        highest_1x2 = max(
            ("home_win", pred["home_win_prob"]),
            ("draw", pred["draw_prob"]),
            ("away_win", pred["away_win_prob"]),
            key=lambda x: x[1],
        )
        result_correct = 1 if highest_1x2[0] == actual_result else 0

        # Over 2.5: ¿acertó?
        over25_prob = pred["over25_prob"] or 0
        actual_over25 = total_goals > 2.5
        # El modelo predijo over si prob > 50%
        over25_correct = 1 if (over25_prob > 0.5) == actual_over25 else 0

        # BTTS: ¿acertó?
        btts_prob = pred["btts_prob"] or 0
        actual_btts = home_goals > 0 and away_goals > 0
        btts_correct = 1 if (btts_prob > 0.5) == actual_btts else 0

        # Verificar si el pick principal fue correcto (para compatibilidad)
        was_correct = 0
        profit = 0.0
        pick = pred["predicted_pick"]
        odds = pred["predicted_odds"]

        if pick:
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
               actual_total_goals = ?,
               was_correct = ?, profit = ?,
               result_correct = ?, over25_correct = ?, btts_correct = ?,
               resolved_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (actual_result, home_goals, away_goals, total_goals,
             was_correct, profit,
             result_correct, over25_correct, btts_correct,
             prediction_id),
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

        # Per-market accuracy (new learning system)
        per_market = {}
        with_result = [p for p in predictions if p.get("result_correct") is not None]
        if with_result:
            per_market["1x2"] = {
                "total": len(with_result),
                "correct": sum(1 for p in with_result if p["result_correct"]),
            }
        with_over25 = [p for p in predictions if p.get("over25_correct") is not None]
        if with_over25:
            per_market["over25"] = {
                "total": len(with_over25),
                "correct": sum(1 for p in with_over25 if p["over25_correct"]),
            }
        with_btts = [p for p in predictions if p.get("btts_correct") is not None]
        if with_btts:
            per_market["btts"] = {
                "total": len(with_btts),
                "correct": sum(1 for p in with_btts if p["btts_correct"]),
            }

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
            "per_market": per_market,
        }


async def get_per_market_calibration(league_id: int = 0, days: int = 60) -> dict:
    """Calcula el sesgo (bias) del modelo por mercado comparando probabilidades predichas vs resultados reales.

    Esto permite al modelo APRENDER: si sistemáticamente sobreestima Over 2.5,
    el sesgo negativo corregirá futuras predicciones.

    Returns: {
        "1x2": {"total": N, "correct": N, "accuracy": 0.X, "bias": +/-0.XX},
        "over25": {"total": N, "correct": N, "accuracy": 0.X, "avg_predicted": 0.X, "avg_actual": 0.X, "bias": +/-0.XX},
        "btts": {"total": N, "correct": N, "accuracy": 0.X, "avg_predicted": 0.X, "avg_actual": 0.X, "bias": +/-0.XX},
        "xg": {"avg_predicted": X.X, "avg_actual": X.X, "bias": +/-X.X},
        "sample_size": N,
    }
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Filtrar por liga si se especifica, sino todas
        if league_id > 0:
            query = """SELECT * FROM predictions
                       WHERE resolved_at IS NOT NULL
                       AND league_id = ?
                       AND created_at >= datetime('now', ? || ' days')
                       ORDER BY created_at DESC"""
            params = (league_id, f"-{days}")
        else:
            query = """SELECT * FROM predictions
                       WHERE resolved_at IS NOT NULL
                       AND created_at >= datetime('now', ? || ' days')
                       ORDER BY created_at DESC"""
            params = (f"-{days}",)

        async with db.execute(query, params) as cursor:
            predictions = [dict(r) for r in await cursor.fetchall()]

    if not predictions:
        return {"sample_size": 0}

    result = {"sample_size": len(predictions)}

    # ── 1X2 Accuracy ──
    with_result = [p for p in predictions if p.get("result_correct") is not None]
    if with_result:
        correct_1x2 = sum(1 for p in with_result if p["result_correct"])
        result["1x2"] = {
            "total": len(with_result),
            "correct": correct_1x2,
            "accuracy": correct_1x2 / len(with_result),
        }
    else:
        # Fallback: calcular desde probabilidades para predicciones antiguas (sin result_correct)
        calc_1x2 = []
        for p in predictions:
            if p.get("actual_result") and p.get("home_win_prob"):
                best = max(
                    ("home_win", p["home_win_prob"]),
                    ("draw", p["draw_prob"]),
                    ("away_win", p["away_win_prob"]),
                    key=lambda x: x[1],
                )
                calc_1x2.append(1 if best[0] == p["actual_result"] else 0)
        if calc_1x2:
            result["1x2"] = {
                "total": len(calc_1x2),
                "correct": sum(calc_1x2),
                "accuracy": sum(calc_1x2) / len(calc_1x2),
            }

    # ── Over 2.5 Calibration ──
    over25_data = [p for p in predictions if p.get("over25_prob", 0) > 0 and p.get("actual_total_goals") is not None]
    if not over25_data:
        # Fallback: calcular desde goles para predicciones antiguas
        over25_data = [p for p in predictions
                       if p.get("over25_prob", 0) > 0
                       and p.get("actual_home_goals") is not None
                       and p.get("actual_away_goals") is not None]
        for p in over25_data:
            p["actual_total_goals"] = (p["actual_home_goals"] or 0) + (p["actual_away_goals"] or 0)

    if over25_data:
        avg_predicted_over25 = sum(p["over25_prob"] for p in over25_data) / len(over25_data)
        actual_over25_rate = sum(1 for p in over25_data if p["actual_total_goals"] > 2.5) / len(over25_data)
        over25_correct = sum(
            1 for p in over25_data
            if (p["over25_prob"] > 0.5) == (p["actual_total_goals"] > 2.5)
        )
        result["over25"] = {
            "total": len(over25_data),
            "correct": over25_correct,
            "accuracy": over25_correct / len(over25_data),
            "avg_predicted": avg_predicted_over25,
            "avg_actual": actual_over25_rate,
            "bias": avg_predicted_over25 - actual_over25_rate,  # Positivo = sobreestima
        }

    # ── BTTS Calibration ──
    btts_data = [p for p in predictions
                 if p.get("btts_prob", 0) > 0
                 and p.get("actual_home_goals") is not None
                 and p.get("actual_away_goals") is not None]
    if btts_data:
        avg_predicted_btts = sum(p["btts_prob"] for p in btts_data) / len(btts_data)
        actual_btts_rate = sum(
            1 for p in btts_data
            if (p["actual_home_goals"] or 0) > 0 and (p["actual_away_goals"] or 0) > 0
        ) / len(btts_data)
        btts_correct = sum(
            1 for p in btts_data
            if (p["btts_prob"] > 0.5) == ((p["actual_home_goals"] or 0) > 0 and (p["actual_away_goals"] or 0) > 0)
        )
        result["btts"] = {
            "total": len(btts_data),
            "correct": btts_correct,
            "accuracy": btts_correct / len(btts_data),
            "avg_predicted": avg_predicted_btts,
            "avg_actual": actual_btts_rate,
            "bias": avg_predicted_btts - actual_btts_rate,
        }

    # ── xG Calibration ──
    xg_data = [p for p in predictions
                if p.get("home_xg", 0) > 0
                and p.get("actual_total_goals") is not None]
    if not xg_data:
        xg_data = [p for p in predictions
                    if p.get("home_xg", 0) > 0
                    and p.get("actual_home_goals") is not None
                    and p.get("actual_away_goals") is not None]
        for p in xg_data:
            if p.get("actual_total_goals") is None:
                p["actual_total_goals"] = (p["actual_home_goals"] or 0) + (p["actual_away_goals"] or 0)

    if xg_data:
        avg_predicted_xg = sum((p["home_xg"] + p["away_xg"]) for p in xg_data) / len(xg_data)
        avg_actual_goals = sum(p["actual_total_goals"] for p in xg_data) / len(xg_data)
        result["xg"] = {
            "total": len(xg_data),
            "avg_predicted": avg_predicted_xg,
            "avg_actual": avg_actual_goals,
            "bias": avg_predicted_xg - avg_actual_goals,
        }

    return result


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


# ══════════════════════════════════════════════════════════════════
# BANKROLL TRACKER
# ══════════════════════════════════════════════════════════════════

async def get_or_create_bankroll(user_id: int, initial: float = 1000.0) -> dict:
    """Obtiene o crea el bankroll de un usuario."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_bankroll WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            return dict(row)
        await db.execute(
            "INSERT INTO user_bankroll (user_id, initial_bankroll, current_bankroll) VALUES (?, ?, ?)",
            (user_id, initial, initial),
        )
        await db.commit()
        return {"user_id": user_id, "initial_bankroll": initial, "current_bankroll": initial}


async def update_bankroll(user_id: int, amount: float):
    """Suma o resta al bankroll actual."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_bankroll SET current_bankroll = current_bankroll + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (amount, user_id),
        )
        await db.commit()


async def set_bankroll(user_id: int, amount: float):
    """Establece el bankroll a un monto específico."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_bankroll SET current_bankroll = ?, initial_bankroll = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (amount, amount, user_id),
        )
        await db.commit()


async def add_user_bet(user_id: int, match_name: str, league: str, pick: str,
                       odds: float, stake: float, estimated_prob: float = 0,
                       edge: float = 0, kelly_stake: float = 0) -> int:
    """Registra una apuesta del usuario."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO user_bets
               (user_id, match_name, league, pick, odds, stake,
                estimated_prob, edge, kelly_stake)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, match_name, league, pick, odds, stake,
             estimated_prob, edge, kelly_stake),
        )
        # Restar stake del bankroll
        await db.execute(
            "UPDATE user_bankroll SET current_bankroll = current_bankroll - ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (stake, user_id),
        )
        await db.commit()
        return cursor.lastrowid


async def resolve_user_bet(bet_id: int, result: str):
    """Resuelve una apuesta: win, loss, void."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_bets WHERE id = ?", (bet_id,)) as cursor:
            bet = await cursor.fetchone()
        if not bet:
            return None

        if result == "win":
            profit = bet["stake"] * (bet["odds"] - 1)
            returned = bet["stake"] + profit
        elif result == "void":
            profit = 0
            returned = bet["stake"]
        else:  # loss
            profit = -bet["stake"]
            returned = 0

        await db.execute(
            "UPDATE user_bets SET result = ?, profit = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result, profit, bet_id),
        )
        # Devolver al bankroll lo que corresponda
        if returned > 0:
            await db.execute(
                "UPDATE user_bankroll SET current_bankroll = current_bankroll + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (returned, bet["user_id"]),
            )
        await db.commit()
        return {"profit": profit, "returned": returned, "bet": dict(bet)}


async def get_user_bets(user_id: int, limit: int = 20) -> list:
    """Obtiene las últimas apuestas del usuario."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_bets WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_all_user_bets(user_id: int) -> list:
    """Obtiene TODAS las apuestas del usuario (para exportar CSV)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_bets WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_user_pending_bets(user_id: int) -> list:
    """Obtiene apuestas pendientes del usuario."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_bets WHERE user_id = ? AND result = 'pending' ORDER BY created_at DESC",
            (user_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_user_bet_stats(user_id: int) -> dict:
    """Estadísticas completas de apuestas del usuario."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN result = 'void' THEN 1 ELSE 0 END) as voids,
                SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN result != 'pending' THEN profit ELSE 0 END) as total_profit,
                SUM(CASE WHEN result != 'pending' THEN stake ELSE 0 END) as total_staked,
                AVG(CASE WHEN result != 'pending' THEN odds ELSE NULL END) as avg_odds,
                MAX(CASE WHEN result = 'win' THEN profit ELSE 0 END) as best_win,
                MIN(CASE WHEN result = 'loss' THEN profit ELSE 0 END) as worst_loss
            FROM user_bets WHERE user_id = ?""",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {}


async def get_user_bets_timeline(user_id: int) -> list:
    """Obtiene todas las apuestas resueltas en orden cronológico para gráficas."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM user_bets
               WHERE user_id = ? AND result != 'pending'
               ORDER BY resolved_at ASC""",
            (user_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_user_streak(user_id: int) -> dict:
    """Calcula la racha actual y mejor racha del usuario."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT result FROM user_bets
               WHERE user_id = ? AND result IN ('win', 'loss')
               ORDER BY resolved_at DESC""",
            (user_id,),
        ) as cursor:
            results = [dict(r)["result"] for r in await cursor.fetchall()]

    if not results:
        return {"current": 0, "current_type": "none", "best_win": 0, "worst_loss": 0}

    # Racha actual
    current_type = results[0]
    current = 0
    for r in results:
        if r == current_type:
            current += 1
        else:
            break

    # Mejor racha de wins y peor racha de losses
    best_win = 0
    worst_loss = 0
    streak = 0
    prev = None
    for r in reversed(results):
        if r == prev:
            streak += 1
        else:
            streak = 1
            prev = r
        if r == "win" and streak > best_win:
            best_win = streak
        if r == "loss" and streak > worst_loss:
            worst_loss = streak

    return {
        "current": current,
        "current_type": current_type,
        "best_win": best_win,
        "worst_loss": worst_loss,
    }


async def get_user_stats_by_pick(user_id: int) -> list:
    """Estadísticas agrupadas por tipo de apuesta."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT
                pick,
                COUNT(*) as total,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN result != 'pending' THEN profit ELSE 0 END) as profit,
                SUM(CASE WHEN result != 'pending' THEN stake ELSE 0 END) as staked
            FROM user_bets WHERE user_id = ? AND result != 'pending'
            GROUP BY pick ORDER BY profit DESC""",
            (user_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_user_weekly_stats(user_id: int) -> list:
    """P&L agrupado por semana."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT
                strftime('%Y-W%W', resolved_at) as week,
                COUNT(*) as bets,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(profit) as profit,
                SUM(stake) as staked
            FROM user_bets
            WHERE user_id = ? AND result != 'pending' AND resolved_at IS NOT NULL
            GROUP BY week ORDER BY week ASC""",
            (user_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]
