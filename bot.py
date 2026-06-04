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
from datetime import datetime, date

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
RENDER_URL     = os.environ.get("RENDER_EXTERNAL_URL", "")   # автоматично в Render

NOTION_HEADERS = {
    "Authorization":  f"Bearer {NOTION_TOKEN}",
    "Content-Type":   "application/json",
    "Notion-Version": "2022-06-28",
}
NOTION_API = "https://api.notion.com/v1"

# ─── Database IDs ─────────────────────────────────────────────────────────────
DB = {
    "ідеї":       "375c228f-5e9f-81c4-9a3b-dc64cf71186a",
    "задачі":     "375c228f-5e9f-8193-9ef4-ddfb51fbb6c3",
    "транзакції": "375c228f-5e9f-81ba-b03c-d3ae800b912b",
    "тренування": "375c228f-5e9f-813a-b77e-ce1093e38893",
    "борги":      "375c228f-5e9f-81ef-939a-fd6852fa011c",
    "проекти":    "375c228f-5e9f-8192-9cb3-e24ed61e799f",
    "звички":     "375c228f-5e9f-81e1-ab37-cb11eecbd78a",
    "люди":       "375c228f-5e9f-81f4-91f4-d3b84d05db6a",
}

# ─── Voice (optional) ─────────────────────────────────────────────────────────
try:
    import speech_recognition as sr
    from pydub import AudioSegment
    VOICE_ENABLED = True
    logger.info("Voice recognition: OK")
except ImportError:
    VOICE_ENABLED = False
    logger.warning("Voice recognition: disabled (install SpeechRecognition + pydub)")

# ─── Локалізація ──────────────────────────────────────────────────────────────
MONTHS_UK = [
    "", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень",
]

# ─── Визначення категорії за ключовими словами ────────────────────────────────
KEYWORDS: list[tuple[str, list[str]]] = [
    ("ідеї",               ["ідея", "думка", "що якщо", "ідею", "мабуть варто"]),
    ("транзакції_витрата", ["витрата", "купив", "заплатив", "потратив",
                            "витратив", "купила", "заплатила", "потратила",
                            "заплатили", "витрачено"]),
    ("транзакції_дохід",   ["дохід", "отримав", "заробив", "прийшло",
                            "отримала", "заробила", "надійшло", "нарахували"]),
    ("задачі",             ["задача", "задачу", "треба", "зробити", "нагадай",
                            "завдання", "to do", "нагадати", "не забути"]),
    ("тренування",         ["тренування", "тренувався", "тренувалась",
                            "зробив зарядку", "пробіг", "пробігла",
                            "зарядка", "качав", "підтягування", "пробіжка"]),
    ("борги",              ["борг", "борги", "позичив", "позичила", "позику",
                            "дав у борг", "взяв у борг", "повернув", "повернула"]),
    ("звички",             ["звичка", "звички", "звичку", "моя звичка"]),
]

def detect_category(text: str) -> str | None:
    t = text.lower()
    for category, keywords in KEYWORDS:
        if any(kw in t for kw in keywords):
            return category
    return None

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
    body: dict = {"page_size": 50}
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

