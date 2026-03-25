#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# grimoire — CLI unifiée pour le Grimoire Kit Grimoire
# ═══════════════════════════════════════════════════════════════════════════════
#
# Point d'entrée unique pour toutes les commandes du framework.
#
# Usage:
#   grimoire <command> [options]
#   grimoire doctor            # Diagnostic complet
#   grimoire status            # État du projet
#   grimoire tools [--tier]    # Liste des outils
#   grimoire lifecycle pre     # Hooks pré-session
#   grimoire lifecycle post    # Hooks post-session
#   grimoire integrity check   # Vérification intégrité agents
#   grimoire init [...]        # Initialiser un projet (proxy grimoire-init.sh)
#   grimoire help              # Aide
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Version ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(cat "${SCRIPT_DIR}/version.txt" 2>/dev/null || echo "dev")"

# ─── Couleurs ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ─── Détection racine ────────────────────────────────────────────────────────
find_project_root() {
    local dir="$PWD"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/project-context.yaml" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    echo "$PWD"
}

PROJECT_ROOT="$(find_project_root)"
TOOLS_DIR="${SCRIPT_DIR}/framework/tools"
MEMORY_DIR="${SCRIPT_DIR}/framework/memory"

# ─── Commandes ────────────────────────────────────────────────────────────────

cmd_help() {
    cat <<EOF
${CYAN}${BOLD}grimoire${NC} v${VERSION} — CLI du Grimoire Kit Grimoire

${BOLD}Commandes:${NC}
  ${GREEN}doctor${NC}           Diagnostic complet du projet
  ${GREEN}status${NC}           État rapide (mémoire, outils, intégrité)
  ${GREEN}tools${NC}            Liste des outils disponibles
  ${GREEN}lifecycle${NC} <pre|post|status>  Hooks de session
  ${GREEN}integrity${NC} <snapshot|verify>  Intégrité des fichiers agents
  ${GREEN}health${NC}           Health-check mémoire
  ${GREEN}setup${NC}            Configurer les valeurs utilisateur du projet
  ${GREEN}init${NC}             Initialiser un projet (proxy grimoire-init.sh)
  ${GREEN}reset${NC}            Réinitialiser l'installation (soft/--hard)
  ${GREEN}uninstall${NC}        Supprimer complètement Grimoire du projet
  ${GREEN}quick-update${NC}     Mise à jour rapide du framework (sans prompts)
  ${GREEN}version${NC}          Version du kit
  ${GREEN}help${NC}             Cette aide

${BOLD}Exemples:${NC}
  grimoire doctor
  grimoire tools --tier core
  grimoire lifecycle pre
  grimoire integrity verify
  grimoire setup --check
  grimoire setup --sync
  grimoire setup --user "Alice" --lang "EN"
  grimoire reset --dry-run
  grimoire quick-update
  grimoire uninstall --keep-config
EOF
}

cmd_version() {
    echo "Grimoire Kit v${VERSION}"
}

