import os
import logging
import aiosqlite
import json # Для возможной персистентности кеша
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
from aiogram.enums import ChatAction # <-- Импорт для статуса "Отправка документа..."

# Импорты из ваших модулей
try:
    from service import notify_admins_about_new_payment, start_scheduler
    from database import (
        init_db as init_database_module,
        save_payment as save_payment_db,
        get_distinct_tw_usernames_with_users,
        get_payments_for_tw_account,
        get_user_language
    )
except ImportError as e:
    logging.error(f"Ошибка импорта: {e}. Убедитесь, что файлы database.py и service.py существуют и содержат нужные функции.")
    exit()


load_dotenv()

# --- Конфигурация ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS")
TRC20_WALLET = os.getenv("TRC20_WALLET")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
DATABASE_FILE = "bot_database.db"
MEDIA_DIR = "media"
CACHE_FILE = "instruction_cache.json" # Файл для сохранения file_id

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

# --- Кеш для file_id инструкций ---
instruction_file_ids = {}

# Функция для загрузки кеша из файла
def load_cache():
    global instruction_file_ids
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                instruction_file_ids = json.load(f)
            logging.info(f"Кеш file_id инструкций загружен из {CACHE_FILE}.")
        except Exception as e:
            logging.error(f"Не удалось загрузить кеш file_id из {CACHE_FILE}: {e}")
            instruction_file_ids = {} # Начинаем с пустого кеша при ошибке
    else:
        logging.info(f"Файл кеша {CACHE_FILE} не найден, кеш пуст.")
        instruction_file_ids = {}

# Функция для сохранения кеша в файл
def save_cache():
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(instruction_file_ids, f, indent=4)
        # logging.info(f"Кеш file_id инструкций сохранен в {CACHE_FILE}.") # Можно логировать, если нужно
    except Exception as e:
        logging.error(f"Не удалось сохранить кеш file_id в {CACHE_FILE}: {e}")

# Храним выбранный язык пользователя в памяти (для быстрого доступа)
user_languages = {}

# --- Тарифные планы ---
PLANS = {
    "1mo": {"price": 58, "days": 30, "name_en": "1 Month", "name_ru": "1 Месяц", "name_es": "1 Mes"},
    "3mo": {"price": 148, "days": 90, "name_en": "3 Months", "name_ru": "3 Месяца", "name_es": "3 Meses"},
    "1yr": {"price": 498, "days": 365, "name_en": "1 Year", "name_ru": "1 Год", "name_es": "1 Año"},
}

# --- Машина состояний для оплаты ---
# (PaymentState - остается без изменений)
class PaymentState(StatesGroup):
    waiting_for_plan_selection = State()
    waiting_for_tw_username = State()
    waiting_for_hash = State()
    waiting_for_confirmation = State()

