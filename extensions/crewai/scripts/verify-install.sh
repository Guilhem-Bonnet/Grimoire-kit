#!/usr/bin/env bash
# Vérification post-installation de l'extension crewai.

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

check_file ".github/agents/crewai-crew-runner.agent.md"
check_file ".github/prompts/crewai-import-crew.prompt.md"
check_file ".github/skills/crewai-crew-design/SKILL.md"
check_file ".github/hooks/crewai-telemetry-bridge.json"
check_file ".github/hooks/scripts/crewai-telemetry-bridge.sh"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python3" ]] && \
   "${PROJECT_ROOT}/.venv/bin/python3" -c "import crewai" 2>/dev/null; then
    echo "OK      : paquet crewai importable"
else
    echo "INFO    : paquet crewai non importable (pip ignoré ou .venv absent)"
fi

exit "$STATUS"
