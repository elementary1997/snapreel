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
    "tkinter",
]

# pynput нужен хоткеям трея и команде daemon, pystray с Pillow — самой иконке.
# Наличие проверяется find_spec, а не импортом: на сборочном раннере дисплея
# нет, и оба модуля там падают на подключении к X-серверу — pynput своим
# ImportError, pystray ошибкой Xlib. Импорт сказал бы «модуля нет», хотя он
# есть, и собрался бы бинарник без хоткеев и без трея. Если модуля правда нет,
# бинарник всё равно должен собраться — без этих возможностей.
# Бэкенды обе библиотеки выбирают в рантайме по платформе, поэтому граф
# импортов их не видит: с одним лишь "pynput" в бинарнике оказывается пакет,
# который на первом же обращении говорит «this platform is not supported».
# Берётся набор той платформы, на которой идёт сборка, — релиз собирается на
# каждой отдельно.
_PYNPUT = {
    "win32": ["pynput.keyboard._win32", "pynput.mouse._win32", "pynput._util.win32"],
    "darwin": ["pynput.keyboard._darwin", "pynput.mouse._darwin", "pynput._util.darwin"],
}.get(sys.platform, ["pynput.keyboard._xorg", "pynput.mouse._xorg", "pynput._util.xorg"])
_PYSTRAY = {
    "win32": ["pystray._win32"],
    "darwin": ["pystray._darwin"],
}.get(sys.platform, ["pystray._xorg"])

if find_spec("pynput"):
    hidden += ["pynput", "pynput.keyboard", "pynput.mouse", *_PYNPUT]
if find_spec("pystray"):
    hidden += ["pystray", *_PYSTRAY, "PIL", "PIL.Image", "PIL.ImageDraw"]

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