# --- Тексты на разных языках ---
# (TEXTS - остается без изменений)
TEXTS = {
    "en": {
        "start": "🌎 Choose a language:",
        "welcome": "Welcome to ZdorMan! Here you can pay for access to the TradingView indicator.",
        "instruction_caption": "📌 Here is the instruction manual:",
        "instruction_error": "⚠️ The instruction file could not be found. Please contact support.",
        "choose_plan": "💳 Choose your subscription plan:",
        "enter_tw_username": "Enter the TradingView username for which you want to pay:",
        "payment_instructions": "Please send {amount} USDT (TRC-20 network) to the following address:",
        "save_hash": "Save the transaction hash.",
        "enter_hash": "Please send the transaction hash:",
        "payment_received": "✅ Your payment for TradingView account **{tw_username}** has been recorded and will be processed soon.",
        "subscription_expired_for": "❌ Your subscription for TradingView account **{tw_username}** has expired on {date}.",
        "subscription_warning_for": "⚠️ Your subscription for TradingView account **{tw_username}** will expire in {days} days on {date}.",
        "generic_subscription_expired": "Your subscription has expired.",
        "generic_subscription_warning": "Your subscription will expire in {days} days on {date}.",
        "support": f"For assistance, please contact @{ADMIN_USERNAME}",
        "paid_button": "✅ Paid",
        "help_button": "🆘 Help",
        "main_menu": ["📜 Instruction", "💳 Payment", "🆘 Support"],
        "admin_access_denied": "You don't have access to this command.",
        "no_tw_accounts": "No TradingView accounts found.",
        "select_tw_account": "Select TradingView account:",
        "client_not_found": "TradingView account data not found.",
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
        "admin_payment_entry": "➡️ Payment #{i}\n📅 Date: {date}\n💰 Amount: {amount} USDT\n🔗 Hash: `{hash}`\n⏳ End Date: {end_date}\n👤 Paid by: @{tg_username} (ID: `{user_id}`)\n",
         "admin_back_to_account": "⬅️ Back to Account Info",

    },
    "ru": {
        "start": "🌎 Выберите язык:",
        "welcome": "Добро пожаловать в ZdorMan! Здесь вы можете оплатить доступ к индикатору TradingView.",
        "instruction_caption": "📌 Вот инструкция:",
        "instruction_error": "⚠️ Не удалось найти файл инструкции. Пожалуйста, обратитесь в поддержку.",
        "choose_plan": "💳 Выберите тарифный план:",
        "enter_tw_username": "Введите никнейм TradingView, для которого хотите оплатить:",
        "payment_instructions": "Пожалуйста, отправьте {amount} USDT (сеть TRC-20) на следующий адрес:",
        "save_hash": "Сохраните хэш транзакции.",
        "enter_hash": "Пожалуйста, отправьте хэш транзакции:",
        "payment_received": "✅ Ваш платеж для аккаунта TradingView **{tw_username}** записан и скоро будет обработан.",
        "subscription_expired_for": "❌ Ваша подписка для аккаунта TradingView **{tw_username}** истекла {date}.",
        "subscription_warning_for": "⚠️ Ваша подписка для аккаунта TradingView **{tw_username}** истечет через {days} дня(ей) {date}.",
        "generic_subscription_expired": "Ваша подписка истекла.",
        "generic_subscription_warning": "Ваша подписка истечет через {days} дня(ей) {date}.",
        "support": f"Для получения помощи, пожалуйста, обратитесь к @{ADMIN_USERNAME}",
        "paid_button": "✅ Оплатил",
        "help_button": "🆘 Помощь",
        "main_menu": ["📜 Инструкция", "💳 Оплата", "🆘 Поддержка"],
        "admin_access_denied": "У вас нет доступа к этой команде.",
        "no_tw_accounts": "Аккаунты TradingView не найдены.",
        "select_tw_account": "Выберите аккаунт TradingView:",
        "client_not_found": "Данные аккаунта TradingView не найдены.",
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
        "admin_payment_entry": "➡️ Платеж #{i}\n📅 Дата: {date}\n💰 Сумма: {amount} USDT\n🔗 Hash: `{hash}`\n⏳ Окончание: {end_date}\n👤 Оплатил: @{tg_username} (ID: `{user_id}`)\n",
        "admin_back_to_account": "⬅️ Назад к инфо об аккаунте",
    },
    "es": {
        "start": "🌎 Elige un idioma:",
        "welcome": "¡Bienvenido a ZdorMan! Aquí puedes pagar el acceso al indicador de TradingView.",
        "instruction_caption": "📌 Aquí tienes el manual de instrucciones:",
        "instruction_error": "⚠️ No se pudo encontrar el archivo de instrucciones. Por favor, contacta con soporte.",
        "choose_plan": "💳 Elige tu plan de suscripción:",
        "enter_tw_username": "Introduce el nombre de usuario de TradingView para el que deseas pagar:",
        "payment_instructions": "Por favor, envíe {amount} USDT (red TRC-20) a la siguiente dirección:",
        "save_hash": "Guarde el hash de la transacción.",
        "enter_hash": "Por favor, envíe el hash de la transacción:",
        "payment_received": "✅ Tu pago para la cuenta de TradingView **{tw_username}** ha sido registrado y será procesado pronto.",
        "subscription_expired_for": "❌ Tu suscripción para la cuenta de TradingView **{tw_username}** ha expirado el {date}.",
        "subscription_warning_for": "⚠️ Tu suscripción para la cuenta de TradingView **{tw_username}** expirará en {days} días el {date}.",
        "generic_subscription_expired": "Su suscripción ha expirado.",
        "generic_subscription_warning": "Su suscripción expirará en {days} días el {date}.",
        "support": f"Para asistencia, por favor contacte a @{ADMIN_USERNAME}",
        "paid_button": "✅ Pagado",
        "help_button": "🆘 Soporte",
        "main_menu": ["📜 Instrucción", "💳 Pago", "🆘 Soporte"],
        "admin_access_denied": "No tienes acceso a este comando.",
        "no_tw_accounts": "No se encontraron cuentas de TradingView.",
        "select_tw_account": "Seleccione la cuenta de TradingView:",
        "client_not_found": "Datos de la cuenta de TradingView no encontrados.",
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
        "admin_payment_entry": "➡️ Pago #{i}\n📅 Fecha: {date}\n💰 Cantidad: {amount} USDT\n🔗 Hash: `{hash}`\n⏳ Fin: {end_date}\n👤 Pagado por: @{tg_username} (ID: `{user_id}`)\n",
        "admin_back_to_account": "⬅️ Volver a Info de Cuenta",
    }
}


