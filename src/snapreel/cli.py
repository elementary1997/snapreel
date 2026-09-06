"""Командная строка snapreel."""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib.util import find_spec
from pathlib import Path

from . import autostart, deps, storage, updates
from . import config as config_module
from . import install as install_module
from . import region as region_module
from .backends import CaptureError, for_environment
from .config import Config
from .encode import EncodeError
from .errors import OverlayUnavailable, SelectionCancelled
from .platform_info import Platform, attach_console, detect
from .recorder import record

PROG = "snapreel"


def _make_output_printable() -> None:
    """Весь вывод snapreel — русский, а кодировка потока может его не взять.

    На Windows перенаправленный stdout берёт кодировку локали (cp1252 в
    английской системе), и первая же кириллическая буква роняет команду
    UnicodeEncodeError — даже `--help` внутри argparse. UTF-8 берёт почти
    любой текст, а `errors="replace"` закрывает остаток: имя файла с
    неразбираемыми байтами приезжает из файловой системы одинокими
    суррогатами, и строгий UTF-8 спотыкается уже о них. Испорченный символ
    в пути лучше потерянного клипа.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # поток подменён на что-то своё
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


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

    auto = sub.add_parser("autostart", help="поднимать иконку в трее при входе в систему")
    auto.add_argument("--remove", action="store_true", help="убрать из автозапуска")

    sub.add_parser("tray", help="иконка в трее: запись, настройки и обновления")

    sub.add_parser("install", help="положить бинарник на место и поднимать при входе")
    sub.add_parser("uninstall", help="убрать установленную копию и автозапуск")

    daemon = sub.add_parser("daemon", help="висеть в фоне и слушать глобальный хоткей")
    daemon.add_argument("--hotkey", help="комбинация для MP4 (по умолчанию из конфига)")
    daemon.add_argument("--hotkey-gif", help="комбинация для GIF")

    sub.add_parser("settings", help="окно настроек: хоткеи, качество, каталог клипов")

    update = sub.add_parser("update", help="проверить и поставить новую версию")
    update.add_argument("--check", action="store_true", help="только проверить, не ставить")
    update.add_argument("--yes", "-y", action="store_true", help="ставить без вопросов")
    sub.add_parser("doctor", help="проверить окружение и внешние зависимости")
    sub.add_parser("config", help="напечатать конфиг со значениями по умолчанию")

    prune = sub.add_parser("prune", help="удалить клипы старше keep_days")
    prune.add_argument("--days", type=int, help="перекрыть keep_days")

    return parser


def main(argv: list[str] | None = None) -> int:
    # оконная сборка Windows своих потоков вывода не имеет: сначала находим,
    # куда писать, и только потом решаем, чем именно
    attach_console()
    _make_output_printable()
    # прошлое обновление отодвинуло старый бинарник — на Windows удалить его
    # можно было только после выхода из него, то есть теперь
    updates.clean_leftovers_if_frozen()
    parser = build_parser()
    args = parser.parse_args(argv)
    bare = args.command is None
    if bare:
        # Голый `snapreel` — это `snapreel tray`: так его запускают двойным
        # щелчком по бинарнику, и человек ждёт иконку рядом с часами, а не
        # немедленное выделение области. Подкоманда дописывается и
        # разбирается заново, а не подставляется строкой: без разбора в
        # Namespace не будет её флагов.
        argv = sys.argv[1:] if argv is None else argv
        args = parser.parse_args([*argv, "tray"])
    command = args.command

    try:
        cfg = config_module.load(args.config)
    except (OSError, ValueError, TypeError) as exc:
        print(f"{PROG}: не прочитать конфиг: {exc}", file=sys.stderr)
        return 2

    if command == "config":
        print(config_module.dump_default_toml(), end="")
        return 0
    if command == "settings":
        return _settings(cfg, args.config)
    if command == "update":
        return _update(args)
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
    if command == "install":
        return _install()
    if command == "uninstall":
        outcome = install_module.uninstall()
        print(outcome.message)
        return 0 if outcome.ok else 1
    if command == "tray":
        # установку предлагает только голый запуск: явный `tray` — это то,
        # что стоит в автозапуске, и оттуда человек ждёт иконку, а не окно
        return _tray(cfg, args.config, offer_install=bare)
    if command == "daemon":
        return _daemon(cfg, args)
    return _record(cfg, args)


# --- обновление -----------------------------------------------------------


def _update(args) -> int:
    """Проверяет github и, если попросят, ставит новую версию."""
    from . import __version__

    try:
        release = updates.check(config_module.config_path().parent, force=True)
    except updates.UpdateError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1

    if release is None:
        print(f"установлена последняя версия ({__version__})")
        return 0

    print(f"есть новая версия: {release.name} (у вас {__version__})")
    if args.check:
        return 0
    if not updates.supported():
        print(f"{PROG}: обновлять умеем только готовый бинарник.", file=sys.stderr)
        print("Из исходников — `pip install -U snapreel` или `git pull`.", file=sys.stderr)
        return 2
    if not args.yes and _interactive():
        answer = input(f"поставить {release.name}? [Y/n]: ").strip().lower()
        if answer not in ("", "y", "yes", "д", "да"):
            print("отменено")
            return 0

    try:
        path = updates.update(release, progress=_progress)
    except updates.UpdateError as exc:
        print(f"\n{PROG}: {exc}", file=sys.stderr)
        return 1
    print(f"\nобновлено до {release.name}: {path}")
    return 0


def _progress(done: int, total: int) -> None:
    if total:
        print(f"\rскачано {done * 100 // total}%", end="", flush=True)


# --- настройки ------------------------------------------------------------


def _settings(cfg: Config, path: Path | None) -> int:
    """Окно настроек; без Qt объясняет, чем его заменить."""
    from .errors import OverlayUnavailable

    try:
        from .settings_ui import open_settings
    except ImportError as exc:  # Qt ставится экстрой и есть не везде
        print(f"{PROG}: не открыть окно настроек: {exc}", file=sys.stderr)
        print("Настройте через `snapreel hotkey set` или правкой конфига.", file=sys.stderr)
        return 2

    try:
        saved = open_settings(cfg, path)
    except OverlayUnavailable as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    print("настройки сохранены" if saved else "настройки не менялись")
    return 0


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
    """Есть ли кого спрашивать.

    Потока может не быть вовсе — у оконной сборки Windows их нет, пока она не
    подключится к чужой консоли, — и «спросить некого» это тоже ответ, а не
    повод падать на `None.isatty()`.
    """
    for stream in (sys.stdin, sys.stdout):
        try:
            if not stream.isatty():
                return False
        except (AttributeError, ValueError, OSError):
            return False
    return True


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
        raw = input(f"{prompt} [{autostart.describe_safe(current)}]: ").strip()
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
            current = getattr(cfg, attribute)
            try:
                autostart.validate(current)
            except autostart.HotkeySetupError as exc:
                # setup — это команда починки, которую советует doctor:
                # испорченное значение она чинит, а не спотыкается о него
                default = getattr(Config(), attribute)
                print(f"{label}: в конфиге испорчено — {exc}")
                print(f"  беру значение по умолчанию: {autostart.describe(default)}")
                current = default
            setattr(cfg, attribute, _ask_hotkey(label, current, args.yes))

    path = config_module.save(cfg, args.config)
    print(f"\nКонфиг сохранён: {path}")
    print(f"  MP4: {autostart.describe_safe(cfg.hotkey_mp4)}")
    print(f"  GIF: {autostart.describe_safe(cfg.hotkey_gif)}")

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
            f"`{PROG} tray` либо второй ярлык на "
            f"{autostart.quote(autostart.launch_argv(as_gif=True))}"
        )
    print(f"\nИконка в трее: `{PROG} tray`, поднимать её при входе в систему — `{PROG} autostart`.")
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
        print(f"MP4: {autostart.describe_safe(cfg.hotkey_mp4)}   ({cfg.hotkey_mp4})")
        print(f"GIF: {autostart.describe_safe(cfg.hotkey_gif)}   ({cfg.hotkey_gif})")
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
    """Прописывает трей в автозагрузку системы — и умеет убрать обратно."""
    env = detect()
    outcome = autostart.remove_autostart(env) if args.remove else autostart.install_autostart(env)
    print(outcome.message)
    return 0 if outcome.ok else 1


# --- трей, демон и диагностика --------------------------------------------


def _install() -> int:
    outcome = install_module.install()
    print(outcome.message)
    if not outcome.ok:
        return 1
    if not install_module.launch(install_module.target_path(), autostarted=True):
        print(f"{PROG}: установленную копию не запустить — запустите её сами", file=sys.stderr)
    return 0


def _offer_install(cfg: Config) -> bool:
    """Спрашивает про установку. True — поставили и подняли новую копию.

    Спрашивается только у скачанного бинарника, который ещё лежит не на
    месте: пока он в «Загрузках», автозапуск ссылается на файл, который
    уберут первой же уборкой папки.
    """
    try:
        from .install_ui import ask_install
    except ImportError:  # без Qt ставят командой
        return False
    try:
        return ask_install(cfg)
    except OverlayUnavailable as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return False


def _tray(cfg: Config, path: Path | None, offer_install: bool = False) -> int:
    """Резидент с иконкой; без Qt объясняет, чем его заменить."""
    from .errors import TrayUnavailable
    from .tray import run as run_tray

    if offer_install and install_module.supported() and not install_module.plan().installed:
        # установленная копия запускается сама и остаётся жить; этой уже
        # ничего делать не нужно
        if _offer_install(cfg):
            return 0

    try:
        return run_tray(cfg, path)
    except TrayUnavailable as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        print(
            f"Без иконки остаются системный хоткей (`{PROG} hotkey set`) и `{PROG} daemon`.",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        return 0


def _daemon(cfg, args) -> int:
    from .hotkeys import HotkeyError, run

    try:
        if args.hotkey:
            cfg.hotkey_mp4 = autostart.to_pynput(args.hotkey)
        if args.hotkey_gif:
            cfg.hotkey_gif = autostart.to_pynput(args.hotkey_gif)
        # значения могли прийти и из конфига — демон без разбираемых комбинаций не поднимется
        autostart.validate(cfg.hotkey_mp4)
        autostart.validate(cfg.hotkey_gif)
    except autostart.HotkeySetupError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2

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
    print(f"хоткей MP4:     {autostart.describe_safe(cfg.hotkey_mp4)}")

    problems: list[str] = []
    for label, spec in (("hotkey_mp4", cfg.hotkey_mp4), ("hotkey_gif", cfg.hotkey_gif)):
        try:
            autostart.validate(spec)
        except autostart.HotkeySetupError as exc:
            problems.append(f"{label} в конфиге испорчен: {exc}")
    if env.is_wsl:
        problems.append(
            "запуск внутри WSL: виден только экран WSLg. Ставьте snapreel на хост Windows."
        )

    ffmpeg = shutil.which(cfg.ffmpeg_path)
    print(f"{'ffmpeg':<15} {ffmpeg or 'НЕ НАЙДЕН'}")
    if not ffmpeg:
        problems.append(f"не найден {cfg.ffmpeg}")

    # ffprobe только украшает итоговую строку размерами и длительностью, и в
    # собранный бинарник его не кладут вовсе — это не поломка окружения
    ffprobe = shutil.which(cfg.ffprobe)
    print(f"{'ffprobe':<15} {ffprobe or 'нет — размеры и длительность будут неполными'}")

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

    if find_spec("pynput"):
        print("pynput          есть")
    else:
        print("pynput          нет (нужен трею и `snapreel daemon` для хоткеев)")

    if find_spec("PySide6"):
        print("PySide6         есть")
    else:
        print("PySide6         нет (окна и трей не показать: pip install 'snapreel[ui]')")

    # Qt на Linux без этой библиотеки не поднимает окно вовсе, и понять это
    # по своему опыту человек не может: окно просто не появляется
    if env.is_linux and not deps.is_satisfied(deps.XCB_CURSOR):
        problems.append(
            "нет libxcb-cursor0 — Qt не покажет ни окна настроек, ни иконки "
            "(apt install libxcb-cursor0)"
        )
    print(f"автозапуск:     {'включён' if autostart.autostart_enabled(env) else 'выключен'}")

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
