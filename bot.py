"""Telegram-бот, который помнит дни рождения сотрудников и напоминает о них владельцу."""
import asyncio
import datetime as dt
import functools
import html
import json
import logging
import os
import re
import socket
import sys
import time
import urllib.request
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      MenuButtonCommands, MenuButtonWebApp, ReplyKeyboardMarkup, Update, WebAppInfo)
from telegram.constants import ParseMode
from telegram import CallbackQuery
from telegram.error import BadRequest, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
                          MessageHandler, filters)

import ai
import birthdays as bd
import kz_holidays as kz
from card import make_card

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Almaty").strip())
NOTIFY_TIME = dt.time.fromisoformat(os.getenv("NOTIFY_TIME", "09:00").strip())
REMIND_DAYS = sorted({int(x) for x in os.getenv("REMIND_DAYS_BEFORE", "7,2,1").split(",") if x.strip()}, reverse=True)
OWNER_ID = os.getenv("OWNER_ID", "").strip()
# Google-таблица со списком, полом и текстами поздравлений (доступ: «все, у кого есть ссылка — читатель»).
SHEET_ID = os.getenv("SHEET_ID", "").strip()
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
SHEET_REFRESH_SECONDS = 120
# Веб-приложение Apps Script в этой таблице — через него бот добавляет и удаляет строки (apps_script.gs).
SHEET_WEBAPP_URL = os.getenv("SHEET_WEBAPP_URL", "").strip()
SHEET_WEBAPP_SECRET = os.getenv("SHEET_WEBAPP_SECRET", "").strip()
# Утренние сообщения шлёт GitHub Actions (notify_once.py); здесь — только если LOCAL_NOTIFY=1.
LOCAL_NOTIFY = os.getenv("LOCAL_NOTIFY", "0").strip() == "1"
# Сбор на подарок: за сколько дней до ДР писать всем, по сколько скидываемся и куда переводить.
GIFT_DAYS_BEFORE = int(os.getenv("GIFT_DAYS_BEFORE", "5"))
GIFT_AMOUNT = int(os.getenv("GIFT_AMOUNT", "2000"))
GIFT_PAYEE = os.getenv("GIFT_PAYEE", "").strip()
# На бесплатном PythonAnywhere интернет доступен только через их прокси.
PROXY = os.getenv("PROXY", "").strip() or ("http://proxy.server:3128" if os.getenv("PYTHONANYWHERE_DOMAIN") else "")
if PROXY:
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})))

# В фоне (pythonw) консоли нет, поэтому журнал всегда пишется в data/bot.log.
(BASE_DIR / "data").mkdir(exist_ok=True)
log_handlers = [RotatingFileHandler(BASE_DIR / "data" / "bot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
if sys.stderr:
    log_handlers.append(logging.StreamHandler())
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO, handlers=log_handlers)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("birthday-bot")

# Telegram ждёт ответа на нажатие кнопки ~15 секунд. Если сервер задержался (прокси PythonAnywhere иногда тормозит),
# answer() падает с «Query is too old» — и обработчик обрывался, кнопка «не нажималась». Теперь просроченный ответ
# пропускаем, а само действие (например, «Да, это я» в /join) выполняется как обычно.
_answer_callback = CallbackQuery.answer


async def _answer_even_if_late(self, *args, **kwargs):
    try:
        return await _answer_callback(self, *args, **kwargs)
    except BadRequest as error:
        if "too old" in str(error).lower() or "query id is invalid" in str(error).lower():
            log.info("Кнопка: ответ опоздал (%s) — продолжаем без него", error)
            return False
        raise


CallbackQuery.answer = _answer_even_if_late

store = bd.Store()
if OWNER_ID.isdigit():
    store.data["owner_id"] = int(OWNER_ID)

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")
MONTH_NAMES = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
               "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MESSAGE_LIMIT = 4000
CAPTION_LIMIT = 1024  # максимум подписи к фото в Telegram
INSTANCE_PORT = 47821  # занятый порт = бот уже запущен (два бота с одним токеном мешают друг другу)
# Кроме владельца раздел с праздниками видят эти Telegram ID (HOLIDAY_VIEWERS в .env, через запятую; id не меняется, в отличие от ника).
HOLIDAY_VIEWERS = {int(x) for x in os.getenv("HOLIDAY_VIEWERS", "").split(",") if x.strip().isdigit()}
NOT_OWNER = "Это личный бот — доступ только у владельца."
PENDING = "⏳ Заявка отправлена владельцу. Как только он подтвердит доступ, я напишу вам."
REJECTED = "Доступ к боту не подтверждён."
ASK_ACCESS = "Чтобы получить доступ, нажмите /start — владелец бота подтвердит заявку."

# Остальные люди (не владелец) — store.data["viewers"]: {"<id>": {"name", "username", "status"}}.
# Подтверждённые видят только даты дней рождения: /soon, /today, /month, /all.
APPROVED, WAITING, DENIED = "approved", "pending", "rejected"


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def fmt_date(date: dt.date) -> str:
    return f"{date.day} {MONTHS[date.month - 1]} ({WEEKDAYS[date.weekday()]})"


def when(days: int) -> str:
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    return f"через {days} {plural(days, 'день', 'дня', 'дней')}"


def fmt_person(person, date=None) -> str:
    """Имя, должность и возраст на дату `date` (если известен год рождения)."""
    text = f"<b>{html.escape(person['name'])}</b>"
    if person.get("position"):
        text += f" — {html.escape(person['position'])}"
    age = bd.age_on(person, date) if date else None
    if age:
        text += f", {age} {plural(age, 'год', 'года', 'лет')}"
    return text


def fmt_birthday(person) -> str:
    text = f"{person['day']} {MONTHS[person['month'] - 1]}"
    return f"{text} {person['year']}" if person.get("year") else text


def soon_text(days: int) -> str:
    now = today()
    items = bd.upcoming(store.people, now, days)
    if not items:
        return f"В ближайшие {days} {plural(days, 'день', 'дня', 'дней')} дней рождения нет."
    lines = [f"🗓 <b>Ближайшие {days} {plural(days, 'день', 'дня', 'дней')}:</b>"]
    for date, person in items:
        lines.append(f"• {fmt_date(date)}, {when((date - now).days)} — {fmt_person(person, date)}")
    return "\n".join(lines)


def daily_text(date: dt.date, include_today=True) -> str:
    """Утреннее сообщение: у кого сегодня и у кого через REMIND_DAYS дней. Пусто — если никого."""
    parts = []
    todays = bd.birthdays_on(store.people, date) if include_today else []
    if todays:
        parts.append("🎂 <b>Сегодня день рождения!</b>\n" + "\n".join(f"• {fmt_person(p, date)}" for p in todays))
    for days in REMIND_DAYS:
        if days <= 0:
            continue
        target = date + dt.timedelta(days=days)
        people = bd.birthdays_on(store.people, target)
        if people:
            parts.append(f"⏰ <b>{when(days).capitalize()}, {fmt_date(target)}:</b>\n"
                         + "\n".join(f"• {fmt_person(p, target)}{no_text_mark(p)}" for p in people))
    return "\n\n".join(parts)


def missed_text(last: dt.date, date: dt.date) -> str:
    """Дни рождения, которые прошли, пока бот был выключен (не больше чем за 30 дней)."""
    day = max(last + dt.timedelta(days=1), date - dt.timedelta(days=30))
    lines = []
    while day < date:
        lines += [f"• {fmt_date(day)} — {fmt_person(p, day)}" for p in bd.birthdays_on(store.people, day)]
        day += dt.timedelta(days=1)
    return "😅 <b>Пока бот был выключен, прошли дни рождения:</b>\n" + "\n".join(lines) if lines else ""


def fetch_sheet() -> str:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
    with urllib.request.urlopen(url, timeout=30) as response:
        if "text/csv" not in response.headers.get("Content-Type", ""):
            raise RuntimeError("таблица закрыта — откройте доступ «Все, у кого есть ссылка: читатель»")
        return response.read().decode("utf-8")


def sheet_call(action: str, timeout=40, **payload) -> dict:
    """Запрос к Apps Script таблицы: add / delete / set_text / set_date / generate (текст пишет AI)."""
    data = json.dumps(dict(payload, action=action, secret=SHEET_WEBAPP_SECRET)).encode("utf-8")
    request = urllib.request.Request(SHEET_WEBAPP_URL, data=data, headers={"Content-Type": "application/json"})
    # Apps Script отвечает редиректом на googleusercontent.com — urllib сам переходит по нему.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "таблица не приняла запрос"))
    return result


async def ai_text(name: str, force=False):
    """Текст поздравления от AI (Gemini, ключ GEMINI_API_KEY в .env); он сразу записывается в таблицу.
    Возвращает (текст, None) или (None, ошибка). Если не вышло — допишется утром (fill_missing_texts)."""
    if not SHEET_WEBAPP_URL:
        return None, "таблица не подключена"
    person = store.find(name)
    if person and person.get("text") and not force:
        return person["text"], None
    try:
        if ai.enabled():
            examples = [p["text"] for p in store.people if p.get("text")]
            text, female = await asyncio.to_thread(ai.greeting, person or {"name": name}, examples)
            await asyncio.to_thread(sheet_call, "set_text", name=name, text=text)
            if female is not None and person and person.get("female_known") is None:
                try:  # пол пустой (новый сотрудник) — AI определил по имени; цвет карточки будет верный
                    await asyncio.to_thread(sheet_call, "set_gender", name=name, gender="әйел" if female else "ер")
                except Exception as error:  # старый Apps Script без set_gender — не страшно
                    log.info("Пол для %s не записан: %s", name, error)
        else:  # ключа на сервере нет — пусть пишет Apps Script таблицы
            text = (await asyncio.to_thread(sheet_call, "generate", timeout=55, name=name, force=force)).get("text")
    except Exception as error:
        log.warning("AI не написал текст для %s: %s", name, error)
        return None, str(error)
    await refresh(force=True)
    return text, None


async def fill_missing_texts(date: dt.date, limit: int, urgent_only=False, urgent_days=7) -> int:
    """AI пишет тексты тем, у кого их нет: сначала у кого день рождения в ближайшие `urgent_days` дней.
    limit — сколько всего за раз; urgent_only — только эти ближайшие (перед утренней рассылкой)."""
    if not SHEET_WEBAPP_URL or not ai.enabled():
        return 0
    urgent = [p["name"] for _, p in bd.upcoming(store.people, date, urgent_days) if not p.get("text")]
    rest = [] if urgent_only else [p["name"] for p in store.people if not p.get("text") and p["name"] not in urgent]
    written = 0
    for name in (urgent + rest)[:limit]:
        _, error = await ai_text(name)
        if error:  # AI недоступен — остальные допишутся в следующий раз
            break
        written += 1
    if written:
        log.info("AI написал тексты: %d", written)
    return written


_last_sync = 0.0


async def refresh(force=False):
    """Подтягивает список из Google-таблицы (не чаще раза в 2 минуты). Возвращает текст ошибки или None."""
    global _last_sync
    if not SHEET_ID or (not force and time.monotonic() - _last_sync < SHEET_REFRESH_SECONDS):
        return None
    try:
        text = await asyncio.to_thread(fetch_sheet)
        store.load_sheet(text)
        store.save()
    except Exception as error:  # нет интернета, закрытый доступ, испорченная таблица — работаем по кэшу
        log.warning("Не удалось обновить список из таблицы: %s", error)
        return str(error)
    _last_sync = time.monotonic()
    return None


def card_name(person) -> str:
    return person.get("card_name") or " ".join(person["name"].split()[:2])


def no_text_mark(person) -> str:
    if not SHEET_ID or person.get("text"):
        return ""
    return f" ✍️ <i>нет текста</i> → <code>/ai {html.escape(person['name'].split()[0])}</code>"


async def send_greeting(bot, chat_id, person, date: dt.date, title: str):
    """Кто именинник — отдельной строкой, затем карточка с текстом поздравления в подписи:
    это одно сообщение, его можно сразу переслать имениннику."""
    photo = await asyncio.to_thread(make_card, card_name(person), bool(person.get("female")))
    await bot.send_message(chat_id, f"🎂 <b>{title}</b>\n{fmt_person(person, date)}", parse_mode=ParseMode.HTML)
    text = person.get("text")
    if text and len(text) <= CAPTION_LIMIT:
        await bot.send_photo(chat_id, photo, caption=text)
        return
    await bot.send_photo(chat_id, photo)
    if text:  # длиннее подписи Telegram — текст отдельным сообщением
        await bot.send_message(chat_id, text)
    else:
        first = html.escape(person["name"].split()[0])
        await bot.send_message(chat_id, f"✍️ Текст поздравления не заполнен — нажмите <code>/ai {first}</code>, "
                                        f"и его напишет AI, или впишите сами в таблицу:\n{SHEET_URL}",
                               parse_mode=ParseMode.HTML)


EXPIRY_WARN_DAYS = 5


def pa_expiry():
    """До какого дня работает сайт бота на бесплатном PythonAnywhere (продлевается кнопкой раз в месяц)."""
    user, token = os.getenv("PA_USERNAME", "").strip(), os.getenv("PA_API_TOKEN", "").strip()
    if not user or not token:
        return None
    request = urllib.request.Request(f"https://www.pythonanywhere.com/api/v0/user/{user}/webapps/",
                                     headers={"Authorization": f"Token {token}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        apps = json.loads(response.read().decode("utf-8"))
    dates = [dt.date.fromisoformat(a["expiry"]) for a in apps if a.get("expiry")]
    return min(dates) if dates else None


async def expiry_line(date: dt.date) -> str:
    """Владельцу за EXPIRY_WARN_DAYS дней: сайт скоро отключится, нужно нажать кнопку продления."""
    try:
        expiry = await asyncio.to_thread(pa_expiry)
    except Exception as error:  # нет связи с PythonAnywhere — не мешаем утренней рассылке
        log.warning("Не удалось узнать срок работы сайта: %s", error)
        return ""
    if expiry is None or (expiry - date).days > EXPIRY_WARN_DAYS:
        return ""
    days = (expiry - date).days
    head = "уже отключён" if days < 0 else f"отключится {fmt_date(expiry)}, {when(days)}"
    return (f"⚠️ <b>Сайт бота {head}!</b>\nПродлите: pythonanywhere.com → Web → "
            "«Run until 1 month from today». Без этого бот перестанет работать.")


def missing_line() -> str:
    """Владельцу: у кого в таблице нет даты рождения (например, новые сотрудники из кадровой таблицы)."""
    people = [p for p in store.people if not bd.has_birthday(p)]
    if not people:
        return ""
    names = ", ".join(html.escape(p["name"]) for p in people[:12]) + (" …" if len(people) > 12 else "")
    return (f"❔ <b>Нет даты рождения: {len(people)}</b> — {names}\n"
            "Впишите в таблицу или <code>/date Фамилия 20.08.2004</code>. Сотрудник может указать сам — /join в боте.")


async def send_morning(bot, chat_id, date: dt.date) -> bool:
    """Утро владельца — только когда есть повод: карточка и текст именинника, напоминания за REMIND_DAYS дней
    (7, 2, 1), праздники, предложение сбора на подарок, срок работы сайта; по понедельникам — у кого нет даты.
    Возвращает, было ли что-то отправлено."""
    await refresh(force=True)
    sent = False
    for holiday in kz.starting_on(date):
        await send_holiday(bot, chat_id, holiday, date)
        sent = True
    for person in bd.birthdays_on(store.people, date):
        await send_greeting(bot, chat_id, person, date, "Сегодня день рождения!")
        sent = True
    parts = [await expiry_line(date), daily_text(date, include_today=False), holiday_reminders(date)]
    if date.weekday() == 0:
        parts.append(missing_line())
    text = "\n\n".join(t for t in parts if t)
    if text:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("📅 Күнтізбе", web_app=WebAppInfo(webapp_url()))]]) \
            if webapp_url() else None
        await send(bot, chat_id, text, markup)
        sent = True
    return await suggest_gifts(bot, chat_id, date) or sent