cmd_doctor() {
    echo -e "\n  ${CYAN}${BOLD}🩺 Grimoire Doctor${NC} — Diagnostic complet"
    echo -e "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "  Projet : ${PROJECT_ROOT}"
    echo -e "  Version: ${VERSION}\n"

    local issues=0
    local warnings=0

    # 1. project-context.yaml
    echo -e "  ${BOLD}1. Configuration${NC}"
    if [[ -f "${PROJECT_ROOT}/project-context.yaml" ]]; then
        echo -e "     ${GREEN}✅${NC} project-context.yaml trouvé"
    else
        echo -e "     ${RED}❌${NC} project-context.yaml MANQUANT"
        ((issues++))
    fi

    # 2. Framework tools
    echo -e "\n  ${BOLD}2. Outils framework${NC}"
    if [[ -d "${TOOLS_DIR}" ]]; then
        local tool_count
        tool_count=$(find "${TOOLS_DIR}" -name "*.py" -not -name "__*" | wc -l)
        echo -e "     ${GREEN}✅${NC} ${tool_count} outils Python trouvés"
    else
        echo -e "     ${RED}❌${NC} Dossier framework/tools/ MANQUANT"
        ((issues++))
    fi

    # 3. Memory
    echo -e "\n  ${BOLD}3. Mémoire${NC}"
    if [[ -d "${PROJECT_ROOT}/_memory" ]]; then
        local mem_files
        mem_files=$(find "${PROJECT_ROOT}/_memory" -type f | wc -l)
        echo -e "     ${GREEN}✅${NC} _memory/ trouvé (${mem_files} fichiers)"

        if [[ -f "${PROJECT_ROOT}/_memory/memories.json" ]]; then
            if python3 -c "import json; json.load(open('${PROJECT_ROOT}/_memory/memories.json'))" 2>/dev/null; then
                echo -e "     ${GREEN}✅${NC} memories.json valide"
            else
                echo -e "     ${YELLOW}⚠️${NC}  memories.json corrompu"
                ((warnings++))
            fi
        fi
    else
        echo -e "     ${YELLOW}⚠️${NC}  _memory/ absent (nouveau projet ?)"
        ((warnings++))
    fi

    # 4. Maintenance health-check
    echo -e "\n  ${BOLD}4. Health-check mémoire${NC}"
    if [[ -f "${MEMORY_DIR}/maintenance.py" ]]; then
        python3 "${MEMORY_DIR}/maintenance.py" health-check 2>/dev/null && \
            echo -e "     ${GREEN}✅${NC} Health-check OK" || \
            echo -e "     ${YELLOW}⚠️${NC}  Health-check a signalé des problèmes"
    else
        echo -e "     ${YELLOW}⚠️${NC}  maintenance.py non trouvé"
        ((warnings++))
    fi

    # 5. Agent integrity
    echo -e "\n  ${BOLD}5. Intégrité agents${NC}"
    if [[ -f "${TOOLS_DIR}/agent-integrity.py" ]]; then
        local integrity_output
        integrity_output=$(python3 "${TOOLS_DIR}/agent-integrity.py" --project-root "${PROJECT_ROOT}" verify --json 2>/dev/null || echo '{"status":"error"}')
        local integrity_status
        integrity_status=$(echo "$integrity_output" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")
        case "$integrity_status" in
            clean)      echo -e "     ${GREEN}✅${NC} Fichiers agents intègres" ;;
            no-snapshot) echo -e "     ${YELLOW}⚠️${NC}  Pas de snapshot — lancer: grimoire integrity snapshot" ; ((warnings++)) ;;
            modified)   echo -e "     ${RED}❌${NC} Fichiers agents MODIFIÉS — lancer: grimoire integrity verify" ; ((issues++)) ;;
            *)          echo -e "     ${YELLOW}⚠️${NC}  Vérification impossible" ; ((warnings++)) ;;
        esac
    else
        echo -e "     ${YELLOW}⚠️${NC}  agent-integrity.py non trouvé"
        ((warnings++))
    fi

    # 6. Tests
    echo -e "\n  ${BOLD}6. Tests${NC}"
    if [[ -d "${PROJECT_ROOT}/tests" ]]; then
        local test_count
        test_count=$(find "${PROJECT_ROOT}/tests" -name "test_*.py" | wc -l)
        echo -e "     ${GREEN}✅${NC} ${test_count} fichiers de test trouvés"

        # Quick smoke test
        if command -v python3 &>/dev/null; then
            if python3 -m pytest "${PROJECT_ROOT}/tests/" -q --tb=no --no-header 2>/dev/null | tail -1 | grep -q "passed"; then
                echo -e "     ${GREEN}✅${NC} Tests passent"
            else
                echo -e "     ${YELLOW}⚠️${NC}  Certains tests échouent"
                ((warnings++))
            fi
        fi
    else
        echo -e "     ${YELLOW}⚠️${NC}  Dossier tests/ absent"
        ((warnings++))
    fi

    # 7. Lint
    echo -e "\n  ${BOLD}7. Lint${NC}"
    if command -v python3 &>/dev/null && python3 -m ruff --version &>/dev/null 2>&1; then
        local lint_errors
        lint_errors=$(python3 -m ruff check "${TOOLS_DIR}/" 2>/dev/null | wc -l)
        if [[ "$lint_errors" -eq 0 ]]; then
            echo -e "     ${GREEN}✅${NC} Ruff: 0 erreurs"
        else
            echo -e "     ${YELLOW}⚠️${NC}  Ruff: ${lint_errors} erreurs"
            ((warnings++))
        fi
    else
        echo -e "     ${YELLOW}⚠️${NC}  Ruff non installé"
    fi

    # Summary
    echo -e "\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if [[ $issues -eq 0 && $warnings -eq 0 ]]; then
        echo -e "  ${GREEN}${BOLD}✅ Tout est OK !${NC}"
    elif [[ $issues -eq 0 ]]; then
        echo -e "  ${YELLOW}${BOLD}⚠️  ${warnings} avertissement(s), 0 erreur critique${NC}"
    else
        echo -e "  ${RED}${BOLD}❌ ${issues} erreur(s) critique(s), ${warnings} avertissement(s)${NC}"
    fi
    echo
}

