#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Grimoire Completion Contract — Git pre-commit hook
# ═══════════════════════════════════════════════════════════════════════════════
# Installé par grimoire-init.sh dans .git/hooks/pre-commit. Vérifie le CC sur les
# fichiers stagés. cc-verify.sh absent : dit « CC non installé » sur stderr (jamais
# muet), se replie sur `grimoire standard gate check` (indicatif), commit permis.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
CC_SCRIPT="$PROJECT_ROOT/_grimoire/kit/framework/cc-verify.sh"

# Outillage du venv projet prioritaire (pytest/ruff/mypy du projet, pas du système)
[[ -d "$PROJECT_ROOT/.venv/bin" ]] && export PATH="$PROJECT_ROOT/.venv/bin:$PATH"

if [[ ! -f "$CC_SCRIPT" ]]; then
    echo "⚠️  Grimoire : CC non installé (_grimoire/kit/framework/cc-verify.sh introuvable) — vérification pre-commit sautée. Synchroniser : grimoire up" >&2
    if [[ -f "$PROJECT_ROOT/_grimoire/standard/task-board.yaml" ]] && command -v grimoire >/dev/null 2>&1; then
        grimoire standard gate check "$PROJECT_ROOT" >&2 || true
    fi
    exit 0
fi

WATCHABLE="go|ts|tsx|py|tf|tfvars|sh|Dockerfile"
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
if ! echo "$STAGED" | grep -qE "\.(${WATCHABLE})$|^Dockerfile[^/]*$"; then
    exit 0  # Rien à vérifier (md, yaml config, images, etc.) — commit autorisé
fi

echo ""
echo "🔒 Grimoire Completion Contract — vérification pre-commit..."
echo "   Fichiers stagés détectés : $(echo "$STAGED" | grep -cE "\.(${WATCHABLE})$|^Dockerfile" || true) fichier(s) vérifiable(s)"
echo ""

# Lancer CC en mode --changed-only pour ne vérifier que le stack impacté
if bash "$CC_SCRIPT" --changed-only; then
    exit 0
fi
echo ""
echo "🚫 Commit bloqué — CC FAIL détecté."
echo "   Corrigez les erreurs ci-dessus puis relancez git commit."
echo "   Pour bypasser (DÉCONSEILLÉ) : git commit --no-verify"
echo ""
exit 1
