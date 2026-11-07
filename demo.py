"""Demo mode: no Telegram token, no Google Sheet, no AI key needed.

Loads the fake team from data/birthdays.json, prints what the bot would send this morning
(today's birthdays, reminders, upcoming list, holidays) and renders greeting cards to output/.

    python demo.py                       # as of today
    python demo.py --date 2026-12-14     # pretend it is another day
    python demo.py --out my_folder
"""
import argparse
import datetime as dt
import html
import os
import re
import shutil
import sys
from io import BytesIO
from pathlib import Path

# Demo never talks to external services, even if a .env with real values happens to exist.
for key in ("BOT_TOKEN", "SHEET_ID", "SHEET_WEBAPP_URL", "GEMINI_API_KEY", "PA_USERNAME", "PA_API_TOKEN", "OWNER_ID"):
    os.environ[key] = ""

from PIL import Image  # noqa: E402

import ai  # noqa: E402
import birthdays as bd  # noqa: E402
import bot  # noqa: E402  — reuses the bot's own formatting functions and data store
import kz_holidays as kz  # noqa: E402
from card import make_card  # noqa: E402


def plain(text: str) -> str:
    """Telegram HTML → console text."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def section(title: str, body: str):
    print(f"\n=== {title} ===")
    print(plain(body) if body else "(nothing)")


def save_card(person, out: Path, prefix: str) -> Path:
    jpeg = make_card(bot.card_name(person), bool(person.get("female")))
    path = out / f"{prefix}_{'female' if person.get('female') else 'male'}.png"
    Image.open(BytesIO(jpeg)).save(path)
    return path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Offline demo of the staff birthday bot")
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "output")
    args = parser.parse_args()

    date = args.date or bot.today()
    bot.today = lambda: date  # soon_text() and friends ask the bot for "today"
    people = bot.store.people
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    print(f"Demo date: {date:%Y-%m-%d} | staff in data/birthdays.json: {len(people)} "
          f"(with birthday: {sum(bd.has_birthday(p) for p in people)})")

    todays = bd.birthdays_on(people, date)
    section("Today's birthdays", "\n".join(f"• {bot.fmt_person(p, date)}" for p in todays))
    section(f"Morning reminders ({', '.join(map(str, bot.REMIND_DAYS))} days before)",
            bot.daily_text(date, include_today=False))
    section("Upcoming 30 days (/soon)", bot.soon_text(30))
    section("Holidays in the next 60 days", "\n".join(
        f"• {h['name']} — {bot.fmt_holiday_days(h, d)}" for d, h in kz.upcoming(date, 60)))
    section("Without a birthday (/missing)", ", ".join(p["name"] for p in people if not bd.has_birthday(p)))

    # Cards: today's people, otherwise the next man and the next woman in the calendar.
    picks = todays or []
    if not picks:
        upcoming = [p for _, p in bd.upcoming(people, date, 366)]
        picks = [p for p in (next((p for p in upcoming if not p.get("female")), None),
                             next((p for p in upcoming if p.get("female")), None)) if p]
    print("\n=== Rendered files ===")
    for i, person in enumerate(picks, 1):
        print(f"card  {save_card(person, out, f'card{i}')}  ← {person['name']}")
    holidays = kz.upcoming(date, 366)
    if holidays:
        day, holiday = holidays[0]
        poster = kz.poster_path(holiday)
        if poster.exists():
            target = out / f"holiday_{holiday['id']}.jpg"
            shutil.copyfile(poster, target)
            print(f"poster {target}  ← {holiday['name']}, {day}")
        section("Holiday greeting text", kz.greeting(holiday))

    if picks:
        prompt = ai.build_prompt(picks[0], examples=())
        section("AI prompt that would be sent to Gemini (GEMINI_API_KEY not set in demo)",
                "\n".join(prompt.splitlines()[:6]) + "\n…")


if __name__ == "__main__":
    main()
