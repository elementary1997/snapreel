"""Готовим заметки именно для выпускаемого тега, а не для всего журнала."""

import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
version = sys.argv[1].removeprefix("v")
changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
match = re.search(
    rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
    changelog,
    re.MULTILINE | re.DOTALL,
)
if match is None:
    raise SystemExit(f"Нет записи CHANGELOG для {version}")
notes = (
    match.group(1).strip()
    + "\n\n"
    + (
        "Готовые бинарники содержат ffmpeg. На Linux нужны системные библиотеки Qt, "
        "утилиты буфера обмена, а для KDE/GNOME Wayland — портал, PipeWire и GStreamer. "
        "`snapreel doctor` проверяет окружение, `snapreel setup` предлагает установку "
        "недостающих пакетов с подтверждением пользователя.\n\n"
        "Проверено на Debian 13 / KDE Plasma 6.3. GNOME использует тот же контракт "
        "ScreenCast, но отдельная нативная приёмка ещё не выполнена.\n\n"
        "macOS: бинарник не подписан; после скачивания снимите карантин: "
        "`xattr -d com.apple.quarantine ./snapreel-macos-*`.\n\n"
        "Контрольные суммы файлов — SHA256SUMS.\n"
    )
)
(root / "release-notes.md").write_text(notes, encoding="utf-8")
