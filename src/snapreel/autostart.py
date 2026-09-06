"""Регистрация горячей клавиши и автозапуска средствами самой ОС.

Системный хоткей надёжнее встроенного демона: он переживает перезагрузку,
не держит фоновый процесс и работает даже в Wayland, где перехват клавиш
приложению запрещён.

Здесь же живёт автозапуск иконки в трее — ярлык в Startup на Windows,
LaunchAgent на macOS, `.desktop` в `~/.config/autostart` на Linux. У каждой
установки есть обратная операция: то, что снапреел прописал в систему, он
обязан уметь оттуда убрать.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .platform_info import Environment, Platform, detect

GNOME_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/snapreel/"
# Имя агента осталось от демона: переименование бросило бы уже прописанный
# у людей plist в системе, а убрать его умеет только тот, кто знает имя.
LAUNCH_AGENT = "com.snapreel.daemon"
SHORTCUT_NAME = "Snapreel.lnk"
STARTUP_NAME = "Snapreel (трей).lnk"
DESKTOP_ENTRY_NAME = "snapreel.desktop"


class HotkeySetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str


# --- разбор комбинаций ----------------------------------------------------

_NAMED_KEYS = {
    "space",
    "tab",
    "enter",
    "esc",
    "escape",
    "insert",
    "delete",
    "home",
    "end",
    "pageup",
    "pagedown",
    "up",
    "down",
    "left",
    "right",
    "print",
    "printscreen",
    *(f"f{n}" for n in range(1, 25)),
}

_MODIFIERS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "option": "alt",
    "cmd": "super",
    "super": "super",
    "win": "super",
    "meta": "super",
}


def parse_hotkey(spec: str) -> tuple[list[str], str]:
    """Из pynput-синтаксиса `<ctrl>+<shift>+r` в модификаторы и клавишу."""
    modifiers: list[str] = []
    key = ""
    for chunk in spec.split("+"):
        token = chunk.strip().strip("<>").lower()
        if not token:
            continue
        if token in _MODIFIERS:
            canonical = _MODIFIERS[token]
            if canonical not in modifiers:
                modifiers.append(canonical)
        else:
            key = token
    if not key:
        raise HotkeySetupError(f"в комбинации {spec!r} нет основной клавиши")
    return modifiers, key


def to_pynput(spec: str) -> str:
    """Канонический вид для конфига и демона: `<ctrl>+<shift>+r`."""
    modifiers, key = parse_hotkey(spec)
    return "+".join([*(f"<{m}>" for m in modifiers), key if len(key) == 1 else f"<{key}>"])


def describe(spec: str) -> str:
    """Человекочитаемый вид для сообщений: `Ctrl+Shift+R`."""
    names = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "super": "Super"}
    modifiers, key = parse_hotkey(spec)
    return "+".join([*(names[m] for m in modifiers), key.upper() if len(key) == 1 else key])


def describe_safe(spec: str) -> str:
    """`describe` для диагностики: испорченное значение показывается, а не роняет команду."""
    try:
        return describe(spec)
    except HotkeySetupError:
        return f"{spec!r} — не разобрать"


def validate(spec: str) -> None:
    """Комбинация без модификатора перехватит обычную печать — так нельзя."""
    modifiers, key = parse_hotkey(spec)
    if not modifiers:
        raise HotkeySetupError(
            f"в {spec!r} нет модификатора. Добавьте Ctrl, Alt, Shift или Super — "
            "иначе клавиша перестанет печататься."
        )
    if len(key) > 1 and key not in _NAMED_KEYS:
        raise HotkeySetupError(
            f"клавиша {key!r} не распознана. Годятся буква, цифра или одно из: "
            + ", ".join(sorted(_NAMED_KEYS))
        )


def to_gnome(spec: str) -> str:
    """GNOME ждёт `<Control><Shift><Alt>r`."""
    names = {"ctrl": "<Control>", "shift": "<Shift>", "alt": "<Alt>", "super": "<Super>"}
    modifiers, key = parse_hotkey(spec)
    return "".join(names[m] for m in modifiers) + key


def to_windows(spec: str) -> str:
    """Свойство Hotkey у ярлыка выглядит как `CTRL+SHIFT+ALT+R`."""
    names = {"ctrl": "CTRL", "shift": "SHIFT", "alt": "ALT", "super": "WIN"}
    modifiers, key = parse_hotkey(spec)
    return "+".join([*(names[m] for m in modifiers), key.upper()])


# --- команда запуска ------------------------------------------------------


def is_frozen() -> bool:
    """Собран ли PyInstaller'ом: тогда `sys.executable` — сам snapreel, а не Python."""
    return bool(getattr(sys, "frozen", False))


