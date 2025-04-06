import os
import logging
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup,
    KeyboardButton, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from datetime import datetime, timedelta
import asyncio
from aiogram.exceptions import TelegramBadRequest

# Импорты из ваших модулей
# Убедитесь, что эти файлы существуют и функции доступны
try:
    from service import notify_admins_about_new_payment, start_scheduler
    from database import (
        init_db as init_database_module,
        save_payment as save_payment_db,
        get_distinct_tw_usernames_with_users, # Новый запрос для админки
        get_payments_for_tw_account,         # Новый/переименованный запрос
        get_user_language # Функция для получения языка
    )
except ImportError as e:
    logging.error(f"Ошибка импорта: {e}. Убедитесь, что файлы database.py и service.py существуют и содержат нужные функции.")
    # Завершаем работу, если критические модули не найдены
    exit()


load_dotenv()

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS")
TRC20_WALLET = os.getenv("TRC20_WALLET")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
DATABASE_FILE = "bot_database.db" # Определим имя файла БД здесь

# Проверка наличия обязательных переменных окружения
if not all([TOKEN, ADMIN_IDS_STR, TRC20_WALLET, ADMIN_USERNAME]):
    logging.error("Одна или несколько переменных окружения (BOT_TOKEN, ADMIN_IDS, TRC20_WALLET, ADMIN_USERNAME) не установлены.")
    exit()

try:
    ADMIN_IDS = list(map(int, ADMIN_IDS_STR.split(",")))
except ValueError:
    logging.error("Переменная ADMIN_IDS должна содержать числа, разделенные запятыми.")
    exit()

# --- Инициализация Aiogram ---
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Храним выбранный язык пользователя в памяти (для быстрого доступа)
user_languages = {}

# --- Тарифные планы ---
PLANS = {
    "1mo": {"price": 58, "days": 30, "name_en": "1 Month", "name_ru": "1 Месяц", "name_es": "1 Mes"},
    "3mo": {"price": 148, "days": 90, "name_en": "3 Months", "name_ru": "3 Месяца", "name_es": "3 Meses"},
    "1yr": {"price": 498, "days": 365, "name_en": "1 Year", "name_ru": "1 Год", "name_es": "1 Año"},
}

# --- Машина состояний для оплаты ---
class PaymentState(StatesGroup):
    waiting_for_plan_selection = State()
    waiting_for_tw_username = State()   # Новое состояние для ввода TW username
    waiting_for_hash = State()
    waiting_for_confirmation = State()