# ─── Обробка повідомлення ─────────────────────────────────────────────────────
async def process_text(update: Update, text: str) -> None:
    category = detect_category(text)
    amount   = parse_amount(text)
    today    = today_iso()

    if not category:
        await update.message.reply_text(
            "🤔 Не зрозумів куди записати.\n"
            "Введи /help щоб побачити всі ключові слова."
        )
        return

    ok       = False
    db_label = ""

    # ── Ідеї ──────────────────────────────────────────────────────────────────
    if category == "ідеї":
        ok = notion_create("ідеї", {
            "Назва":   title_prop(text),
            "Статус":  select_prop("🆕 Нова"),
            "Дата":    date_prop(today),
        })
        db_label = "💡 Ідеї"

    # ── Витрата ───────────────────────────────────────────────────────────────
    elif category == "транзакції_витрата":
        тип = "🔴 Витрата"
        props = {"Опис": title_prop(text), "Тип": select_prop(тип), "Дата": date_prop(today)}
        if amount:
            props["Сума"] = number_prop(amount)
        ok = notion_create("транзакції", props)
        db_label = f"💸 Транзакції ({тип})"

    # ── Дохід ─────────────────────────────────────────────────────────────────
    elif category == "транзакції_дохід":
        тип = "💚 Дохід"
        props = {"Опис": title_prop(text), "Тип": select_prop(тип), "Дата": date_prop(today)}
        if amount:
            props["Сума"] = number_prop(amount)
        ok = notion_create("транзакції", props)
        db_label = f"💸 Транзакції ({тип})"

    # ── Задачі ────────────────────────────────────────────────────────────────
    elif category == "задачі":
        ok = notion_create("задачі", {
            "Назва":   title_prop(text),
            "Статус":  select_prop("⬜ Не почато"),
            "Дата":    date_prop(today),
        })
        db_label = "✅ Задачі"

    # ── Тренування ────────────────────────────────────────────────────────────
    elif category == "тренування":
        ok = notion_create("тренування", {
            "Тип":  title_prop(text),
            "Дата": date_prop(today),
        })
        db_label = "🏋️ Тренування"

    # ── Борги ─────────────────────────────────────────────────────────────────
    elif category == "борги":
        t = text.lower()
        тип = "📤 Я дав" if any(kw in t for kw in ["дав", "позичив", "позичила"]) else "📥 Мені дали"
        props = {
            "Опис":   title_prop(text),
            "Тип":    select_prop(тип),
            "Дата":   date_prop(today),
            "Статус": select_prop("⏳ Активний"),
        }
        if amount:
            props["Сума"] = number_prop(amount)
        ok = notion_create("борги", props)
        db_label = f"🏦 Борги і позики ({тип})"

    # ── Звички ────────────────────────────────────────────────────────────────
    elif category == "звички":
        ok = notion_create("звички", {
            "Звичка":  title_prop(text),
            "Дата":    date_prop(today),
            "Виконано": {"checkbox": True},
        })
        db_label = "🔁 Звички"

    # ── Відповідь ─────────────────────────────────────────────────────────────
    if ok:
        reply = f"✅ Додано в {db_label}\n\n📝 {text}"
        if amount:
            reply += f"\n💰 Сума: {amount:,.0f} грн"
        await update.message.reply_text(reply)
    else:
        await update.message.reply_text(
            "❌ Помилка запису в Notion.\n"
            "Перевір NOTION_TOKEN та доступ до баз даних."
        )

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


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = today_iso()
    results = notion_query(
        "задачі",
        filter_obj={
            "and": [
                {"property": "Дата",   "date":   {"equals":       today}},
                {"property": "Статус", "select": {"does_not_equal": "✅ Готово"}},
            ]
        },
        sorts=[{"property": "Пріоритет", "direction": "ascending"}],
    )

    if not results:
        await update.message.reply_text(
            f"📅 Задач на сьогодні ({today}) немає!\n"
            "🎉 Список порожній або все виконано."
        )
        return

    lines = [f"📅 Задачі на {today}:\n"]
    priority_icons = {"🔴 Критично": "🔴", "🟡 Важливо": "🟡", "🟢 Колись": "🟢"}
    for item in results:
        props  = item.get("properties", {})
        title  = get_title(item)
        status = props.get("Статус",   {}).get("select", {}).get("name", "—")
        prio   = props.get("Пріоритет",{}).get("select", {}).get("name", "")
        icon   = priority_icons.get(prio, "•")
        lines.append(f"{icon} {title}  [{status}]")

    await update.message.reply_text("\n".join(lines))


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now          = datetime.now()
    month_start  = f"{now.year}-{now.month:02d}-01"
    next_m       = now.month % 12 + 1
    next_y       = now.year + (1 if now.month == 12 else 0)
    month_end    = f"{next_y}-{next_m:02d}-01"

    results = notion_query(
        "транзакції",
        filter_obj={
            "and": [
                {"property": "Дата", "date": {"on_or_after": month_start}},
                {"property": "Дата", "date": {"before":      month_end}},
            ]
        },
    )

    income = expense = 0.0
    for item in results:
        props  = item.get("properties", {})
        amount = props.get("Сума", {}).get("number") or 0
        тип    = props.get("Тип",  {}).get("select", {}).get("name", "")
        if "Дохід"   in тип: income  += amount
        if "Витрата" in тип: expense += amount

    balance = income - expense
    sign    = "+" if balance >= 0 else ""
    emoji   = "💪" if balance >= 0 else "😬"

    await update.message.reply_text(
        f"💰 Баланс за {MONTHS_UK[now.month]} {now.year}:\n\n"
        f"💚 Дохід:   {income:>10,.0f} грн\n"
        f"🔴 Витрати: {expense:>10,.0f} грн\n"
        f"{'─' * 24}\n"
        f"{emoji} Залишок: {sign}{balance:>9,.0f} грн"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Notion Life OS Bot*\n\n"
        "Просто напиши або надішли голосове — я визначу куди записати:\n\n"
        "💡 *Ідеї*\n"
        "  ідея, думка, що якщо\n\n"
        "🔴 *Витрата*\n"
        "  купив, заплатив, потратив, витрата\n\n"
        "💚 *Дохід*\n"
        "  отримав, заробив, прийшло, дохід\n\n"
        "✅ *Задача*\n"
        "  треба, зробити, нагадай, задача\n\n"
        "🏋️ *Тренування*\n"
        "  тренувався, пробіг, зарядка\n\n"
        "🏦 *Борг*\n"
        "  борг, позичив, дав у борг, взяв у борг\n\n"
        "🔁 *Звичка*\n"
        "  звичка\n\n"
        "💰 Числа в тексті автоматично стають сумою\n\n"
        "─────────────────────\n"
        "⌨️ *Команди:*\n"
        "/today — задачі на сьогодні\n"
        "/balance — баланс за місяць\n"
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