# ---------- Сотрудники: казахский интерфейс, меню, стена пожеланий ----------

MONTHS_KZ = ("қаңтар", "ақпан", "наурыз", "сәуір", "мамыр", "маусым",
             "шілде", "тамыз", "қыркүйек", "қазан", "қараша", "желтоқсан")
MONTH_NAMES_KZ = tuple(m.capitalize() for m in MONTHS_KZ)
WEEKDAYS_KZ = ("дс", "сс", "ср", "бс", "жм", "сб", "жс")


def fmt_date_kz(date: dt.date) -> str:
    return f"{date.day} {MONTHS_KZ[date.month - 1]} ({WEEKDAYS_KZ[date.weekday()]})"


def when_kz(days: int) -> str:
    return "бүгін" if days == 0 else "ертең" if days == 1 else f"{days} күннен кейін"


def fmt_person_kz(person, date=None) -> str:
    text = f"<b>{html.escape(person['name'])}</b>"
    if person.get("position"):
        text += f" — {html.escape(person['position'])}"
    age = bd.age_on(person, date) if date else None
    return text + (f", {age} жас" if age else "")


def first_name(person) -> str:
    """Имя для обращения: в таблице «Фамилия Имя [Отчество]»."""
    words = person["name"].split()
    return words[1] if len(words) >= 2 else words[0]


def joined_name(user_id) -> str:
    return viewers().get(str(user_id), {}).get("joined") or ""


def webapp_url() -> str:
    """Адрес календаря (Mini App) — страница /app на сайте бота."""
    url = os.getenv("WEBAPP_URL", "").strip()
    user = os.getenv("PA_USERNAME", "").strip()
    return url or (f"https://{user.lower()}.pythonanywhere.com/app" if user else "")


WISH_TEXT = 20  # состояние диалога «написать пожелание»

BTN_SOON = "🎂 Жақын туған күндер"
BTN_CALENDAR = "📅 Күнтізбе"
BTN_MONTH = "🗓 Осы ай"
BTN_NOTIFY = "🔔 Хабарландыру"
BTN_HOLIDAYS = "🇰🇿 Мерекелер"
BTN_ADMIN = "⚙️ Басқару"
MENU_TEXTS = (BTN_SOON, BTN_CALENDAR, BTN_MONTH, BTN_NOTIFY, BTN_HOLIDAYS, BTN_ADMIN)
MENU_FILTER = filters.Text(list(MENU_TEXTS))


def main_menu(user) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(BTN_SOON), KeyboardButton(BTN_CALENDAR)], [KeyboardButton(BTN_MONTH), KeyboardButton(BTN_NOTIFY)]]
    extra = [KeyboardButton(BTN_HOLIDAYS)] if can_see_holidays(user) else []
    if is_owner(user):
        extra.append(KeyboardButton(BTN_ADMIN))
    if extra:
        rows.append(extra)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def calendar_button(text="📅 Күнтізбені ашу"):
    url = webapp_url()
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, web_app=WebAppInfo(url))]]) if url else None


def soon_kz(days=30, user_id=None) -> str:
    now = today()
    me = joined_name(user_id)
    items = [(d, p) for d, p in bd.upcoming(store.people, now, days) if p["name"] != me]
    if not items:
        later = [(d, p) for d, p in bd.upcoming(store.people, now, 366) if p["name"] != me]
        if not later:
            return "Тізімде туған күндер әлі жоқ."
        d, p = later[0]
        return f"🎂 Жақын {days} күнде туған күн жоқ.\nЕң жақыны — {fmt_date_kz(d)}, {when_kz((d - now).days)}: {fmt_person_kz(p, d)}"
    lines = [f"🎂 <b>Жақын {days} күндегі туған күндер</b>"]
    for d, p in items:
        lines.append(f"\n<b>{fmt_date_kz(d)}</b> · {when_kz((d - now).days)}\n   {fmt_person_kz(p, d)}")
    return "\n".join(lines)


def month_kz(number=None) -> str:
    now = today()
    number = number or now.month
    year = now.year if number >= now.month else now.year + 1
    people = sorted((p for p in store.people if p.get("month") == number), key=lambda p: (p["day"], p["name"]))
    if not people:
        return f"🗓 {MONTH_NAMES_KZ[number - 1]} айында туған күн жоқ."
    lines = [f"🗓 <b>{MONTH_NAMES_KZ[number - 1]} {year}</b>"]
    for p in people:
        d = bd.occurrence(p, year)
        mark = " 🎉" if d == now else " ✓" if d < now else ""
        lines.append(f"• <b>{d.day}</b> — {fmt_person_kz(p, d)}{mark}")
    return "\n".join(lines)


# Стена пожеланий: store.data["wishes"]["<гггг-мм-дд>|<ФИО>"] = {"items": {"<user_id>": {"from", "text"}},
#                                                               "delivered": [user_id, ...]}
def wishes() -> dict:
    return store.data.setdefault("wishes", {})


def wish_key(person, date: dt.date) -> str:
    return f"{date.isoformat()}|{person['name']}"


def wish_callback(person, date: dt.date) -> str:
    index = bd.birthdays_on(store.people, date).index(person)
    return f"w:{date:%y%m%d}:{index}"


def wish_buttons(pairs, user_id=None) -> list:
    """Кнопки «💌 Тілек жазу» для именинников [(дата, человек)], кроме самого пользователя."""
    me = joined_name(user_id)
    return [[InlineKeyboardButton(f"💌 {first_name(p)} — тілек жазу"[:60], callback_data=wish_callback(p, d))]
            for d, p in pairs if p["name"] != me]


def save_wish(person, date: dt.date, user_id: int, author: str, text: str) -> int:
    entry = wishes().setdefault(wish_key(person, date), {"items": {}, "delivered": []})
    entry["items"][str(user_id)] = {"from": author, "text": text.strip()[:1000]}
    store.save()
    return len(entry["items"])


def person_chat(person):
    """Telegram именинника, если он отметился в боте (/join)."""
    for uid, v in viewers().items():
        if v.get("joined") == person["name"] and v.get("status") == APPROVED:
            return int(uid)
    return None


def check_init_data(init_data: str, max_age=7 * 86400):
    """Проверка подписи Telegram Mini App (initData): возвращает пользователя {id, first_name…} или None."""
    import hashlib
    import hmac
    from urllib.parse import parse_qsl
    pairs = dict(parse_qsl(init_data or "", keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received or not BOT_TOKEN:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), received):
        return None
    if time.time() - int(pairs.get("auth_date", "0") or 0) > max_age:
        return None
    try:
        return json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None


def member_allowed(user_id: int) -> bool:
    return user_id == store.data.get("owner_id") or viewers().get(str(user_id), {}).get("status") == APPROVED


def calendar_payload(user_id: int) -> dict:
    """Данные для календаря (Mini App): имена, должности, даты — без текстов поздравлений."""
    now = today()
    me = joined_name(user_id)
    people = []
    for p in store.people:
        if not bd.has_birthday(p):
            continue
        date = bd.next_birthday(p, now)
        entry = wishes().get(wish_key(p, date), {}).get("items", {})
        people.append({
            "name": p["name"], "first": first_name(p), "position": p.get("position") or "", "dept": p.get("dept") or "",
            "day": p["day"], "month": p["month"], "date": date.isoformat(), "days": (date - now).days,
            "age": bd.age_on(p, date), "me": p["name"] == me, "wishes": len(entry),
            "myWish": (entry.get(str(user_id)) or {}).get("text"),
        })
    people.sort(key=lambda x: (x["days"], x["name"]))
    holidays = []
    if user_id == store.data.get("owner_id") or user_id in HOLIDAY_VIEWERS:
        holidays = [{"name": h["name"], "date": d.isoformat(), "days": (d - now).days} for d, h in kz.upcoming(now, 365)]
    return {"today": now.isoformat(), "me": me, "people": people, "holidays": holidays,
            "missing": sum(not bd.has_birthday(p) for p in store.people)}


