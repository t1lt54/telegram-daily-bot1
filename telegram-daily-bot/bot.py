import asyncio
import csv
import logging
import os
import sqlite3
from contextlib import closing
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import time
from pathlib import Path
from time import monotonic
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
EXPORTS_DIR = DATA_DIR / "exports"
DEFAULT_RELEASE_DATE = "2026-11-19"
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_REQUIRED_CHANNEL = "@t1lt54_vov"
DEFAULT_REQUIRED_CHANNEL_URL = "https://t.me/t1lt54_vov"
MAX_MESSAGE_LENGTH = 4000
USER_COMMAND_COOLDOWN_SECONDS = 2.0
ADMIN_COMMAND_COOLDOWN_SECONDS = 3.0


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
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


def list_subscriber_details() -> list[tuple[int, str | None, str | None, str]]:
    with sqlite3.connect(DB_PATH) as connection, closing(
        connection.execute(
            """
            SELECT chat_id, username, first_name, subscribed_at
            FROM subscribers
            ORDER BY subscribed_at DESC
            """
        )
    ) as cursor:
        return cursor.fetchall()


def get_setting(key: str) -> str | None:
    with sqlite3.connect(DB_PATH) as connection, closing(
        connection.execute("SELECT value FROM settings WHERE key = ?", (key,))
    ) as cursor:
        row = cursor.fetchone()
        return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        connection.commit()


def delete_setting(key: str) -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("DELETE FROM settings WHERE key = ?", (key,))
        connection.commit()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context):
        return

    await subscribe_user_or_prompt(update, context)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None:
        return
    if await is_rate_limited(update, context):
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
    if await is_rate_limited(update, context):
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
    if await is_rate_limited(update, context):
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

    await update.effective_message.reply_text(get_current_report_text())


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return

    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return

    subscribers = list_subscriber_details()
    if not subscribers:
        await update.effective_message.reply_text("Подписчиков пока нет.")
        return

    lines = [f"Подписчики бота: {len(subscribers)}"]
    for index, (chat_id, username, first_name, subscribed_at) in enumerate(
        subscribers, start=1
    ):
        username_text = f"@{username}" if username else "без username"
        first_name_text = first_name or "без имени"
        lines.append(
            f"{index}. {username_text} | {first_name_text} | {chat_id} | {subscribed_at}"
        )

    await send_long_message(update.effective_message.reply_text, "\n".join(lines))


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return

    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Используй так: /broadcast текст сообщения"
        )
        return

    message_text = " ".join(context.args).strip()
    save_pending_broadcast(
        context,
        update.effective_user.id,
        "custom",
        message_text,
    )
    await update.effective_message.reply_text(
        "Подтверди рассылку всем подписчикам.",
        reply_markup=build_broadcast_confirmation_keyboard(update.effective_user.id),
    )


