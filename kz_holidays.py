"""Праздники Казахстана, в которые люди отдыхают: даты, общий текст поздравления и постер (cards/holidays/<id>.jpg)."""
import datetime as dt
from pathlib import Path

POSTERS_DIR = Path(__file__).resolve().parent / "cards" / "holidays"

# Дату Курбан-айта объявляет ДУМК, она меняется каждый год: нужно вписывать сюда на каждый новый год.
# Выходной — только первый день. Дата может сдвинуться на день из-за новолуния.
KURBAN_DATES = {2027: (5, 16)}

SIGNATURE = "\n\nІзгі тілекпен, EduApp командасы 💜"

# id, название, дата (месяц, день; None — плавающая), сколько дней, переносится ли выходной с субботы и воскресенья,
# заголовок постера и текст поздравления.
HOLIDAYS = [
    {"id": "new_year", "name": "Новый год", "date": (1, 1), "days": 2, "moves": True,
     "poster": "ЖАҢА ЖЫЛЫҢЫЗБЕН!",
     "text": "Құрметті әріптестер, Жаңа жылыңыз құтты болсын! 🎄\n\n"
             "Жаңа жыл әр отбасына амандық, береке мен қуаныш әкелсін. "
             "Барлық жоспарларыңыз жүзеге асып, жаңа белестер бағындыра берейік!"},
    {"id": "women", "name": "Международный женский день", "date": (3, 8), "days": 1, "moves": True,
     "poster": "8 НАУРЫЗ МЕРЕКЕСІМЕН!",
     "text": "Қымбатты әріптес қыз-келіншектер, 8 наурыз мерекесімен құттықтаймыз! 🌷\n\n"
             "Күлкілеріңіз әрдайым жарасып, отбасыларыңыз аман, өздеріңіз бақытты болыңыздар. "
             "Командамызға сыйлайтын жылулығыңыз бен шабытыңыз үшін рахмет!"},
    {"id": "constitution", "name": "День Конституции", "date": (3, 15), "days": 1, "moves": True,
     "poster": "КОНСТИТУЦИЯ КҮНІМЕН!",
     "text": "Құрметті әріптестер, Конституция күнімен құттықтаймыз! 🇰🇿\n\n"
             "Еліміздің іргесі берік, заңы мен әділдігі салтанат құрсын. "
             "Әрқайсымыздың еңбегіміз Қазақстанның дамуына үлес қоса берсін!"},
    {"id": "nauryz", "name": "Наурыз мейрамы", "date": (3, 21), "days": 3, "moves": True,
     "poster": "НАУРЫЗ ҚҰТТЫ БОЛСЫН!",
     "text": "Құрметті әріптестер, Әз-Наурыз мейрамы құтты болсын! 🌱\n\n"
             "Ұлыстың ұлы күні әр үйге молшылық пен ынтымақ әкелсін. "
             "Ақ мол болсын, жаңа жыл жаңа бастамаларға жол ашсын!"},
    {"id": "unity", "name": "Праздник единства народа Казахстана", "date": (5, 1), "days": 1, "moves": True,
     "poster": "БІРЛІК МЕРЕКЕСІМЕН!",
     "text": "Құрметті әріптестер, Қазақстан халқының бірлігі мерекесімен құттықтаймыз! 🤝\n\n"
             "Бірлігіміз бекем, ынтымағымыз жарасымды болсын. "
             "Бір команда болып бірге жеткен әр жетістігіміз еселене берсін!"},
    {"id": "defender", "name": "День защитника Отечества", "date": (5, 7), "days": 1, "moves": True,
     "poster": "ОТАН ҚОРҒАУШЫ КҮНІМЕН!",
     "text": "Құрметті әріптестер, Отан қорғаушы күнімен құттықтаймыз! 🎖\n\n"
             "Еліміздің тыныштығын сақтап жүрген барша азаматтарға алғыс айтамыз. "
             "Елімізге бейбітшілік, өздеріңізге денсаулық пен мықты рух тілейміз!"},
    {"id": "victory", "name": "День Победы", "date": (5, 9), "days": 1, "moves": True,
     "poster": "ЖЕҢІС КҮНІМЕН!",
     "text": "Құрметті әріптестер, Жеңіс күнімен құттықтаймыз! 🕊\n\n"
             "Батыр бабаларымыздың ерлігі мәңгі есімізде. "
             "Еліміздің аспаны әрдайым ашық, жеріміз тыныш болсын!"},
    {"id": "kurban", "name": "Курбан-айт", "date": None, "days": 1, "moves": False,
     "poster": "ҚҰРБАН АЙТ ҚҰТТЫ БОЛСЫН!",
     "text": "Құрметті әріптестер, Құрбан айт мерекесі құтты болсын! 🌙\n\n"
             "Құрбандықтарыңыз қабыл болып, шаңырақтарыңызға береке мен бейбітшілік орнасын. "
             "Ағайын-туыспен татулығымыз арта берсін!"},
    {"id": "capital", "name": "День столицы", "date": (7, 6), "days": 1, "moves": True,
     "poster": "ЕЛОРДА КҮНІМЕН!",
     "text": "Құрметті әріптестер, Елорда күнімен құттықтаймыз! 🏙\n\n"
             "Астанамыз көркейіп, еліміздің мақтанышы бола берсін. "
             "Әрқайсымызға жаңа мүмкіндіктер мен биік мақсаттар тілейміз!"},
    {"id": "republic", "name": "День Республики", "date": (10, 25), "days": 1, "moves": True,
     "poster": "РЕСПУБЛИКА КҮНІМЕН!",
     "text": "Құрметті әріптестер, Республика күнімен құттықтаймыз! 🇰🇿\n\n"
             "Еліміз тәуелсіздік жолында берік қадам басып, өркендей берсін. "
             "Әр шаңыраққа амандық пен молшылық тілейміз!"},
    {"id": "independence", "name": "День Независимости", "date": (12, 16), "days": 1, "moves": True,
     "poster": "ТӘУЕЛСІЗДІК КҮНІМЕН!",
     "text": "Құрметті әріптестер, Тәуелсіздік күнімен құттықтаймыз! 🇰🇿\n\n"
             "Тәуелсіздігіміз тұғырлы, еліміз тыныш, халқымыз бақытты болсын. "
             "Қазақстанның ертеңіне бірге еңбек ете берейік!"},
]
BY_ID = {h["id"]: h for h in HOLIDAYS}


