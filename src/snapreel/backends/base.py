"""Общий контракт бэкендов захвата."""

from __future__ import annotations

import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import proc
from ..config import Config
from ..region import Region


class CaptureError(RuntimeError):
    """Захват невозможен или сорвался."""


@dataclass
class Recording:
    """Запущенный процесс записи.

    Останавливается двумя способами: `stop()` просит процесс закрыть контейнер
    штатно, `kill()` — крайняя мера, после которой файл может оказаться битым.
    """

    process: subprocess.Popen
    output: Path
    started_at: float = field(default_factory=time.monotonic)
    graceful_stop: str = "q"  # "q" в stdin (ffmpeg) либо "sigint" (wf-recorder)
    _stderr_tail: list[str] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def finished(self) -> bool:
        return self.process.poll() is not None

    def stop(self, timeout: float = 10.0) -> int:
        """Просит процесс дописать файл и дожидается его завершения."""
        if self.process.poll() is None:
            try:
                if self.graceful_stop == "q" and self.process.stdin:
                    self.process.stdin.write(b"q")
                    self.process.stdin.flush()
                else:
                    self.process.send_signal(signal.SIGINT)
            except (OSError, ValueError):
                pass
        return self.wait(timeout)

    def wait(self, timeout: float = 10.0) -> int:
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
        self._drain()
        return self.process.returncode or 0

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)

    def _drain(self) -> None:
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
        if self.process.stderr:
            try:
                text = self.process.stderr.read().decode("utf-8", "replace")
            except (OSError, ValueError):
                text = ""
            finally:
                self.process.stderr.close()
            if text:
                self._stderr_tail = text.strip().splitlines()[-25:]

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)


class CaptureBackend:
    """Базовый бэкенд: строит команду и запускает её."""

    name = "base"
    graceful_stop = "q"

    def __init__(self, config: Config) -> None:
        self.config = config

    def preflight(self) -> list[str]:
        """Список причин, по которым бэкенд не заработает. Пусто — всё в порядке."""
        raise NotImplementedError

    def build_command(self, region: Region, output: Path, duration: float) -> list[str]:
        raise NotImplementedError

    def start(self, region: Region, output: Path, duration: float) -> Recording:
        problems = self.preflight()
        if problems:
            raise CaptureError("; ".join(problems))
        command = self.build_command(region, output, duration)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            process = proc.popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise CaptureError(f"не запустить {command[0]}: {exc}") from exc
        return Recording(process=process, output=output, graceful_stop=self.graceful_stop)


def require_binary(name: str, hint: str = "") -> list[str]:
    if shutil.which(name):
        return []
    message = f"не найден {name}"
    return [f"{message} ({hint})" if hint else message]


def encode_args(config: Config) -> list[str]:
    """Общий хвост кодирования в H.264 для всех ffmpeg-бэкендов."""
    return [
        "-c:v",
        "libx264",
        "-preset",
        config.preset,
        "-crf",
        str(config.crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
