#!/usr/bin/env python3
"""Рисует иконку snapreel и раскладывает её по нужным форматам.

Запускается руками, результат коммитится: иконка меняется раз в год, а
Pillow в рантайме есть не у всех — в трее она нужна как готовый файл.

    python3 scripts/make-icon.py

Кладёт:
    src/snapreel/assets/icon.png            — приложение и иконка в трее
    src/snapreel/assets/icon-recording.png  — та же, но во время записи
    src/snapreel/assets/icon.ico            — иконка exe и окна на Windows

Смысл рисунка: рамка выделения (четыре уголка) и точка записи внутри. В
трее иконка видна размером с букву, поэтому форм всего две и обе крупные.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "snapreel" / "assets"
ICO = ASSETS / "icon.ico"

SIZE = 512
SUPERSAMPLE = 4  # рисуем крупнее и уменьшаем: у Pillow нет сглаживания фигур
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

IDLE = ((47, 111, 235), (28, 74, 173))  # синий градиент — обычное состояние
BUSY = ((214, 60, 60), (166, 32, 32))  # красный — идёт запись
CORNER = (255, 255, 255, 255)
DOT_IDLE = (255, 82, 82, 255)
DOT_BUSY = (255, 255, 255, 255)


def _background(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """Скруглённый квадрат с вертикальным градиентом."""
    gradient = Image.new("RGB", (1, size))
    for y in range(size):
        weight = y / max(size - 1, 1)
        gradient.putpixel(
            (0, y),
            tuple(round(top[i] + (bottom[i] - top[i]) * weight) for i in range(3)),
        )
    plate = gradient.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=round(size * 0.22), fill=255
    )
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(plate, (0, 0), mask)
    return out


def _corners(pen: ImageDraw.ImageDraw, size: int, small: bool) -> None:
    """Четыре уголка рамки выделения — то, что человек рисует мышью.

    На мелких размерах пропорции другие: тонкие линии с широкими полями на
    16 пикселях сливаются в пятно, поэтому там уголки короче, толще и ближе
    к краю.
    """
    margin = round(size * (0.14 if small else 0.20))
    arm = round(size * (0.20 if small else 0.16))  # длина уголка
    width = round(size * (0.10 if small else 0.065))
    low, high = margin, size - margin

    for x, y, dx, dy in (
        (low, low, 1, 1),
        (high, low, -1, 1),
        (low, high, 1, -1),
        (high, high, -1, -1),
    ):
        pen.line((x, y, x + arm * dx, y), fill=CORNER, width=width)
        pen.line((x, y, x, y + arm * dy), fill=CORNER, width=width)
        # круглая заглушка в углу: иначе стык двух линий даёт зазубрину
        half = width // 2
        pen.ellipse((x - half, y - half, x + half, y + half), fill=CORNER)


def draw(recording: bool = False, size: int = SIZE) -> Image.Image:
    """Рисунок под конкретный размер: мелкий — не уменьшенный крупный."""
    small = size <= 32
    big = size * SUPERSAMPLE
    top, bottom = BUSY if recording else IDLE
    image = _background(big, top, bottom)
    pen = ImageDraw.Draw(image)
    _corners(pen, big, small)

    radius = round(big * (0.17 if small else 0.13))
    centre = big // 2
    pen.ellipse(
        (centre - radius, centre - radius, centre + radius, centre + radius),
        fill=DOT_BUSY if recording else DOT_IDLE,
    )
    return image.resize((size, size), Image.LANCZOS)


def arrow(colour: tuple[int, int, int], size: int = 14) -> Image.Image:
    """Стрелка выпадающего списка.

    Qt рисует псевдоэлемент `down-arrow` только картинкой: треугольник из
    рамок, как в CSS, у него получается чёрточкой.
    """
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    margin = big * 0.28
    pen.polygon(
        [
            (margin, big * 0.38),
            (big - margin, big * 0.38),
            (big / 2, big * 0.68),
        ],
        fill=(*colour, 255),
    )
    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    idle = draw()
    idle.save(ASSETS / "icon.png")
    draw(recording=True).save(ASSETS / "icon-recording.png")
    # ICO несёт все размеры сразу, и каждый нарисован под себя: Windows
    # уменьшает сам только когда выбора нет, а её уменьшение на 16 пикселях
    # превращает уголки в кашу
    layers = [draw(size=n) for n in ICO_SIZES]
    layers[-1].save(ICO, sizes=[(n, n) for n in ICO_SIZES], append_images=layers[:-1])
    # стрелки списков: по одной на палитру, цвет — приглушённый текст темы
    arrow((107, 116, 128)).save(ASSETS / "arrow-light.png")
    arrow((152, 160, 173)).save(ASSETS / "arrow-dark.png")
    print(f"готово: {ASSETS / 'icon.png'}, {ASSETS / 'icon-recording.png'}, {ICO}, стрелки")
    return 0


if __name__ == "__main__":
    sys.exit(main())
