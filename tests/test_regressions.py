"""Тесты на дефекты, найденные локальным ревью первого круга."""

from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime

import pytest

from snapreel import cli, encode, naming, recorder, storage
from snapreel import config as config_module
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
    assert naming.is_ours(name, Config().filename_template) is expected


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
        [sys.executable, "-c", "import sys; sys.stdin.read(1); sys.exit(0)"],
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


def signal_only(tmp_path) -> Recording:
    """Процесс в духе wf-recorder: stdin игнорирует, по SIGINT выходит с кодом 7."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, sys, time;"
            "signal.signal(signal.SIGINT, lambda *a: sys.exit(7));"
            "print('ready', flush=True);"
            "time.sleep(30)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # без этого сигнал успевает прийти раньше, чем установлен обработчик,
    # и процесс умирает с кодом -2 вместо своего 7
    assert process.stdout.readline().strip() == b"ready"
    return Recording(process=process, output=tmp_path / "clip.mp4")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="ветка SIGINT нужна только wf-recorder, а он существует лишь в Linux",
)
def test_stop_falls_back_to_signal_for_wf_recorder(tmp_path):
    """Код 7 доказывает, что процесс получил именно SIGINT, а не был убит по таймауту."""
    recording = signal_only(tmp_path)
    recording.graceful_stop = "sigint"

    assert recording.stop(timeout=10) == 7
    assert recording.finished


def test_stop_is_idempotent(tmp_path):
    """`recorder` вызывает stop и из индикатора, и в finally."""
    recording = sleeper(tmp_path)

    assert recording.stop(timeout=10) == 0
    assert recording.stop(timeout=10) == 0


def test_kill_ends_a_process_that_ignores_the_letter(tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    recording = Recording(process=process, output=tmp_path / "clip.mp4")

    recording.stop(timeout=1)

    assert recording.finished


def test_stderr_tail_is_captured(tmp_path):
    process = subprocess.Popen(
        # только ASCII: кодировка командной строки в Windows исказит кириллицу
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('ffmpeg: bad format\\n'); sys.exit(1)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    recording = Recording(process=process, output=tmp_path / "clip.mp4")

    recording.wait(timeout=10)

    assert "bad format" in recording.stderr_tail


# --- шаблон имени сужен до проверяемой грамматики ------------------------


@pytest.mark.parametrize("template", ["%F", "%s", "%T", "%D", "%R", "%e", "%-d", "%Y/%m"])
def test_unknown_directive_is_rejected_at_load(template, tmp_path):
    """Незнакомая директива дала бы выражение, совпадающее с чем угодно."""
    path = tmp_path / "config.toml"
    path.write_text(f'filename_template = "{template}"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="filename_template"):
        config_module.load(path)


@pytest.mark.parametrize("template", ["snapreel-%Y%m%d-%H%M%S", "clip_%Y-%m-%d", "clip", "%j.%Y"])
def test_supported_templates_round_trip(template, tmp_path):
    """Что `new_path` создал, то `prune` обязан узнать — иначе keep_days молча мёртв."""
    cfg = Config(output_dir=str(tmp_path), filename_template=template, keep_days=7)
    cfg.validate()

    created = storage.new_path(cfg, ".mp4", datetime(2024, 3, 5, 9, 7, 1))
    created.touch()
    duplicate = storage.new_path(cfg, ".mp4", datetime(2024, 3, 5, 9, 7, 1))
    duplicate.touch()
    for path in (created, duplicate):
        stale(path)

    assert sorted(storage.prune(cfg)) == sorted([created, duplicate])


def test_foreign_names_survive_every_supported_template(tmp_path):
    cfg = Config(output_dir=str(tmp_path), filename_template="clip_%Y-%m-%d", keep_days=7)
    foreign = [tmp_path / "wedding-2019.mp4", tmp_path / "cat.gif", tmp_path / "clip_report.mp4"]
    for path in foreign:
        path.touch()
        stale(path)

    assert storage.prune(cfg) == []
    assert all(path.exists() for path in foreign)


def test_path_separators_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        Config(filename_template="../../etc/passwd").validate()


# --- сборка GIF не выпускает наружу ничего, кроме EncodeError ------------


def test_gif_timeout_scales_with_clip_length():
    """300 секунд не хватало палитре получасовой записи."""
    assert encode.gif_timeout(Config(max_seconds=60)) > 60
    assert encode.gif_timeout(Config(max_seconds=1800)) > encode.gif_timeout(Config(max_seconds=60))


def test_gif_timeout_becomes_encode_error(monkeypatch, tmp_path):
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(encode.subprocess, "run", hang)

    with pytest.raises(EncodeError, match="не уложилась"):
        encode.to_gif(tmp_path / "in.mp4", tmp_path / "out.gif", Config())


def test_missing_ffmpeg_becomes_encode_error(monkeypatch, tmp_path):
    def missing(command, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(encode.subprocess, "run", missing)

    with pytest.raises(EncodeError):
        encode.to_gif(tmp_path / "in.mp4", tmp_path / "out.gif", Config())


def test_hanging_gif_keeps_the_clip(wired, tmp_path, monkeypatch):
    """Зависший ffmpeg проходит всю настоящую цепочку: to_gif -> EncodeError -> клип цел."""
    wired()

    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 300))

    monkeypatch.setattr(encode.subprocess, "run", hang)

    result = recorder.record(
        Config(output_dir=str(tmp_path / "clips"), notify=False),
        region=Region(0, 0, 100, 100),
        as_gif=True,
        indicator=False,
        env=X11,
    )

    assert result.video.is_file()
    assert result.gif is None
    assert result.payload == result.video
    assert "не уложилась" in result.gif_error
    assert wired.copied == [result.video]


# --- испорченный хоткей в конфиге не роняет команды ----------------------


@pytest.mark.parametrize("command", [["doctor"], ["hotkey", "show"]])
def test_broken_hotkey_does_not_break_commands(command, tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('hotkey_mp4 = "<ctrl>"\n', encoding="utf-8")

    code = cli.main(["--config", str(path), *command])
    output = capsys.readouterr()

    assert code in (0, 1)  # doctor вправе сообщить о проблеме, но не упасть
    assert "Traceback" not in output.err
    assert "ctrl" in output.out


def test_doctor_lists_the_broken_hotkey_as_a_problem(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('hotkey_gif = "+"\n', encoding="utf-8")

    cli.main(["--config", str(path), "doctor"])

    assert "hotkey_gif" in capsys.readouterr().out


def test_daemon_refuses_an_unparsable_hotkey(tmp_path, capsys):
    code = cli.main(["--config", str(tmp_path / "c.toml"), "daemon", "--hotkey", "<ctrl>"])

    assert code == 2
    assert "Traceback" not in capsys.readouterr().err


# --- круг 3: команда починки чинит, а не падает --------------------------


def test_setup_repairs_a_broken_hotkey_from_the_config(tmp_path, monkeypatch, capsys):
    """doctor советует setup как починку — она не вправе спотыкаться о то же значение."""
    path = tmp_path / "config.toml"
    path.write_text('hotkey_mp4 = "<ctrl>"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "detect", lambda: X11)

    code = cli.main(["--config", str(path), "setup", "--yes", "--no-deps", "--no-hotkey"])
    output = capsys.readouterr()

    assert code == 0
    assert "Traceback" not in output.err
    assert config_module.load(path).hotkey_mp4 == Config().hotkey_mp4


def test_hotkey_prompt_survives_a_broken_current_value(tty, answers, capsys):
    answers("")
    assert cli._ask_hotkey("Хоткей", "<ctrl>", assume_yes=False) == "<ctrl>"
    assert "Traceback" not in capsys.readouterr().out


# --- круг 3: вывод внешних утилит не обязан быть валидным UTF-8 ----------


def test_undecodable_stderr_becomes_encode_error(monkeypatch, tmp_path):
    """Имена файлов и вывод ffmpeg не обязаны быть UTF-8; строгое декодирование роняло запись."""

    def undecodable(command, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(encode.subprocess, "run", undecodable)

    with pytest.raises(EncodeError):
        encode.to_gif(tmp_path / "in.mp4", tmp_path / "out.gif", Config())


def test_undecodable_output_keeps_the_clip(wired, tmp_path, monkeypatch):
    wired()

    def undecodable(command, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(encode.subprocess, "run", undecodable)

    result = recorder.record(
        Config(output_dir=str(tmp_path / "clips"), notify=False),
        region=Region(0, 0, 100, 100),
        as_gif=True,
        indicator=False,
        env=X11,
    )

    assert result.video.is_file()
    assert result.payload == result.video
    assert result.gif_error
    assert wired.copied == [result.video]


def test_probe_survives_an_undecodable_answer(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"data")

    def undecodable(command, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(encode.subprocess, "run", undecodable)

    info = encode.probe(clip, Config())

    assert info is not None
    assert info.size_bytes == 4


def test_external_output_is_decoded_leniently():
    """errors=replace на каждом вызове: иначе один странный байт в выводе роняет команду."""
    import re

    package = pathlib.Path(encode.__file__).parent
    strict = re.compile(r"text=True(?!\s*,\s*errors=)")
    offenders = [
        f"{path.relative_to(package)}:{source[: match.start()].count(chr(10)) + 1}"
        for path in sorted(package.rglob("*.py"))
        for source in [path.read_text(encoding="utf-8")]
        for match in strict.finditer(source)
    ]

    assert offenders == []


# --- вывод переживает консоль, не знающую кириллицы ----------------------


def ansi_console():
    """Перенаправленный stdout на английской Windows: cp1252 и никакой кириллицы."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")