def poster_path(holiday) -> Path:
    return POSTERS_DIR / f"{holiday['id']}.jpg"


def greeting(holiday) -> str:
    return holiday["text"] + SIGNATURE


def first_day(holiday, year: int):
    """Первый день праздника в этом году; None — если дата Курбан-айта ещё не внесена."""
    month_day = holiday["date"] or KURBAN_DATES.get(year)
    return dt.date(year, *month_day) if month_day else None


def _days(holiday, start: dt.date):
    return [start + dt.timedelta(days=i) for i in range(holiday["days"])]


def holidays_in(year: int):
    """[(первый день, праздник)] за год, по дате."""
    items = [(first_day(h, year), h) for h in HOLIDAYS]
    return sorted(((d, h) for d, h in items if d), key=lambda item: item[0])


def moved_days_off(holiday, start: dt.date):
    """Рабочие дни, на которые переносится выходной (пятидневка): праздник в субботу или воскресенье даёт
    выходной на следующий рабочий день. На Курбан-айт перенос не действует."""
    if not holiday["moves"]:
        return []
    taken = {d for s, h in holidays_in(start.year) + holidays_in(start.year + 1) for d in _days(h, s)}
    moved = []
    for day in _days(holiday, start):
        if day.weekday() < 5:
            continue
        target = day + dt.timedelta(days=1)
        while target.weekday() >= 5 or target in taken or target in moved:
            target += dt.timedelta(days=1)
        moved.append(target)
    return moved


def upcoming(today: dt.date, days: int):
    """[(первый день, праздник)] на ближайшие `days` дней, включая сегодня."""
    items = holidays_in(today.year) + holidays_in(today.year + 1)
    return [(d, h) for d, h in items if 0 <= (d - today).days <= days]


def starting_on(date: dt.date):
    return [h for d, h in holidays_in(date.year) if d == date]


def next_date(holiday, today: dt.date):
    for year in (today.year, today.year + 1):
        date = first_day(holiday, year)
        if date and date >= today:
            return date
    return None