# --- Сопоставление языков и файлов инструкций ---
INSTRUCTION_FILES = {
    "ru": "инструкция.pdf",
    "es": "instrucciones.pdf",
    "en": "manual.pdf"
}
DEFAULT_INSTRUCTION_FILE = "manual.pdf" # Файл по умолчанию

# --- Вспомогательные функции ---
# (get_lang, get_text - остаются без изменений)
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
    # Сначала пытаемся получить текст для указанного языка
    lang_texts = TEXTS.get(lang)
    if lang_texts:
        text = lang_texts.get(key)
        if text:
            return text
    # Если для указанного языка текста нет, пробуем английский
    en_texts = TEXTS.get("en", {})
    text = en_texts.get(key)
    if text:
        return text
    # Если и на английском нет, возвращаем заглушку
    return f"<{key}_NOT_FOUND_FOR_LANG_{lang}>"


# --- Клавиатуры ---
# (language_keyboard, main_menu, plans_keyboard - остаются без изменений)
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
# (save_user_and_payment - остается без изменений)
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
        await save_payment_db(db, user_id, tw_username, tx_hash, amount, purchase_date, subscription_end)

        # 3. Коммитим транзакцию
        await db.commit()
        logging.info(f"Платеж и пользователь user_id {user_id} для TW {tw_username} успешно сохранены.")

# ========== ОБРАБОТЧИКИ КОМАНД ==========

# --- /start ---
# (start_cmd - остается без изменений)
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear() # Очищаем состояние при старте
    user_id = message.from_user.id
    username = message.from_user.username or f"id_{user_id}"

    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('''
            INSERT INTO users (user_id, username, language) VALUES (?, ?, 'en')
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        ''', (user_id, username))
        await db.commit()

    lang = await get_lang(user_id)
    user_languages[user_id] = lang

    await message.answer(get_text("start", "en"), reply_markup=language_keyboard)


# --- Выбор языка ---
# (set_language - остается без изменений)
@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    lang = callback.data.split("_")[1]
    user_languages[user_id] = lang
    await state.update_data(language=lang)

    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute('UPDATE users SET language = ? WHERE user_id = ?', (lang, user_id))
        await db.commit()

    await bot.send_message(user_id, get_text("welcome", lang), reply_markup=main_menu(lang))
    try:
        await callback.message.delete()
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение выбора языка: {e}")
    await callback.answer()