def wish_from_app(user: dict, name: str, date_iso: str, text: str) -> str:
    """Пожелание из календаря. Возвращает текст ошибки или ''."""
    person = store.find(name)
    try:
        date = dt.date.fromisoformat(date_iso)
    except ValueError:
        return "Күн қате"
    if person is None or not bd.has_birthday(person) or bd.next_birthday(person, today()) != date:
        return "Бұл адам тізімде жоқ"
    if person["name"] == joined_name(user["id"]):
        return "Бұл — сіздің туған күніңіз 🎂"
    text = (text or "").strip()
    if len(text) < 2:
        return "Тілек бос"
    author = joined_name(user["id"]) or " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    save_wish(person, date, user["id"], author or "Әріптес", text)
    return ""


def has_undelivered_wishes(date: dt.date) -> bool:
    prefix = date.isoformat() + "|"
    return any(k.startswith(prefix) and set(v["items"]) - set(v["delivered"]) for k, v in wishes().items())


# Кому приходят собранные пожелания: владелец + эти люди (WISH_RECIPIENTS в .env, через запятую; по умолчанию — только владелец).
WISH_RECIPIENTS = [int(x) for x in os.getenv("WISH_RECIPIENTS", "").split(",") if x.strip().isdigit()]


def wish_recipients() -> list:
    owner = store.data.get("owner_id")
    return list(dict.fromkeys(([owner] if owner else []) + WISH_RECIPIENTS))


def wishes_message(person, date: dt.date, items: list, more=False) -> str:
    """Собранные пожелания одним сообщением: его удобно переслать имениннику или вставить в общее поздравление."""
    age = bd.age_on(person, date)
    who = f"🎂 <b>{html.escape(person['name'])}</b>" + (f" — {html.escape(person['position'])}" if person.get("position") else "")
    if more:
        head = [f"💌 <b>Тағы {len(items)} тілек келді</b>", who, ""]
    else:
        head = ["💌 <b>Тілектер қабырғасы</b>", who,
                f"{'Бүгін' if date == today() else fmt_date_kz(date)} туған күні{f', {age} жас' if age else ''}. "
                f"Әріптестер {len(items)} тілек жазды:", ""]
    body = [f"{i}. 💬 <b>{html.escape(w['from'])}</b>\n{html.escape(w['text'])}\n" for i, w in enumerate(items, 1)]
    foot = ["📨 Бұл хабарды туған күн иесіне жіберіңіз (Переслать) немесе құттықтауға қосыңыз."]
    return "\n".join(head + body + foot)


async def deliver_wishes(bot, date: dt.date) -> int:
    """Утром в день рождения — все пожелания одним сообщением владельцу и WISH_RECIPIENTS (они передают имениннику).
    Пожелания, написанные позже в тот же день, уходят им сразу отдельным сообщением «ещё N пожеланий»."""
    delivered = 0
    for person in bd.birthdays_on(store.people, date):
        entry = wishes().get(wish_key(person, date))
        if not entry:
            continue
        new = {uid: w for uid, w in entry["items"].items() if uid not in entry["delivered"]}
        if not new:
            continue
        text = wishes_message(person, date, list(new.values()), more=bool(entry["delivered"]))
        reached = 0
        for chat in wish_recipients():
            try:
                await send(bot, chat, text)
                reached += 1
            except TelegramError as error:
                log.warning("Тілектер %s-ке жетпеді: %s", chat, error)
        if not reached:
            raise TelegramError("тілектер ешкімге жетпеді")
        entry["delivered"] += list(new)
        delivered += len(new)
        store.save()
    return delivered


async def wish_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not is_owner(user) and viewer_status(user) != APPROVED:
        await query.answer(ASK_ACCESS, show_alert=True)
        return ConversationHandler.END
    _, stamp, index = query.data.split(":")
    date = dt.datetime.strptime(stamp, "%y%m%d").date()
    people = bd.birthdays_on(store.people, date)
    if int(index) >= len(people) or date < today():
        await query.answer("Бұл туған күн өтіп кетті 🙂", show_alert=True)
        return ConversationHandler.END
    person = people[int(index)]
    if person["name"] == joined_name(user.id):
        await query.answer("Бұл — сіздің туған күніңіз 🎂", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data["wish"] = wish_key(person, date)
    old = wishes().get(wish_key(person, date), {}).get("items", {}).get(str(user.id))
    text = (f"💌 <b>{html.escape(person['name'])}</b> үшін тілегіңізді жазыңыз — бір хабарламамен.\n"
            f"Ол {fmt_date_kz(date)} күні таңертең иесіне жетеді (аты-жөніңізбен).")
    if old:
        text += f"\n\nБұрынғы тілегіңіз:\n<i>{html.escape(old['text'])}</i>\nЖаңасын жазсаңыз, ауыстырылады."
    await query.message.reply_text(text + "\n\n<i>Бас тарту — /cancel</i>", parse_mode=ParseMode.HTML)
    return WISH_TEXT


async def wish_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.pop("wish", None)
    if not key:
        return ConversationHandler.END
    stamp, name = key.split("|", 1)
    date = dt.date.fromisoformat(stamp)
    person = store.find(name)
    if person is None:
        await update.message.reply_text("Бұл адам тізімде жоқ.")
        return ConversationHandler.END
    user = update.effective_user
    author = joined_name(user.id) or user.full_name
    count = save_wish(person, date, user.id, author, update.message.text)
    await update.message.reply_text(f"✅ Тілегіңіз сақталды! {html.escape(first_name(person))} оны "
                                     f"{'бүгін' if date == today() else fmt_date_kz(date) + ' күні'} алады 🎁\n"
                                     f"Барлық тілек: {count}", parse_mode=ParseMode.HTML)
    if date == today() and (store.data.get("morning") or {}).get("date") == date.isoformat():
        await deliver_wishes(context.bot, date)  # таңғы хабар кетіп қойған — тілек бірден жетеді
    return ConversationHandler.END


async def wish_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("wish", None)
    await update.effective_message.reply_text("Бас тартылды.")
    return ConversationHandler.END


MUTE_BUTTON_ROW = [InlineKeyboardButton("🔕 Хабарландыруды өшіру", callback_data="mute:on")]
MUTE_BUTTON = InlineKeyboardMarkup([MUTE_BUTTON_ROW])
UNMUTE_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("🔔 Қайта қосу", callback_data="mute:off")]])


def viewer_morning(user_id: int, date: dt.date):
    """Сотрудникам с доступом — только в день рождения и за REMIND_DAYS дней (7, 2, 1), без текстов и карточек.
    Про свой собственный день рождения человек напоминаний не получает. (текст, [(дата, человек)]); пусто — не шлём."""
    me = joined_name(user_id)
    parts, pairs = [], []
    todays = [p for p in bd.birthdays_on(store.people, date) if p["name"] != me]
    if todays:
        parts.append("🎉 <b>Бүгін — туған күн!</b>\n" + "\n".join(f"• {fmt_person_kz(p, date)}" for p in todays)
                     + "\nҚұттықтауды ұмытпаңыз 💐")
        pairs += [(date, p) for p in todays]
    for days in REMIND_DAYS:
        target = date + dt.timedelta(days=days)
        people = [p for p in bd.birthdays_on(store.people, target) if p["name"] != me] if days > 0 else []
        if people:
            parts.append(f"⏰ <b>{when_kz(days).capitalize()}, {fmt_date_kz(target)} — туған күн:</b>\n"
                         + "\n".join(f"• {fmt_person_kz(p, target)}" for p in people))
            pairs += [(target, p) for p in people]
    if user_id in HOLIDAY_VIEWERS:
        parts += [f"🇰🇿 <b>Бүгін: {html.escape(h['name'])}</b>" for h in kz.starting_on(date)]
        parts += [t for t in (holiday_reminders(date),) if t]
    return "\n\n".join(parts), pairs


def viewer_morning_text(user_id: int, date: dt.date) -> str:
    return viewer_morning(user_id, date)[0]


def morning_recipients() -> list:
    """Кому утренняя рассылка: владелец и все с подтверждённым доступом, кто её не отключил."""
    owner = store.data.get("owner_id")
    people = [owner] if owner else []
    people += [int(uid) for uid, v in viewers().items()
               if v.get("status") == APPROVED and not v.get("muted") and int(uid) != owner]
    return people


async def send_viewer_morning(bot, chat_id: int, date: dt.date) -> bool:
    text, pairs = viewer_morning(chat_id, date)
    if text:
        rows = []
        url = webapp_url()
        if url:
            rows.append([InlineKeyboardButton("📅 Күнтізбе", web_app=WebAppInfo(url))])
        rows.append(MUTE_BUTTON_ROW)
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
    return bool(text)


async def on_mute_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    viewer = viewers().get(str(update.effective_user.id))
    if viewer is None:
        await query.answer()
        return
    muted = query.data == "mute:on"
    viewer["muted"] = muted
    store.save()
    await query.answer("Хабарландыру өшірілді" if muted else "Хабарландыру қосылды")
    await query.edit_message_reply_markup(UNMUTE_BUTTON if muted else MUTE_BUTTON)


# ---------- Праздники Казахстана: их видит только владелец ----------

def fmt_holiday_days(holiday, date: dt.date) -> str:
    """«21–23 марта (сб–пн)» и дни, на которые переносится выходной."""
    last = date + dt.timedelta(days=holiday["days"] - 1)
    text = fmt_date(date)
    if last != date:
        text = f"{date.day}–{last.day} {MONTHS[date.month - 1]} ({WEEKDAYS[date.weekday()]}–{WEEKDAYS[last.weekday()]})"
    moved = kz.moved_days_off(holiday, date)
    if moved:
        text += ", выходной также " + ", ".join(fmt_date(d) for d in moved)
    return text


def holiday_reminders(date: dt.date) -> str:
    parts = []
    for days in REMIND_DAYS:
        if days <= 0:
            continue
        target = date + dt.timedelta(days=days)
        holidays = kz.starting_on(target)
        if holidays:
            parts.append(f"🇰🇿 <b>{when(days).capitalize()}, праздник:</b>\n"
                         + "\n".join(f"• {html.escape(h['name'])} — {fmt_holiday_days(h, target)}" for h in holidays))
    return "\n\n".join(parts)


async def send_holiday(bot, chat_id, holiday, date: dt.date):
    """Заголовок для владельца, затем постер с общим поздравлением в подписи: его можно сразу переслать."""
    await bot.send_message(chat_id, f"🇰🇿 <b>{html.escape(holiday['name'])}</b>\n{fmt_holiday_days(holiday, date)}",
                           parse_mode=ParseMode.HTML)
    poster = kz.poster_path(holiday)
    if not poster.exists():  # постеры генерирует design/make_holiday_posters.py; без него — только текст
        await bot.send_message(chat_id, kz.greeting(holiday))
        return
    photo = await asyncio.to_thread(poster.read_bytes)
    await bot.send_photo(chat_id, photo, caption=kz.greeting(holiday))


def chunks(text: str):
    """Режет длинный текст по строкам, чтобы уложиться в лимит Telegram."""
    chunk = ""
    for line in text.split("\n"):
        if chunk and len(chunk) + len(line) + 1 > MESSAGE_LIMIT:
            yield chunk
            chunk = ""
        chunk = f"{chunk}\n{line}" if chunk else line
    if chunk:
        yield chunk


