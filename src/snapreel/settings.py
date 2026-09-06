"""Модель окна настроек: какие поля показывать и как разобрать введённое.

Здесь нет ни tkinter, ни побочных эффектов — только описание формы и разбор
её значений. Окно (`settings_ui`) из этого описания строится, а все проверки
остаются проверяемыми без экрана.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from . import autostart
from .config import Config

# Пресеты x264 от самого быстрого к самому плотному.
PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str  # hotkey | int | float | bool | text | dir | choice
    hint: str = ""
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    title: str
    fields: tuple[Field, ...]


GROUPS: tuple[Group, ...] = (
    Group(
        "Горячие клавиши",
        (
            Field("hotkey_mp4", "Записать MP4", "hotkey"),
            Field("hotkey_gif", "Записать GIF", "hotkey"),
        ),
    ),
    Group(
        "Запись",
        (
            Field("min_seconds", "Не короче, с", "float", "раньше этого остановиться нельзя"),
            Field("max_seconds", "Не длиннее, с", "float", "по достижении запись закроется сама"),
            Field("fps", "Кадров в секунду", "int", "1..120"),
            Field("capture_cursor", "Рисовать курсор", "bool"),
            Field("capture_audio", "Писать звук", "bool"),
            Field("audio_device", "Устройство звука", "text", "пусто — звук не пишется"),
            Field("screen_index", "Экран", "int", "macOS: номер экрана, -1 — определить самому"),
        ),
    ),
    Group(
        "Качество",
        (
            Field("crf", "Качество H.264", "int", "0..51, меньше — лучше и тяжелее"),
            Field("preset", "Скорость сжатия", "choice", choices=PRESETS),
        ),
    ),
    Group(
        "GIF",
        (
            Field("gif_fps", "Кадров в секунду", "int"),
            Field(
                "gif_max_width", "Ширина не больше", "int", "GIF только уменьшается, 0 — не менять"
            ),
        ),
    ),
    Group(
        "Файлы",
        (
            Field("output_dir", "Каталог клипов", "dir", "пусто — стандартный каталог видео"),
            Field("filename_template", "Имя файла", "text", "допустимы %Y %m %d %H %M %S"),
            Field("keep_days", "Удалять старше, дней", "int", "0 — не удалять"),
        ),
    ),
    Group(
        "Прочее",
        (
            Field("copy_path_as_text", "В буфер путь, а не файл", "bool"),
            Field("notify", "Показывать уведомления", "bool"),
            Field("ffmpeg", "Команда ffmpeg", "text", "оставьте как есть — используется вшитый"),
            Field("ffprobe", "Команда ffprobe", "text"),
        ),
    ),
)

FIELDS: tuple[Field, ...] = tuple(field for group in GROUPS for field in group.fields)


def values_of(config: Config) -> dict[str, object]:
    """Значения для заполнения формы."""
    return {field.name: getattr(config, field.name) for field in FIELDS}


def parse(raw: Mapping[str, object], base: Config | None = None) -> tuple[Config, dict[str, str]]:
    """Собирает конфиг из введённого; ошибки возвращаются по именам полей.

    Ошибки не бросаются, а раскладываются по полям: окну нужно подсветить ту
    строку, где непорядок, а не показать один общий текст на всю форму.
    """
    config = replace(base or Config())
    errors: dict[str, str] = {}

    for field in FIELDS:
        if field.name not in raw:
            continue
        try:
            setattr(config, field.name, _convert(field, raw[field.name]))
        except (ValueError, autostart.HotkeySetupError) as exc:
            errors[field.name] = str(exc)

    if errors:
        return config, errors

    try:
        config.validate()
    except ValueError as exc:
        errors[_blame(str(exc))] = str(exc)
    return config, errors


def _convert(field: Field, value: object) -> object:
    if field.kind == "bool":
        return bool(value)
    text = str(value).strip()
    if field.kind == "int":
        return _number(int, text, field.label)
    if field.kind == "float":
        return _number(float, text, field.label)
    if field.kind == "hotkey":
        autostart.validate(text)
        return autostart.to_pynput(text)
    if field.kind == "choice" and text not in field.choices:
        raise ValueError(f"допустимо одно из: {', '.join(field.choices)}")
    return text


def _number(cast, text: str, label: str):
    try:
        return cast(text.replace(",", "."))
    except ValueError:
        raise ValueError(f"«{label}» — это число, а не {text!r}") from None


def _blame(message: str) -> str:
    """К какому полю отнести жалобу `Config.validate`, чтобы подсветить строку."""
    for field in FIELDS:
        if message.startswith(field.name) or field.name in message.split():
            return field.name
    return ""


# --- захват комбинации нажатием ------------------------------------------
#
# Модификаторы считаются по нажатиям и отпусканиям, а не по битовой маске
# события: маска у каждой оконной системы своя, а имена клавиш одинаковы
# везде, где есть tkinter.

_MODIFIER_KEYSYMS = {
    "control_l": "ctrl",
    "control_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "option_l": "alt",
    "option_r": "alt",
    "meta_l": "super",
    "meta_r": "super",
    "super_l": "super",
    "super_r": "super",
    "win_l": "super",
    "win_r": "super",
    "command": "super",
}

_KEY_ALIASES = {
    "return": "enter",
    "kp_enter": "enter",
    "escape": "esc",
    "prior": "pageup",
    "next": "pagedown",
    "print": "printscreen",
}


def modifier_of(keysym: str) -> str | None:
    """Модификатор по имени клавиши Tk или None, если это обычная клавиша."""
    return _MODIFIER_KEYSYMS.get(keysym.lower())


def key_of(keysym: str) -> str:
    """Основная клавиша в терминах grammar хоткеев."""
    name = keysym.lower()
    return _KEY_ALIASES.get(name, name)


def combo(modifiers: Sequence[str], keysym: str) -> str:
    """Из нажатого — канонический вид для конфига; годность проверяется тут же."""
    spec = "+".join([*modifiers, key_of(keysym)])
    autostart.validate(spec)
    return autostart.to_pynput(spec)
