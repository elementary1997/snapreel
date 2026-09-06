"""Общие подмены: сценарий записи без экрана, ffmpeg и буфера обмена."""

from __future__ import annotations

import os

import pytest

# Qt умеет рисовать в память, и в тестах это единственный допустимый режим:
# прогон обязан быть headless, а на машине с дисплеем окна иначе всплывали бы
# поверх работы. Ставится до первого импорта Qt, иначе платформа уже выбрана.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from snapreel import recorder
from snapreel.config import Config
from snapreel.region import Region

DESKTOP = Region(0, 0, 1920, 1080)


class FakeRecording:
    def __init__(self, output, payload=b"video-bytes"):
        self.output = output
        self.payload = payload
        self.stopped = 0
        self.stderr_tail = ""

    @property
    def finished(self) -> bool:
        return True

    @property
    def elapsed(self) -> float:
        return 1.0

    def stop(self, timeout: float = 10.0) -> int:
        self.stopped += 1
        if self.payload:
            self.output.write_bytes(self.payload)
        return 0


class FakeBackend:
    name = "fake"

    def __init__(self, config, payload=b"video-bytes"):
        self.config = config
        self.payload = payload
        self.region = None

    def preflight(self):
        return []

    def start(self, region, output, duration):
        self.region = region
        self.duration = duration
        output.parent.mkdir(parents=True, exist_ok=True)
        return FakeRecording(output, self.payload)


@pytest.fixture
def need():
    """Пропускает тест, когда модуль не поставлен или требует дисплея.

    `pytest.importorskip` тут не годится: без `DISPLAY` pystray и pynput
    бросают не `ModuleNotFoundError`, а свои ошибки подключения к X-серверу,
    а прогон обязан оставаться headless.
    """
    import importlib

    def require(module: str):
        try:
            return importlib.import_module(module)
        except Exception as exc:  # ImportError, Xlib.error.*, что угодно ещё
            pytest.skip(f"{module} недоступен здесь: {exc}")

    return require


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Подменяет всё, что требует живого экрана."""
    backends = {}

    def install(payload=b"video-bytes"):
        backend = FakeBackend(Config(), payload)
        backends["backend"] = backend
        monkeypatch.setattr(recorder, "for_environment", lambda cfg, env: backend)
        return backend

    monkeypatch.setattr(recorder, "virtual_desktop", lambda env: DESKTOP)
    monkeypatch.setattr(recorder, "enable_dpi_awareness", lambda: None)
    monkeypatch.setattr(recorder, "probe", lambda path, cfg: None)
    monkeypatch.setattr(recorder.notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(recorder, "_indicator_class", lambda: None)
    copied = []
    monkeypatch.setattr(recorder.clipboard, "copy_files", lambda paths, env: copied.extend(paths))
    monkeypatch.setattr(recorder.clipboard, "copy_text", lambda text, env: copied.append(text))
    install.copied = copied
    install.backends = backends
    return install


@pytest.fixture
def tty(monkeypatch):
    """Делает вид, что мы в терминале: иначе диалоги молча берут текущее значение."""
    from snapreel import cli

    monkeypatch.setattr(cli, "_interactive", lambda: True)


@pytest.fixture
def answers(monkeypatch):
    """Подставляет ответы на input по очереди; возвращает остаток очереди."""

    def feed(*values):
        queue = list(values)
        monkeypatch.setattr("builtins.input", lambda _prompt="": queue.pop(0))
        return queue

    return feed