async def send(bot, chat_id, text: str, reply_markup=None):
    parts = list(chunks(text))
    for i, chunk in enumerate(parts):  # кнопки — под последним куском
        await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML,
                               reply_markup=reply_markup if i == len(parts) - 1 else None)


async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    await send(context.bot, update.effective_chat.id, text)


def help_text() -> str:
    before = " и ".join(f"за {d} {plural(d, 'день', 'дня', 'дней')}" for d in REMIND_DAYS if d > 0)
    return (
        f"👋 Я помню дни рождения сотрудников и каждый день в {NOTIFY_TIME:%H:%M} пишу вам, "
        f"если у кого-то праздник" + (f" (и заранее — {before})." if before else ".") + "\n\n"
        "/soon — ближайшие дни рождения (30 дней, можно /soon 60)\n"
        "/today — у кого сегодня\n"
        "/month — в этом месяце (/month 3 — в марте)\n"
        "/all — весь список по месяцам\n"
        "/missing — у кого не указана дата\n"
        "/find Имя — найти сотрудника\n"
        "/card Имя — карточка и текст поздравления (или просто напишите имя)\n"
        "/check — показать сегодняшнее напоминание\n"
        "/sync — обновить список из Google-таблицы\n"
        "/add — добавить человека (по шагам; текст напишет AI)\n"
        "/date Фамилия 20.08.2004 — вписать дату рождения\n"
        "/ai Фамилия — AI заново напишет текст поздравления\n"
        "/text Фамилия — поменять текст поздравления вручную\n"
        "/gift — сборы на подарки: кто уже сдал\n"
        "/holidays — праздники Казахстана: постер и текст поздравления\n"
        "/del Фамилия Имя — удалить из таблицы\n"
        "/users — кто ещё имеет доступ к боту\n\n"
        + (f"📋 Список, пол и тексты поздравлений — в Google-таблице:\n{SHEET_URL}" if SHEET_ID else
           "/add Фамилия Имя 20.02.1988 — добавить или изменить дату\n/del Фамилия Имя — удалить\n\n"
           "📎 Пришлите Excel-файл сотрудников — я обновлю список.")
    )


def viewer_help_text() -> str:
    before = ", ".join(str(d) for d in REMIND_DAYS if d > 0)
    return (
        "👋 <b>Сәлем! Мен — EduApp командасының туған күн боты</b> 🎂\n\n"
        f"⏰ Әріптестің туған күніне {before} күн қалғанда және туған күннің өзінде {NOTIFY_TIME:%H:%M}-де еске саламын.\n"
        "📅 «Күнтізбе» — бүкіл команданың туған күндері бір бетте.\n\n"
        "Төмендегі мәзірді пайдаланыңыз 👇\n"
        "/join — өзіңізді тізімде белгілеу (туған күніңізді жазу)"
    )


def viewers() -> dict:
    return store.data.setdefault("viewers", {})


def is_owner(user) -> bool:
    return user is not None and user.id == store.data.get("owner_id")


def viewer_status(user):
    return viewers().get(str(user.id), {}).get("status") if user else None


def viewer_label(viewer) -> str:
    text = html.escape(viewer["name"])
    return f"{text} (@{html.escape(viewer['username'])})" if viewer.get("username") else text


async def deny(update: Update, user):
    """Ответ тем, у кого нет доступа: зависит от того, подавал ли человек заявку."""
    status = viewer_status(user)
    text = PENDING if status == WAITING else REJECTED if status == DENIED else ASK_ACCESS
    if update.effective_message:
        await update.effective_message.reply_text(text)


def owner_only(handler):
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_owner(user):
            if viewer_status(user) == APPROVED:
                await update.effective_message.reply_text("Эта команда доступна только владельцу бота.\n\n" + viewer_help_text())
            else:
                await deny(update, user)
            return
        await refresh()
        await handler(update, context)
    return wrapper


def viewer_allowed(handler):
    """Владелец и подтверждённые люди."""
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_owner(user) and viewer_status(user) != APPROVED:
            await deny(update, user)
            return
        await refresh()
        await handler(update, context)
    return wrapper