cmd_status() {
    echo -e "\n  ${CYAN}Grimoire Status${NC} — ${PROJECT_ROOT}"
    echo -e "  Version: ${VERSION}"

    # Tools count
    if [[ -d "${TOOLS_DIR}" ]]; then
        local tc
        tc=$(find "${TOOLS_DIR}" -name "*.py" -not -name "__*" | wc -l)
        echo -e "  Outils : ${tc}"
    fi

    # Memory
    if [[ -d "${PROJECT_ROOT}/_memory" ]]; then
        local mc
        mc=$(find "${PROJECT_ROOT}/_memory" -type f | wc -l)
        echo -e "  Mémoire: ${mc} fichiers"
    fi

    echo
}

cmd_tools() {
    if [[ -f "${TOOLS_DIR}/tool-registry.py" ]]; then
        python3 "${TOOLS_DIR}/tool-registry.py" --project-root "${PROJECT_ROOT}" list "$@"
    else
        echo -e "  Outils dans ${TOOLS_DIR}/ :"
        find "${TOOLS_DIR}" -name "*.py" -not -name "__*" | sort | while read -r f; do
            echo "    $(basename "$f" .py)"
        done
    fi
}

cmd_lifecycle() {
    local sub="${1:-}"
    if [[ -z "$sub" ]]; then
        echo "Usage: grimoire lifecycle <pre|post|status>"
        return 1
    fi
    shift
    python3 "${TOOLS_DIR}/session-lifecycle.py" --project-root "${PROJECT_ROOT}" "$sub" "$@"
}

cmd_integrity() {
    local sub="${1:-}"
    if [[ -z "$sub" ]]; then
        echo "Usage: grimoire integrity <snapshot|verify>"
        return 1
    fi
    shift
    python3 "${TOOLS_DIR}/agent-integrity.py" --project-root "${PROJECT_ROOT}" "$sub" "$@"
}

cmd_health() {
    python3 "${MEMORY_DIR}/maintenance.py" health-check "$@"
}

cmd_setup() {
    python3 "${TOOLS_DIR}/grimoire-setup.py" --project-root "${PROJECT_ROOT}" "$@"
}

cmd_init() {
    bash "${SCRIPT_DIR}/grimoire-init.sh" "$@"
}

cmd_reset() {
    bash "${SCRIPT_DIR}/grimoire-init.sh" reset "$@"
}

cmd_uninstall() {
    bash "${SCRIPT_DIR}/grimoire-init.sh" uninstall "$@"
}

cmd_quickupdate() {
    bash "${SCRIPT_DIR}/grimoire-init.sh" quick-update "$@"
}

# ─── Dispatcher ───────────────────────────────────────────────────────────────

main() {
    local cmd="${1:-help}"
    shift 2>/dev/null || true

    case "$cmd" in
        doctor)     cmd_doctor "$@" ;;
        status)     cmd_status "$@" ;;
        tools)      cmd_tools "$@" ;;
        lifecycle)  cmd_lifecycle "$@" ;;
        integrity)  cmd_integrity "$@" ;;
        health)     cmd_health "$@" ;;
        setup)      cmd_setup "$@" ;;
        init)       cmd_init "$@" ;;
        reset)      cmd_reset "$@" ;;
        uninstall)  cmd_uninstall "$@" ;;
        quick-update) cmd_quickupdate "$@" ;;
        version|-v|--version)  cmd_version ;;
        help|-h|--help)        cmd_help ;;
        *)
            echo -e "${RED}Commande inconnue: ${cmd}${NC}"
            echo "Tapez 'grimoire help' pour voir les commandes disponibles."
            exit 1
            ;;
    esac
}

main "$@"
