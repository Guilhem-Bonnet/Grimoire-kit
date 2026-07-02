#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Grimoire — Git pre-push hook
# ══════════════════════════════════════════════════════════════════════════════
#
# Déclenché avant git push.
# Lit remote/branch depuis stdin : <local_ref> <local_sha> <remote_ref> <remote_sha>
#
# Comportement :
#   1. Bloque les pushes directs vers les branches protegees si PR obligatoire
#   2. Vérifie la syntaxe bash des scripts Grimoire modifies
#   3. Lance quickcheck ou validate --all selon project-context.yaml
#   4. Skip si Grimoire_SKIP_PUSH_CHECK=1 (CI/CD ou urgence)
#
# Installation via : grimoire-init.sh hooks --install
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Bypass d'urgence
[[ "${Grimoire_SKIP_PUSH_CHECK:-0}" == "1" ]] && exit 0

GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
INIT_SCRIPT="$GIT_ROOT/grimoire-init.sh"
KIT_INIT_SCRIPT="$GIT_ROOT/grimoire-kit/grimoire-init.sh"
QUICKCHECK_SCRIPT="$GIT_ROOT/grimoire-kit/framework/tools/quick-check.sh"
PROJECT_CTX="$GIT_ROOT/project-context.yaml"

[[ -d "$GIT_ROOT/_grimoire" ]] || exit 0

if [[ ! -f "$INIT_SCRIPT" ]] && [[ -f "$KIT_INIT_SCRIPT" ]]; then
    INIT_SCRIPT="$KIT_INIT_SCRIPT"
fi

