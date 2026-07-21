#!/usr/bin/env bash
# Bras « activated » — hook SessionStart (ACTIVATION.md, pré-enregistré 2026-07-09).
# Émet sur stdout le contenu de `.claude/activation-context.md`, injecté comme
# contexte au démarrage de la session (stdin JSON, stdout = additionalContext,
# exit 0). Le prompt de tâche reste strictement identique aux autres bras :
# seul le contexte de session diffère.
set -euo pipefail

# Consommer le stdin JSON sans en dépendre (session_id, source, etc.).
cat > /dev/null 2>&1 || true

cat "${CLAUDE_PROJECT_DIR:?CLAUDE_PROJECT_DIR non défini}/.claude/activation-context.md"
exit 0
