#!/usr/bin/env bash
# crewai-telemetry-bridge — hook PostToolUse (mode shadow)
#
# Détecte les traces d'exécution CrewAI dans les sorties d'outils et les
# journalise dans le flux télémétrie du projet pour le replay blueprint.
# Non bloquant : sort toujours en 0.

set -euo pipefail

INPUT="$(cat)"
OUTPUT_DIR="${GRIMOIRE_PROJECT_ROOT:-.}/_grimoire-runtime-output/hook-runtime"
OUTPUT_FILE="${OUTPUT_DIR}/crewai-traces.jsonl"

if echo "$INPUT" | grep -q "crewai"; then
    mkdir -p "$OUTPUT_DIR"
    printf '{"source":"crewai-telemetry-bridge","at":"%s","event":%s}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$(echo "$INPUT" | head -c 4000 | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
        >> "$OUTPUT_FILE"
fi

exit 0
