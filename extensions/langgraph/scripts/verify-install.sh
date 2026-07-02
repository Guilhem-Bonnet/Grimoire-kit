#!/usr/bin/env bash
# Vérification post-installation de l'extension langgraph.

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

check_file ".github/agents/langgraph-graph-runner.agent.md"
check_file ".github/prompts/langgraph-import-graph.prompt.md"
check_file ".github/skills/langgraph-graph-design/SKILL.md"

if [[ -x "${PROJECT_ROOT}/.venv/bin/python3" ]] && \
   "${PROJECT_ROOT}/.venv/bin/python3" -c "import langgraph" 2>/dev/null; then
    echo "OK      : paquet langgraph importable"
else
    echo "INFO    : paquet langgraph non importable (pip ignoré ou .venv absent)"
fi

exit "$STATUS"
