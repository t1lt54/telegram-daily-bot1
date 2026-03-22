import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from datetime import date
from datetime import datetime
from datetime import time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton
from telegram import InlineKeyboardMarkup
from telegram import Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DB_PATH = DATA_DIR / "users.db"
DEFAULT_RELEASE_DATE = "2026-11-19"
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_REQUIRED_CHANNEL = "@t1lt54_vov"
DEFAULT_REQUIRED_CHANNEL_URL = "https://t.me/t1lt54_vov"


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                subscribed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()


def add_subscriber(chat_id: int, username: str | None, first_name: str | None) -> bool:
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO subscribers (chat_id, username, first_name)
            VALUES (?, ?, ?)
            """,
            (chat_id, username, first_name),
        )
        connection.commit()
        return cursor.rowcount > 0


def remove_subscriber(chat_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            "DELETE FROM subscribers WHERE chat_id = ?",
            (chat_id,),
        )
        connection.commit()
        return cursor.rowcount > 0


def list_subscribers() -> list[int]:
    with sqlite3.connect(DB_PATH) as connection, closing(
        connection.execute("SELECT chat_id FROM subscribers")
    ) as cursor:
        return [row[0] for row in cursor.fetchall()]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    await subscribe_user_or_prompt(update, context)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return

    removed = remove_subscriber(update.effective_chat.id)
    text = (
        "Ежедневные уведомления отключены."
        if removed
        else "Подписка уже была отключена."
    )
    await update.effective_message.reply_text(text)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return

    subscribers = set(list_subscribers())
    is_active = update.effective_chat.id in subscribers
    text = (
        "Подписка активна. Я пришлю следующее ежедневное сообщение по расписанию."
        if is_active
        else "Подписка не активна. Нажми /start, чтобы включить рассылку."
    )
    await update.effective_message.reply_text(text)


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return

    subscribed, channel_url = await check_required_subscription(
        context, update.effective_user.id
    )
    if not subscribed:
        await update.effective_message.reply_text(
            "Для получения отчёта сначала подпишись на канал:\n"
            f"{channel_url}"
        )
        return

    bot_timezone = ZoneInfo(os.getenv("BOT_TIMEZONE", DEFAULT_TIMEZONE))
    current_date = datetime.now(bot_timezone).date()
    await update.effective_message.reply_text(build_daily_message(current_date))


async def verify_subscription_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.message is None or query.from_user is None:
        return

    await query.answer()
    subscribed, channel_url = await check_required_subscription(context, query.from_user.id)

    if not subscribed:
        await query.edit_message_text(
            "Подписка на канал пока не найдена.\n"
            f"Сначала подпишись: {channel_url}\n\n"
            "Потом снова нажми кнопку «Проверить подписку».",
            reply_markup=build_subscription_keyboard(channel_url),
        )
        return

    added = add_subscriber(
        query.message.chat_id,
        query.from_user.username,
        query.from_user.first_name,
    )
    text = (
        "Подписка подтверждена. Ежедневные уведомления включены."
        if added
        else "Подписка подтверждена. Уведомления уже были включены."
    )
    await query.edit_message_text(text)


def parse_release_date() -> date:
    raw_date = os.getenv("RELEASE_DATE", DEFAULT_RELEASE_DATE)
    return date.fromisoformat(raw_date)


def get_days_word(days: int) -> str:
    remainder_10 = days % 10
    remainder_100 = days % 100

    if remainder_10 == 1 and remainder_100 != 11:
        return "день"
    if remainder_10 in (2, 3, 4) and remainder_100 not in (12, 13, 14):
        return "дня"
    return "дней"


def build_daily_message(now_date: date) -> str:
    release_date = parse_release_date()
    days_left = (release_date - now_date).days

    if days_left > 0:
        return (
            f"На {now_date.strftime('%d.%m.%Y')} до выхода GTA 6 осталось "
            f"{days_left} {get_days_word(days_left)}.\n"
            f"Официальная дата релиза: {release_date.strftime('%d.%m.%Y')}."
        )

    if days_left == 0:
        return (
            f"На {now_date.strftime('%d.%m.%Y')} релиз GTA 6 уже сегодня.\n"
            f"Официальная дата релиза: {release_date.strftime('%d.%m.%Y')}."
        )

    days_after_release = abs(days_left)
    return (
        f"На {now_date.strftime('%d.%m.%Y')} GTA 6 вышла "
        f"{days_after_release} {get_days_word(days_after_release)} назад.\n"
        f"Официальная дата релиза: {release_date.strftime('%d.%m.%Y')}."
    )


async def check_required_subscription(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> tuple[bool, str]:
    required_channel = os.getenv("REQUIRED_CHANNEL", DEFAULT_REQUIRED_CHANNEL)
    channel_url = os.getenv("REQUIRED_CHANNEL_URL", DEFAULT_REQUIRED_CHANNEL_URL)

    try:
        member = await context.bot.get_chat_member(required_channel, user_id)
    except TelegramError as exc:
        logging.warning("Failed to check channel subscription for %s: %s", user_id, exc)
        return False, channel_url

    return member.status in {"creator", "administrator", "member"}, channel_url


def build_subscription_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Подписаться на канал", url=channel_url)],
            [InlineKeyboardButton("Проверить подписку", callback_data="check_subscription")],
        ]
    )


async def subscribe_user_or_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    user = update.effective_user
    subscribed, channel_url = await check_required_subscription(context, user.id)
    if not subscribed:
        await update.effective_message.reply_text(
            "Чтобы пользоваться ботом, сначала подпишись на канал, а потом нажми кнопку проверки.",
            reply_markup=build_subscription_keyboard(channel_url),
        )
        return

    added = add_subscriber(update.effective_chat.id, user.username, user.first_name)
    text = (
        "Привет! Ты подписан на ежедневные уведомления."
        if added
        else "Ты уже подписан на ежедневные уведомления."
    )
    await update.effective_message.reply_text(text)


async def send_daily_message(
    application: Application, chat_ids: Iterable[int], message_text: str
) -> None:
    for chat_id in chat_ids:
        try:
            await application.bot.send_message(chat_id=chat_id, text=message_text)
        except Forbidden:
            logging.warning("Chat %s blocked the bot. Removing subscription.", chat_id)
            remove_subscriber(chat_id)
        except TelegramError as exc:
            logging.exception("Failed to send message to %s: %s", chat_id, exc)
        await asyncio.sleep(0.05)


async def daily_notify(context: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers = list_subscribers()
    if not subscribers:
        logging.info("No subscribers to notify.")
        return

    bot_timezone = ZoneInfo(os.getenv("BOT_TIMEZONE", DEFAULT_TIMEZONE))
    current_date = datetime.now(bot_timezone).date()
    message_text = build_daily_message(current_date)
    logging.info("Sending daily notification to %s subscribers.", len(subscribers))
    await send_daily_message(context.application, subscribers, message_text)


def build_application() -> Application:
    load_dotenv(BASE_DIR / ".env")

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Fill telegram-daily-bot/.env first.")

    bot_timezone = os.getenv("BOT_TIMEZONE", DEFAULT_TIMEZONE)
    send_hour = int(os.getenv("SEND_HOUR", "0"))
    send_minute = int(os.getenv("SEND_MINUTE", "5"))

    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("report", report))
    application.add_handler(CallbackQueryHandler(verify_subscription_callback, pattern="^check_subscription$"))

    application.job_queue.run_daily(
        daily_notify,
        time=time(hour=send_hour, minute=send_minute, tzinfo=ZoneInfo(bot_timezone)),
        name="daily-notify",
    )

    return application


def main() -> None:
    setup_logging()
    init_db()
    application = build_application()
    logging.info("Bot is starting.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
