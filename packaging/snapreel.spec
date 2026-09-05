# PyInstaller spec: один самодостаточный исполняемый файл на каждую ОС.
#
#   pyinstaller packaging/snapreel.spec --noconfirm
#
# ffmpeg намеренно НЕ бандлится: это +80 МБ к каждому артефакту и отдельные
# лицензионные условия у сборок с libx264. Пользователь ставит его сам —
# `snapreel setup` умеет это сделать (см. ADR-0003).

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
    "tkinter",
]

# pynput нужен только команде daemon; если его нет в окружении сборки,
# бинарник всё равно должен собраться
try:
    import pynput  # noqa: F401

    hidden.append("pynput")
except ImportError:
    pass

analysis = Analysis(
    ["entrypoint.py"],
    pathex=["../src"],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "PIL", "pytest"],
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
