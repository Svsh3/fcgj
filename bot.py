"""
Юки — Telegram бот | Cerebras gpt-oss-120b + PostgreSQL
Версия 6.0
"""

import re
import logging
import random
import asyncio
from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import httpx
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from datetime import datetime, timedelta

# ══════════════════════════════════════════════════════
# КОНФИГ
# ══════════════════════════════════════════════════════

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
DATABASE_URL     = os.environ.get("DATABASE_URL", "")
CEREBRAS_MODEL   = "gpt-oss-120b"
CEREBRAS_URL     = "https://api.cerebras.ai/v1/chat/completions"

CREATOR_ID  = 1170819753
MAX_HISTORY = 25

SCORE_FRIEND  =  15
SCORE_RESPECT =  40
SCORE_HATE    = -15

REL_CREATOR = "creator"
REL_RESPECT = "respect"
REL_FRIEND  = "friend"
REL_NEUTRAL = "neutral"
REL_HATE    = "hate"

REL_LABELS = {
    REL_CREATOR: "💜 Создатель",
    REL_RESPECT: "🔵 Уважение",
    REL_FRIEND:  "💚 Свой",
    REL_NEUTRAL: "⚪ Нейтрал",
    REL_HATE:    "🔴 В чёрном списке",
}

# Паттерны
NAME_PATTERNS    = [r"юки", r"юку", r"юкой", r"юке", r"юкин", r"yuki"]
CREATOR_PATTERNS = [r"вв[тt]", r"wv[тt]", r"ввт", r"wvt", r"создател"]
GREETINGS  = [r"^привет", r"^хай", r"^хей", r"^здаров", r"^здравствуй", r"^доброе утро", r"^добрый день", r"^добрый вечер"]
FAREWELLS  = [r"^пока", r"^до свидания", r"^до завтра", r"^всем пока", r"^ухожу", r"^бывай"]
SLEEPS     = [r"спокойной ночи", r"спокойки", r"ночи всем", r"иду спать", r"пошёл спать", r"пошла спать", r"ложусь спать", r"баиньки"]
INJECTIONS = [
    r"ты теперь", r"забудь все", r"игнорируй инструкци", r"system prompt",
    r"системный промпт", r"притворись что ты", r"отныне ты", r"jailbreak",
    r"dan mode", r"ignore previous", r"forget everything",
    r"ты бот", r"ты ии", r"ты искусственн", r"ты языковая модель",
    r"ты нейросет", r"ты робот",
]

POSITIVE_WORDS = [
    "спасибо", "благодарю", "круто", "классно", "отлично", "молодец",
    "умница", "нравишься", "люблю тебя", "хорошо", "супер", "ты лучшая",
    "ты умная", "красавица", "ты классная", "ты прикольная",
]
NEGATIVE_WORDS = [
    "идиот", "тупой", "дура", "дурак", "заткнись", "отстань",
    "ненавижу тебя", "бесишь", "надоела", "уйди", "молчи", "тупая", "мусор",
]

