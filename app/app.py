import os
import logging
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from datetime import datetime, timedelta
import asyncio
from aiogram.types import FSInputFile
from service import notify_admins_about_new_payment, start_scheduler
from database import get_all_clients, get_client_payments

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(",")))
TRC20_WALLET = os.getenv("TRC20_WALLET")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Храним выбранный язык
user_languages = {}


# Машина состояний для оплаты
class PaymentState(StatesGroup):
    waiting_for_username = State()
    waiting_for_hash = State()
    waiting_for_confirmation = State()  # Новое состояние для подтверждения


# Тексты на разных языках
TEXTS = {
    "en": {
        "start": "🌎 Choose a language:",
        "welcome": "Welcome to ZdorMan! You can pay for access to the TradingView indicator.",
        "instruction": "📌 Instruction: [Click here](https://t.me/c/2063756053/31)",
        "enter_username": "Enter your TradingView username:",
        "payment_instructions": "Please send 35 USDT (TRC-20 network) to the following address:",
        "save_hash": "Save the transaction hash.",
        "enter_hash": "Please send the transaction hash:",
        "payment_received": "✅ Your payment has been recorded and will be processed soon.",
        "subscription_expired": "Your subscription has expired.",
        "subscription_warning": "Your subscription will expire in {days} days on {date}.",
        "support": f"For assistance, please contact @{ADMIN_USERNAME}",
        "paid_button": "✅ Paid",
        "help_button": "🆘 Help",
        "main_menu": ["📜 Instruction", "💳 Payment", "🆘 Support"],
        "admin_access_denied": "You don't have access to this command.",
        "no_clients": "No clients.",
        "select_client": "Select client:",
        "client_not_found": "Client not found.",
        "confirm_data": "📋 Please check your details:\n\n👤 TradingView username: {username}\n🔗 Transaction hash: {tx_hash}\n\nIs everything correct?",
        "confirm_yes": "✅ All correct",
        "confirm_no": "✏️ Edit"
    },
    "ru": {
        "start": "🌎 Выберите язык:",
        "welcome": "Добро пожаловать в ZdorMan! Здесь вы можете оплатить доступ к индикатору TradingView.",
        "instruction": "📌 Инструкция: [Нажмите здесь](https://t.me/c/2063756053/31)",
        "enter_username": "Введите ваш никнейм TradingView:",
        "payment_instructions": "Пожалуйста, отправьте 35 USDT (сеть TRC-20) на следующий адрес:",
        "save_hash": "Сохраните хэш транзакции.",
        "enter_hash": "Пожалуйста, отправьте хэш транзакции:",
        "payment_received": "✅ Ваш платеж записан и скоро будет обработан.",
        "subscription_expired": "Ваша подписка истекла.",
        "subscription_warning": "Ваша подписка истечет через {days} дня(ей) {date}.",
        "support": f"Для получения помощи, пожалуйста, обратитесь к @{ADMIN_USERNAME}",
        "paid_button": "✅ Оплатил",
        "help_button": "🆘 Помощь",
        "main_menu": ["📜 Инструкция", "💳 Оплата", "🆘 Поддержка"],
        "admin_access_denied": "У вас нет доступа к этой команде.",
        "no_clients": "Клиентов нет.",
        "select_client": "Выберите клиента:",
        "client_not_found": "Клиент не найден.",
        "confirm_data": "📋 Пожалуйста, проверьте введенные данные:\n\n👤 TradingView username: {username}\n🔗 Transaction hash: {tx_hash}\n\nВсе верно?",
        "confirm_yes": "✅ Все верно",
        "confirm_no": "✏️ Изменить"
    },
    "es": {
        "start": "🌎 Elige un idioma:",
        "welcome": "¡Bienvenido a ZdorMan! Aquí puedes pagar el acceso al indicador de TradingView.",
        "instruction": "📌 Instrucción: [Haz clic aquí](https://t.me/c/2063756053/31)",
        "enter_username": "Ingrese su nombre de usuario de TradingView:",
        "payment_instructions": "Por favor, envíe 35 USDT (red TRC-20) a la siguiente dirección:",
        "save_hash": "Guarde el hash de la transacción.",
        "enter_hash": "Por favor, envíe el hash de la transacción:",
        "payment_received": "✅ Su pago ha sido registrado y será procesado pronto.",
        "subscription_expired": "Su suscripción ha expirado.",
        "subscription_warning": "Su suscripción expirará en {days} días el {date}.",
        "support": f"Para asistencia, por favor contacte a @{ADMIN_USERNAME}",
        "paid_button": "✅ Pagado",
        "help_button": "🆘 Soporte",
        "main_menu": ["📜 Instrucción", "💳 Pago", "🆘 Soporte"],
        "admin_access_denied": "No tienes acceso a este comando.",
        "no_clients": "No hay clientes.",
        "select_client": "Seleccione cliente:",
        "client_not_found": "Cliente no encontrado.",
        "confirm_data": "📋 Por favor, verifique sus datos:\n\n👤 Nombre de usuario de TradingView: {username}\n🔗 Hash de transacción: {tx_hash}\n\n¿Todo correcto?",
        "confirm_yes": "✅ Todo correcto",
        "confirm_no": "✏️ Editar"
    }
}

