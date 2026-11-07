"""Поздравительная карточка: фон из cards/ (male / female) + имя человека в центре."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CARDS_DIR = Path(__file__).resolve().parent / "cards"
FONT_CANDIDATES = (CARDS_DIR / "font.ttf", Path("C:/Windows/Fonts/segoeuib.ttf"),
                   Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
# Зона для имени на фоне 2048×2048: между «ТУҒАН КҮНІҢІЗБЕН!» и «EduApp командасы атынан».
CENTER_Y, MAX_WIDTH, MAX_HEIGHT = 1120, 1040, 440
MAX_FONT, LINE_GAP = 210, 0.12


def font_path():
    return next((p for p in FONT_CANDIDATES if p.exists()), None)


def load_font(size: int):
    path = font_path()
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default(size)


def load_background(female: bool) -> Image.Image:
    """Фон из cards/. Если файла нет (свой дизайн ещё не положили) — простой градиент, карточка всё равно рисуется.
    Нейтральные фоны генерирует design/make_demo_backgrounds.py."""
    path = CARDS_DIR / ("female.jpg" if female else "male.jpg")
    if path.exists():
        return Image.open(path).convert("RGBA")
    top, bottom = ((196, 76, 112), (110, 22, 62)) if female else ((58, 30, 120), (12, 10, 48))
    mask = Image.linear_gradient("L").resize((2048, 2048))
    return Image.composite(Image.new("RGBA", mask.size, bottom + (255,)), Image.new("RGBA", mask.size, top + (255,)), mask)


def card_lines(name: str):
    """«Фамилия Имя Отчество» → две строки: фамилия и имя (отчество на карточку не пишем)."""
    words = name.split()
    return words[:2] if len(words) >= 2 else words


def make_card(name: str, female: bool) -> bytes:
    """PNG-карточка для `name` (две строки, если есть пробел — как «Сапаров / Ерлан»)."""
    background = load_background(female)
    lines = name.split("\n") if "\n" in name else card_lines(name)
    size = MAX_FONT
    while True:
        font = load_font(size)
        boxes = [font.getbbox(line) for line in lines]
        widths = [b[2] - b[0] for b in boxes]
        line_h = font.getbbox("ҒАЙ")[3] - font.getbbox("ҒАЙ")[1]
        total_h = line_h * len(lines) + int(size * LINE_GAP) * (len(lines) - 1)
        if (max(widths) <= MAX_WIDTH and total_h <= MAX_HEIGHT) or size <= 60:
            break
        size -= 4

    text = Image.new("RGBA", background.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text)
    y = CENTER_Y - total_h / 2
    positions = []
    for line in lines:
        positions.append((background.width / 2, y + line_h / 2))
        y += line_h + int(size * LINE_GAP)

    # Мягкая тень, как у надписи на исходном макете.
    shadow = Image.new("RGBA", background.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    for x, cy in positions:
        shadow_draw.text((x, cy + size * 0.04), lines[positions.index((x, cy))], font=font, fill=(20, 0, 30, 120), anchor="mm")
    background.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size * 0.06)))
    for (x, cy), line in zip(positions, lines):
        draw.text((x, cy), line, font=font, fill=(255, 255, 255, 255), anchor="mm")
    background.alpha_composite(text)

    out = BytesIO()
    background.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


if __name__ == "__main__":
    import sys
    name = " ".join(sys.argv[1:]) or "Сапаров Ерлан"
    for female in (False, True):
        (CARDS_DIR / f"_test_{'female' if female else 'male'}.jpg").write_bytes(make_card(name, female))
