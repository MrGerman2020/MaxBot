import aiosqlite
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS appeals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                address TEXT,
                category TEXT,
                text TEXT,
                status TEXT DEFAULT 'new',
                uk_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Подписки и счётчики больше не используются: бот проверяет почту и
        # пересылает ответы, напоминаний о поверке нет. Таблицы оставлены,
        # чтобы ранее сохранённые данные не потерялись.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                address TEXT,
                notify_meters INTEGER DEFAULT 1,
                notify_capital INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS meters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                meter_type TEXT,
                serial_number TEXT,
                next_verified DATE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS protocols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                address TEXT,
                ocr_text TEXT,
                verification_status TEXT DEFAULT 'pending',
                owners_found TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Профиль отправителя: нужны ФИО и адрес для обращения по 59-ФЗ.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                address TEXT,
                email TEXT,
                uk_email TEXT
            )
        """)
        # Журнал попыток доставки по каждому обращению.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appeal_id INTEGER,
                channel TEXT,
                target TEXT,
                status TEXT,
                detail TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Курсор чтения почтового ящика: последний обработанный UID и ошибка.
        # Строка всегда одна (id=1) — это состояние, а не журнал.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inbox_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_uid INTEGER DEFAULT 0,
                last_check TIMESTAMP,
                last_error TEXT,
                processed INTEGER DEFAULT 0
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO inbox_state (id, last_uid) VALUES (1, 0)"
        )
        await _migrate_appeals(db)
        await db.commit()


# Колонки, добавленные к appeals после первой версии схемы.
# Миграция только аддитивная — существующие обращения не теряются.
_APPEAL_MIGRATIONS = {
    "full_name": "TEXT",
    "contact_email": "TEXT",
    "uk_email": "TEXT",
    "delivery_channel": "TEXT",
    "delivery_status": "TEXT DEFAULT 'draft'",
    "delivery_detail": "TEXT",
}


async def _migrate_appeals(db):
    cur = await db.execute("PRAGMA table_info(appeals)")
    existing = {row[1] for row in await cur.fetchall()}
    for column, ddl in _APPEAL_MIGRATIONS.items():
        if column not in existing:
            await db.execute(f"ALTER TABLE appeals ADD COLUMN {column} {ddl}")


async def create_appeal(user_id, address, category, text,
                        full_name=None, contact_email=None, uk_email=None,
                        delivery_status="draft"):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO appeals (user_id, address, category, text, full_name, "
            "contact_email, uk_email, delivery_status) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, address, category, text, full_name, contact_email,
             uk_email, delivery_status),
        )
        await db.commit()
        return cur.lastrowid


async def get_user_appeals(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT id, address, category, text, status, uk_response,
                   created_at, updated_at
            FROM appeals WHERE user_id=? ORDER BY id DESC
        """, (user_id,))
        return await cur.fetchall()


async def save_profile(user_id: int, **fields):
    """Создаёт или обновляет профиль отправителя."""
    allowed = {"full_name", "address", "email", "uk_email"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not fields:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        columns = ", ".join(fields)
        placeholders = ", ".join("?" * len(fields))
        assignments = ", ".join(f"{k}=excluded.{k}" for k in fields)
        await db.execute(
            f"INSERT INTO profiles (user_id, {columns}) "
            f"VALUES (?, {placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {assignments}",
            [user_id, *fields.values()],
        )
        await db.commit()


async def get_profile(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM profiles WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def log_delivery(appeal_id: int, channel: str, target: str,
                       status: str, detail: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO deliveries (appeal_id, channel, target, status, detail) "
            "VALUES (?,?,?,?,?)",
            (appeal_id, channel, target, status, detail),
        )
        await db.commit()


async def set_delivery(appeal_id: int, channel: str, status: str,
                       target: str = "", detail: str = ""):
    """Фиксирует результат доставки в appeals и пишет запись в журнал."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE appeals SET delivery_channel=?, delivery_status=?, "
            "delivery_detail=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (channel, status, detail, appeal_id),
        )
        await db.commit()
    await log_delivery(appeal_id, channel, target, status, detail)


async def get_deliveries(appeal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT channel, target, status, detail, created_at "
            "FROM deliveries WHERE appeal_id=? ORDER BY id",
            (appeal_id,),
        )
        return await cur.fetchall()


async def get_appeal_owned(appeal_id: int, user_id: int):
    """Отдельный запрос — чтобы не отдавать чужое обращение по кнопке."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT id, address, category, text, status, uk_response,
                   created_at, updated_at
            FROM appeals WHERE id=? AND user_id=?
        """, (appeal_id, user_id))
        return await cur.fetchone()


async def get_appeal(appeal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, category, text, status, uk_response, created_at FROM appeals WHERE id=?",
            (appeal_id,)
        )
        return await cur.fetchone()


async def update_appeal_status(appeal_id: int, status: str, uk_response: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE appeals SET status=?, uk_response=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, uk_response, appeal_id)
        )
        await db.commit()


async def save_protocol(user_id: int, address: str, ocr_text: str, owners_found: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO protocols (user_id, address, ocr_text, owners_found) VALUES (?,?,?,?)",
            (user_id, address, ocr_text, owners_found)
        )
        await db.commit()
        return cur.lastrowid


async def update_protocol_status(protocol_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE protocols SET verification_status=? WHERE id=?",
            (status, protocol_id)
        )
        await db.commit()


# ===== ОТВЕТЫ УК ИЗ ПОЧТОВОГО ЯЩИКА =====

async def find_appeal_owner(appeal_id: int):
    """Возвращает (user_id, address, category) обращения для ответа в MAX."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, address, category FROM appeals WHERE id=?",
            (appeal_id,)
        )
        return await cur.fetchone()


async def save_uk_response(appeal_id: int, response: str,
                           status: str = "answered") -> None:
    """Сохраняет ответ УК в карточку обращения."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE appeals SET uk_response=?, status=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (response, status, appeal_id)
        )
        await db.commit()


async def get_appeals_with_replies(user_id: int):
    """Обращения пользователя, на которые уже пришёл ответ."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT id, address, category, uk_response, updated_at
            FROM appeals
            WHERE user_id=? AND uk_response IS NOT NULL AND uk_response != ''
            ORDER BY updated_at DESC
        """, (user_id,))
        return await cur.fetchall()


async def get_inbox_state() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM inbox_state WHERE id=1")
        row = await cur.fetchone()
        return dict(row) if row else {"last_uid": 0, "processed": 0}


async def set_inbox_state(last_uid: int, error: str = None,
                          processed: int = 0) -> None:
    """Фиксирует курсор чтения ящика и результат последней проверки."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO inbox_state (id, last_uid, last_check, last_error, processed) "
            "VALUES (1, ?, CURRENT_TIMESTAMP, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_uid=excluded.last_uid, "
            "last_check=excluded.last_check, last_error=excluded.last_error, "
            "processed=excluded.processed",
            (last_uid, error, processed)
        )
        await db.commit()