def access_buttons(user_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Дать доступ", callback_data=f"ok:{user_id}"),
                                  InlineKeyboardButton("❌ Отказать", callback_data=f"no:{user_id}")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if store.data.get("owner_id") is None:
        store.data["owner_id"] = user.id
        store.save()
        log.info("Владелец бота: %s (%s)", user.id, user.full_name)
    if is_owner(user):
        await reply(update, context, help_text() + "\n\n" + soon_text(30))
        await update.message.reply_text("👇 Мәзір төменде", reply_markup=main_menu(user))
        return
    status = viewer_status(user)
    if status == APPROVED:
        await update.message.reply_text(viewer_help_text(), parse_mode=ParseMode.HTML, reply_markup=main_menu(user))
        await update.message.reply_text(soon_kz(30, user.id), parse_mode=ParseMode.HTML, reply_markup=calendar_button())
        return
    if status == DENIED:
        await update.message.reply_text(REJECTED)
        return
    viewer = {"name": user.full_name, "username": user.username, "status": WAITING}
    viewers()[str(user.id)] = viewer
    store.save()
    log.info("Заявка на доступ: %s (%s)", user.id, user.full_name)
    await update.message.reply_text(PENDING)
    await context.bot.send_message(
        store.data["owner_id"],
        f"🔔 <b>Заявка на доступ к боту</b>\n{viewer_label(viewer)}, id {user.id}\n\n"
        "Он будет видеть только даты дней рождения.",
        parse_mode=ParseMode.HTML, reply_markup=access_buttons(user.id))


async def on_access_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки владельца: ok/no — ответ на заявку, del — забрать доступ."""
    query = update.callback_query
    if not is_owner(update.effective_user):
        await query.answer(NOT_OWNER, show_alert=True)
        return
    action, user_id = query.data.split(":", 1)
    viewer = viewers().get(user_id)
    if viewer is None:
        await query.answer("Этой заявки уже нет.", show_alert=True)
        return
    status = APPROVED if action == "ok" else DENIED
    if viewer["status"] == status:  # кнопку нажали повторно (например, в старом сообщении)
        await query.answer("Уже сделано.")
        return
    viewer["status"] = status
    store.save()
    log.info("Доступ %s: %s (%s)", viewer["status"], user_id, viewer["name"])
    await query.answer()
    result = "✅ Доступ выдан" if action == "ok" else "❌ Доступ закрыт" if action == "del" else "❌ Отказано"
    # Под сообщением всегда остаётся обратное действие: решение можно поменять в любой момент.
    if action == "ok":
        undo = InlineKeyboardButton("🚫 Закрыть доступ", callback_data=f"del:{user_id}")
    else:
        undo = InlineKeyboardButton("✅ Всё-таки дать доступ", callback_data=f"ok:{user_id}")
    text = re.sub(r"\n\n<b>(✅|❌)[^<]*</b>$", "", query.message.text_html)
    await query.edit_message_text(f"{text}\n\n<b>{result}</b>", parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup([[undo]]))
    try:
        if action == "ok":
            member = await context.bot.get_chat(int(user_id))
            await set_calendar_menu(context.bot, int(user_id))
            await context.bot.send_message(int(user_id), "✅ <b>Доступ ашылды!</b>\n\n" + viewer_help_text(),
                                           parse_mode=ParseMode.HTML, reply_markup=main_menu(member))
            if SHEET_WEBAPP_URL:
                await context.bot.send_message(int(user_id), JOIN_OFFER, reply_markup=JOIN_BUTTON)
        elif action == "del":
            await context.bot.send_message(int(user_id), REJECTED)
    except TelegramError:
        log.warning("Не удалось написать %s — возможно, он остановил бота", user_id)


@owner_only
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = {APPROVED: [], WAITING: [], DENIED: []}
    for user_id, viewer in viewers().items():
        groups.setdefault(viewer["status"], []).append((user_id, viewer))
    if not any(groups.values()):
        await reply(update, context, "Кроме вас, доступа ни у кого нет. "
                                     "Пусть человек откроет бота и нажмёт /start — вам придёт заявка.")
        return
    if groups[APPROVED]:
        await reply(update, context, "👥 <b>Есть доступ:</b>")
        for user_id, viewer in groups[APPROVED]:
            await update.effective_message.reply_text(
                f"• {viewer_label(viewer)}", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Закрыть доступ", callback_data=f"del:{user_id}")]]))
    for user_id, viewer in groups[WAITING]:
        await update.effective_message.reply_text(
            f"⏳ Ждёт подтверждения: {viewer_label(viewer)}", parse_mode=ParseMode.HTML, reply_markup=access_buttons(user_id))
    if groups[DENIED]:
        lines = [f"• {viewer_label(v)}" for _, v in groups[DENIED]]
        await update.effective_message.reply_text(
            "❌ <b>Отказано:</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Дать доступ: {v['name'][:30]}", callback_data=f"ok:{i}")]
                                               for i, v in groups[DENIED]]))


@viewer_allowed
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_owner(user):
        await update.effective_message.reply_text(help_text(), parse_mode=ParseMode.HTML, reply_markup=main_menu(user))
    else:
        await update.effective_message.reply_text(viewer_help_text(), parse_mode=ParseMode.HTML, reply_markup=main_menu(user))


@viewer_allowed
async def notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/notify — включить или выключить утренние сообщения (для сотрудников с доступом)."""
    if is_owner(update.effective_user):
        await reply(update, context, "Иесіне (владельцу) хабар әрқашан келеді — оны өшіруге болмайды.")
        return
    viewer = viewers()[str(update.effective_user.id)]
    viewer["muted"] = not viewer.get("muted")
    store.save()
    if viewer["muted"]:
        await update.effective_message.reply_text("🔕 Хабарландыру өшірілді. Қайта қосу — төмендегі батырма.",
                                                  reply_markup=UNMUTE_BUTTON)
    else:
        before = ", ".join(str(d) for d in REMIND_DAYS if d > 0)
        await update.effective_message.reply_text(f"🔔 Хабарландыру қосулы: туған күнге {before} күн қалғанда және "
                                                  f"туған күннің өзінде {NOTIFY_TIME:%H:%M}-де жазамын.",
                                                  reply_markup=MUTE_BUTTON)


@owner_only
async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ai Фамилия — AI заново пишет текст поздравления и сразу показывает карточку."""
    query = " ".join(context.args)
    if not query:
        await reply(update, context, "Напишите, кому написать текст: /ai Омарова")
        return
    person, matches = find_one(query)
    if person is None:
        text = "Нашёл несколько — уточните:\n" + "\n".join(f"• {html.escape(p['name'])}" for p in matches[:15]) \
            if matches else f"Никого не нашёл по запросу «{html.escape(query)}»."
        await reply(update, context, text)
        return
    await reply(update, context, f"🤖 Пишу текст для <b>{html.escape(person['name'])}</b>…")
    text, error = await ai_text(person["name"], force=True)
    if error:
        await reply(update, context, f"⚠️ AI не ответил: {html.escape(error)}")
        return
    await show_cards(update, context, person["name"])


@owner_only
async def date_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/date Фамилия Имя 20.08.2004 — вписать дату рождения в таблицу."""
    args = context.args
    parsed = bd.parse_date(args[-1]) if len(args) >= 2 else None
    if not parsed:
        await reply(update, context, "Формат: /date Фамилия Имя 20.08.2004 (год можно не писать: 20.08)")
        return
    if not SHEET_WEBAPP_URL:
        await reply(update, context, f"Впишите дату в таблицу:\n{SHEET_URL}")
        return
    person, matches = find_one(" ".join(args[:-1]))
    if person is None:
        text = "Нашёл несколько — уточните:\n" + "\n".join(f"• {html.escape(p['name'])}" for p in matches[:15]) \
            if matches else "Никого не нашёл."
        await reply(update, context, text)
        return
    error = await save_date(person["name"], parsed)
    if error:
        await reply(update, context, f"⚠️ Не получилось сохранить: {html.escape(error)}")
        return
    person = store.find(person["name"]) or person
    await reply(update, context, f"✅ {fmt_person(person)} — 🎂 {fmt_birthday(person) if bd.has_birthday(person) else ''}")


def date_cell(parsed) -> str:
    month, day, year = parsed
    return f"{day:02d}.{month:02d}" + (f".{year}" if year else "")


async def save_date(name: str, parsed):
    try:
        await asyncio.to_thread(sheet_call, "set_date", name=name, date=date_cell(parsed))
    except Exception as error:
        return str(error)
    await refresh(force=True)
    return None


@viewer_allowed
async def soon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = int(context.args[0]) if context.args and context.args[0].isdigit() else 30
    await reply(update, context, soon_text(min(days, 366)))


@viewer_allowed
async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = today()
    people = bd.birthdays_on(store.people, now)
    if people:
        text = "🎂 <b>Сегодня день рождения!</b>\n" + "\n".join(f"• {fmt_person(p, now)}" for p in people)
    else:
        text = "Сегодня дней рождения нет."
        nearest = bd.upcoming(store.people, now, 366)
        if nearest:
            date = nearest[0][0]
            names = ", ".join(fmt_person(p, date) for d, p in nearest if d == date)
            text += f"\nБлижайший — {fmt_date(date)}, {when((date - now).days)}: {names}"
    await reply(update, context, text)


@viewer_allowed
async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = today()
    number = int(context.args[0]) if context.args and context.args[0].isdigit() else now.month
    if not 1 <= number <= 12:
        await reply(update, context, "Укажите номер месяца от 1 до 12, например /month 3")
        return
    year = now.year if number >= now.month else now.year + 1
    people = sorted((p for p in store.people if p.get("month") == number), key=lambda p: (p["day"], p["name"]))
    if not people:
        await reply(update, context, f"В месяце «{MONTH_NAMES[number - 1]}» дней рождения нет.")
        return
    lines = [f"🗓 <b>{MONTH_NAMES[number - 1]} {year}:</b>"]
    for person in people:
        date = bd.occurrence(person, year)
        lines.append(f"• {fmt_date(date)} — {fmt_person(person, date)}")
    await reply(update, context, "\n".join(lines))


@viewer_allowed
async def all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dated = sorted((p for p in store.people if bd.has_birthday(p)), key=lambda p: (p["month"], p["day"], p["name"]))
    if not dated:
        await reply(update, context, "Список пуст. Пришлите Excel-файл сотрудников.")
        return
    lines = [f"📋 <b>Все дни рождения ({len(dated)}):</b>"]
    current = None
    for person in dated:
        if person["month"] != current:
            current = person["month"]
            lines.append(f"\n<b>{MONTH_NAMES[current - 1]}</b>")
        lines.append(f"• {person['day']} — {fmt_person(person)}")
    missing = len(store.people) - len(dated)
    if missing:
        lines.append(f"\nБез даты: {missing} — /missing")
    await reply(update, context, "\n".join(lines))


@owner_only
async def missing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    people = [p for p in store.people if not bd.has_birthday(p)]
    if not people:
        await reply(update, context, "У всех сотрудников указана дата рождения 👍")
        return
    lines = [f"❔ <b>Нет даты рождения ({len(people)}):</b>"]
    lines += [f"• {fmt_person(p)}" for p in people]
    lines.append("\nДобавить: /add Фамилия Имя 20.02.1988 (год можно не указывать)")
    await reply(update, context, "\n".join(lines))


@owner_only
async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await reply(update, context, "Напишите имя: /find Ерлан")
        return
    people = store.search(query)
    if not people:
        await reply(update, context, "Никого не нашёл.")
        return
    now = today()
    lines = []
    for person in people[:20]:
        line = f"• {fmt_person(person)}"
        if bd.has_birthday(person):
            date = bd.next_birthday(person, now)
            line += f"\n   🎂 {fmt_birthday(person)} — следующий {fmt_date(date)}, {when((date - now).days)}"
            age = bd.age_on(person, date)
            if age:
                line += f", исполнится {age}"
        else:
            line += "\n   дата не указана"
        lines.append(line)
    await reply(update, context, "\n".join(lines))


@owner_only
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старый режим без таблицы (/add Фамилия Имя 20.02.1988). С таблицей /add — это диалог join_*."""
    if SHEET_ID:
        await reply(update, context, f"Список теперь ведётся в Google-таблице — правьте там:\n{SHEET_URL}")
        return
    args = context.args
    parsed = bd.parse_date(args[-1]) if len(args) >= 2 else None
    if not parsed:
        await reply(update, context, "Формат: /add Фамилия Имя 20.02.1988\nГод можно не указывать: /add Аиша 05.03")
        return
    person, created = store.set_birthday(" ".join(args[:-1]), *parsed)
    store.save()
    action = "Добавил" if created else "Обновил"
    await reply(update, context, f"✅ {action}: {fmt_person(person)} — 🎂 {fmt_birthday(person)}")


@owner_only
async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args)
    if not name:
        await reply(update, context, "Формат: /del Фамилия Имя")
        return
    if SHEET_ID and SHEET_WEBAPP_URL:
        await delete_from_sheet(update, context, name)
        return
    if SHEET_ID:
        await reply(update, context, f"Список теперь ведётся в Google-таблице — правьте там:\n{SHEET_URL}")
        return
    person = store.remove(name)
    if person:
        store.save()
        await reply(update, context, f"🗑 Удалил: {fmt_person(person)}")
        return
    similar = store.search(name)
    text = "Точного совпадения нет."
    if similar:
        text += " Похожие:\n" + "\n".join(f"• {html.escape(p['name'])}" for p in similar[:10])
    await reply(update, context, text)


def can_see_holidays(user) -> bool:
    return is_owner(user) or (user is not None and user.id in HOLIDAY_VIEWERS)


async def holidays_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not can_see_holidays(user):
        if viewer_status(user) == APPROVED:
            await update.effective_message.reply_text("Этот раздел вам недоступен.\n\n" + viewer_help_text())
        else:
            await deny(update, user)
        return
    now = today()
    items = kz.upcoming(now, 365)
    lines = ["🇰🇿 <b>Праздники Казахстана на год вперёд:</b>"]
    lines += [f"• {fmt_holiday_days(h, d)} — {html.escape(h['name'])}, {when((d - now).days)}" for d, h in items]
    if not any(h["id"] == "kurban" for _, h in items):
        lines.append(f"\n⚠️ Дата Курбан-айта на {now.year + 1} год не внесена — она объявляется каждый год (KURBAN_DATES в kz_holidays.py).")
    lines.append("\nЧтобы получить постер и текст, нажмите на праздник 👇")
    buttons = [InlineKeyboardButton(f"{d.day:02d}.{d.month:02d} {h['name']}"[:40], callback_data=f"hd:{h['id']}") for d, h in items]
    markup = InlineKeyboardMarkup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_holiday_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not can_see_holidays(update.effective_user):
        await query.answer("Этот раздел вам недоступен.", show_alert=True)
        return
    holiday = kz.BY_ID.get(query.data.split(":", 1)[1])
    date = kz.next_date(holiday, today()) if holiday else None
    if date is None:
        await query.answer("Дата этого праздника пока не внесена.", show_alert=True)
        return
    await query.answer()
    await send_holiday(context.bot, update.effective_chat.id, holiday, date)


@owner_only
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await send_morning(context.bot, update.effective_chat.id, today()):
        await reply(update, context, "Сегодня напоминаний нет.")


async def show_cards(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    people = store.search(query)
    if not people:
        await reply(update, context, f"Никого не нашёл по запросу «{html.escape(query)}».\n\n/help — список команд")
        return
    if len(people) > 1:
        exact = [p for p in people if bd.name_key(p["name"]).startswith(bd.name_key(query))]
        people = exact if len(exact) == 1 else people
    if len(people) > 1:
        await reply(update, context, "Нашёл несколько — уточните:\n" + "\n".join(f"• {html.escape(p['name'])}" for p in people[:15]))
        return
    person = people[0]
    now = today()
    if bd.has_birthday(person):
        date = bd.next_birthday(person, now)
        title = f"{fmt_date(date)}, {when((date - now).days)}"
    else:
        date, title = now, "Дата рождения не указана"
    await send_greeting(context.bot, update.effective_chat.id, person, date, title)


@owner_only
async def card_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await reply(update, context, "Напишите имя: /card Сапаров")
        return
    await show_cards(update, context, query)


@owner_only
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SHEET_ID:
        await reply(update, context, "Google-таблица не подключена (SHEET_ID в .env).")
        return
    error = await refresh(force=True)
    if error:
        await reply(update, context, f"⚠️ Не получилось прочитать таблицу: {html.escape(error)}")
        return
    people = store.people
    await reply(update, context, f"✅ Список обновлён: {len(people)} человек, с датой — "
                                 f"{sum(bd.has_birthday(p) for p in people)}, "
                                 f"с текстом поздравления — {sum(bool(p.get('text')) for p in people)}.")


@owner_only
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SHEET_ID:
        await reply(update, context, f"Список теперь ведётся в Google-таблице — правьте там:\n{SHEET_URL}")
        return
    document = update.message.document
    if not (document.file_name or "").lower().endswith((".xlsx", ".xlsm")):
        await reply(update, context, "Пришлите файл Excel (.xlsx).")
        return
    file = await document.get_file()
    content = await file.download_as_bytearray()
    try:
        report = store.import_excel(BytesIO(content))
    except Exception as error:  # битый файл или не те колонки
        log.exception("Не удалось прочитать Excel")
        await reply(update, context, f"Не получилось прочитать файл: {html.escape(str(error))}")
        return
    store.save()
    await reply(update, context, "✅ " + html.escape(bd.describe_import(report)))


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки нижнего меню (на казахском)."""
    user = update.effective_user
    if not is_owner(user) and viewer_status(user) != APPROVED:
        await deny(update, user)
        return ConversationHandler.END
    context.user_data.pop("wish", None)
    await refresh()
    text = update.message.text
    if text == BTN_SOON:
        await update.message.reply_text(soon_kz(30, user.id), parse_mode=ParseMode.HTML, reply_markup=calendar_button())
    elif text == BTN_CALENDAR:
        markup = calendar_button("📅 Күнтізбені ашу")
        await update.message.reply_text("Бүкіл команданың туған күндері — бір бетте 👇" if markup else
                                        "Күнтізбе әлі бапталмаған.", reply_markup=markup)
    elif text == BTN_MONTH:
        await update.message.reply_text(month_kz(), parse_mode=ParseMode.HTML, reply_markup=calendar_button())
    elif text == BTN_NOTIFY:
        await notify_command(update, context)
    elif text == BTN_HOLIDAYS:
        await holidays_command(update, context)
    elif text == BTN_ADMIN and is_owner(user):
        await reply(update, context, help_text())
    return ConversationHandler.END


def wish_handler(persistent=False) -> ConversationHandler:
    return ConversationHandler(
        name="wish", persistent=persistent,
        # Команда начала всегда начинает заново: иначе диалог, оборванный на середине, «залипает» навсегда
        # (в webhook-режиме нет таймаута), и бот молча игнорирует повторный /join.
        allow_reentry=True,
        entry_points=[CallbackQueryHandler(wish_start, pattern=r"^w:\d{6}:\d+$")],
        states={WISH_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_FILTER, wish_save)]},
        fallbacks=[CommandHandler("cancel", wish_cancel), MessageHandler(MENU_FILTER, on_menu)],
        conversation_timeout=None if persistent else 30 * 60,
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_owner(user):
        await refresh()
        await show_cards(update, context, update.message.text.strip())
    elif viewer_status(user) == APPROVED:
        await reply(update, context, viewer_help_text())
    else:
        await deny(update, user)


