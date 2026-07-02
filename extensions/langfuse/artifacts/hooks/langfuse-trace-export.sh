#!/usr/bin/env bash
# langfuse-trace-export — hook PostToolUse (mode shadow)
#
# Export best-effort de la télémétrie vers Langfuse. Sans LANGFUSE_HOST,
# journalise localement dans la file d'attente d'export. Ne bloque jamais :
# sort toujours en 0.

set -euo pipefail

INPUT="$(cat)"
OUTPUT_DIR="${GRIMOIRE_PROJECT_ROOT:-.}/_grimoire-runtime-output/hook-runtime"
QUEUE_FILE="${OUTPUT_DIR}/langfuse-export-queue.jsonl"

mkdir -p "$OUTPUT_DIR"
printf '{"at":"%s","exported":%s,"event":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$([ -n "${LANGFUSE_HOST:-}" ] && echo true || echo false)" \
    "$(echo "$INPUT" | head -c 4000 | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
    >> "$QUEUE_FILE" 2>/dev/null || true

if [ -n "${LANGFUSE_HOST:-}" ] && [ -n "${LANGFUSE_PUBLIC_KEY:-}" ]; then
    python3 - <<'EOF' 2>/dev/null || true
try:
    from langfuse import Langfuse
    Langfuse().flush()
except Exception:
    pass
EOF
fi

exit 0
