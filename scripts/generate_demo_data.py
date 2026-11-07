"""Generates a fake staff list for the demo: data/birthdays.json + the demo block of web/calendar.html.

Every person is random: plausible Kazakh names (male and female, so both card colours are used),
random birthdays, random positions/departments. Nothing here refers to real people.

    python scripts/generate_demo_data.py            # default seed → the committed dataset
    python scripts/generate_demo_data.py --seed 7   # another random team
"""
import argparse
import datetime as dt
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "birthdays.json"
CALENDAR = ROOT / "web" / "calendar.html"

MALE = ["Ерлан", "Нұрлан", "Арман", "Бауыржан", "Ержан", "Санжар", "Темірлан", "Асхат", "Дәурен", "Қанат",
        "Мұрат", "Ерасыл", "Олжас", "Әлихан", "Самат", "Тимур", "Азамат", "Дастан", "Жасұлан", "Нұрсұлтан"]
FEMALE = ["Айгерім", "Дана", "Мадина", "Әсел", "Салтанат", "Жансая", "Камила", "Ақбота", "Динара", "Томирис",
          "Ләззат", "Гүлнұр", "Аяжан", "Іңкәр", "Перизат", "Айым", "Балнұр", "Жұлдыз", "Мөлдір", "Сабина"]
# Surname stems: male = stem + "ов"/"ев", female = stem + "ова"/"ева". Plus a few gender-neutral Kazakh surnames.
STEMS = ["Сапар", "Ахмет", "Омар", "Жұмабай", "Бекет", "Серік", "Әбдір", "Қасен", "Мұрат", "Нұрпейіс",
         "Есен", "Оспан", "Тілеу", "Дәулет", "Ермек", "Сұлтан", "Жақып", "Әлім", "Тұрсын", "Исмаил"]
NEUTRAL = ["Сәрсенбай", "Қуанышбек", "Ерғали", "Жақсылық", "Мұхтар", "Бейсен", "Құдайберген", "Әміржан"]
FATHERS = ["Серік", "Ержан", "Марат", "Асқар", "Мұрат", "Дәурен", "Қанат", "Бауыржан", "Самат", "Есен"]

ROLES = [
    ("Content", ["Автор-разработчик контента", "Контент инженер", "Старший эксперт", "Главный эксперт"]),
    ("IT", ["Фуллстэк-разработчик", "Мобайл-разработчик", "Дата-сайнтист", "UI/UX-дизайнер", "Проект-менеджер"]),
    ("Analytics", ["Дата-аналитик"]),
    ("Marketing", ["Смм-менеджер", "Дизайнер", "Таргетолог"]),
    ("Sales", ["Отдел продаж"]),
    ("Office", ["Руководитель офиса", "HR-менеджер"]),
]

LATIN = str.maketrans({"а": "a", "ә": "a", "б": "b", "в": "v", "г": "g", "ғ": "g", "д": "d", "е": "e", "ж": "zh",
                       "з": "z", "и": "i", "й": "i", "і": "i", "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n",
                       "ң": "n", "о": "o", "ө": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ұ": "u",
                       "ү": "u", "ф": "f", "х": "kh", "һ": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
                       "ы": "y", "э": "e", "ю": "yu", "я": "ya"})


def latin(text: str) -> str:
    return " ".join(w.lower().translate(LATIN).capitalize() for w in text.split())


def surname(rng: random.Random, female: bool) -> str:
    if rng.random() < 0.2:
        return rng.choice(NEUTRAL)
    stem = rng.choice(STEMS)
    base = stem[:-1] + "ев" if stem.endswith("й") else stem + "ов"
    return base + ("а" if female else "")


def make_person(rng: random.Random, female: bool, used: set):
    while True:
        first = rng.choice(FEMALE if female else MALE)
        last = surname(rng, female)
        if (last, first) not in used:
            used.add((last, first))
            break
    name = f"{last} {first}"
    if rng.random() < 0.45:  # some rows carry a patronymic (…ұлы / …қызы), like a real HR export
        name += " " + rng.choice(FATHERS) + ("қызы" if female else "ұлы")
    dept, positions = rng.choice(ROLES)
    year = rng.randint(1975, 2005) if rng.random() < 0.8 else None
    date = dt.date(2000, 1, 1) + dt.timedelta(days=rng.randrange(366))  # 2000 is a leap year → 29 Feb possible
    card = f"{last} {first}"
    if rng.random() < 0.1:
        card = latin(card)  # the card name column sometimes holds a Latin spelling
    gender_known = rng.random() < 0.85  # otherwise the bot guesses the gender from the name
    return {
        "name": name, "position": rng.choice(positions), "dept": dept, "source": "sheet",
        "month": date.month, "day": date.day, "year": year,
        "female": female, "female_known": female if gender_known else None, "card_name": card,
    }


def generate(seed: int, size: int):
    rng = random.Random(seed)
    used = set()
    people = [make_person(rng, rng.random() < 0.55, used) for _ in range(size)]
    for person in rng.sample(people, 3):  # a few colleagues without a known birthday → /missing
        person.update(month=None, day=None, year=None)
    viewers = {str(100000001 + i): {"name": p["name"].split()[1], "username": f"eduapp_demo_{i + 1}",
                                    "status": "approved" if i < 4 else "pending"}
               for i, p in enumerate(rng.sample(people, 5))}
    return {"owner_id": None, "last_notified": None, "people": people, "viewers": viewers}


def patch_calendar(data, seed: int):
    """Rewrites the offline demo of the Mini App (web/calendar.html?demo) with people from the fake dataset."""
    rng = random.Random(seed + 1)
    dated = [p for p in data["people"] if p["month"]]
    picked = rng.sample(dated, 5)
    offsets = (3, 9, 17, 23, 40)
    rows = []
    for i, (person, offset) in enumerate(zip(picked, offsets)):
        age = rng.randint(22, 45)
        me = ", true" if i == len(picked) - 1 else ""
        short = " ".join(person["name"].split()[:2])
        rows.append(f'mk("{short}", "{person["position"]}", "{person["dept"]}", {offset}, {age}{me})')
    block = "    " + ",\n    ".join(", ".join(rows[i:i + 2]) for i in range(0, len(rows), 2)) + "],"
    html = CALENDAR.read_text(encoding="utf-8")
    pattern = re.compile(r"(// DEMO_PEOPLE:BEGIN[^\n]*\n).*?(\n\s*// DEMO_PEOPLE:END)", re.S)
    if not pattern.search(html):
        raise SystemExit("web/calendar.html: DEMO_PEOPLE markers not found")
    CALENDAR.write_text(pattern.sub(lambda m: m[1] + block + m[2], html), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--size", type=int, default=40)
    args = parser.parse_args()
    data = generate(args.seed, args.size)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    patch_calendar(data, args.seed)
    women = sum(p["female"] for p in data["people"])
    print(f"{DATA_FILE.relative_to(ROOT).as_posix()}: {len(data['people'])} people ({women} female), "
          f"{len(data['viewers'])} viewers; web/calendar.html demo block updated.")


if __name__ == "__main__":
    main()