# --- Тексты на разных языках (обновлены/добавлены) ---
TEXTS = {
    "en": {
        "start": "🌎 Choose a language:",
        "welcome": "Welcome to ZdorMan! Here you can pay for access to the TradingView indicator.",
        "instruction": "📌 Instruction: [Click here](https://t.me/c/2063756053/31)",
        "choose_plan": "💳 Choose your subscription plan:",
        # Новый текст для запроса TW username
        "enter_tw_username": "Enter the TradingView username for which you want to pay:",
        "payment_instructions": "Please send {amount} USDT (TRC-20 network) to the following address:",
        "save_hash": "Save the transaction hash.",
        "enter_hash": "Please send the transaction hash:",
        "payment_received": "✅ Your payment for TradingView account **{tw_username}** has been recorded and will be processed soon.",
        # Уведомления теперь включают TW username
        "subscription_expired_for": "❌ Your subscription for TradingView account **{tw_username}** has expired on {date}.",
        "subscription_warning_for": "⚠️ Your subscription for TradingView account **{tw_username}** will expire in {days} days on {date}.",
        "generic_subscription_expired": "Your subscription has expired.", # Общее сообщение, если TW не указан
        "generic_subscription_warning": "Your subscription will expire in {days} days on {date}.", # Общее сообщение, если TW не указан
        "support": f"For assistance, please contact @{ADMIN_USERNAME}",
        "paid_button": "✅ Paid",
        "help_button": "🆘 Help",
        "main_menu": ["📜 Instruction", "💳 Payment", "🆘 Support"],
        "admin_access_denied": "You don't have access to this command.",
        "no_tw_accounts": "No TradingView accounts found.", # Изменено с no_clients
        "select_tw_account": "Select TradingView account:", # Изменено с select_client
        "client_not_found": "TradingView account data not found.", # Изменено
        # Обновлен для отображения TW username
        "confirm_data": "📋 Please check your details:\n\n👤 TradingView username: **{tw_username}**\n💰 Plan: {plan_name} ({amount} USDT)\n🔗 Transaction hash: `{tx_hash}`\n\nIs everything correct?",
        "confirm_yes": "✅ All correct",
        "confirm_no": "✏️ Edit",
        "error_occurred": "An error occurred. Please try again later or contact support.",
        "duplicate_hash": "This transaction hash has already been used. If you believe this is an error, please contact support.",
        "admin_panel_title": "Welcome to the Admin Panel!",
        "admin_list_tw_accounts": "📋 List of TradingView Accounts",
        "admin_client_info_title": "👤 Account Info: {tw_username}",
        "admin_no_payments": "⚠️ No payments found for this TradingView account.",
        "admin_associated_tg": "👤 Associated Telegram User",
        "admin_active_subscription": "✅ Active Subscription Until",
        "admin_no_active_subscription": "❌ No Active Subscription",
        "admin_total_payments": "💳 Total Payments",
        "admin_last_payment": "Last Payment Details",
        "admin_payment_hash": "🔗 Hash",
        "admin_payment_amount": "💰 Amount",
        "admin_payment_date": "📅 Date",
        "admin_payment_sub_end": "⏳ Subscription End (this payment)",
        "admin_show_history": "📜 Show Payment History",
        "admin_back_to_list": "⬅️ Back to List",
        "admin_back_to_main": "⬅️ Back to Main Menu",
        "admin_history_title": "📜 Payment History for {tw_username}",
        "admin_payment_entry": "➡️ Payment #{i}\n📅 Date: {date}\n💰 Amount: {amount} USDT\n🔗 Hash: `{hash}`\n⏳ End Date: {end_date}\n👤 Paid by: @{tg_username} (ID: `{user_id}`)\n\n",
         "admin_back_to_account": "⬅️ Back to Account Info",

    },
    "ru": {
        "start": "🌎 Выберите язык:",
        "welcome": "Добро пожаловать в ZdorMan! Здесь вы можете оплатить доступ к индикатору TradingView.",
        "instruction": "📌 Инструкция: [Нажмите здесь](https://t.me/c/2063756053/31)",
        "choose_plan": "💳 Выберите тарифный план:",
        # Новый текст для запроса TW username
        "enter_tw_username": "Введите никнейм TradingView, для которого хотите оплатить:",
        "payment_instructions": "Пожалуйста, отправьте {amount} USDT (сеть TRC-20) на следующий адрес:",
        "save_hash": "Сохраните хэш транзакции.",
        "enter_hash": "Пожалуйста, отправьте хэш транзакции:",
        "payment_received": "✅ Ваш платеж для аккаунта TradingView **{tw_username}** записан и скоро будет обработан.",
        # Уведомления теперь включают TW username
        "subscription_expired_for": "❌ Ваша подписка для аккаунта TradingView **{tw_username}** истекла {date}.",
        "subscription_warning_for": "⚠️ Ваша подписка для аккаунта TradingView **{tw_username}** истечет через {days} дня(ей) {date}.",
        "generic_subscription_expired": "Ваша подписка истекла.",
        "generic_subscription_warning": "Ваша подписка истечет через {days} дня(ей) {date}.",
        "support": f"Для получения помощи, пожалуйста, обратитесь к @{ADMIN_USERNAME}",
        "paid_button": "✅ Оплатил",
        "help_button": "🆘 Помощь",
        "main_menu": ["📜 Инструкция", "💳 Оплата", "🆘 Поддержка"],
        "admin_access_denied": "У вас нет доступа к этой команде.",
        "no_tw_accounts": "Аккаунты TradingView не найдены.", # Изменено
        "select_tw_account": "Выберите аккаунт TradingView:", # Изменено
        "client_not_found": "Данные аккаунта TradingView не найдены.", # Изменено
        # Обновлен для отображения TW username
        "confirm_data": "📋 Пожалуйста, проверьте введенные данные:\n\n👤 Аккаунт TradingView: **{tw_username}**\n💰 План: {plan_name} ({amount} USDT)\n🔗 Transaction hash: `{tx_hash}`\n\nВсе верно?",
        "confirm_yes": "✅ Все верно",
        "confirm_no": "✏️ Изменить",
        "error_occurred": "Произошла ошибка. Пожалуйста, попробуйте позже или обратитесь в поддержку.",
        "duplicate_hash": "Этот хэш транзакции уже был использован. Если вы считаете, что это ошибка, обратитесь в поддержку.",
        "admin_panel_title": "Добро пожаловать в админ-панель!",
        "admin_list_tw_accounts": "📋 Список аккаунтов TradingView",
        "admin_client_info_title": "👤 Инфо об аккаунте: {tw_username}",
        "admin_no_payments": "⚠️ Платежи для этого аккаунта TradingView не найдены.",
        "admin_associated_tg": "👤 Связанный пользователь Telegram",
        "admin_active_subscription": "✅ Активная подписка до",
        "admin_no_active_subscription": "❌ Нет активной подписки",
        "admin_total_payments": "💳 Всего платежей",
        "admin_last_payment": "Детали последнего платежа",
        "admin_payment_hash": "🔗 Hash",
        "admin_payment_amount": "💰 Сумма",
        "admin_payment_date": "📅 Дата",
        "admin_payment_sub_end": "⏳ Окончание подписки (этот платеж)",
        "admin_show_history": "📜 Показать историю платежей",
        "admin_back_to_list": "⬅️ Назад к списку",
        "admin_back_to_main": "⬅️ Назад в главное меню",
        "admin_history_title": "📜 История платежей для {tw_username}",
        "admin_payment_entry": "➡️ Платеж #{i}\n📅 Дата: {date}\n💰 Сумма: {amount} USDT\n🔗 Hash: `{hash}`\n⏳ Окончание: {end_date}\n👤 Оплатил: @{tg_username} (ID: `{user_id}`)\n\n",
        "admin_back_to_account": "⬅️ Назад к инфо об аккаунте",
    },
    "es": {
        "start": "🌎 Elige un idioma:",
        "welcome": "¡Bienvenido a ZdorMan! Aquí puedes pagar el acceso al indicador de TradingView.",
        "instruction": "📌 Instrucción: [Haz clic aquí](https://t.me/c/2063756053/31)",
        "choose_plan": "💳 Elige tu plan de suscripción:",
         # Новый текст для запроса TW username
        "enter_tw_username": "Introduce el nombre de usuario de TradingView para el que deseas pagar:",
        "payment_instructions": "Por favor, envíe {amount} USDT (red TRC-20) a la siguiente dirección:",
        "save_hash": "Guarde el hash de la transacción.",
        "enter_hash": "Por favor, envíe el hash de la transacción:",
        "payment_received": "✅ Tu pago para la cuenta de TradingView **{tw_username}** ha sido registrado y será procesado pronto.",
        # Уведомления теперь включают TW username
        "subscription_expired_for": "❌ Tu suscripción para la cuenta de TradingView **{tw_username}** ha expirado el {date}.",
        "subscription_warning_for": "⚠️ Tu suscripción para la cuenta de TradingView **{tw_username}** expirará en {days} días el {date}.",
        "generic_subscription_expired": "Su suscripción ha expirado.",
        "generic_subscription_warning": "Su suscripción expirará en {days} días el {date}.",
        "support": f"Para asistencia, por favor contacte a @{ADMIN_USERNAME}",
        "paid_button": "✅ Pagado",
        "help_button": "🆘 Soporte",
        "main_menu": ["📜 Instrucción", "💳 Pago", "🆘 Soporte"],
        "admin_access_denied": "No tienes acceso a este comando.",
        "no_tw_accounts": "No se encontraron cuentas de TradingView.", # Изменено
        "select_tw_account": "Seleccione la cuenta de TradingView:", # Изменено
        "client_not_found": "Datos de la cuenta de TradingView no encontrados.", # Изменено
        # Обновлен для отображения TW username
        "confirm_data": "📋 Por favor, verifique sus datos:\n\n👤 Cuenta TradingView: **{tw_username}**\n💰 Plan: {plan_name} ({amount} USDT)\n🔗 Hash de transacción: `{tx_hash}`\n\n¿Todo correcto?",
        "confirm_yes": "✅ Todo correcto",
        "confirm_no": "✏️ Editar",
        "error_occurred": "Ocurrió un error. Por favor, inténtelo de nuevo más tarde o contacte con soporte.",
        "duplicate_hash": "Este hash de transacción ya ha sido utilizado. Si cree que esto es un error, por favor contacte con soporte.",
        "admin_panel_title": "¡Bienvenido al Panel de Administración!",
        "admin_list_tw_accounts": "📋 Lista de Cuentas de TradingView",
        "admin_client_info_title": "👤 Info de Cuenta: {tw_username}",
        "admin_no_payments": "⚠️ No se encontraron pagos para esta cuenta de TradingView.",
        "admin_associated_tg": "👤 Usuario de Telegram Asociado",
        "admin_active_subscription": "✅ Suscripción Activa Hasta",
        "admin_no_active_subscription": "❌ Sin Suscripción Activa",
        "admin_total_payments": "💳 Pagos Totales",
        "admin_last_payment": "Detalles del Último Pago",
        "admin_payment_hash": "🔗 Hash",
        "admin_payment_amount": "💰 Cantidad",
        "admin_payment_date": "📅 Fecha",
        "admin_payment_sub_end": "⏳ Fin de Suscripción (este pago)",
        "admin_show_history": "📜 Mostrar Historial de Pagos",
        "admin_back_to_list": "⬅️ Volver a la Lista",
        "admin_back_to_main": "⬅️ Volver al Menú Principal",
        "admin_history_title": "📜 Historial de Pagos para {tw_username}",
        "admin_payment_entry": "➡️ Pago #{i}\n📅 Fecha: {date}\n💰 Cantidad: {amount} USDT\n🔗 Hash: `{hash}`\n⏳ Fin: {end_date}\n👤 Pagado por: @{tg_username} (ID: `{user_id}`)\n\n",
        "admin_back_to_account": "⬅️ Volver a Info de Cuenta",
    }
}

