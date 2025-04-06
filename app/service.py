import asyncio
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

# Импортируем новую функцию из database.py
try:
    from database import get_subscriptions_for_notification_check, DATABASE_FILE
    import aiosqlite # Нужен для получения TG username в notify_admins...
except ImportError as e:
     logging.error(f"Ошибка импорта из database.py в service.py: {e}")
     exit()

load_dotenv()

ADMIN_IDS_STR = os.getenv("ADMIN_IDS")
ADMIN_IDS = []
if ADMIN_IDS_STR:
    try:
        ADMIN_IDS = list(map(int, ADMIN_IDS_STR.split(",")))
    except ValueError:
        logging.error("Неверный формат ADMIN_IDS в .env")
else:
    logging.warning("ADMIN_IDS не найдены в .env")


# --- Уведомление админов о НОВОМ платеже ---
async def notify_admins_about_new_payment(bot: Bot, user_id: int, tw_username: str, tx_hash: str, amount: float, purchase_date: str, subscription_end: str):
    """Уведомление админов о новом платеже с деталями"""
    try:
        # Получаем telegram username из базы
        tg_username = "не указан"
        try:
            async with aiosqlite.connect(DATABASE_FILE) as db:
               async with db.execute("SELECT username FROM users WHERE user_id = ?", (user_id,)) as cursor:
                   user_info = await cursor.fetchone()
                   if user_info and user_info[0]:
                       tg_username = user_info[0]
        except Exception as db_err:
            logging.error(f"Ошибка получения tg_username для user_id {user_id} при уведомлении админов: {db_err}")

        message = (
            "💰 *Новый платеж!*\n\n"
            f"👤 Telegram ID: `{user_id}`\n"
            f"👤 Telegram: @{tg_username}\n"
            f"👤 TradingView: **{tw_username}**\n" # Выделяем TW username
            f"🔗 Hash: `{tx_hash}`\n"
            f"💵 Сумма: *{amount} USDT*\n"
            f"📅 Дата платежа: {purchase_date}\n"
            f"⏳ Окончание подписки: *{subscription_end}*"
        )

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, message, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления админу {admin_id} о платеже user_id {user_id}: {e}")

    except Exception as e:
        logging.exception(f"Критическая ошибка в notify_admins_about_new_payment для user_id {user_id}: {e}")


