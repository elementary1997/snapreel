# PyInstaller spec: один самодостаточный исполняемый файл на каждую ОС.
#
#   pyinstaller packaging/snapreel.spec --noconfirm
#
# ffmpeg вшивается в релизные бинарники: путь к собранному
# `scripts/build-ffmpeg.sh` приходит в SNAPREEL_BUNDLE_FFMPEG (см. ADR-0006).
# Без этой переменной получается прежняя сборка, которая ищет ffmpeg в PATH, —
# именно так собирается `make build` из исходников.

import os
import sys

block_cipher = None

hidden = [
    # платформенные модули берутся ленивыми импортами, граф их не видит
    "snapreel.clipboard.windows",
    "snapreel.clipboard.posix",
    "snapreel.backends.windows",
    "snapreel.backends.linux",
    "snapreel.backends.macos",
    "snapreel.selector",
    "snapreel.indicator",
    "snapreel.tray",
    "tkinter",
]

# pynput нужен хоткеям трея и команде daemon; если его нет в окружении
# сборки, бинарник всё равно должен собраться
try:
    import pynput  # noqa: F401

    hidden.append("pynput")
except ImportError:
    pass

# то же для иконки в трее: без pystray собирается бинарник без трея, и это
# честнее, чем упасть на сборке
try:
    import pystray  # noqa: F401

    hidden += ["pystray", "PIL", "PIL.Image", "PIL.ImageDraw"]
except ImportError:
    pass

binaries = []
_bundled_ffmpeg = os.environ.get("SNAPREEL_BUNDLE_FFMPEG")
if _bundled_ffmpeg:
    if not os.path.isfile(_bundled_ffmpeg):
        raise SystemExit(f"SNAPREEL_BUNDLE_FFMPEG указывает в никуда: {_bundled_ffmpeg}")
    # корень распакованного бандла: там его ищет snapreel.bundled.binary
    binaries.append((_bundled_ffmpeg, "."))

analysis = Analysis(
    ["entrypoint.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # PIL исключается только там, где трея в сборке нет: иконку рисует он
    excludes=["numpy", "pytest"] + ([] if "PIL" in hidden else ["PIL"]),
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    [],
    name="snapreel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # консольный: snapreel печатает путь к клипу и диагностику
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