# --- Вспомогательные функции ---
async def get_lang(user_id: int, state: FSMContext = None) -> str:
    """Получает язык пользователя из FSM, кэша или БД."""
    if state:
        data = await state.get_data()
        lang = data.get("language")
        if lang:
            return lang
    lang = user_languages.get(user_id)
    if not lang:
        lang = await get_user_language(user_id) # Запрос к БД
        user_languages[user_id] = lang # Кэшируем
    return lang

def get_text(key: str, lang: str) -> str:
    """Получает текст по ключу и языку, с фоллбэком на английский."""
    return TEXTS.get(lang, TEXTS["en"]).get(key, f"<{key}_NOT_FOUND>")

# --- Клавиатуры ---
language_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang_es")
    ]
])

def main_menu(lang):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_text("main_menu", lang)[0])],
            [KeyboardButton(text=get_text("main_menu", lang)[1])],
            [KeyboardButton(text=get_text("main_menu", lang)[2])]
        ],
        resize_keyboard=True
    )
    return kb

def plans_keyboard(lang):
    buttons = []
    for plan_id, details in PLANS.items():
        plan_name_key = f"name_{lang}"
        button_text = f"{details.get(plan_name_key, details['name_en'])} - {details['price']} USDT"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"plan_{plan_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Сохранение пользователя и платежа ---
