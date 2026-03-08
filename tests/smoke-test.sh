#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# BMAD Custom Kit — Smoke Tests
# ═══════════════════════════════════════════════════════════════════════════════
#
# Teste grimoire-init.sh dans un tmpdir vierge pour vérifier que l'installation
# produit la structure attendue et que les sous-commandes fonctionnent.
#
# Usage:
#   bash tests/smoke-test.sh                    # tous les scénarios
#   bash tests/smoke-test.sh --scenario minimal # un seul scénario
#
# Exit: 0 = tout OK, 1 = au moins un échec
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KIT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BMAD_INIT="$KIT_DIR/grimoire-init.sh"
PASSED=0
FAILED=0
TOTAL=0
SCENARIO_FILTER="${1:-}"

# ─── Couleurs ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

# ─── Helpers ─────────────────────────────────────────────────────────────────
pass() { PASSED=$((PASSED + 1)); TOTAL=$((TOTAL + 1)); echo -e "  ${GREEN}✅ $*${NC}"; }
fail() { FAILED=$((FAILED + 1)); TOTAL=$((TOTAL + 1)); echo -e "  ${RED}❌ $*${NC}"; }

assert_file_exists() {
    local path="$1"
    local label="${2:-$path}"
    if [[ -f "$path" ]]; then
        pass "$label"
    else
        fail "$label — fichier manquant: $path"
    fi
}

assert_dir_exists() {
    local path="$1"
    local label="${2:-$path}"
    if [[ -d "$path" ]]; then
        pass "$label"
    else
        fail "$label — dossier manquant: $path"
    fi
}

assert_file_contains() {
    local path="$1"
    local pattern="$2"
    local label="${3:-$path contient '$pattern'}"
    if grep -q "$pattern" "$path" 2>/dev/null; then
        pass "$label"
    else
        fail "$label — pattern '$pattern' absent de $path"
    fi
}

assert_file_not_empty() {
    local path="$1"
    local label="${2:-$path non vide}"
    if [[ -s "$path" ]]; then
        pass "$label"
    else
        fail "$label — fichier vide: $path"
    fi
}

assert_exit_code() {
    local expected="$1"
    shift
    local label="$1"
    shift
    local actual
    set +e
    "$@" >/dev/null 2>&1
    actual=$?
    set -e
    if [[ "$actual" == "$expected" ]]; then
        pass "$label (exit $actual)"
    else
        fail "$label — attendu exit $expected, obtenu exit $actual"
    fi
}

# ─── Setup tmpdir ────────────────────────────────────────────────────────────
setup_tmpdir() {
    local name="$1"
    TMPDIR_TEST="$(mktemp -d "/tmp/grimoire-smoke-${name}-XXXXXX")"
    # Initialiser un repo git (nécessaire pour les hooks)
    git init -q "$TMPDIR_TEST"
    cd "$TMPDIR_TEST"
}

cleanup_tmpdir() {
    cd "$KIT_DIR"
    [[ -n "${TMPDIR_TEST:-}" ]] && rm -rf "$TMPDIR_TEST"
}

# ═══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 1 : Installation minimale
# ═══════════════════════════════════════════════════════════════════════════════
scenario_minimal() {
    echo ""
    echo -e "${CYAN}━━━ Scénario 1 : Installation minimale ━━━${NC}"
    setup_tmpdir "minimal"

    # Exécuter l'init
    bash "$BMAD_INIT" --name "TestSmoke" --user "CI" --archetype minimal --force 2>&1 | tail -5

    # Structure de base
    assert_dir_exists  "_bmad/_config/custom/agents"       "Dossier agents custom"
    assert_dir_exists  "_bmad/_config/custom/workflows"    "Dossier workflows"
    assert_dir_exists  "_bmad/_memory"                     "Dossier memory"
    assert_dir_exists  "_bmad/_memory/agent-learnings"     "Dossier agent-learnings"
    assert_dir_exists  "_bmad-output/.runs/main"           "Session main"

    # Framework files
    assert_file_exists "_bmad/_config/custom/agent-base.md"    "agent-base.md"
    assert_file_exists "_bmad/_config/custom/cc-verify.sh"     "cc-verify.sh"
    assert_file_exists "_bmad/_config/custom/sil-collect.sh"   "sil-collect.sh"
    assert_file_exists "_bmad/_memory/maintenance.py"          "maintenance.py"
    assert_file_exists "_bmad/_memory/mem0-bridge.py"          "mem0-bridge.py"
    assert_file_exists "_bmad/_memory/session-save.py"         "session-save.py"
    assert_file_exists "_bmad/_memory/backends/__init__.py"    "backends/__init__.py"

    # Config files
    assert_file_exists "project-context.yaml"                  "project-context.yaml"
    assert_file_exists "_bmad/_memory/config.yaml"             "memory config.yaml"
    assert_file_exists "_bmad/_memory/shared-context.md"       "shared-context.md"
    assert_file_exists "_bmad/_memory/decisions-log.md"        "decisions-log.md"
    assert_file_exists "_bmad/_memory/failure-museum.md"       "failure-museum.md"
    assert_file_exists "_bmad/_memory/memories.json"           "memories.json"
    assert_file_exists "_bmad/_config/agent-manifest.csv"      "agent-manifest.csv"

    # Meta agents (toujours inclus)
    assert_file_exists "_bmad/_config/custom/agents/project-navigator.md"  "Atlas (meta)"
    assert_file_exists "_bmad/_config/custom/agents/agent-optimizer.md"    "Sentinel (meta)"
    assert_file_exists "_bmad/_config/custom/agents/memory-keeper.md"      "Mnemo (meta)"

    # Minimal template
    assert_file_exists "_bmad/_config/custom/agents/custom-agent.tpl.md"   "custom-agent template"

    # project-context.yaml contenu
    assert_file_contains "project-context.yaml" "TestSmoke"               "project-context contient le nom"
    assert_file_contains "project-context.yaml" "bmad_kit_version"        "project-context contient la version du kit"

    # Session branching
    assert_file_exists "_bmad-output/.runs/main/branch.json"  "branch.json main"

    # Manifest
    assert_file_contains "_bmad/_config/agent-manifest.csv" "project-navigator"  "manifest contient atlas"

    # Copilot instructions
    assert_file_exists ".github/copilot-instructions.md"                       "copilot-instructions.md"
    assert_file_contains ".github/copilot-instructions.md" "TestSmoke"         "copilot-instructions contient le nom"
    assert_file_contains ".github/copilot-instructions.md" "project-navigator" "copilot-instructions contient atlas"

    cleanup_tmpdir
}