async def broadcast_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return

    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return

    save_pending_broadcast(
        context,
        update.effective_user.id,
        "report",
        get_current_report_text(),
    )
    await update.effective_message.reply_text(
        "Подтверди рассылку текущего отчёта всем подписчикам.",
        reply_markup=build_broadcast_confirmation_keyboard(update.effective_user.id),
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return

    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return

    subscribers = list_subscriber_details()
    total = len(subscribers)
    with_username = sum(1 for _, username, _, _ in subscribers if username)
    without_username = total - with_username

    await update.effective_message.reply_text(
        "Статистика бота:\n"
        f"Всего подписчиков: {total}\n"
        f"С username: {with_username}\n"
        f"Без username: {without_username}"
    )


async def time_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    if await is_rate_limited(update, context):
        return

    bot_timezone = ZoneInfo(os.getenv("BOT_TIMEZONE", DEFAULT_TIMEZONE))
    send_hour = int(os.getenv("SEND_HOUR", "0"))
    send_minute = int(os.getenv("SEND_MINUTE", "5"))
    now = datetime.now(bot_timezone)
    next_run = now.replace(hour=send_hour, minute=send_minute, second=0, microsecond=0)
    if next_run <= now:
        next_run = next_run + timedelta(days=1)

    channel = get_autopost_channel()
    channel_text = channel if channel else "не настроен"

    await update.effective_message.reply_text(
        f"Текущее время: {now.strftime('%d.%m.%Y %H:%M %Z')}\n"
        f"Следующая рассылка: {next_run.strftime('%d.%m.%Y %H:%M %Z')}\n"
        f"Автопост в канал: {channel_text}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context):
        return

    lines = [
        "Доступные команды:",
        "/start - включить ежедневные уведомления",
        "/stop - отключить ежедневные уведомления",
        "/status - проверить статус подписки",
        "/report - получить отчёт сразу",
        "/time - посмотреть текущее время и следующую рассылку",
    ]

    if is_admin(update.effective_user.id):
        lines.extend(
            [
                "",
                "Команды администратора:",
                "/users - список подписчиков",
                "/stats - краткая статистика",
                "/export_txt - выгрузить подписчиков в txt",
                "/export_csv - выгрузить подписчиков в csv",
                "/broadcast текст - ручная рассылка",
                "/broadcast_report - разослать текущий отчёт всем",
                "/setchannel @channel или -100... - включить автопост в канал",
                "/channel - показать текущий канал автопоста",
            ]
        )

    await update.effective_message.reply_text("\n".join(lines))


async def export_txt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return

    subscribers = list_subscriber_details()
    if not subscribers:
        await update.effective_message.reply_text("Подписчиков пока нет.")
        return

    file_path = EXPORTS_DIR / f"subscribers_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    lines = []
    for index, (chat_id, username, first_name, subscribed_at) in enumerate(
        subscribers, start=1
    ):
        lines.append(
            f"{index}. chat_id={chat_id} | username={username or ''} | "
            f"first_name={first_name or ''} | subscribed_at={subscribed_at}"
        )
    file_path.write_text("\n".join(lines), encoding="utf-8")
    await send_file_and_cleanup(update, file_path, "subscribers.txt")


async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return

    subscribers = list_subscriber_details()
    if not subscribers:
        await update.effective_message.reply_text("Подписчиков пока нет.")
        return

    file_path = EXPORTS_DIR / f"subscribers_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    with file_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["chat_id", "username", "first_name", "subscribed_at"])
        writer.writerows(subscribers)
    await send_file_and_cleanup(update, file_path, "subscribers.csv")


async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Используй так: /setchannel @channelusername или /setchannel -100..."
        )
        return

    channel_value = context.args[0].strip()
    try:
        chat = await context.bot.get_chat(channel_value)
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"Не удалось проверить канал: {exc}"
        )
        return

    set_setting("autopost_channel", str(chat.id if str(chat.id).startswith("-100") else channel_value))
    await update.effective_message.reply_text(
        f"Автопост в канал включён.\nКанал: {chat.title or channel_value}\nID: {chat.id}"
    )


async def channel_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    if await is_rate_limited(update, context, admin=True):
        return
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "Эта команда доступна только администратору."
        )
        return

    channel = get_autopost_channel()
    if not channel:
        await update.effective_message.reply_text("Автопост в канал сейчас не настроен.")
        return

    await update.effective_message.reply_text(f"Текущий канал автопоста: {channel}")


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
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=get_current_report_text(),
    )


async def broadcast_confirmation_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.from_user is None or query.message is None:
        return

    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("Эта кнопка доступна только администратору.")
        return

    action, owner_id = parse_broadcast_callback(query.data or "")
    if owner_id != query.from_user.id:
        await query.answer("Подтверждать может только тот админ, кто создал рассылку.", show_alert=True)
        return

    if action == "cancel":
        clear_pending_broadcast(context, owner_id)
        await query.edit_message_text("Рассылка отменена.")
        return

    pending = get_pending_broadcast(context, owner_id)
    if pending is None:
        await query.edit_message_text("Черновик рассылки не найден. Создай рассылку заново.")
        return

    await query.edit_message_text("Рассылка запущена. Подожди немного...")
    sent_count, failed_count = await broadcast_to_subscribers(
        context.application, pending["text"]
    )
    clear_pending_broadcast(context, owner_id)
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            "Рассылка завершена.\n"
            f"Успешно: {sent_count}\n"
            f"Ошибок: {failed_count}"
        ),
    )


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


def get_current_report_text() -> str:
    bot_timezone = ZoneInfo(os.getenv("BOT_TIMEZONE", DEFAULT_TIMEZONE))
    current_date = datetime.now(bot_timezone).date()
    return build_daily_message(current_date)


def get_autopost_channel() -> str | None:
    return get_setting("autopost_channel")


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


