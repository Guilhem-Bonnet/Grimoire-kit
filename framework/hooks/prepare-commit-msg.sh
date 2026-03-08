#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Grimoire — Git prepare-commit-msg hook
# ══════════════════════════════════════════════════════════════════════════════
#
# Déclenché avant l'ouverture de l'éditeur de commit.
# Paramètres git : $1=fichier msg, $2=source (message|template|merge|squash|commit)
#
# Comportement :
#   - Injecte en bas du message (section commentaires) :
#     → Agent actif depuis state.json (ex: "Amelia [dev]")
#     → Checkpoint_id court actif (ex: "ckpt:a3f2b1c4")
#     → Branche courante si session-branch Grimoire
#   - Ne modifie jamais les rebase/squash/merge automatiques
#
# Installation via : grimoire-init.sh hooks --install
# ══════════════════════════════════════════════════════════════════════════════

set -uo pipefail  # pas -e : enrichissement du message, ne doit pas bloquer

COMMIT_MSG_FILE="$1"
COMMIT_SOURCE="${2:-}"

# Ne pas toucher aux merges, squash, rebase, ammend automatiques
case "$COMMIT_SOURCE" in
    merge|squash) exit 0 ;;
esac

GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
MEMORY_DIR="$GIT_ROOT/_grimoire/_memory"
STATE_FILE="$MEMORY_DIR/state.json"

[[ -d "$GIT_ROOT/_grimoire" ]] || exit 0

# ── Lire l'état Grimoire ─────────────────────────────────────────────────────────
Grimoire_CONTEXT=""

if [[ -f "$STATE_FILE" ]] && command -v python3 &>/dev/null; then
    Grimoire_CONTEXT=$(python3 - "$STATE_FILE" <<'PYEOF'
import json, sys

try:
    with open(sys.argv[1]) as f:
        state = json.load(f)

    parts = []

    # Agent actif
    active_agent = state.get("active_agent", "")
    if active_agent:
        parts.append(f"agent:{active_agent}")

    # Dernier checkpoint
    checkpoints = state.get("checkpoints", [])
    if checkpoints:
        ckpt_id = checkpoints[-1].get("checkpoint_id", "")[:8]
        if ckpt_id:
            parts.append(f"ckpt:{ckpt_id}")

    # Story courante
    current_story = state.get("current_story", "")
    if current_story:
        parts.append(f"story:{current_story}")

    if parts:
        print(" | ".join(parts))
except Exception:
    pass
PYEOF
)
fi

# ── Ajouter le contexte Grimoire en commentaire ───────────────────────────────────
if [[ -n "$Grimoire_CONTEXT" ]]; then
    # Ajouter après les lignes existantes, avant les commentaires git
    {
        cat "$COMMIT_MSG_FILE"
        echo ""
        echo "# Grimoire: $Grimoire_CONTEXT"
    } > "${COMMIT_MSG_FILE}.tmp" && mv "${COMMIT_MSG_FILE}.tmp" "$COMMIT_MSG_FILE"
fi

exit 0