# Кнопки выбора языка
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang_es")
    ]
])


# Основное меню
def main_menu(lang):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TEXTS[lang]["main_menu"][0])],
            [KeyboardButton(text=TEXTS[lang]["main_menu"][1])],
            [KeyboardButton(text=TEXTS[lang]["main_menu"][2])]
        ],
        resize_keyboard=True
    )
    return kb


# Инициализация базы данных
async def init_db():
    async with aiosqlite.connect("bot_database.db") as db:
        # Таблица пользователей (основная информация)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                language TEXT DEFAULT 'en'
            )
        ''')

        # Таблица платежей (история всех транзакций)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tw_username TEXT,
                tx_hash TEXT,
                amount REAL,
                purchase_date TEXT,
                subscription_end TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        await db.commit()


# Сохраняем данные пользователя и платежа
async def save_user_payment(user_id, username, tw_username, tx_hash, purchase_date, subscription_end):
    async with aiosqlite.connect("bot_database.db") as db:
        # Сохраняем/обновляем основную информацию о пользователе
        await db.execute('''
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username
        ''', (user_id, username))

        # Добавляем запись о платеже
        await db.execute('''
            INSERT INTO payments (user_id, tw_username, tx_hash, amount, purchase_date, subscription_end)
            VALUES (?, ?, ?, 35, ?, ?)
        ''', (user_id, tw_username, tx_hash, purchase_date, subscription_end))
        await db.commit()


# Получаем последнюю активную подписку пользователя
async def get_user_active_subscription(user_id):
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT * FROM payments 
            WHERE user_id = ? 
            AND subscription_end >= date('now')
            ORDER BY subscription_end DESC
            LIMIT 1
        ''', (user_id,)) as cursor:
            return await cursor.fetchone()


