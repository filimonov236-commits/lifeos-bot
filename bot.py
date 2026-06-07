#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion Life OS — Telegram Bot
Записує думки, задачі, витрати, тренування у Notion одним повідомленням.
"""

import os
import re
import logging
import tempfile
from datetime import datetime, date, timedelta

import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
PORT           = int(os.environ.get("PORT", 8000))
RENDER_URL     = os.environ.get("RENDER_EXTERNAL_URL", "")

NOTION_HEADERS = {
    "Authorization":  f"Bearer {NOTION_TOKEN}",
    "Content-Type":   "application/json",
    "Notion-Version": "2022-06-28",
}
NOTION_API = "https://api.notion.com/v1"

# ─── Database IDs ─────────────────────────────────────────────────────────────
DB = {
    "ідеї":             "375c228f-5e9f-81c4-9a3b-dc64cf71186a",
    "задачі":           "65ec228f-5e9f-831f-a2dd-81bcf445f6e9",  # Habit Tracker
    "тренування":       "375c228f-5e9f-813a-b77e-ce1093e38893",
    "проекти":          "375c228f-5e9f-8192-9cb3-e24ed61e799f",
    "звички":           "375c228f-5e9f-81e1-ab37-cb11eecbd78a",
    "люди":             "375c228f-5e9f-81f4-91f4-d3b84d05db6a",
    # ── Бюджет ──────────────────────────────────────────────────────────────
    "транзакції":       "5cac228f-5e9f-8244-b4c9-81258e9dc075",
    "місячний_бюджет":  "ca2c228f-5e9f-83e9-bbf4-8198e3305cc7",
    "доходи_витрати":   "1eec228f-5e9f-83cd-88cb-019140bf89ac",
    "рахунки":          "7edc228f-5e9f-834f-ab9c-8168d45ca289",
    "аналіз":           "f39c228f-5e9f-829f-81ee-81b3c0bf9a9c",
}

# IDs типу транзакції: Дохід / Витрата
INCOME_TYPE_ID  = "d18c228f-5e9f-82a5-9647-81794899cfce"
EXPENSE_TYPE_ID = "de1c228f-5e9f-82f4-b6e4-019eb100cfc1"

# IDs рахунків
ACCOUNT_IDS: dict[str, str] = {
    "Готівка":    "f00c228f-5e9f-828e-975e-8128ec963fd4",
    "ПриватБанк": "53fc228f-5e9f-8324-be11-81d10249b8ec",
    "Monobank":   "63ac228f-5e9f-83b0-b5df-81e4a34baec7",
}

# Прив'язка назви категорії → page_id в Місячний бюджет
BUDGET_CAT_IDS: dict[str, str] = {
    "Їжа та кафе":     "623c228f-5e9f-8263-bb78-811858e16b2d",
    "Транспорт":       "23bc228f-5e9f-82eb-9bbe-013c3852b35d",
    "Комунальні":      "275c228f-5e9f-82cb-8fb0-019c7f868706",
    "Розваги та спорт":"815c228f-5e9f-82d2-9e51-017184c0b53f",
    "Одяг":            "79dc228f-5e9f-8378-b7ff-01a6f3522b3e",
    "Підписки":        "2e6c228f-5e9f-828b-895c-81db3ac89365",
    "Зарплата":        "20ec228f-5e9f-83f3-bc94-810e7d64e19a",
    "Інше":            "7bfc228f-5e9f-8323-b477-01ce25868ae8",
}

# Ключові слова для визначення категорії витрати
EXPENSE_CAT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Їжа та кафе",     ["їжа", "продукти", "ресторан", "кафе", "кава", "обід",
                         "вечеря", "сніданок", "піца", "суші", "фастфуд", "бар", "groceries"]),
    ("Транспорт",       ["таксі", "метро", "бус", "автобус", "авто", "бензин",
                         "пальне", "поїзд", "маршрутка", "uber", "bolt", "укрзалізниця"]),
    ("Комунальні",      ["комуналка", "електрика", "вода", "газ", "інтернет",
                         "зв'язок", "телефон", "комунальні"]),
    ("Розваги та спорт",["кіно", "концерт", "ігри", "гра", "розваги", "спорт",
                         "відпочин", "парк", "зоопарк", "netflix", "spotify"]),
    ("Одяг",            ["одяг", "взуття", "куртка", "штани", "сорочка",
                         "шопінг", "кросівки", "сукня", "одягу"]),
    ("Підписки",        ["підписка", "програм", "сервіс", "курс", "онлайн",
                         "software", "додаток", "app", "хостинг"]),
]

# ─── Voice (optional) ─────────────────────────────────────────────────────────
try:
    import speech_recognition as sr
    from pydub import AudioSegment
    VOICE_ENABLED = True
    logger.info("Voice recognition: OK")
except ImportError:
    VOICE_ENABLED = False
    logger.warning("Voice recognition: disabled (install SpeechRecognition + pydub)")

# ─── Dateparser (optional) ────────────────────────────────────────────────────
try:
    import dateparser
    DATEPARSER_ENABLED = True
    logger.info("Dateparser: OK")
except ImportError:
    DATEPARSER_ENABLED = False
    logger.warning("Dateparser: disabled (install dateparser)")

# ─── Локалізація ──────────────────────────────────────────────────────────────
MONTHS_UK = [
    "", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень",
]
MONTHS_UK_GEN = [
    "", "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]

DAYS_UA: dict[str, int] = {
    "понеділок": 0, "понеділка": 0,
    "вівторок":  1, "вівторка":  1,
    "середу":    2, "середа":    2, "середи":    2,
    "четвер":    3, "четверга":  3,
    "п'ятницю":  4, "п'ятниця":  4, "п'ятниці":  4,
    "пятницю":   4, "пятниця":   4, "пятниці":   4,
    "суботу":    5, "субота":    5, "суботи":    5,
    "неділю":    6, "неділя":    6, "неділі":    6,
}

CATEGORY_LABELS: dict[str, str] = {
    "ідеї":    "💡 Ідея",
    "витрата": "💸 Витрата",
    "дохід":   "💚 Дохід",
    "задачі":  "✅ Задача",
    "тренування": "🏋️ Тренування",
    "звички":  "🔁 Звичка",
}

# ─── Визначення категорії за ключовими словами ────────────────────────────────
KEYWORDS: list[tuple[str, list[str]]] = [
    ("ідеї",    ["ідея", "думка", "що якщо", "ідею", "мабуть варто"]),
    ("витрата", ["витрата", "купив", "заплатив", "потратив", "витратив",
                 "купила", "заплатила", "потратила", "заплатили", "витрачено"]),
    ("дохід",   ["дохід", "отримав", "заробив", "прийшло", "отримала",
                 "заробила", "надійшло", "нарахували"]),
    ("задачі",  ["задача", "задачу", "треба", "зробити", "нагадай",
                 "завдання", "to do", "нагадати", "не забути"]),
    ("тренування", ["тренування", "тренувався", "тренувалась", "зробив зарядку",
                    "пробіг", "пробігла", "зарядка", "качав", "підтягування", "пробіжка"]),
    ("звички",  ["звичка", "звички", "звичку", "моя звичка"]),
]

# Розбивка повідомлення на частини по сполучниках
_SPLIT_RE = re.compile(
    r'(?<!\w)(?:і\s+ще|також|плюс|і|та|ще)(?!\w)',
    re.IGNORECASE | re.UNICODE,
)


def split_message(text: str) -> list[str]:
    parts = _SPLIT_RE.split(text)
    cleaned = [p.strip() for p in parts if p.strip() and len(p.strip()) > 2]
    return cleaned if len(cleaned) > 1 else [text]


def detect_category(text: str) -> str | None:
    t = text.lower()
    for category, keywords in KEYWORDS:
        if any(kw in t for kw in keywords):
            return category
    return None


def detect_expense_category(text: str) -> str:
    t = text.lower()
    for cat_name, keywords in EXPENSE_CAT_KEYWORDS:
        if any(kw in t for kw in keywords):
            return cat_name
    return "Інше"


def parse_amount(text: str) -> float | None:
    matches = re.findall(r"\b\d[\d\s]*(?:[.,]\d{1,2})?\b", text)
    for m in matches:
        clean = m.strip().replace(" ", "").replace(",", ".")
        try:
            val = float(clean)
            if val > 0:
                return val
        except ValueError:
            continue
    return None


def parse_date_from_text(text: str) -> str | None:
    t = text.lower()
    today = date.today()

    if "сьогодні" in t:
        return today.isoformat()
    if "завтра" in t:
        return (today + timedelta(days=1)).isoformat()

    m = re.search(r'через\s+(\d+)\s+(день|дні|днів|тижні|тижнів|тижня)', t)
    if m:
        n    = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(weeks=n) if "тижн" in unit else timedelta(days=n)
        return (today + delta).isoformat()

    for day_name, weekday in DAYS_UA.items():
        if day_name in t:
            days_ahead = (weekday - today.weekday()) % 7 or 7
            return (today + timedelta(days=days_ahead)).isoformat()

    if DATEPARSER_ENABLED:
        try:
            parsed = dateparser.parse(
                text,
                languages=["uk", "ru"],
                settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
            )
            if parsed and parsed.date() != today:
                return parsed.date().isoformat()
        except Exception:
            pass

    return None


def format_date_uk(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_UK_GEN[d.month]}"


# ─── Notion helpers ───────────────────────────────────────────────────────────
def today_iso() -> str:
    return date.today().isoformat()


def title_prop(text: str) -> dict:
    return {"title": [{"text": {"content": text[:2000]}}]}


def date_prop(iso: str) -> dict:
    return {"date": {"start": iso}}


def select_prop(name: str) -> dict:
    return {"select": {"name": name}}


def number_prop(val: float) -> dict:
    return {"number": val}


def relation_prop(page_id: str) -> dict:
    return {"relation": [{"id": page_id}]}


def get_title(page: dict) -> str:
    for p in page.get("properties", {}).values():
        if p.get("type") == "title":
            return "".join(b.get("plain_text", "") for b in p.get("title", []))
    return "(без назви)"


def notion_create(db_key: str, properties: dict) -> bool:
    resp = requests.post(
        f"{NOTION_API}/pages",
        headers=NOTION_HEADERS,
        json={"parent": {"database_id": DB[db_key]}, "properties": properties},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error(f"Notion create error {resp.status_code}: {resp.text[:300]}")
    return resp.status_code == 200


def notion_create_page(db_key: str, properties: dict) -> dict | None:
    resp = requests.post(
        f"{NOTION_API}/pages",
        headers=NOTION_HEADERS,
        json={"parent": {"database_id": DB[db_key]}, "properties": properties},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error(f"Notion create error {resp.status_code}: {resp.text[:300]}")
        return None
    return resp.json()


def notion_get_page(page_id: str) -> dict | None:
    resp = requests.get(
        f"{NOTION_API}/pages/{page_id}",
        headers=NOTION_HEADERS,
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error(f"Notion get page error: {resp.text[:200]}")
        return None
    return resp.json()


def notion_patch_page(page_id: str, properties: dict) -> dict | None:
    resp = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": properties},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error(f"Notion patch error: {resp.text[:200]}")
        return None
    return resp.json()


def notion_query(db_key: str, filter_obj: dict | None = None,
                 sorts: list | None = None) -> list:
    body: dict = {"page_size": 100}
    if filter_obj:
        body["filter"] = filter_obj
    if sorts:
        body["sorts"] = sorts
    resp = requests.post(
        f"{NOTION_API}/databases/{DB[db_key]}/query",
        headers=NOTION_HEADERS,
        json=body,
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error(f"Notion query error: {resp.text[:200]}")
        return []
    return resp.json().get("results", [])


# ─── Створення одного запису ──────────────────────────────────────────────────
def create_single_record(text: str, category: str, date_iso: str | None) -> tuple[bool, str]:
    amount = parse_amount(text)
    label  = CATEGORY_LABELS.get(category, category)

    if category == "ідеї":
        props: dict = {"Назва": title_prop(text), "Статус": select_prop("🆕 Нова")}
        if date_iso:
            props["Дата"] = date_prop(date_iso)
        return notion_create("ідеї", props), label

    if category == "витрата":
        exp_cat = detect_expense_category(text)
        cat_id  = BUDGET_CAT_IDS.get(exp_cat, BUDGET_CAT_IDS["Інше"])
        props = {
            "Деталі":    title_prop(text),
            "Дата":      date_prop(date_iso or today_iso()),
            "Тип":       relation_prop(EXPENSE_TYPE_ID),
            "Категорія": relation_prop(cat_id),
        }
        if amount:
            props["Сума"] = number_prop(amount)
        ok = notion_create("транзакції", props)
        return ok, f"💸 Витрата [{exp_cat}]"

    if category == "дохід":
        props = {
            "Деталі": title_prop(text),
            "Дата":   date_prop(date_iso or today_iso()),
            "Тип":    relation_prop(INCOME_TYPE_ID),
        }
        if amount:
            props["Сума"] = number_prop(amount)
        return notion_create("транзакції", props), label

    if category == "задачі":
        d = date_iso or today_iso()
        props = {
            "Name": title_prop(d),
            "Date": date_prop(d),
        }
        if text:
            props["Notes"] = {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}
        return notion_create("задачі", props), label

    if category == "тренування":
        props = {"Тип": title_prop(text), "Дата": date_prop(date_iso or today_iso())}
        return notion_create("тренування", props), label

    if category == "звички":
        props = {
            "Звичка":   title_prop(text),
            "Дата":     date_prop(date_iso or today_iso()),
            "Виконано": {"checkbox": True},
        }
        return notion_create("звички", props), label

    return False, label


def _record_line(text: str, label: str, date_iso: str | None, amount: float | None) -> str:
    date_part   = f" — {format_date_uk(date_iso)}" if date_iso else ""
    amount_part = f" ({amount:,.0f} грн)"          if amount   else ""
    short       = text[:60] + ("…" if len(text) > 60 else "")
    return f"{label}: «{short}»{date_part}{amount_part}"


# ─── Обробка повідомлення ─────────────────────────────────────────────────────
async def process_text(update: Update, text: str) -> None:
    parts = split_message(text)
    lines: list[str] = []
    any_recognized = False

    for part in parts:
        category = detect_category(part)
        if not category:
            if len(parts) > 1:
                continue
            await update.message.reply_text(
                "🤔 Не зрозумів куди записати.\n"
                "Введи /help щоб побачити всі ключові слова."
            )
            return

        any_recognized = True
        date_iso = parse_date_from_text(part)
        amount   = parse_amount(part)
        ok, label = create_single_record(part, category, date_iso)

        if ok:
            lines.append(f"✅ {_record_line(part, label, date_iso, amount)}")
        else:
            lines.append(f"❌ Помилка запису: «{part[:50]}»")

    if not any_recognized:
        await update.message.reply_text(
            "🤔 Не зрозумів жодної дії.\n"
            "Введи /help щоб побачити всі ключові слова."
        )
        return

    await update.message.reply_text("\n".join(lines))


# ─── Handlers ─────────────────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await process_text(update, update.message.text.strip())


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not VOICE_ENABLED:
        await update.message.reply_text(
            "❌ Голосові повідомлення недоступні.\n"
            "Потрібні пакети: SpeechRecognition, pydub, ffmpeg."
        )
        return

    await update.message.reply_text("🎤 Розпізнаю голос...")
    ogg_path = wav_path = None

    try:
        tg_file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            ogg_path = f.name
        await tg_file.download_to_drive(ogg_path)

        wav_path = ogg_path.replace(".ogg", ".wav")
        AudioSegment.from_ogg(ogg_path).export(wav_path, format="wav")

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)

        text = recognizer.recognize_google(audio_data, language="uk-UA")
        await update.message.reply_text(f"🎤 Розпізнано: «{text}»")
        await process_text(update, text)

    except sr.UnknownValueError:
        await update.message.reply_text("❌ Не вдалося розпізнати. Спробуй чіткіше або текстом.")
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("❌ Помилка обробки аудіо.")
    finally:
        for path in [ogg_path, wav_path]:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "друже"
    await update.message.reply_text(
        f"Привіт, {name}! 👋\n\n"
        "Я твій асистент для Notion Life OS.\n"
        "Надсилай текст або голосове — запишу у потрібну базу.\n\n"
        "📋 /help — всі команди і ключові слова"
    )


HABITS_LIST = [
    "Sleep 7-8 hours",
    "Eat healthy meals",
    "Exercise 30 minutes ",   # trailing space — matches DB property name
    "Journal & self-reflect ", # trailing space
    "No porn/alcohol",
    "Plan tomorrow's tasks",
    "Read 30 minutes",
    "Social media ≤ 90min",   # space before 90
    "Study ≥ 2 hours",        # space before 2
    "Drink 2L water ",         # trailing space
]

HABITS_DISPLAY = [
    "Sleep 7-8 hours 💤",
    "Eat healthy meals 🥗",
    "Exercise 30 minutes 🏋️",
    "Journal & self-reflect 🖋️",
    "No porn/alcohol 🚫",
    "Plan tomorrow's tasks 📋",
    "Read 30 minutes 📖",
    "Social media ≤90min 📱",
    "Study ≥2 hours 💻",
    "Drink 2L water 💧",
]


def _habits_text(props: dict, date_label: str) -> str:
    done = sum(1 for h in HABITS_LIST if props.get(h, {}).get("checkbox", False))
    pct  = done * 10
    bar  = "⬛" * done + "⬜" * (10 - done)
    text = f"📅 Звички на {date_label}:\n{bar} {pct}%  ({done}/10)"
    notes = props.get("Notes", {}).get("rich_text", [])
    if notes:
        note_text = "".join(b.get("plain_text", "") for b in notes)
        if note_text:
            text += f"\n📝 {note_text}"
    return text


def _habits_keyboard(props: dict, page_id: str) -> InlineKeyboardMarkup:
    rows = []
    for i, (habit, display) in enumerate(zip(HABITS_LIST, HABITS_DISPLAY)):
        checked = props.get(habit, {}).get("checkbox", False)
        icon = "✅" if checked else "◻️"
        rows.append([InlineKeyboardButton(
            f"{icon} {display}",
            callback_data=f"h_{i}_{page_id}",
        )])
    return InlineKeyboardMarkup(rows)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = today_iso()
    d = date.fromisoformat(today)
    date_label = f"{d.day} {MONTHS_UK_GEN[d.month]}"

    results = notion_query(
        "задачі",
        filter_obj={"property": "Date", "date": {"equals": today}},
    )

    if results:
        entry = results[0]
    else:
        entry = notion_create_page("задачі", {
            "Name": title_prop(today),
            "Date": date_prop(today),
        })
        if not entry:
            await update.message.reply_text("❌ Не вдалося створити запис на сьогодні.")
            return

    page_id = entry["id"]
    props   = entry.get("properties", {})

    await update.message.reply_text(
        _habits_text(props, date_label),
        reply_markup=_habits_keyboard(props, page_id),
    )


async def habit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_", 2)   # h_{idx}_{page_id}
    if len(parts) != 3 or parts[0] != "h":
        return

    idx     = int(parts[1])
    page_id = parts[2]
    habit   = HABITS_LIST[idx]

    page = notion_get_page(page_id)
    if not page:
        await query.answer("❌ Помилка читання", show_alert=True)
        return

    current = page.get("properties", {}).get(habit, {}).get("checkbox", False)
    updated = notion_patch_page(page_id, {habit: {"checkbox": not current}})
    if not updated:
        await query.answer("❌ Помилка оновлення", show_alert=True)
        return

    updated_props = updated.get("properties", {})
    date_str  = updated_props.get("Date", {}).get("date", {}).get("start", today_iso())
    d         = date.fromisoformat(date_str)
    date_label = f"{d.day} {MONTHS_UK_GEN[d.month]}"

    await query.edit_message_text(
        _habits_text(updated_props, date_label),
        reply_markup=_habits_keyboard(updated_props, page_id),
    )


def _month_range() -> tuple[str, str]:
    now    = datetime.now()
    start  = f"{now.year}-{now.month:02d}-01"
    next_m = now.month % 12 + 1
    next_y = now.year + (1 if now.month == 12 else 0)
    end    = f"{next_y}-{next_m:02d}-01"
    return start, end


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now()
    start, end = _month_range()

    rows = notion_query("транзакції", filter_obj={
        "and": [
            {"property": "Дата", "date": {"on_or_after": start}},
            {"property": "Дата", "date": {"before":      end}},
        ]
    })

    income = expense = 0.0
    for r in rows:
        props = r.get("properties", {})
        сума  = props.get("Сума", {}).get("number") or 0
        тип   = [rel["id"] for rel in props.get("Тип", {}).get("relation", [])]
        if INCOME_TYPE_ID in тип:
            income += сума
        elif EXPENSE_TYPE_ID in тип:
            expense += сума

    balance = income - expense
    sign  = "+" if balance >= 0 else ""
    emoji = "💪" if balance >= 0 else "😬"

    # Account balances from Рахунки
    account_rows = notion_query("рахунки")
    acc_lines = []
    for a in account_rows:
        props = a.get("properties", {})
        name = "".join(t.get("plain_text", "") for t in props.get("Назва рахунку", {}).get("title", []))
        bal  = (props.get("Balance", {}).get("formula") or {}).get("number") or 0
        acc_lines.append(f"  {name}: {bal:,.0f} грн")

    text = (
        f"💰 Баланс за {MONTHS_UK[now.month]} {now.year}:\n\n"
        f"💚 Доходи:  {income:>10,.0f} грн\n"
        f"🔴 Витрати: {expense:>10,.0f} грн\n"
        f"{'─' * 26}\n"
        f"{emoji} Баланс:  {sign}{balance:>9,.0f} грн"
    )
    if acc_lines:
        text += "\n\n🏦 Рахунки:\n" + "\n".join(acc_lines)

    await update.message.reply_text(text)


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now()
    start, end = _month_range()

    # Calculate actual spending per category directly from Транзакції
    expense_rows = notion_query("транзакції", filter_obj={
        "and": [
            {"property": "Дата",  "date":     {"on_or_after": start}},
            {"property": "Дата",  "date":     {"before":      end}},
            {"property": "Тип",   "relation": {"contains":    EXPENSE_TYPE_ID}},
        ]
    })

    cat_spent: dict[str, float] = {}
    for r in expense_rows:
        props = r.get("properties", {})
        сума  = props.get("Сума", {}).get("number") or 0
        for rel in props.get("Категорія", {}).get("relation", []):
            cid = rel["id"]
            cat_spent[cid] = cat_spent.get(cid, 0.0) + сума

    # Budget limits from Місячний бюджет
    budget_rows = notion_query("місячний_бюджет")
    if not budget_rows:
        await update.message.reply_text("📊 Місячний бюджет порожній.")
        return

    lines        = [f"📊 Бюджет за {MONTHS_UK[now.month]} {now.year}:\n"]
    total_budget = total_actual = 0.0

    for item in budget_rows:
        props  = item.get("properties", {})
        cat    = "".join(b.get("plain_text", "") for b in props.get("Name", {}).get("title", []))
        limit  = props.get("Amount", {}).get("number") or 0.0
        actual = cat_spent.get(item["id"], 0.0)

        diff     = limit - actual
        diff_str = f"+{diff:,.0f}" if diff >= 0 else f"{diff:,.0f}"
        status   = "🟢" if diff >= 0 else "🔴"

        lines.append(f"{status} {cat}: {actual:,.0f} / {limit:,.0f} грн  ({diff_str})")
        total_budget += limit
        total_actual += actual

    total_diff = total_budget - total_actual
    diff_str   = f"+{total_diff:,.0f}" if total_diff >= 0 else f"{total_diff:,.0f}"
    lines.append(f"\n{'─' * 30}")
    lines.append(f"Всього витрат: {total_actual:,.0f} / {total_budget:,.0f} грн  ({diff_str})")

    await update.message.reply_text("\n".join(lines))


async def cmd_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 10 transactions of the current month."""
    start, end = _month_range()
    now = datetime.now()

    rows = notion_query("транзакції", filter_obj={
        "and": [
            {"property": "Дата", "date": {"on_or_after": start}},
            {"property": "Дата", "date": {"before":      end}},
        ]
    }, sorts=[{"property": "Дата", "direction": "descending"}])

    if not rows:
        await update.message.reply_text(f"📭 Транзакцій за {MONTHS_UK[now.month]} поки немає.")
        return

    lines = [f"📋 Транзакції за {MONTHS_UK[now.month]} {now.year}:\n"]
    for r in rows[:10]:
        props = r.get("properties", {})
        деталі = "".join(t.get("plain_text", "") for t in props.get("Деталі", {}).get("title", []))
        сума   = props.get("Сума",  {}).get("number") or 0
        дата   = (props.get("Дата", {}).get("date") or {}).get("start", "")
        тип    = [rel["id"] for rel in props.get("Тип", {}).get("relation", [])]
        icon   = "💚" if INCOME_TYPE_ID in тип else "🔴"
        day    = дата[8:10] if len(дата) >= 10 else "?"
        lines.append(f"{icon} {day} — {деталі}: {сума:,.0f} грн")

    await update.message.reply_text("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Notion Life OS Bot*\n\n"
        "Просто напиши або надішли голосове — я визначу куди записати:\n\n"
        "💡 *Ідеї*\n"
        "  ідея, думка, що якщо\n\n"
        "💸 *Витрата* → Транзакції (тип: Витрата)\n"
        "  купив, заплатив, потратив, витрата\n"
        "  Категорія визначається автоматично:\n"
        "  їжа та кафе / транспорт / комунальні / розваги та спорт / одяг / підписки\n\n"
        "💚 *Дохід* → Транзакції (тип: Дохід)\n"
        "  отримав, заробив, прийшло, дохід\n\n"
        "✅ *Задача*\n"
        "  треба, зробити, нагадай, задача\n\n"
        "🏋️ *Тренування*\n"
        "  тренувався, пробіг, зарядка\n\n"
        "🔁 *Звичка*\n"
        "  звичка\n\n"
        "💰 Числа в тексті автоматично стають сумою\n"
        "📅 Дати: «завтра», «в п'ятницю», «через 3 дні», «сьогодні»\n\n"
        "─────────────────────\n"
        "⌨️ *Команди:*\n"
        "/today — звички на сьогодні (кнопки для відмітки)\n"
        "/balance — доходи / витрати / баланс + рахунки за місяць\n"
        "/budget — ліміти по категоріях vs фактичні витрати\n"
        "/transactions — останні 10 транзакцій за місяць\n"
        "/help — ця довідка",
        parse_mode="Markdown",
    )


# ─── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("today",   cmd_today))
    app.add_handler(CommandHandler("balance",      cmd_balance))
    app.add_handler(CommandHandler("budget",       cmd_budget))
    app.add_handler(CommandHandler("transactions", cmd_transactions))
    app.add_handler(CallbackQueryHandler(habit_callback, pattern=r"^h_\d+_.+"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/{TELEGRAM_TOKEN}"
        logger.info(f"🌐 Webhook mode → {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=webhook_url,
        )
    else:
        logger.info("🔄 Polling mode (local)...")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
