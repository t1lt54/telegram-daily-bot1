# Telegram Daily Bot

Telegram-бот на Python, который:

- подписывает пользователя на ежедневные уведомления через `/start`
- требует обязательную подписку на канал `https://t.me/t1lt54_vov`
- показывает кнопки `Подписаться на канал` и `Проверить подписку`
- по команде `/report` вручную показывает, сколько дней осталось до выхода GTA 6
- каждый день в `00:05` по Москве отправляет всем подписанным отчёт

## Команды

- `/start` - включить ежедневные уведомления
- `/stop` - отключить ежедневные уведомления
- `/status` - проверить статус подписки
- `/report` - получить отчёт сразу, не дожидаясь ночной отправки

## Как работает подписка на канал

Если пользователь не подписан на канал, бот показывает две кнопки:

- `Подписаться на канал`
- `Проверить подписку`

Пользователь подписывается на канал, потом нажимает `Проверить подписку`, и бот сразу включает уведомления без повторного ввода `/start`.

## Как работает отчёт

Если дата релиза указана как `2026-11-19`, бот отправляет сообщение такого вида:

```text
На 22.03.2026 до выхода GTA 6 осталось 242 дня.
Официальная дата релиза: 19.11.2026.
```

## Настройки

Файл `.env` для локального запуска должен содержать:

```env
BOT_TOKEN=твой_токен_от_BotFather
DATA_DIR=.
BOT_TIMEZONE=Europe/Moscow
SEND_HOUR=0
SEND_MINUTE=5
RELEASE_DATE=2026-11-19
REQUIRED_CHANNEL=@t1lt54_vov
REQUIRED_CHANNEL_URL=https://t.me/t1lt54_vov
```

## Запуск через cmd

1. Установи Python 3.11 или новее.
2. Открой `cmd`.
3. Перейди в папку проекта:

```cmd
cd C:\Users\homut\Desktop\codex\telegram-daily-bot
```

4. Создай виртуальное окружение:

```cmd
python -m venv .venv
```

5. Активируй виртуальное окружение:

```cmd
.venv\Scripts\activate
```

6. Установи зависимости:

```cmd
pip install -r requirements.txt
```

7. Создай файл `.env`:

```cmd
copy .env.example .env
```

8. Открой `.env` в Блокноте:

```cmd
notepad .env
```

9. Вставь свой токен в `BOT_TOKEN` и сохрани файл.
10. Запусти бота:

```cmd
python bot.py
```

## Как залить на GitHub через cmd

Важно: файл `.env` не должен попадать в GitHub. Он уже добавлен в `.gitignore`.

1. Перейди в корень workspace:

```cmd
cd C:\Users\homut\Desktop\codex\telegram-daily-bot
```

2. Инициализируй Git, если он ещё не инициализирован:

```cmd
git init
```

3. Добавь файлы:

```cmd
git add .
```

4. Сделай первый коммит:

```cmd
git commit -m "Add Telegram daily bot"
```

5. Создай пустой репозиторий на GitHub.
6. Скопируй ссылку на репозиторий, например:

```text
https://github.com/USERNAME/telegram-daily-bot.git
```

7. Подключи GitHub-репозиторий:

```cmd
git remote add origin https://github.com/USERNAME/telegram-daily-bot.git
```

8. Отправь код:

```cmd
git branch -M main
git push -u origin main
```

## Как подключить к Render

Для Telegram-бота с polling лучше использовать `Background Worker`, а не `Web Service`. У Render есть отдельный тип сервиса для постоянно работающих фоновых процессов.

1. Открой [Render Dashboard](https://dashboard.render.com/).
2. Нажми `New`.
3. Выбери `Blueprint` или `Background Worker`.
4. Подключи GitHub-аккаунт.
5. Выбери свой репозиторий.

Если используешь `Blueprint`, Render прочитает файл `render.yaml` автоматически.

Если создаёшь сервис вручную, укажи:

- Runtime: `Python`
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

Потом добавь переменные окружения в Render:

- `BOT_TOKEN` = твой токен от BotFather
- `BOT_TIMEZONE` = `Europe/Moscow`
- `SEND_HOUR` = `0`
- `SEND_MINUTE` = `5`
- `RELEASE_DATE` = `2026-11-19`
- `REQUIRED_CHANNEL` = `@t1lt54_vov`
- `REQUIRED_CHANNEL_URL` = `https://t.me/t1lt54_vov`
- `DATA_DIR` = `.` локально и `/var/data` на Render
- `PYTHON_VERSION` = `3.11.11`

После сохранения Render соберёт и запустит бота. При каждом новом `git push` Render может автоматически перезапускать деплой.

## Что важно для обязательной подписки

- бот должен быть администратором канала или как минимум иметь доступ к проверке участников
- пользователь сначала подписывается на канал `https://t.me/t1lt54_vov`
- потом нажимает кнопку `Проверить подписку`
- если подписки нет, бот не активирует уведомления

## Где хранятся подписчики

После первого запуска рядом с `bot.py` создаётся база `users.db`.

Локально база хранится в папке проекта. На Render для постоянного хранения лучше использовать `Disk` и переменную `DATA_DIR=/var/data`. Тогда база будет лежать на подключённом диске и не потеряется между деплоями.

## Как остановить бота локально

В окне `cmd`, где запущен бот, нажми `Ctrl + C`.