def is_admin(user_id: int) -> bool:
    raw_admin_id = os.getenv("ADMIN_ID", "").strip()
    return raw_admin_id.isdigit() and int(raw_admin_id) == user_id


def save_pending_broadcast(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, kind: str, text: str
) -> None:
    pending = context.application.bot_data.setdefault("pending_broadcasts", {})
    pending[user_id] = {
        "kind": kind,
        "text": text,
        "created_at": monotonic(),
    }


def get_pending_broadcast(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> dict | None:
    pending = context.application.bot_data.setdefault("pending_broadcasts", {})
    return pending.get(user_id)


def clear_pending_broadcast(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    pending = context.application.bot_data.setdefault("pending_broadcasts", {})
    pending.pop(user_id, None)


def build_broadcast_confirmation_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Подтвердить", callback_data=f"broadcast:confirm:{user_id}"
                ),
                InlineKeyboardButton(
                    "Отмена", callback_data=f"broadcast:cancel:{user_id}"
                ),
            ]
        ]
    )


def parse_broadcast_callback(data: str) -> tuple[str, int]:
    _, action, user_id = data.split(":")
    return action, int(user_id)


async def is_rate_limited(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin: bool = False,
) -> bool:
    if update.effective_message is None or update.effective_user is None:
        return False

    command_name = update.effective_message.text.split()[0].lower()
    key = (update.effective_user.id, command_name)
    now = monotonic()
    cooldown = ADMIN_COMMAND_COOLDOWN_SECONDS if admin else USER_COMMAND_COOLDOWN_SECONDS
    rate_limits = context.application.bot_data.setdefault("rate_limits", {})
    last_call = rate_limits.get(key)

    if last_call is not None and now - last_call < cooldown:
        remaining = cooldown - (now - last_call)
        await update.effective_message.reply_text(
            f"Слишком часто. Подожди ещё {remaining:.1f} сек."
        )
        return True

    rate_limits[key] = now
    return False


async def send_long_message(send_func, text: str) -> None:
    for start in range(0, len(text), MAX_MESSAGE_LENGTH):
        await send_func(text[start : start + MAX_MESSAGE_LENGTH])


async def send_file_and_cleanup(update: Update, file_path: Path, filename: str) -> None:
    try:
        with file_path.open("rb") as document:
            await update.effective_message.reply_document(
                document=document,
                filename=filename,
            )
    finally:
        file_path.unlink(missing_ok=True)


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
    await update.effective_message.reply_text(get_current_report_text())


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


async def broadcast_to_subscribers(
    application: Application, message_text: str
) -> tuple[int, int]:
    subscribers = list_subscribers()
    sent_count = 0
    failed_count = 0

    for chat_id in subscribers:
        try:
            await application.bot.send_message(chat_id=chat_id, text=message_text)
            sent_count += 1
        except Forbidden:
            logging.warning("Chat %s blocked the bot. Removing subscription.", chat_id)
            remove_subscriber(chat_id)
            failed_count += 1
        except TelegramError as exc:
            logging.exception("Broadcast failed for %s: %s", chat_id, exc)
            failed_count += 1
        await asyncio.sleep(0.05)

    return sent_count, failed_count


async def send_channel_post(application: Application, message_text: str) -> None:
    channel = get_autopost_channel()
    if not channel:
        return

    try:
        await application.bot.send_message(chat_id=channel, text=message_text)
    except TelegramError as exc:
        logging.exception("Failed to send message to channel %s: %s", channel, exc)


async def daily_notify(context: ContextTypes.DEFAULT_TYPE) -> None:
    subscribers = list_subscribers()

    message_text = get_current_report_text()
    if subscribers:
        logging.info("Sending daily notification to %s subscribers.", len(subscribers))
        await send_daily_message(context.application, subscribers, message_text)
    else:
        logging.info("No subscribers to notify.")

    await send_channel_post(context.application, message_text)


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
    application.add_handler(CommandHandler("time", time_info))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("users", users))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("export_txt", export_txt))
    application.add_handler(CommandHandler("export_csv", export_csv))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("broadcast_report", broadcast_report))
    application.add_handler(CommandHandler("setchannel", set_channel))
    application.add_handler(CommandHandler("channel", channel_info))
    application.add_handler(CallbackQueryHandler(verify_subscription_callback, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(broadcast_confirmation_callback, pattern="^broadcast:(confirm|cancel):\\d+$"))

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
