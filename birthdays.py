"""Хранение дней рождения сотрудников и импорт из Excel."""
import calendar
import csv
import datetime as dt
import io
import json
import re
import time
from pathlib import Path

import openpyxl

DATA_FILE = Path(__file__).resolve().parent / "data" / "birthdays.json"

# Варианты заголовков (в нижнем регистре). Порядок важен: побеждает первая подходящая колонка,
# поэтому «фио» стоит раньше «аты-жөні» — иначе подхватится «Латынша аты-жөні».
NAME_HEADERS = ("фио", "аты-жөні", "аты жөні", "имя", "name")
BIRTHDAY_HEADERS = ("туған күні", "дата рождения", "день рождения", "birthday")
POSITION_HEADERS = ("позиция", "лауазым", "должность", "position")
GENDER_HEADERS = ("жыныс", "пол", "gender")
CARD_NAME_HEADERS = ("карточка", "card")
TEXT_HEADERS = ("мәтін", "текст", "text")
DEPT_HEADERS = ("бөлім", "отдел", "department")
SKIP_SHEETS = ("архив",)
# Поля, которые живут только в памяти и не попадают в data/birthdays.json (а значит, и в git).
PRIVATE_FIELDS = ("text",)


def clean_name(name) -> str:
    return re.sub(r"\s+", " ", str(name)).strip()


def name_key(name) -> str:
    return clean_name(name).casefold()


def has_birthday(person) -> bool:
    return bool(person.get("month") and person.get("day"))


def occurrence(person, year: int) -> dt.date:
    """Дата дня рождения в указанном году (29 февраля в невисокосный год — 28 февраля)."""
    month, day = person["month"], person["day"]
    if month == 2 and day == 29 and not calendar.isleap(year):
        day = 28
    return dt.date(year, month, day)


def next_birthday(person, today: dt.date) -> dt.date:
    date = occurrence(person, today.year)
    return date if date >= today else occurrence(person, today.year + 1)


def age_on(person, date: dt.date):
    return date.year - person["year"] if person.get("year") else None


def birthdays_on(people, date: dt.date):
    return [p for p in people if has_birthday(p) and occurrence(p, date.year) == date]


def upcoming(people, today: dt.date, days: int):
    """[(дата, сотрудник)] на ближайшие `days` дней, включая сегодня."""
    result = []
    for person in people:
        if has_birthday(person):
            date = next_birthday(person, today)
            if (date - today).days <= days:
                result.append((date, person))
    result.sort(key=lambda item: (item[0], item[1]["name"]))
    return result


def parse_date(value):
    """Ячейка Excel или строка вида 20.02.1988 / 20.02 → (month, day, year | None) или None."""
    if value is None or value == "":
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.month, value.day, value.year
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Дата без формата ячейки — порядковый номер дня Excel.
        date = dt.date(1899, 12, 30) + dt.timedelta(days=int(value))
        return date.month, date.day, date.year
    match = re.fullmatch(r"\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}|\d{2}))?\s*", str(value))
    if not match:
        return None
    day, month = int(match[1]), int(match[2])
    year = int(match[3]) if match[3] else None
    if year is not None and year < 100:
        year += 2000 if year <= dt.date.today().year % 100 else 1900
    try:
        dt.date(year or 2000, month, day)  # 2000 — високосный, чтобы 29.02 без года проходило
    except ValueError:
        return None
    return month, day, year


def _find_column(headers, variants):
    for variant in variants:
        for index, header in enumerate(headers):
            if variant in header:
                return index
    return None


def _cell(row, index):
    return row[index] if index is not None and index < len(row) else None


def read_excel(source):
    """Сотрудники из первого подходящего листа: [{"name", "position", "birthday": (m, d, y) | None}]."""
    workbook = openpyxl.load_workbook(source, data_only=True, read_only=True)
    try:
        for sheet in workbook.worksheets:
            if sheet.title.strip().lower() in SKIP_SHEETS:
                continue
            rows = list(sheet.iter_rows(values_only=True))
            for header_index, header_row in enumerate(rows[:10]):
                headers = [str(c).strip().lower() if c is not None else "" for c in header_row]
                name_col = _find_column(headers, NAME_HEADERS)
                birthday_col = _find_column(headers, BIRTHDAY_HEADERS)
                if name_col is None or birthday_col is None:
                    continue
                position_col = _find_column(headers, POSITION_HEADERS)
                employees = []
                for row in rows[header_index + 1:]:
                    name = _cell(row, name_col)
                    if not isinstance(name, str) or not clean_name(name):
                        continue
                    position = _cell(row, position_col)
                    employees.append({
                        "name": clean_name(name),
                        "position": clean_name(position) if position else None,
                        "birthday": parse_date(_cell(row, birthday_col)),
                    })
                if employees:
                    return employees
                break
    finally:
        workbook.close()
    raise ValueError("В файле не найден лист с колонками «ФИО» и «Туған күні / Дата рождения».")


def is_female(value) -> bool:
    return clean_name(value or "").casefold().startswith(("ә", "ж", "қыз", "f", "w"))


def guess_female(name: str):
    """Пол по ФИО, когда колонка пустая (например, новый сотрудник из кадровой таблицы): True / False / None."""
    words = clean_name(name).casefold().split()
    if len(words) >= 3:
        if words[2].endswith(("қызы", "кызы", "овна", "евна", "ична", "kyzy", "ovna", "evna")):
            return True
        if words[2].endswith(("ұлы", "улы", "ович", "евич", "ич", "uly", "ovich", "evich")):
            return False
    if len(words) >= 2 and words[0].endswith(("ова", "ева", "ина", "ая", "ova", "eva", "ina")):
        return True
    if len(words) >= 2 and words[0].endswith(("ов", "ев", "ин", "ov", "ev", "in")):
        return False
    return None