# Получаем все платежи пользователя
async def get_user_payments(user_id):
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT * FROM payments 
            WHERE user_id = ?
            ORDER BY purchase_date DESC
        ''', (user_id,)) as cursor:
            return await cursor.fetchall()


# Получаем всех пользователей
async def get_all_users():
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute("SELECT tw_username FROM payments GROUP BY tw_username") as cursor:
            return await cursor.fetchall()


# Получаем данные пользователя по никнейму на TradingView
async def get_user_data_by_tw_username(tw_username):
    async with aiosqlite.connect("bot_database.db") as db:
        async with db.execute('''
            SELECT p.*, u.username 
            FROM payments p
            JOIN users u ON p.user_id = u.user_id
            WHERE p.tw_username = ?
            ORDER BY p.subscription_end DESC
            LIMIT 1
        ''', (tw_username,)) as cursor:
            return await cursor.fetchone()


# Уведомление о подписке
async def notify_subscription(user_id, lang):
    subscription = await get_user_active_subscription(user_id)
    if not subscription:
        await bot.send_message(user_id, TEXTS[lang]["subscription_expired"])
        return

    subscription_end = datetime.strptime(subscription[6], "%Y-%m-%d")
    if datetime.now() >= subscription_end:
        await bot.send_message(user_id, TEXTS[lang]["subscription_expired"])
    elif datetime.now() >= subscription_end - timedelta(days=3):
        warning_msg = TEXTS[lang]["subscription_warning"].format(
            days=3,
            date=subscription_end.strftime("%Y-%m-%d")
        )
        await bot.send_message(user_id, warning_msg)


# Планировщик уведомлений
async def scheduler():
    while True:
        async with aiosqlite.connect("bot_database.db") as db:
            async with db.execute("SELECT user_id, language FROM users") as cursor:
                users = await cursor.fetchall()
                for user in users:
                    await notify_subscription(user[0], user[1] if user[1] else "en")
        await asyncio.sleep(86400)  # Проверка каждые 24 часа


# ========== ОБРАБОТЧИКИ КОМАНД ==========

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(TEXTS["en"]["start"], reply_markup=language_keyboard)


@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_languages[callback.from_user.id] = lang

    # Сохраняем язык в базе данных
    async with aiosqlite.connect("bot_database.db") as db:
        await db.execute('''
            INSERT INTO users (user_id, username, language)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            language = excluded.language
        ''', (callback.from_user.id, callback.from_user.username, lang))
        await db.commit()

    await bot.send_message(callback.from_user.id, TEXTS[lang]["welcome"], reply_markup=main_menu(lang))
    await callback.answer()


@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][0],
    TEXTS["ru"]["main_menu"][0],
    TEXTS["es"]["main_menu"][0]
])
async def send_instruction(message: types.Message):
    lang = user_languages.get(message.from_user.id, "en")
    await message.answer(TEXTS[lang]["instruction"], parse_mode="Markdown")


@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][1],
    TEXTS["ru"]["main_menu"][1],
    TEXTS["es"]["main_menu"][1]
])
async def request_username(message: types.Message, state: FSMContext):
    lang = user_languages.get(message.from_user.id, "en")
    await message.answer(TEXTS[lang]["enter_username"])
    await state.set_state(PaymentState.waiting_for_username)


@dp.message(PaymentState.waiting_for_username)
async def request_transaction_hash(message: types.Message, state: FSMContext):
    await state.update_data(username=message.text)
    lang = user_languages.get(message.from_user.id, "en")

    try:
        photo = FSInputFile("media/1.jpg")
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo,
            caption=f"{TEXTS[lang]['payment_instructions']}\n\n`{TRC20_WALLET}`\n\n{TEXTS[lang]['save_hash']}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=TEXTS[lang]["paid_button"], callback_data="paid")],
                [InlineKeyboardButton(text=TEXTS[lang]["help_button"], url=f"https://t.me/{ADMIN_USERNAME}")]
            ]))
    except Exception as e:
        logging.error(f"Error sending photo: {e}")
        await message.answer(f"{TEXTS[lang]['payment_instructions']}\n\n`{TRC20_WALLET}`")

    await state.set_state(PaymentState.waiting_for_hash)


@dp.callback_query(lambda c: c.data == "paid")
async def process_paid(callback: types.CallbackQuery, state: FSMContext):
    lang = user_languages.get(callback.from_user.id, "en")
    await bot.send_message(callback.from_user.id, TEXTS[lang]["enter_hash"])
    await callback.answer()


@dp.message(PaymentState.waiting_for_hash)
async def process_payment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    username = data.get("username")
    tx_hash = message.text
    lang = user_languages.get(message.from_user.id, "en")

    # Сохраняем хэш в состоянии
    await state.update_data(tx_hash=tx_hash)

    # Формируем сообщение с данными для подтверждения
    confirmation_message = TEXTS[lang]["confirm_data"].format(
        username=username,
        tx_hash=tx_hash
    )

    # Клавиатура с кнопками подтверждения
    confirmation_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=TEXTS[lang]["confirm_yes"], callback_data="confirm_yes"),
            InlineKeyboardButton(text=TEXTS[lang]["confirm_no"], callback_data="confirm_no")
        ]
    ])

    await message.answer(confirmation_message, reply_markup=confirmation_keyboard)
    await state.set_state(PaymentState.waiting_for_confirmation)


@dp.callback_query(PaymentState.waiting_for_confirmation, lambda c: c.data == "confirm_yes")
async def confirm_payment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    username = data.get("username")
    tx_hash = data.get("tx_hash")
    lang = user_languages.get(callback.from_user.id, "en")

    purchase_date = datetime.now().strftime("%Y-%m-%d")
    subscription_end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    await save_user_payment(
        user_id=callback.from_user.id,
        username=callback.from_user.username,
        tw_username=username,
        tx_hash=tx_hash,
        purchase_date=purchase_date,
        subscription_end=subscription_end
    )

    await callback.message.edit_text(TEXTS[lang]["payment_received"])
    await state.clear()

    # Уведомление админов
    await notify_admins_about_new_payment(bot, callback.from_user.id, tx_hash)
    await callback.answer()


@dp.callback_query(PaymentState.waiting_for_confirmation, lambda c: c.data == "confirm_no")
async def reject_payment(callback: types.CallbackQuery, state: FSMContext):
    lang = user_languages.get(callback.from_user.id, "en")
    await callback.message.edit_text(TEXTS[lang]["enter_username"])
    await state.set_state(PaymentState.waiting_for_username)
    await callback.answer()


@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][2],
    TEXTS["ru"]["main_menu"][2],
    TEXTS["es"]["main_menu"][2]
])
async def support_handler(message: types.Message):
    lang = user_languages.get(message.from_user.id, "en")
    await message.answer(TEXTS[lang]["support"])


# ========== АДМИН ПАНЕЛЬ ==========

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(
            "Добро пожаловать в админ-панель!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Список клиентов", callback_data="list_clients")]
            ]))
    else:
        await message.answer(TEXTS["ru"]["admin_access_denied"])


@dp.callback_query(lambda c: c.data == "list_clients")
async def list_clients(callback: types.CallbackQuery):
    """Показывает список всех клиентов"""
    clients = await get_all_clients()

    if not clients:
        await callback.message.answer("📭 Список клиентов пуст")
        await callback.answer()
        return

    buttons = []
    for client in clients:
        # client: (user_id, tg_username, tw_username)
        btn_text = f"{client[2]}"  # tw_username
        if client[1]:  # Если есть telegram username
            btn_text += f" (@{client[1]})"

        buttons.append(
            [InlineKeyboardButton(
                text=btn_text,
                callback_data=f"client_{client[2]}"  # Используем tw_username как идентификатор
            )]
        )

    await callback.message.answer(
        "📋 Список клиентов:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("client_"))
async def client_info(callback: types.CallbackQuery):
    """Показывает информацию о клиенте и его платежах"""
    tw_username = callback.data.split("_")[1]
    payments = await get_client_payments(tw_username)

    if not payments:
        await callback.message.answer("⚠️ У клиента нет платежей")
        await callback.answer()
        return

    # Первый платеж (последний по дате)
    last_payment = payments[0]

    # Формируем основную информацию
    message = (
        f"👤 Клиент: {tw_username}\n"
        f"📱 Telegram: @{last_payment[7] if last_payment[7] else 'не указан'}\n"
        f"🔢 ID: {last_payment[1]}\n\n"
        f"💳 Всего платежей: {len(payments)}\n\n"
        "Последний платеж:\n"
        f"🔗 Hash: {last_payment[3]}\n"
        f"💰 Сумма: {last_payment[4]} USDT\n"
        f"📅 Дата: {last_payment[5]}\n"
        f"⏳ Окончание подписки: {last_payment[6]}"
    )

    # Кнопки для просмотра истории платежей
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📜 Показать историю платежей",
            callback_data=f"history_{tw_username}"
        )]
    ])

    await callback.message.answer(message, reply_markup=kb)
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("history_"))
async def payment_history(callback: types.CallbackQuery):
    """Показывает полную историю платежей клиента"""
    tw_username = callback.data.split("_")[1]
    payments = await get_client_payments(tw_username)

    if not payments:
        await callback.message.answer("⚠️ История платежей пуста")
        await callback.answer()
        return

    message = f"📜 История платежей для {tw_username}:\n\n"

    for i, payment in enumerate(payments, 1):
        message += (
            f"➡️ Платеж #{i}\n"
            f"📅 Дата: {payment[5]}\n"
            f"💰 Сумма: {payment[4]} USDT\n"
            f"🔗 Hash: {payment[3]}\n"
            f"⏳ Окончание: {payment[6]}\n\n"
        )

    await callback.message.answer(message)
    await callback.answer()


async def main():
    await init_db()
    # Запускаем планировщик проверки подписок
    asyncio.create_task(start_scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())