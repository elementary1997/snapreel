"""Прямоугольная область экрана в физических пикселях."""

from __future__ import annotations

from dataclasses import dataclass


class RegionError(ValueError):
    """Область непригодна для записи."""


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    def __str__(self) -> str:
        return f"{self.width}x{self.height}+{self.x}+{self.y}"


def from_corners(x1: int, y1: int, x2: int, y2: int) -> Region:
    """Область по двум углам в любом порядке."""
    x, y = min(x1, x2), min(y1, y2)
    return Region(x, y, abs(x2 - x1), abs(y2 - y1))


def normalize(region: Region, min_side: int = 16) -> Region:
    """Приводит область к виду, который переварят кодеки.

    yuv420p требует чётных сторон, поэтому ширина и высота округляются вниз
    до чётного. Отрицательные координаты допустимы: на нескольких мониторах
    виртуальный рабочий стол может начинаться левее и выше нуля.
    """
    width = region.width - (region.width % 2)
    height = region.height - (region.height % 2)
    if width < min_side or height < min_side:
        raise RegionError(
            f"область {region.width}x{region.height} слишком мала, минимум {min_side}x{min_side}"
        )
    return Region(region.x, region.y, width, height)


def clamp(region: Region, bounds: Region) -> Region:
    """Обрезает область по границам виртуального рабочего стола."""
    x = max(region.x, bounds.x)
    y = max(region.y, bounds.y)
    right = min(region.right, bounds.right)
    bottom = min(region.bottom, bounds.bottom)
    if right <= x or bottom <= y:
        raise RegionError("область целиком вне экрана")
    return Region(x, y, right - x, bottom - y)


def parse(spec: str) -> Region:
    """Разбирает `WxH+X+Y`, как в X11-геометрии. Смещения могут быть отрицательными."""
    text = spec.strip()
    try:
        size, rest = text.split("+", 1)
        width_s, height_s = size.split("x", 1)
        # координаты могут быть отрицательными: 1920x1080+-1920+0
        parts = rest.split("+")
        if len(parts) != 2:
            raise ValueError
        return Region(int(parts[0]), int(parts[1]), int(width_s), int(height_s))
    except ValueError as exc:
        raise RegionError(f"не разобрать геометрию {spec!r}, ожидается WxH+X+Y") from exc