async def notify(bot):
    """Отправляет утреннее напоминание один раз в день."""
    owner = store.data.get("owner_id")
    date = today()
    if not owner or store.data.get("last_notified") == date.isoformat():
        return
    last = store.data.get("last_notified")
    missed = missed_text(dt.date.fromisoformat(last), date) if last else ""
    if missed:
        await send(bot, owner, missed)
    text = await send_morning(bot, owner, date) or missed
    store.data["last_notified"] = date.isoformat()
    store.save()
    log.info("Проверка за %s: %s", date, "отправлено напоминание" if text else "дней рождения нет")


async def notify_job(context: ContextTypes.DEFAULT_TYPE):
    # Проверяем раз в минуту: так напоминание придёт и после перезапуска/сна ноутбука, если время уже прошло.
    if dt.datetime.now(TZ).time() >= NOTIFY_TIME:
        await notify(context.bot)


async def post_init(app: Application):
    await set_commands(app.bot)
    await refresh(force=True)
    if LOCAL_NOTIFY:
        app.job_queue.run_repeating(notify_job, interval=60, first=5)


async def set_commands(bot):
    common = [
        BotCommand("soon", "Ближайшие дни рождения"),
        BotCommand("today", "У кого сегодня"),
        BotCommand("month", "Дни рождения в этом месяце"),
        BotCommand("all", "Весь список"),
    ]
    viewer_extra = [BotCommand("join", "Отметиться в списке"), BotCommand("notify", "Утренние сообщения: вкл/выкл"),
                    BotCommand("start", "Запросить доступ / начать")]
    await bot.set_my_commands(common + viewer_extra)
    for user_id in HOLIDAY_VIEWERS:
        try:
            await bot.set_my_commands(common + [BotCommand("holidays", "Праздники Казахстана")] + viewer_extra,
                                      scope=BotCommandScopeChat(user_id))
        except TelegramError:  # человек ещё не открывал бота
            log.warning("Не удалось задать меню для %s", user_id)
    owner = store.data.get("owner_id")
    if owner:  # владелец видит в меню и свои команды
        await bot.set_my_commands(common + [
            BotCommand("missing", "У кого нет даты"),
            BotCommand("find", "Найти сотрудника"),
            BotCommand("card", "Карточка и текст поздравления"),
            BotCommand("check", "Показать сегодняшнее напоминание"),
            BotCommand("sync", "Обновить список из Google-таблицы"),
            BotCommand("add", "Добавить человека в таблицу"),
            BotCommand("date", "Вписать дату рождения"),
            BotCommand("ai", "AI напишет текст поздравления"),
            BotCommand("text", "Поменять текст поздравления"),
            BotCommand("gift", "Сборы на подарки"),
            BotCommand("holidays", "Праздники Казахстана"),
            BotCommand("del", "Удалить человека из таблицы"),
            BotCommand("users", "Кто имеет доступ к боту"),
            BotCommand("help", "Помощь"),
        ], scope=BotCommandScopeChat(owner))
    url = webapp_url()
    if url:  # кнопка «Меню» у поля ввода открывает календарь; у владельца остаётся список команд
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp("📅 Күнтізбе", WebAppInfo(url)))
        for uid, viewer in viewers().items():
            if viewer.get("status") == APPROVED:
                await set_calendar_menu(bot, int(uid))
        if owner:
            await bot.set_chat_menu_button(chat_id=owner, menu_button=MenuButtonCommands())


async def set_calendar_menu(bot, chat_id: int):
    """Кнопка «Меню» в чате сотрудника открывает календарь (Mini App)."""
    url = webapp_url()
    if not url:
        return
    try:
        await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonWebApp("📅 Күнтізбе", WebAppInfo(url)))
    except TelegramError as error:
        log.warning("Меню-кнопка для %s: %s", chat_id, error)


# ---------- Добавление в список: /join (сам человек) и /add (владелец) ----------

JOIN_NAME, JOIN_DATE, JOIN_POSITION, JOIN_GENDER, JOIN_TEXT, JOIN_CONFIRM, JOIN_LINK, TEXT_EDIT, LINK_DATE = range(9)
ASK_OWN_DATE = ("🎂 В списке нет вашей даты рождения. Напишите её, например: <b>20.08.2004</b>\n"
                "(год можно не писать: 20.08)")
JOIN_OFFER = ("📋 Отметьтесь в списке сотрудников: нажмите кнопку и напишите своё ФИО.\n"
              "Если вы уже есть в списке — бот это отметит, если нет — добавит вас. Это займёт минуту.")
JOIN_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("👤 Отметиться в списке", callback_data="join")]])
GENDER_BUTTONS = InlineKeyboardMarkup([[InlineKeyboardButton("👨 Мужчина", callback_data="g:m"),
                                        InlineKeyboardButton("👩 Женщина", callback_data="g:f")]])
CONFIRM_BUTTONS = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Сохранить", callback_data="j:save"),
                                         InlineKeyboardButton("✏️ Заново", callback_data="j:again")],
                                        [InlineKeyboardButton("✖️ Отмена", callback_data="j:cancel")]])
CANCEL_HINT = "\n\n<i>Отменить — /cancel</i>"


def find_existing(name: str):
    """Есть ли человек в списке: полное совпадение или те же фамилия и имя."""
    person = store.find(name)
    if person:
        return person
    key = bd.name_key(" ".join(name.split()[:2]))
    return next((p for p in store.people if bd.name_key(" ".join(p["name"].split()[:2])) == key), None)


async def join_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    owner_mode = is_owner(user)
    if not owner_mode and viewer_status(user) != APPROVED:
        await deny(update, user)
        return ConversationHandler.END
    if owner_mode and not SHEET_ID:  # старый режим без таблицы: /add Фамилия Имя 20.02.1988
        await add(update, context)
        return ConversationHandler.END
    if not (SHEET_ID and SHEET_WEBAPP_URL):
        await message.reply_text("Добавление через бота пока не настроено.")
        return ConversationHandler.END
    joined = None if owner_mode else viewers()[str(user.id)].get("joined")
    await refresh(force=True)
    if joined:
        person = store.find(joined)
        if person and not bd.has_birthday(person):  # например, добавлен из кадровой таблицы без даты
            context.user_data["join"] = {"owner_mode": False, "link": joined}
            await message.reply_text(ASK_OWN_DATE + CANCEL_HINT, parse_mode=ParseMode.HTML)
            return LINK_DATE
        await message.reply_text(f"Вы уже отмечены в списке как «{joined}». "
                                 "Если что-то указано неверно — напишите владельцу бота.")
        return ConversationHandler.END
    context.user_data["join"] = {"owner_mode": owner_mode}
    who = "человека" if owner_mode else "свои"
    await message.reply_text(f"Напишите фамилию, имя и отчество {who} — как в документах.\n"
                             "Например: <b>Сапаров Ерлан Серікұлы</b>" + CANCEL_HINT, parse_mode=ParseMode.HTML)
    return JOIN_NAME


async def join_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = bd.clean_name(update.message.text)
    if len(name.split()) < 2 or len(name) > 80:
        await update.message.reply_text("Нужны хотя бы фамилия и имя, например: Сапаров Ерлан")
        return JOIN_NAME
    existing = find_existing(name)
    if existing and not context.user_data["join"]["owner_mode"]:
        context.user_data["join"]["link"] = existing["name"]
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Да, это я", callback_data="jl:yes"),
                                         InlineKeyboardButton("Нет", callback_data="jl:no")]])
        await update.message.reply_text(f"В списке уже есть: {fmt_person(existing)}\nЭто вы?",
                                        parse_mode=ParseMode.HTML, reply_markup=buttons)
        return JOIN_LINK
    if existing:
        await reply(update, context, f"Такой человек уже есть в списке: {fmt_person(existing)}\n"
                                     "Если там ошибка — исправьте её в таблице.")
        context.user_data.pop("join", None)
        return ConversationHandler.END
    context.user_data["join"]["name"] = name
    await update.message.reply_text("Дата рождения? Например: <b>20.08.2004</b>\n(год можно не писать: 20.08)"
                                    + CANCEL_HINT, parse_mode=ParseMode.HTML)
    return JOIN_DATE


async def join_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = bd.parse_date(update.message.text)
    if not parsed:
        await update.message.reply_text("Не понял дату. Напишите так: 20.08.2004 или 20.08")
        return JOIN_DATE
    month, day, year = parsed
    if year and not 1940 <= year <= today().year - 14:
        await update.message.reply_text("Проверьте год рождения и напишите дату ещё раз: 20.08.2004")
        return JOIN_DATE
    context.user_data["join"]["date"] = f"{day:02d}.{month:02d}" + (f".{year}" if year else "")
    await update.message.reply_text("Должность? Например: <b>Аналитик</b>\nЕсли не хотите указывать — напишите «-»"
                                    + CANCEL_HINT, parse_mode=ParseMode.HTML)
    return JOIN_POSITION


async def join_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    position = bd.clean_name(update.message.text)
    context.user_data["join"]["position"] = "" if position in ("-", "—", ".") else position[:60]
    await update.message.reply_text("Пол — нужен, чтобы выбрать цвет поздравительной карточки:", reply_markup=GENDER_BUTTONS)
    return JOIN_GENDER


async def join_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data["join"]
    data["female"] = query.data == "g:f"
    await query.edit_message_text(f"Пол: {'👩 Женщина' if data['female'] else '👨 Мужчина'}")
    if data["owner_mode"]:
        await query.message.reply_text("Текст поздравления — пришлите сообщением.\n"
                                       "Или нажмите /skip — текст напишет AI 🤖" + CANCEL_HINT, parse_mode=ParseMode.HTML)
        return JOIN_TEXT
    return await join_summary(query.message, data)


async def join_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["join"]
    data["text"] = "" if update.message.text.strip() == "/skip" else update.message.text.strip()
    return await join_summary(update.message, data)


