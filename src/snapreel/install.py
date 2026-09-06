"""Установка скачанного бинарника на машину.

Человек скачивает один файл, запускает — и дальше программа должна жить
сама: лежать в постоянном месте и подниматься при входе в систему. Пока она
лежит в «Загрузках», любая уборка папки уносит её вместе с автозапуском.

Ставится только собранный бинарник: установка из исходников делается pip'ом,
и копировать там нечего.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .autostart import Outcome
from .platform_info import Environment, Platform, detect

APP = "snapreel"


@dataclass(frozen=True)
class Plan:
    """Что именно произойдёт при установке — это же показывается человеку."""

    source: Path
    target: Path
    installed: bool  # уже стоит на месте


def supported() -> bool:
    """Ставить умеем только собранный бинарник."""
    return bool(getattr(sys, "frozen", False))


def target_path(env: Environment | None = None) -> Path:
    """Куда кладём: у каждой системы своё место для программ пользователя.

    Права администратора не нужны нигде: snapreel ставится только текущему
    пользователю, и просить у него больше — значит просить зря.
    """
    env = env or detect()
    if env.platform is Platform.WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Programs" / APP / f"{APP}.exe"
    if env.platform is Platform.MACOS:
        return Path.home() / "Applications" / APP
    return Path.home() / ".local" / "bin" / APP


def plan(env: Environment | None = None) -> Plan:
    source = Path(sys.executable).resolve()
    target = target_path(env)
    try:
        installed = target.exists() and target.resolve() == source
    except OSError:
        installed = False
    return Plan(source=source, target=target, installed=installed)


def install(env: Environment | None = None, with_autostart: bool = True) -> Outcome:
    """Копирует бинарник на место и, если просят, прописывает автозапуск.

    Копия кладётся рядом и переименовывается на место — ровно по той же
    причине, что и в обновлении: оборванное копирование не должно оставить
    обрубок вместо рабочего файла.
    """
    if not supported():
        return Outcome(False, "устанавливать умеем только готовый бинарник")

    current = plan(env)
    if current.installed:
        return Outcome(True, f"уже установлен: {current.target}")

    staged = current.target.with_name(current.target.name + ".new")
    try:
        current.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current.source, staged)
        staged.chmod(0o755)
        os.replace(staged, current.target)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
        return Outcome(False, f"не установить в {current.target}: {exc}")

    if with_autostart:
        # автозапуск прописывает уже установленная копия: путь к себе она
        # знает сама, а нам пришлось бы его подставлять
        result = subprocess.run(
            [str(current.target), "autostart"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            return Outcome(True, f"установлен: {current.target}. Автозапуск не вышел: {message}")

    return Outcome(True, f"установлен: {current.target}")


def uninstall(env: Environment | None = None) -> Outcome:
    """Обратная операция: снимает автозапуск и убирает установленную копию."""
    from . import autostart

    env = env or detect()
    target = target_path(env)
    autostart.remove_autostart(env)
    if not target.exists():
        return Outcome(True, "установленной копии не найдено")

    if supported() and Path(sys.executable).resolve() == target.resolve():
        # удалять сам себя нельзя: бутлоадер PyInstaller при выходе не найдёт
        # свой файл и напишет человеку страшное английское сообщение о том,
        # что продолжение невозможно. Уборку делает отдельный процесс, который
        # нас переживёт
        _remove_after_exit(target, env)
        return Outcome(True, f"удалён: {target}")

    try:
        target.unlink()
    except OSError as exc:
        return Outcome(False, f"не удалить {target}: {exc}")
    return Outcome(True, f"удалён: {target}")


def _remove_after_exit(target: Path, env: Environment) -> None:
    """Просит систему убрать файл через секунду — когда нас уже не будет."""
    if env.platform is Platform.WINDOWS:
        # ping вместо timeout: timeout требует консоли, а её у оконной сборки нет
        command = ["cmd", "/c", f'ping -n 2 127.0.0.1 >nul & del /f /q "{target}"']
    else:
        command = ["sh", "-c", f'sleep 1; rm -f "{target}"']
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass  # не вышло — файл останется лежать, но это не отказ удаления


def launch(target: Path, env: Environment | None = None, autostarted: bool = False) -> bool:
    """Поднимает установленную копию и говорит, вышло ли.

    На macOS с только что прописанным LaunchAgent поднимать нечего:
    `launchctl load` запускает его сам (RunAtLoad), и вторая копия просто
    подралась бы с первой за горячие клавиши.
    """
    env = env or detect()
    if autostarted and env.platform is Platform.MACOS:
        return True
    try:
        subprocess.Popen([str(target), "tray"], stdin=subprocess.DEVNULL)
    except OSError:
        return False
    return True
