"""Тесты на дефекты, найденные локальным ревью первого круга."""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from snapreel import cli, recorder, storage
from snapreel.backends.base import Recording
from snapreel.config import Config
from snapreel.encode import EncodeError
from snapreel.platform_info import Environment, Platform
from snapreel.region import Region

X11 = Environment(Platform.LINUX_X11, is_wsl=False)


def stale(path, days=99):
    moment = time.time() - days * 86400
    os.utime(path, (moment, moment))


# --- prune не трогает чужие файлы ----------------------------------------


def test_prune_spares_foreign_videos(tmp_path):
    """Каталогом вывода может быть общий ~/Videos с чужими записями."""
    cfg = Config(output_dir=str(tmp_path), keep_days=7)
    mine = tmp_path / "snapreel-20240101-120000.mp4"
    theirs = tmp_path / "wedding-2019.mp4"
    also_theirs = tmp_path / "cat.gif"
    for path in (mine, theirs, also_theirs):
        path.touch()
        stale(path)

    removed = storage.prune(cfg)

    assert removed == [mine]
    assert theirs.exists() and also_theirs.exists()


def test_prune_removes_numbered_duplicates(tmp_path):
    cfg = Config(output_dir=str(tmp_path), keep_days=7)
    duplicate = tmp_path / "snapreel-20240101-120000-2.gif"
    duplicate.touch()
    stale(duplicate)

    assert storage.prune(cfg) == [duplicate]


def test_prune_follows_a_custom_template(tmp_path):
    cfg = Config(output_dir=str(tmp_path), keep_days=7, filename_template="clip_%Y%m%d")
    mine = tmp_path / "clip_20240101.mp4"
    default_named = tmp_path / "snapreel-20240101-120000.mp4"
    for path in (mine, default_named):
        path.touch()
        stale(path)

    assert storage.prune(cfg) == [mine]
    assert default_named.exists()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("snapreel-20260905-194351.mp4", True),
        ("snapreel-20260905-194351-2.mp4", True),
        ("snapreel-20260905-194351.gif", True),
        ("snapreel-2026-09-05.mp4", False),
        ("wedding-2019.mp4", False),
        ("snapreel-20260905-194351.txt", False),
        ("prefix-snapreel-20260905-194351.mp4", False),
    ],
)
def test_name_pattern_recognizes_only_our_clips(name, expected):
    assert storage.is_ours(name, Config()) is expected


# --- сбой GIF не уносит записанный клип ----------------------------------


def test_gif_failure_keeps_the_mp4(wired, tmp_path, monkeypatch):
    wired()

    def explode(source, target, config):
        raise EncodeError("палитра не собралась")

    monkeypatch.setattr(recorder, "to_gif", explode)

    result = recorder.record(
        Config(output_dir=str(tmp_path / "clips"), notify=False),
        region=Region(0, 0, 100, 100),
        as_gif=True,
        indicator=False,
        env=X11,
    )

    assert result.video.is_file()
    assert result.gif is None
    assert result.payload == result.video  # в буфер уходит MP4
    assert "палитра" in result.gif_error
    assert wired.copied == [result.video]


# --- пользовательский ввод не выходит трейсбеком -------------------------


def test_wrong_type_in_config_is_a_readable_error(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('fps = "30"\n', encoding="utf-8")

    code = cli.main(["--config", str(path), "doctor"])

    assert code == 2
    assert "fps" in capsys.readouterr().err


def test_bad_region_is_a_readable_error(tmp_path, capsys):
    code = cli.main(["--config", str(tmp_path / "c.toml"), "record", "--region", "нечто"])

    assert code == 2
    assert "геометрию" in capsys.readouterr().err


def test_out_of_range_flag_is_a_readable_error(tmp_path, capsys):
    code = cli.main(["--config", str(tmp_path / "c.toml"), "record", "--fps", "9000"])

    assert code == 2
    assert "fps" in capsys.readouterr().err


# --- LaunchAgent запускает то, что существует ----------------------------


def test_launch_agent_uses_the_binary_when_frozen(monkeypatch):
    from snapreel import autostart

    monkeypatch.setattr(autostart, "is_frozen", lambda: True)
    monkeypatch.setattr(autostart.sys, "executable", "/opt/snapreel")

    assert autostart.daemon_argv() == ["/opt/snapreel", "daemon"]
    assert "<string>-m</string>" not in autostart.launch_agent_plist()
    assert "<string>/opt/snapreel</string>" in autostart.launch_agent_plist()


def test_launch_agent_uses_module_when_installed(monkeypatch):
    from snapreel import autostart

    monkeypatch.setattr(autostart, "is_frozen", lambda: False)
    monkeypatch.setattr(autostart.sys, "executable", "/usr/bin/python3")

    assert autostart.daemon_argv() == ["/usr/bin/python3", "-m", "snapreel", "daemon"]


# --- останов ffmpeg ------------------------------------------------------


def sleeper(tmp_path) -> Recording:
    """Настоящий процесс, читающий stdin, — как ffmpeg, ждущий букву `q`."""
    process = subprocess.Popen(
        ["python3", "-c", "import sys; sys.stdin.read(1); sys.exit(0)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return Recording(process=process, output=tmp_path / "clip.mp4")


def test_stop_writes_q_and_waits(tmp_path):
    """Останов по букве, а не по сигналу: на Windows SIGINT не доходит."""
    recording = sleeper(tmp_path)

    assert recording.stop(timeout=10) == 0
    assert recording.finished


def test_stop_falls_back_to_signal_for_wf_recorder(tmp_path):
    recording = sleeper(tmp_path)
    recording.graceful_stop = "sigint"

    recording.stop(timeout=10)

    assert recording.finished


def test_stop_is_idempotent(tmp_path):
    """`recorder` вызывает stop и из индикатора, и в finally."""
    recording = sleeper(tmp_path)

    assert recording.stop(timeout=10) == 0
    assert recording.stop(timeout=10) == 0


def test_kill_ends_a_process_that_ignores_the_letter(tmp_path):
    process = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    recording = Recording(process=process, output=tmp_path / "clip.mp4")

    recording.stop(timeout=1)

    assert recording.finished


def test_stderr_tail_is_captured(tmp_path):
    process = subprocess.Popen(
        ["python3", "-c", "import sys; sys.stderr.write('ffmpeg: не тот формат\\n'); sys.exit(1)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    recording = Recording(process=process, output=tmp_path / "clip.mp4")

    recording.wait(timeout=10)

    assert "не тот формат" in recording.stderr_tail