async def join_summary(message, data):
    lines = [f"<b>{html.escape(data['name'])}</b>", f"🎂 {data['date']}"]
    if data["position"]:
        lines.append(f"💼 {html.escape(data['position'])}")
    lines.append("👩 Женщина" if data["female"] else "👨 Мужчина")
    if data["owner_mode"]:
        lines.append("✍️ Текст поздравления: " + ("свой" if data.get("text") else "напишет AI 🤖"))
    await message.reply_text("Проверьте:\n" + "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=CONFIRM_BUTTONS)
    return JOIN_CONFIRM


async def join_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("join")
    if data is None or query.data == "j:cancel":
        await query.edit_message_text("Отменено.")
        context.user_data.pop("join", None)
        return ConversationHandler.END
    if query.data == "j:again":
        context.user_data["join"] = {"owner_mode": data["owner_mode"]}
        await query.edit_message_text("Хорошо, заново. Фамилия, имя и отчество?" + CANCEL_HINT, parse_mode=ParseMode.HTML)
        return JOIN_NAME
    name = data["name"]
    card = " ".join(name.split()[:2])
    row = [name, data["date"], data["position"], "әйел" if data["female"] else "ер", card, data.get("text", "")]
    try:
        await asyncio.to_thread(sheet_call, "add", row=row)
    except Exception as error:
        log.exception("Не удалось добавить строку в таблицу")
        await query.edit_message_text(f"⚠️ Не получилось записать в таблицу: {html.escape(str(error))}\n"
                                      "Попробуйте позже или напишите владельцу бота.", parse_mode=ParseMode.HTML)
        context.user_data.pop("join", None)
        return ConversationHandler.END
    log.info("Добавлен в таблицу: %s (%s)", name, "владелец" if data["owner_mode"] else update.effective_user.id)
    context.user_data.pop("join", None)
    await refresh(force=True)
    await query.edit_message_reply_markup(None)
    if data["owner_mode"]:
        if not data.get("text"):
            await query.message.reply_text(f"✅ {name} добавлен(а) в таблицу. 🤖 AI пишет текст поздравления…")
            _, error = await ai_text(name)
            if error:
                await query.message.reply_text("⚠️ AI сейчас не ответил — таблица сама допишет текст в течение 15 минут.")
        else:
            await query.message.reply_text(f"✅ {name} добавлен(а) в таблицу.")
        await query.message.reply_text("Так будет выглядеть поздравление:")
        if store.find(name):
            await show_cards(update, context, name)
        return ConversationHandler.END

    user = update.effective_user
    viewers()[str(user.id)]["joined"] = name
    store.save()
    await query.message.reply_text("✅ Готово, вы в списке! Спасибо 🙌")
    text, _ = await ai_text(name)
    owner = store.data.get("owner_id")
    if owner:
        note = (f"🤖 Текст поздравления написал AI — посмотреть: <code>/card {html.escape(name.split()[0])}</code>, "
                f"переписать: <code>/ai {html.escape(name.split()[0])}</code>" if text else
                "🤖 Текст поздравления AI допишет в таблицу в течение 15 минут.")
        info = f"🆕 <b>{html.escape(user.full_name)}</b> добавил(а) себя в список:\n" \
               f"{html.escape(name)}, 🎂 {data['date']}" + (f", {html.escape(data['position'])}" if data["position"] else "") + \
               f", {'женщина' if data['female'] else 'мужчина'}\n\n{note}"
        button = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Удалить из списка", callback_data=f"rm:{user.id}")]])
        await context.bot.send_message(owner, info, parse_mode=ParseMode.HTML, reply_markup=button)
    return ConversationHandler.END


async def join_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("join", None)
    await update.effective_message.reply_text("Отменено.")
    return ConversationHandler.END


async def delete_from_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str):
    person = store.find(name)
    if person is None:
        matches = store.search(name)
        if len(matches) != 1:
            text = "Точного совпадения нет."
            if matches:
                text += " Похожие:\n" + "\n".join(f"• {html.escape(p['name'])}" for p in matches[:10])
            await reply(update, context, text)
            return
        person = matches[0]
    try:
        await asyncio.to_thread(sheet_call, "delete", name=person["name"])
    except Exception as error:
        await reply(update, context, f"⚠️ Не получилось удалить: {html.escape(str(error))}")
        return
    await refresh(force=True)
    await reply(update, context, f"🗑 Удалил из таблицы: {fmt_person(person)}")