def read_sheet_csv(text: str):
    """Google-таблица (CSV-экспорт): [{"name", "position", "birthday", "female", "card_name", "text"}]."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("Таблица пустая.")
    headers = [h.strip().lower() for h in rows[0]]
    cols = {key: _find_column(headers, variants) for key, variants in (
        ("name", NAME_HEADERS), ("birthday", BIRTHDAY_HEADERS), ("position", POSITION_HEADERS),
        ("gender", GENDER_HEADERS), ("card", CARD_NAME_HEADERS), ("text", TEXT_HEADERS), ("dept", DEPT_HEADERS))}
    if cols["name"] is None or cols["birthday"] is None:
        raise ValueError("В таблице нет колонок «Аты-жөні» и «Туған күні».")
    people = []
    for row in rows[1:]:
        name = clean_name(_cell(row, cols["name"]) or "")
        if not name:
            continue
        gender = clean_name(_cell(row, cols["gender"]) or "")
        female_known = is_female(gender) if gender else None
        people.append({
            "name": name,
            "position": clean_name(_cell(row, cols["position"]) or "") or None,
            "dept": clean_name(_cell(row, cols["dept"]) or "") or None,
            "birthday": parse_date(_cell(row, cols["birthday"])),
            "female": female_known if gender else bool(guess_female(name)),
            "female_known": female_known,
            "card_name": clean_name(_cell(row, cols["card"]) or "") or None,
            "text": (_cell(row, cols["text"]) or "").strip(),
        })
    return people


class Store:
    """JSON-файл с сотрудниками и настройками бота."""

    def __init__(self, path=DATA_FILE):
        self.path = Path(path)
        self.data = {"owner_id": None, "last_notified": None, "people": []}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    @property
    def people(self):
        return self.data["people"]

    def reload_meta(self):
        """Перечитывает с диска всё, кроме списка людей (в памяти он свежее: в нём тексты из таблицы).
        Нужно на сервере: утренняя задача и сайт — разные процессы, и оба пишут в один файл."""
        if self.path.exists():
            disk = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.update({k: v for k, v in disk.items() if k != "people"})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = dict(self.data, people=[{k: v for k, v in p.items() if k not in PRIVATE_FIELDS} for p in self.people])
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):  # OneDrive иногда ненадолго держит файл во время синхронизации
            try:
                tmp.replace(self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.5)

    def find(self, name):
        key = name_key(name)
        return next((p for p in self.people if name_key(p["name"]) == key), None)

    def search(self, text):
        key = name_key(text)
        return [p for p in self.people if key in name_key(p["name"])]

    def set_birthday(self, name, month, day, year=None):
        person = self.find(name)
        created = person is None
        if created:
            person = {"name": clean_name(name), "position": None, "source": "manual"}
            self.people.append(person)
        person.update(month=month, day=day, year=year)
        return person, created

    def remove(self, name):
        person = self.find(name)
        if person:
            self.people.remove(person)
        return person

    def load_sheet(self, text: str):
        """Список из Google-таблицы заменяет текущий целиком: таблица — главный источник."""
        people = []
        for row in read_sheet_csv(text):
            month, day, year = row["birthday"] or (None, None, None)
            people.append({"name": row["name"], "position": row["position"], "dept": row["dept"], "source": "sheet",
                           "month": month, "day": day, "year": year, "female": row["female"],
                           "female_known": row["female_known"],
                           "card_name": row["card_name"], "text": row["text"]})
        self.data["people"] = people
        return people

    def import_excel(self, source):
        """Обновляет список из Excel. Даты, добавленные вручную, и ручные записи не теряются."""
        employees = read_excel(source)
        old = {name_key(p["name"]): p for p in self.people}
        people, added, changed = [], [], []
        for employee in employees:
            key = name_key(employee["name"])
            if any(name_key(p["name"]) == key for p in people):
                continue  # дубль строки в файле
            prev = old.pop(key, None)
            person = {"name": employee["name"], "position": employee["position"], "source": "excel",
                      "month": None, "day": None, "year": None}
            if employee["birthday"]:
                person["month"], person["day"], person["year"] = employee["birthday"]
            elif prev and has_birthday(prev):
                person.update(month=prev["month"], day=prev["day"], year=prev.get("year"))
            if prev is None:
                added.append(person)
            elif (prev.get("month"), prev.get("day"), prev.get("year")) != (person["month"], person["day"], person["year"]):
                changed.append(person)
            people.append(person)
        manual = [p for p in old.values() if p.get("source") == "manual"]
        removed = [p for p in old.values() if p.get("source") != "manual"]
        self.data["people"] = people + manual
        return {
            "total": len(self.people),
            "with_date": sum(has_birthday(p) for p in self.people),
            "added": added,
            "changed": changed,
            "removed": removed,
            "missing": [p for p in self.people if not has_birthday(p)],
        }


def describe_import(report) -> str:
    def names(people):
        return ", ".join(p["name"] for p in people)

    lines = [f"Загружено сотрудников: {report['total']} (с датой рождения — {report['with_date']})."]
    for title, key in (("Новые", "added"), ("Изменилась дата", "changed"), ("Удалены (нет в файле)", "removed")):
        people = report[key]
        if people:
            lines.append(f"{title}: {len(people)}" + (f" — {names(people)}" if len(people) <= 10 else ""))
    if report["missing"]:
        lines.append(f"Без даты рождения ({len(report['missing'])}): {names(report['missing'])}")
    return "\n".join(lines)
