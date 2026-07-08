#!/usr/bin/env bash
# stigmergy-sense.sh — hook stigmergique (mode shadow, non bloquant).
# Fail-open : toute erreur ⇒ {} et code 0.
set -uo pipefail
input=$(cat 2>/dev/null || true)
here="$(cd "$(dirname "$0")" && pwd)"
py="$(command -v python3 || true)"
if [[ -z "$py" || ! -f "$here/stigmergy_hook.py" ]]; then
  echo "{}"; exit 0
fi
printf '%s' "$input" | "$py" "$here/stigmergy_hook.py" sense 2>/dev/null || echo "{}"
exit 0
