"""Проверка и установка обновлений с GitHub Releases.

Единственное место во всём snapreel, которому разрешено ходить в сеть, и
ходит оно только на github.com и только за релизами (ADR-0007). Ни телеметрии,
ни отправки чего-либо наружу здесь нет и быть не должно: запись экрана — это
личные данные, и она не покидает машину.

Обновляется только собранный бинарник. Установленному из исходников обновлять
нечего — там `pip install -U` или `git pull`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import bundled
from .platform_info import Environment, Platform, detect, machine

REPO = "elementary1997/snapreel"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600  # раз в сутки: релизы выходят реже, чаще — беспокоить зря
TIMEOUT = 15


class UpdateError(RuntimeError):
    """Обновиться не вышло — с объяснением, что делать."""


@dataclass(frozen=True)
class Release:
    version: tuple[int, ...]
    tag: str
    asset: str
    url: str
    checksums_url: str | None
    size: int

    @property
    def name(self) -> str:
        return ".".join(str(part) for part in self.version)


def parse_version(text: str) -> tuple[int, ...]:
    """`v0.2.0` и `0.2.0` — одно и то же; нечисловой хвост отбрасывается."""
    parts: list[int] = []
    for chunk in text.strip().lstrip("vV").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def current() -> tuple[int, ...]:
    from . import __version__

    return parse_version(__version__)


def asset_name(env: Environment | None = None, arch: str | None = None) -> str:
    """Как называется файл этой платформы в релизе."""
    env = env or detect()
    arch = arch or machine()
    if env.platform is Platform.WINDOWS:
        return "snapreel-windows-x86_64.exe"
    if env.platform is Platform.MACOS:
        return f"snapreel-macos-{'arm64' if arch == 'arm64' else 'x86_64'}"
    return "snapreel-linux-x86_64"


def supported() -> bool:
    """Обновлять умеем только собранный бинарник."""
    return bundled.root() is not None


def state_path(config_dir: Path) -> Path:
    return config_dir / "updates.json"


def _read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # не смогли запомнить — просто проверим ещё раз в следующий раз
        pass


def due(path: Path, now: float | None = None) -> bool:
    """Пора ли спрашивать github: не чаще раза в сутки."""
    now = time.time() if now is None else now
    last = _read_state(path).get("checked_at", 0)
    return not isinstance(last, (int, float)) or now - last >= CHECK_EVERY


def fetch(env: Environment | None = None, arch: str | None = None) -> Release:
    """Спрашивает github о последнем релизе. Наружу уходит только этот запрос."""
    request = urllib.request.Request(
        API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "snapreel"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise UpdateError(f"не спросить github об обновлениях: {exc}") from exc

    tag = str(data.get("tag_name") or "")
    version = parse_version(tag)
    if not version:
        raise UpdateError(f"непонятная версия релиза: {tag!r}")

    wanted = asset_name(env, arch)
    assets = {item.get("name"): item for item in data.get("assets", []) if item.get("name")}
    found = assets.get(wanted)
    if not found:
        raise UpdateError(f"в релизе {tag} нет файла для этой системы ({wanted})")

    checksums = assets.get("SHA256SUMS")
    return Release(
        version=version,
        tag=tag,
        asset=wanted,
        url=found.get("browser_download_url", ""),
        checksums_url=checksums.get("browser_download_url") if checksums else None,
        size=int(found.get("size") or 0),
    )


def check(config_dir: Path, force: bool = False, now: float | None = None) -> Release | None:
    """Новая версия или None. Без `force` спрашивает не чаще раза в сутки."""
    path = state_path(config_dir)
    if not force and not due(path, now):
        return None
    release = fetch()
    _write_state(path, {"checked_at": time.time() if now is None else now, "seen": release.tag})
    return release if release.version > current() else None


def _download(url: str, target: Path, progress=None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "snapreel"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, target.open("wb") as fh:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(64 * 1024):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"не скачать {url}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_sum(text: str, asset: str) -> str | None:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == asset:
            return parts[0]
    return None


def download(release: Release, into: Path, progress=None) -> Path:
    """Качает файл релиза и сверяет его с SHA256SUMS оттуда же."""
    into.mkdir(parents=True, exist_ok=True)
    target = into / release.asset
    _download(release.url, target, progress)

    if not release.checksums_url:
        raise UpdateError(
            f"в релизе {release.tag} нет SHA256SUMS — проверить скачанное нечем. "
            f"Скачайте вручную: {PAGE}"
        )
    sums = into / "SHA256SUMS"
    _download(release.checksums_url, sums)
    expected = _expected_sum(sums.read_text(encoding="utf-8", errors="replace"), release.asset)
    if not expected:
        raise UpdateError(f"в SHA256SUMS нет строки про {release.asset}")
    actual = _sha256(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        raise UpdateError(f"скачанное не сошлось по сумме: ждали {expected}, получили {actual}")
    return target


def install(new_binary: Path, running: Path | None = None) -> Path:
    """Ставит скачанное на место работающего бинарника.

    Работающий файл сначала отодвигается, а не удаляется: на Windows удалить
    запущенный exe нельзя, а переименовать — можно, и старая копия убирается
    при следующем запуске (`clean_leftovers`).
    """
    running = running or Path(sys.executable)
    backup = running.with_name(running.name + ".old")
    try:
        backup.unlink(missing_ok=True)
        os.replace(running, backup)
        shutil.copy2(new_binary, running)
        running.chmod(0o755)
    except OSError as exc:
        # вернуть как было: обновление не удалось, но рабочая копия нужна
        if backup.exists() and not running.exists():
            os.replace(backup, running)
        raise UpdateError(f"не заменить {running}: {exc}") from exc
    return running


def clean_leftovers(running: Path | None = None) -> None:
    """Убирает отодвинутую копию после удачного обновления."""
    running = running or Path(sys.executable)
    backup = running.with_name(running.name + ".old")
    try:
        backup.unlink(missing_ok=True)
    except OSError:
        # на Windows файл может быть ещё занят — уберём в следующий раз
        pass


def clean_leftovers_if_frozen() -> None:
    """Уборка после обновления; из исходников убирать нечего."""
    if supported():
        clean_leftovers()


def update(release: Release, progress=None) -> Path:
    """Скачать, проверить, поставить. Каталог для скачанного — временный."""
    if not supported():
        raise UpdateError(
            "обновлять умеем только готовый бинарник. Установлено из исходников — "
            "обновляйтесь через `pip install -U snapreel` или `git pull`."
        )
    with tempfile.TemporaryDirectory(prefix="snapreel-update-") as tmp:
        downloaded = download(release, Path(tmp), progress)
        return install(downloaded)
