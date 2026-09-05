#!/usr/bin/env bash
# AGENTS.md — единственный источник правды. Остальные файлы правил агентов
# генерируются из него этим скриптом.
#
# Копии, а не симлинки: проект разрабатывается в том числе на Windows, где
# симлинки в git работают только в developer mode и легко ломаются.
#
#   ./scripts/sync-agent-rules.sh          # пересобрать
#   ./scripts/sync-agent-rules.sh --check  # проверить актуальность (для CI)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

SRC="AGENTS.md"
HEADER="<!-- СГЕНЕРИРОВАНО из AGENTS.md скриптом scripts/sync-agent-rules.sh. Не редактировать. -->"
TARGETS=(
    "CLAUDE.md"
    ".github/copilot-instructions.md"
)

if [ ! -f "$SRC" ]; then
    echo "нет $SRC" >&2
    exit 1
fi

check_only=false
[ "${1:-}" = "--check" ] && check_only=true

status=0
for target in "${TARGETS[@]}"; do
    mkdir -p "$(dirname "$target")"
    tmp="$(mktemp)"
    {
        printf '%s\n\n' "$HEADER"
        cat "$SRC"
    } > "$tmp"

    if $check_only; then
        if ! cmp -s "$tmp" "$target"; then
            echo "устарел: $target — запустите ./scripts/sync-agent-rules.sh" >&2
            status=1
        fi
        rm -f "$tmp"
    else
        mv "$tmp" "$target"
        echo "обновлён: $target"
    fi
done

exit $status