# ═══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 2 : Installation infra-ops
# ═══════════════════════════════════════════════════════════════════════════════
scenario_infra_ops() {
    echo ""
    echo -e "${CYAN}━━━ Scénario 2 : Installation infra-ops ━━━${NC}"
    setup_tmpdir "infra-ops"

    bash "$BMAD_INIT" --name "InfraTest" --user "CI" --archetype infra-ops --force 2>&1 | tail -3

    # Agents infra-ops
    assert_file_exists "_bmad/_config/custom/agents/ops-engineer.md"          "ops-engineer"
    assert_file_exists "_bmad/_config/custom/agents/systems-debugger.md"      "systems-debugger"
    assert_file_exists "_bmad/_config/custom/agents/security-hardener.md"     "security-hardener"
    assert_file_exists "_bmad/_config/custom/agents/k8s-navigator.md"         "k8s-navigator"
    assert_file_exists "_bmad/_config/custom/agents/monitoring-specialist.md"  "monitoring-specialist"
    assert_file_exists "_bmad/_config/custom/agents/pipeline-architect.md"    "pipeline-architect"
    assert_file_exists "_bmad/_config/custom/agents/backup-dr-specialist.md"  "backup-dr-specialist"

    # Meta toujours présents
    assert_file_exists "_bmad/_config/custom/agents/project-navigator.md"    "Atlas (meta) avec infra-ops"

    # Prompt templates
    assert_dir_exists  "_bmad/_config/custom/prompt-templates"               "prompt-templates copiés"
    assert_file_exists "_bmad/_config/custom/prompt-templates/troubleshoot.md" "troubleshoot template"

    cleanup_tmpdir
}

# ═══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 3 : Sous-commandes (doctor, validate, guard)
# ═══════════════════════════════════════════════════════════════════════════════
scenario_subcommands() {
    echo ""
    echo -e "${CYAN}━━━ Scénario 3 : Sous-commandes ━━━${NC}"
    setup_tmpdir "subcmd"

    # Installer d'abord
    bash "$BMAD_INIT" --name "CmdTest" --user "CI" --archetype minimal --force >/dev/null 2>&1

    # --version
    local ver
    ver="$(bash "$BMAD_INIT" --version 2>&1)"
    if [[ "$ver" =~ ^BMAD\ Custom\ Kit\ v ]]; then
        pass "--version retourne une version"
    else
        fail "--version output: '$ver'"
    fi

    # doctor (doit exit 0 dans un projet initialisé)
    assert_exit_code 0 "doctor exit 0" bash "$BMAD_INIT" doctor

    # validate —all (doit exit 0 — rien à valider dans minimal mais pas d'erreur)
    assert_exit_code 0 "validate --all exit 0" bash "$BMAD_INIT" validate --all

    # guard (doit exit 0 car agents existent)
    assert_exit_code 0 "guard exit 0" bash "$BMAD_INIT" guard

    # hooks --status (doit exit 0)
    assert_exit_code 0 "hooks --status exit 0" bash "$BMAD_INIT" hooks --status

    # session-branch --list (doit exit 0)
    assert_exit_code 0 "session-branch --list exit 0" bash "$BMAD_INIT" session-branch --list

    # install --list (doit exit 0)
    assert_exit_code 0 "install --list exit 0" bash "$BMAD_INIT" install --list

    # upgrade (doit exit 0 — même version = OK)
    assert_exit_code 0 "upgrade exit 0" bash "$BMAD_INIT" upgrade

    # upgrade --dry-run --force (doit exit 0)
    assert_exit_code 0 "upgrade --dry-run --force exit 0" bash "$BMAD_INIT" upgrade --dry-run --force

    cleanup_tmpdir
}

