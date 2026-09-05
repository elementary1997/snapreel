"""Конфигурация snapreel: значения по умолчанию, файл, переменные окружения."""

from __future__ import annotations

import os
import platform
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import tomllib

from . import naming

APP_NAME = "snapreel"


@dataclass
class Config:
    # запись
    fps: int = 30
    min_seconds: float = 5.0
    max_seconds: float = 60.0
    capture_cursor: bool = True
    capture_audio: bool = False
    # dshow-имя на Windows, avfoundation-индекс на macOS, pulse-source на Linux
    audio_device: str = ""
    screen_index: int = -1  # avfoundation: индекс экрана; -1 -> определить автоматически

    # кодирование
    crf: int = 23
    preset: str = "veryfast"
    gif_fps: int = 15
    gif_max_width: int = 900

    # вывод
    output_dir: str = ""  # пусто -> ~/Videos/Snapreel (или ~/Movies на macOS)
    filename_template: str = "snapreel-%Y%m%d-%H%M%S"
    keep_days: int = 30  # 0 -> не удалять
    copy_path_as_text: bool = False

    # демон
    hotkey_mp4: str = "<ctrl>+<shift>+<alt>+r"
    hotkey_gif: str = "<ctrl>+<shift>+<alt>+g"

    # прочее
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    notify: bool = True

    def validate(self) -> None:
        # тип приходит из TOML или из окружения, поэтому проверяется до сравнений:
        # иначе строковый fps роняет CLI голым TypeError
        self._check_types()
        if self.fps < 1 or self.fps > 120:
            raise ValueError("fps должен быть в диапазоне 1..120")
        if self.min_seconds < 0:
            raise ValueError("min_seconds не может быть отрицательным")
        if self.max_seconds <= 0:
            raise ValueError("max_seconds должен быть больше нуля")
        if self.min_seconds > self.max_seconds:
            raise ValueError("min_seconds больше max_seconds")
        if not 0 <= self.crf <= 51:
            raise ValueError("crf должен быть в диапазоне 0..51")
        naming.validate(self.filename_template)

    def _check_types(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            expected = _EXPECTED_TYPES[field.name]
            # bool — подкласс int, но «fps = true» осмысленным числом не является
            if isinstance(value, bool) is not (expected is bool):
                raise ValueError(
                    f"{field.name}: ожидается {_TYPE_NAMES[expected]}, получено {value!r}"
                )
            allowed: tuple[type, ...] = (int, float) if expected is float else (expected,)
            if not isinstance(value, allowed):
                raise ValueError(
                    f"{field.name}: ожидается {_TYPE_NAMES[expected]}, получено {value!r}"
                )

    def resolved_output_dir(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir).expanduser()
        return default_output_dir()


_TYPE_NAMES = {int: "целое число", float: "число", bool: "true или false", str: "строка"}


def _expected_types() -> dict[str, type]:
    """Тип каждого поля берём из значений по умолчанию, а не из аннотаций."""
    defaults = Config.__dataclass_fields__
    mapping: dict[str, type] = {}
    for name, field in defaults.items():
        mapping[name] = type(field.default)
    return mapping


_EXPECTED_TYPES = _expected_types()


def default_output_dir() -> Path:
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Movies" / "Snapreel"
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", home)) / "Videos" / "Snapreel"
    return home / "Videos" / "Snapreel"


def config_path() -> Path:
    override = os.environ.get("SNAPREEL_CONFIG")
    if override:
        return Path(override).expanduser()
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME / "config.toml"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME / "config.toml"


def load(path: Path | None = None) -> Config:
    """Читает конфиг с диска; отсутствующий файл — это норма, а не ошибка."""
    path = path or config_path()
    config = Config()
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        known = {f.name: f.type for f in fields(Config)}
        for key, value in data.items():
            if key in known:
                setattr(config, key, value)
    _apply_env(config)
    config.validate()
    return config


def _apply_env(config: Config) -> None:
    """Переменные SNAPREEL_* перекрывают файл — удобно для разовых запусков."""
    for field in fields(Config):
        raw = os.environ.get(f"SNAPREEL_{field.name.upper()}")
        if raw is None:
            continue
        current = getattr(config, field.name)
        if isinstance(current, bool):
            setattr(config, field.name, raw.strip().lower() in ("1", "true", "yes", "on"))
        elif isinstance(current, int):
            setattr(config, field.name, int(raw))
        elif isinstance(current, float):
            setattr(config, field.name, float(raw))
        else:
            setattr(config, field.name, raw)


def to_toml(config: Config) -> str:
    lines = [f"# {config_path()}", ""]
    for key, value in asdict(config).items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, str):
            rendered = f'"{value}"'
        else:
            rendered = str(value)
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def dump_default_toml() -> str:
    return to_toml(Config())


def save(config: Config, path: Path | None = None) -> Path:
    """Пишет конфиг целиком: частичное обновление не стоит риска потерять файл."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_toml(config), encoding="utf-8")
    return path
