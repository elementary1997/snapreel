#!/usr/bin/env bash
# Установка snapreel на Linux или macOS с нуля.
#
#   ./scripts/install.sh                    # спросит хоткей
#   ./scripts/install.sh --hotkey 'Ctrl+Alt+5'
#   ./scripts/install.sh --yes              # молча, с текущими значениями
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="${SNAPREEL_VENV:-$root/.venv}"

python="${PYTHON:-python3}"
if ! command -v "$python" >/dev/null 2>&1; then
    echo "нужен python3" >&2
    exit 1
fi

version_ok=$("$python" -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')
if [ "$version_ok" != "1" ]; then
    echo "нужен Python 3.10 или новее, найден: $("$python" --version)" >&2
    exit 1
fi

echo "== окружение: $venv"
"$python" -m venv "$venv"
"$venv/bin/pip" install --quiet --upgrade pip
"$venv/bin/pip" install --quiet -e "$root[daemon]"

echo "== настройка"
"$venv/bin/snapreel" setup "$@"

echo
echo "== проверка"
"$venv/bin/snapreel" doctor || true

cat <<EOF

Готово. Команда записи:
  $venv/bin/snapreel record

Поменять хоткей позже:
  $venv/bin/snapreel hotkey set 'Ctrl+Alt+5'
EOF