def argv_for(*arguments: str) -> list[str]:
    """Команда запуска snapreel с подкомандой — для системы, а не для шелла.

    В обычной установке это `<python> -m snapreel ...`, а в собранном
    бинарнике модуля `snapreel` для интерпретатора не существует — там сам
    исполняемый файл принимает подкоманду.
    """
    if is_frozen():
        return [sys.executable, *arguments]
    return [_windowless_python(), "-m", "snapreel", *arguments]


def launch_argv(as_gif: bool = False) -> list[str]:
    """Команда, которую вешаем на хоткей."""
    return argv_for("record", "--gif") if as_gif else argv_for("record")


def tray_argv() -> list[str]:
    """Команда резидента с иконкой в трее — она же уходит в автозапуск."""
    return argv_for("tray")


def _windowless_python() -> str:
    """На Windows берём pythonw.exe, иначе при каждой записи мигает консоль."""
    if os.name != "nt":
        return sys.executable
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else sys.executable


def quote(argv: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in argv)


# --- точка входа ----------------------------------------------------------


def install(hotkey: str, env: Environment | None = None) -> Outcome:
    env = env or detect()
    if env.platform is Platform.WINDOWS:
        return _windows_install(hotkey)
    if env.platform is Platform.MACOS:
        return _macos_hint(hotkey)
    return _gnome_install(hotkey)


def remove(env: Environment | None = None) -> Outcome:
    env = env or detect()
    if env.platform is Platform.WINDOWS:
        return _windows_remove()
    if env.platform is Platform.MACOS:
        return Outcome(True, "на macOS хоткей заводится вручную, удалять нечего")
    return _gnome_remove()


# --- GNOME ----------------------------------------------------------------


def _gsettings(*args: str) -> str:
    if not shutil.which("gsettings"):
        raise HotkeySetupError("не найден gsettings — это не GNOME")
    result = subprocess.run(
        ["gsettings", *args], capture_output=True, text=True, errors="replace", timeout=15
    )
    if result.returncode != 0:
        raise HotkeySetupError(result.stderr.strip() or "gsettings вернул ошибку")
    return result.stdout.strip()


def parse_gsettings_list(raw: str) -> list[str]:
    """`@as []` и `['/a/', '/b/']` — оба варианта ответа gsettings."""
    text = raw.strip()
    if not text or text.startswith("@as"):
        return []
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def format_gsettings_list(paths: list[str]) -> str:
    inner = ", ".join(f"'{path}'" for path in paths)
    return f"[{inner}]"


def _gnome_install(hotkey: str) -> Outcome:
    if os.environ.get("XDG_CURRENT_DESKTOP", "").upper().find("GNOME") < 0 and not shutil.which(
        "gsettings"
    ):
        return Outcome(
            False,
            "автоматически хоткей ставится только в GNOME. В других окружениях "
            f"назначьте комбинацию на команду: {quote(launch_argv())}",
        )
    binding = to_gnome(hotkey)
    existing = parse_gsettings_list(_gsettings("get", GNOME_SCHEMA, "custom-keybindings"))
    if GNOME_PATH not in existing:
        existing.append(GNOME_PATH)
        _gsettings("set", GNOME_SCHEMA, "custom-keybindings", format_gsettings_list(existing))
    schema = f"{GNOME_SCHEMA}.custom-keybinding:{GNOME_PATH}"
    _gsettings("set", schema, "name", "Snapreel")
    _gsettings("set", schema, "command", quote(launch_argv()))
    _gsettings("set", schema, "binding", binding)
    return Outcome(True, f"хоткей {binding} назначен в GNOME")


def _gnome_remove() -> Outcome:
    existing = parse_gsettings_list(_gsettings("get", GNOME_SCHEMA, "custom-keybindings"))
    if GNOME_PATH not in existing:
        return Outcome(True, "хоткей snapreel в GNOME не найден")
    existing.remove(GNOME_PATH)
    _gsettings("set", GNOME_SCHEMA, "custom-keybindings", format_gsettings_list(existing))
    return Outcome(True, "хоткей snapreel убран из GNOME")


# --- Windows --------------------------------------------------------------


def windows_shortcut_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / SHORTCUT_NAME


def windows_startup_path() -> Path:
    """Автозагрузка Windows: всё, что лежит в этой папке, стартует при входе."""
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_NAME


def windows_shortcut_script(
    path: Path,
    hotkey: str | None = None,
    argv: list[str] | None = None,
    description: str = "Snapreel — записать область экрана",
) -> str:
    """Скрипт создания ярлыка. Свойство Hotkey живёт только у ярлыка в «Пуске».

    Windows слушает комбинацию у ярлыков меню «Пуск»; у ярлыка в автозагрузке
    оно бессмысленно, поэтому хоткей необязателен.
    """
    argv = argv or launch_argv()
    arguments = quote(argv[1:])
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut('{path}'); "
        f"$link.TargetPath = '{argv[0]}'; "
        f"$link.Arguments = '{arguments}'; "
        f"$link.WorkingDirectory = '{Path.home()}'; "
        f"$link.Description = '{description}'; "
    )
    if hotkey:
        script += f"$link.Hotkey = '{to_windows(hotkey)}'; "
    return script + "$link.Save()"