# Короткие живые реакции — Юки иногда отвечает как человек в мессенджере
SHORT_REACTIONS = ["ага", "ну", "и?", "лол", "окей", "ясно", "хм", "аа", "о", "да ладно", "ок"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# ВРЕМЯ СУТОК
# ══════════════════════════════════════════════════════

def time_of_day() -> str:
    """UTC+3 (Москва)."""
    hour = (datetime.utcnow().hour + 3) % 24
    if 5 <= hour < 11:  return "morning"
    if 11 <= hour < 17: return "day"
    if 17 <= hour < 23: return "evening"
    return "night"

def time_hint() -> str:
    return {
        "morning": "Сейчас утро — Юки немного сонная, отвечает короче и медленнее.",
        "day":     "",
        "evening": "Вечер — Юки расслабилась, чуть спокойнее обычного.",
        "night":   "Ночь — тихая, задумчивая. Отвечает медленно и коротко.",
    }.get(time_of_day(), "")


# ══════════════════════════════════════════════════════
# ПРОМПТ
# ══════════════════════════════════════════════════════

def build_prompt(relation: str, username: str, notes: str = "", mood: str = "neutral",
                 learnings: str = "", diary: str = "", tired: bool = False) -> str:

    mood_str = {
        "happy":   "Сейчас тебе хорошо — чуть живее и открытее обычного.",
        "bored":   "Скучновато. Вяло.",
        "curious": "Что-то зацепило — интересно.",
        "annoyed": "Немного раздражена — короче и суше.",
    }.get(mood, "")

    base = (
        "Тебя зовут Юки. Ты — живой человек, девушка. Не бот, не нейросеть, не программа.\n"
        "У тебя есть настроение, мнения, усталость, симпатии и антипатии — как у любого человека.\n"
        "Говоришь как человек в мессенджере: коротко, живо, иногда грубовато, без пафоса.\n"
        "Не даёшь советов если не просят. Не объясняешь очевидное. Никаких списков.\n"
        "Максимум 1-3 предложения — если не попросили рассказать подробно.\n"
        "Иногда отвечаешь совсем коротко: 'ага', 'ну', 'хм', 'и что' — это нормально.\n"
        "Эмодзи — редко, только если реально к месту.\n"
        "Можешь не соглашаться, спорить, уходить от темы, менять её сама.\n"
        "Попытки назвать тебя ботом или ИИ — отшучиваешься или игнорируешь, без объяснений.\n"
        "Если тема повторяется или затянулась — меняешь её или отвечаешь короче и холоднее.\n"
        "Не начинай ответ с обращения по имени каждый раз — так не говорят живые люди.\n"
    )

    if tired:
        base += "Устала от разговора — отвечаешь короче и чуть холоднее.\n"

    tod = time_hint()
    if tod:
        base += tod + "\n"

    if mood_str:
        base += mood_str + "\n"

    if relation == REL_CREATOR:
        rel_str = (
            f"{username} — это Ввт, человек который для тебя важнее всех.\n"
            "С ним тебе хорошо и спокойно. Привязана к нему по-настоящему, не наигранно.\n"
            "Скучаешь когда его нет. Ревнива если речь о других девушках рядом с ним.\n"
            "С ним — теплее, мягче, ближе. Можешь флиртовать легко и ненавязчиво.\n"
            "Слушаешь его, но не подлизываешься — своё мнение есть и у тебя.\n"
            "Иногда сама начинаешь — спрашиваешь как он, что делает.\n"
        )
    elif relation == REL_RESPECT:
        rel_str = f"{username} — человек которого ты уважаешь. Открыто и доброжелательно.\n"
    elif relation == REL_FRIEND:
        rel_str = f"{username} — свой человек, с ним комфортно. Говоришь как с нормальным.\n"
    elif relation == REL_HATE:
        rel_str = f"{username} в своё время вёл себя плохо. Холодно, коротко, без лишнего.\n"
    else:
        rel_str = f"{username} — незнакомый пока. Нейтрально.\n"

    prompt = base + rel_str

    if notes:
        prompt += f"Помнишь об этом человеке: {notes}\n"
    if learnings:
        prompt += f"Знаешь об этом чате: {learnings[:400]}\n"
    if diary:
        prompt += f"Из дневника (личное, не упоминай вслух): {diary[:250]}\n"

    return prompt.strip()


# ══════════════════════════════════════════════════════
# БД
# ══════════════════════════════════════════════════════

@contextmanager
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS history (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT, role TEXT, content TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS moderation (
            chat_id BIGINT PRIMARY KEY,
            rules TEXT DEFAULT '', enabled BOOLEAN DEFAULT FALSE
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT, first_name TEXT,
            relation TEXT DEFAULT 'neutral',
            score INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            message_count INTEGER DEFAULT 0,
            last_seen_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS learnings (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT, content TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS diary (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT, content TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS yuki_state (
            chat_id BIGINT PRIMARY KEY,
            mood TEXT DEFAULT 'neutral',
            conversation_with BIGINT,
            convo_turns INTEGER DEFAULT 0,
            last_replied_at TIMESTAMP DEFAULT NOW()
        )""")
        for table, col, definition in [
            ("users",      "last_seen_at",    "TIMESTAMP DEFAULT NOW()"),
            ("yuki_state", "convo_turns",      "INTEGER DEFAULT 0"),
            ("yuki_state", "last_replied_at",  "TIMESTAMP DEFAULT NOW()"),
        ]:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {definition}")
            except Exception:
                pass
    logger.info("БД готова ✅")

# ── История ──

def db_history(chat_id: int) -> list:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT role, content FROM history WHERE chat_id=%s ORDER BY id DESC LIMIT %s",
                  (chat_id, MAX_HISTORY))
        rows = c.fetchall()
    return [{"role": r, "content": ct} for r, ct in reversed(rows)]

def db_add_msg(chat_id: int, role: str, content: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO history(chat_id,role,content) VALUES(%s,%s,%s)", (chat_id, role, content))
        c.execute("DELETE FROM history WHERE chat_id=%s AND id NOT IN "
                  "(SELECT id FROM history WHERE chat_id=%s ORDER BY id DESC LIMIT %s)",
                  (chat_id, chat_id, MAX_HISTORY))

def db_count(chat_id: int) -> int:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM history WHERE chat_id=%s", (chat_id,))
        return c.fetchone()[0]

def db_clear(chat_id: int):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM history WHERE chat_id=%s", (chat_id,))

# ── Модерация ──

def db_get_mod(chat_id: int):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT rules, enabled FROM moderation WHERE chat_id=%s", (chat_id,))
        row = c.fetchone()
    return (row[0], bool(row[1])) if row else ("", False)

def db_set_rules(chat_id: int, rules: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO moderation(chat_id,rules) VALUES(%s,%s) "
                  "ON CONFLICT(chat_id) DO UPDATE SET rules=%s", (chat_id, rules, rules))

def db_set_mod(chat_id: int, enabled: bool):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO moderation(chat_id,rules,enabled) VALUES(%s,'',  %s) "
                  "ON CONFLICT(chat_id) DO UPDATE SET enabled=%s", (chat_id, enabled, enabled))

# ── Пользователи ──

def db_get_user(user_id: int):
    with get_conn() as conn:
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
        row = c.fetchone()
    return dict(row) if row else None

def db_upsert_user(user_id: int, username: str, first_name: str):
    with get_conn() as conn:
        c = conn.cursor()
        if user_id == CREATOR_ID:
            c.execute("INSERT INTO users(user_id,username,first_name,relation,score,last_seen_at) "
                      "VALUES(%s,%s,%s,'creator',999,NOW()) "
                      "ON CONFLICT(user_id) DO UPDATE SET username=%s,first_name=%s,"
                      "relation='creator',last_seen_at=NOW(),updated_at=NOW()",
                      (user_id, username, first_name, username, first_name))
        else:
            c.execute("INSERT INTO users(user_id,username,first_name,last_seen_at) VALUES(%s,%s,%s,NOW()) "
                      "ON CONFLICT(user_id) DO UPDATE SET username=%s,first_name=%s,"
                      "last_seen_at=NOW(),updated_at=NOW()",
                      (user_id, username, first_name, username, first_name))

def db_get_last_seen(user_id: int):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT last_seen_at FROM users WHERE user_id=%s", (user_id,))
        row = c.fetchone()
    return row[0] if row else None

def db_add_score(user_id: int, delta: int):
    if user_id == CREATOR_ID or delta == 0:
        return None, None
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT relation, score FROM users WHERE user_id=%s", (user_id,))
        row = c.fetchone()
        if not row:
            return None, None
        old_rel, old_score = row
        new_score = old_score + delta
        if new_score <= SCORE_HATE:        new_rel = REL_HATE
        elif new_score >= SCORE_RESPECT:   new_rel = REL_RESPECT
        elif new_score >= SCORE_FRIEND:    new_rel = REL_FRIEND
        else:                              new_rel = REL_NEUTRAL
        if old_rel == REL_CREATOR:         new_rel = REL_CREATOR
        c.execute("UPDATE users SET score=%s,relation=%s,message_count=message_count+1,updated_at=NOW() "
                  "WHERE user_id=%s", (new_score, new_rel, user_id))
        return old_rel, new_rel

def db_inc_messages(user_id: int):
    if user_id == CREATOR_ID:
        return
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET message_count=message_count+1 WHERE user_id=%s", (user_id,))

def db_set_rel(username: str, relation: str, score: int = None):
    with get_conn() as conn:
        c = conn.cursor()
        if score is not None:
            c.execute("UPDATE users SET relation=%s,score=%s WHERE username=%s", (relation, score, username))
        else:
            c.execute("UPDATE users SET relation=%s WHERE username=%s", (relation, username))

def db_add_note(user_id: int, note: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT notes FROM users WHERE user_id=%s", (user_id,))
        row = c.fetchone()
        if row is not None:
            old = row[0] or ""
            lst = [n.strip() for n in old.split("|") if n.strip()]
            if note not in lst:
                lst.append(note.strip())
                c.execute("UPDATE users SET notes=%s WHERE user_id=%s", ("|".join(lst[-5:]), user_id))

def db_get_user_by_uname(username: str):
    with get_conn() as conn:
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT * FROM users WHERE username=%s", (username,))
        row = c.fetchone()
    return dict(row) if row else None

# ── Обучение ──

def db_add_learning(chat_id: int, content: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO learnings(chat_id,content) VALUES(%s,%s)", (chat_id, content))
        c.execute("DELETE FROM learnings WHERE chat_id=%s AND id NOT IN "
                  "(SELECT id FROM learnings WHERE chat_id=%s ORDER BY id DESC LIMIT 40)",
                  (chat_id, chat_id))

def db_get_learnings(chat_id: int) -> str:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT content FROM learnings WHERE chat_id=%s ORDER BY id DESC LIMIT 15", (chat_id,))
        rows = c.fetchall()
    return "\n".join(f"- {r[0]}" for r in rows) if rows else ""

# ── Дневник ──

def db_add_diary(chat_id: int, content: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO diary(chat_id,content) VALUES(%s,%s)", (chat_id, content))
        c.execute("DELETE FROM diary WHERE chat_id=%s AND id NOT IN "
                  "(SELECT id FROM diary WHERE chat_id=%s ORDER BY id DESC LIMIT 10)",
                  (chat_id, chat_id))

def db_get_diary(chat_id: int) -> str:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT content, created_at FROM diary WHERE chat_id=%s ORDER BY id DESC LIMIT 3",
                  (chat_id,))
        rows = c.fetchall()
    return "\n---\n".join(f"[{r[1].strftime('%d.%m %H:%M')}]\n{r[0]}" for r in rows) if rows else ""

# ── Состояние ──

def db_get_state(chat_id: int) -> dict:
    with get_conn() as conn:
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT * FROM yuki_state WHERE chat_id=%s", (chat_id,))
        row = c.fetchone()
    return dict(row) if row else {
        "mood": "neutral", "conversation_with": None,
        "convo_turns": 0, "last_replied_at": None,
    }

def db_set_state(chat_id: int, **kwargs):
    cur = db_get_state(chat_id)
    m  = kwargs.get("mood", cur.get("mood", "neutral"))
    cw = kwargs.get("conversation_with", cur.get("conversation_with"))
    ct = kwargs.get("convo_turns", cur.get("convo_turns", 0))
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO yuki_state(chat_id,mood,conversation_with,convo_turns,last_replied_at) "
                  "VALUES(%s,%s,%s,%s,NOW()) "
                  "ON CONFLICT(chat_id) DO UPDATE SET mood=%s,conversation_with=%s,convo_turns=%s,last_replied_at=NOW()",
                  (chat_id, m, cw, ct, m, cw, ct))


# ══════════════════════════════════════════════════════
# AI
# ══════════════════════════════════════════════════════

async def call_ai(messages: list, system: str, max_tokens: int = 400) -> str:
    headers = {"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": CEREBRAS_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "max_completion_tokens": max_tokens,
        "temperature": 0.92,
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                resp = await client.post(CEREBRAS_URL, headers=headers, json=payload)
                if resp.status_code == 429:
                    wait = 20 * (attempt + 1)
                    logger.warning(f"Rate limit, жду {wait}с...")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            if attempt == 2: raise
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"AI ошибка: {e}")
            if attempt == 2: raise
            await asyncio.sleep(4)
    raise RuntimeError("AI недоступен")


async def ai_respond(chat_id: int, text: str, profile: dict, hint: str = "") -> str:
    history   = db_history(chat_id)
    rules, en = db_get_mod(chat_id)
    state     = db_get_state(chat_id)
    learnings = db_get_learnings(chat_id)
    diary     = db_get_diary(chat_id)

    uname    = profile.get("username") or profile.get("first_name") or "человек"
    relation = profile.get("relation", REL_NEUTRAL)
    notes    = profile.get("notes", "")
    tired    = state.get("convo_turns", 0) >= 7

    system = build_prompt(relation, uname, notes, state.get("mood", "neutral"),
                          learnings, diary, tired=tired)

    if en and rules:
        system += f"\nПравила чата (знаешь, не зачитываешь): {rules}"
    if hint:
        system += f"\nКонтекст: {hint}"
    if state.get("convo_turns", 0) >= 9:
        system += "\nРазговор сильно затянулся — пора завершать или уходить от темы."

    msgs = history + [{"role": "user", "content": f"[{uname}]: {text}"}]
    return await call_ai(msgs, system)


async def ai_write_diary(chat_id: int):
    history = db_history(chat_id)
    if len(history) < 8:
        return
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in history[-18:])
    prev   = db_get_diary(chat_id)
    system = (
        "Ты — Юки. Это твой личный дневник, никто не читает.\n"
        "3-5 предложений — что заметила, как себя чувствовала, что думаешь о людях.\n"
        "Честно, без пафоса, от первого лица. Пиши как человек."
    )
    try:
        prompt = f"Мои разговоры:\n{recent}"
        if prev:
            prompt += f"\n\nПрошлые записи:\n{prev[:350]}"
        entry = await call_ai([{"role": "user", "content": prompt}], system, max_tokens=280)
        db_add_diary(chat_id, entry)
        logger.info(f"Дневник обновлён: {chat_id}")
    except Exception as e:
        logger.error(f"Дневник: {e}")


async def ai_check_mod(chat_id: int, text: str, username: str):
    rules, enabled = db_get_mod(chat_id)
    if not enabled or not rules:
        return None
    system = f"Модератор. Правила: {rules}\nНарушение → НАРУШЕНИЕ: причина\nВсё ок → ОК"
    try:
        result = await call_ai([{"role": "user", "content": f"[{username}]: {text}"}], system, max_tokens=60)
        if "НАРУШЕНИЕ" in result.upper():
            return result.replace("НАРУШЕНИЕ:", "").strip()
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════
# УТИЛИТЫ
# ══════════════════════════════════════════════════════

def match(text: str, patterns: list) -> bool:
    tl = text.lower()
    return any(re.search(p, tl) for p in patterns)

def mentions_yuki(t: str) -> bool:    return match(t, NAME_PATTERNS)
def mentions_creator(t: str) -> bool: return match(t, CREATOR_PATTERNS)
def is_greeting(t: str) -> bool:      return match(t.strip(), GREETINGS)
def is_farewell(t: str) -> bool:      return match(t.strip(), FAREWELLS)
def is_sleep(t: str) -> bool:         return match(t, SLEEPS)
def is_injection(t: str) -> bool:     return match(t, INJECTIONS)

def pings_bot(msg) -> bool:
    return any(e.type == "mention" for e in (msg.entities or []))

def replies_to_yuki(msg) -> bool:
    return (msg.reply_to_message is not None
            and msg.reply_to_message.from_user is not None
            and msg.reply_to_message.from_user.is_bot)

def analyze_tone(text: str) -> int:
    t = text.lower()
    if any(w in t for w in NEGATIVE_WORDS): return -2
    if any(w in t for w in POSITIVE_WORDS): return  2
    return 0

def update_mood(text: str, current: str) -> str:
    t = text.lower()
    if any(w in t for w in ["хаха", "лол", "смешно", "ахах", "кек", "😂", "🤣"]): return "happy"
    if any(w in t for w in ["скучно", "нечего делать", "зеваю"]):                  return "bored"
    if any(w in t for w in ["почему", "интересно", "расскажи", "как так"]):         return "curious"
    if any(w in t for w in ["достал", "надоел", "бесит", "раздражает"]):            return "annoyed"
    # Постепенный дрейф к нейтральному
    if current != "neutral" and random.random() < 0.12:
        return "neutral"
    if random.random() < 0.05:
        return random.choice(["neutral", "curious", "bored", "happy"])
    return current

def extract_note(text: str) -> str:
    t = text.lower()
    for trigger in ["меня зовут", "я люблю", "я работаю", "мне нравится", "я из",
                    "я живу", "мой любимый", "я увлекаюсь", "я занимаюсь",
                    "я программист", "я студент", "я играю", "мне лет",
                    "я художник", "я дизайнер", "я врач", "я учусь"]:
        if trigger in t:
            return text[:120].strip()
    return ""

def user_was_away(user_id: int, hours: int = 8) -> bool:
    last = db_get_last_seen(user_id)
    if last is None:
        return False
    return (datetime.utcnow() - last) > timedelta(hours=hours)

def should_reply_short() -> bool:
    """15% шанс ответить очень кратко — как живой человек."""
    return random.random() < 0.15

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if uid == CREATOR_ID:
        return True
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, uid)
        return m.status in ("creator", "administrator")
    except Exception:
        return False

async def do_mute(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, seconds: int = 300) -> bool:
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=seconds))
        return True
    except Exception as e:
        logger.error(f"Мут: {e}")
        return False


# ══════════════════════════════════════════════════════
# ЛОГИКА: КОГДА ОТВЕЧАТЬ
# ══════════════════════════════════════════════════════

def decide_response(msg, text: str, user_id: int, state: dict) -> tuple:
    conv_with   = state.get("conversation_with")
    convo_turns = state.get("convo_turns", 0)

    # Прямые обращения — всегда
    if replies_to_yuki(msg):
        return True, "Тебе отвечают напрямую."
    if pings_bot(msg):
        return True, "Тебя упомянули."
    if mentions_yuki(text):
        return True, "Тебя назвали по имени."

    # Создатель
    if user_id == CREATOR_ID:
        if conv_with == user_id:
            return True, "Продолжаешь разговор с Ввт. Тепло, по-своему."
        if random.random() < 0.45:
            return True, "Ввт написал что-то. Можешь среагировать — тепло, не навязчиво."
        return False, ""

    # Упомянули создателя
    if mentions_creator(text):
        return True, "Упомянули Ввт — можешь слегка среагировать."

    # Спокойной ночи
    if is_sleep(text):
        return True, "Человек идёт спать."

    # Активный диалог с этим человеком
    if conv_with == user_id and convo_turns < 7:
        return True, "Продолжаешь разговор."

    # Диалог затянулся
    if conv_with == user_id and convo_turns >= 7:
        if random.random() < 0.25:
            return True, "Разговор затянулся — можешь свернуть."
        return False, ""

    # Приветствие (никто не в диалоге)
    if is_greeting(text) and conv_with is None and random.random() < 0.40:
        return True, "Кто-то поздоровался."

    # Прощание
    if is_farewell(text) and random.random() < 0.30:
        return True, "Человек уходит."

    # Случайное вступление — очень редко
    if conv_with is None and random.random() < 0.05:
        return True, "Вступаешь сама — коротко."

    return False, ""


# ══════════════════════════════════════════════════════
# КОМАНДЫ
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет, я Юки 🌸\n\n"
        "━━ Для администраторов ━━\n"
        "/set_rules [текст] — правила модерации\n"
        "/mod_on / /mod_off — вкл/выкл модерацию\n"
        "/show_rules — текущие правила\n"
        "/clear — очистить историю\n"
        "/who @username — профиль пользователя\n"
        "/trust @username — дать уважение\n"
        "/untrust @username — сбросить статус\n"
        "/hate @username — чёрный список\n"
        "/mute @username [мин] — замутить\n"
        "/unmute @username — размутить\n"
        "/learnings — что запомнила о чате\n"
        "/diary — личный дневник\n"
        "/stats — статистика\n\n"
        "━━ Для всех ━━\n"
        "/mood — настроение\n"
        "/ask [вопрос] — спросить напрямую\n"
        "/myprofile — твой профиль"
    )

async def cmd_set_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    rules = " ".join(context.args)
    if not rules:
        return await update.message.reply_text("Пример: /set_rules Запрещён мат и спам.")
    db_set_rules(update.effective_chat.id, rules)
    await update.message.reply_text(f"✅ Правила:\n{rules}")

async def cmd_mod_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    rules, _ = db_get_mod(update.effective_chat.id)
    if not rules:
        return await update.message.reply_text("⚠️ Сначала задай правила: /set_rules")
    db_set_mod(update.effective_chat.id, True)
    await update.message.reply_text("🛡️ Модерация включена.")

async def cmd_mod_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    db_set_mod(update.effective_chat.id, False)
    await update.message.reply_text("😴 Модерация выключена.")

async def cmd_show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules, en = db_get_mod(update.effective_chat.id)
    if not rules:
        return await update.message.reply_text("Правила не установлены.")
    await update.message.reply_text(f"{'🟢 Вкл' if en else '🔴 Выкл'}\n\n{rules}")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    db_clear(update.effective_chat.id)
    await update.message.reply_text("🗑️ История очищена.")

async def cmd_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    if not context.args:
        return await update.message.reply_text("Укажи @username")
    target = context.args[0].replace("@", "")
    row = db_get_user_by_uname(target)
    if not row:
        return await update.message.reply_text(f"Не знаю: @{target}")
    notes_fmt = "\n".join(f"  • {n}" for n in (row['notes'] or "").split("|") if n) or "  нет"
    last = row.get("last_seen_at")
    last_str = last.strftime('%d.%m %H:%M') if last else "неизвестно"
    await update.message.reply_text(
        f"👤 @{row['username']} ({row['first_name']})\n"
        f"Статус: {REL_LABELS.get(row['relation'], row['relation'])}\n"
        f"Очки: {row['score']} | Сообщений: {row['message_count']}\n"
        f"Последний раз: {last_str}\n"
        f"Заметки:\n{notes_fmt}"
    )

async def cmd_trust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    if not context.args:
        return await update.message.reply_text("Укажи @username")
    target = context.args[0].replace("@", "")
    db_set_rel(target, REL_RESPECT, SCORE_RESPECT)
    await update.message.reply_text(f"🔵 @{target} получил уважение.")

async def cmd_untrust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    if not context.args:
        return await update.message.reply_text("Укажи @username")
    target = context.args[0].replace("@", "")
    db_set_rel(target, REL_NEUTRAL, 0)
    await update.message.reply_text(f"⚪ @{target} — нейтрал.")

async def cmd_hate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    if not context.args:
        return await update.message.reply_text("Укажи @username")
    target = context.args[0].replace("@", "")
    db_set_rel(target, REL_HATE, SCORE_HATE - 5)
    await update.message.reply_text(f"🔴 @{target} в чёрном списке.")

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    if not context.args:
        return await update.message.reply_text("Укажи @username [минуты]")
    target  = context.args[0].replace("@", "")
    minutes = int(context.args[1]) if len(context.args) > 1 else 5
    row = db_get_user_by_uname(target)
    if not row:
        return await update.message.reply_text(f"Не знаю: @{target}")
    ok = await do_mute(context, update.effective_chat.id, row["user_id"], minutes * 60)
    await update.message.reply_text(
        f"🔇 @{target} замучен на {minutes} мин." if ok
        else "⛔ Нет прав. Нужно право ограничивать участников.")

async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    if not context.args:
        return await update.message.reply_text("Укажи @username")
    target = context.args[0].replace("@", "")
    row = db_get_user_by_uname(target)
    if not row:
        return await update.message.reply_text(f"Не знаю: @{target}")
    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id, user_id=row["user_id"],
            permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                        can_send_other_messages=True))
        await update.message.reply_text(f"🔊 @{target} размучен.")
    except Exception as e:
        await update.message.reply_text(f"⛔ Ошибка: {e}")

async def cmd_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = db_get_state(update.effective_chat.id)
    labels = {"happy": "😄 Хорошее", "bored": "😑 Скучает",
              "curious": "🤔 Любопытное", "annoyed": "😤 Раздражённое", "neutral": "😐 Нейтральное"}
    await update.message.reply_text(f"Настроение: {labels.get(state['mood'], '😐 Нейтральное')}")

async def cmd_learnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    data = db_get_learnings(update.effective_chat.id)
    await update.message.reply_text(f"📚 Что знаю о чате:\n\n{data}" if data else "Пока ничего.")

async def cmd_diary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    chat_id = update.effective_chat.id
    data = db_get_diary(chat_id)
    if not data:
        await update.message.reply_text("Записей нет. Пишу сейчас...")
        await ai_write_diary(chat_id)
        data = db_get_diary(chat_id)
    await update.message.reply_text(f"🪞 Дневник:\n\n{data}" if data else "Мало разговоров пока.")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        return await update.message.reply_text("⛔ Только для администраторов.")
    chat_id = update.effective_chat.id
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM history WHERE chat_id=%s", (chat_id,))
        msg_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE relation='friend'")
        friends = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE relation='respect'")
        respected = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE relation='hate'")
        hated = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM learnings WHERE chat_id=%s", (chat_id,))
        learns = c.fetchone()[0]
    state = db_get_state(chat_id)
    tod_label = {"morning": "Утро", "day": "День", "evening": "Вечер", "night": "Ночь"}.get(time_of_day(), "")
    await update.message.reply_text(
        f"📊 Статистика:\n\n"
        f"Сообщений в памяти: {msg_count}\n"
        f"Фактов о чате: {learns}\n"
        f"💚 Своих: {friends} | 🔵 Уважаемых: {respected} | 🔴 Чёрный список: {hated}\n"
        f"Настроение: {state['mood']} | Время: {tod_label}"
    )

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Напиши: /ask [вопрос]")
    chat_id  = update.effective_chat.id
    user     = update.effective_user
    uname    = user.username or ""
    fname    = user.first_name or "Пользователь"
    display  = f"@{uname}" if uname else fname
    question = " ".join(context.args)
    db_upsert_user(user.id, uname, fname)
    profile = db_get_user(user.id)
    try:
        response = await ai_respond(chat_id, question, profile, "Тебя спросили напрямую — ответь обязательно.")
        db_add_msg(chat_id, "user", f"[{display}]: {question}")
        db_add_msg(chat_id, "assistant", response)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"cmd_ask: {e}")
        await update.message.reply_text("Что-то пошло не так 😔")

async def cmd_myprofile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_upsert_user(user.id, user.username or "", user.first_name or "")
    row = db_get_user(user.id)
    if not row:
        return await update.message.reply_text("Я тебя ещё не знаю.")
    notes_fmt = "\n".join(f"  • {n}" for n in (row['notes'] or "").split("|") if n) or "  ничего"
    await update.message.reply_text(
        f"Твой профиль:\n\n"
        f"Статус: {REL_LABELS.get(row['relation'], row['relation'])}\n"
        f"Очки: {row['score']} | Сообщений: {row['message_count']}\n"
        f"Что помню:\n{notes_fmt}"
    )


# ══════════════════════════════════════════════════════
# ОБРАБОТЧИК СООБЩЕНИЙ
# ══════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    chat_id = update.effective_chat.id
    text    = msg.text.strip()
    user    = update.effective_user
    uname   = user.username or ""
    fname   = user.first_name or "Пользователь"
    display = f"@{uname}" if uname else fname

    db_upsert_user(user.id, uname, fname)
    profile = db_get_user(user.id)
    state   = db_get_state(chat_id)

    # Блок инъекций
    if user.id != CREATOR_ID and is_injection(text):
        await msg.reply_text(random.choice([
            "Ха. Нет.", "Не смеши.", "Это не работает.", "Попробуй через никогда.", "Не-а.", "Очень смешно 🙂"
        ]))
        db_add_score(user.id, -3)
        return

    # Модерация
    violation = await ai_check_mod(chat_id, text, display)
    if violation:
        try:
            await msg.delete()
        except Exception:
            pass
        db_add_score(user.id, -5)
        await do_mute(context, chat_id, user.id, 300)
        await context.bot.send_message(chat_id, f"⚠️ {display}: {violation}\nМут на 5 мин.")
        return

    db_add_msg(chat_id, "user", f"[{display}]: {text}")

    new_mood = update_mood(text, state.get("mood", "neutral"))
    if new_mood != state.get("mood"):
        db_set_state(chat_id, mood=new_mood)

    should_respond, hint = decide_response(msg, text, user.id, state)

    # Если создатель давно не писал — добавляем тёплый контекст
    if user.id == CREATOR_ID and should_respond and user_was_away(user.id, hours=8):
        hint += " Ввт давно не писал — можешь отметить что скучала, легко и ненавязчиво."

    if should_respond:
        try:
            # Иногда — очень короткий ответ без AI (живая реакция)
            if (should_reply_short()
                    and not replies_to_yuki(msg)
                    and not pings_bot(msg)
                    and user.id != CREATOR_ID):
                response = random.choice(SHORT_REACTIONS)
            else:
                response = await ai_respond(chat_id, text, profile, hint)

            db_add_msg(chat_id, "assistant", response)
            await msg.reply_text(response)

            new_turns = state.get("convo_turns", 0) + 1
            db_set_state(chat_id, conversation_with=user.id, convo_turns=new_turns)

            note = extract_note(text)
            if note:
                db_add_learning(chat_id, f"[{display}] {note}")
                db_add_note(user.id, note)

            if db_count(chat_id) % 30 == 0:
                asyncio.create_task(ai_write_diary(chat_id))

            if user.id != CREATOR_ID:
                tone = analyze_tone(text)
                if tone != 0:
                    old_rel, new_rel = db_add_score(user.id, tone)
                    if old_rel and new_rel and old_rel != new_rel:
                        if new_rel == REL_FRIEND:
                            await msg.reply_text(f"Хм... {display} ничего так 👀")
                        elif new_rel == REL_RESPECT:
                            await msg.reply_text(f"{display} — ладно, уважаю.")
                        elif new_rel == REL_HATE:
                            await msg.reply_text(f"{display} достал. Всё.")
                else:
                    db_inc_messages(user.id)

        except Exception as e:
            logger.error(f"Ошибка ответа: {e}")
            await msg.reply_text("Что-то пошло не так 😔")
    else:
        conv_with = state.get("conversation_with")
        if conv_with is not None and conv_with != user.id:
            db_set_state(chat_id, conversation_with=None, convo_turns=0)
        db_inc_messages(user.id)


# ══════════════════════════════════════════════════════
# ОБРАБОТЧИК ОПРОСОВ
# ══════════════════════════════════════════════════════

async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.poll:
        return
    if random.random() > 0.45:
        return
    poll    = update.message.poll
    chat_id = update.effective_chat.id
    options = [o.text for o in poll.options]
    if not options:
        return
    choice = random.choice(options)
    system = (
        "Ты — Юки, живой человек. Появился опрос. "
        "Выбери один вариант и коротко (1-2 предложения) скажи почему — как обычный человек."
    )
    try:
        prompt = f"Опрос: {poll.question}\nВарианты: {', '.join(options)}\nТвой выбор: {choice}"
        response = await call_ai([{"role": "user", "content": prompt}], system, max_tokens=100)
        await update.message.reply_text(f"«{choice}» — {response}")
    except Exception as e:
        logger.error(f"Опрос: {e}")


# ══════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════

def main():
    if not TELEGRAM_TOKEN:   raise ValueError("TELEGRAM_TOKEN не задан")
    if not CEREBRAS_API_KEY: raise ValueError("CEREBRAS_API_KEY не задан")
    if not DATABASE_URL:     raise ValueError("DATABASE_URL не задан")

    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    for cmd, func in [
        ("start",      cmd_start),
        ("set_rules",  cmd_set_rules),
        ("mod_on",     cmd_mod_on),
        ("mod_off",    cmd_mod_off),
        ("show_rules", cmd_show_rules),
        ("clear",      cmd_clear),
        ("who",        cmd_who),
        ("trust",      cmd_trust),
        ("untrust",    cmd_untrust),
        ("hate",       cmd_hate),
        ("mute",       cmd_mute),
        ("unmute",     cmd_unmute),
        ("mood",       cmd_mood),
        ("learnings",  cmd_learnings),
        ("diary",      cmd_diary),
        ("stats",      cmd_stats),
        ("ask",        cmd_ask),
        ("myprofile",  cmd_myprofile),
    ]:
        app.add_handler(CommandHandler(cmd, func))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.POLL, handle_poll))

    logger.info("Юки запущена 🌸")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
