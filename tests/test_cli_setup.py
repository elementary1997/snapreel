"""Выбор хоткея пользователем и неинтерактивный setup."""

from __future__ import annotations

import pytest

from snapreel import cli
from snapreel import config as config_module
from snapreel.platform_info import Environment, Platform

X11 = Environment(Platform.LINUX_X11, is_wsl=False)


def test_ask_hotkey_keeps_current_on_empty_input(tty, answers):
    answers("")
    assert cli._ask_hotkey("Хоткей", "<ctrl>+<alt>+r", assume_yes=False) == "<ctrl>+<alt>+r"


def test_ask_hotkey_normalizes_human_input(tty, answers):
    answers("Ctrl+Alt+5")
    assert cli._ask_hotkey("Хоткей", "<ctrl>+<alt>+r", assume_yes=False) == "<ctrl>+<alt>+5"


def test_ask_hotkey_reprompts_until_valid(tty, answers, capsys):
    """Клавиша без модификатора отбивается, но диалог не падает."""
    rest = answers("r", "Ctrl+Shift+F9")

    result = cli._ask_hotkey("Хоткей", "<ctrl>+<alt>+r", assume_yes=False)

    assert result == "<ctrl>+<shift>+<f9>"
    assert rest == []
    assert "модификатор" in capsys.readouterr().out


def test_ask_hotkey_does_not_prompt_without_a_terminal(monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _p="": pytest.fail("спрашивать нельзя"))
    assert cli._ask_hotkey("Хоткей", "<ctrl>+<alt>+r", assume_yes=False) == "<ctrl>+<alt>+r"


def test_setup_saves_chosen_hotkeys(tmp_path, monkeypatch, capsys):
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "detect", lambda: X11)
    monkeypatch.setattr(
        cli.autostart,
        "install",
        lambda hotkey, env: cli.autostart.Outcome(True, f"назначено {hotkey}"),
    )

    code = cli.main(
        [
            "--config",
            str(config_file),
            "setup",
            "--yes",
            "--no-deps",
            "--hotkey",
            "Ctrl+Alt+5",
            "--hotkey-gif",
            "Ctrl+Alt+6",
        ]
    )

    assert code == 0
    saved = config_module.load(config_file)
    assert saved.hotkey_mp4 == "<ctrl>+<alt>+5"
    assert saved.hotkey_gif == "<ctrl>+<alt>+6"
    assert "Ctrl+Alt+5" in capsys.readouterr().out


def test_setup_rejects_hotkey_without_modifier(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "detect", lambda: X11)
    code = cli.main(
        ["--config", str(tmp_path / "c.toml"), "setup", "--yes", "--no-deps", "--hotkey", "r"]
    )
    assert code == 2
    assert not (tmp_path / "c.toml").exists()


def test_setup_can_skip_registering_the_hotkey(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "detect", lambda: X11)
    monkeypatch.setattr(
        cli.autostart, "install", lambda *a, **k: pytest.fail("хоткей ставить не просили")
    )
    code = cli.main(
        [
            "--config",
            str(tmp_path / "c.toml"),
            "setup",
            "--yes",
            "--no-deps",
            "--no-hotkey",
            "--hotkey",
            "Ctrl+Alt+7",
        ]
    )
    assert code == 0
    assert config_module.load(tmp_path / "c.toml").hotkey_mp4 == "<ctrl>+<alt>+7"


def test_hotkey_set_writes_config_and_registers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "detect", lambda: X11)
    installed = []
    monkeypatch.setattr(
        cli.autostart,
        "install",
        lambda hotkey, env: installed.append(hotkey) or cli.autostart.Outcome(True, "ок"),
    )

    code = cli.main(["--config", str(tmp_path / "c.toml"), "hotkey", "set", "Win+Shift+S"])

    assert code == 0
    assert installed == ["<super>+<shift>+s"]
    assert config_module.load(tmp_path / "c.toml").hotkey_mp4 == "<super>+<shift>+s"


def test_hotkey_set_for_gif_does_not_touch_the_system(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "detect", lambda: X11)
    monkeypatch.setattr(
        cli.autostart, "install", lambda *a, **k: pytest.fail("GIF системно не назначается")
    )

    code = cli.main(["--config", str(tmp_path / "c.toml"), "hotkey", "set", "--gif", "Ctrl+Alt+G"])

    assert code == 0
    assert config_module.load(tmp_path / "c.toml").hotkey_gif == "<ctrl>+<alt>+g"


def test_hotkey_show_lists_both(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "c.toml"), "hotkey", "show"]) == 0
    out = capsys.readouterr().out
    assert "MP4:" in out and "GIF:" in out


def test_setup_survives_a_desktop_that_cannot_register(tmp_path, monkeypatch, capsys):
    """KDE, i3 и прочие: конфиг всё равно сохранён, дальше — ручная привязка."""
    monkeypatch.setattr(cli, "detect", lambda: X11)

    def refuse(hotkey, env):
        raise cli.autostart.HotkeySetupError("не найден gsettings — это не GNOME")

    monkeypatch.setattr(cli.autostart, "install", refuse)

    code = cli.main(
        [
            "--config",
            str(tmp_path / "c.toml"),
            "setup",
            "--yes",
            "--no-deps",
            "--hotkey",
            "Ctrl+Alt+5",
        ]
    )

    assert code == 0
    assert config_module.load(tmp_path / "c.toml").hotkey_mp4 == "<ctrl>+<alt>+5"
    assert "Назначьте вручную" in capsys.readouterr().out
