import aiosqlite
import os

DB_PATH = os.getenv("DB_PATH", "searches.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                query TEXT NOT NULL,
                results_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def log_search(user_id: int, username: str | None, full_name: str | None,
                     query: str, results_count: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO searches (user_id, username, full_name, query, results_count) VALUES (?, ?, ?, ?, ?)",
            (user_id, username or "", full_name or "", query, results_count)
        )
        await db.commit()


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute("SELECT COUNT(DISTINCT user_id) FROM searches")
        total_users = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM searches")
        total_searches = (await cur.fetchone())[0]

        cur = await db.execute("""
            SELECT user_id, username, full_name, COUNT(*) as cnt,
                   MAX(created_at) as last_seen
            FROM searches
            GROUP BY user_id
            ORDER BY last_seen DESC
        """)
        users = await cur.fetchall()

        return {
            "total_users": total_users,
            "total_searches": total_searches,
            "users": [dict(r) for r in users],
        }


async def get_user_searches(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT username, full_name FROM searches WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
        row = await cur.fetchone()
        if not row:
            return {}

        cur = await db.execute(
            """SELECT query, results_count, created_at
               FROM searches WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,)
        )
        searches = await cur.fetchall()

        return {
            "user_id": user_id,
            "username": row["username"],
            "full_name": row["full_name"],
            "searches": [dict(r) for r in searches],
        }
