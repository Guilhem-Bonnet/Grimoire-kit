#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Grimoire — Git post-commit hook
# ══════════════════════════════════════════════════════════════════════════════
#
# Déclenché APRÈS chaque commit réussi.
# Ne reçoit pas de paramètres.
#
# Comportement :
#   1. Append une entrée [GIT-COMMIT] dans Grimoire_TRACE.md avec :
#      - Hash court du commit
#      - Message du commit
#      - Fichiers modifiés (liste)
#      - Agent actif (depuis state.json)
#      - Branche courante
#   2. Ne bloque jamais (exit 0 garanti)
#
# Installation via : grimoire-init.sh hooks --install
# ══════════════════════════════════════════════════════════════════════════════

set -uo pipefail  # pas -e : hook post-commit ne doit jamais bloquer

GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
Grimoire_DIR="$GIT_ROOT/_grimoire"

[[ -d "$Grimoire_DIR" ]] || exit 0

TRACE_FILE="$GIT_ROOT/_grimoire-output/Grimoire_TRACE.md"
STATE_FILE="$Grimoire_DIR/_memory/state.json"

# Informations du commit
COMMIT_HASH="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
COMMIT_MSG="$(git log -1 --pretty=%s 2>/dev/null || echo "")"
BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "detached")"
# TIMESTAMP et DATE disponibles via $(date) inline dans TRACE_ENTRY

# Fichiers modifiés dans le commit
CHANGED_FILES="$(git diff-tree --no-commit-id -r --name-only HEAD 2>/dev/null | head -20 | tr '\n' ', ' | sed 's/,$//')"

# Agent actif
ACTIVE_AGENT=""
if [[ -f "$STATE_FILE" ]] && command -v python3 &>/dev/null; then
    ACTIVE_AGENT=$(python3 - <<PYEOF 2>/dev/null || true
import json
try:
    with open("$STATE_FILE") as f:
        state = json.load(f)
    print(state.get("active_agent", ""))
except Exception:
    pass
PYEOF
)
fi

# Construire l'entrée TRACE
AGENT_TAG="${ACTIVE_AGENT:-system}"
TRACE_ENTRY="
## $(date -u +"%Y-%m-%d %H:%M") | git-commit | $AGENT_TAG

[GIT-COMMIT] hash:$COMMIT_HASH branch:$BRANCH
**Message :** $COMMIT_MSG
**Fichiers :** $CHANGED_FILES
"

# Créer le répertoire si nécessaire
mkdir -p "$(dirname "$TRACE_FILE")"

# Initialiser le fichier s'il n'existe pas
if [[ ! -f "$TRACE_FILE" ]]; then
    echo "# Grimoire_TRACE — Audit Trail" > "$TRACE_FILE"
    echo "" >> "$TRACE_FILE"
    echo "> Généré automatiquement — ne pas éditer manuellement" >> "$TRACE_FILE"
    echo "" >> "$TRACE_FILE"
fi

# Append l'entrée
echo "$TRACE_ENTRY" >> "$TRACE_FILE"

# ── Dream auto-trigger ───────────────────────────────────────────────────────
# Lancer un dream --quick --emit --since auto quand des fichiers mémoire changent.
# Le compteur ne s'incrémente que si le commit touche _grimoire/_memory/ ou _grimoire-output/
# → pas de dream inutile sur un commit qui ne change que du code source.
DREAM_INTERVAL="${Grimoire_DREAM_INTERVAL:-10}"
DREAM_COUNTER_FILE="$Grimoire_DIR/_memory/dream-trigger-count"
DREAM_SCRIPT="$GIT_ROOT/framework/tools/dream.py"

if [[ -f "$DREAM_SCRIPT" ]] && command -v python3 &>/dev/null; then
    # Smart trigger : ne compter que les commits qui touchent la mémoire
    MEMORY_CHANGED=$(git diff-tree --no-commit-id -r --name-only HEAD 2>/dev/null \
        | grep -cE '^_grimoire/(_memory|_config)|^_grimoire-output/' || true)

    if [[ "$MEMORY_CHANGED" -gt 0 ]]; then
        # Lire ou initialiser le compteur
        DREAM_COUNT=0
        if [[ -f "$DREAM_COUNTER_FILE" ]]; then
            DREAM_COUNT=$(cat "$DREAM_COUNTER_FILE" 2>/dev/null || echo "0")
            DREAM_COUNT=$(( DREAM_COUNT + 0 )) 2>/dev/null || DREAM_COUNT=0
        fi

        DREAM_COUNT=$((DREAM_COUNT + 1))

        if [[ "$DREAM_COUNT" -ge "$DREAM_INTERVAL" ]]; then
            # Reset le compteur
            echo "0" > "$DREAM_COUNTER_FILE"
            # Lancer le dream incrémental en background (ne bloque pas le commit)
            (
                python3 "$DREAM_SCRIPT" \
                    --project-root "$GIT_ROOT" \
                    --quick --emit --since auto 2>/dev/null
            ) &
            echo "   🌙 Dream auto-trigger (${DREAM_COUNT} memory commits)"
        else
            echo "$DREAM_COUNT" > "$DREAM_COUNTER_FILE"
        fi
    fi
fi

exit 0