async def save_user_and_payment(user_id, username, tw_username, tx_hash, amount, purchase_date, subscription_end, language):
    """Сохраняет пользователя и информацию о конкретном платеже."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        # 1. Сохраняем/обновляем пользователя (включая язык)
        await db.execute('''
            INSERT INTO users (user_id, username, language)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            language = excluded.language
        ''', (user_id, username, language))

        # 2. Сохраняем платеж
        # Используем функцию из database.py, передавая соединение
        await save_payment_db(db, user_id, tw_username, tx_hash, amount, purchase_date, subscription_end)

        # 3. Коммитим транзакцию
        await db.commit()
        logging.info(f"Платеж и пользователь user_id {user_id} для TW {tw_username} успешно сохранены.")

# ========== ОБРАБОТЧИКИ КОМАНД ==========

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear() # Очищаем состояние при старте
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}" # На случай если username пустой

    # Сохраняем/обновляем пользователя при старте, язык пока 'en' по умолчанию
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('''
            INSERT INTO users (user_id, username, language) VALUES (?, ?, 'en')
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        ''', (user_id, username))
        await db.commit()

    # Загружаем язык (если он уже был установлен ранее)
    lang = await get_lang(user_id)
    user_languages[user_id] = lang # Обновляем кэш

    # Предлагаем выбрать язык снова, если нужно
    await message.answer(get_text("start", "en"), reply_markup=language_keyboard)
    # Сразу основное меню не показываем, ждем выбора языка


@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    lang = callback.data.split("_")[1]
    user_languages[user_id] = lang # Обновляем кэш
    await state.update_data(language=lang) # Сохраняем в FSM на всякий случай

    # Сохраняем язык в базе данных
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('UPDATE users SET language = ? WHERE user_id = ?', (lang, user_id))
        await db.commit()

    await bot.send_message(user_id, get_text("welcome", lang), reply_markup=main_menu(lang))
    # Удаляем сообщение с выбором языка
    try:
        await callback.message.delete()
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение выбора языка: {e}")
    await callback.answer()


@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][0], TEXTS["ru"]["main_menu"][0], TEXTS["es"]["main_menu"][0]
])
async def send_instruction(message: types.Message, state: FSMContext):
    lang = await get_lang(message.from_user.id, state)
    await message.answer(get_text("instruction", lang), parse_mode="Markdown", disable_web_page_preview=True)


# --- Процесс Оплаты ---

# 1. Нажатие кнопки "Оплата" -> Предлагаем выбрать план
@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][1], TEXTS["ru"]["main_menu"][1], TEXTS["es"]["main_menu"][1]
])
async def start_payment_process(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_lang(user_id, state)
    await message.answer(get_text("choose_plan", lang), reply_markup=plans_keyboard(lang))
    await state.set_state(PaymentState.waiting_for_plan_selection)

# 2. Выбор плана -> Сохраняем план, спрашиваем TW Username
@dp.callback_query(PaymentState.waiting_for_plan_selection, lambda c: c.data.startswith("plan_"))
async def process_plan_selection(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    plan_id = callback.data.split("_")[1]
    lang = await get_lang(user_id, state)

    if plan_id not in PLANS:
        await callback.answer("Invalid plan selected.", show_alert=True)
        return

    selected_plan = PLANS[plan_id]
    await state.update_data(plan_id=plan_id, amount=selected_plan["price"], days=selected_plan["days"])

    await callback.message.edit_text(get_text("enter_tw_username", lang)) # Запрашиваем TW Username
    await state.set_state(PaymentState.waiting_for_tw_username) # Переходим к ожиданию TW Username
    await callback.answer()

# 3. Ввод TW Username -> Сохраняем TW Username, показываем инструкцию по оплате
@dp.message(PaymentState.waiting_for_tw_username)
async def process_tw_username(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    tw_username = message.text.strip() # Убираем лишние пробелы
    # Можно добавить валидацию формата TW username, если нужно
    if not tw_username:
         lang = await get_lang(user_id, state)
         await message.answer(get_text("enter_tw_username", lang)) # Повторно просим ввести
         return

    await state.update_data(tw_username=tw_username)
    user_data = await state.get_data()
    amount = user_data.get("amount")
    lang = await get_lang(user_id, state)

    instruction_text = get_text("payment_instructions", lang).format(amount=amount)
    caption = f"{instruction_text}\n\n`{TRC20_WALLET}`\n\n{get_text('save_hash', lang)}"
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text("paid_button", lang), callback_data="paid")],
        [InlineKeyboardButton(text=get_text("help_button", lang), url=f"https://t.me/{ADMIN_USERNAME}")]
    ])

    try:
        photo_path = os.path.join("media", "1.jpg")
        if os.path.exists(photo_path):
            photo = FSInputFile(photo_path)
            await bot.send_photo(
                chat_id=user_id, photo=photo, caption=caption,
                parse_mode="Markdown", reply_markup=reply_markup
            )
        else:
             logging.warning(f"Файл не найден: {photo_path}. Отправка текстовой инструкции.")
             await message.answer(caption, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Ошибка при отправке инструкции по оплате для user {user_id}: {e}")
        await message.answer(caption, parse_mode="Markdown", reply_markup=reply_markup) # Отправляем текст в случае ошибки

    await state.set_state(PaymentState.waiting_for_hash) # Переходим к ожиданию хеша

# 4. Нажатие кнопки "Оплатил" -> Просим ввести хеш
@dp.callback_query(PaymentState.waiting_for_hash, lambda c: c.data == "paid")
async def process_paid_button(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    lang = await get_lang(user_id, state)
    try:
        await callback.message.delete() # Удаляем сообщение с кнопкой
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение с кнопкой 'Оплатил': {e}")
    await bot.send_message(user_id, get_text("enter_hash", lang))
    await callback.answer()

# 5. Ввод хеша -> Сохраняем хеш, показываем данные для подтверждения
@dp.message(PaymentState.waiting_for_hash)
async def process_hash(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    tx_hash = message.text.strip()
    if not tx_hash: # Простая проверка на пустой ввод
        lang = await get_lang(user_id, state)
        await message.answer(get_text("enter_hash", lang))
        return

    await state.update_data(tx_hash=tx_hash)
    data = await state.get_data()
    tw_username = data.get("tw_username")
    plan_id = data.get("plan_id")
    amount = data.get("amount")
    lang = await get_lang(user_id, state)

    plan_name_key = f"name_{lang}"
    plan_name = PLANS.get(plan_id, {}).get(plan_name_key, "Unknown Plan")

    confirmation_message = get_text("confirm_data", lang).format(
        tw_username=tw_username,
        plan_name=plan_name,
        amount=amount,
        tx_hash=tx_hash
    )
    confirmation_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=get_text("confirm_yes", lang), callback_data="confirm_yes"),
            InlineKeyboardButton(text=get_text("confirm_no", lang), callback_data="confirm_no")
        ]
    ])

    await message.answer(confirmation_message, reply_markup=confirmation_keyboard, parse_mode="Markdown")
    await state.set_state(PaymentState.waiting_for_confirmation)

# 6. Подтверждение ("Все верно") -> Сохраняем платеж, уведомляем
@dp.callback_query(PaymentState.waiting_for_confirmation, lambda c: c.data == "confirm_yes")
async def confirm_payment(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    username = callback.from_user.username or f"id_{user_id}"
    data = await state.get_data()
    tw_username = data.get("tw_username")
    tx_hash = data.get("tx_hash")
    amount = data.get("amount")
    days = data.get("days")
    lang = await get_lang(user_id, state)

    if not all([tw_username, tx_hash, amount, days]):
         logging.error(f"Ошибка: Недостаточно данных в состоянии для user_id {user_id}. Data: {data}")
         await callback.message.edit_text(get_text("error_occurred", lang))
         await state.clear()
         await callback.answer("Ошибка!", show_alert=True)
         return

    purchase_date = datetime.now().strftime("%Y-%m-%d")
    subscription_end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        await save_user_and_payment(
            user_id=user_id,
            username=username,
            tw_username=tw_username,
            tx_hash=tx_hash,
            amount=amount,
            purchase_date=purchase_date,
            subscription_end=subscription_end,
            language=lang # Передаем язык для сохранения в профиле пользователя
        )

        await callback.message.edit_text(
             get_text("payment_received", lang).format(tw_username=tw_username),
             parse_mode="Markdown"
        )

        # Уведомление админов
        await notify_admins_about_new_payment(
            bot, user_id, tw_username, tx_hash, amount, purchase_date, subscription_end
        )

    except aiosqlite.IntegrityError:
         logging.warning(f"Попытка дубликата платежа с хешем: {tx_hash} от user_id {user_id}")
         await callback.message.edit_text(get_text("duplicate_hash", lang))
    except Exception as e:
        logging.exception(f"Ошибка при сохранении платежа или отправке уведомления для user_id {user_id}: {e}")
        await callback.message.edit_text(get_text("error_occurred", lang))
    finally:
        await state.clear() # Очищаем состояние в любом случае после попытки
        await callback.answer()

# 7. Отклонение ("Изменить") -> Возвращаемся к выбору плана
@dp.callback_query(PaymentState.waiting_for_confirmation, lambda c: c.data == "confirm_no")
async def reject_payment(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    lang = await get_lang(user_id, state)
    # Возвращаемся к самому началу - выбору плана
    await callback.message.edit_text(get_text("choose_plan", lang), reply_markup=plans_keyboard(lang))
    # Данные в state сохранятся, но пользователь пройдет флоу заново
    await state.set_state(PaymentState.waiting_for_plan_selection)
    await callback.answer()


# --- Поддержка ---
@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][2], TEXTS["ru"]["main_menu"][2], TEXTS["es"]["main_menu"][2]
])
async def support_handler(message: types.Message, state: FSMContext):
    lang = await get_lang(message.from_user.id, state)
    await message.answer(get_text("support", lang))

# ========== АДМИН ПАНЕЛЬ (Переработана под TW Аккаунты) ==========

# Вход в админ панель
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    user_id = message.from_user.id
    if user_id in ADMIN_IDS:
        lang = await get_lang(user_id) # Используем язык админа, если он выбирал
        await message.answer(
            get_text("admin_panel_title", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=get_text("admin_list_tw_accounts", lang), callback_data="list_tw_accounts")]
            ]))
    else:
        lang = await get_lang(user_id)
        await message.answer(get_text("admin_access_denied", lang))

# Показ списка аккаунтов TradingView
@dp.callback_query(lambda c: c.data == "list_tw_accounts")
async def list_tw_accounts(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    lang = await get_lang(user_id)

    accounts = await get_distinct_tw_usernames_with_users()
    # accounts: список кортежей [(tw_username, tg_user_id, tg_username)]

    if not accounts:
        await callback.message.edit_text(get_text("no_tw_accounts", lang))
        await callback.answer()
        return

    buttons = []
    for tw_user, tg_id, tg_user in accounts:
        btn_text = f"{tw_user}"
        if tg_user:
            btn_text += f" (@{tg_user})"
        elif tg_id:
             btn_text += f" (ID: {tg_id})"

        buttons.append([InlineKeyboardButton(
            text=btn_text,
            # В callback передаем только tw_username, т.к. он ключ для след. шага
            callback_data=f"client_{tw_user}"
        )])

    buttons.append([InlineKeyboardButton(text=get_text("admin_back_to_main", lang), callback_data="admin_back_to_main")])

    await callback.message.edit_text(
        get_text("select_tw_account", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

# Показ информации о конкретном аккаунте TradingView
@dp.callback_query(lambda c: c.data.startswith("client_"))
async def client_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    lang = await get_lang(user_id)

    tw_username = callback.data.split("_", 1)[1] # Получаем tw_username
    # Получаем все платежи для этого TW аккаунта, отсортированные по дате (новые первые)
    payments = await get_payments_for_tw_account(tw_username)
    # payments: список кортежей [(id, user_id, tw_username, tx_hash, amount, purchase_date, subscription_end, tg_username)]

    if not payments:
        await callback.message.edit_text(get_text("admin_no_payments", lang))
        await callback.answer()
        return

    # Берем данные из самого последнего платежа для основной информации
    last_payment = payments[0]
    tg_user_id = last_payment[1]
    tg_username_display = last_payment[7] if last_payment[7] else f"`{tg_user_id}`" # Отображаем ID если нет ника

    # Определяем статус активной подписки для этого TW аккаунта
    active_sub_end = None
    current_date_str = datetime.now().strftime("%Y-%m-%d")
    # Ищем самую позднюю дату окончания среди всех платежей этого аккаунта
    for p in payments:
        if p[6] >= current_date_str: # p[6] is subscription_end
            if active_sub_end is None or p[6] > active_sub_end:
                 active_sub_end = p[6]

    active_sub_status = f"{get_text('admin_active_subscription', lang)}: *{active_sub_end}*" if active_sub_end \
                       else get_text("admin_no_active_subscription", lang)

    # Формируем сообщение
    message_text = f"{get_text('admin_client_info_title', lang).format(tw_username=tw_username)}\n\n" \
                   f"{get_text('admin_associated_tg', lang)}: @{tg_username_display}\n" \
                   f"{active_sub_status}\n" \
                   f"{get_text('admin_total_payments', lang)}: {len(payments)}\n\n" \
                   f"*{get_text('admin_last_payment', lang)}:*\n" \
                   f"{get_text('admin_payment_hash', lang)}: `{last_payment[3]}`\n" \
                   f"{get_text('admin_payment_amount', lang)}: {last_payment[4]} USDT\n" \
                   f"{get_text('admin_payment_date', lang)}: {last_payment[5]}\n" \
                   f"{get_text('admin_payment_sub_end', lang)}: {last_payment[6]}"

    # Кнопки
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=get_text("admin_show_history", lang),
            callback_data=f"history_{tw_username}"
        )],
        [InlineKeyboardButton(text=get_text("admin_back_to_list", lang), callback_data="list_tw_accounts")]
    ])

    await callback.message.edit_text(message_text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

# Показ истории платежей для конкретного аккаунта TradingView
@dp.callback_query(lambda c: c.data.startswith("history_"))
async def payment_history(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    lang = await get_lang(user_id)

    tw_username = callback.data.split("_", 1)[1]
    payments = await get_payments_for_tw_account(tw_username) # Запрос тот же

    if not payments:
        # Это не должно произойти, если мы пришли с экрана client_info, но на всякий случай
        await callback.message.edit_text(get_text("admin_no_payments", lang))
        await callback.answer()
        return

    message_text = f"{get_text('admin_history_title', lang).format(tw_username=tw_username)}\n\n"

    for i, p in enumerate(payments, 1):
        # p: (id, user_id, tw_username, tx_hash, amount, purchase_date, subscription_end, tg_username)
        tg_user_display = p[7] if p[7] else 'нет'
        message_text += get_text("admin_payment_entry", lang).format(
            i=i,
            date=p[5],
            amount=p[4],
            hash=p[3],
            end_date=p[6],
            tg_username=tg_user_display,
            user_id=p[1]
        )

    # Кнопка Назад к информации об аккаунте
    kb = InlineKeyboardMarkup(inline_keyboard=[
         [InlineKeyboardButton(text=get_text("admin_back_to_account", lang), callback_data=f"client_{tw_username}")]
    ])

    try:
         # Используем edit_text для обновления сообщения
         await callback.message.edit_text(message_text, reply_markup=kb, parse_mode="Markdown")
    except TelegramBadRequest as e:
        # Если сообщение слишком длинное, отправляем новое
        if "message is too long" in str(e):
             logging.warning(f"Сообщение истории для {tw_username} слишком длинное. Отправка новым сообщением.")
             # Отправляем части или просто новое сообщение с клавиатурой
             await callback.message.answer(f"{get_text('admin_history_title', lang).format(tw_username=tw_username)}\n\n" \
                                           f"История слишком длинная для одного сообщения.", reply_markup=kb)
             # Можно добавить логику разделения на страницы, если необходимо
        else:
            logging.error(f"Ошибка при редактировании сообщения истории: {e}")
            await callback.answer(get_text("error_occurred", lang), show_alert=True)

    await callback.answer()

# Кнопка "Назад" в главное меню админки
@dp.callback_query(lambda c: c.data == "admin_back_to_main")
async def admin_back_to_main(callback: types.CallbackQuery):
     user_id = callback.from_user.id
     if user_id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
     lang = await get_lang(user_id)
     # Показываем главное меню админки снова
     await callback.message.edit_text(
            get_text("admin_panel_title", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=get_text("admin_list_tw_accounts", lang), callback_data="list_tw_accounts")]
            ]))
     await callback.answer()

# ========== ЗАПУСК БОТА ==========

async def main():
    # Инициализация БД
    await init_database_module()

    # Загружаем языки пользователей из БД в кэш при старте
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute("SELECT user_id, language FROM users WHERE language IS NOT NULL") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                user_languages[row[0]] = row[1]
            logging.info(f"Загружено {len(user_languages)} языковых настроек пользователей.")

    # Запускаем планировщик проверки подписок
    # Передаем ему словарь TEXTS для локализации уведомлений
    asyncio.create_task(start_scheduler(bot, TEXTS))
    logging.info("Запуск опроса бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.")
    except Exception as e:
         logging.exception(f"Критическая ошибка при запуске бота: {e}")