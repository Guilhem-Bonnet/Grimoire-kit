#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Grimoire Custom Kit — Bootstrap Installer
# ═══════════════════════════════════════════════════════════════════════════════
#
# Installation rapide sans clone préalable.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Guilhem-Bonnet/Grimoire-kit/main/install.sh | bash
#   curl -fsSL <URL>/install.sh | bash -s -- --name "Mon Projet" --user "Alice"
#   bash install.sh --name "Mon Projet" --user "Alice" --archetype infra-ops
#
# CHEMIN RECOMMANDE (SDK Python) :
#   pipx install grimoire-kit
#   grimoire up .          # init + setup + standard + doctor en une commande
#
# Ce script shell est le chemin LEGACY (mode maintenance). Sans --legacy, il
# affiche les instructions SDK et s'arrête.
#
# Mode legacy (--legacy) :
#   1. Clone le kit Grimoire dans un dossier temporaire
#   2. Exécute grimoire-init.sh avec les arguments fournis
#   3. Nettoie le clone temporaire (ou --keep-kit pour garder)
#
# Prérequis : git, bash 4+
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Constantes ───────────────────────────────────────────────────────────────
REPO_URL="https://github.com/Guilhem-Bonnet/Grimoire-kit.git"
BRANCH="main"
KEEP_KIT=false
KIT_DIR=""
LEGACY=false

# ─── Couleurs ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}ℹ️  $*${NC}"; }
ok()    { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}"; }
error() { echo -e "${RED}❌ $*${NC}" >&2; exit 1; }

# ─── Prérequis ───────────────────────────────────────────────────────────────
check_prerequisites() {
    if ! command -v git &>/dev/null; then
        error "git est requis. Installez-le : https://git-scm.com"
    fi
    if ! command -v bash &>/dev/null; then
        error "bash est requis"
    fi
    # Vérifier bash 4+
    local bash_major
    bash_major="${BASH_VERSION%%.*}"
    if [[ "$bash_major" -lt 4 ]]; then
        error "bash 4+ requis (actuel: $BASH_VERSION)"
    fi
}

# ─── Nettoyage ───────────────────────────────────────────────────────────────
cleanup() {
    if [[ "$KEEP_KIT" == false && -n "$KIT_DIR" && -d "$KIT_DIR" ]]; then
        rm -rf "$KIT_DIR"
    fi
}
trap cleanup EXIT

# ─── Usage ───────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
${CYAN}Grimoire Custom Kit — Bootstrap Installer${NC}

CHEMIN RECOMMANDE (SDK Python) :
  pipx install grimoire-kit
  grimoire up .              # init + setup + standard + doctor en une commande

Ce script est le chemin legacy (mode maintenance). Il exige --legacy.

Usage:
  bash install.sh --legacy --name "Nom du Projet" --user "Votre Nom" [options]

Options:
  --legacy            Exécuter l'installeur shell legacy (sinon: instructions SDK)
  --name NAME         Nom du projet (requis)
  --user USER         Votre nom (requis)
  --lang LANGUAGE     Langue de communication (défaut: Français)
  --archetype TYPE    Archétype: minimal, infra-ops, web-app, fix-loop (défaut: minimal)
  --auto              Détecter automatiquement le stack
  --memory BACKEND    Backend mémoire: auto, local, qdrant-local, ollama (défaut: auto)
  --branch BRANCH     Branche git du kit (défaut: main)
  --keep-kit          Garder le clone du kit après installation
  --kit-dir DIR       Chemin pour le clone du kit (défaut: .grimoire-kit-tmp)
  --help              Afficher cette aide

Exemples:
  bash install.sh --name "Mon API" --user "Alice"
  bash install.sh --name "Infra Prod" --user "Bob" --archetype infra-ops --auto
  curl -fsSL <URL>/install.sh | bash -s -- --name "Test" --user "Dev"
EOF
    exit 0
}

# ─── Arguments ───────────────────────────────────────────────────────────────
INIT_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --legacy)    LEGACY=true; shift ;;
        --branch)    BRANCH="$2"; shift 2 ;;
        --keep-kit)  KEEP_KIT=true; shift ;;
        --kit-dir)   KIT_DIR="$2"; shift 2 ;;
        --help|-h)   usage ;;
        # Tous les autres args sont passés à grimoire-init.sh
        *)           INIT_ARGS+=("$1"); shift ;;
    esac
done

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${CYAN}Grimoire Custom Kit — Bootstrap Installer${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
    echo ""

    if [[ "$LEGACY" == false ]]; then
        cat <<EOF
Le chemin d'installation recommandé est le SDK Python :

    pipx install grimoire-kit      # ou: pip install grimoire-kit (dans un venv)
    grimoire up .                  # init + setup + standard + doctor en une commande

Autres commandes utiles :
    grimoire doctor                # diagnostic de l'environnement
    grimoire cockpit scan ~/dev    # détecter et enrôler vos projets existants

Cet installeur shell est conservé en mode maintenance uniquement.
Pour l'exécuter malgré tout : relancez avec --legacy
EOF
        exit 0
    fi

    warn "Mode legacy : le chemin recommandé est le SDK (pipx install grimoire-kit && grimoire up .)"
    echo ""

    check_prerequisites

    # Déterminer le dossier du kit
    if [[ -z "$KIT_DIR" ]]; then
        KIT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/grimoire-kit-XXXXXX")"
    fi

    # Vérifier si le kit est déjà présent localement
    if [[ -f "$KIT_DIR/grimoire-init.sh" ]]; then
        info "Kit détecté dans $KIT_DIR — utilisation du cache"
    else
        info "Téléchargement du Grimoire Kit (branche: $BRANCH)..."
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$KIT_DIR" 2>&1 | tail -2
        ok "Kit téléchargé"
    fi

    # Vérifier que grimoire-init.sh existe
    if [[ ! -f "$KIT_DIR/grimoire-init.sh" ]]; then
        error "grimoire-init.sh non trouvé dans le kit. Vérifiez le repo."
    fi

    echo ""
    info "Lancement de l'initialisation..."
    echo ""

    # Exécuter grimoire-init.sh avec les arguments
    bash "$KIT_DIR/grimoire-init.sh" "${INIT_ARGS[@]}"

    echo ""
    if [[ "$KEEP_KIT" == true ]]; then
        ok "Kit conservé dans : $KIT_DIR"
        info "Pour mettre à jour : cd $KIT_DIR && git pull"
    else
        info "Kit temporaire nettoyé"
    fi
}

main
