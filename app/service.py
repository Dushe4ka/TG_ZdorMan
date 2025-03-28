import asyncio
import logging
import os
from datetime import datetime, timedelta
import aiosqlite
from dotenv import load_dotenv

from database import get_user_active_subscription, get_user_payments, get_expired_subscriptions, \
    get_expiring_subscriptions
from aiogram import Bot

load_dotenv()

ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(",")))


async def notify_admins_about_new_payment(bot: Bot, user_id: int, tx_hash: str):
    """Уведомление админов о новом платеже"""
    try:
        payment = (await get_user_payments(user_id))[0]  # Берем последний платеж
        message = (
            "💰 *Новый платеж!*\n"
            f"👤 ID: `{user_id}`\n"
            f"👤 TW: {payment[2]}\n"
            f"🔗 Hash: `{tx_hash}`\n"
            f"📅 Дата: {payment[5]}\n"
            f"⏳ Окончание: {payment[6]}\n"
            f"💵 Сумма: {payment[4]} USDT"
        )

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, message, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Error sending to admin {admin_id}: {e}")

    except Exception as e:
        logging.error(f"Error in notify_admins_about_new_payment: {e}")


async def check_subscriptions(bot: Bot):
    """Проверка подписок и отправка уведомлений"""
    try:
        # Уведомления за 3 дня до окончания
        expiring = await get_expiring_subscriptions(3)
        for payment in expiring:
            try:
                await bot.send_message(
                    payment[1],  # user_id
                    f"⚠️ Ваша подписка истекает через 3 дня ({payment[6]})\n"
                    f"👤 TW: {payment[2]}\n"
                    f"📅 Дата оплаты: {payment[5]}"
                )
            except Exception as e:
                logging.error(f"Error notifying user {payment[1]}: {e}")

        # Уведомления об истекших подписках
        expired = await get_expired_subscriptions()
        for payment in expired:
            try:
                # Пользователю
                await bot.send_message(
                    payment[1],
                    f"❌ Ваша подписка истекла {payment[6]}\n"
                    f"👤 TW: {payment[2]}\n"
                    f"💳 Последний платеж: {payment[5]}"
                )

                # Админам
                for admin_id in ADMIN_IDS:
                    await bot.send_message(
                        admin_id,
                        f"⚠️ Подписка истекла\n"
                        f"👤 ID: {payment[1]}\n"
                        f"👤 @{payment[7]}\n"
                        f"👤 TW: {payment[2]}\n"
                        f"📅 Была до: {payment[6]}\n"
                        f"🔗 Последний hash: {payment[3]}"
                    )
            except Exception as e:
                logging.error(f"Error processing expired sub {payment[1]}: {e}")

    except Exception as e:
        logging.error(f"Error in check_subscriptions: {e}")


async def start_scheduler(bot: Bot):
    """Запуск периодической проверки подписок"""
    while True:
        try:
            await check_subscriptions(bot)
            await asyncio.sleep(86400)  # Проверка раз в сутки
        except Exception as e:
            logging.error(f"Scheduler error: {e}")
            await asyncio.sleep(3600)  # При ошибке ждем час