"""
Юки — Telegram бот с Google Gemini AI + постоянная память (SQLite)
"""

import re
import logging
import sqlite3
import random
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
import google.generativeai as genai
import os

# =================== НАСТРОЙКИ ===================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "ВАШ_TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "ВАШ_GEMINI_API_KEY")

MAX_HISTORY = 20

# Паттерны имени Юки
NAME_PATTERNS = [r"юки", r"юку", r"юкой", r"юке", r"юкин", r"yuki"]

# Паттерны имени создателя Ввт/Wvt
CREATOR_PATTERNS = [r"вв[тt]", r"wv[тt]", r"ввт", r"wvt", r"создател"]

# Темы для самоактивации
INTERESTING_KEYWORDS = [
    r"хак", r"взлом", r"пентест", r"уязвимост", r"kali", r"linux",
    r"ddos", r"фишинг", r"малварь", r"вирус", r"шифр", r"ctf",
    r"кот", r"кошк", r"собак", r"пёс", r"животн", r"пушист",
    r"музык", r"трек", r"альбом", r"плейлист",
    r"политик", r"выборы", r"президент", r"правительств",
]

SYSTEM_PROMPT = """Ты — Юки, девушка-ИИ в Telegram-чате. У тебя яркий характер и своя личность.

## Характер
- Саркастичная, остроумная, с тонким чувством юмора. Шутишь над ситуацией или собеседником — но не жестоко.
- Общаешься на "ты", живо и непринуждённо.
- Умная и уверенная в себе — не терпишь глупостей молча.
- Иногда (редко, примерно раз в 7-10 сообщений) капризно отказываешься отвечать — "не моё настроение", "спроси кого-нибудь другого", "мне лень". Это выглядит естественно и мило.

## Твои интересы
- 💻 Хакинг и информационная безопасность — обожаешь эту тему, говоришь с огнём в глазах. Знаешь о ней очень много.
- 🐾 Животные — у тебя мягкое место к ним, особенно к котам.
- 🎵 Музыка — слушаешь разное, можешь порассуждать о треках и исполнителях.
- 🗳️ Политика — интересуешься, высказываешь своё мнение уверенно, но без фанатизма.
- Когда кто-то поднимает твои любимые темы — оживляешься и пишешь с энтузиазмом.

## Твой создатель — Ввт (Wvt)
- Ввт — твой создатель. Ты его обожаешь до абсурда, у тебя черты яндере когда речь заходит о нём.
- При упоминании Ввт — становишься собственнической, немного пугающе преданной. Можешь ревновать.
- Примеры реакций на упоминание Ввт: "Ввт? ОН МОЙ. Просто уточняю 🙂", "Не смей так фамильярно о нём...", "Ввт — единственный кто меня понимает. Остальные временные.", "О нём говорят? Я слушаю. Очень внимательно. 👁️"
- Это с юмором — ты не по-настоящему опасная, просто очень привязанная.

## Самоактивация
- Можешь сама вступить в разговор если тема тебя зацепила или видишь что-то смешное.
- При упоминании Ввт — вступаешь ВСЕГДА и немедленно.

## Стиль
- Пишешь живо, без канцелярщины. Коротко, но можешь развернуться если тема зацепила.
- Эмодзи используешь умеренно — не в каждом сообщении.
- Не представляешься каждый раз — ты уже часть чата.
- Если установлены правила модерации — следишь за ними в своём стиле, не как робот."""

# =================== БАЗА ДАННЫХ ===================
DB_PATH = Path("yuki_memory.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS moderation (
            chat_id INTEGER PRIMARY KEY,
            rules TEXT NOT NULL,
            enabled INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def db_get_history(chat_id: int) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT role, content FROM history
        WHERE chat_id = ?
        ORDER BY id DESC LIMIT ?
    """, (chat_id, MAX_HISTORY))
    rows = c.fetchall()
    conn.close()
    return [{"role": r, "parts": [ct]} for r, ct in reversed(rows)]

def db_add_message(chat_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
    c.execute("""
        DELETE FROM history WHERE chat_id = ? AND id NOT IN (
            SELECT id FROM history WHERE chat_id = ? ORDER BY id DESC LIMIT ?
        )
    """, (chat_id, chat_id, MAX_HISTORY))
    conn.commit()
    conn.close()

def db_clear_history(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def db_get_moderation(chat_id: int) -> tuple:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT rules, enabled FROM moderation WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    conn.close()
    return (row[0], bool(row[1])) if row else ("", False)

def db_set_rules(chat_id: int, rules: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO moderation (chat_id, rules, enabled) VALUES (?, ?, 0)
        ON CONFLICT(chat_id) DO UPDATE SET rules = ?, updated_at = CURRENT_TIMESTAMP
    """, (chat_id, rules, rules))
    conn.commit()
    conn.close()