# --- Обработчик кнопки "Инструкция" (ИЗМЕНЕН с кешированием file_id) ---
@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][0], TEXTS["ru"]["main_menu"][0], TEXTS["es"]["main_menu"][0]
])
async def send_instruction(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_lang(user_id, state)
    instruction_filename = INSTRUCTION_FILES.get(lang, DEFAULT_INSTRUCTION_FILE)
    caption_text = get_text("instruction_caption", lang)
    error_text = get_text("instruction_error", lang) # Текст при ошибке
    global instruction_file_ids # Указываем, что работаем с глобальным кешем

    cached_file_id = instruction_file_ids.get(instruction_filename)

    if cached_file_id:
        # --- Используем кешированный file_id ---
        logging.info(f"Используем кеш file_id для '{instruction_filename}' (user: {user_id})")
        try:
            await bot.send_document(
                chat_id=user_id,
                document=cached_file_id,
                caption=caption_text
            )
            return # Успешно отправили из кеша
        except TelegramBadRequest as e:
            # Если file_id стал недействительным (редко, но возможно)
            logging.warning(f"Недействительный file_id '{cached_file_id}' для '{instruction_filename}': {e}. Удаляем из кеша.")
            del instruction_file_ids[instruction_filename] # Удаляем неверный ID из кеша
            save_cache() # Сохраняем обновленный кеш без невалидного ID
            # Продолжаем выполнение, чтобы отправить файл заново
        except Exception as e:
             logging.error(f"Ошибка отправки документа по file_id '{cached_file_id}' пользователю {user_id}: {e}")
             await message.answer(get_text("error_occurred", lang))
             return # Прерываем выполнение при другой ошибке

    # --- Отправка файла (если file_id не кеширован или стал недействительным) ---
    file_path = os.path.join(MEDIA_DIR, instruction_filename)
    logging.info(f"Отправка файла '{instruction_filename}' пользователю {user_id} (язык: {lang}), file_id не кеширован или невалиден.")

    if os.path.exists(file_path):
        try:
            # Показываем статус "Отправка документа..."
            await bot.send_chat_action(chat_id=user_id, action=ChatAction.UPLOAD_DOCUMENT)
            # Отправляем файл
            document_to_send = FSInputFile(file_path)
            sent_message = await bot.send_document(
                chat_id=user_id,
                document=document_to_send,
                caption=caption_text
            )
            # Кешируем file_id, если отправка прошла успешно
            if sent_message and sent_message.document:
                new_file_id = sent_message.document.file_id
                instruction_file_ids[instruction_filename] = new_file_id
                save_cache() # Сохраняем обновленный кеш
                logging.info(f"Успешно отправлен и кеширован file_id '{new_file_id}' для '{instruction_filename}'")
            else:
                 logging.warning(f"Не удалось получить file_id после отправки '{instruction_filename}' пользователю {user_id}")

        except Exception as e:
            logging.error(f"Ошибка отправки файла инструкции '{file_path}' пользователю {user_id}: {e}")
            await message.answer(get_text("error_occurred", lang)) # Общая ошибка
    else:
        # Если файл для выбранного языка не найден
        logging.warning(f"Файл инструкции не найден: {file_path} (язык: {lang})")
        # Пытаемся отправить файл по умолчанию (английский)
        default_file_path = os.path.join(MEDIA_DIR, DEFAULT_INSTRUCTION_FILE)
        default_caption = get_text("instruction_caption", 'en')

        # Проверяем, есть ли file_id для файла по умолчанию в кеше
        cached_default_id = instruction_file_ids.get(DEFAULT_INSTRUCTION_FILE)
        if cached_default_id:
             logging.info(f"Отправляем инструкцию по умолчанию '{DEFAULT_INSTRUCTION_FILE}' из кеша пользователю {user_id}.")
             try:
                 await bot.send_document(
                     chat_id=user_id,
                     document=cached_default_id,
                     caption=default_caption
                 )
                 return # Успешно отправили дефолтный файл из кеша
             except TelegramBadRequest as e:
                 logging.warning(f"Недействительный file_id '{cached_default_id}' для default '{DEFAULT_INSTRUCTION_FILE}': {e}. Удаляем из кеша.")
                 if DEFAULT_INSTRUCTION_FILE in instruction_file_ids:
                      del instruction_file_ids[DEFAULT_INSTRUCTION_FILE]
                      save_cache()
                 # Продолжаем, чтобы попробовать отправить дефолтный файл заново
             except Exception as e:
                 logging.error(f"Ошибка отправки default документа по file_id '{cached_default_id}' пользователю {user_id}: {e}")
                 await message.answer(error_text) # Сообщаем об ошибке, если даже кеш не сработал
                 return

        # Если кеша для дефолтного файла нет или он стал невалидным
        if lang != 'en' and os.path.exists(default_file_path):
            logging.info(f"Отправляем инструкцию по умолчанию '{DEFAULT_INSTRUCTION_FILE}' (файл) пользователю {user_id}.")
            try:
                await bot.send_chat_action(chat_id=user_id, action=ChatAction.UPLOAD_DOCUMENT)
                document_to_send = FSInputFile(default_file_path)
                sent_message = await bot.send_document(
                    chat_id=user_id,
                    document=document_to_send,
                    caption=default_caption
                )
                # Кешируем file_id для файла по умолчанию
                if sent_message and sent_message.document:
                    new_file_id = sent_message.document.file_id
                    instruction_file_ids[DEFAULT_INSTRUCTION_FILE] = new_file_id
                    save_cache()
                    logging.info(f"Успешно отправлен и кеширован file_id '{new_file_id}' для default '{DEFAULT_INSTRUCTION_FILE}'")
                else:
                    logging.warning(f"Не удалось получить file_id после отправки default '{DEFAULT_INSTRUCTION_FILE}' пользователю {user_id}")

            except Exception as e:
                 logging.error(f"Ошибка отправки файла инструкции по умолчанию '{default_file_path}' пользователю {user_id}: {e}")
                 await message.answer(error_text) # Сообщаем об ошибке, если и дефолтный не ушел
        else:
            # Если и файл по умолчанию не найден, или язык и так английский
             logging.error(f"Файл инструкции по умолчанию '{DEFAULT_INSTRUCTION_FILE}' также не найден.")
             await message.answer(error_text)


# --- Процесс Оплаты ---
# (start_payment_process, process_plan_selection, process_tw_username,
#  process_paid_button, process_hash, confirm_payment, reject_payment - без изменений)
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
        photo_path = os.path.join(MEDIA_DIR, "1.jpg")
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
# (support_handler - без изменений)
@dp.message(lambda message: message.text in [
    TEXTS["en"]["main_menu"][2], TEXTS["ru"]["main_menu"][2], TEXTS["es"]["main_menu"][2]
])
async def support_handler(message: types.Message, state: FSMContext):
    lang = await get_lang(message.from_user.id, state)
    await message.answer(get_text("support", lang))

# ========== АДМИН ПАНЕЛЬ ==========
# (admin_panel, list_tw_accounts, client_info, payment_history, admin_back_to_main - без изменений)
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
    # Загружаем кеш file_id из файла
    load_cache()

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
    asyncio.create_task(start_scheduler(bot, TEXTS))
    logging.info("Запуск опроса бота...")
    # Регистрируем обработчик для сохранения кеша при выключении
    # dp.shutdown.register(save_cache) # Не работает в asyncio.run? Проще сохранять после каждого добавления.

    try:
        await dp.start_polling(bot)
    finally:
        # Сохраняем кеш при остановке бота (даже при ошибке или KeyboardInterrupt)
        logging.info("Бот останавливается, сохраняем кеш file_id...")
        save_cache()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    if not os.path.exists(MEDIA_DIR):
        os.makedirs(MEDIA_DIR)
        logging.info(f"Создана директория: {MEDIA_DIR}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.")
    except Exception as e:
         logging.exception(f"Критическая ошибка при запуске бота: {e}")