def test_help_survives_a_console_without_cyrillic(monkeypatch):
    stream = ansi_console()
    monkeypatch.setattr(sys, "stdout", stream)

    with pytest.raises(SystemExit) as exit_code:
        cli.main(["--help"])

    assert exit_code.value.code == 0
    stream.flush()
    assert "буфер" in stream.buffer.getvalue().decode("utf-8")


def test_error_message_survives_a_console_without_cyrillic(monkeypatch, tmp_path):
    stream = ansi_console()
    monkeypatch.setattr(sys, "stderr", stream)

    code = cli.main(["--config", str(tmp_path / "c.toml"), "record", "--region", "boom"])

    assert code == 2
    stream.flush()
    assert "геометрию" in stream.buffer.getvalue().decode("utf-8")


def test_a_path_with_undecodable_bytes_still_prints(monkeypatch):
    """Имя файла из ФС приезжает одинокими суррогатами, а строгий UTF-8 падает и на них."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", stream)
    cli._make_output_printable()

    print("/tmp/\udcff.mp4")

    stream.flush()
    assert b".mp4" in stream.buffer.getvalue()


# --- голый вызов без подкоманды пишет клип -------------------------------


def test_bare_invocation_records_a_clip(wired, tmp_path, monkeypatch, capsys):
    """Двойной щелчок по бинарнику зовёт snapreel без единого аргумента."""
    wired()
    monkeypatch.setattr(recorder, "select_region", lambda env: Region(0, 0, 100, 100))
    config = tmp_path / "config.toml"
    config.write_text(
        f'output_dir = "{(tmp_path / "clips").as_posix()}"\nnotify = false\n', encoding="utf-8"
    )

    code = cli.main(["--config", str(config)])

    assert code == 0
    assert "Traceback" not in capsys.readouterr().err
    assert list((tmp_path / "clips").glob("*.mp4"))


def test_bare_invocation_reads_argv_when_none_is_given(wired, tmp_path, monkeypatch):
    """Бинарник зовёт main() без аргументов — подкоманда дописывается к sys.argv."""
    wired()
    monkeypatch.setattr(recorder, "select_region", lambda env: Region(0, 0, 100, 100))
    config = tmp_path / "config.toml"
    config.write_text(
        f'output_dir = "{(tmp_path / "clips").as_posix()}"\nnotify = false\n', encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["snapreel", "--config", str(config)])

    assert cli.main() == 0
    assert list((tmp_path / "clips").glob("*.mp4"))