# ═══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 4 : Install incrémental (install --archetype stack/go)
# ═══════════════════════════════════════════════════════════════════════════════
scenario_incremental_install() {
    echo ""
    echo -e "${CYAN}━━━ Scénario 4 : Install incrémental stack/go ━━━${NC}"
    setup_tmpdir "incremental"

    # D'abord init minimal
    bash "$BMAD_INIT" --name "IncrTest" --user "CI" --archetype minimal --force >/dev/null 2>&1

    # Puis installer un agent stack
    bash "$BMAD_INIT" install --archetype stack/go 2>&1 | tail -3

    assert_file_exists "_bmad/_config/custom/agents/go-expert.md"    "go-expert installé"

    # Installer un second
    bash "$BMAD_INIT" install --archetype stack/docker 2>&1 | tail -3

    assert_file_exists "_bmad/_config/custom/agents/docker-expert.md" "docker-expert installé"

    # Verify they coexist
    local count
    count=$(ls _bmad/_config/custom/agents/*.md 2>/dev/null | wc -l)
    if [[ "$count" -ge 6 ]]; then  # 3 meta + 1 minimal + 2 stack
        pass "Au moins 6 agents après install incrémental ($count trouvés)"
    else
        fail "Attendu >= 6 agents, trouvé $count"
    fi

    cleanup_tmpdir
}

# ═══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 5 : Idempotence (double init avec --force)
# ═══════════════════════════════════════════════════════════════════════════════
scenario_idempotence() {
    echo ""
    echo -e "${CYAN}━━━ Scénario 5 : Idempotence (double --force) ━━━${NC}"
    setup_tmpdir "idempotent"

    # Première init
    bash "$BMAD_INIT" --name "IdempTest" --user "CI" --archetype infra-ops --force >/dev/null 2>&1
    local count1
    count1=$(ls _bmad/_config/custom/agents/*.md 2>/dev/null | wc -l)

    # Seconde init identique
    bash "$BMAD_INIT" --name "IdempTest" --user "CI" --archetype infra-ops --force >/dev/null 2>&1
    local count2
    count2=$(ls _bmad/_config/custom/agents/*.md 2>/dev/null | wc -l)

    if [[ "$count1" -eq "$count2" ]]; then
        pass "Nombre d'agents stable après double init ($count1 = $count2)"
    else
        fail "Nombre d'agents changé: $count1 → $count2"
    fi

    # Le project-context.yaml ne doit PAS être écrasé
    assert_file_contains "project-context.yaml" "IdempTest" "project-context préservé après re-init"

    cleanup_tmpdir
}

# ═══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 6 : Python tools syntax
# ═══════════════════════════════════════════════════════════════════════════════
scenario_python_tools() {
    echo ""
    echo -e "${CYAN}━━━ Scénario 6 : Syntaxe Python & Bash ━━━${NC}"

    # Python compile check
    for py in "$KIT_DIR"/framework/tools/*.py "$KIT_DIR"/framework/memory/*.py; do
        [[ -f "$py" ]] || continue
        local name
        name="$(basename "$py")"
        if python3 -c "import py_compile; py_compile.compile('$py', doraise=True)" 2>/dev/null; then
            pass "Python compile: $name"
        else
            fail "Python compile: $name"
        fi
    done

    # Bash syntax check
    for sh in "$KIT_DIR"/grimoire-init.sh "$KIT_DIR"/framework/hooks/*.sh "$KIT_DIR"/framework/cc-verify.sh "$KIT_DIR"/framework/sil-collect.sh; do
        [[ -f "$sh" ]] || continue
        local name
        name="$(basename "$sh")"
        if bash -n "$sh" 2>/dev/null; then
            pass "Bash syntax: $name"
        else
            fail "Bash syntax: $name"
        fi
    done

    # YAML syntax check
    for yaml in "$KIT_DIR"/archetypes/*/archetype.dna.yaml "$KIT_DIR"/framework/*.schema.yaml; do
        [[ -f "$yaml" ]] || continue
        local name
        name="$(basename "$(dirname "$yaml")")/$(basename "$yaml")"
        if python3 -c "import yaml; yaml.safe_load(open('$yaml'))" 2>/dev/null; then
            pass "YAML syntax: $name"
        else
            fail "YAML syntax: $name"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  BMAD Custom Kit — Smoke Tests v$(cat "$KIT_DIR/version.txt")${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"

if [[ "$SCENARIO_FILTER" == "--scenario" ]]; then
    scenario="${2:-}"
    [[ -z "$scenario" ]] && { echo "Usage: $0 --scenario <name>"; exit 1; }
    "scenario_${scenario}"
else
    scenario_minimal
    scenario_infra_ops
    scenario_subcommands
    scenario_incremental_install
    scenario_idempotence
    scenario_python_tools
fi

# ─── Résumé ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [[ "$FAILED" -eq 0 ]]; then
    echo -e "${GREEN}  ✅ Tous les tests passent : ${PASSED}/${TOTAL}${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit 0
else
    echo -e "${RED}  ❌ ${FAILED} échec(s) sur ${TOTAL} tests${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit 1
fi
