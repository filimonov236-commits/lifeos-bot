#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion Life OS — Telegram Bot
Записує думки, задачі, витрати, тренування у Notion одним повідомленням.
"""

import os
import re
import asyncio
import logging
import tempfile
from datetime import datetime, date, timedelta

import requests
from aiohttp import web
from dotenv import load_dotenv
from telegram import (
    Update, BotCommand,
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
)
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
MONO_TOKEN      = os.environ.get("MONO_TOKEN", "")
MONO_ACCOUNT    = os.environ.get("MONO_ACCOUNT", "")   # ID рахунку чорної картки
PORT            = int(os.environ.get("PORT", 8000))
RENDER_URL      = os.environ.get("RENDER_EXTERNAL_URL", "")
CHAT_ID         = int(os.environ.get("CHAT_ID", 0))

# chat_ids for Monobank notifications (populated at runtime + from env)
_chat_ids: set[int] = {CHAT_ID} if CHAT_ID else set()

# Deduplicates Monobank events: Mono sends hold=True then hold=False for the same tx
_seen_mono_ids: set[str] = set()

# Persistent keyboard — always visible above the input field
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["🗂 Меню"]],
    resize_keyboard=True,
    is_persistent=True,
)

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

# Категорії витрат з іконками
EXPENSE_CATEGORIES: list[tuple[str, str]] = [
    ("Їжа та кафе",      "🍕"),
    ("Транспорт",        "🚌"),
    ("Комунальні",       "🏠"),
    ("Розваги та спорт", "🎭"),
    ("Одяг",             "👕"),
    ("Підписки",         "📱"),
    ("Перекази",         "💸"),
    ("Інше",             "📦"),
]
EXPENSE_CAT_ICONS = dict(EXPENSE_CATEGORIES)

# MCC-коди → категорія витрат (Monobank надає MCC для кожної транзакції)
MCC_TO_CATEGORY: dict[int, str] = {
    # Їжа та кафе
    5411: "Їжа та кафе", 5412: "Їжа та кафе", 5422: "Їжа та кафе",
    5441: "Їжа та кафе", 5451: "Їжа та кафе", 5462: "Їжа та кафе",
    5499: "Їжа та кафе", 5811: "Їжа та кафе", 5812: "Їжа та кафе",
    5813: "Їжа та кафе", 5814: "Їжа та кафе", 5921: "Їжа та кафе",
    # Транспорт
    4111: "Транспорт", 4112: "Транспорт", 4121: "Транспорт",
    4131: "Транспорт", 4411: "Транспорт", 4511: "Транспорт",
    4784: "Транспорт", 5541: "Транспорт", 5542: "Транспорт",
    7512: "Транспорт", 7513: "Транспорт",
    # Комунальні
    4811: "Комунальні", 4812: "Комунальні", 4813: "Комунальні",
    4814: "Комунальні", 4899: "Комунальні", 4900: "Комунальні",
    # Розваги та спорт
    5941: "Розваги та спорт", 7011: "Розваги та спорт",
    7832: "Розваги та спорт", 7922: "Розваги та спорт",
    7941: "Розваги та спорт", 7991: "Розваги та спорт",
    7993: "Розваги та спорт", 7996: "Розваги та спорт",
    7997: "Розваги та спорт", 7999: "Розваги та спорт",
    # Одяг
    5600: "Одяг", 5611: "Одяг", 5621: "Одяг", 5631: "Одяг",
    5641: "Одяг", 5651: "Одяг", 5661: "Одяг", 5691: "Одяг", 5699: "Одяг",
    # Підписки / digital
    4816: "Підписки", 5045: "Підписки", 5734: "Підписки",
    7372: "Підписки", 7379: "Підписки",
    # Перекази
    4829: "Перекази", 6012: "Перекази", 6051: "Перекази",
    6211: "Перекази", 6540: "Перекази",
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
    ("Перекази",        ["переказ", "переказав", "переказала", "перевів", "перевела",
                         "надіслав", "надіслала", "відправив", "відправила", "переказати"]),
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
DAY_ABBR_UK = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

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
    results = []
    cursor  = None
    while True:
        body: dict = {"page_size": 100}
        if filter_obj:
            body["filter"] = filter_obj
        if sorts:
            body["sorts"] = sorts
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(
            f"{NOTION_API}/databases/{DB[db_key]}/query",
            headers=NOTION_HEADERS,
            json=body,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error(f"Notion query error: {resp.text[:200]}")
            return results
        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


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
        props = {
            "Деталі":  title_prop(text),
            "Дата":    date_prop(date_iso or today_iso()),
            "Примітка": {"rich_text": [{"text": {"content": f"витрата|{exp_cat}"}}]},
        }
        if amount:
            props["Сума"] = number_prop(amount)
        ok = notion_create("транзакції", props)
        return ok, f"💸 Витрата [{exp_cat}]"

    if category == "дохід":
        props = {
            "Деталі":  title_prop(text),
            "Дата":    date_prop(date_iso or today_iso()),
            "Примітка": {"rich_text": [{"text": {"content": "дохід|"}}]},
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
async def process_text(update: Update, text: str,
                       context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
    parts = split_message(text)

    # ── Single-part interactive flow ─────────────────────────────────────────
    if len(parts) == 1 and context is not None:
        part     = parts[0]
        category = detect_category(part)
        amount   = parse_amount(part)
        date_iso = parse_date_from_text(part)

        if category == "витрата":
            auto_cat = detect_expense_category(part)
            context.user_data["pending_tx"] = {
                "text": part, "amount": amount, "date_iso": date_iso,
            }
            amt = f"  —  {amount:,.0f} грн" if amount else ""
            await update.message.reply_text(
                f"💸 Витрата{amt}\nОберіть категорію:",
                reply_markup=_category_keyboard(auto_cat),
            )
            return

        if category is None and amount:
            context.user_data["pending_tx"] = {
                "text": part, "amount": amount, "date_iso": date_iso,
            }
            amt = f"  —  {amount:,.0f} грн"
            short = part[:50] + ("…" if len(part) > 50 else "")
            await update.message.reply_text(
                f"❓ «{short}»  {amt}\nЩо це?",
                reply_markup=_type_keyboard(),
            )
            return

    # ── Multi-part or non-financial: auto-process ─────────────────────────────
    lines: list[str] = []
    any_recognized   = False

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
    text     = update.message.text.strip()

    if text == "🗂 Меню":
        await cmd_menu(update, context)
        return

    awaiting = context.user_data.pop("awaiting", None)

    if awaiting == "витрата":
        amount   = parse_amount(text)
        date_iso = parse_date_from_text(text)
        auto_cat = detect_expense_category(text)
        context.user_data["pending_tx"] = {"text": text, "amount": amount, "date_iso": date_iso}
        amt = f"  —  {amount:,.0f} грн" if amount else ""
        await update.message.reply_text(
            f"💸 Витрата{amt}\nОберіть категорію:",
            reply_markup=_category_keyboard(auto_cat),
        )
        return

    if awaiting == "дохід":
        amount = parse_amount(text)
        props  = {
            "Деталі":   title_prop(text),
            "Дата":     date_prop(parse_date_from_text(text) or today_iso()),
            "Примітка": {"rich_text": [{"text": {"content": "дохід|"}}]},
        }
        if amount:
            props["Сума"] = number_prop(amount)
        ok      = notion_create("транзакції", props)
        amt_str = f" — {amount:,.0f} грн" if amount else ""
        await update.message.reply_text(
            f"✅ Дохід записано{amt_str}" if ok else "❌ Помилка запису в Notion"
        )
        return

    if awaiting == "ідеї":
        props = {"Назва": title_prop(text), "Статус": select_prop("🆕 Нова")}
        ok    = notion_create("ідеї", props)
        await update.message.reply_text(
            f"✅ Ідея записана: «{text[:60]}»" if ok else "❌ Помилка запису в Notion"
        )
        return

    await process_text(update, text, context)


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
        await process_text(update, text, context)

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
        "📋 /help — всі команди і ключові слова",
        reply_markup=MAIN_KEYBOARD,
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


def _habits_keyboard(props: dict, page_id: str,
                     from_week: bool = False) -> InlineKeyboardMarkup:
    prefix = "hw" if from_week else "h"
    rows = []
    for i, (habit, display) in enumerate(zip(HABITS_LIST, HABITS_DISPLAY)):
        checked = props.get(habit, {}).get("checkbox", False)
        icon = "✅" if checked else "◻️"
        rows.append([InlineKeyboardButton(
            f"{icon} {display}",
            callback_data=f"{prefix}_{i}_{page_id}",
        )])
    if from_week:
        rows.append([InlineKeyboardButton("📅 До тижня", callback_data="week_back")])
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


async def cmd_yesterday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    yesterday  = (date.today() - timedelta(days=1)).isoformat()
    d          = date.fromisoformat(yesterday)
    date_label = f"{d.day} {MONTHS_UK_GEN[d.month]}"

    results = notion_query(
        "задачі",
        filter_obj={"property": "Date", "date": {"equals": yesterday}},
    )

    if results:
        entry = results[0]
    else:
        entry = notion_create_page("задачі", {
            "Name": title_prop(yesterday),
            "Date": date_prop(yesterday),
        })
        if not entry:
            await update.message.reply_text("❌ Не вдалося створити запис на вчора.")
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

    parts = query.data.split("_", 2)   # h_{idx}_{page_id}  or  hw_{idx}_{page_id}
    if len(parts) != 3 or parts[0] not in ("h", "hw"):
        return

    from_week = parts[0] == "hw"
    idx       = int(parts[1])
    page_id   = parts[2]
    habit     = HABITS_LIST[idx]

    page = notion_get_page(page_id)
    if not page:
        await query.edit_message_text("❌ Не вдалося прочитати сторінку Notion.")
        return

    current = page.get("properties", {}).get(habit, {}).get("checkbox", False)
    updated = notion_patch_page(page_id, {habit: {"checkbox": not current}})
    if not updated:
        await query.edit_message_text("❌ Не вдалося оновити Notion.")
        return

    updated_props = updated.get("properties", {})
    date_str  = updated_props.get("Date", {}).get("date", {}).get("start", today_iso())
    d         = date.fromisoformat(date_str)
    date_label = f"{d.day} {MONTHS_UK_GEN[d.month]}"

    await query.edit_message_text(
        _habits_text(updated_props, date_label),
        reply_markup=_habits_keyboard(updated_props, page_id, from_week=from_week),
    )


def _get_week_data() -> tuple[str, InlineKeyboardMarkup]:
    today   = date.today()
    monday  = today - timedelta(days=today.weekday())
    week    = [monday + timedelta(days=i) for i in range(7)]

    results = notion_query("задачі", filter_obj={
        "and": [
            {"property": "Date", "date": {"on_or_after":  week[0].isoformat()}},
            {"property": "Date", "date": {"on_or_before": week[-1].isoformat()}},
        ]
    })
    entries: dict[str, dict] = {}
    for r in results:
        d = (r.get("properties", {}).get("Date", {}).get("date") or {}).get("start", "")
        if d:
            entries[d] = r

    # Header: handle month boundary
    s, e = week[0], week[-1]
    if s.month == e.month:
        header = f"📅 Тиждень {s.day}–{e.day} {MONTHS_UK_GEN[s.month]}:"
    else:
        header = (f"📅 Тиждень {s.day} {MONTHS_UK_GEN[s.month]}"
                  f" – {e.day} {MONTHS_UK_GEN[e.month]}:")

    lines = [header, ""]
    total_done = days_logged = 0

    for d in week:
        iso   = d.isoformat()
        abbr  = DAY_ABBR_UK[d.weekday()]
        today_mark = " ◀" if d == today else ""

        if iso in entries:
            props = entries[iso].get("properties", {})
            done  = sum(1 for h in HABITS_LIST if props.get(h, {}).get("checkbox", False))
            bar   = "⬛" * done + "⬜" * (10 - done)
            star  = " ⭐" if done == 10 else ""
            lines.append(f"{abbr} {d.day:02d}  {bar}  {done}/10{star}{today_mark}")
            if d <= today:
                total_done += done
                days_logged += 1
        elif d <= today:
            lines.append(f"{abbr} {d.day:02d}  ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜  —{today_mark}")
        else:
            lines.append(f"{abbr} {d.day:02d}  · · · · · · · · · ·{today_mark}")

    if days_logged > 0:
        avg  = total_done / days_logged
        pct  = avg * 10
        lines.append(f"\n📊 Середнє: {avg:.1f}/10  ({pct:.0f}%) за {days_logged} дн.")

    lines.append("Тисни на день щоб відмітити:")

    btns = [
        InlineKeyboardButton(
            f"{DAY_ABBR_UK[d.weekday()]} {d.day}",
            callback_data=f"week_d_{d.isoformat()}",
        )
        for d in week
    ]
    keyboard = InlineKeyboardMarkup([btns[:4], btns[4:]])
    return "\n".join(lines), keyboard


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = _get_week_data()
    await update.message.reply_text(text, reply_markup=keyboard)


async def week_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data

    # ── Back to week view ─────────────────────────────────────────────────────
    if data == "week_back":
        text, keyboard = _get_week_data()
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    # ── Open a specific day ───────────────────────────────────────────────────
    # data = "week_d_2026-06-07"
    iso = data[7:]                          # strip "week_d_"
    d   = date.fromisoformat(iso)
    date_label = f"{d.day} {MONTHS_UK_GEN[d.month]}"

    results = notion_query(
        "задачі",
        filter_obj={"property": "Date", "date": {"equals": iso}},
    )
    if results:
        entry = results[0]
    else:
        entry = notion_create_page("задачі", {
            "Name": title_prop(iso),
            "Date": date_prop(iso),
        })
        if not entry:
            await query.answer("❌ Не вдалося відкрити день", show_alert=True)
            return

    page_id = entry["id"]
    props   = entry.get("properties", {})
    await query.edit_message_text(
        _habits_text(props, date_label),
        reply_markup=_habits_keyboard(props, page_id, from_week=True),
    )


def _month_range() -> tuple[str, str]:
    now    = datetime.now()
    start  = f"{now.year}-{now.month:02d}-01"
    next_m = now.month % 12 + 1
    next_y = now.year + (1 if now.month == 12 else 0)
    end    = f"{next_y}-{next_m:02d}-01"
    return start, end


def _parse_tx_note(props: dict) -> tuple[str, str]:
    note = "".join(t.get("plain_text", "") for t in props.get("Примітка", {}).get("rich_text", []))
    parts = note.split("|", 1)
    тип = parts[0].strip() if parts else ""
    кат = parts[1].strip() if len(parts) > 1 else ""
    return тип, кат


def _type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💸 Витрата",   callback_data="tx_t_витрата"),
            InlineKeyboardButton("💚 Дохід",     callback_data="tx_t_дохід"),
        ],
        [InlineKeyboardButton("❌ Скасувати", callback_data="tx_cancel")],
    ])


def _category_keyboard(auto_cat: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    cats = EXPENSE_CATEGORIES  # list of (name, icon)
    for i in range(0, len(cats), 2):
        row = []
        for name, icon in cats[i:i + 2]:
            mark = " ✓" if name == auto_cat else ""
            row.append(InlineKeyboardButton(
                f"{icon} {name}{mark}",
                callback_data=f"tx_c_{name}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Скасувати", callback_data="tx_cancel")])
    return InlineKeyboardMarkup(rows)


def _budget_status(cat: str, new_amount: float) -> str:
    """Return budget warning line for a category after adding new_amount, or ''."""
    start, end = _month_range()
    rows = notion_query("транзакції", filter_obj={
        "and": [
            {"property": "Дата", "date": {"on_or_after": start}},
            {"property": "Дата", "date": {"before":      end}},
        ]
    })
    spent = new_amount
    for r in rows:
        props = r.get("properties", {})
        тип, кат = _parse_tx_note(props)
        if тип == "витрата" and кат == cat:
            spent += props.get("Сума", {}).get("number") or 0

    budget_rows = notion_query("місячний_бюджет")
    limit = 0.0
    for br in budget_rows:
        bcat = "".join(b.get("plain_text", "") for b in
                       br.get("properties", {}).get("Name", {}).get("title", []))
        if bcat == cat:
            limit = br.get("properties", {}).get("Amount", {}).get("number") or 0.0
            break

    if limit <= 0:
        return ""
    remaining = limit - spent
    pct = spent / limit * 100
    icon = EXPENSE_CAT_ICONS.get(cat, "📦")
    if remaining < 0:
        return f"\n🔴 Ліміт {icon} {cat} перевищено! {spent:,.0f} / {limit:,.0f} грн"
    if pct >= 80:
        return f"\n⚠️ {icon} {cat}: залишок {remaining:,.0f} грн ({pct:.0f}% використано)"
    return f"\n💚 {icon} {cat}: залишок {remaining:,.0f} / {limit:,.0f} грн"


async def tx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "tx_cancel":
        context.user_data.pop("pending_tx", None)
        await query.edit_message_text("❌ Скасовано.")
        return

    # ── Обрано тип ────────────────────────────────────────────────────────────
    if data in ("tx_t_витрата", "tx_t_дохід"):
        pending = context.user_data.get("pending_tx")
        if not pending:
            await query.edit_message_text("⏱ Сесія закінчилась. Надішли повідомлення ще раз.")
            return

        if data == "tx_t_дохід":
            props: dict = {
                "Деталі":   title_prop(pending["text"]),
                "Дата":     date_prop(pending.get("date_iso") or today_iso()),
                "Примітка": {"rich_text": [{"text": {"content": "дохід|"}}]},
            }
            if pending.get("amount"):
                props["Сума"] = number_prop(pending["amount"])
            ok = notion_create("транзакції", props)
            context.user_data.pop("pending_tx", None)
            amt = f" — {pending['amount']:,.0f} грн" if pending.get("amount") else ""
            await query.edit_message_text(
                f"✅ Дохід записано{amt}" if ok else "❌ Помилка запису в Notion"
            )
            return

        # витрата → показати категорії
        auto_cat = detect_expense_category(pending["text"])
        pending["auto_cat"] = auto_cat
        amt = f"  —  {pending['amount']:,.0f} грн" if pending.get("amount") else ""
        await query.edit_message_text(
            f"💸 Витрата{amt}\nОберіть категорію:",
            reply_markup=_category_keyboard(auto_cat),
        )
        return

    # ── Обрано категорію ──────────────────────────────────────────────────────
    if data.startswith("tx_c_"):
        cat     = data[5:]
        pending = context.user_data.get("pending_tx")
        if not pending:
            await query.edit_message_text("⏱ Сесія закінчилась. Надішли повідомлення ще раз.")
            return

        amount  = pending.get("amount")
        warning = _budget_status(cat, amount or 0)   # перед збереженням — щоб не подвоювати

        props = {
            "Деталі":   title_prop(pending["text"]),
            "Дата":     date_prop(pending.get("date_iso") or today_iso()),
            "Примітка": {"rich_text": [{"text": {"content": f"витрата|{cat}"}}]},
        }
        if amount:
            props["Сума"] = number_prop(amount)
        ok = notion_create("транзакції", props)
        context.user_data.pop("pending_tx", None)

        if not ok:
            await query.edit_message_text("❌ Помилка запису в Notion")
            return

        icon    = EXPENSE_CAT_ICONS.get(cat, "📦")
        amt_str = f" — {amount:,.0f} грн" if amount else ""
        await query.edit_message_text(
            f"✅ Витрата [{icon} {cat}]{amt_str}{warning}"
        )


# ─── Data builders (used by both commands and menu callback) ──────────────────
def _build_balance() -> str:
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
        тип, _ = _parse_tx_note(props)
        if тип == "дохід":
            income += сума
        elif тип == "витрата":
            expense += сума
    balance = income - expense
    sign  = "+" if balance >= 0 else ""
    emoji = "💪" if balance >= 0 else "😬"
    return (
        f"💰 Баланс за {MONTHS_UK[now.month]} {now.year}:\n\n"
        f"💚 Доходи:  {income:>10,.0f} грн\n"
        f"🔴 Витрати: {expense:>10,.0f} грн\n"
        f"{'─' * 26}\n"
        f"{emoji} Баланс:  {sign}{balance:>9,.0f} грн"
    )


def _build_budget() -> str:
    now = datetime.now()
    start, end = _month_range()
    rows = notion_query("транзакції", filter_obj={
        "and": [
            {"property": "Дата", "date": {"on_or_after": start}},
            {"property": "Дата", "date": {"before":      end}},
        ]
    })
    cat_spent: dict[str, float] = {}
    for r in rows:
        props = r.get("properties", {})
        сума  = props.get("Сума", {}).get("number") or 0
        тип, кат = _parse_tx_note(props)
        if тип == "витрата" and кат:
            cat_spent[кат] = cat_spent.get(кат, 0.0) + сума

    budget_rows = notion_query("місячний_бюджет")
    if not budget_rows:
        return "📊 Місячний бюджет порожній."

    lines        = [f"📊 Бюджет за {MONTHS_UK[now.month]} {now.year}:\n"]
    total_budget = total_actual = 0.0
    for item in budget_rows:
        props  = item.get("properties", {})
        cat    = "".join(b.get("plain_text", "") for b in props.get("Name", {}).get("title", []))
        limit  = props.get("Amount", {}).get("number") or 0.0
        actual = cat_spent.get(cat, 0.0)
        diff   = limit - actual
        lines.append(
            f"{'🟢' if diff >= 0 else '🔴'} {cat}: "
            f"{actual:,.0f} / {limit:,.0f} грн  "
            f"({'+'if diff>=0 else ''}{diff:,.0f})"
        )
        total_budget += limit
        total_actual += actual
    total_diff = total_budget - total_actual
    lines.append(f"\n{'─' * 30}")
    lines.append(
        f"Всього витрат: {total_actual:,.0f} / {total_budget:,.0f} грн  "
        f"({'+'if total_diff>=0 else''}{total_diff:,.0f})"
    )
    return "\n".join(lines)


def _build_transactions() -> str:
    now = datetime.now()
    start, end = _month_range()
    rows = notion_query("транзакції", filter_obj={
        "and": [
            {"property": "Дата", "date": {"on_or_after": start}},
            {"property": "Дата", "date": {"before":      end}},
        ]
    }, sorts=[{"property": "Дата", "direction": "descending"}])

    if not rows:
        return f"📭 Транзакцій за {MONTHS_UK[now.month]} поки немає."

    lines = [f"📋 Транзакції за {MONTHS_UK[now.month]} {now.year}:\n"]
    shown = 0
    for r in rows:
        if shown >= 10:
            break
        props  = r.get("properties", {})
        деталі = "".join(t.get("plain_text", "") for t in props.get("Деталі", {}).get("title", []))
        сума   = props.get("Сума", {}).get("number") or 0
        дата   = (props.get("Дата", {}).get("date") or {}).get("start", "")
        тип, кат = _parse_tx_note(props)
        if not тип and not деталі and not сума:
            continue  # пропускаємо порожні записи
        icon   = "💚" if тип == "дохід" else "🔴"
        day    = дата[8:10] if len(дата) >= 10 else "?"
        suffix = f" [{кат}]" if кат else ""
        lines.append(f"{icon} {day} — {деталі}: {сума:,.0f} грн{suffix}")
        shown += 1
    if shown == 0:
        return f"📭 Транзакцій за {MONTHS_UK[now.month]} поки немає."
    return "\n".join(lines)


# ─── Commands (thin wrappers around builders) ─────────────────────────────────
async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_balance())


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_budget())


async def cmd_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_build_transactions())


# ─── Menu ─────────────────────────────────────────────────────────────────────
def _back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("◀️ Меню", callback_data="menu_back"),
    ]])


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Сьогодні",   callback_data="menu_today"),
            InlineKeyboardButton("🗓 Тиждень",    callback_data="menu_week"),
        ],
        [
            InlineKeyboardButton("💸 Витрата",    callback_data="menu_витрата"),
            InlineKeyboardButton("💚 Дохід",      callback_data="menu_дохід"),
        ],
        [
            InlineKeyboardButton("💰 Баланс",     callback_data="menu_balance"),
            InlineKeyboardButton("📊 Бюджет",     callback_data="menu_budget"),
        ],
        [
            InlineKeyboardButton("🧾 Транзакції", callback_data="menu_transactions"),
            InlineKeyboardButton("💡 Ідея",       callback_data="menu_ідея"),
        ],
    ])


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🗂 Головне меню:", reply_markup=_menu_keyboard())


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query  = update.callback_query
    await query.answer()
    action = query.data[5:]  # strip "menu_"

    # ── Back to menu ─────────────────────────────────────────────────────────
    if action == "back":
        await query.edit_message_text("🗂 Головне меню:", reply_markup=_menu_keyboard())
        return

    # ── View actions — edit menu in place ────────────────────────────────────
    if action == "today":
        iso = today_iso()
        d   = date.fromisoformat(iso)
        results = notion_query("задачі", filter_obj={"property": "Date", "date": {"equals": iso}})
        entry   = results[0] if results else notion_create_page(
            "задачі", {"Name": title_prop(iso), "Date": date_prop(iso)}
        )
        if not entry:
            await query.edit_message_text("❌ Не вдалося створити запис Notion.")
            return
        props = entry.get("properties", {})
        label = f"{d.day} {MONTHS_UK_GEN[d.month]}"
        await query.edit_message_text(
            _habits_text(props, label),
            reply_markup=_habits_keyboard(props, entry["id"]),
        )
        return

    if action == "week":
        text, keyboard = _get_week_data()
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if action == "balance":
        await query.edit_message_text(_build_balance(), reply_markup=_back_to_menu_keyboard())
        return

    if action == "budget":
        await query.edit_message_text(_build_budget(), reply_markup=_back_to_menu_keyboard())
        return

    if action == "transactions":
        await query.edit_message_text(_build_transactions(), reply_markup=_back_to_menu_keyboard())
        return

    # ── Input actions — prompt + set awaiting ────────────────────────────────
    if action == "витрата":
        context.user_data["awaiting"] = "витрата"
        await query.edit_message_text(
            "💸 Напиши суму і опис витрати:\n"
            "Наприклад: «кава 80» або «150 таксі»"
        )
        return

    if action == "дохід":
        context.user_data["awaiting"] = "дохід"
        await query.edit_message_text(
            "💚 Напиши суму і опис доходу:\n"
            "Наприклад: «зарплата 35000»"
        )
        return

    if action == "ідея":
        context.user_data["awaiting"] = "ідеї"
        await query.edit_message_text("💡 Напиши свою ідею:")


# ─── Monobank integration ─────────────────────────────────────────────────────
def _register_mono_webhook() -> None:
    if not MONO_TOKEN or not RENDER_URL:
        logger.warning("Monobank webhook: MONO_TOKEN або RENDER_URL не задано")
        return
    url = f"{RENDER_URL}/mono"
    resp = requests.post(
        "https://api.monobank.ua/personal/webhook",
        headers={"X-Token": MONO_TOKEN},
        json={"webHookUrl": url},
        timeout=10,
    )
    if resp.status_code == 200:
        logger.info(f"✅ Monobank webhook → {url}")
    else:
        logger.error(f"❌ Monobank webhook error {resp.status_code}: {resp.text[:200]}")


async def handle_mono_transaction(bot, data: dict) -> None:
    try:
        item = (data.get("data") or {}).get("statementItem") or {}
        if not item:
            logger.warning("Mono webhook: порожній statementItem")
            return
        logger.info(
            f"Mono tx: id={item.get('id')!r} amount={item.get('amount')} hold={item.get('hold')} "
            f"mcc={item.get('mcc')} desc={item.get('description')!r} "
            f"currency={item.get('currencyCode')}"
        )

        account = (data.get("data") or {}).get("account", "")
        if MONO_ACCOUNT and account != MONO_ACCOUNT:
            logger.info(f"Mono tx: пропущено (рахунок {account!r} ≠ {MONO_ACCOUNT!r})")
            return

        tx_id = item.get("id", "")
        if tx_id:
            if tx_id in _seen_mono_ids:
                logger.info(f"Mono tx: пропущено дублікат (id={tx_id!r})")
                return
            _seen_mono_ids.add(tx_id)
        if item.get("currencyCode", 980) != 980:
            logger.info("Mono tx: пропущено (не гривня)")
            return

        amount_kopecks = item.get("amount", 0)
        if amount_kopecks == 0:
            logger.info("Mono tx: пропущено (сума 0)")
            return

        description = (item.get("description") or "").strip() or "Monobank"
        mcc         = item.get("mcc", 0)
        time_unix   = item.get("time", 0)
        amount_uah  = abs(amount_kopecks) / 100
        tx_date     = date.fromtimestamp(time_unix).isoformat()
        is_expense  = amount_kopecks < 0

        if is_expense:
            cat     = MCC_TO_CATEGORY.get(mcc, "Інше")
            warning = _budget_status(cat, amount_uah)
            props   = {
                "Деталі":   title_prop(description),
                "Дата":     date_prop(tx_date),
                "Сума":     number_prop(amount_uah),
                "Примітка": {"rich_text": [{"text": {"content": f"витрата|{cat}"}}]},
            }
            ok   = notion_create("транзакції", props)
            icon = EXPENSE_CAT_ICONS.get(cat, "📦")
            if warning:
                msg = f"💳 Monobank\n💸 {description} — {amount_uah:,.0f} грн{warning}"
            else:
                msg = f"💳 Monobank\n💸 {description} — {amount_uah:,.0f} грн\n{icon} {cat}"
        else:
            props = {
                "Деталі":   title_prop(description),
                "Дата":     date_prop(tx_date),
                "Сума":     number_prop(amount_uah),
                "Примітка": {"rich_text": [{"text": {"content": "дохід|"}}]},
            }
            ok  = notion_create("транзакції", props)
            msg = f"💳 Monobank\n💚 {description} — {amount_uah:,.0f} грн"

        logger.info(f"Mono tx: notion_create={'ok' if ok else 'FAIL'}, targets={list(_chat_ids)}")
        prefix   = "✅" if ok else "❌ Notion: помилка запису\n"
        full_msg = f"{prefix} {msg}" if ok else f"{prefix}{msg}"

        targets = list(_chat_ids)
        if not targets:
            logger.warning("Mono tx received but no chat_ids — set CHAT_ID env var")
            return
        for chat_id in targets:
            try:
                await bot.send_message(chat_id=chat_id, text=full_msg)
                logger.info(f"Mono tx: повідомлення надіслано → {chat_id}")
            except Exception as e:
                logger.error(f"Mono notify {chat_id}: {e}")
    except Exception as e:
        logger.error(f"handle_mono_transaction crash: {e}", exc_info=True)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    _chat_ids.add(chat_id)
    await update.message.reply_text(
        f"🆔 Ваш Chat ID: {chat_id}\n\n"
        f"Щоб бот повідомляв про Monobank-транзакції після рестарту — "
        f"додайте в Render Environment: CHAT_ID={chat_id}"
    )


async def cmd_setmono(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not MONO_TOKEN:
        await update.message.reply_text(
            "❌ MONO_TOKEN не встановлено.\n"
            "Додайте в Render Environment Variables:\n"
            "MONO_TOKEN = ваш токен з додатку Monobank"
        )
        return
    _register_mono_webhook()
    url = f"{RENDER_URL}/mono"
    await update.message.reply_text(f"✅ Monobank webhook зареєстровано:\n{url}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Notion Life OS Bot*\n\n"
        "Просто напиши або надішли голосове — я визначу куди записати:\n\n"
        "💡 *Ідея*\n"
        "  ідея, думка, що якщо, мабуть варто\n\n"
        "💸 *Витрата*\n"
        "  купив, заплатив, витратив, витрата\n"
        "  Категорія визначається автоматично:\n"
        "  🍕 їжа та кафе · 🚌 транспорт · 🏠 комунальні\n"
        "  🎭 розваги та спорт · 👕 одяг · 📱 підписки · 💸 перекази\n\n"
        "💚 *Дохід*\n"
        "  отримав, заробив, надійшло, нарахували\n\n"
        "✅ *Задача*\n"
        "  треба, зробити, нагадай, не забути, завдання\n\n"
        "🏋️ *Тренування*\n"
        "  тренувався, пробіг, зарядка, підтягування\n\n"
        "🔁 *Звичка*\n"
        "  звичка, звички\n\n"
        "💳 *Monobank* — автоматично\n"
        "  Витрати та доходи з чорної картки фіксуються\n"
        "  без жодних дій з твого боку\n\n"
        "💰 Числа в тексті автоматично стають сумою\n"
        "📅 Дати: сьогодні · завтра · в п'ятницю · через 3 дні\n\n"
        "─────────────────────\n"
        "⌨️ *Команди:*\n"
        "/today — звички на сьогодні\n"
        "/yesterday — звички за вчора\n"
        "/week — тижнева таблиця звичок\n"
        "/balance — баланс доходів і витрат за місяць\n"
        "/budget — ліміти по категоріях vs фактичні витрати\n"
        "/transactions — останні 10 транзакцій\n"
        "/menu — головне меню\n"
        "/setmono — перереєструвати Monobank webhook\n"
        "/help — ця довідка",
        parse_mode="Markdown",
    )


# ─── Main ──────────────────────────────────────────────────────────────────────
def _setup_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("id",           cmd_id))
    app.add_handler(CommandHandler("setmono",      cmd_setmono))
    app.add_handler(CommandHandler("today",        cmd_today))
    app.add_handler(CommandHandler("balance",      cmd_balance))
    app.add_handler(CommandHandler("budget",       cmd_budget))
    app.add_handler(CommandHandler("transactions", cmd_transactions))
    app.add_handler(CommandHandler("menu",      cmd_menu))
    app.add_handler(CommandHandler("week",      cmd_week))
    app.add_handler(CommandHandler("yesterday", cmd_yesterday))
    app.add_handler(CallbackQueryHandler(menu_callback,  pattern=r"^menu_"))
    app.add_handler(CallbackQueryHandler(habit_callback, pattern=r"^h[w]?_\d+_.+"))
    app.add_handler(CallbackQueryHandler(week_callback,  pattern=r"^week_"))
    app.add_handler(CallbackQueryHandler(tx_callback,    pattern=r"^tx_"))
    app.add_handler(MessageHandler(filters.Regex(r"^/вчора"), cmd_yesterday))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))


async def _webhook_main() -> None:
    """Production mode: aiohttp server handles both Telegram and Monobank webhooks."""
    ptb_app = Application.builder().token(TELEGRAM_TOKEN).build()
    _setup_handlers(ptb_app)

    async def tg_handler(request: web.Request) -> web.Response:
        try:
            data   = await request.json()
            update = Update.de_json(data, ptb_app.bot)
            if CHAT_ID and update:
                user = update.effective_user
                if user is None or user.id != CHAT_ID:
                    logger.warning(f"Відхилено запит від user_id={user.id if user else 'None'}")
                    return web.Response(status=200)
            if update and update.effective_chat:
                _chat_ids.add(update.effective_chat.id)
            await ptb_app.process_update(update)
        except Exception as e:
            logger.error(f"TG handler error: {e}")
        return web.Response(status=200)

    async def mono_post(request: web.Request) -> web.Response:
        try:
            data = await request.json()
            asyncio.create_task(handle_mono_transaction(ptb_app.bot, data))
        except Exception as e:
            logger.error(f"Mono handler error: {e}")
        return web.Response(status=200)

    aio = web.Application()
    aio.router.add_post(f"/{TELEGRAM_TOKEN}", tg_handler)
    aio.router.add_post("/mono", mono_post)
    aio.router.add_get("/mono",  lambda _: web.Response(status=200))  # Monobank verification

    async with ptb_app:
        tg_url = f"{RENDER_URL}/{TELEGRAM_TOKEN}"
        await ptb_app.bot.set_webhook(tg_url)
        logger.info(f"🌐 TG webhook → {tg_url}")
        await ptb_app.bot.set_my_commands([
            BotCommand("menu",         "🗂 Головне меню"),
            BotCommand("today",        "📅 Звички на сьогодні"),
            BotCommand("yesterday",    "📅 Звички за вчора"),
            BotCommand("week",         "🗓 Тижнева таблиця звичок"),
            BotCommand("balance",      "💰 Баланс за місяць"),
            BotCommand("budget",       "📊 Бюджет по категоріях"),
            BotCommand("transactions", "🧾 Останні транзакції"),
            BotCommand("help",         "❓ Довідка"),
            BotCommand("setmono",      "🔗 Перереєстрація Monobank"),
        ])
        await ptb_app.start()
        runner = web.AppRunner(aio)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        logger.info(f"✅ Server on :{PORT}")
        _register_mono_webhook()
        await asyncio.Event().wait()


def main() -> None:
    if RENDER_URL:
        asyncio.run(_webhook_main())
    else:
        logger.info("🔄 Polling mode (local)...")
        ptb_app = Application.builder().token(TELEGRAM_TOKEN).build()
        _setup_handlers(ptb_app)
        ptb_app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