async def on_remove_joined(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка владельца под уведомлением «добавил себя в список»."""
    query = update.callback_query
    if not is_owner(update.effective_user):
        await query.answer(NOT_OWNER, show_alert=True)
        return
    viewer = viewers().get(query.data.split(":", 1)[1], {})
    name = viewer.get("joined")
    if not name:
        await query.answer("Эта запись уже удалена.", show_alert=True)
        return
    try:
        await asyncio.to_thread(sheet_call, "delete", name=name)
    except Exception as error:
        await query.answer(f"Не получилось: {error}", show_alert=True)
        return
    viewer.pop("joined", None)
    store.save()
    await refresh(force=True)
    await query.answer("Удалено")
    await query.edit_message_text(f"{query.message.text_html}\n\n<b>🗑 Удалено из таблицы</b>", parse_mode=ParseMode.HTML)


def join_handler(persistent=False) -> ConversationHandler:
    """persistent — для webhook-режима: состояние диалога хранится в файле между запросами."""
    text = filters.TEXT & ~filters.COMMAND & ~MENU_FILTER
    return ConversationHandler(
        name="join", persistent=persistent,
        # Команда начала всегда начинает заново: иначе диалог, оборванный на середине, «залипает» навсегда
        # (в webhook-режиме нет таймаута), и бот молча игнорирует повторный /join.
        allow_reentry=True,
        entry_points=[CommandHandler("join", join_start), CallbackQueryHandler(join_start, pattern=r"^join$"),
                      CommandHandler("add", join_start)],
        states={
            JOIN_NAME: [MessageHandler(text, join_name)],
            JOIN_DATE: [MessageHandler(text, join_date)],
            JOIN_POSITION: [MessageHandler(text, join_position)],
            JOIN_GENDER: [CallbackQueryHandler(join_gender, pattern=r"^g:[mf]$")],
            JOIN_TEXT: [MessageHandler(text, join_text), CommandHandler("skip", join_text)],
            JOIN_CONFIRM: [CallbackQueryHandler(join_confirm, pattern=r"^j:(save|again|cancel)$")],
            JOIN_LINK: [CallbackQueryHandler(join_link, pattern=r"^jl:(yes|no)$")],
            LINK_DATE: [MessageHandler(text, link_date)],
        },
        fallbacks=[CommandHandler("cancel", join_cancel), MessageHandler(MENU_FILTER, on_menu)],
        # Таймауту нужен постоянно работающий планировщик — в webhook-режиме его нет.
        conversation_timeout=None if persistent else 30 * 60,
    )


async def join_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Человек нашёл себя в списке: запоминаем, кто он, — чтобы не звать его скидываться себе на подарок."""
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("join") or {}
    if query.data == "jl:no" or not data.get("link"):
        data.pop("link", None)
        await query.edit_message_text("Хорошо. Тогда напишите ФИО полностью, как в документах." + CANCEL_HINT,
                                      parse_mode=ParseMode.HTML)
        return JOIN_NAME
    user = update.effective_user
    name = data["link"]
    viewers()[str(user.id)]["joined"] = name
    store.save()
    log.info("Отметился в списке: %s → %s", user.id, name)
    await query.edit_message_text(f"✅ Готово! Бот знает, что вы — {html.escape(name)}.", parse_mode=ParseMode.HTML)
    owner = store.data.get("owner_id")
    if owner:
        await context.bot.send_message(owner, f"🔗 {html.escape(user.full_name)} отметил(а), что это — "
                                              f"<b>{html.escape(name)}</b>", parse_mode=ParseMode.HTML)
    person = store.find(name)
    if person and not bd.has_birthday(person) and SHEET_WEBAPP_URL:
        await query.message.reply_text(ASK_OWN_DATE + CANCEL_HINT, parse_mode=ParseMode.HTML)
        return LINK_DATE
    context.user_data.pop("join", None)
    return ConversationHandler.END


async def link_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сотрудник из списка без даты рождения сам пишет свою дату — она сразу попадает в таблицу."""
    parsed = bd.parse_date(update.message.text)
    if not parsed:
        await update.message.reply_text("Не понял дату. Напишите так: 20.08.2004 или 20.08")
        return LINK_DATE
    if parsed[2] and not 1940 <= parsed[2] <= today().year - 14:
        await update.message.reply_text("Проверьте год рождения и напишите дату ещё раз: 20.08.2004")
        return LINK_DATE
    name = (context.user_data.pop("join", None) or {}).get("link")
    if not name:
        return ConversationHandler.END
    error = await save_date(name, parsed)
    if error:
        log.warning("Не удалось записать дату %s: %s", name, error)
        await update.message.reply_text("⚠️ Не получилось сохранить — попробуйте позже (/join) или напишите владельцу бота.")
        return ConversationHandler.END
    await update.message.reply_text(f"✅ Записал: 🎂 {date_cell(parsed)}. Спасибо 🙌")
    owner = store.data.get("owner_id")
    if owner:
        await context.bot.send_message(owner, f"🎂 {html.escape(update.effective_user.full_name)} указал(а) дату рождения: "
                                              f"<b>{html.escape(name)}</b> — {date_cell(parsed)}", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


# ---------- /text — поменять текст поздравления ----------

def find_one(query: str):
    """Человек по имени: точное совпадение или единственный похожий. Иначе (None, список похожих)."""
    person = store.find(query)
    if person:
        return person, []
    matches = store.search(query)
    return (matches[0], []) if len(matches) == 1 else (None, matches)


async def text_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user):
        await deny(update, update.effective_user)
        return ConversationHandler.END
    query = " ".join(context.args)
    if not query:
        await reply(update, context, "Напишите, чей текст поменять: /text Омарова")
        return ConversationHandler.END
    if not (SHEET_ID and SHEET_WEBAPP_URL):
        await reply(update, context, f"Править текст через бота пока нельзя — правьте в таблице:\n{SHEET_URL}")
        return ConversationHandler.END
    await refresh(force=True)
    person, matches = find_one(query)
    if person is None:
        text = "Нашёл несколько — уточните:\n" + "\n".join(f"• {html.escape(p['name'])}" for p in matches[:15]) \
            if matches else f"Никого не нашёл по запросу «{html.escape(query)}»."
        await reply(update, context, text)
        return ConversationHandler.END
    context.user_data["text_for"] = person["name"]
    current = person.get("text")
    await reply(update, context, f"✍️ <b>{html.escape(person['name'])}</b>\n\n"
                                 + (f"Сейчас:\n<i>{html.escape(current)}</i>\n\n" if current else "Текста пока нет.\n\n")
                                 + "Пришлите новый текст поздравления одним сообщением." + CANCEL_HINT)
    return TEXT_EDIT


async def text_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data.pop("text_for", None)
    text = update.message.text.strip()
    if not name:
        return ConversationHandler.END
    try:
        await asyncio.to_thread(sheet_call, "set_text", name=name, text=text)
    except Exception as error:
        await reply(update, context, f"⚠️ Не получилось сохранить: {html.escape(str(error))}")
        return ConversationHandler.END
    await refresh(force=True)
    await reply(update, context, "✅ Текст обновлён. Так будет выглядеть поздравление:")
    await show_cards(update, context, name)
    return ConversationHandler.END


async def text_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("text_for", None)
    await update.effective_message.reply_text("Отменено.")
    return ConversationHandler.END


def text_handler(persistent=False) -> ConversationHandler:
    return ConversationHandler(
        name="text", persistent=persistent,
        # Команда начала всегда начинает заново: иначе диалог, оборванный на середине, «залипает» навсегда
        # (в webhook-режиме нет таймаута), и бот молча игнорирует повторный /join.
        allow_reentry=True,
        entry_points=[CommandHandler("text", text_start)],
        states={TEXT_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_FILTER, text_save)]},
        fallbacks=[CommandHandler("cancel", text_cancel), MessageHandler(MENU_FILTER, on_menu)],
        conversation_timeout=None if persistent else 30 * 60,
    )


# ---------- Сбор на подарок ----------
# Кому и когда отправить приглашение, решает владелец: бот только напоминает и готовит список.
# store.data["gifts"]: {"<id>": {"name", "date", "draft": [user_id] — выбраны к отправке,
#                                "members": [user_id] — уже получили приглашение, "paid": [user_id]}}

def gifts() -> dict:
    return store.data.setdefault("gifts", {})


def member_name(user_id: str) -> str:
    viewer = viewers().get(user_id, {})
    return viewer.get("joined") or viewer.get("name") or user_id


def gift_candidates() -> list:
    """Кого можно позвать: все с доступом, кроме владельца."""
    owner = str(store.data.get("owner_id"))
    return [uid for uid, v in viewers().items() if v.get("status") == APPROVED and uid != owner]


def ensure_gift(person, date: dt.date) -> str:
    """Сбор для человека на дату. Сразу выбраны все с доступом, кроме самого именинника (если он отметился)."""
    for gift_id, gift in gifts().items():
        if gift["name"] == person["name"] and gift["date"] == date.isoformat():
            return gift_id
    draft = [uid for uid in gift_candidates() if viewers()[uid].get("joined") != person["name"]]
    gift_id = f"{date:%y%m%d}{sum(g['date'] == date.isoformat() for g in gifts().values())}"
    gifts()[gift_id] = {"name": person["name"], "date": date.isoformat(), "draft": draft, "members": [], "paid": []}
    return gift_id


def gift_title(gift) -> str:
    date = dt.date.fromisoformat(gift["date"])
    return (f"🎁 <b>Сбор на подарок: {html.escape(gift['name'])}</b>\n"
            f"ДР {fmt_date(date)}, {when((date - today()).days)} · по {GIFT_AMOUNT} ₸")


def gift_picker(gift_id: str):
    """Выбор, кому отправить приглашение: ☑️ — выбран, ⬜ — нет, 📨 — уже получил."""
    gift = gifts()[gift_id]
    members, draft = set(gift["members"]), set(gift["draft"])
    to_send = [uid for uid in gift_candidates() if uid in draft and uid not in members]
    text = (f"{gift_title(gift)}\n\nКому отправить приглашение? Нажимайте на людей, чтобы выбрать.\n"
            f"Выбрано: <b>{len(to_send)}</b>" + (f" · уже получили: {len(members)}" if members else ""))
    buttons = []
    for uid in gift_candidates():
        mark = "📨" if uid in members else "☑️" if uid in draft else "⬜"
        buttons.append(InlineKeyboardButton(f"{mark} {member_name(uid)}"[:40], callback_data=f"gx:{gift_id}:{uid}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("Выбрать всех", callback_data=f"ga:{gift_id}"),
                 InlineKeyboardButton("Снять всех", callback_data=f"gz:{gift_id}")])
    rows.append([InlineKeyboardButton(f"📨 Отправить сейчас ({len(to_send)})", callback_data=f"gg:{gift_id}")])
    if members:
        rows.append([InlineKeyboardButton("📊 Кто сдал", callback_data=f"gr:{gift_id}")])
    return text, InlineKeyboardMarkup(rows)


def gift_board(gift_id: str):
    """Табло владельца: кто сдал, кто нет; нажатие на человека переключает отметку."""
    gift = gifts()[gift_id]
    paid = set(gift["paid"])
    total, count = len(gift["members"]), sum(uid in paid for uid in gift["members"])
    text = (f"{gift_title(gift)}\n"
            f"Сдали <b>{count} из {total}</b> — собрано <b>{count * GIFT_AMOUNT} ₸</b>\n\n"
            "<i>Нажмите на человека, чтобы отметить или снять отметку.</i>")
    buttons = [InlineKeyboardButton(f"{'✅' if uid in paid else '⬜'} {member_name(uid)}"[:40],
                                    callback_data=f"gt:{gift_id}:{uid}") for uid in gift["members"]]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("➕ Отправить ещё кому-то", callback_data=f"gs:{gift_id}"),
                 InlineKeyboardButton("🔄 Обновить", callback_data=f"gr:{gift_id}")])
    return text, InlineKeyboardMarkup(rows)


def gift_invite(gift) -> str:
    date = dt.date.fromisoformat(gift["date"])
    payee = f"\nПереводите: <b>{html.escape(GIFT_PAYEE)}</b>" if GIFT_PAYEE else ""
    return (f"🎁 <b>Скидываемся на подарок!</b>\n"
            f"У <b>{html.escape(gift['name'])}</b> день рождения {fmt_date(date)}.\n"
            f"Собираем по <b>{GIFT_AMOUNT} ₸</b>.{payee}\n\n"
            "Когда переведёте — нажмите кнопку 👇")


async def suggest_gifts(bot, chat_id, date: dt.date) -> bool:
    """Утром за GIFT_DAYS_BEFORE дней до ДР — предложить владельцу начать сбор. Сам бот никому не пишет."""
    if GIFT_DAYS_BEFORE <= 0:
        return False
    target = date + dt.timedelta(days=GIFT_DAYS_BEFORE)
    suggested = False
    for person in bd.birthdays_on(store.people, target):
        gift_id = ensure_gift(person, target)
        store.save()
        if gifts()[gift_id]["members"]:
            continue  # сбор уже запущен вручную
        button = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Выбрать, кому отправить", callback_data=f"gs:{gift_id}")]])
        await bot.send_message(chat_id, f"🎁 Через {GIFT_DAYS_BEFORE} {plural(GIFT_DAYS_BEFORE, 'день', 'дня', 'дней')} "
                                        f"день рождения у <b>{html.escape(person['name'])}</b>. Начать сбор на подарок?",
                               parse_mode=ParseMode.HTML, reply_markup=button)
        suggested = True
    return suggested


async def send_invites(bot, gift_id: str) -> int:
    gift = gifts()[gift_id]
    button = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Я перевёл(а) {GIFT_AMOUNT} ₸",
                                                         callback_data=f"gp:{gift_id}")]])
    delivered = 0
    for uid in [u for u in gift["draft"] if u not in gift["members"] and u in gift_candidates()]:
        try:
            await bot.send_message(int(uid), gift_invite(gift), parse_mode=ParseMode.HTML, reply_markup=button)
        except TelegramError:
            log.warning("Не удалось написать %s про сбор", uid)
            continue
        gift["members"].append(uid)
        delivered += 1
    store.save()
    return delivered


async def on_gift_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки владельца: gs — выбор людей, gx — отметить человека, ga/gz — всех/никого,
    gg — отправить, gt — отметить взнос, gr — табло, gn — начать сбор из /gift."""
    query = update.callback_query
    if not is_owner(update.effective_user):
        await query.answer(NOT_OWNER, show_alert=True)
        return
    action, gift_id, *rest = query.data.split(":")
    if action == "gn":  # gn:<гггг-мм-дд>:<номер человека в этот день>
        date = dt.date.fromisoformat(gift_id)
        people = bd.birthdays_on(store.people, date)
        index = int(rest[0])
        if index >= len(people):
            await query.answer("Список изменился — откройте /gift заново.", show_alert=True)
            return
        gift_id = ensure_gift(people[index], date)
        store.save()
        action = "gs"
    gift = gifts().get(gift_id)
    if gift is None:
        await query.answer("Этого сбора уже нет.", show_alert=True)
        return
    note = None
    if action == "gx":
        uid = rest[0]
        if uid in gift["members"]:
            await query.answer("Уже получил(а) приглашение.")
            return
        if uid in gift["draft"]:
            gift["draft"].remove(uid)
        else:
            gift["draft"].append(uid)
    elif action == "ga":
        gift["draft"] = gift_candidates()
    elif action == "gz":
        gift["draft"] = []
    elif action == "gt":
        uid = rest[0]
        if uid in gift["paid"]:
            gift["paid"].remove(uid)
        else:
            gift["paid"].append(uid)
    elif action == "gg":
        if not [u for u in gift["draft"] if u not in gift["members"]]:
            await query.answer("Никто не выбран.", show_alert=True)
            return
        delivered = await send_invites(context.bot, gift_id)
        note = f"📨 Приглашение отправлено: {delivered} чел."
    store.save()
    await query.answer(note or "")
    text, markup = gift_picker(gift_id) if action in ("gs", "gx", "ga", "gz") else gift_board(gift_id)
    if note:
        text = f"{text}\n\n{note}"
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except TelegramError:  # «message is not modified» — ничего не поменялось
        pass


async def on_gift_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка участника «Я перевёл(а)»."""
    query = update.callback_query
    gift = gifts().get(query.data.split(":", 1)[1])
    uid = str(update.effective_user.id)
    if gift is None or uid not in gift["members"]:
        await query.answer("Этого сбора уже нет.", show_alert=True)
        return
    if uid in gift["paid"]:
        await query.answer("Уже отмечено 👍")
        return
    gift["paid"].append(uid)
    store.save()
    await query.answer("Спасибо! 🙌")
    await query.edit_message_text(f"{query.message.text_html}\n\n✅ <b>Вы отметили, что перевели</b>",
                                  parse_mode=ParseMode.HTML)
    owner = store.data.get("owner_id")
    if owner:
        count = sum(u in gift["paid"] for u in gift["members"])
        await context.bot.send_message(
            owner, f"💸 {html.escape(member_name(uid))} отметил(а) взнос на подарок "
                   f"{html.escape(gift['name'])} ({count} из {len(gift['members'])}). Все сборы — /gift",
            parse_mode=ParseMode.HTML)


@owner_only
async def gift_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Идущие сборы (табло) + кнопки, чтобы начать сбор для ближайших именинников."""
    now = today()
    since = (now - dt.timedelta(days=3)).isoformat()
    running = sorted((g["date"], gid) for gid, g in gifts().items() if g["date"] >= since and g["members"])
    for _, gift_id in running:
        text, markup = gift_board(gift_id)
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    started = {(g["name"], g["date"]) for g in gifts().values() if g["members"]}
    buttons = []
    for date, person in bd.upcoming(store.people, now, 45):
        if (person["name"], date.isoformat()) in started:
            continue
        index = bd.birthdays_on(store.people, date).index(person)
        label = f"{date.day:02d}.{date.month:02d} — {card_name(person)}"
        buttons.append([InlineKeyboardButton(label[:40], callback_data=f"gn:{date.isoformat()}:{index}")])
    if buttons:
        await update.effective_message.reply_text(
            "🎁 Начать сбор — выберите именинника (ближайшие 45 дней).\n"
            "Потом выберете, кому отправить приглашение.", reply_markup=InlineKeyboardMarkup(buttons[:15]))
    elif not running:
        await reply(update, context, "В ближайшие 45 дней дней рождения нет.")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Ошибка при обработке обновления", exc_info=context.error)


def single_instance_lock():
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", INSTANCE_PORT))
    except OSError:
        lock.close()
        return None
    return lock


def main():
    if not BOT_TOKEN:
        log.error("Укажите BOT_TOKEN в файле .env (токен выдаёт @BotFather).")
        raise SystemExit(1)
    lock = single_instance_lock()
    if lock is None:
        log.info("Бот уже запущен — второй экземпляр не нужен.")
        return
    app = build_application(Application.builder().post_init(post_init))
    dated = sum(bd.has_birthday(p) for p in store.people)
    log.info("Сотрудников: %d, с датой рождения: %d. Напоминание в %s (%s).",
             len(store.people), dated, f"{NOTIFY_TIME:%H:%M}", TZ.key)
    if not store.people:
        log.warning("Список пуст: запустите import_excel.py или пришлите Excel-файл боту.")
    if not store.data.get("owner_id"):
        log.warning("Владелец не задан: откройте бота в Telegram и нажмите /start.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def build_application(builder=None, persistence=None) -> Application:
    """Бот со всеми обработчиками. persistence задаётся в webhook-режиме (webhook_app.py)."""
    builder = (builder or Application.builder()).token(BOT_TOKEN)
    if PROXY:
        builder = builder.proxy(PROXY).get_updates_proxy(PROXY)
    if persistence:
        builder = builder.persistence(persistence)
    app = builder.build()
    app.add_handler(join_handler(persistent=persistence is not None))
    app.add_handler(wish_handler(persistent=persistence is not None))
    app.add_handler(text_handler(persistent=persistence is not None))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("soon", soon))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CommandHandler("all", all_command))
    app.add_handler(CommandHandler("missing", missing))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("del", delete))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("card", card_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("notify", notify_command))
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("date", date_command))
    app.add_handler(CallbackQueryHandler(on_mute_button, pattern=r"^mute:(on|off)$"))
    app.add_handler(CallbackQueryHandler(on_access_button, pattern=r"^(ok|no|del):\d+$"))
    app.add_handler(CallbackQueryHandler(on_remove_joined, pattern=r"^rm:\d+$"))
    app.add_handler(CommandHandler("holidays", holidays_command))
    app.add_handler(CallbackQueryHandler(on_holiday_button, pattern=r"^hd:\w+$"))
    app.add_handler(CommandHandler("gift", gift_command))
    app.add_handler(CallbackQueryHandler(on_gift_paid, pattern=r"^gp:\d+$"))
    app.add_handler(CallbackQueryHandler(on_gift_owner, pattern=r"^(g[sxazgtr]:\d+(:\d+)?|gn:[\d-]+:\d+)$"))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(MENU_FILTER, on_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


if __name__ == "__main__":
    main()
