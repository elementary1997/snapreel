"""Командная строка snapreel."""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib.util import find_spec
from pathlib import Path

from . import autostart, deps, storage
from . import config as config_module
from . import region as region_module
from .backends import CaptureError, for_environment
from .encode import EncodeError
from .errors import OverlayUnavailable, SelectionCancelled
from .platform_info import Platform, detect
from .recorder import record

PROG = "snapreel"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Запись видео выделенной области экрана прямо в буфер обмена",
    )
    parser.add_argument("--config", type=Path, help="путь к config.toml")
    sub = parser.add_subparsers(dest="command")

    rec = sub.add_parser("record", help="выделить область и записать клип")
    rec.add_argument("--gif", action="store_true", help="положить в буфер GIF, а не MP4")
    rec.add_argument("--region", help="готовая геометрия WxH+X+Y вместо выделения мышью")
    rec.add_argument("--seconds", type=float, help="максимальная длительность")
    rec.add_argument("--min-seconds", type=float, help="раньше этого момента стоп недоступен")
    rec.add_argument("--fps", type=int, help="частота кадров")
    rec.add_argument("--no-indicator", action="store_true", help="без рамки и таймера")
    rec.add_argument("--no-cursor", action="store_true", help="не рисовать курсор")
    rec.add_argument("--audio", action="store_true", help="писать звук")
    rec.add_argument("--output-dir", help="куда сохранять клипы")

    setup = sub.add_parser("setup", help="поставить зависимости и назначить хоткей")
    setup.add_argument("--hotkey", help="комбинация для MP4, например 'Ctrl+Shift+5'")
    setup.add_argument("--hotkey-gif", help="комбинация для GIF")
    setup.add_argument("--yes", "-y", action="store_true", help="не задавать вопросов")
    setup.add_argument("--no-deps", action="store_true", help="не трогать системные пакеты")
    setup.add_argument("--no-hotkey", action="store_true", help="не назначать хоткей")

    hotkey = sub.add_parser("hotkey", help="управление горячей клавишей")
    hotkey_sub = hotkey.add_subparsers(dest="action")
    hotkey_set = hotkey_sub.add_parser("set", help="назначить свою комбинацию")
    hotkey_set.add_argument("combo", nargs="?", help="например 'Ctrl+Alt+5'")
    hotkey_set.add_argument("--gif", action="store_true", help="назначить комбинацию для GIF")
    hotkey_sub.add_parser("show", help="показать текущие комбинации")
    hotkey_sub.add_parser("remove", help="снять системный хоткей")

    auto = sub.add_parser("autostart", help="демон с хоткеем в автозапуске (macOS)")
    auto.add_argument("--remove", action="store_true", help="убрать из автозапуска")

    daemon = sub.add_parser("daemon", help="висеть в фоне и слушать глобальный хоткей")
    daemon.add_argument("--hotkey", help="комбинация для MP4 (по умолчанию из конфига)")
    daemon.add_argument("--hotkey-gif", help="комбинация для GIF")

    sub.add_parser("doctor", help="проверить окружение и внешние зависимости")
    sub.add_parser("config", help="напечатать конфиг со значениями по умолчанию")

    prune = sub.add_parser("prune", help="удалить клипы старше keep_days")
    prune.add_argument("--days", type=int, help="перекрыть keep_days")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "record"

    try:
        cfg = config_module.load(args.config)
    except (OSError, ValueError, TypeError) as exc:
        print(f"{PROG}: не прочитать конфиг: {exc}", file=sys.stderr)
        return 2

    if command == "config":
        print(config_module.dump_default_toml(), end="")
        return 0
    if command == "doctor":
        return _doctor(cfg)
    if command == "setup":
        return _setup(cfg, args)
    if command == "hotkey":
        return _hotkey(cfg, args)
    if command == "autostart":
        return _autostart(args)
    if command == "prune":
        if args.days is not None:
            cfg.keep_days = args.days
        removed = storage.prune(cfg)
        print(f"удалено файлов: {len(removed)}")
        return 0
    if command == "daemon":
        return _daemon(cfg, args)
    return _record(cfg, args)


# --- запись ---------------------------------------------------------------


def _apply_record_flags(cfg, args) -> None:
    if getattr(args, "seconds", None) is not None:
        cfg.max_seconds = args.seconds
    if getattr(args, "min_seconds", None) is not None:
        cfg.min_seconds = args.min_seconds
    if getattr(args, "fps", None) is not None:
        cfg.fps = args.fps
    if getattr(args, "no_cursor", False):
        cfg.capture_cursor = False
    if getattr(args, "audio", False):
        cfg.capture_audio = True
    if getattr(args, "output_dir", None):
        cfg.output_dir = args.output_dir
    cfg.validate()


