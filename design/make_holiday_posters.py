"""Общие постеры к праздникам: фон из holiday_backgrounds/ (картинки Figma AI, без текста), золотой заголовок и логотип.

python make_holiday_posters.py → ../cards/holidays/<id>.jpg
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import kz_holidays as kz  # noqa: E402

FONT = HERE.parent / "cards" / "font.ttf"
SIZE = 1600
TITLE_Y, TITLE_WIDTH, MAX_FONT = 0.775, 0.86, 128  # заголовок — в тёмной нижней части фона
LOGO_SIDE, LOCKUP_Y = 84, 0.935
# Золото сверху вниз: блик, тёплый тон, глубокая тень, снова светлее — как у металла.
GOLD_STOPS = ((0.0, (255, 246, 205)), (0.42, (248, 211, 120)), (0.62, (196, 138, 48)), (1.0, (255, 224, 146)))
LILAC = (226, 216, 255)


def darken_bottom(image):
    """Плавно затемняет низ, чтобы надпись читалась на любом фоне."""
    mask = Image.new("L", (1, SIZE))
    for y in range(SIZE):
        mask.putpixel((0, y), round(215 * max(0.0, (y / SIZE - 0.62) / 0.38) ** 0.8))
    image.paste(Image.new("RGB", image.size, (6, 6, 22)), (0, 0), mask.resize(image.size))


def gold_fill(top, bottom):
    gradient = Image.new("RGB", (1, SIZE), GOLD_STOPS[0][1])
    height = max(1, bottom - top)
    for y in range(SIZE):
        t = min(1.0, max(0.0, (y - top) / height))
        for (t0, c0), (t1, c1) in zip(GOLD_STOPS, GOLD_STOPS[1:]):
            if t <= t1:
                k = (t - t0) / (t1 - t0)
                gradient.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(c0, c1)))
                break
    return gradient.resize((SIZE, SIZE))


def wrap_title(title, font):
    lines, line = [], ""
    for word in title.split():
        candidate = f"{line} {word}".strip()
        if font.getlength(candidate) > SIZE * TITLE_WIDTH and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + [line]


def draw_title(image, title):
    size = MAX_FONT
    while True:
        font = ImageFont.truetype(str(FONT), size)
        lines = wrap_title(title, font)
        if len(lines) <= 2 and max(font.getlength(l) for l in lines) <= SIZE * TITLE_WIDTH:
            break
        size -= 2
    step = round(size * 1.16)
    first = SIZE * TITLE_Y - step * (len(lines) - 1) / 2
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    for i, line in enumerate(lines):
        draw.text((SIZE / 2, first + i * step), line, font=font, fill=255, anchor="mm")

    black = Image.new("RGB", image.size, (4, 2, 14))
    image.paste(black, (0, round(size * 0.045)), mask.filter(ImageFilter.GaussianBlur(size * 0.05)))
    # Тёплое свечение вокруг букв.
    glow = mask.filter(ImageFilter.GaussianBlur(size * 0.2)).point(lambda v: round(v * 0.5))
    image.paste(Image.new("RGB", image.size, (232, 165, 62)), (0, 0), glow)
    # Тёмная кромка и сами буквы: каждая строка получает свой перелив золота.
    edge = mask.filter(ImageFilter.MaxFilter(5))
    image.paste(Image.new("RGB", image.size, (92, 56, 14)), (0, 0), edge)
    for i in range(len(lines)):
        center = first + i * step
        top, bottom = round(center - size * 0.5), round(center + size * 0.5)
        band = Image.new("L", image.size, 0)
        ImageDraw.Draw(band).rectangle((0, round(center - step / 2), SIZE, round(center + step / 2)), fill=255)
        image.paste(gold_fill(top, bottom), (0, 0), ImageChops.multiply(mask, band))
    return first + (len(lines) - 1) * step + size * 0.5


def draw_lockup(image):
    """Внизу: логотип и «EduApp командасы атынан» одной строкой по центру."""
    font = ImageFont.truetype(str(FONT), 42)
    text = "EduApp командасы атынан"
    gap = 24
    total = LOGO_SIDE + gap + font.getlength(text)
    left = round((SIZE - total) / 2)
    y = round(SIZE * LOCKUP_Y)
    logo = Image.open(HERE / "logo.png").convert("RGBA").crop((10, 10, 502, 502)).resize((LOGO_SIDE, LOGO_SIDE), Image.LANCZOS)
    image.paste(logo, (left, y - LOGO_SIDE // 2), logo)
    ImageDraw.Draw(image).text((left + LOGO_SIDE + gap, y), text, font=font, fill=LILAC, anchor="lm")


def build(holiday):
    image = Image.open(HERE / "holiday_backgrounds" / f"{holiday['id']}.jpg").convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
    darken_bottom(image)
    draw_title(image, holiday["poster"])
    draw_lockup(image)
    kz.POSTERS_DIR.mkdir(parents=True, exist_ok=True)
    image.save(kz.poster_path(holiday), quality=90, subsampling=0)


if __name__ == "__main__":
    for item in kz.HOLIDAYS:
        build(item)
    print(f"Готово: {len(kz.HOLIDAYS)} постеров в {kz.POSTERS_DIR}")
