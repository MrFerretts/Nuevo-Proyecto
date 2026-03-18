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