def _powershell(script: str) -> None:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        raise HotkeySetupError(result.stderr.strip() or "powershell вернул ошибку")


def _windows_install(hotkey: str) -> Outcome:
    path = windows_shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _powershell(windows_shortcut_script(path, hotkey))
    return Outcome(
        True,
        f"ярлык с хоткеем {to_windows(hotkey)} создан: {path}. "
        "Windows подхватывает такие комбинации через пару секунд.",
    )


def _windows_remove() -> Outcome:
    path = windows_shortcut_path()
    if path.is_file():
        path.unlink()
        return Outcome(True, f"ярлык удалён: {path}")
    return Outcome(True, "ярлык snapreel не найден")


# --- macOS ----------------------------------------------------------------


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT}.plist"


def daemon_argv() -> list[str]:
    """Команда демона без трея — остаётся для тех, кому иконка не нужна."""
    return argv_for("daemon")


def launch_agent_plist() -> str:
    """LaunchAgent поднимает трей: он и хоткеи слушает, и виден в строке меню."""
    arguments = "\n".join(f"        <string>{part}</string>" for part in tray_argv())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCH_AGENT}</string>
    <key>ProgramArguments</key>
    <array>
{arguments}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
"""


# --- автозапуск иконки в трее ---------------------------------------------


def desktop_entry_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / DESKTOP_ENTRY_NAME


def desktop_entry() -> str:
    """`.desktop` в `~/.config/autostart` — общий способ у GNOME, KDE и Sway."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Snapreel\n"
        "Comment=Запись области экрана в буфер обмена\n"
        f"Exec={quote(tray_argv())}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def autostart_enabled(env: Environment | None = None) -> bool:
    """Прописан ли трей в автозапуск. Это спрашивает меню, поэтому не бросает."""
    env = env or detect()
    try:
        if env.platform is Platform.WINDOWS:
            return windows_startup_path().is_file()
        if env.platform is Platform.MACOS:
            return launch_agent_path().is_file()
        return desktop_entry_path().is_file()
    except OSError:
        return False


def install_autostart(env: Environment | None = None) -> Outcome:
    """Просит систему поднимать трей при входе."""
    env = env or detect()
    try:
        if env.platform is Platform.WINDOWS:
            path = windows_startup_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _powershell(
                windows_shortcut_script(
                    path, argv=tray_argv(), description="Snapreel — иконка в трее"
                )
            )
            return Outcome(True, f"трей будет стартовать при входе: {path}")
        if env.platform is Platform.MACOS:
            return install_launch_agent()
        path = desktop_entry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop_entry(), encoding="utf-8")
        return Outcome(True, f"трей будет стартовать при входе: {path}")
    except (HotkeySetupError, OSError, subprocess.SubprocessError) as exc:
        return Outcome(False, f"не прописать автозапуск: {exc}")


def remove_autostart(env: Environment | None = None) -> Outcome:
    """Обратная операция к `install_autostart`; отсутствие записи — не ошибка."""
    env = env or detect()
    try:
        if env.platform is Platform.MACOS:
            return remove_launch_agent()
        path = windows_startup_path() if env.platform is Platform.WINDOWS else desktop_entry_path()
        if not path.is_file():
            return Outcome(True, "автозапуск snapreel не найден")
        path.unlink()
        return Outcome(True, f"автозапуск убран: {path}")
    except (OSError, subprocess.SubprocessError) as exc:
        return Outcome(False, f"не убрать автозапуск: {exc}")


def _macos_hint(hotkey: str) -> Outcome:
    """У macOS нет открытого API для чужих глобальных хоткеев: демон или Automator."""
    return Outcome(
        False,
        "в macOS системный хоткей назначается вручную: Automator → Быстрое действие → "
        f"«Запустить shell-скрипт» с командой {quote(launch_argv())}, затем Системные "
        "настройки → Клавиатура → Сочетания клавиш → Службы. "
        f"Либо `snapreel autostart` — иконка в трее с хоткеем {hotkey} в автозапуске.",
    )


def install_launch_agent() -> Outcome:
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(launch_agent_plist(), encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, timeout=30)
    result = subprocess.run(
        ["launchctl", "load", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        return Outcome(False, result.stderr.strip() or "launchctl load не сработал")
    return Outcome(True, f"трей в автозапуске: {path}")


def remove_launch_agent() -> Outcome:
    path = launch_agent_path()
    if not path.is_file():
        return Outcome(True, "автозапуск snapreel не найден")
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, timeout=30)
    path.unlink()
    return Outcome(True, f"автозапуск убран: {path}")
