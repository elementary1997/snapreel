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
from importlib.util import find_spec

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
    "snapreel.hotkeys_x11",
]

# pynput нужен хоткеям, PySide6 — окнам и трею. Наличие проверяется find_spec,
# а не импортом: на сборочном раннере дисплея нет, и оба модуля там падают на
# подключении к оконной системе. Импорт сказал бы «модуля нет», хотя он есть,
# и собрался бы бинарник без хоткеев и без окон.
#
# Бэкенды pynput выбираются в рантайме, поэтому граф импортов их не видит:
# берётся набор той платформы, на которой идёт сборка.
_PYNPUT = {
    "win32": ["pynput.keyboard._win32", "pynput.mouse._win32", "pynput._util.win32"],
    "darwin": ["pynput.keyboard._darwin", "pynput.mouse._darwin", "pynput._util.darwin"],
}.get(sys.platform, ["pynput.keyboard._xorg", "pynput.mouse._xorg", "pynput._util.xorg"])

if find_spec("pynput"):
    hidden += ["pynput", "pynput.keyboard", "pynput.mouse", *_PYNPUT]
# python-xlib приезжает вместе с pynput, но нужен и сам по себе: на X-сервере
# без расширения RECORD комбинации ловит наш XGrabKey, а он импортирует Xlib
# внутри функций — из графа импортов этого не видно
if sys.platform not in ("win32", "darwin") and find_spec("Xlib"):
    hidden += ["Xlib", "Xlib.display", "Xlib.X", "Xlib.XK", "Xlib.error", "Xlib.ext"]
if find_spec("PySide6"):
    hidden += ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"]

# Qt большой, и в бинарник ему незачем ехать целиком: snapreel рисует окна
# виджетами и не трогает ни QML, ни мультимедиа, ни базы данных
qt_excludes = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

binaries = []
_bundled_ffmpeg = os.environ.get("SNAPREEL_BUNDLE_FFMPEG")
if _bundled_ffmpeg:
    if not os.path.isfile(_bundled_ffmpeg):
        raise SystemExit(f"SNAPREEL_BUNDLE_FFMPEG указывает в никуда: {_bundled_ffmpeg}")
    # корень распакованного бандла: там его ищет snapreel.bundled.binary
    binaries.append((_bundled_ffmpeg, "."))

# иконка приложения: её же читают окно настроек и трей
_assets = os.path.join(os.path.abspath("../src/snapreel"), "assets")
datas = [(_assets, os.path.join("snapreel", "assets"))] if os.path.isdir(_assets) else []
_icon = os.path.join(_assets, "icon.ico" if sys.platform == "win32" else "icon.png")

analysis = Analysis(
    ["entrypoint.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # tkinter из проекта ушёл вместе с оверлеями (ADR-0009)
    excludes=["numpy", "pytest", "PIL", "tkinter", "tcl", "tk"] + qt_excludes,
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
    # На Windows сборка оконная: у консольной двойной щелчок открывает рядом
    # с иконкой чёрное окно терминала, а приложение живёт в трее. Вывод при
    # запуске из терминала не теряется — `platform_info.attach_console`
    # подключается к консоли родителя. На остальных системах терминал сам
    # решает, показывать ли окно, и консольная сборка ничего не портит.
    console=sys.platform != "win32",
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon if os.path.isfile(_icon) else None,
)
