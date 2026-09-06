import pytest

from snapreel import deps
from snapreel.config import Config
from snapreel.platform_info import Environment, Platform

X11 = Environment(Platform.LINUX_X11, is_wsl=False)
WAYLAND = Environment(Platform.LINUX_WAYLAND, is_wsl=False)
MACOS = Environment(Platform.MACOS, is_wsl=False)
WINDOWS = Environment(Platform.WINDOWS, is_wsl=False)


def keys(env) -> set[str]:
    return {item.key for item in deps.required(env)}


def test_x11_needs_xclip_not_wayland_tools():
    assert "xclip" in keys(X11)
    assert "wl-copy" not in keys(X11)


def test_wayland_needs_its_own_stack():
    assert {"wf-recorder", "wl-copy"} <= keys(WAYLAND)
    assert "xclip" not in keys(WAYLAND)


@pytest.mark.parametrize("env", [MACOS, WINDOWS])
def test_desktop_platforms_need_only_ffmpeg(env):
    """Всё остальное там встроено: Qt ставится pip'ом, буфер обмена системный."""
    assert keys(env) == {"ffmpeg"}


def test_apt_updates_before_installing():
    commands = deps.install_commands("apt", [deps.FFMPEG, deps.XCLIP])
    assert commands[0][-2:] == ["apt-get", "update"]
    assert "ffmpeg" in commands[-1] and "xclip" in commands[-1]


def test_pacman_and_zypper_are_non_interactive():
    assert "--noconfirm" in deps.install_commands("pacman", [deps.FFMPEG])[0]
    assert "-y" in deps.install_commands("zypper", [deps.FFMPEG])[0]


def test_brew_installs_in_one_call():
    commands = deps.install_commands("brew", [deps.FFMPEG, deps.NOTIFY_SEND])
    assert commands == [["brew", "install", "ffmpeg"]]


def test_winget_installs_one_package_per_call():
    commands = deps.install_commands("winget", [deps.FFMPEG])
    assert len(commands) == 1
    assert commands[0][-1] == "Gyan.FFmpeg"
    assert "--silent" in commands[0]


def test_requirements_without_a_package_are_reported():
    """xclip в winget не поставить — об этом надо сказать, а не молчать."""
    assert deps.unresolved("winget", [deps.FFMPEG, deps.XCLIP]) == [deps.XCLIP]
    assert deps.install_commands("winget", [deps.XCLIP]) == []


def test_unknown_manager_is_an_error():
    with pytest.raises(ValueError):
        deps.install_commands("emerge", [deps.FFMPEG])


def test_missing_reports_absent_tools(monkeypatch):
    """Список считается от машины, поэтому в тесте машина подменяется целиком."""
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    monkeypatch.setattr(deps, "_library_present", lambda name: False)
    absent = {item.key for item in deps.missing(Config(), X11)}
    assert absent == {"ffmpeg", "libxcb-cursor", "xclip", "notify-send"}


def test_missing_is_empty_when_everything_is_present(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(deps, "_library_present", lambda name: True)
    assert deps.missing(Config(), X11) == []


def test_run_stops_at_first_failure(monkeypatch):
    calls = []

    class Result:
        def __init__(self, code):
            self.returncode = code

    def fake_run(command):
        calls.append(command)
        return Result(1 if len(calls) == 1 else 0)

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    ok, error = deps.run([["a"], ["b"]])

    assert ok is False
    assert calls == [["a"]]
    assert "кодом 1" in error
