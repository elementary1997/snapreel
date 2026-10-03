"""Проверяем договор файлового буфера, не подключаясь к Wayland."""

from pathlib import Path

from snapreel import autostart
from snapreel.clipboard import posix, wayland_owner


def test_portal_identity_never_enables_autostart_or_overwrites_another_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    autostart.ensure_portal_entry()
    path = autostart.portal_entry_path()
    content = path.read_text(encoding="utf-8")
    assert "NoDisplay=true" in content
    assert "X-GNOME-Autostart-enabled" not in content
    autostart.remove_portal_entry()
    assert not path.exists()
    path.write_text("[Desktop Entry]\nName=User choice\n", encoding="utf-8")
    autostart.ensure_portal_entry()
    autostart.remove_portal_entry()
    assert path.read_text(encoding="utf-8") == "[Desktop Entry]\nName=User choice\n"


def test_data_control_failure_falls_back_to_a_file_uri(monkeypatch):
    calls = []

    def unavailable(paths):
        raise posix.ClipboardError("Нет data-control")

    monkeypatch.setattr(wayland_owner, "copy_files", unavailable)
    monkeypatch.setattr(posix.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        posix.proc, "run", lambda command, **kwargs: calls.append((command, kwargs))
    )
    path = Path("/tmp/мой клип.gif")
    posix.wayland_copy_files([path])
    command, options = calls[0]
    assert command == ["wl-copy", "--type", "text/uri-list"]
    assert options["input"] == (path.resolve().as_uri() + "\n").encode()
    assert b"%20" in options["input"] and b"%D0" in options["input"]


def test_a_native_owner_does_not_start_a_second_clipboard_owner(monkeypatch):
    calls = []
    monkeypatch.setattr(wayland_owner, "copy_files", lambda paths: calls.append(paths))
    monkeypatch.setattr(
        posix.proc, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    paths = [Path("/tmp/clip.gif")]
    posix.wayland_copy_files(paths)
    assert calls == [paths]
