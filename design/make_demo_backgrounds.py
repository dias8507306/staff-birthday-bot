"""Neutral demo artwork drawn from scratch with Pillow (no photos, no real brand):

    python design/make_demo_backgrounds.py
        → design/logo.png        placeholder "EA" logo (also design/logo.svg)
        → cards/male.jpg         violet birthday background
        → cards/female.jpg       raspberry birthday background

The layout matches what card.py expects on a 2048×2048 canvas: logo on top, «ТУҒАН КҮНІҢІЗБЕН!» above the
name zone (CENTER_Y = 1120) and «EduApp командасы атынан» below it. Swap these files for your own design.
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
CARDS = HERE.parent / "cards"
FONT = CARDS / "font.ttf"  # Roboto Bold (Apache 2.0)
SIZE = 2048
BRAND = "EduApp"

THEMES = {
    "male": {"top": (58, 30, 120), "bottom": (12, 10, 48), "glow": (140, 90, 255),
             "balloons": [(232, 188, 92), (240, 150, 190), (250, 210, 120)], "footer": (214, 200, 255)},
    "female": {"top": (196, 76, 112), "bottom": (110, 22, 62), "glow": (255, 170, 170),
               "balloons": [(236, 186, 160), (250, 236, 228), (214, 140, 150)], "footer": (255, 214, 230)},
}
GOLD = (246, 205, 120)


def font(size):
    return ImageFont.truetype(str(FONT), size) if FONT.exists() else ImageFont.load_default(size)


def make_logo(side=512) -> Image.Image:
    """Rounded square with a soft teal→indigo gradient and white "EA" letters."""
    gradient = Image.new("RGB", (side, side))
    px = gradient.load()
    for y in range(side):
        for x in range(side):
            t = (x + y) / (2 * side)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip((38, 166, 154), (63, 81, 181)))
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle((10, 10, side - 10, side - 10), radius=side // 4.5, fill=255)
    logo = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    logo.paste(gradient, (0, 0), mask)
    ImageDraw.Draw(logo).text((side / 2, side / 2), "EA", font=font(side // 2), fill="white", anchor="mm")
    return logo


LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192" width="192" height="192">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#26A69A"/><stop offset="1" stop-color="#3F51B5"/></linearGradient></defs>
  <rect x="4" y="4" width="184" height="184" rx="42" fill="url(#g)"/>
  <text x="96" y="122" text-anchor="middle" font-family="Roboto, Arial, sans-serif" font-size="80"
        font-weight="700" fill="#fff">EA</text>
</svg>
"""


def background(theme) -> Image.Image:
    """Vertical gradient + a soft glow behind the logo."""
    column = Image.new("RGB", (1, SIZE))
    for y in range(SIZE):
        t = y / (SIZE - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(theme["top"], theme["bottom"])))
    image = column.resize((SIZE, SIZE)).convert("RGBA")
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((SIZE / 2 - 330, 60, SIZE / 2 + 330, 640), fill=theme["glow"] + (150,))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    return image


def balloon(draw, cx, cy, r, color):
    draw.line([(cx, cy + r * 1.2), (cx + r * 0.3, cy + r * 3.4)], fill=(235, 220, 190, 200), width=4)
    draw.ellipse((cx - r, cy - r * 1.2, cx + r, cy + r * 1.2), fill=color + (255,))
    draw.polygon([(cx - 14, cy + r * 1.2 + 16), (cx + 14, cy + r * 1.2 + 16), (cx, cy + r * 1.2 - 4)], fill=color + (255,))
    light = tuple(min(255, c + 60) for c in color)
    draw.ellipse((cx - r * 0.55, cy - r * 0.85, cx - r * 0.15, cy - r * 0.25), fill=light + (170,))


def sparkle(draw, x, y, r, color):
    draw.polygon([(x, y - r), (x + r * 0.22, y - r * 0.22), (x + r, y), (x + r * 0.22, y + r * 0.22),
                  (x, y + r), (x - r * 0.22, y + r * 0.22), (x - r, y), (x - r * 0.22, y - r * 0.22)], fill=color)


def decorate(image, theme, rng):
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    # Confetti everywhere except the central text column.
    for _ in range(170):
        x = rng.choice([rng.uniform(0, 560), rng.uniform(1490, SIZE)]) if rng.random() < 0.8 else rng.uniform(0, SIZE)
        y = rng.uniform(0, SIZE)
        if 560 < x < 1490 and 650 < y < 1650:
            continue
        w, h, a = rng.uniform(14, 34), rng.uniform(6, 12), rng.uniform(0, math.pi)
        color = rng.choice(theme["balloons"] + [GOLD, (190, 170, 255)])
        pts = [(x + dx * math.cos(a) - dy * math.sin(a), y + dx * math.sin(a) + dy * math.cos(a))
               for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]
        draw.polygon(pts, fill=color + (210,))
    for _ in range(26):
        x, y = rng.uniform(80, SIZE - 80), rng.uniform(80, SIZE - 80)
        if 560 < x < 1490 and 650 < y < 1650:
            continue
        sparkle(draw, x, y, rng.uniform(12, 30), GOLD + (230,))
    # Balloon bunches on both sides.
    for side in (0, 1):
        for i in range(7):
            cx = (rng.uniform(70, 330) if side == 0 else rng.uniform(SIZE - 330, SIZE - 70))
            cy = 380 + i * 230 + rng.uniform(-40, 40)
            balloon(draw, cx, cy, rng.uniform(95, 125), rng.choice(theme["balloons"]))
    image.alpha_composite(layer)


def text_block(image, theme, logo):
    side = 340
    small = logo.resize((side, side), Image.LANCZOS)
    image.alpha_composite(small, (SIZE // 2 - side // 2, 330 - side // 2))
    draw = ImageDraw.Draw(image)
    draw.text((SIZE / 2 + 4, 790 + 6), "ТУҒАН КҮНІҢІЗБЕН!", font=font(96), fill=(20, 0, 30, 140), anchor="mm")
    draw.text((SIZE / 2, 790), "ТУҒАН КҮНІҢІЗБЕН!", font=font(96), fill=GOLD + (255,), anchor="mm")
    draw.text((SIZE / 2, 1510), f"{BRAND} командасы атынан", font=font(78), fill=theme["footer"] + (255,), anchor="mm")


def main():
    logo = make_logo()
    logo.save(HERE / "logo.png")
    (HERE / "logo.svg").write_text(LOGO_SVG, encoding="utf-8")
    CARDS.mkdir(exist_ok=True)
    for name, theme in THEMES.items():
        rng = random.Random(name)
        image = background(theme)
        decorate(image, theme, rng)
        text_block(image, theme, logo)
        image.convert("RGB").save(CARDS / f"{name}.jpg", quality=88)
    print("ok: design/logo.png, design/logo.svg, cards/male.jpg, cards/female.jpg")


if __name__ == "__main__":
    main()
