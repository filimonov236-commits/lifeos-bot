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
from telegram import Update
from telegram.ext import (
    Application,
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
    "задачі":           "65ec228f-5e9f-831f-a2dd-81bcf445f6e9",  # Habit Tracker (template)
    "тренування":       "375c228f-5e9f-813a-b77e-ce1093e38893",
    "проекти":          "375c228f-5e9f-8192-9cb3-e24ed61e799f",
    "звички":           "375c228f-5e9f-81e1-ab37-cb11eecbd78a",
    "люди":             "375c228f-5e9f-81f4-91f4-d3b84d05db6a",
    "monthly_overview": "facc228f-5e9f-83b5-9221-01e85e270842",
    "the_streak":       "bbac228f-5e9f-82a7-b2b1-81ad2afeb4df",
    # ── Фінанси ─────────────────────────────────────────────────────────────
    "expenses":         "376c228f-5e9f-8186-b4ad-d8b4205e7c16",
    "budget_categories":"376c228f-5e9f-818b-8e82-d1dce4cec889",
    "income":           "376c228f-5e9f-8100-8a76-eb860867d186",
}

# Прив'язка назви категорії → page_id в Budget Categories
BUDGET_CAT_IDS: dict[str, str] = {
    "Їжа":         "376c228f-5e9f-813f-a157-fe595bd2700f",
    "Транспорт":   "376c228f-5e9f-81dd-ba8d-dbf6b3326258",
    "Дім":         "376c228f-5e9f-819a-8a85-f206df300101",
    "Здоров'я":    "376c228f-5e9f-8183-afc5-ede03aa1e25e",
    "Розваги":     "376c228f-5e9f-8180-8149-d2eec4f331ac",
    "Одяг":        "376c228f-5e9f-81b0-9e09-fd4fad4ac5d0",
    "Інше":        "376c228f-5e9f-81b4-9e8b-d1f9ec9b394a",
    "Комунальні":  "376c228f-5e9f-8132-8a24-c41c592528f7",
    "Інструменти": "376c228f-5e9f-814e-afac-d4f3709fed03",
}

