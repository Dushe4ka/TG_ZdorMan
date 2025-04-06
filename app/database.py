import aiosqlite
from datetime import datetime, timedelta
import logging

DATABASE_FILE = "bot_database.db"

async def init_db():
    """Инициализация базы данных."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        # Таблица пользователей (хранит ID телеграм, ник и язык)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                language TEXT DEFAULT 'en'
            )
        ''')

        # Таблица платежей (связана с users, хранит данные о платеже и TW аккаунте)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,      -- ID пользователя Telegram
                tw_username TEXT NOT NULL,   -- Имя пользователя TradingView
                tx_hash TEXT UNIQUE NOT NULL,-- Хэш транзакции (уникальный)
                amount REAL NOT NULL,        -- Сумма платежа
                purchase_date TEXT NOT NULL, -- Дата покупки (YYYY-MM-DD)
                subscription_end TEXT NOT NULL,-- Дата окончания подписки (YYYY-MM-DD)
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE -- Удаление платежей при удалении пользователя
            )
        ''')

        # Индексы для ускорения запросов
        # Индекс по user_id и tw_username для поиска подписок конкретного пользователя для конкретного аккаунта
        await db.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_tw ON payments(user_id, tw_username)')
        # Индекс по tw_username для админ-панели и поиска
        await db.execute('CREATE INDEX IF NOT EXISTS idx_payments_tw_username ON payments(tw_username)')
         # Индекс по дате окончания для планировщика
        await db.execute('CREATE INDEX IF NOT EXISTS idx_payments_sub_end ON payments(subscription_end)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)')

        # Миграция: проверка и добавление колонки language в users, если ее нет
        try:
            cursor = await db.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in await cursor.fetchall()]
            if 'language' not in columns:
                await db.execute('ALTER TABLE users ADD COLUMN language TEXT DEFAULT \'en\'')
                logging.info("Добавлена колонка 'language' в таблицу 'users'.")
        except Exception as e:
            logging.error(f"Ошибка при проверке/миграции таблицы users: {e}")

        await db.commit()
        logging.info("Инициализация/проверка базы данных завершена.")


async def save_payment(db: aiosqlite.Connection, user_id: int, tw_username: str, tx_hash: str,
                       amount: float, purchase_date: str, subscription_end: str):
    """
    Сохранение записи о платеже. Используется внутри транзакции save_user_and_payment.
    """
    try:
        await db.execute('''
            INSERT INTO payments
            (user_id, tw_username, tx_hash, amount, purchase_date, subscription_end)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, tw_username, tx_hash, amount, purchase_date, subscription_end))
        logging.info(f"Платеж для user_id={user_id}, tw_username='{tw_username}', hash='{tx_hash}' подготовлен к сохранению.")
    except aiosqlite.IntegrityError as e:
        # Обрабатывается в вызывающей функции (confirm_payment в app.py)
        logging.error(f"Ошибка целостности при сохранении платежа (вероятно, дубликат хеша '{tx_hash}'): {e}")
        raise
    except Exception as e:
        logging.exception(f"Неизвестная ошибка при подготовке сохранения платежа: {e}")
        raise

async def get_user_language(user_id: int) -> str:
    """Получает язык пользователя из БД, по умолчанию 'en'."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
            return result[0] if result and result[0] else 'en'

# --- Функции для Админ-панели ---

async def get_distinct_tw_usernames_with_users():
    """
    Получение списка уникальных TW аккаунтов и связанных с ними пользователей Telegram.
    Используется для построения списка в админ-панели.
    Возвращает: список кортежей [(tw_username, tg_user_id, tg_username)]
               отсортированный по tw_username.
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        # Выбираем уникальные tw_username, и для каждого берем user_id и username
        # из ПОСЛЕДНЕГО платежа для этого tw_username (на случай если разные юзеры платили за один TW акк)
        # Хотя логика бота не должна этого допускать, но для надежности запроса.
        query = '''
            SELECT
                p.tw_username,
                p.user_id,
                u.username
            FROM payments p
            LEFT JOIN users u ON p.user_id = u.user_id
            WHERE p.id IN (
                -- Находим ID последнего платежа для каждого tw_username
                SELECT MAX(id) FROM payments GROUP BY tw_username
            )
            ORDER BY p.tw_username COLLATE NOCASE; -- Сортировка без учета регистра
        '''
        async with db.execute(query) as cursor:
            return await cursor.fetchall()

async def get_payments_for_tw_account(tw_username: str):
    """
    Получение ВСЕХ платежей для конкретного аккаунта TradingView.
    Используется для показа деталей и истории в админ-панели.
    Возвращает: список кортежей платежей + telegram username, отсортированный от новых к старым.
    Структура кортежа: (id, user_id, tw_username, tx_hash, amount, purchase_date, subscription_end, tg_username)
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        query = '''
            SELECT
                p.id, p.user_id, p.tw_username, p.tx_hash, p.amount,
                p.purchase_date, p.subscription_end,
                u.username AS tg_username
            FROM payments p
            LEFT JOIN users u ON p.user_id = u.user_id -- Используем LEFT JOIN на случай, если юзер удален
            WHERE p.tw_username = ?
            ORDER BY p.purchase_date DESC, p.id DESC; -- Сортируем по дате, затем по ID
        '''
        async with db.execute(query, (tw_username,)) as cursor:
            return await cursor.fetchall()

# --- Функции для Планировщика Уведомлений ---

async def get_subscriptions_for_notification_check():
    """
    Получает данные о ПОСЛЕДНЕЙ подписке для КАЖДОЙ уникальной пары (user_id, tw_username).
    Возвращает список кортежей:
    (user_id, tw_username, subscription_end, language, tg_username)
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        query = """
            SELECT
                p.user_id,
                p.tw_username,
                p.subscription_end,
                COALESCE(u.language, 'en') as language, -- Язык пользователя, 'en' если не найден
                u.username as tg_username -- Имя пользователя TG для уведомлений админам
            FROM payments p
            JOIN users u ON p.user_id = u.user_id -- JOIN чтобы получить язык и имя пользователя
            WHERE p.id IN (
                -- Находим ID самого последнего платежа для каждой пары (user_id, tw_username)
                SELECT MAX(id)
                FROM payments
                GROUP BY user_id, tw_username
            )
        """
        async with db.execute(query) as cursor:
            return await cursor.fetchall()


# Убраны старые функции get_expiring_subscriptions и get_expired_subscriptions,
# т.к. новая функция get_subscriptions_for_notification_check() дает все данные,
# а логика проверки (истекает/истекла) перенесена в service.py для ясности.

# Убрана функция get_user_active_subscription(user_id), т.к. она больше не используется напрямую
# и логика проверки активности теперь учитывает tw_username.

# Убрана функция get_all_clients(), заменена на get_distinct_tw_usernames_with_users().
# Убрана функция get_client_payments(tw_username), заменена на get_payments_for_tw_account(tw_username).
# Убрана функция save_user(), т.к. пользователь сохраняется вместе с платежом.