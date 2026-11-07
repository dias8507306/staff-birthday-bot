"""Утренняя рассылка. Каждое утро в 9:00 её запускает Apps Script Google-таблицы (будильник tick, запасной — GitHub
Actions) запросом на сайт бота (/morning в webhook_app.py), поэтому всё делается на сервере. Можно и вручную:

python notify_once.py          — владельцу: карточка и текст именинника, напоминания за 7, 2 и 1 день, праздники,
                                 сбор на подарок, срок сайта; всем с доступом — в день рождения и за 7, 2, 1 день.
                                 Если повода нет — никому ничего не приходит.
python notify_once.py --force  — то же, даже если сегодня уже отправляли
python notify_once.py --test   — проверочное сообщение владельцу с ближайшими днями рождения

Повторный запрос в тот же день досылает только тем, кому не дошло (store.data["morning"]), — поэтому будильник
может стучаться сколько угодно раз. Если кому-то отправить не удалось, ответ — ошибка, и будильник повторит позже.
"""
import asyncio
import sys
import time

from telegram import Bot
from telegram.error import Forbidden, NetworkError, TelegramError
from telegram.request import HTTPXRequest

import bot


async def with_retries(make_call, attempts=3):
    """Прокси PythonAnywhere иногда на несколько секунд отвечает 503 — повторяем."""
    for attempt in range(attempts):
        try:
            return await make_call()
        except NetworkError:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(3 * (attempt + 1))


async def run(test=False, force=False, fill_limit=3) -> str:
    """fill_limit — сколько недостающих текстов AI допишет после рассылки (по запросу /morning — немного,
    чтобы ответить быстро; ежедневная задача сервера — все)."""
    owner = bot.store.data.get("owner_id")
    if not bot.BOT_TOKEN or not owner:
        raise RuntimeError("Нет BOT_TOKEN или владельца (owner_id в data/birthdays.json).")
    if not test and not force:
        # Будильники стучатся много раз в день — быстрый ответ, если сегодня всем уже отправлено.
        bot.store.reload_meta()
        progress = bot.store.data.get("morning") or {}
        if progress.get("date") == bot.today().isoformat() and \
                {str(c) for c in bot.morning_recipients()} <= set(progress.get("done", [])) and \
                not bot.has_undelivered_wishes(bot.today()):
            # Будильник всё равно пришёл — заодно AI допишет несколько недостающих текстов (новые сотрудники).
            written = 0
            if fill_limit and not await bot.refresh(force=True):
                written = await bot.fill_missing_texts(bot.today(), limit=fill_limit)
            return f"{bot.today()}: сегодня уже отправлено." + (f" AI написал текстов: {written}." if written else "")
    request = HTTPXRequest(proxy=bot.PROXY) if bot.PROXY else None
    telegram = Bot(bot.BOT_TOKEN, request=request)
    await with_retries(telegram.initialize, attempts=4)
    async with telegram:
        if test:
            error = await bot.refresh(force=True)
            text = "✅ <b>Проверка: утренние сообщения работают.</b>\n\n" + bot.soon_text(30)
            if error:
                text += f"\n\n⚠️ Google-таблица не прочиталась: {error}"
            await bot.send(telegram, owner, text)
            return f"{bot.today()}: проверочное сообщение отправлено."

        date = bot.today()
        bot.store.reload_meta()
        progress = bot.store.data.get("morning") or {}
        if progress.get("date") != date.isoformat() or force:
            progress = {"date": date.isoformat(), "done": []}
        bot.store.data["morning"] = progress
        await bot.refresh(force=True)
        # У кого день рождения на этой неделе, а текста нет — AI пишет до рассылки, чтобы карточка ушла с текстом.
        await bot.fill_missing_texts(date, limit=5, urgent_only=True)

        failed = []
        for chat_id in bot.morning_recipients():
            if str(chat_id) in progress["done"]:
                continue
            try:
                if chat_id == owner:
                    await with_retries(lambda: bot.send_morning(telegram, owner, date), attempts=2)
                    bot.store.data["last_notified"] = date.isoformat()
                else:
                    await with_retries(lambda: bot.send_viewer_morning(telegram, chat_id, date))
            except Forbidden:  # человек остановил бота — больше не пытаемся сегодня
                bot.log.warning("Утро: %s остановил(а) бота", chat_id)
            except TelegramError as error:
                bot.log.warning("Утро: не удалось отправить %s: %s", chat_id, error)
                failed.append(chat_id)
                continue
            progress["done"].append(str(chat_id))
            bot.store.save()
        try:  # тілектер қабырғасы: жиналған тілектер владельцу и WISH_RECIPIENTS (.env)
            await bot.deliver_wishes(telegram, date)
        except TelegramError as error:
            bot.log.warning("Тілектер жеткізілмеді: %s", error)
            failed.append("wishes")
        bot.store.save()

    sent = len(progress["done"])
    if failed:
        # Ошибка → будильник повторит позже и дошлёт только им.
        raise RuntimeError(f"{date}: отправлено {sent}, не удалось {len(failed)}: {failed}")
    if fill_limit:
        written = await bot.fill_missing_texts(date, limit=fill_limit)
        if written:
            return f"{date}: утреннее сообщение получили {sent}; AI написал текстов: {written}."
    return f"{date}: утреннее сообщение получили {sent}."


def run_with_retries(tries=8, pause=300) -> str:
    """Для ежедневной задачи PythonAnywhere: если прокси или Telegram подвели — пробуем ещё, каждые 5 минут."""
    for attempt in range(1, tries + 1):
        try:
            return asyncio.run(run(fill_limit=60))
        except Exception as error:  # noqa: BLE001 — журнал задачи покажет причину, а мы попробуем снова
            print(f"Попытка {attempt}: {error}", flush=True)
            if attempt == tries:
                raise
            time.sleep(pause)


if __name__ == "__main__":
    if "--retry" in sys.argv:
        print(run_with_retries())
    else:
        print(asyncio.run(run(test="--test" in sys.argv, force="--force" in sys.argv, fill_limit=60)))
