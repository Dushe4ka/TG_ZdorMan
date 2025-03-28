import aiosqlite
from datetime import datetime


async def init_db():
    """Инициализация базы данных с историей платежей"""
    async with aiosqlite.connect("bot_database.db") as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tw_username TEXT,
                tx_hash TEXT UNIQUE,
                amount REAL DEFAULT 35.0,
                purchase_date TEXT,
                subscription_end TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')

        # Индексы для ускорения запросов
        await db.execute('CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_payments_tw ON payments(tw_username)')
        await db.commit()


async def save_user(user_id: int, username: str = None):
    """Сохранение/обновление пользователя"""
    async with aiosqlite.connect("bot_database.db") as db:
        await db.execute('''
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username
        ''', (user_id, username))
        await db.commit()


async def save_payment(user_id: int, tw_username: str, tx_hash: str,
                       purchase_date: str, subscription_end: str):
    """Сохранение платежа (каждый новый платеж добавляется отдельно)"""
    async with aiosqlite.connect("bot_database.db") as db:
        await db.execute('''
            INSERT INTO payments 
            (user_id, tw_username, tx_hash, purchase_date, subscription_end)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, tw_username, tx_hash, purchase_date, subscription_end))
        await db.commit()


async def get_all_clients():
    """Получение списка всех уникальных клиентов"""
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT DISTINCT u.user_id, u.username, p.tw_username
            FROM users u
            JOIN payments p ON u.user_id = p.user_id
            ORDER BY p.subscription_end DESC
        ''') as cursor:
            return await cursor.fetchall()


async def get_client_payments(tw_username: str):
    """Получение всех платежей конкретного клиента"""
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT p.*, u.username
            FROM payments p
            JOIN users u ON p.user_id = u.user_id
            WHERE p.tw_username = ?
            ORDER BY p.purchase_date DESC
        ''', (tw_username,)) as cursor:
            return await cursor.fetchall()


async def get_user_active_subscription(user_id: int):
    """
    Получает активную подписку пользователя (если есть)
    Возвращает:
    - None если нет активной подписки
    - Иначе кортеж с данными последнего платежа
    """
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT * FROM payments 
            WHERE user_id = ? 
            AND subscription_end >= date('now')
            ORDER BY subscription_end DESC
            LIMIT 1
        ''', (user_id,)) as cursor:
            return await cursor.fetchone()

async def get_user_payments(user_id: int):
    """
    Получает все платежи пользователя в хронологическом порядке
    """
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT * FROM payments 
            WHERE user_id = ?
            ORDER BY purchase_date DESC
        ''', (user_id,)) as cursor:
            return await cursor.fetchall()

async def get_expiring_subscriptions(days: int = 3):
    """
    Получает подписки, истекающие в течение указанных дней
    """
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT p.*, u.username 
            FROM payments p
            JOIN users u ON p.user_id = u.user_id
            WHERE p.subscription_end BETWEEN date('now') AND date('now', ? || ' days')
            AND p.id IN (
                SELECT MAX(id) FROM payments GROUP BY user_id
            )
        ''', (str(days),)) as cursor:
            return await cursor.fetchall()


async def get_expired_subscriptions():
    """
    Получает пользователей с истекшими подписками
    (без активных подписок)
    """
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT p.*, u.username 
            FROM payments p
            JOIN users u ON p.user_id = u.user_id
            WHERE p.subscription_end < date('now')
            AND NOT EXISTS (
                SELECT 1 FROM payments p2 
                WHERE p2.user_id = p.user_id 
                AND p2.subscription_end >= date('now')
            )
            AND p.id IN (
                SELECT MAX(id) FROM payments GROUP BY user_id
            )
        ''') as cursor:
            return await cursor.fetchall()