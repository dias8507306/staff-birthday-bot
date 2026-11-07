"""Фоны карточек из картинок Gemini: стираем имя (Coons-patch заливка), ставим логотип, увеличиваем до 2048.

python make_backgrounds.py → ../cards/male.jpg и ../cards/female.jpg

Исходные картинки (gemini_original.jpg, gemini_pink_original.jpg) в портфолио-репозиторий не входят —
положите свои макеты 1024×1024 с именем в центре. Для демо есть make_demo_backgrounds.py (рисует фоны с нуля).
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "cards"
SIZE = 2048

# Исходник Gemini (1024×1024), прямоугольник с именем, центр логотипа и его сторона.
SOURCES = {
    "male": ("gemini_original.jpg", (250, 440, 790, 680), (512, 168), 172),
    "female": ("gemini_pink_original.jpg", (250, 435, 790, 685), (512, 168), 172),
}


def erase(img: Image.Image, box) -> Image.Image:
    """Закрашивает прямоугольник плавным градиентом, натянутым на его края (Coons patch)."""
    x0, y0, x1, y1 = box
    a = np.asarray(img, dtype=np.float64).copy()
    smooth = np.asarray(img.filter(ImageFilter.GaussianBlur(6)), dtype=np.float64)
    top, bottom = smooth[y0, x0:x1], smooth[y1, x0:x1]
    left, right = smooth[y0:y1, x0], smooth[y0:y1, x1]
    h, w = y1 - y0, x1 - x0
    u = np.linspace(0, 1, w)[None, :, None]
    v = np.linspace(0, 1, h)[:, None, None]
    patch = ((1 - v) * top[None] + v * bottom[None] + (1 - u) * left[:, None] + u * right[:, None]
             - ((1 - u) * (1 - v) * top[0] + u * (1 - v) * top[-1] + (1 - u) * v * bottom[0] + u * v * bottom[-1]))
    # Лёгкое зерно, как у остального изображения, и мягкий переход по краям.
    patch += np.random.default_rng(1).normal(0, 1.2, patch.shape)
    feather = 14
    mask = np.ones((h, w))
    ramp = np.linspace(0, 1, feather)
    mask[:feather] *= ramp[:, None]
    mask[-feather:] *= ramp[::-1, None]
    mask[:, :feather] *= ramp[None]
    mask[:, -feather:] *= ramp[None, ::-1]
    mask = mask[..., None]
    region = a[y0:y1, x0:x1]
    a[y0:y1, x0:x1] = region * (1 - mask) + patch * mask
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def build(name, source, box, logo_center, logo_side):
    img = erase(Image.open(HERE / source).convert("RGB"), box)
    scale = SIZE / img.width
    big = img.resize((SIZE, SIZE), Image.LANCZOS)
    big = big.filter(ImageFilter.UnsharpMask(radius=2.2, percent=70, threshold=3)).convert("RGBA")
    logo = Image.open(HERE / "logo.png").convert("RGBA").crop((10, 10, 502, 502))
    side = round(logo_side * scale) + 2
    logo = logo.resize((side, side), Image.LANCZOS)
    cx, cy = (round(c * scale) for c in logo_center)
    big.alpha_composite(logo, (cx - side // 2, cy - side // 2))
    OUT.mkdir(exist_ok=True)
    big.convert("RGB").save(OUT / f"{name}.jpg", quality=95, subsampling=0)


if __name__ == "__main__":
    for name, args in SOURCES.items():
        build(name, *args)
    print("ok")
