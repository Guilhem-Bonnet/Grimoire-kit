#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Grimoire Custom Kit — Installeur
# ═══════════════════════════════════════════════════════════════════════════════
#
# Installe uv s'il manque, puis grimoire-kit en version épinglée avec
# `uv tool install` : grimoire, grimoire-mcp et grimoire-hook se résolvent
# ensuite depuis n'importe quel shell, sans venv actif.
#
# Usage:
#   bash install.sh                       # version épinglée par la release
#   bash install.sh --version 3.71.0      # une autre version
#   curl -fsSL <URL>/install.sh | bash
#
# Le script publié en artefact de release porte sa version et une attestation
# de provenance : `gh attestation verify install.sh --repo Guilhem-Bonnet/Grimoire-kit`.
# Sorti du dépôt, sans version estampillée, il exige --version.
#
# Variables : GRIMOIRE_VERSION (= --version), GRIMOIRE_SPEC (wheel ou source à
# installer à la place du paquet publié, pour la CI), UV_VERSION (version d'uv).
#
# Mode legacy (--legacy) : clone le kit, exécute grimoire-init.sh, nettoie.
#   bash install.sh --legacy --name "Mon Projet" --user "Alice" [options]
#
# Prérequis : curl ou wget ; git et bash 4+ pour --legacy seulement.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Constantes ───────────────────────────────────────────────────────────────
REPO_URL="https://github.com/Guilhem-Bonnet/Grimoire-kit.git"
# Estampillé par la release (publish.yml) ; vide dans le dépôt.
PINNED_VERSION=""
VERSION="${GRIMOIRE_VERSION:-}"
SPEC="${GRIMOIRE_SPEC:-}"
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

# ─── Prérequis (legacy) ──────────────────────────────────────────────────────
check_prerequisites() {
    if ! command -v git &>/dev/null; then
        error "git est requis. Installez-le : https://git-scm.com"
    fi
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
${CYAN}Grimoire Custom Kit — Installeur${NC}

Installe uv si absent, puis grimoire-kit épinglé via uv tool install.

Usage:
  bash install.sh [--version X.Y.Z]
  bash install.sh --legacy --name "Nom du Projet" --user "Votre Nom" [options]

Options:
  --version X.Y.Z     Version de grimoire-kit à installer (défaut: celle de la release)
  --legacy            Installeur shell legacy (clone le kit, lance grimoire-init.sh)
  --name NAME         (legacy) Nom du projet (requis)
  --user USER         (legacy) Votre nom (requis)
  --lang LANGUAGE     (legacy) Langue de communication (défaut: Français)
  --archetype TYPE    (legacy) Archétype: minimal, infra-ops, web-app, fix-loop
  --auto              (legacy) Détecter automatiquement le stack
  --memory BACKEND    (legacy) Backend mémoire: auto, local, qdrant-local, ollama
  --branch BRANCH     (legacy) Branche git du kit (défaut: main)
  --keep-kit          (legacy) Garder le clone du kit après installation
  --kit-dir DIR       (legacy) Chemin pour le clone du kit
  --help              Afficher cette aide
EOF
    exit 0
}

# ─── Arguments ───────────────────────────────────────────────────────────────
INIT_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --legacy)    LEGACY=true; shift ;;
        --version)   VERSION="$2"; shift 2 ;;
        --branch)    BRANCH="$2"; shift 2 ;;
        --keep-kit)  KEEP_KIT=true; shift ;;
        --kit-dir)   KIT_DIR="$2"; shift 2 ;;
        --help|-h)   usage ;;
        # Tous les autres args sont passés à grimoire-init.sh
        *)           INIT_ARGS+=("$1"); shift ;;
    esac
done

# ─── uv ──────────────────────────────────────────────────────────────────────
ensure_uv() {
    if command -v uv &>/dev/null; then
        ok "uv déjà présent : $(uv --version)"
        return
    fi
    info "uv absent — installation..."
    local url="https://astral.sh/uv/install.sh"
    if [[ -n "${UV_VERSION:-}" ]]; then
        url="https://astral.sh/uv/${UV_VERSION}/install.sh"
    fi
    if command -v curl &>/dev/null; then
        curl -LsSf "$url" | sh
    elif command -v wget &>/dev/null; then
        wget -qO- "$url" | sh
    else
        error "curl ou wget est requis pour installer uv"
    fi
    # uv s'installe dans ~/.local/bin (ou ~/.cargo/bin selon la version).
    export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
    command -v uv &>/dev/null || error "uv est installé mais introuvable dans le PATH"
    ok "uv installé : $(uv --version)"
}

# ─── SDK : uv tool install ───────────────────────────────────────────────────
install_sdk() {
    VERSION="${VERSION:-$PINNED_VERSION}"
    [[ -n "$VERSION" ]] || error "aucune version épinglée : relancez avec --version X.Y.Z (ou utilisez le script publié avec la release)"
    ensure_uv

    local target="${SPEC:-grimoire-kit==$VERSION}"
    info "Installation de grimoire-kit $VERSION (uv tool install)..."
    uv tool install --force "$target"

    # Le dossier des exécutables d'uv doit être dans le PATH, ici et aux shells futurs.
    local bindir
    bindir="$(uv tool dir --bin 2>/dev/null || true)"
    if [[ -n "$bindir" ]]; then
        export PATH="$bindir:$PATH"
    fi
    uv tool update-shell >/dev/null 2>&1 || true

    local tool
    for tool in grimoire grimoire-mcp grimoire-hook; do
        command -v "$tool" &>/dev/null || error "$tool ne se résout pas dans le PATH après l'installation"
    done
    local got
    got="$(grimoire --version)"
    [[ "$got" == "grimoire-kit $VERSION" ]] || error "version installée inattendue : '$got' (attendu : grimoire-kit $VERSION)"
    ok "$got — grimoire, grimoire-mcp et grimoire-hook sont installés"
    echo ""
    info "Si \`grimoire\` n'est pas encore trouvé, ouvrez un nouveau shell. Puis : grimoire up ."
}

# ─── Main ────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${CYAN}Grimoire Custom Kit — Installeur${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
    echo ""

    if [[ "$LEGACY" == false ]]; then
        install_sdk
        exit 0
    fi

    warn "Mode legacy : le chemin recommandé est l'installation sans --legacy (uv tool install)"
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
