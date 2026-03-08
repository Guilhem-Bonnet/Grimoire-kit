#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Grimoire — Git commit-msg hook
# ══════════════════════════════════════════════════════════════════════════════
#
# Déclenché après saisie du message de commit.
# Paramètre : $1 = fichier contenant le message de commit
#
# Comportement :
#   - Valide le format Conventional Commits (optionnel, configurable)
#   - Bloque : messages trop courts (<10 chars hors commentaires)
#   - Warn  : pas de type CC mais message valide → avertissement non-bloquant
#   - Le mode strict s'active avec Grimoire_CC_STRICT=1 (défaut: souple)
#
# Configuration dans project-context.yaml :
#   commit_convention: conventional  # ou: free (défaut)
#   commit_min_length: 10
#
# Installation via : grimoire-init.sh hooks --install
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

COMMIT_MSG_FILE="$1"
GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

[[ -d "$GIT_ROOT/_grimoire" ]] || exit 0

# Lire le message (sans les commentaires)
MSG=$(grep -v '^#' "$COMMIT_MSG_FILE" | sed '/^[[:space:]]*$/d' | head -1)

# ── Vérification longueur minimale ───────────────────────────────────────────
MIN_LEN=10
if [[ ${#MSG} -lt $MIN_LEN ]]; then
    echo ""
    echo "🚫 Grimoire commit-msg : message trop court (${#MSG} chars, min ${MIN_LEN})"
    echo "   Message : \"$MSG\""
    echo ""
    exit 1
fi

# ── Lire config projet ───────────────────────────────────────────────────────
PROJECT_CTX="$GIT_ROOT/project-context.yaml"
CONVENTION="free"
if [[ -f "$PROJECT_CTX" ]] && command -v python3 &>/dev/null; then
    CONVENTION=$(python3 - "$PROJECT_CTX" <<'PYEOF' 2>/dev/null || echo "free"
import sys
try:
    # Parse YAML minimal sans dépendance
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if line.startswith("commit_convention:"):
                val = line.split(":", 1)[1].strip().strip('"').strip("'")
                print(val)
                sys.exit(0)
    print("free")
except Exception:
    print("free")
PYEOF
)
fi

# ── Validation Conventional Commits (mode strict) ────────────────────────────
CC_TYPES="feat|fix|docs|style|refactor|perf|test|chore|ci|build|revert"
CC_PATTERN="^(${CC_TYPES})(\([a-zA-Z0-9_-]+\))?(!)?: .{1,}"

if [[ "$CONVENTION" == "conventional" ]] || [[ "${Grimoire_CC_STRICT:-0}" == "1" ]]; then
    if ! echo "$MSG" | grep -qE "$CC_PATTERN"; then
        echo ""
        echo "🚫 Grimoire commit-msg : format Conventional Commits requis"
        echo "   Message : \"$MSG\""
        echo "   Format  : <type>(<scope>): <description>"
        echo "   Types   : feat|fix|docs|style|refactor|perf|test|chore|ci|build|revert"
        echo "   Exemple : feat(stack): ajouter DNA go-expert"
        echo ""
        echo "   Pour désactiver : définir commit_convention: free dans project-context.yaml"
        echo ""
        exit 1
    fi
else
    # Mode souple : juste un avertissement
    if ! echo "$MSG" | grep -qE "$CC_PATTERN"; then
        echo "💡 Grimoire: message hors format CC — pensez à prefixer avec feat:/fix:/chore: etc."
    fi
fi

exit 0
