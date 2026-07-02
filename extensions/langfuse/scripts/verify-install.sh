#!/usr/bin/env bash
# Vérification post-installation de l'extension langfuse.

set -euo pipefail

PROJECT_ROOT="${GRIMOIRE_PROJECT_ROOT:?GRIMOIRE_PROJECT_ROOT requis}"
STATUS=0

check_file() {
    if [[ -e "${PROJECT_ROOT}/$1" ]]; then
        echo "OK      : $1"
    else
        echo "MANQUANT: $1" >&2
        STATUS=1
    fi
}

check_file ".github/skills/langfuse-tracing-setup/SKILL.md"
check_file ".github/instructions/langfuse-observability.instructions.md"
check_file ".github/hooks/langfuse-trace-export.json"
check_file ".github/hooks/scripts/langfuse-trace-export.sh"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python3" ]] && \
   "${PROJECT_ROOT}/.venv/bin/python3" -c "import langfuse" 2>/dev/null; then
    echo "OK      : paquet langfuse importable"
else
    echo "INFO    : paquet langfuse non importable (pip ignoré ou .venv absent)"
fi

exit "$STATUS"