REQUIRE_PULL_REQUEST="true"
PROTECTED_BRANCHES_RAW="main"
PRE_PUSH_VALIDATION="quickcheck"
if [[ -f "$PROJECT_CTX" ]] && command -v python3 &>/dev/null; then
    mapfile -t Grimoire_CONFIG < <(python3 - "$PROJECT_CTX" <<'PYEOF' 2>/dev/null || true
import sys
try:
    values = {
        "require_pull_request": "true",
        "protected_branches": "main",
        "pre_push_validation": "quickcheck",
    }
    with open(sys.argv[1], encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.split("#", 1)[0].rstrip()
            if not line or line.startswith((" ", "\t", "-")):
                continue
            for key in values:
                prefix = f"{key}:"
                if line.startswith(prefix):
                    value = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if value.startswith("[") and value.endswith("]"):
                        value = value[1:-1]
                    if value:
                        values[key] = value
    print(values["require_pull_request"])
    print(values["protected_branches"])
    print(values["pre_push_validation"])
except Exception:
    pass
PYEOF
)
    REQUIRE_PULL_REQUEST="${Grimoire_CONFIG[0]:-true}"
    PROTECTED_BRANCHES_RAW="${Grimoire_CONFIG[1]:-main}"
    PRE_PUSH_VALIDATION="${Grimoire_CONFIG[2]:-quickcheck}"
fi

normalize_branch() {
    printf '%s' "$1" | sed "s/[\"' ]//g"
}

branch_is_protected() {
    local candidate="$1"
    shift
    local protected_branch
    for protected_branch in "$@"; do
        [[ -n "$protected_branch" ]] || continue
        if [[ "$candidate" == "$protected_branch" ]]; then
            return 0
        fi
    done
    return 1
}

PROTECTED_BRANCHES=()
while IFS= read -r branch; do
    branch="$(normalize_branch "$branch")"
    [[ -n "$branch" ]] && PROTECTED_BRANCHES+=("$branch")
done < <(printf '%s' "$PROTECTED_BRANCHES_RAW" | tr ',' '\n')

if [[ ${#PROTECTED_BRANCHES[@]} -eq 0 ]]; then
    PROTECTED_BRANCHES=("main")
fi

ERRORS=0

echo ""
echo "🚀 Grimoire pre-push checks..."

# ── 0. Blocage des pushes directs vers branches protegees ────────────────────
if [[ "$REQUIRE_PULL_REQUEST" == "true" ]]; then
    while read -r local_ref local_sha remote_ref remote_sha; do
        [[ -n "${remote_ref:-}" ]] || continue
        [[ "$remote_ref" == refs/heads/* ]] || continue
        remote_branch="${remote_ref#refs/heads/}"
        if branch_is_protected "$remote_branch" "${PROTECTED_BRANCHES[@]}"; then
            echo "   ✗ push direct vers '$remote_branch' interdit"
            echo "     Ouvrir une branche de travail puis une pull request pour fusionner."
            echo "     Bypass d'urgence: Grimoire_SKIP_PUSH_CHECK=1 git push"
            echo ""
            exit 1
        fi
    done
fi

# ── 1. Syntaxe bash sur les scripts Grimoire modifies ────────────────────────
for shell_target in "$GIT_ROOT/grimoire-init.sh" "$KIT_INIT_SCRIPT"; do
    [[ -f "$shell_target" ]] || continue
    rel_target="${shell_target#$GIT_ROOT/}"
    MODIFIED=$(git diff --name-only origin/HEAD HEAD 2>/dev/null | grep -F "$rel_target" || true)
    if [[ -n "$MODIFIED" ]] || git diff --cached --name-only 2>/dev/null | grep -qF "$rel_target"; then
        if bash -n "$shell_target" 2>&1; then
            echo "   ✓ $rel_target — syntaxe bash OK"
        else
            echo "   ✗ $rel_target — ERREUR de syntaxe bash !"
            ERRORS=$((ERRORS + 1))
        fi
    fi
done

# ── 2. Validation locale configurable ────────────────────────────────────────
case "$PRE_PUSH_VALIDATION" in
    none)
        echo "   • validation locale skip (pre_push_validation=none)"
        ;;
    quickcheck)
        if [[ -x "$QUICKCHECK_SCRIPT" ]] && [[ -d "$GIT_ROOT/grimoire-kit" ]]; then
            if (cd "$GIT_ROOT/grimoire-kit" && "$QUICKCHECK_SCRIPT"); then
                echo "   ✓ quickcheck local OK"
            else
                echo "   ✗ quickcheck local en echec"
                ERRORS=$((ERRORS + 1))
            fi
        else
            echo "   ✗ quickcheck indisponible (script manquant)"
            ERRORS=$((ERRORS + 1))
        fi
        ;;
    validate)
        DNA_FILES=$(find "$GIT_ROOT/archetypes" -name "*.dna.yaml" 2>/dev/null | wc -l)
        if [[ "$DNA_FILES" -gt 0 ]] && [[ -f "$INIT_SCRIPT" ]] && command -v python3 &>/dev/null; then
            VALIDATE_RESULT=$(bash "$INIT_SCRIPT" validate --all 2>&1 | tail -3)
            if echo "$VALIDATE_RESULT" | grep -qi "erreur\|error\|invalid"; then
                echo "   ✗ DNA validate --all — erreurs detectees !"
                echo "     $VALIDATE_RESULT"
                ERRORS=$((ERRORS + 1))
            else
                echo "   ✓ DNA validate — $DNA_FILES fichiers OK"
            fi
        fi
        ;;
    *)
        echo "   ✗ valeur inconnue pour pre_push_validation: $PRE_PUSH_VALIDATION"
        ERRORS=$((ERRORS + 1))
        ;;
esac

# ── 3. Résumé ─────────────────────────────────────────────────────────────────
if [[ $ERRORS -eq 0 ]]; then
    echo "   ✅ Tous les checks Grimoire passent — push autorisé"
    echo ""
    exit 0
else
    echo ""
    echo "   🚫 $ERRORS check(s) échoué(s) — push bloqué"
    echo "   Pour bypasser : Grimoire_SKIP_PUSH_CHECK=1 git push"
    echo ""
    exit 1
fi