# Ключові слова для визначення категорії витрати
EXPENSE_CAT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Їжа",         ["їжа", "продукти", "ресторан", "кафе", "кава", "обід",
                     "вечеря", "сніданок", "піца", "суші", "фастфуд", "бар", "groceries"]),
    ("Транспорт",   ["таксі", "метро", "бус", "автобус", "авто", "бензин",
                     "пальне", "поїзд", "маршрутка", "uber", "bolt", "укрзалізниця"]),
    ("Дім",         ["ремонт", "меблі", "побут", "квартира", "оренда",
                     "будинок", "техніка", "посуд", "дім", "хата"]),
    ("Здоров'я",    ["ліки", "лікар", "аптека", "клініка", "лікарня",
                     "аналізи", "здоров", "зуб", "стоматолог", "спорт"]),
    ("Розваги",     ["кіно", "концерт", "ігри", "гра", "розваги",
                     "відпочин", "парк", "зоопарк", "netflix", "spotify"]),
    ("Одяг",        ["одяг", "взуття", "куртка", "штани", "сорочка",
                     "шопінг", "кросівки", "сукня", "одягу"]),
    ("Комунальні",  ["комуналка", "електрика", "вода", "газ", "інтернет",
                     "зв'язок", "телефон", "комунальні"]),
    ("Інструменти", ["підписка", "програм", "сервіс", "курс", "онлайн",
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
        exp_cat  = detect_expense_category(text)
        cat_id   = BUDGET_CAT_IDS.get(exp_cat, BUDGET_CAT_IDS["Інше"])
        props = {
            "Description":   title_prop(text),
            "Date":          date_prop(date_iso or today_iso()),
            "Budget Category": relation_prop(cat_id),
        }
        if amount:
            props["Amount"] = number_prop(amount)
        ok = notion_create("expenses", props)
        return ok, f"💸 Витрата [{exp_cat}]"

    if category == "дохід":
        props = {
            "Income": title_prop(text),
            "Date":   date_prop(date_iso or today_iso()),
        }
        if amount:
            props["Amount"] = number_prop(amount)
        return notion_create("income", props), label

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


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = today_iso()
    results = notion_query(
        "задачі",
        filter_obj={"property": "Date", "date": {"equals": today}},
    )

    d = date.fromisoformat(today)
    date_label = f"{d.day} {MONTHS_UK_GEN[d.month]}"

    if not results:
        await update.message.reply_text(
            f"📅 Запис на {date_label} ще не створено.\n"
            "Відкрий Habit Tracker у Notion щоб додати запис."
        )
        return

    entry = results[0]
    props = entry.get("properties", {})

    lines = [f"📅 Звички на {date_label}:\n"]
    done = 0
    for habit, display in zip(HABITS_LIST, HABITS_DISPLAY):
        checked = props.get(habit, {}).get("checkbox", False)
        icon = "✅" if checked else "❌"
        lines.append(f"{icon} {display}")
        if checked:
            done += 1

    pct = done * 10
    filled = "⬛" * done + "⬜" * (10 - done)
    lines.append(f"\n{filled} {pct}%")

    notes_blocks = props.get("Notes", {}).get("rich_text", [])
    if notes_blocks:
        note_text = "".join(b.get("plain_text", "") for b in notes_blocks)
        if note_text:
            lines.append(f"📝 {note_text}")

    await update.message.reply_text("\n".join(lines))


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now         = datetime.now()
    month_start = f"{now.year}-{now.month:02d}-01"
    next_m      = now.month % 12 + 1
    next_y      = now.year + (1 if now.month == 12 else 0)
    month_end   = f"{next_y}-{next_m:02d}-01"

    month_filter = {
        "and": [
            {"property": "Date", "date": {"on_or_after": month_start}},
            {"property": "Date", "date": {"before":      month_end}},
        ]
    }

    expenses_rows = notion_query("expenses", filter_obj=month_filter)
    income_rows   = notion_query("income",   filter_obj={
        "and": [
            {"property": "Date", "date": {"on_or_after": month_start}},
            {"property": "Date", "date": {"before":      month_end}},
        ]
    })

    expense = sum(
        (r.get("properties", {}).get("Amount", {}).get("number") or 0)
        for r in expenses_rows
    )
    income = sum(
        (r.get("properties", {}).get("Amount", {}).get("number") or 0)
        for r in income_rows
    )

    balance = income - expense
    sign    = "+" if balance >= 0 else ""
    emoji   = "💪" if balance >= 0 else "😬"

    await update.message.reply_text(
        f"💰 Баланс за {MONTHS_UK[now.month]} {now.year}:\n\n"
        f"💚 Доходи:  {income:>10,.0f} грн\n"
        f"🔴 Витрати: {expense:>10,.0f} грн\n"
        f"{'─' * 26}\n"
        f"{emoji} Баланс:  {sign}{balance:>9,.0f} грн"
    )


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now()
    rows = notion_query("budget_categories")

    if not rows:
        await update.message.reply_text("📊 Budget Categories порожній.")
        return

    lines = [f"📊 Бюджет за {MONTHS_UK[now.month]} {now.year}:\n"]
    total_budget = total_actual = 0.0

    for item in rows:
        props = item.get("properties", {})

        # Category name
        cat = "".join(
            b.get("plain_text", "")
            for b in props.get("Category", {}).get("title", [])
        )

        budget = props.get("Budget", {}).get("number") or 0.0

        # Actual — rollup повертає число
        actual_raw = props.get("Actual", {}).get("rollup", {})
        actual = actual_raw.get("number") or 0.0

        diff = budget - actual
        diff_str = f"+{diff:,.0f}" if diff >= 0 else f"{diff:,.0f}"
        status = "🟢" if diff >= 0 else "🔴"

        lines.append(
            f"{status} {cat}: витрачено {actual:,.0f} / ліміт {budget:,.0f} грн  ({diff_str} грн)"
        )
        total_budget += budget
        total_actual += actual

    total_diff = total_budget - total_actual
    diff_str   = f"+{total_diff:,.0f}" if total_diff >= 0 else f"{total_diff:,.0f}"
    lines.append(f"\n{'─' * 30}")
    lines.append(f"Всього: {total_actual:,.0f} / {total_budget:,.0f} грн  ({diff_str} грн)")

    await update.message.reply_text("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Notion Life OS Bot*\n\n"
        "Просто напиши або надішли голосове — я визначу куди записати:\n\n"
        "💡 *Ідеї*\n"
        "  ідея, думка, що якщо\n\n"
        "💸 *Витрата* → Expenses\n"
        "  купив, заплатив, потратив, витрата\n"
        "  Категорія визначається автоматично:\n"
        "  їжа / транспорт / дім / здоров'я / розваги / одяг / комунальні / інструменти\n\n"
        "💚 *Дохід* → Source of Income\n"
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
        "/today — звички на сьогодні\n"
        "/balance — доходи / витрати / баланс за місяць\n"
        "/budget — бюджет по категоріях\n"
        "/help — ця довідка",
        parse_mode="Markdown",
    )


# ─── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("today",   cmd_today))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("budget",  cmd_budget))
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
