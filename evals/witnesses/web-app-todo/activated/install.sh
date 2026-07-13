#!/usr/bin/env bash
# Installe le mécanisme d'activation (bras « activated », ACTIVATION.md) dans
# une copie baseline déjà enrôlée. Voir README.md de ce dossier et
# RUN-PROTOCOL.md.
set -euo pipefail

RUN_DIR="${1:?usage: install.sh <run-dir>}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$RUN_DIR/_grimoire/standard" ]; then
  echo "erreur : $RUN_DIR n'est pas enrôlé dans le standard." >&2
  echo "Lancer d'abord : grimoire init . -a web-app -b local" >&2
  echo "puis : grimoire standard init . --needs solo-prototyping" >&2
  exit 1
fi

mkdir -p "$RUN_DIR/.claude/hooks"
cp "$HERE/claude-settings.json" "$RUN_DIR/.claude/settings.json"
cp "$HERE/activation-context.md" "$RUN_DIR/.claude/activation-context.md"
cp "$HERE/hooks/activation-session-start.sh" "$RUN_DIR/.claude/hooks/"
chmod +x "$RUN_DIR/.claude/hooks/activation-session-start.sh"

echo "Mécanisme d'activation installé dans $RUN_DIR"
echo "  $RUN_DIR/.claude/settings.json"
echo "  $RUN_DIR/.claude/activation-context.md"
echo "  $RUN_DIR/.claude/hooks/activation-session-start.sh"