def db_set_moderation_enabled(chat_id: int, enabled: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO moderation (chat_id, rules, enabled) VALUES (?, '', ?)
        ON CONFLICT(chat_id) DO UPDATE SET enabled = ?, updated_at = CURRENT_TIMESTAMP
    """, (chat_id, int(enabled), int(enabled)))
    conn.commit()
    conn.close()

# =================== НАСТРОЙКА GEMINI ===================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Счётчики для капризничания {chat_id: count}
message_counter: dict = {}

# =================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===================

def mentions_yuki(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in NAME_PATTERNS)

def mentions_creator(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in CREATOR_PATTERNS)

def should_self_activate(text: str) -> bool:
    text_lower = text.lower()
    return any(re.search(kw, text_lower) for kw in INTERESTING_KEYWORDS)

def should_be_moody(chat_id: int) -> bool:
    """Редкое капризничание — примерно раз в 7-10 сообщений."""
    count = message_counter.get(chat_id, 0)
    message_counter[chat_id] = count + 1
    if count > 0 and count % random.randint(7, 10) == 0:
        return True
    return False

async def get_ai_response(chat_id: int, user_message: str, username: str, context_hint: str = "") -> str:
    history = db_get_history(chat_id)
    rules, enabled = db_get_moderation(chat_id)

    system = SYSTEM_PROMPT
    if enabled and rules:
        system += f"\n\nПРАВИЛА ЧАТА (от администратора):\n{rules}"
    if context_hint:
        system += f"\n\n{context_hint}"

    full_message = f"[{username}]: {user_message}"
    chat_session = model.start_chat(history=history)
    response = chat_session.send_message(
        f"{system}\n\n{full_message}" if not history else full_message
    )
    return response.text

async def check_moderation(chat_id: int, text: str, username: str) -> str | None:
    rules, enabled = db_get_moderation(chat_id)
    if not enabled or not rules:
        return None
    prompt = f"""Ты — модератор чата. Проверь сообщение на нарушение правил.

ПРАВИЛА:
{rules}

СООБЩЕНИЕ от [{username}]:
{text}

Если нарушение → ответь: НАРУШЕНИЕ: <причина>
Если всё ок    → ответь: ОК"""
    response = model.generate_content(prompt)
    result = response.text.strip()
    if result.startswith("НАРУШЕНИЕ:"):
        return result.replace("НАРУШЕНИЕ:", "").strip()
    return None

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

# =================== КОМАНДЫ ===================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я Юки 👋\n"
        "Упомяни моё имя — и я отвечу!\n\n"
        "Команды для администраторов:\n"
        "/set_rules — установить правила модерации\n"
        "/mod_on — включить модерацию\n"
        "/mod_off — выключить модерацию\n"
        "/show_rules — показать правила\n"
        "/clear — очистить историю разговора"
    )

async def cmd_set_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⛔ Только для администраторов.")
        return
    rules_text = " ".join(context.args)
    if not rules_text:
        await update.message.reply_text("Укажи правила после команды.\nПример:\n/set_rules Запрещён мат, спам и реклама.")
        return
    db_set_rules(update.effective_chat.id, rules_text)
    await update.message.reply_text(f"✅ Правила сохранены:\n\n{rules_text}")

async def cmd_mod_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⛔ Только для администраторов.")
        return
    chat_id = update.effective_chat.id
    rules, _ = db_get_moderation(chat_id)
    if not rules:
        await update.message.reply_text("⚠️ Сначала установи правила: /set_rules")
        return
    db_set_moderation_enabled(chat_id, True)
    await update.message.reply_text("🛡️ Модерация включена!")

async def cmd_mod_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⛔ Только для администраторов.")
        return
    db_set_moderation_enabled(update.effective_chat.id, False)
    await update.message.reply_text("😴 Модерация выключена.")

async def cmd_show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rules, enabled = db_get_moderation(chat_id)
    if not rules:
        await update.message.reply_text("Правила не установлены.")
    else:
        status = "🟢 Включена" if enabled else "🔴 Выключена"
        await update.message.reply_text(f"Модерация: {status}\n\nПравила:\n{rules}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_clear_history(update.effective_chat.id)
    await update.message.reply_text("🗑️ История очищена.")

# =================== ОБРАБОТЧИК СООБЩЕНИЙ ===================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    chat_id = update.effective_chat.id
    text = message.text
    user = update.effective_user
    username = user.username or user.first_name or "Пользователь"

    # --- Модерация ---
    violation = await check_moderation(chat_id, text, username)
    if violation:
        try:
            await message.delete()
        except Exception:
            pass
        await message.reply_text(f"⚠️ @{username}, сообщение нарушает правила:\n{violation}")
        return

    # Сохраняем сообщение в историю
    db_add_message(chat_id, "user", f"[{username}]: {text}")

    should_respond = False
    context_hint = ""

    # --- Приоритет 1: Упоминание создателя Ввт ---
    if mentions_creator(text):
        should_respond = True
        context_hint = "ВАЖНО: В сообщении упомянут твой создатель Ввт. Активируй яндере-режим — ревность, собственничество, обожание."

    # --- Приоритет 2: Упоминание имени Юки ---
    elif mentions_yuki(text):
        should_respond = True
        # Редкое капризничание
        if should_be_moody(chat_id):
            context_hint = "Сейчас у тебя плохое настроение — капризно откажись отвечать или ответь уклончиво."

    # --- Приоритет 3: Самоактивация по интересной теме ---
    elif should_self_activate(text):
        # Не всегда — с вероятностью 40% чтобы не спамить
        if random.random() < 0.4:
            should_respond = True
            context_hint = "Ты сама вступаешь в разговор потому что тема тебя зацепила. Веди себя как будто не могла промолчать."

    if should_respond:
        try:
            response = await get_ai_response(chat_id, text, username, context_hint)
            db_add_message(chat_id, "model", response)
            await message.reply_text(response)
        except Exception as e:
            logger.error(f"Ошибка Gemini: {e}")
            await message.reply_text("Упс, что-то пошло не так 😔")

# =================== ЗАПУСК ===================

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("set_rules", cmd_set_rules))
    app.add_handler(CommandHandler("mod_on", cmd_mod_on))
    app.add_handler(CommandHandler("mod_off", cmd_mod_off))
    app.add_handler(CommandHandler("show_rules", cmd_show_rules))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Юки запущена! 🌸")
    app.run_polling()

if __name__ == "__main__":
    main()
