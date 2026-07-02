#!/usr/bin/env bash
# Vérification post-installation de l'extension autogen.

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

check_file ".github/agents/autogen-conversation-runner.agent.md"
check_file ".github/skills/autogen-conversation-design/SKILL.md"

exit "$STATUS"