# --- Проверка подписок и отправка уведомлений ---
async def check_subscriptions(bot: Bot, texts: dict):
    """
    Проверяет все последние подписки для пар (user_id, tw_username)
    и отправляет уведомления пользователям и админам.
    """
    logging.info("Запуск периодической проверки подписок...")
    current_date = datetime.now().date() # Используем только дату для сравнения
    expiring_count = 0
    expired_count = 0

    try:
        subscriptions_to_check = await get_subscriptions_for_notification_check()
        logging.info(f"Получено {len(subscriptions_to_check)} уникальных последних подписок для проверки.")

        for user_id, tw_username, sub_end_str, lang, tg_username in subscriptions_to_check:
            try:
                sub_end_date = datetime.strptime(sub_end_str, "%Y-%m-%d").date()
            except ValueError:
                logging.error(f"Неверный формат даты '{sub_end_str}' для user_id={user_id}, tw_username='{tw_username}'. Пропуск.")
                continue

            lang = lang or 'en' # Фоллбэк на английский, если язык не указан
            days_until_expiry = (sub_end_date - current_date).days

            # 1. Проверка на скорое окончание (1-3 дня включительно)
            if 0 <= days_until_expiry < 3:
                days_left = days_until_expiry + 1 # Дней осталось (1, 2 или 3)
                try:
                    warning_msg = texts.get(lang, texts['en'])["subscription_warning_for"].format(
                        tw_username=tw_username,
                        days=days_left,
                        date=sub_end_str
                    )
                    await bot.send_message(user_id, warning_msg, parse_mode="Markdown")
                    logging.info(f"Отправлено ПРЕДУПРЕЖДЕНИЕ user_id={user_id} для TW='{tw_username}' (осталось {days_left} дн.)")
                    expiring_count += 1
                except TelegramForbiddenError:
                     logging.warning(f"ПРЕДУПРЕЖДЕНИЕ: Пользователь {user_id} заблокировал бота.")
                except TelegramBadRequest as e:
                     logging.warning(f"ПРЕДУПРЕЖДЕНИЕ: Не удалось отправить сообщение user_id={user_id}. Ошибка: {e}")
                except Exception as e:
                    logging.error(f"ПРЕДУПРЕЖДЕНИЕ: Ошибка отправки user_id={user_id}, TW='{tw_username}': {e}")
                await asyncio.sleep(0.1) # Небольшая пауза

            # 2. Проверка на истечение (дата окончания < сегодня)
            elif days_until_expiry < 0:
                try:
                    # Уведомление пользователю
                    expired_msg = texts.get(lang, texts['en'])["subscription_expired_for"].format(
                        tw_username=tw_username,
                        date=sub_end_str
                    )
                    await bot.send_message(user_id, expired_msg, parse_mode="Markdown")
                    logging.info(f"Отправлено уведомление об ИСТЕЧЕНИИ user_id={user_id} для TW='{tw_username}' (истекла {sub_end_str})")
                    expired_count += 1

                    # Уведомление админам
                    tg_username_display = tg_username if tg_username else "не указан"
                    admin_message = (
                        f"❌ *Подписка истекла!*\n\n"
                        f"👤 Telegram ID: `{user_id}`\n"
                        f"👤 Telegram: @{tg_username_display}\n"
                        f"👤 TradingView: **{tw_username}**\n"
                        f"📅 Истекла: {sub_end_str}"
                        # Можно добавить хеш последнего платежа, если нужно, но это усложнит запрос
                    )
                    for admin_id in ADMIN_IDS:
                        try:
                             await bot.send_message(admin_id, admin_message, parse_mode="Markdown")
                        except Exception as e_admin:
                             logging.error(f"ИСТЕЧЕНИЕ: Ошибка отправки уведомления админу {admin_id} для user {user_id}, TW {tw_username}: {e_admin}")

                except TelegramForbiddenError:
                    logging.warning(f"ИСТЕЧЕНИЕ: Пользователь {user_id} заблокировал бота.")
                    # Уведомить админов, что бот заблокирован
                    tg_username_display = tg_username if tg_username else "не указан"
                    admin_message = (
                        f"❌ *Подписка истекла (БОТ ЗАБЛОКИРОВАН ПОЛЬЗОВАТЕЛЕМ)*\n\n"
                        f"👤 Telegram ID: `{user_id}`\n"
                        f"👤 Telegram: @{tg_username_display}\n"
                        f"👤 TradingView: **{tw_username}**\n"
                        f"📅 Истекла: {sub_end_str}"
                    )
                    for admin_id in ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id, admin_message, parse_mode="Markdown")
                        except Exception as e_admin:
                            logging.error(f"ИСТЕЧЕНИЕ (Бот заблок.): Ошибка отправки админу {admin_id} для user {user_id}, TW {tw_username}: {e_admin}")

                except TelegramBadRequest as e:
                     logging.warning(f"ИСТЕЧЕНИЕ: Не удалось отправить сообщение user_id={user_id}. Ошибка: {e}")
                except Exception as e:
                    logging.error(f"ИСТЕЧЕНИЕ: Ошибка обработки user_id={user_id}, TW='{tw_username}': {e}")
                await asyncio.sleep(0.1) # Пауза

    except Exception as e:
        logging.exception(f"Глобальная ошибка в check_subscriptions при получении или обработке данных: {e}")

    logging.info(f"Проверка подписок завершена. Истекающих: {expiring_count}, Истекших: {expired_count}.")


# --- Запуск планировщика ---
async def start_scheduler(bot: Bot, texts: dict):
    """Запуск периодической проверки подписок (раз в сутки)"""
    logging.info("Планировщик уведомлений о подписках запущен.")
    await asyncio.sleep(20) # Небольшая задержка перед первым запуском после старта бота
    while True:
        start_time = datetime.now()
        try:
            # Передаем словарь texts для локализации уведомлений
            await check_subscriptions(bot, texts)
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            logging.info(f"Проверка подписок выполнена за {duration:.2f} секунд.")

            # Ожидание до начала следующих суток (например, 3 часа ночи)
            # Или просто ожидание 24 часа
            wait_interval_seconds = 86400 # 24 часа
            # wait_interval_seconds = 3600 # 1 час для теста
            # wait_interval_seconds = 60 # 1 минута для теста

            # Расчет времени до следующего запуска (для выравнивания по времени суток)
            # now = datetime.now()
            # next_run = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
            # wait_interval_seconds = (next_run - now).total_seconds()
            # if wait_interval_seconds < 0: # Если 3 часа уже прошли сегодня
            #     next_run = (now + timedelta(days=2)).replace(hour=3, minute=0, second=0, microsecond=0)
            #     wait_interval_seconds = (next_run - now).total_seconds()

            logging.info(f"Следующая проверка подписок через ~{wait_interval_seconds / 3600:.1f} часов.")
            await asyncio.sleep(wait_interval_seconds)

        except TelegramRetryAfter as e:
             retry_seconds = e.retry_after
             logging.warning(f"Планировщик: Превышен лимит запросов Telegram API. Повтор через {retry_seconds} секунд.")
             await asyncio.sleep(retry_seconds)
        except Exception as e:
            logging.exception(f"Критическая ошибка в цикле планировщика: {e}")
            # При серьезной ошибке ждем 1 час перед повторной попыткой
            logging.info("Ожидание 1 час перед следующей попыткой запуска проверки подписок.")
            await asyncio.sleep(3600)