def _record(cfg, args) -> int:
    try:
        _apply_record_flags(cfg, args)
        region = region_module.parse(args.region) if args.region else None
        result = record(
            cfg,
            region=region,
            as_gif=args.gif,
            indicator=not args.no_indicator,
        )
    except SelectionCancelled:
        print("отменено")
        return 130
    except OverlayUnavailable as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except (region_module.RegionError, ValueError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except EncodeError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    except CaptureError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1

    info = result.info
    summary = str(result.payload)
    if info:
        parts = [f"{info.width}x{info.height}"] if info.width else []
        if info.duration:
            parts.append(f"{info.duration:.1f} с")
        parts.append(info.human_size)
        summary = f"{summary}  ({', '.join(parts)})"
    print(summary)
    if result.gif_error:
        print(f"{PROG}: GIF собрать не удалось, в буфере MP4: {result.gif_error}", file=sys.stderr)
    if result.clipboard_error:
        print(f"{PROG}: в буфер положить не удалось: {result.clipboard_error}", file=sys.stderr)
        return 1
    return 0


# --- установка ------------------------------------------------------------


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes or not _interactive():
        return assume_yes
    answer = input(f"{question} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes", "д", "да")


def _ask_hotkey(prompt: str, current: str, assume_yes: bool) -> str:
    """Спрашивает комбинацию, пока не получит годную. Пустой ввод оставляет текущую."""
    if assume_yes or not _interactive():
        return current
    while True:
        raw = input(f"{prompt} [{autostart.describe(current)}]: ").strip()
        if not raw:
            return current
        try:
            autostart.validate(raw)
            return autostart.to_pynput(raw)
        except autostart.HotkeySetupError as exc:
            print(f"  {exc}")


def _setup(cfg, args) -> int:
    env = detect()
    print(f"Платформа: {env.platform.value}{' (WSL)' if env.is_wsl else ''}\n")

    if env.is_wsl:
        print(
            "Внимание: внутри WSL виден только экран WSLg, а не рабочий стол Windows.\n"
            "Чтобы записывать экран Windows, поставьте snapreel на хосте.\n"
        )

    if not args.no_deps and _install_deps(cfg, env, args.yes) != 0:
        return 1

    for label, attribute, flag in (
        ("Хоткей для MP4", "hotkey_mp4", "hotkey"),
        ("Хоткей для GIF", "hotkey_gif", "hotkey_gif"),
    ):
        provided = getattr(args, flag, None)
        if provided:
            try:
                autostart.validate(provided)
            except autostart.HotkeySetupError as exc:
                print(f"{PROG}: {exc}", file=sys.stderr)
                return 2
            setattr(cfg, attribute, autostart.to_pynput(provided))
        else:
            setattr(cfg, attribute, _ask_hotkey(label, getattr(cfg, attribute), args.yes))

    path = config_module.save(cfg, args.config)
    print(f"\nКонфиг сохранён: {path}")
    print(f"  MP4: {autostart.describe(cfg.hotkey_mp4)}")
    print(f"  GIF: {autostart.describe(cfg.hotkey_gif)}")

    if args.no_hotkey:
        return 0

    print()
    try:
        outcome = autostart.install(cfg.hotkey_mp4, env)
    except autostart.HotkeySetupError as exc:
        print(f"Хоткей не назначен: {exc}")
        print(f"Назначьте вручную команду: {autostart.quote(autostart.launch_argv())}")
        return 0
    print(outcome.message)
    if outcome.ok:
        print(
            "Комбинация для GIF системно не назначается — используйте "
            f"`{PROG} daemon` либо второй ярлык на "
            f"{autostart.quote(autostart.launch_argv(as_gif=True))}"
        )
    return 0


def _install_deps(cfg, env, assume_yes: bool) -> int:
    absent = deps.missing(cfg, env)
    if not absent:
        print("Системные зависимости на месте.")
        return 0

    print("Не хватает:")
    for item in absent:
        mark = " (необязательно)" if item.optional else ""
        print(f"  · {item.key}{mark} — {item.reason}")

    manager = deps.package_manager(env)
    if manager is None:
        print("\nПакетный менеджер не найден — поставьте перечисленное вручную.")
        return 0 if all(item.optional for item in absent) else 1

    commands = deps.install_commands(manager, absent)
    unresolved = deps.unresolved(manager, absent)
    print(f"\nПакетный менеджер: {manager}")
    for command in commands:
        print(f"  $ {' '.join(command)}")
    for item in unresolved:
        print(f"  · {item.key} — пакета нет в {manager}, поставьте вручную")

    if not commands:
        return 0
    if not _confirm("\nВыполнить?", assume_yes):
        print("Пропущено.")
        return 0

    ok, error = deps.run(commands)
    if not ok:
        print(f"\nУстановка не удалась: {error}", file=sys.stderr)
        return 1
    print("\nЗависимости поставлены.")
    return 0


# --- хоткей ---------------------------------------------------------------


def _hotkey(cfg, args) -> int:
    action = getattr(args, "action", None) or "show"
    env = detect()

    if action == "show":
        print(f"MP4: {autostart.describe(cfg.hotkey_mp4)}   ({cfg.hotkey_mp4})")
        print(f"GIF: {autostart.describe(cfg.hotkey_gif)}   ({cfg.hotkey_gif})")
        print(f"команда: {autostart.quote(autostart.launch_argv())}")
        return 0

    if action == "remove":
        try:
            outcome = autostart.remove(env)
        except autostart.HotkeySetupError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 1
        print(outcome.message)
        return 0 if outcome.ok else 1

    combo = args.combo or _ask_hotkey(
        "Новая комбинация",
        cfg.hotkey_gif if args.gif else cfg.hotkey_mp4,
        assume_yes=False,
    )
    try:
        autostart.validate(combo)
    except autostart.HotkeySetupError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

    normalized = autostart.to_pynput(combo)
    if args.gif:
        cfg.hotkey_gif = normalized
    else:
        cfg.hotkey_mp4 = normalized
    path = config_module.save(cfg, args.config)
    print(f"{autostart.describe(normalized)} сохранён в {path}")

    if args.gif:
        print("Комбинация для GIF работает в `snapreel daemon`.")
        return 0

    try:
        outcome = autostart.install(normalized, env)
    except autostart.HotkeySetupError as exc:
        print(f"Системный хоткей не назначен: {exc}")
        print(f"Назначьте вручную команду: {autostart.quote(autostart.launch_argv())}")
        return 0
    print(outcome.message)
    return 0


def _autostart(args) -> int:
    env = detect()
    if env.platform is not Platform.MACOS:
        print(
            f"{PROG}: автозапуск демона сделан для macOS. "
            f"На этой платформе назначьте системный хоткей: {PROG} hotkey set",
            file=sys.stderr,
        )
        return 2
    outcome = autostart.remove_launch_agent() if args.remove else autostart.install_launch_agent()
    print(outcome.message)
    return 0 if outcome.ok else 1


# --- демон и диагностика --------------------------------------------------


def _daemon(cfg, args) -> int:
    from .hotkeys import HotkeyError, run

    if args.hotkey:
        cfg.hotkey_mp4 = autostart.to_pynput(args.hotkey)
    if args.hotkey_gif:
        cfg.hotkey_gif = autostart.to_pynput(args.hotkey_gif)

    def handler(as_gif: bool) -> None:
        try:
            result = record(cfg, as_gif=as_gif)
        except SelectionCancelled:
            return
        print(result.payload)

    try:
        return run(cfg, handler)
    except HotkeyError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1


def _doctor(cfg) -> int:
    env = detect()
    print(f"платформа:      {env.platform.value}{' (WSL)' if env.is_wsl else ''}")
    print(f"каталог клипов: {cfg.resolved_output_dir()}")
    print(f"конфиг:         {config_module.config_path()}")
    print(f"хоткей MP4:     {autostart.describe(cfg.hotkey_mp4)}")

    problems: list[str] = []
    if env.is_wsl:
        problems.append(
            "запуск внутри WSL: виден только экран WSLg. Ставьте snapreel на хост Windows."
        )

    for binary in {cfg.ffmpeg, cfg.ffprobe}:
        found = shutil.which(binary)
        print(f"{binary:<15} {found or 'НЕ НАЙДЕН'}")
        if not found:
            problems.append(f"не найден {binary}")

    try:
        backend = for_environment(cfg, env)
        print(f"бэкенд:         {backend.name}")
        problems.extend(backend.preflight())
    except CaptureError as exc:
        problems.append(str(exc))

    clipboard_tool = {
        Platform.LINUX_X11: "xclip",
        Platform.LINUX_WAYLAND: "wl-copy",
    }.get(env.platform)
    if clipboard_tool:
        found = shutil.which(clipboard_tool)
        print(f"{clipboard_tool:<15} {found or 'НЕ НАЙДЕН'}")
        if not found:
            problems.append(f"не найден {clipboard_tool} — файл не попадёт в буфер")

    if find_spec("tkinter"):
        print("tkinter         есть")
    else:
        problems.append("нет tkinter — не показать оверлей выделения (apt install python3-tk)")

    if find_spec("pynput"):
        print("pynput          есть")
    else:
        print("pynput          нет (нужен только для `snapreel daemon`)")

    if problems:
        print("\nПроблемы:")
        for item in problems:
            print(f"  · {item}")
        print(f"\nПочинить одной командой: {PROG} setup")
        return 1
    print("\nВсё на месте.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
