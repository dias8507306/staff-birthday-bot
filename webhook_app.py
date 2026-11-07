"""Бот в режиме webhook — для хостинга (PythonAnywhere): Telegram сам присылает каждое сообщение на сайт.

Компьютер не нужен. Состояние диалогов (/join, /add) между запросами хранится в data/state.pickle.
Адрес: https://<логин>.pythonanywhere.com/telegram, подключается один раз: python webhook_app.py setup
"""
import asyncio
import os
import sys

from flask import Flask, abort, request
from telegram import Bot, Update
from telegram.error import NetworkError
from telegram.ext import PicklePersistence

import bot

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
STATE_FILE = bot.BASE_DIR / "data" / "state.pickle"

app = Flask(__name__)


async def process(data: dict):
    bot.store.reload_meta()  # утренняя задача могла записать в файл новые сборы и отметки
    application = bot.build_application(persistence=PicklePersistence(STATE_FILE))
    # Прокси PythonAnywhere иногда на несколько секунд отвечает 503. Повторяем только запуск (getMe):
    # до обработки сообщения ещё ничего не отправлено, так что повтор ничего не задвоит.
    for attempt in range(4):
        try:
            await application.initialize()
            break
        except NetworkError:
            if attempt == 3:
                raise
            await asyncio.sleep(2 * (attempt + 1))
    try:
        await application.process_update(Update.de_json(data, application.bot))
        await application.update_persistence()
    finally:
        await application.shutdown()


@app.post("/telegram")
def telegram_webhook():
    # Telegram присылает секрет из setWebhook в этом заголовке — чужие запросы отбрасываем.
    if not WEBHOOK_SECRET or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)
    asyncio.run(process(request.get_json(force=True)))
    return "ok"


@app.post("/morning")
def morning():
    """Утренняя рассылка: раз в день вызывает GitHub Actions (на бесплатном тарифе нет своих задач по расписанию)."""
    if not WEBHOOK_SECRET or request.headers.get("X-Morning-Secret") != WEBHOOK_SECRET:
        abort(403)
    # ?not_before=9 — будильник GitHub стучится каждый час; до 09:00 по Алматы ничего не шлём.
    not_before = request.args.get("not_before", "")
    if request.args.get("test") != "1" and not_before.isdigit() and bot.dt.datetime.now(bot.TZ).hour < int(not_before):
        return f"{bot.today()}: рано, рассылка с {int(not_before):02d}:00."
    import notify_once
    return asyncio.run(notify_once.run(test=request.args.get("test") == "1", force=request.args.get("force") == "1"))


@app.post("/selftest")
def selftest():
    """Проверка с сервера: читается ли таблица и отвечает ли Gemini (тот же заголовок X-Morning-Secret)."""
    if not WEBHOOK_SECRET or request.headers.get("X-Morning-Secret") != WEBHOOK_SECRET:
        abort(403)
    import json
    import ai
    report = {"sheet": asyncio.run(bot.refresh(force=True)) or "ok", "people": len(bot.store.people),
              "without_text": sum(not p.get("text") for p in bot.store.people), "ai_key": ai.enabled()}
    try:
        report["site_expiry"] = str(bot.pa_expiry())
    except Exception as error:  # noqa: BLE001
        report["site_expiry"] = f"error: {error}"[:200]
    if ai.enabled() and request.args.get("ai") == "1":
        try:
            text, _ = ai.greeting({"name": "Тестов Тест", "position": "Аналитик"})
            report["ai"] = "ok: " + text[:60]
        except Exception as error:  # noqa: BLE001 — показываем причину как есть
            report["ai"] = f"error: {error}"[:300]
    return app.response_class(json.dumps(report, ensure_ascii=False), mimetype="application/json")


@app.get("/app")
def calendar_page():
    """Telegram Mini App: календарь дней рождения команды (web/calendar.html)."""
    from flask import send_file
    response = send_file(bot.BASE_DIR / "web" / "calendar.html", mimetype="text/html")
    response.headers["Cache-Control"] = "no-cache"
    return response


def _app_user():
    data = request.get_json(force=True, silent=True) or {}
    user = bot.check_init_data(data.get("initData", ""))
    if not user or not bot.member_allowed(int(user.get("id", 0))):
        abort(403)
    return user, data


@app.post("/api/calendar")
def api_calendar():
    user, _ = _app_user()
    bot.store.reload_meta()
    asyncio.run(bot.refresh())  # из таблицы, не чаще раза в 2 минуты
    return bot.calendar_payload(int(user["id"]))


@app.post("/api/wish")
def api_wish():
    user, data = _app_user()
    bot.store.reload_meta()
    error = bot.wish_from_app(user, data.get("name", ""), data.get("date", ""), data.get("text", ""))
    if error:
        return {"ok": False, "error": error}, 400
    date = data.get("date", "")
    if date == bot.today().isoformat() and (bot.store.data.get("morning") or {}).get("date") == date:
        asyncio.run(_deliver_now())  # таңғы хабар кетіп қойған — тілек бірден жетеді
    return {"ok": True}


async def _deliver_now():
    from telegram.request import HTTPXRequest
    telegram = Bot(bot.BOT_TOKEN, request=HTTPXRequest(proxy=bot.PROXY) if bot.PROXY else None)
    async with telegram:
        await bot.deliver_wishes(telegram, bot.today())


@app.get("/")
def index():
    return "EduApp birthday bot: ok"


async def setup(url: str):
    """Подключает webhook и меню команд. После этого бот на компьютере (getUpdates) работать не будет."""
    async with Bot(bot.BOT_TOKEN) as telegram:
        await telegram.set_webhook(url, secret_token=WEBHOOK_SECRET, allowed_updates=["message", "callback_query"],
                                   drop_pending_updates=False)
        await bot.set_commands(telegram)
        print(await telegram.get_webhook_info())


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "setup":
    asyncio.run(setup(sys.argv[2]))
