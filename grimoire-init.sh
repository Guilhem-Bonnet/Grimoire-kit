#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Grimoire Custom Kit — Initialisation d'un nouveau projet
# ═══════════════════════════════════════════════════════════════════════════════
#
# Usage:
#   grimoire-init.sh --name "Mon Projet" --user "Guilhem" --lang "Français" --archetype infra-ops
#   grimoire-init.sh --help
#
# Ce script installe le framework Grimoire Custom dans le répertoire courant.
# Il est conçu pour être exécuté depuis la racine du projet cible.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Version ──────────────────────────────────────────────────────────────────
GRIMOIRE_KIT_VERSION="$(cat "$(cd "$(dirname "$0")" && pwd)/version.txt" 2>/dev/null || echo "dev")"

# ─── Variables ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$(pwd)"
PROJECT_NAME=""
USER_NAME=""
LANGUAGE="Français"
ARCHETYPE="minimal"
AUTO_DETECT=false
FORCE=false
MEMORY_BACKEND="auto"

# ─── Couleurs ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ─── Fonctions utilitaires ───────────────────────────────────────────────────
info()  { echo -e "${BLUE}ℹ️  $*${NC}"; }
ok()    { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}"; }
error() { echo -e "${RED}❌ $*${NC}" >&2; exit 1; }

usage() {
    cat <<EOF
${CYAN}Grimoire Custom Kit v${GRIMOIRE_KIT_VERSION} — Initialisation${NC}

Usage:
  $(basename "$0") --name "Nom du Projet" --user "Votre Nom" [options]
  $(basename "$0") session-branch --name "branch-name" [--list|--diff|--merge|--archive|--cherry-pick]
  $(basename "$0") install --archetype TYPE [--force] [--list] [--inspect TYPE]
  $(basename "$0") reset [--hard] [--dry-run] [--yes]
  $(basename "$0") uninstall [--yes] [--keep-config]
  $(basename "$0") quick-update [--dry-run]

Options init:
  --name NAME         Nom du projet (requis)
  --user USER         Votre nom (requis)
  --lang LANGUAGE     Langue de communication (défaut: Français)
  --archetype TYPE    Archétype à utiliser: minimal, infra-ops, fix-loop (défaut: minimal)
  --target DIR        Répertoire cible (défaut: répertoire courant)
  --auto              Détecter automatiquement le stack et choisir l'archétype optimal
  --memory BACKEND    Backend mémoire: auto, local, qdrant-local, qdrant-server, ollama,
                      qdrant-docker, qdrant-k8s (défaut: auto)
  --version           Afficher la version du kit et quitter
                        qdrant-docker (génère docker-compose.memory.yml)
  --force             Écraser une installation existante sans demander confirmation
  --help              Afficher cette aide

Options session-branch:
  session-branch --name NAME    Créer une nouvelle branche de session
  session-branch --list         Lister toutes les branches actives
  session-branch --diff B1 B2   Comparer les artefacts de deux branches
  session-branch --merge NAME   Merger une branche vers main
  session-branch --archive NAME Archiver une branche terminée
  session-branch --cherry-pick BRANCH SRC DST  Copier un artefact spécifique vers main

Options install:
  install --archetype TYPE  Installer un archétype dans un projet Grimoire existant
  install --list            Lister tous les archétypes disponibles avec leur DNA
  install --inspect TYPE    Inspecter un archétype (agents, traits, contraintes) sans installer
  install --force           Forcer la réinstallation (overwrite fichiers existants)

Archétypes:
  minimal     Meta-agents (Atlas, Sentinel, Mnemo) + 1 agent vierge
  infra-ops   Agents Infrastructure & DevOps complets (10 agents)
  web-app     Agents Full-Stack (Stack, Pixel) + agents stack auto
  fix-loop    Orchestrateur boucle de correction certifiée + workflow 9 phases

Exemples:
  $(basename "$0") --name "Mon API" --user "Alice" --archetype minimal
  $(basename "$0") --name "Infra Prod" --user "Bob" --archetype infra-ops --lang "English"
  $(basename "$0") --name "Mon App" --user "Guilhem" --auto --memory ollama
  $(basename "$0") session-branch --name "explore-graphql"
  $(basename "$0") session-branch --list
  $(basename "$0") session-branch --diff main explore-graphql
  $(basename "$0") install --list
  $(basename "$0") install --archetype infra-ops
  $(basename "$0") install --archetype stack/go --force
  $(basename "$0") install --inspect fix-loop
  $(basename "$0") reset                                    # Soft reset (préserve mémoire)
  $(basename "$0") reset --hard --yes                        # Hard reset (tout supprimer)
  $(basename "$0") reset --dry-run                           # Preview du reset
  $(basename "$0") uninstall                                 # Supprimer Grimoire du projet
  $(basename "$0") uninstall --keep-config                   # Garder project-context.yaml
  $(basename "$0") quick-update                              # MAJ rapide framework
  $(basename "$0") quick-update --dry-run                    # Preview des MAJ
  $(basename "$0") resume
  $(basename "$0") resume --checkpoint a3f9b2
  $(basename "$0") resume --list
  $(basename "$0") trace --tail 50
  $(basename "$0") trace --agent dev
  $(basename "$0") trace --type DECISION
  $(basename "$0") doctor
  $(basename "$0") doctor --fix
  $(basename "$0") validate --dna archetypes/web-app/archetype.dna.yaml
  $(basename "$0") validate --all
  $(basename "$0") changelog
  $(basename "$0") hooks --install
  $(basename "$0") hooks --install --hook post-commit
  $(basename "$0") hooks --list
  $(basename "$0") hooks --status
  $(basename "$0") bench --report
  $(basename "$0") bench --report --since 2026-01-01
  $(basename "$0") bench --report --agent forge
  $(basename "$0") bench --improve
  $(basename "$0") bench --summary
  $(basename "$0") forge --from "je veux un agent pour les migrations DB"
  $(basename "$0") forge --from-gap
  $(basename "$0") forge --from-trace
  $(basename "$0") forge --list
  $(basename "$0") forge --install db-migrator
  $(basename "$0") guard                          # Budget contexte de tous les agents
  $(basename "$0") guard --agent atlas --detail   # Détail d'un agent
  $(basename "$0") guard --suggest               # + recommandations Mnemo
  $(basename "$0") evolve                        # Proposer évolutions DNA
  $(basename "$0") evolve --report              # Rapport seul
  $(basename "$0") evolve --since 2026-01-01    # Depuis une date
  $(basename "$0") evolve --apply               # Appliquer le dernier patch
  $(basename "$0") upgrade                      # Mettre à jour le framework
  $(basename "$0") upgrade --dry-run            # Voir les changements sans appliquer
  $(basename "$0") dream                        # Dream Mode — insights hors-session
  $(basename "$0") dream --since 2026-01-01     # Depuis une date
  $(basename "$0") dream --agent dev            # Focus un agent
  $(basename "$0") dream --quick --emit         # Rêve rapide + émettre phéromones
  $(basename "$0") dream --dry-run              # Preview sans écrire
  $(basename "$0") consensus --proposal "..."   # Adversarial Consensus Protocol
  $(basename "$0") consensus --history          # Historique des décisions
  $(basename "$0") consensus --stats            # Statistiques
  $(basename "$0") antifragile                  # Score d'anti-fragilité
  $(basename "$0") antifragile --detail         # Rapport détaillé
  $(basename "$0") antifragile --trend          # Tendance historique
  $(basename "$0") reasoning log --agent dev --type HYPOTHESIS --text "..."  # Log reasoning
  $(basename "$0") reasoning query --type DOUBT # Interroger le stream
  $(basename "$0") reasoning analyze            # Analyse du flux de raisonnement
  $(basename "$0") migrate export               # Exporter un bundle de migration
  $(basename "$0") migrate import --bundle B    # Importer un bundle
  $(basename "$0") migrate inspect --bundle B   # Inspecter un bundle
  $(basename "$0") migrate diff --bundle B      # Diff bundle vs projet
  $(basename "$0") darwinism evaluate           # Évaluer la fitness des agents
  $(basename "$0") darwinism leaderboard        # Classement des agents
  $(basename "$0") darwinism evolve             # Actions évolutives
  $(basename "$0") darwinism history            # Historique des générations
  $(basename "$0") darwinism lineage --agent X  # Lignée d'un agent
  $(basename "$0") stigmergy emit --type NEED --location "src/auth" --text "review" --agent dev
  $(basename "$0") stigmergy sense              # Phéromones actives
  $(basename "$0") stigmergy sense --type ALERT # Alertes uniquement
  $(basename "$0") stigmergy amplify --id PH-xx --agent qa  # Renforcer un signal
  $(basename "$0") stigmergy resolve --id PH-xx --agent qa  # Résoudre un signal
  $(basename "$0") stigmergy landscape          # Carte phéromonique
  $(basename "$0") stigmergy trails             # Patterns émergents
  $(basename "$0") stigmergy evaporate          # Nettoyer les signaux morts
  $(basename "$0") stigmergy stats              # Statistiques
  $(basename "$0") status                       # Tableau de bord unifié
  $(basename "$0") status --json                # Sortie JSON

Options guard:
  guard                    Analyser le budget de contexte de tous les agents
  guard --agent AGENT_ID   Analyser un agent spécifique
  guard --detail           Détail fichier par fichier
  guard --model MODEL      Modèle cible (défaut: copilot / 200K tokens)
  guard --threshold PCT    Seuil d'alerte en %% (défaut: 40)
  guard --suggest          Afficher des recommandations de réduction
  guard --list-models      Lister les modèles supportés
  guard --json             Sortie JSON (pour CI)

Options evolve:
  evolve                   Analyser le projet et proposer des évolutions DNA
  evolve --report          Générer seulement le rapport (sans patch)
  evolve --apply           Appliquer le dernier patch gén

Options upgrade:
  upgrade                  Mettre à jour le framework dans un projet existant
  upgrade --dry-run        Afficher les changements sans les appliquer
  upgrade --force          Mettre à jour même si la version est identiqueéré
  evolve --since YYYY-MM-DD Analyser depuis une date
  evolve --dna PATH        Chemin vers archetype.dna.yaml

Options forge:
  forge --from "description"  Générer un squelette d'agent depuis une description textuelle
  forge --from-gap            Générer depuis les requêtes inter-agents non résolues (shared-context.md)
  forge --from-trace          Générer depuis les failure patterns sans agent (GRIMOIRE_TRACE)
  forge --list                Lister les proposals en attente
  forge --install AGENT       Installer un proposal reviewé dans le répertoire des agents
  forge --archetype TYPE      Archétype de référence (défaut: custom)
  forge --out DIR             Dossier de sortie (défaut: _grimoire-output/forge-proposals/)

Options dream:
  dream                    Exécuter un cycle de Dream Mode (consolidation hors-session)
  dream --since YYYY-MM-DD Analyser depuis une date
  dream --since auto       Incrémental — depuis le dernier dream
  dream --agent AGENT_ID   Filtrer les sources par agent
  dream --validate         Activer la validation stricte des insights
  dream --quick            Mode rapide O(n) — patterns + opportunités uniquement
  dream --emit             Émettre les insights en phéromones stigmergy
  dream --dry-run          Afficher sans écrire le journal
  dream --json             Sortie JSON

Options consensus:
  consensus --proposal "texte"  Évaluer une proposition via le protocole adversarial
  consensus --proposal-file F   Charger la proposition depuis un fichier
  consensus --threshold PCT     Seuil de consensus (défaut: 0.66)
  consensus --history           Afficher l'historique des décisions
  consensus --stats             Statistiques agrégées
  consensus --json              Sortie JSON
  consensus --dry-run           Ne pas sauvegarder dans l'historique

Options antifragile:
  antifragile                   Calculer le score d'anti-fragilité
  antifragile --since YYYY-MM-DD  Analyser depuis une date
  antifragile --detail          Rapport détaillé avec recommandations
  antifragile --trend           Tendance historique des scores
  antifragile --json            Sortie JSON
  antifragile --dry-run         Calculer sans sauvegarder

Options reasoning:
  reasoning log --agent ID --type TYPE --text "..."   Ajouter une entrée
  reasoning query [--agent ID] [--type TYPE] [--status STATUS]  Interroger
  reasoning analyze [--since DATE]   Analyser le flux de raisonnement
  reasoning compact [--before DATE]  Compacter les anciennes entrées
  reasoning stats                    Statistiques rapides
  reasoning resolve --timestamp TS --status STATUS  Changer le statut

Options migrate:
  migrate export                  Exporter les artefacts dans un bundle portable
  migrate export --only TYPE,...  Filtrer par type (learnings,rules,dna_patches,agents,consensus,antifragile)
  migrate export --since DATE     Exporter depuis une date
  migrate export --output PATH    Fichier de sortie (défaut: _grimoire-output/migration-bundle.json)
  migrate import --bundle PATH    Importer un bundle dans le projet
  migrate import --bundle P --dry-run  Preview sans modifier
  migrate inspect --bundle PATH   Inspecter le contenu d'un bundle
  migrate diff --bundle PATH      Comparer un bundle avec le projet

Options darwinism:
  darwinism evaluate              Évaluer la fitness de tous les agents
  darwinism evaluate --since DATE Évaluer depuis une date
  darwinism evaluate --json       Sortie JSON
  darwinism leaderboard           Classement par score fitness
  darwinism evolve                Proposer des actions évolutives
  darwinism evolve --dry-run      Preview sans sauvegarder
  darwinism evolve --json         Sortie JSON
  darwinism history               Historique des générations
  darwinism lineage --agent ID    Lignée évolutive d'un agent

Options stigmergy:
  stigmergy emit --type TYPE --location LOC --text TXT --agent AGENT  Émettre une phéromone
  stigmergy emit ... --tags t1,t2 --intensity 0.9  Options émission
  stigmergy sense                 Détecter les phéromones actives
  stigmergy sense --type ALERT    Filtrer par type (NEED|ALERT|OPPORTUNITY|PROGRESS|COMPLETE|BLOCK)
  stigmergy sense --location LOC  Filtrer par zone
  stigmergy sense --tag TAG       Filtrer par tag
  stigmergy sense --emitter AGENT Filtrer par émetteur
  stigmergy sense --json          Sortie JSON
  stigmergy amplify --id PH-xxx --agent AGENT  Renforcer un signal
  stigmergy resolve --id PH-xxx --agent AGENT  Résoudre un signal
  stigmergy landscape             Carte complète du paysage phéromonique
  stigmergy trails                Patterns de coordination émergents
  stigmergy evaporate             Supprimer les signaux sous le seuil
  stigmergy evaporate --dry-run   Preview sans modifier
  stigmergy stats                 Statistiques rapides

Options status:
  status                          Tableau de bord unifié du Nervous System
  status --json                   Sortie JSON machine-readable

EOF
    exit 0
}

# ─── Session Branching (BM-16) ───────────────────────────────────────────────
# Gestion des branches de session Grimoire
# Usage: grimoire-init.sh session-branch [--name|--list|--diff|--merge|--archive|--cherry-pick]
cmd_session_branch() {
    local RUNS_DIR="${TARGET_DIR}/_grimoire-output/.runs"
    local action=""
    local branch_name=""
    local branch_b=""
    local src_file=""
    local dst_file=""
    local NOW
    NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Parser les sous-arguments
    shift  # retirer "session-branch"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name)       action="create"; branch_name="$2"; shift 2 ;;
            --list)       action="list"; shift ;;
            --diff)       action="diff"; branch_name="$2"; branch_b="${3:-main}"; shift 3 ;;
            --merge)      action="merge"; branch_name="$2"; shift 2 ;;
            --archive)    action="archive"; branch_name="$2"; shift 2 ;;
            --cherry-pick) action="cherry-pick"; branch_name="$2"; src_file="$3"; dst_file="$4"; shift 4 ;;
            *) error "Option inconnue pour session-branch: $1. Utilisez --help." ;;
        esac
    done

    case "$action" in
        create)
            [[ -z "$branch_name" ]] && error "Nom de branche requis : --name nom-branche"
            local branch_dir="${RUNS_DIR}/${branch_name}"
            if [[ -d "$branch_dir" ]] && ! $FORCE; then
                error "La branche '${branch_name}' existe déjà. Utilisez --force pour écraser."
            fi
            mkdir -p "$branch_dir"
            cat > "${branch_dir}/branch.json" << EOF
{
  "branch": "${branch_name}",
  "created_at": "${NOW}",
  "created_by": "grimoire-init",
  "purpose": "Session branch created by grimoire-init.sh",
  "parent_branch": "main",
  "status": "active"
}
EOF
            ok "Branche de session créée : ${branch_name}"
            info "Outputs isolés dans : ${branch_dir}/"
            info "Référencer dans project-context.yaml : session_branch: ${branch_name}"
            ;;

        list)
            if [[ ! -d "$RUNS_DIR" ]]; then
                info "Aucun run trouvé dans ${RUNS_DIR}/ — pas encore de sessions"
                exit 0
            fi
            echo -e "${CYAN}Branches de session Grimoire :${NC}"
            echo ""
            local found=false
            for dir in "${RUNS_DIR}"/*/; do
                [[ -d "$dir" ]] || continue
                local bname
                bname="$(basename "$dir")"
                [[ "$bname" == "archive" ]] && continue
                local bstatus="active"
                local bdate="—"
                if [[ -f "${dir}/branch.json" ]]; then
                    bstatus="$(grep '"status"' "${dir}/branch.json" | sed 's/.*: *"\(.*\)".*/\1/')"
                    bdate="$(grep '"created_at"' "${dir}/branch.json" | sed 's/.*: *"\(.*\)".*/\1/' | cut -c1-10)"
                fi
                local run_count
                run_count="$(find "$dir" -maxdepth 1 -name "state.json" | wc -l | tr -d ' ')"
                echo -e "  ${GREEN}●${NC} ${CYAN}${bname}${NC} — statut: ${bstatus} | créée: ${bdate} | runs: ${run_count}"
                found=true
            done
            $found || info "Aucune branche trouvée"
            ;;

        diff)
            [[ -z "$branch_name" ]] && error "Deux branches requises : --diff branch1 branch2"
            local dir_a="${RUNS_DIR}/${branch_name}"
            local dir_b="${RUNS_DIR}/${branch_b}"
            [[ ! -d "$dir_a" ]] && error "Branche '$branch_name' non trouvée"
            [[ ! -d "$dir_b" ]] && error "Branche '$branch_b' non trouvée"
            echo -e "${CYAN}Diff : ${branch_name} vs ${branch_b}${NC}"
            echo ""
            echo "=== Artefacts dans ${branch_name} (pas dans ${branch_b}) ==="
            comm -23 <(find "$dir_a" -type f | sed "s|${dir_a}/||" | sort) \
                     <(find "$dir_b" -type f | sed "s|${dir_b}/||" | sort) || true
            echo ""
            echo "=== Artefacts dans ${branch_b} (pas dans ${branch_name}) ==="
            comm -13 <(find "$dir_a" -type f | sed "s|${dir_a}/||" | sort) \
                     <(find "$dir_b" -type f | sed "s|${dir_b}/||" | sort) || true
            ;;

        merge)
            [[ -z "$branch_name" ]] && error "Nom de branche requis pour le merge"
            local src_dir="${RUNS_DIR}/${branch_name}"
            local dst_dir="${RUNS_DIR}/main"
            [[ ! -d "$src_dir" ]] && error "Branche '$branch_name' non trouvée"
            mkdir -p "$dst_dir"
            info "Merge de '${branch_name}' → main..."
            cp -r "${src_dir}/." "${dst_dir}/"
            # Mettre à jour le statut
            [[ -f "${src_dir}/branch.json" ]] && \
                sed -i 's/"status": "active"/"status": "merged"/' "${src_dir}/branch.json"
            ok "Branche '${branch_name}' mergée dans main"
            warn "Vérifiez les conflits manuellement dans ${dst_dir}/"
            ;;

        archive)
            [[ -z "$branch_name" ]] && error "Nom de branche requis pour l'archivage"
            local arch_src="${RUNS_DIR}/${branch_name}"
            local arch_dst
            arch_dst="${RUNS_DIR}/archive/${branch_name}-$(date +%Y%m%d)"
            [[ ! -d "$arch_src" ]] && error "Branche '$branch_name' non trouvée"
            mkdir -p "${RUNS_DIR}/archive"
            mv "$arch_src" "$arch_dst"
            ok "Branche '${branch_name}' archivée dans archive/$(basename "$arch_dst")"
            ;;

        cherry-pick)
            [[ -z "$branch_name" || -z "$src_file" || -z "$dst_file" ]] && \
                error "Usage: session-branch --cherry-pick branch source-file destination-file"
            [[ ! -f "$src_file" ]] && error "Fichier source non trouvé: $src_file"
            mkdir -p "$(dirname "$dst_file")"
            cp "$src_file" "$dst_file"
            ok "Cherry-pick : $src_file → $dst_file"
            ;;

        "")
            error "Action requise. Exemples: --name, --list, --diff, --merge, --archive"
            ;;
    esac
    exit 0
}

# ─── Archetype Registry Install (BM-21) ──────────────────────────────────────────────────
# Usage: grimoire-init.sh install [--archetype|--list|--inspect]
cmd_install() {
    shift  # retirer "install"
    local action="install"
    local archetype_id=""
    local target_grimoire=""
    local INSTALL_FORCE=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --archetype)  action="install"; archetype_id="$2"; shift 2 ;;
            --list)       action="list"; shift ;;
            --inspect)    action="inspect"; archetype_id="$2"; shift 2 ;;
            --force)      INSTALL_FORCE=true; shift ;;
            *) error "Option inconnue pour install: $1. Utilisez --help." ;;
        esac
    done

    # Localiser le projet Grimoire le plus proche
    target_grimoire="$(pwd)/_grimoire"
    if [[ ! -d "$target_grimoire/_config/custom" ]]; then
        error "Aucun projet Grimoire trouvé dans $(pwd). Lancez d'abord grimoire-init.sh --name ... --user ..."
    fi

    case "$action" in
        list)
            echo -e "${CYAN}Archétypes Grimoire disponibles :${NC}"
            echo ""
            # Parcourir les répertoires d'archétypes
            local arch_base="$SCRIPT_DIR/archetypes"
            for dir in "$arch_base"/*/; do
                [[ -d "$dir" ]] || continue
                local aid
                aid="$(basename "$dir")"
                local dna="${dir}archetype.dna.yaml"
                if [[ -f "$dna" ]]; then
                    local aname
                    aname="$(grep '^name:' "$dna" | head -1 | sed 's/name: *//;s/"//g')"
                    local adesc
                    adesc="$(grep '^description:' "$dna" | head -1 | sed 's/description: *//;s/"//g' | cut -c1-80)"
                    echo -e "  ${GREEN}●${NC} ${CYAN}${aid}${NC} — ${aname}"
                    echo -e "      ${adesc}"
                else
                    echo -e "  ${YELLOW}◦${NC} ${aid} (pas de DNA déclarée)"
                fi
                # Stack sub-archetypes
                if [[ -d "${dir}agents/" ]]; then
                    for subdir in "${dir}agents/"*.md; do
                        [[ -f "$subdir" ]] || continue
                        local sname
                        sname="$(basename "$subdir" .md | sed 's/-expert$//')"
                        echo -e "    ${BLUE}└ stack/${sname}${NC}"
                    done
                fi
            done
            echo ""
            info "Pour installer : $(basename "$0") install --archetype <id>"
            ;;

        inspect)
            [[ -z "$archetype_id" ]] && error "ID d'archétype requis : --inspect <id>"
            # Gestion stack/{lang}
            if [[ "$archetype_id" == stack/* ]]; then
                local lang="${archetype_id#stack/}"
                local sa="$SCRIPT_DIR/archetypes/stack/agents/${lang}-expert.md"
                [[ ! -f "$sa" ]] && error "Agent stack '$lang' non trouvé. Valides : $(ls "$SCRIPT_DIR/archetypes/stack/agents/" | sed 's/-expert\.md//' | tr '\n' ' ')"
                echo -e "${CYAN}Agent stack : ${archetype_id}${NC}"
                cat "$sa"
                exit 0
            fi
            local arch_dir="$SCRIPT_DIR/archetypes/${archetype_id}"
            [[ ! -d "$arch_dir" ]] && error "Archétype '$archetype_id' non trouvé dans archetypes/"
            local dna="${arch_dir}/archetype.dna.yaml"
            echo -e "${CYAN}Inspection de l'archétype : ${archetype_id}${NC}"
            echo ""
            if [[ -f "$dna" ]]; then
                cat "$dna"
            else
                warn "Pas de fichier archetype.dna.yaml pour $archetype_id"
                echo "Agents disponibles :"
                ls "${arch_dir}/agents/" 2>/dev/null | sed 's/^/  • /'
            fi
            ;;

        install)
            [[ -z "$archetype_id" ]] && error "ID d'archétype requis : --archetype <id>"

            # ── Gestion sous-archétypes stack : stack/go → archetypes/stack/agents/go-expert.md ──
            if [[ "$archetype_id" == stack/* ]]; then
                local lang="${archetype_id#stack/}"
                local sa="$SCRIPT_DIR/archetypes/stack/agents/${lang}-expert.md"
                # Fallback : chercher par glob si le nom exact n'existe pas
                if [[ ! -f "$sa" ]]; then
                    local candidate
                    candidate="$(find "$SCRIPT_DIR/archetypes/stack/agents/" -name "${lang}*.md" | head -1)"
                    [[ -n "$candidate" ]] && sa="$candidate"
                fi
                [[ ! -f "$sa" ]] && error "Agent stack '$lang' non trouvé. Valides : $(ls "$SCRIPT_DIR/archetypes/stack/agents/" | sed 's/-expert\.md//' | tr '\n' ' ')"
                local fname
                fname="$(basename "$sa")"
                local agents_dst="$target_grimoire/_config/custom/agents"
                if [[ -f "${agents_dst}/${fname}" ]] && ! $INSTALL_FORCE; then
                    warn "${fname} existe déjà (utilisez --force pour écraser)"
                else
                    cp "$sa" "${agents_dst}/"
                    ok "Agent stack installé : ${fname}"
                fi
                local installed_log="$target_grimoire/_config/installed-archetypes.yaml"
                [[ ! -f "$installed_log" ]] && { echo "# Auto-généré par grimoire-init.sh" > "$installed_log"; echo "installed:" >> "$installed_log"; }
                cat >> "$installed_log" << STACKEOF
  - id: ${archetype_id}
    installed_at: "$(date +%Y-%m-%d)"
    force: ${INSTALL_FORCE}
STACKEOF
                ok "Stack '${archetype_id}' installé avec succès"
                info "Activer l'agent dans Copilot : ouvrir ${agents_dst}/${fname}"
                exit 0
            fi

            local arch_dir="$SCRIPT_DIR/archetypes/${archetype_id}"
            [[ ! -d "$arch_dir" ]] && error "Archétype '$archetype_id' non trouvé dans archetypes/"

            local agents_dst="$target_grimoire/_config/custom/agents"
            local workflows_dst="$target_grimoire/_config/custom/workflows"

            info "Installation de l'archétype '${archetype_id}'..."

            # Copier les agents
            if [[ -d "${arch_dir}/agents/" ]]; then
                local count=0
                for agent_file in "${arch_dir}/agents/"*.md; do
                    [[ -f "$agent_file" ]] || continue
                    local fname
                    fname="$(basename "$agent_file")"
                    if [[ -f "${agents_dst}/${fname}" ]] && ! $INSTALL_FORCE; then
                        warn "  ${fname} existe déjà (utilisez --force pour écraser)"
                    else
                        cp "$agent_file" "${agents_dst}/"
                        ok "  Agent installé : ${fname}"
                        (( count++ )) || true
                    fi
                done
                [[ $count -eq 0 ]] || ok "${count} agent(s) installé(s)"
            fi

            # Copier les workflows
            if [[ -d "${arch_dir}/workflows/" ]]; then
                mkdir -p "$workflows_dst"
                cp -r "${arch_dir}/workflows/"* "${workflows_dst}/" 2>/dev/null || true
                ok "  Workflows installés"
            fi

            # Fusionner le shared-context si existant
            if [[ -f "${arch_dir}/shared-context.tpl.md" ]]; then
                local sc_dst="$target_grimoire/_memory/shared-context.md"
                if [[ ! -f "$sc_dst" ]]; then
                    cp "${arch_dir}/shared-context.tpl.md" "$sc_dst"
                    ok "  shared-context.md créé"
                else
                    echo "" >> "$sc_dst"
                    echo "<!-- Ajout archétype ${archetype_id} : $(date +%Y-%m-%d) -->" >> "$sc_dst"
                    cat "${arch_dir}/shared-context.tpl.md" >> "$sc_dst"
                    ok "  shared-context.md enrichi avec le contexte ${archetype_id}"
                fi
            fi

            # Enregistrer dans installed-archetypes.yaml
            local installed_log="$target_grimoire/_config/installed-archetypes.yaml"
            if [[ ! -f "$installed_log" ]]; then
                echo "# Auto-généré par grimoire-init.sh" > "$installed_log"
                echo "installed:" >> "$installed_log"
            fi
            cat >> "$installed_log" << YAMLEOF
  - id: ${archetype_id}
    installed_at: "$(date +%Y-%m-%d)"
    force: ${INSTALL_FORCE}
YAMLEOF
            ok "Archétype '${archetype_id}' installé avec succès"
            info "Agents disponibles dans : ${agents_dst}/"
            ;;
    esac
    exit 0
}

# ─── Hooks (BM-40) ────────────────────────────────────────────────────────────
# Gestion des git hooks Grimoire
# Usage: grimoire-init.sh hooks [--install [--hook <name>] | --list | --status]
cmd_hooks() {
    shift  # retirer "hooks"
    local action="status"
    local specific_hook=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --install)  action="install"; shift ;;
            --list)     action="list"; shift ;;
            --status)   action="status"; shift ;;
            --hook)     specific_hook="$2"; shift 2 ;;
            *) error "Option inconnue pour hooks: $1" ;;
        esac
    done

    local GIT_HOOKS_DIR
    GIT_HOOKS_DIR="$(git rev-parse --git-dir 2>/dev/null)/hooks"
    local FRAMEWORK_HOOKS_DIR
    FRAMEWORK_HOOKS_DIR="$(dirname "$(realpath "$0")")/framework/hooks"

    # Mapping : nom git hook → fichier source dans framework/hooks/
    declare -A HOOK_MAP
    HOOK_MAP["pre-commit"]="pre-commit-cc.sh"
    HOOK_MAP["pre-commit-mnemo"]="mnemo-consolidate.sh"
    HOOK_MAP["post-checkout"]="post-checkout.sh"
    HOOK_MAP["prepare-commit-msg"]="prepare-commit-msg.sh"
    HOOK_MAP["commit-msg"]="commit-msg.sh"
    HOOK_MAP["post-commit"]="post-commit.sh"
    HOOK_MAP["pre-push"]="pre-push.sh"

    case "$action" in
        # ── list ──────────────────────────────────────────────────────────────
        list)
            echo ""
            echo "Hooks Grimoire disponibles :"
            echo ""
            for hook_name in "${!HOOK_MAP[@]}"; do
                local src="${FRAMEWORK_HOOKS_DIR}/${HOOK_MAP[$hook_name]}"
                local installed="  (non installé)"
                [[ -f "$GIT_HOOKS_DIR/$hook_name" ]] && installed="  ${GREEN}✓ installé${NC}"
                printf "  %-28s ← %s%b\n" "$hook_name" "${HOOK_MAP[$hook_name]}" "$installed"
            done
            echo ""
            ;;

        # ── status ────────────────────────────────────────────────────────────
        status)
            echo ""
            echo "Status des hooks Grimoire dans .git/hooks/ :"
            echo ""
            local installed_count=0
            local total_count=${#HOOK_MAP[@]}
            for hook_name in pre-commit post-checkout prepare-commit-msg commit-msg post-commit pre-push; do
                local src_file="${HOOK_MAP[$hook_name]:-}"
                if [[ -z "$src_file" ]]; then continue; fi
                local dst="$GIT_HOOKS_DIR/$hook_name"
                if [[ -f "$dst" ]]; then
                    # Vérifier si c'est bien un hook Grimoire
                    if grep -q "Grimoire" "$dst" 2>/dev/null; then
                        echo -e "  ${GREEN}✓${NC}  $hook_name"
                        installed_count=$((installed_count + 1))
                    else
                        echo -e "  ${YELLOW}⚠${NC}  $hook_name (hook tiers — pas Grimoire)"
                    fi
                else
                    echo -e "  ${RED}✗${NC}  $hook_name"
                fi
            done
            echo ""
            echo "  $installed_count/$total_count hooks installés"
            if [[ $installed_count -lt $total_count ]]; then
                echo "  → Pour tout installer : $(basename "$0") hooks --install"
            fi
            echo ""
            ;;

        # ── install ───────────────────────────────────────────────────────────
        install)
            if [[ ! -d "$GIT_HOOKS_DIR" ]]; then
                error "Pas dans un dépôt git (ou .git/hooks introuvable)"
            fi
            if [[ ! -d "$FRAMEWORK_HOOKS_DIR" ]]; then
                error "framework/hooks/ introuvable — lancez depuis la racine du kit"
            fi

            local hooks_to_install=()
            if [[ -n "$specific_hook" ]]; then
                hooks_to_install=("$specific_hook")
            else
                hooks_to_install=(pre-commit post-checkout prepare-commit-msg commit-msg post-commit pre-push)
            fi

            echo ""
            echo "Installation des hooks Grimoire dans $GIT_HOOKS_DIR/ ..."
            echo ""
            local installed_ok=0
            local skipped=0
            for hook_name in "${hooks_to_install[@]}"; do
                local src_file="${HOOK_MAP[$hook_name]:-}"
                if [[ -z "$src_file" ]]; then
                    warn "Hook inconnu : $hook_name — ignoré"
                    continue
                fi
                local src="$FRAMEWORK_HOOKS_DIR/$src_file"
                local dst="$GIT_HOOKS_DIR/$hook_name"

                if [[ ! -f "$src" ]]; then
                    warn "  Source manquante : $src — ignoré"
                    continue
                fi

                # Gestion des hooks pre-commit multiples via run-parts ou chaînage
                if [[ "$hook_name" == "pre-commit" ]] && [[ -f "$dst" ]] && ! grep -q "Grimoire" "$dst" 2>/dev/null; then
                    warn "  pre-commit existant (non-Grimoire) détecté — création pre-commit.d/grimoire-cc.sh"
                    mkdir -p "$GIT_HOOKS_DIR/../.git-hooks-precommit"
                    cp "$src" "$GIT_HOOKS_DIR/../.git-hooks-precommit/grimoire-cc.sh"
                    chmod +x "$GIT_HOOKS_DIR/../.git-hooks-precommit/grimoire-cc.sh"
                    skipped=$((skipped + 1))
                    continue
                fi

                cp "$src" "$dst"
                chmod +x "$dst"
                echo -e "  ${GREEN}✓${NC}  $hook_name ← $src_file"
                installed_ok=$((installed_ok + 1))
            done

            # Mnemo dans pre-commit si pas déjà là
            local mnemo_src="$FRAMEWORK_HOOKS_DIR/mnemo-consolidate.sh"
            local precommit_dst="$GIT_HOOKS_DIR/pre-commit"
            if [[ -f "$mnemo_src" ]] && [[ -f "$precommit_dst" ]]; then
                if ! grep -q "mnemo" "$precommit_dst" 2>/dev/null; then
                    echo "" >> "$precommit_dst"
                    echo "# Grimoire Mnemo consolidation" >> "$precommit_dst"
                    echo "bash \"\$(git rev-parse --show-toplevel)/framework/hooks/mnemo-consolidate.sh\"" >> "$precommit_dst"
                    echo -e "  ${GREEN}+${NC}  mnemo-consolidate injecté dans pre-commit"
                fi
            fi

            echo ""
            echo "  $installed_ok hook(s) installé(s)${skipped:+ — $skipped ignoré(s) (hook tiers préservé)}"
            echo ""

            # Générer .pre-commit-config.yaml si absent
            local precommit_cfg
            precommit_cfg="$(git rev-parse --show-toplevel 2>/dev/null)/.pre-commit-config.yaml"
            if [[ ! -f "$precommit_cfg" ]] && [[ -f "$(git rev-parse --show-toplevel 2>/dev/null)/framework/hooks/.pre-commit-config.tpl.yaml" ]]; then
                cp "$(git rev-parse --show-toplevel 2>/dev/null)/framework/hooks/.pre-commit-config.tpl.yaml" "$precommit_cfg"
                echo -e "  ${GREEN}✓${NC}  .pre-commit-config.yaml généré"
            fi
            ;;
    esac
    exit 0
}

# ─── Bench (BM-51) ────────────────────────────────────────────────────────────
# Benchmark de performance des agents depuis GRIMOIRE_TRACE
# Usage: grimoire-init.sh bench [--report|--improve|--summary] [--since YYYY-MM-DD] [--agent ID]
cmd_bench() {
    shift  # retirer "bench"
    local action="summary"
    local since=""
    local agent_filter=""
    local out_path="_grimoire-output/bench-reports/latest.md"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --report)   action="report"; shift ;;
            --improve)  action="improve"; shift ;;
            --summary)  action="summary"; shift ;;
            --since)    since="$2"; shift 2 ;;
            --agent)    agent_filter="$2"; shift 2 ;;
            --out)      out_path="$2"; shift 2 ;;
            *) error "Option inconnue pour bench: $1" ;;
        esac
    done

    local bench_script
    bench_script="$(dirname "$(realpath "$0")")/framework/tools/agent-bench.py"

    if [[ ! -f "$bench_script" ]]; then
        error "framework/tools/agent-bench.py introuvable — lancez depuis la racine du kit"
    fi

    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour bench"
    fi

    local py_args=("$bench_script")

    case "$action" in
        report)  py_args+=("--report") ;;
        improve) py_args+=("--report" "--improve") ;;
        summary) py_args+=("--summary") ;;
    esac

    [[ -n "$since" ]]        && py_args+=("--since" "$since")
    [[ -n "$agent_filter" ]] && py_args+=("--agent" "$agent_filter")
    py_args+=("--out" "$out_path")

    echo ""
    info "Grimoire Bench — analyse GRIMOIRE_TRACE en cours..."
    echo ""

    python3 "${py_args[@]}"

    if [[ "$action" == "improve" ]] && [[ -f "_grimoire-output/bench-reports/bench-context.md" ]]; then
        echo ""
        echo -e "${CYAN}→  bench-context.md prêt pour Sentinel${NC}"
        echo "   Ouvrez Copilot Chat → activez l'agent Sentinel"
        echo "   Passez le fichier en contexte et tapez : bench-review"
    fi
    exit 0
}

# ─── Agent Forge (BM-52) ─────────────────────────────────────────────────────
# Génération de scaffolds d'agents depuis des besoins détectés
# Usage: grimoire-init.sh forge [--from DESCRIPTION | --from-gap | --from-trace | --list | --install AGENT]
cmd_forge() {
    shift  # retirer "forge"
    local from_desc=""
    local mode=""
    local install_name=""
    local archetype="custom"
    local out_dir="_grimoire-output/forge-proposals"
    local shared_context="_grimoire/_memory/shared-context.md"
    local trace_path="_grimoire-output/GRIMOIRE_TRACE.md"
    local agents_dir="_grimoire/_config/custom/agents"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --from)         mode="from"; from_desc="$2"; shift 2 ;;
            --from-gap)     mode="from-gap"; shift ;;
            --from-trace)   mode="from-trace"; shift ;;
            --list)         mode="list"; shift ;;
            --install)      mode="install"; install_name="$2"; shift 2 ;;
            --archetype)    archetype="$2"; shift 2 ;;
            --out)          out_dir="$2"; shift 2 ;;
            --shared-context) shared_context="$2"; shift 2 ;;
            --trace)        trace_path="$2"; shift 2 ;;
            --agents-dir)   agents_dir="$2"; shift 2 ;;
            *) error "Option inconnue pour forge: $1" ;;
        esac
    done

    if [[ -z "$mode" ]]; then
        echo ""
        echo -e "${YELLOW}Usage :${NC}"
        echo "  $(basename "$0") forge --from \"description du besoin\""
        echo "  $(basename "$0") forge --from-gap"
        echo "  $(basename "$0") forge --from-trace"
        echo "  $(basename "$0") forge --list"
        echo "  $(basename "$0") forge --install <nom-agent>"
        echo ""
        echo "  Pipeline forge :"
        echo "   1. forge --from \"...\"  → génère proposal dans _grimoire-output/forge-proposals/"
        echo "   2. [Réviser les [TODO] dans le fichier .proposed.md]"
        echo "   3. forge --install <tag>  → copie dans _grimoire/_config/custom/agents/"
        echo "   4. Sentinel [AA] pour audit qualité"
        exit 0
    fi

    local forge_script
    forge_script="$(dirname "$(realpath "$0")")/framework/tools/agent-forge.py"

    if [[ ! -f "$forge_script" ]]; then
        error "framework/tools/agent-forge.py introuvable — lancez depuis la racine du kit"
    fi

    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour forge"
    fi

    local py_args=("$forge_script")

    case "$mode" in
        from)         py_args+=("--from" "$from_desc") ;;
        from-gap)     py_args+=("--from-gap" "--shared-context" "$shared_context") ;;
        from-trace)   py_args+=("--from-trace" "--trace" "$trace_path") ;;
        list)         py_args+=("--list") ;;
        install)      py_args+=("--install" "$install_name" "--agents-dir" "$agents_dir") ;;
    esac

    py_args+=("--archetype" "$archetype" "--out-dir" "$out_dir")

    echo ""
    info "Grimoire Agent Forge — mode : ${mode}"
    echo ""

    python3 "${py_args[@]}"

    if [[ "$mode" == "from" ]] || [[ "$mode" == "from-gap" ]] || [[ "$mode" == "from-trace" ]]; then
        echo ""
        echo -e "${CYAN}→  Proposals générés dans ${out_dir}/${NC}"
        echo "   Étapes : 1) Réviser les [TODO]  2) forge --install <tag>  3) Sentinel [AA]"
    fi
    exit 0
}

# ─── Context Budget Guard (BM-55) ─────────────────────────────────────────────
# Estime le budget de contexte LLM consommé par les agents au démarrage
# Usage: grimoire-init.sh guard [--agent ID] [--detail] [--model MODEL] [--suggest]
cmd_guard() {
    shift  # retirer "guard"

    local guard_script
    guard_script="$(dirname "$(realpath "$0")")/framework/tools/context-guard.py"

    if [[ ! -f "$guard_script" ]]; then
        error "framework/tools/context-guard.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour guard"
    fi

    echo ""
    info "Grimoire Context Budget Guard"
    echo ""
    python3 "$guard_script" "$@"
    exit $?
}

# ─── DNA Evolution Engine (BM-56) ─────────────────────────────────────────────
# Fait évoluer la DNA d'un archétype depuis l'usage réel (TRACE + decisions)
# Usage: grimoire-init.sh evolve [--report] [--apply] [--since DATE] [--dna PATH]
cmd_evolve() {
    shift  # retirer "evolve"

    local evolve_script
    evolve_script="$(dirname "$(realpath "$0")")/framework/tools/dna-evolve.py"

    if [[ ! -f "$evolve_script" ]]; then
        error "framework/tools/dna-evolve.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour evolve"
    fi

    echo ""
    info "Grimoire DNA Evolution Engine"
    python3 "$evolve_script" "$@"
    exit $?
}

# ─── Memory Lint ─────────────────────────────────────────────────────────────
# Linter de cohérence mémoire — cross-validation des fichiers mémoire
# Usage: grimoire-init.sh memory-lint [--json] [--fix] [--emit]
cmd_memorylint() {
    shift  # retirer "memory-lint"

    local ml_script
    ml_script="$(dirname "$(realpath "$0")")/framework/tools/memory-lint.py"

    if [[ ! -f "$ml_script" ]]; then
        error "framework/tools/memory-lint.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour memory-lint"
    fi

    echo ""
    python3 "$ml_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── NSO — Nervous System Orchestrator ───────────────────────────────────────
# Orchestrateur du système nerveux : exécute dream → stigmergy → antifragile → darwinism → memory-lint
# Usage: grimoire-init.sh nso run [--since DATE] [--quick] [--json] [--emit]
cmd_nso() {
    shift  # retirer "nso"

    local nso_script
    nso_script="$(dirname "$(realpath "$0")")/framework/tools/nso.py"

    if [[ ! -f "$nso_script" ]]; then
        error "framework/tools/nso.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour nso"
    fi

    echo ""
    python3 "$nso_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Schema Validator ────────────────────────────────────────────────────────
# Validation des fichiers YAML (archetype DNA, team manifests, agent DNA)
# Usage: grimoire-init.sh schema-validate [--type dna|team|agent_dna] [--file PATH] [--json]
cmd_schemav() {
    shift  # retirer "schema-validate"

    local sv_script
    sv_script="$(dirname "$(realpath "$0")")/framework/tools/schema-validator.py"

    if [[ ! -f "$sv_script" ]]; then
        error "framework/tools/schema-validator.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour schema-validate"
    fi

    echo ""
    python3 "$sv_script" --project-root "$(pwd)" validate "$@"
    exit $?
}

# ─── Auto-Doc Sync ───────────────────────────────────────────────────────────
# Détection et correction automatique du drift README ↔ code
# Usage: grimoire-init.sh auto-doc check [--json] | auto-doc sync [--json]
cmd_autodoc() {
    shift  # retirer "auto-doc"

    local ad_script
    ad_script="$(dirname "$(realpath "$0")")/framework/tools/auto-doc.py"

    if [[ ! -f "$ad_script" ]]; then
        error "framework/tools/auto-doc.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour auto-doc"
    fi

    echo ""
    python3 "$ad_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Dream Mode ──────────────────────────────────────────────────────────────
# Consolidation hors-session : insights émergents depuis la mémoire
# Usage: grimoire-init.sh dream [--since DATE] [--agent ID] [--validate] [--dry-run]
cmd_dream() {
    shift  # retirer "dream"

    local dream_script
    dream_script="$(dirname "$(realpath "$0")")/framework/tools/dream.py"

    if [[ ! -f "$dream_script" ]]; then
        error "framework/tools/dream.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour dream"
    fi

    echo ""
    python3 "$dream_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Adversarial Consensus Protocol ──────────────────────────────────────────
# Protocole de consensus BFT pour les décisions critiques
# Usage: grimoire-init.sh consensus --proposal "..." [--threshold N] [--history] [--stats]
cmd_consensus() {
    shift  # retirer "consensus"

    local consensus_script
    consensus_script="$(dirname "$(realpath "$0")")/framework/tools/adversarial-consensus.py"

    if [[ ! -f "$consensus_script" ]]; then
        error "framework/tools/adversarial-consensus.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour consensus"
    fi

    echo ""
    python3 "$consensus_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Anti-Fragile Score ──────────────────────────────────────────────────────
# Mesure la résilience adaptative du système Grimoire
# Usage: grimoire-init.sh antifragile [--detail] [--trend] [--since DATE] [--json]
cmd_antifragile() {
    shift  # retirer "antifragile"

    local af_script
    af_script="$(dirname "$(realpath "$0")")/framework/tools/antifragile-score.py"

    if [[ ! -f "$af_script" ]]; then
        error "framework/tools/antifragile-score.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour antifragile"
    fi

    echo ""
    python3 "$af_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Reasoning Stream ────────────────────────────────────────────────────────
# Flux de raisonnement structuré pour capturer le POURQUOI des décisions
# Usage: grimoire-init.sh reasoning log|query|analyze|compact|stats|resolve [...]
cmd_reasoning() {
    shift  # retirer "reasoning"

    local rs_script
    rs_script="$(dirname "$(realpath "$0")")/framework/tools/reasoning-stream.py"

    if [[ ! -f "$rs_script" ]]; then
        error "framework/tools/reasoning-stream.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour reasoning"
    fi

    echo ""
    python3 "$rs_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Cross-Project Migration ─────────────────────────────────────────────────
# Migration d'artefacts entre projets Grimoire (learnings, rules, DNA, agents)
# Usage: grimoire-init.sh migrate export|import|inspect|diff [...]
cmd_migrate() {
    shift  # retirer "migrate"

    local mg_script
    mg_script="$(dirname "$(realpath "$0")")/framework/tools/cross-migrate.py"

    if [[ ! -f "$mg_script" ]]; then
        error "framework/tools/cross-migrate.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour migrate"
    fi

    echo ""
    python3 "$mg_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Agent Darwinism ─────────────────────────────────────────────────────────
# Sélection naturelle des agents — fitness, évolution, leaderboard
# Usage: grimoire-init.sh darwinism evaluate|leaderboard|evolve|history|lineage [...]
cmd_darwinism() {
    shift  # retirer "darwinism"

    local dw_script
    dw_script="$(dirname "$(realpath "$0")")/framework/tools/agent-darwinism.py"

    if [[ ! -f "$dw_script" ]]; then
        error "framework/tools/agent-darwinism.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour darwinism"
    fi

    echo ""
    python3 "$dw_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Stigmergy ─────────────────────────────────────────────────────────────
# Coordination stigmergique — phéromones numériques entre agents
# Usage: grimoire-init.sh stigmergy emit|sense|amplify|resolve|landscape|trails|evaporate|stats [...]
cmd_stigmergy() {
    shift  # retirer "stigmergy"

    local sg_script
    sg_script="$(dirname "$(realpath "$0")")/framework/tools/stigmergy.py"

    if [[ ! -f "$sg_script" ]]; then
        error "framework/tools/stigmergy.py introuvable — lancez depuis la racine du kit"
    fi
    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour stigmergy"
    fi

    echo ""
    python3 "$sg_script" --project-root "$(pwd)" "$@"
    exit $?
}

# ─── Status Dashboard ────────────────────────────────────────────────────────
# Tableau de bord unifié : phéromones, rêves, anti-fragilité, agents
# Usage: grimoire-init.sh status [--json]
cmd_status() {
    shift  # retirer "status"

    local project_root
    project_root="$(pwd)"
    local tools_dir
    tools_dir="$(dirname "$(realpath "$0")")/framework/tools"
    local json_mode=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --json) json_mode=true; shift ;;
            --help)
                echo -e "${CYAN}Usage: $(basename "$0") status [--json]${NC}"
                echo ""
                echo "  Tableau de bord unifié du Grimoire Nervous System."
                echo "  Agrège les données de stigmergy, dream, antifragile et darwinism."
                echo ""
                echo "  --json   Sortie JSON machine-readable"
                exit 0
                ;;
            *) shift ;;
        esac
    done

    if ! command -v python3 &>/dev/null; then
        error "python3 requis pour status"
    fi

    # ─── JSON mode ───
    if [[ "$json_mode" == true ]]; then

        # Stigmergy
        local sg_json="{}"
        if [[ -f "$tools_dir/stigmergy.py" ]]; then
            sg_json=$(python3 "$tools_dir/stigmergy.py" --project-root "$project_root" sense --json 2>/dev/null || echo '{"pheromones":[]}')
        fi

        # Dream — use dream-memory.json for accurate stats
        local dream_json="{}"
        local dream_memory="$project_root/_grimoire-output/dream-memory.json"
        if [[ -f "$dream_memory" ]]; then
            dream_json=$(python3 -c "
import json, sys
try:
    m = json.load(open(sys.argv[1]))
    insights = m.get('insights', {})
    print(json.dumps({
        'total_dreams': m.get('total_dreams', 0),
        'active_insights': sum(1 for v in insights.values() if not v.get('stale')),
        'persistent_insights': sum(1 for v in insights.values() if v.get('seen_count', 0) >= 2 and not v.get('stale')),
        'last_dream': m.get('last_dream', ''),
    }))
except Exception:
    print('{}')
" "$dream_memory" 2>/dev/null || echo '{}')
        fi
        local trigger_count=0
        local counter_file="$project_root/_grimoire/_memory/dream-trigger-count"
        if [[ -f "$counter_file" ]]; then
            trigger_count=$(cat "$counter_file" 2>/dev/null || echo "0")
        fi

        # Antifragile
        local af_json="{}"
        if [[ -f "$tools_dir/antifragile-score.py" ]]; then
            af_json=$(python3 "$tools_dir/antifragile-score.py" --project-root "$project_root" --json 2>/dev/null || echo '{}')
        fi

        # Darwinism
        local dw_json="{}"
        if [[ -f "$tools_dir/agent-darwinism.py" ]]; then
            dw_json=$(python3 "$tools_dir/agent-darwinism.py" --project-root "$project_root" evaluate --json 2>/dev/null || echo '{}')
        fi

        python3 -c "
import json, sys
dream = json.loads(sys.argv[2])
dream['auto_trigger_count'] = int(sys.argv[3])
dream['auto_trigger_interval'] = int(sys.argv[4])
result = {
    'stigmergy': json.loads(sys.argv[1]),
    'dream': dream,
    'antifragile': json.loads(sys.argv[5]),
    'darwinism': json.loads(sys.argv[6])
}
print(json.dumps(result, indent=2))
" "$sg_json" "$dream_json" "$trigger_count" "${Grimoire_DREAM_INTERVAL:-10}" "$af_json" "$dw_json"
        exit $?
    fi

    # ─── Text dashboard ───
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║              🧠  Grimoire Nervous System — Status              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    # ─── Stigmergy ───
    echo "┌─── 🐜 Stigmergy ─────────────────────────────────────────────┐"
    if [[ -f "$tools_dir/stigmergy.py" ]]; then
        python3 "$tools_dir/stigmergy.py" --project-root "$project_root" stats 2>/dev/null \
            | sed 's/^/  /' || echo "  (aucune donnée)"
    else
        echo "  stigmergy.py introuvable"
    fi
    echo ""

    # ─── Dream ───
    echo "┌─── 🌙 Dream Mode ────────────────────────────────────────────┐"
    local dream_memory="$project_root/_grimoire-output/dream-memory.json"
    local dream_journal="$project_root/_grimoire-output/dream-journal.md"
    if [[ -f "$dream_memory" ]]; then
        python3 -c "
import json, sys
try:
    m = json.load(open(sys.argv[1]))
    total = m.get('total_dreams', 0)
    insights = m.get('insights', {})
    active = sum(1 for v in insights.values() if not v.get('stale'))
    persistent = sum(1 for v in insights.values() if v.get('seen_count', 0) >= 2 and not v.get('stale'))
    print(f'  Sessions: {total} | Insights actifs: {active} | Persistants: {persistent}')
except Exception:
    print('  (erreur lecture dream-memory.json)')
" "$dream_memory"
    elif [[ -f "$dream_journal" ]]; then
        local insights
        insights=$(grep -c "^### " "$dream_journal" 2>/dev/null || echo "0")
        echo "  Sessions: ? | Insights journal: $insights"
    else
        echo "  (aucun journal — lancez: grimoire-init.sh dream)"
    fi
    local counter_file="$project_root/_grimoire/_memory/dream-trigger-count"
    if [[ -f "$counter_file" ]]; then
        local count
        count=$(cat "$counter_file" 2>/dev/null || echo "0")
        local interval="${Grimoire_DREAM_INTERVAL:-10}"
        echo "  Auto-trigger: $count/$interval commits"
    else
        echo "  Auto-trigger: inactif"
    fi
    echo ""

    # ─── Anti-Fragile ───
    echo "┌─── 💪 Anti-Fragile Score ────────────────────────────────────┐"
    if [[ -f "$tools_dir/antifragile-score.py" ]]; then
        local af_output
        af_output=$(python3 "$tools_dir/antifragile-score.py" --project-root "$project_root" --json 2>/dev/null)
        if [[ -n "$af_output" ]]; then
            local score_line
            score_line=$(echo "$af_output" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f\"  Score: {d.get('score', 'N/A')}/100 — Grade: {d.get('grade', 'N/A')}\")
except Exception:
    print('  (erreur de lecture)')" 2>/dev/null)
            echo "$score_line"
        else
            echo "  (aucune donnée — lancez: grimoire-init.sh antifragile)"
        fi
    else
        echo "  antifragile-score.py introuvable"
    fi
    echo ""

    # ─── Darwinism ───
    echo "┌─── 🧬 Agent Darwinism ───────────────────────────────────────┐"
    if [[ -f "$tools_dir/agent-darwinism.py" ]]; then
        local darwin_output
        darwin_output=$(python3 "$tools_dir/agent-darwinism.py" --project-root "$project_root" leaderboard 2>/dev/null | head -8)
        if [[ -n "$darwin_output" ]]; then
            echo "$darwin_output" | sed 's/^/  /'
        else
            echo "  (aucune donnée — lancez: grimoire-init.sh darwinism evaluate)"
        fi
    else
        echo "  agent-darwinism.py introuvable"
    fi
    echo ""

    echo "════════════════════════════════════════════════════════════════"
    echo "  Commandes rapides:"
    echo "    grimoire-init.sh dream --quick --emit   Rêver + émettre phéromones"
    echo "    grimoire-init.sh antifragile --detail    Score détaillé"
    echo "    grimoire-init.sh stigmergy landscape     Carte phéromonique"
    echo "    grimoire-init.sh darwinism evolve        Proposer évolutions"
    echo "════════════════════════════════════════════════════════════════"
    echo ""
    exit 0
}

# ─── Upgrade (NEW) ─────────────────────────────────────────────────────────
# Mise à jour du framework dans un projet existant
# Usage: grimoire-init.sh upgrade [--dry-run] [--force]
cmd_upgrade() {
    shift  # retirer "upgrade"
    local dry_run=false
    local force_upgrade=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)  dry_run=true; shift ;;
            --force)    force_upgrade=true; shift ;;
            --help)
                echo -e "${CYAN}Usage: $(basename "$0") upgrade [--dry-run] [--force]${NC}"
                echo ""
                echo "  Met à jour les fichiers du framework Grimoire dans le projet courant"
                echo "  sans toucher aux agents, configs ou artefacts personnalisés."
                echo ""
                echo "  --dry-run   Afficher les changements sans les appliquer"
                echo "  --force     Mettre à jour même si la version est identique"
                exit 0
                ;;
            *)  error "Option upgrade inconnue: $1" ;;
        esac
    done

    local grimoire_dir="${TARGET_DIR}/_grimoire"
    local project_ctx="${TARGET_DIR}/project-context.yaml"

    # Vérifier qu'un projet Grimoire existe
    if [[ ! -d "$grimoire_dir/_config/custom" ]]; then
        error "Pas de projet Grimoire détecté dans $(pwd). Lancez d'abord grimoire-init.sh --name ..."
    fi

    # Lire la version installée
    local installed_version="unknown"
    if [[ -f "$project_ctx" ]]; then
        installed_version="$(grep 'grimoire_kit_version:' "$project_ctx" 2>/dev/null | sed 's/.*: *//' | tr -d '"' || echo "unknown")"
    fi

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}║  Grimoire Custom Kit — Upgrade${NC}"
    echo -e "${CYAN}╙═══════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Version installée : ${YELLOW}${installed_version}${NC}"
    echo -e "  Version kit       : ${GREEN}${GRIMOIRE_KIT_VERSION}${NC}"
    echo ""

    if [[ "$installed_version" == "$GRIMOIRE_KIT_VERSION" && "$force_upgrade" == false ]]; then
        ok "La version est déjà à jour ($GRIMOIRE_KIT_VERSION)"
        info "Utilisez --force pour forcer la mise à jour"
        exit 0
    fi

    # ─── Liste des fichiers framework à mettre à jour ───────────────────
    local updated=0
    local skipped=0

    # Fonction interne : comparer et copier un fichier
    _upgrade_file() {
        local src="$1"
        local dst="$2"
        local label="$3"

        if [[ ! -f "$src" ]]; then
            echo -e "  ${YELLOW}⚠${NC}  Source manquante : $label"
            skipped=$((skipped + 1))
            return
        fi

        if [[ ! -f "$dst" ]]; then
            # Fichier n'existe pas dans le projet — nouveau fichier
            if [[ "$dry_run" == true ]]; then
                echo -e "  ${GREEN}+${NC}  $label ${CYAN}(nouveau)${NC}"
            else
                mkdir -p "$(dirname "$dst")"
                cp "$src" "$dst"
                echo -e "  ${GREEN}+${NC}  $label ${CYAN}(nouveau)${NC}"
            fi
            updated=$((updated + 1))
            return
        fi

        if diff -q "$src" "$dst" &>/dev/null; then
            skipped=$((skipped + 1))
            return
        fi

        # Fichier différent
        if [[ "$dry_run" == true ]]; then
            echo -e "  ${YELLOW}~${NC}  $label ${CYAN}(modifié)${NC}"
            diff --color=always -u "$dst" "$src" 2>/dev/null | head -20
            echo ""
        else
            cp "$src" "$dst"
            echo -e "  ${GREEN}✓${NC}  $label ${CYAN}(mis à jour)${NC}"
        fi
        updated=$((updated + 1))
    }

    # ─── 1. Framework core files ────────────────────────────────────────
    echo -e "${YELLOW}▶ Framework core${NC}"
    _upgrade_file "$SCRIPT_DIR/framework/agent-base.md" \
                  "$grimoire_dir/_config/custom/agent-base.md" \
                  "agent-base.md"

    _upgrade_file "$SCRIPT_DIR/framework/cc-verify.sh" \
                  "$grimoire_dir/_config/custom/cc-verify.sh" \
                  "cc-verify.sh"

    _upgrade_file "$SCRIPT_DIR/framework/sil-collect.sh" \
                  "$grimoire_dir/_config/custom/sil-collect.sh" \
                  "sil-collect.sh"
    echo ""

    # ─── 2. Memory scripts ──────────────────────────────────────────────
    echo -e "${YELLOW}▶ Memory scripts${NC}"
    _upgrade_file "$SCRIPT_DIR/framework/memory/maintenance.py" \
                  "$grimoire_dir/_memory/maintenance.py" \
                  "maintenance.py"

    _upgrade_file "$SCRIPT_DIR/framework/memory/mem0-bridge.py" \
                  "$grimoire_dir/_memory/mem0-bridge.py" \
                  "mem0-bridge.py"

    _upgrade_file "$SCRIPT_DIR/framework/memory/session-save.py" \
                  "$grimoire_dir/_memory/session-save.py" \
                  "session-save.py"

    # Backends
    if [[ -d "$SCRIPT_DIR/framework/memory/backends" ]]; then
        for backend_file in "$SCRIPT_DIR/framework/memory/backends/"*.py; do
            [[ -f "$backend_file" ]] || continue
            local bname
            bname="$(basename "$backend_file")"
            _upgrade_file "$backend_file" \
                          "$grimoire_dir/_memory/backends/$bname" \
                          "backends/$bname"
        done
    fi
    echo ""

    # ─── 3. Meta agents ─────────────────────────────────────────────────
    echo -e "${YELLOW}▶ Meta agents${NC}"
    if [[ -d "$SCRIPT_DIR/archetypes/meta/agents" ]]; then
        for meta_agent in "$SCRIPT_DIR/archetypes/meta/agents/"*.md; do
            [[ -f "$meta_agent" ]] || continue
            local aname
            aname="$(basename "$meta_agent")"
            _upgrade_file "$meta_agent" \
                          "$grimoire_dir/_config/custom/agents/$aname" \
                          "meta/$aname"
        done
    fi
    echo ""

    # ─── 4. Prompt templates & workflows ────────────────────────────────
    echo -e "${YELLOW}▶ Templates & Workflows${NC}"
    if [[ -d "$SCRIPT_DIR/framework/prompt-templates" ]]; then
        for tpl in "$SCRIPT_DIR/framework/prompt-templates/"*; do
            [[ -f "$tpl" ]] || continue
            local tname
            tname="$(basename "$tpl")"
            _upgrade_file "$tpl" \
                          "$grimoire_dir/_config/custom/prompt-templates/$tname" \
                          "prompt-templates/$tname"
        done
    fi
    if [[ -d "$SCRIPT_DIR/framework/workflows" ]]; then
        for wf in "$SCRIPT_DIR/framework/workflows/"*; do
            [[ -f "$wf" ]] || continue
            local wname
            wname="$(basename "$wf")"
            _upgrade_file "$wf" \
                          "$grimoire_dir/_config/custom/workflows/$wname" \
                          "workflows/$wname"
        done
    fi
    echo ""

    # ─── 5. Hooks ───────────────────────────────────────────────────────
    echo -e "${YELLOW}▶ Git hooks${NC}"
    local git_hooks_dir
    git_hooks_dir="$(git rev-parse --git-dir 2>/dev/null)/hooks" || true
    if [[ -d "$SCRIPT_DIR/framework/hooks" && -d "${git_hooks_dir:-/dev/null}" ]]; then
        for hook_src in "$SCRIPT_DIR/framework/hooks/"*.sh; do
            [[ -f "$hook_src" ]] || continue
            local hname
            hname="$(basename "$hook_src" .sh)"
            # Ne pas écraser des hooks non-Grimoire
            local hook_dst="$git_hooks_dir/$hname"
            if [[ -f "$hook_dst" ]] && ! grep -q "Grimoire" "$hook_dst" 2>/dev/null; then
                echo -e "  ${YELLOW}⚠${NC}  $hname — hook non-Grimoire existant, ignoré"
                skipped=$((skipped + 1))
                continue
            fi
            _upgrade_file "$hook_src" "$hook_dst" "hook/$hname"
        done
    else
        echo -e "  ${YELLOW}⚠${NC}  Git hooks non mis à jour (pas de dépôt git ou hooks manquants)"
    fi
    echo ""

    # ─── 6. Mise à jour version dans project-context.yaml ──────────────
    if [[ "$dry_run" == false && -f "$project_ctx" ]]; then
        if grep -q 'grimoire_kit_version:' "$project_ctx"; then
            sed -i "s/grimoire_kit_version:.*/grimoire_kit_version: \"$GRIMOIRE_KIT_VERSION\"/" "$project_ctx"
        else
            echo "grimoire_kit_version: \"$GRIMOIRE_KIT_VERSION\"" >> "$project_ctx"
        fi
        echo -e "  ${GREEN}✓${NC}  project-context.yaml → v${GRIMOIRE_KIT_VERSION}"
    fi

    # ─── Résumé ─────────────────────────────────────────────────────────
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    if [[ "$dry_run" == true ]]; then
        echo -e "  ${CYAN}Dry-run : ${updated} fichier(s) seraient mis à jour, ${skipped} inchangé(s)${NC}"
        info "Retirez --dry-run pour appliquer les changements"
    else
        echo -e "  ${GREEN}✅ Upgrade terminé : ${updated} mis à jour, ${skipped} inchangé(s)${NC}"
        echo -e "  ${GREEN}   ${installed_version} → ${GRIMOIRE_KIT_VERSION}${NC}"
    fi
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit 0
}

# ─── Doctor (BM-33) ───────────────────────────────────────────────────────────
# Health check de l'installation Grimoire
# Usage: grimoire-init.sh doctor [--fix]
cmd_doctor() {
    local do_fix=false
    shift  # retirer "doctor"
    [[ "${1:-}" == "--fix" ]] && do_fix=true

    local errors=0
    local warnings=0

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}║  Grimoire Doctor — Diagnostic de l'installation$([ "$do_fix" = true ] && echo " + auto-fix")${NC}"
    echo -e "${CYAN}╙═══════════════════════════════════════════════════════${NC}"
    echo ""

    # ─ 1. Outils système ──────────────────────────────────────────────────
    echo -e "${YELLOW}▶ Outils système${NC}"
    local tools=("bash:bash --version" "git:git --version" "python3:python3 --version")
    for entry in "${tools[@]}"; do
        local tool="${entry%%:*}"
        local cmd="${entry#*:}"
        if $cmd &>/dev/null; then
            local ver
            ver="$(${cmd} 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)"
            echo -e "  ${GREEN}✓${NC}  ${tool} (${ver})"
        else
            echo -e "  ${RED}✗${NC}  ${tool} — MANQUANT"
            errors=$((errors + 1))
        fi
    done

    # PyYAML
    if python3 -c "import yaml" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC}  PyYAML disponible"
    else
        echo -e "  ${YELLOW}⚠${NC}  PyYAML manquant (requis pour gen-tests.py + validate)"
        warnings=$((warnings + 1))
        if $do_fix; then
            info "  → Installation PyYAML..."
            if python3 -m pip install pyyaml -q; then
                ok "  PyYAML installé"
            else
                warn "  Échec install PyYAML"
            fi
        fi
    fi
    echo ""

    # ─ 2. Structure Grimoire ─────────────────────────────────────────────────
    echo -e "${YELLOW}▶ Structure Grimoire${NC}"
    local grimoire_dir="${TARGET_DIR:-$SCRIPT_DIR}/_grimoire"
    local critical_dirs=("_grimoire/_config" "_grimoire/_memory" "_grimoire/_config/custom/agents" "_grimoire-output")
    for d in "${critical_dirs[@]}"; do
        local full="${TARGET_DIR:-$SCRIPT_DIR}/${d}"
        if [[ -d "$full" ]]; then
            echo -e "  ${GREEN}✓${NC}  ${d}/"
        else
            echo -e "  ${RED}✗${NC}  ${d}/ — manquant"
            errors=$((errors + 1))
        fi
    done

    # shared-context.md
    local sc="${grimoire_dir}/_memory/shared-context.md"
    if [[ -f "$sc" ]]; then
        local wc
        wc="$(wc -l < "$sc" | tr -d ' ')"
        echo -e "  ${GREEN}✓${NC}  _grimoire/_memory/shared-context.md (${wc} lignes)"
    else
        echo -e "  ${YELLOW}⚠${NC}  shared-context.md manquant — projet non initialisé"
        warnings=$((warnings + 1))
    fi

    # project-context.yaml
    local pc="${TARGET_DIR:-$SCRIPT_DIR}/project-context.yaml"
    if [[ -f "$pc" ]]; then
        echo -e "  ${GREEN}✓${NC}  project-context.yaml"
    else
        echo -e "  ${YELLOW}⚠${NC}  project-context.yaml manquant — lancez : grimoire-init.sh --name ..."
        warnings=$((warnings + 1))
    fi
    echo ""

    # ─ 3. Mémoire / Qdrant ─────────────────────────────────────────────
    echo -e "${YELLOW}▶ Mémoire${NC}"
    local bridge="${grimoire_dir}/_memory/mem0-bridge.py"
    if [[ -f "$bridge" ]]; then
        echo -e "  ${GREEN}✓${NC}  mem0-bridge.py présent"
        # Vérif Qdrant
        if curl -sf --connect-timeout 2 --max-time 3 http://localhost:6333/healthz &>/dev/null 2>&1; then
            echo -e "  ${GREEN}✓${NC}  Qdrant accessible (localhost:6333)"
        else
            echo -e "  ${YELLOW}⚠${NC}  Qdrant non détecté — mémoire structurée désactivée"
            echo -e "       Pour activer : docker run -p 6333:6333 qdrant/qdrant"
            warnings=$((warnings + 1))
        fi
    else
        echo -e "  ${YELLOW}⚠${NC}  mem0-bridge.py manquant"
        warnings=$((warnings + 1))
    fi
    echo ""

    # ─ 4. DNA des archétypes ──────────────────────────────────────────
    echo -e "${YELLOW}▶ DNA des archétypes${NC}"
    local arch_base="$SCRIPT_DIR/archetypes"
    if [[ -d "$arch_base" ]]; then
        for dna_file in "${arch_base}"/*/archetype.dna.yaml; do
            [[ -f "$dna_file" ]] || continue
            local arch_name
            arch_name="$(basename "$(dirname "$dna_file")")"
            if python3 -c "import yaml; yaml.safe_load(open('${dna_file}'))" 2>/dev/null; then
                echo -e "  ${GREEN}✓${NC}  ${arch_name}/archetype.dna.yaml (YAML valide)"
            else
                echo -e "  ${RED}✗${NC}  ${arch_name}/archetype.dna.yaml — YAML invalide"
                errors=$((errors + 1))
            fi
        done
        # Stack DNA files
        local stack_dna_count
        stack_dna_count="$(find "${arch_base}/stack/agents" -name '*.dna.yaml' 2>/dev/null | wc -l | tr -d ' ')"
        echo -e "  ${GREEN}✓${NC}  stack agents DNA : ${stack_dna_count}/7 présents"
    fi
    echo ""

    # ─ 5. Git hooks ────────────────────────────────────────────────────
    echo "Git hooks Grimoire :"
    local git_hooks_dir
    git_hooks_dir="$(git rev-parse --git-dir 2>/dev/null)/hooks"
    local required_hooks=("pre-commit" "post-checkout" "prepare-commit-msg" "commit-msg" "post-commit" "pre-push")
    local hooks_ok=0
    if [[ -d "$git_hooks_dir" ]]; then
        for h in "${required_hooks[@]}"; do
            if [[ -f "$git_hooks_dir/$h" ]] && grep -q "Grimoire" "$git_hooks_dir/$h" 2>/dev/null; then
                echo -e "  ${GREEN}✓${NC}  $h"
                hooks_ok=$((hooks_ok + 1))
            else
                echo -e "  ${YELLOW}⚠${NC}  $h non installé"
                warnings=$((warnings + 1))
            fi
        done
        if [[ $hooks_ok -lt ${#required_hooks[@]} ]]; then
            info "Installez les hooks : $(basename "$0") hooks --install"
            if [[ "$do_fix" == true ]]; then
                bash "$0" hooks --install
                echo -e "  ${GREEN}✓${NC}  Hooks installés via --fix"
            fi
        fi
    else
        echo -e "  ${YELLOW}⚠${NC}  Pas dans un dépôt git — hooks non vérifiés"
        warnings=$((warnings + 1))
    fi
    echo ""

    # ─ 6. Résumé ───────────────────────────────────────────────────────
    if [[ $errors -eq 0 && $warnings -eq 0 ]]; then
        echo -e "${GREEN}✅  Tout est OK — Grimoire Custom Kit prêt à l'usage${NC}"
    elif [[ $errors -eq 0 ]]; then
        echo -e "${YELLOW}⚠  ${warnings} avertissement(s) — fonctionnel mais vérifiez les warnings ci-dessus${NC}"
        [[ "$do_fix" == false ]] && info "Relancez avec --fix pour corriger automatiquement les problèmes simples"
    else
        echo -e "${RED}❌  ${errors} erreur(s) critique(s) + ${warnings} avertissement(s) — installation incomplète${NC}"
        [[ "$do_fix" == false ]] && info "Relancez avec --fix pour tenter la correction automatique"
    fi
    exit 0
}

# ─── Validate DNA (BM-34) ─────────────────────────────────────────────────────
# Validation d'un ou plusieurs fichiers DNA archetype
# Usage: grimoire-init.sh validate [--dna path] [--all]
cmd_validate() {
    shift  # retirer "validate"
    local dna_file=""
    local validate_all=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dna)  dna_file="$2"; shift 2 ;;
            --all)  validate_all=true; shift ;;
            *) error "Option inconnue pour validate: $1" ;;
        esac
    done

    if ! python3 -c "import yaml" 2>/dev/null; then
        error "PyYAML requis : pip install pyyaml"
    fi

    validate_one_dna() {
        local f="$1"
        local result
        result="$(python3 - "$f" << 'PYEOF'
import sys, yaml, os
path = sys.argv[1]
try:
    with open(path) as fh:
        data = yaml.safe_load(fh)
except Exception as e:
    print(f"YAML_ERROR: {e}")
    sys.exit(1)
required = ["$schema", "id", "name", "version", "description"]
missing = [k for k in required if k not in data]
if missing:
    print(f"MISSING_FIELDS: {', '.join(missing)}")
    sys.exit(1)
# Vérifier le schéma
if data.get("$schema") != "grimoire-archetype-dna/v1":
    print(f"SCHEMA_MISMATCH: attendu grimoire-archetype-dna/v1, trouvé {data.get('$schema')}")
print(f"OK: id={data['id']} name='{data['name']}' version={data.get('version','?')}")
# Compter les AC et tools
ac_count = len(data.get('acceptance_criteria', []))
for t in data.get('traits', []):
    ac_count += len(t.get('acceptance_criteria', []))
tools_count = len(data.get('tools_required', []))
print(f"STATS: {ac_count} AC, {tools_count} tools_required")
PYEOF
        )"
        local exit_code=$?
        if [[ $exit_code -eq 0 ]]; then
            echo -e "  ${GREEN}✓${NC}  $(basename "$f")"
            echo "      $(echo "$result" | grep '^OK:' | sed 's/^OK: //')"
            echo "      $(echo "$result" | grep '^STATS:' | sed 's/^STATS: //')"
        else
            echo -e "  ${RED}✗${NC}  $(basename "$f") — ${result}"
            return 1
        fi
    }

    local errors=0

    if [[ "$validate_all" == true ]]; then
        echo -e "${CYAN}Validation de tous les fichiers DNA :${NC}"
        echo ""
        while IFS= read -r -d '' f; do
            validate_one_dna "$f" || errors=$((errors + 1))
        done < <(find "$SCRIPT_DIR/archetypes" -name "*.dna.yaml" -print0 2>/dev/null)
    elif [[ -n "$dna_file" ]]; then
        echo -e "${CYAN}Validation : ${dna_file}${NC}"
        echo ""
        validate_one_dna "$dna_file" || errors=$((errors + 1))
    else
        error "Spécifiez --dna chemin/archetype.dna.yaml ou --all"
    fi

    echo ""
    if [[ $errors -eq 0 ]]; then
        ok "Tous les fichiers DNA sont valides"
    else
        warn "${errors} fichier(s) DNA invalide(s) — corrigez les erreurs ci-dessus"
    fi
    exit 0
}

# ─── Changelog (BM-37) ─────────────────────────────────────────────────────────
# Génère CHANGELOG.md depuis les entrées [DECISION] du GRIMOIRE_TRACE
# Usage: grimoire-init.sh changelog [--output path] [--since YYYY-MM-DD]
cmd_changelog() {
    local TRACE_FILE="${TARGET_DIR:-$SCRIPT_DIR}/_grimoire-output/GRIMOIRE_TRACE.md"
    local output_file="${TARGET_DIR:-$SCRIPT_DIR}/CHANGELOG.md"
    local since_date=""

    shift  # retirer "changelog"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --output) output_file="$2"; shift 2 ;;
            --since)  since_date="$2"; shift 2 ;;
            *) error "Option inconnue pour changelog: $1" ;;
        esac
    done

    if [[ ! -f "$TRACE_FILE" ]]; then
        warn "Pas de GRIMOIRE_TRACE.md trouvé dans _grimoire-output/"
        info "Le trace est créé automatiquement lors des actions agents."
        exit 0
    fi

    local project_name
    project_name="$(basename "${TARGET_DIR:-$SCRIPT_DIR}")"
    local now
    now="$(date +%Y-%m-%d)"

    echo "# Changelog — ${project_name}" > "$output_file"
    echo "" >> "$output_file"
    echo "> Généré automatiquement depuis GRIMOIRE_TRACE.md le ${now}" >> "$output_file"
    echo "> Source des décisions : entrées [DECISION] et [REMEMBER:decisions]" >> "$output_file"
    echo "" >> "$output_file"

    # Extraire les dates uniques présentes dans la trace
    local dates
    dates="$(grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' "$TRACE_FILE" | sort -r | uniq)"

    local entries=0
    while IFS= read -r date; do
        [[ -n "$since_date" && "$date" < "$since_date" ]] && continue
        local day_decisions
        day_decisions="$(grep "^\[${date}" "$TRACE_FILE" | grep '\[DECISION\]\|\[REMEMBER:decisions\]')"
        [[ -z "$day_decisions" ]] && continue

        echo "## ${date}" >> "$output_file"
        echo "" >> "$output_file"
        while IFS= read -r line; do
            local agent
            agent="$(echo "$line" | grep -oP '\[\K[^\]]+(?=\])' | sed -n '2p')"
            local payload
            payload="$(echo "$line" | sed 's/^\[[^]]*\] \[[^]]*\] \[[^]]*\] //')"
            echo "- **${agent}** : ${payload}" >> "$output_file"
            ((entries++))
        done <<< "$day_decisions"
        echo "" >> "$output_file"
    done <<< "$dates"

    if [[ $entries -eq 0 ]]; then
        echo "_(Aucune décision enregistrée dans GRIMOIRE_TRACE.md)_" >> "$output_file"
    fi

    ok "CHANGELOG.md généré : ${output_file} (${entries} entrées)"
    exit 0
}

# ─── Resume (BM-26) ──────────────────────────────────────────────────────────
# Reprise d'un workflow interrompu depuis un checkpoint
# Usage: grimoire-init.sh resume [--checkpoint ID] [--list]
cmd_resume() {
    local RUNS_DIR="${TARGET_DIR:-$SCRIPT_DIR}/_grimoire-output/.runs"
    local checkpoint_id=""
    local action="resume"

    shift  # retirer "resume"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --checkpoint) checkpoint_id="$2"; shift 2 ;;
            --list)       action="list"; shift ;;
            *) error "Option inconnue pour resume: $1" ;;
        esac
    done

    case "$action" in
        list)
            echo -e "${CYAN}Checkpoints disponibles :${NC}"
            echo ""
            local found=false
            while IFS= read -r -d '' f; do
                local rid
                rid="$(basename "$(dirname "$f")")"
                local status
                status="$(grep '"status"' "$f" 2>/dev/null | sed 's/.*: *"\(.*\)".*/\1/' | head -1)"
                local step
                step="$(grep '"current_step"' "$f" 2>/dev/null | sed 's/[^0-9]//g' | head -1)"
                local total
                total="$(grep '"total_steps"' "$f" 2>/dev/null | sed 's/[^0-9]//g' | head -1)"
                local cid
                cid="$(grep '"checkpoint_id"' "$f" 2>/dev/null | sed 's/.*: *"\(.*\)".*/\1/' | head -1)"
                [[ -n "$cid" && "$cid" != "null" ]] && echo -e "  ${GREEN}●${NC} ${CYAN}${cid}${NC} — run: ${rid} | étape: ${step:-?}/${total:-?} | statut: ${status:-?}"
                found=true
            done < <(find "$RUNS_DIR" -name "state.json" -print0 2>/dev/null)
            $found || info "Aucun checkpoint trouvé"
            ;;
        resume)
            if [[ -n "$checkpoint_id" ]]; then
                local target
                target="$(grep -rl "\"${checkpoint_id}\"" "$RUNS_DIR" 2>/dev/null | grep 'state.json' | head -1)"
                if [[ -z "$target" ]]; then
                    error "Checkpoint '${checkpoint_id}' non trouvé. Listez avec : resume --list"
                fi
                local run_dir
                run_dir="$(dirname "$target")"
                ok "Checkpoint trouvé : ${run_dir}"
                info "Reprenez le workflow dans votre agent depuis l'étape indiquée dans :\n  ${target}"
            else
                # Trouver le run le plus récent non-terminé
                local latest
                latest="$(find "$RUNS_DIR" -name "state.json" -print0 2>/dev/null | \
                    xargs -0 grep -l '"status": "running"\|"status": "failed"' 2>/dev/null | \
                    sort | tail -1)"
                if [[ -z "$latest" ]]; then
                    info "Aucun run non-terminé trouvé dans ${RUNS_DIR}/"
                    exit 0
                fi
                local run_dir
                run_dir="$(dirname "$latest")"
                ok "Run non-terminé trouvé : $(basename "$run_dir")"
                cat "$latest"
            fi
            ;;
    esac
    exit 0
}

# ─── Trace (BM-28) ───────────────────────────────────────────────────────────
# Gestion du GRIMOIRE_TRACE.md — audit trail append-only
# Usage: grimoire-init.sh trace [--tail N] [--agent X] [--type X] [--archive] [--reset --confirm]
cmd_trace() {
    local TRACE_FILE="${TARGET_DIR:-$SCRIPT_DIR}/_grimoire-output/GRIMOIRE_TRACE.md"
    local action="view"
    local filter_agent=""
    local filter_type=""
    local tail_n=30

    shift  # retirer "trace"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tail)    tail_n="$2"; action="tail"; shift 2 ;;
            --agent)   filter_agent="$2"; action="filter"; shift 2 ;;
            --type)    filter_type="$2"; action="filter"; shift 2 ;;
            --archive) action="archive"; shift ;;
            --reset)   action="reset"; shift ;;
            --confirm) shift ;;  # flag de confirmation
            *) error "Option inconnue pour trace: $1" ;;
        esac
    done

    if [[ ! -f "$TRACE_FILE" ]]; then
        info "Pas de trace trouvée dans ${TRACE_FILE}"
        info "La trace est créée automatiquement lors des actions agents."
        exit 0
    fi

    case "$action" in
        tail|view)
            echo -e "${CYAN}Grimoire Trace — dernières ${tail_n} entrées :${NC}"
            echo ""
            tail -"${tail_n}" "$TRACE_FILE"
            ;;
        filter)
            echo -e "${CYAN}Grimoire Trace — filtre: agent='${filter_agent}' type='${filter_type}' :${NC}"
            echo ""
            local pattern=""
            [[ -n "$filter_agent" ]] && pattern="\[${filter_agent}"
            [[ -n "$filter_type" ]] && pattern="${pattern}.*\[${filter_type}"
            [[ -z "$pattern" ]] && pattern="."
            grep -E "${pattern}" "$TRACE_FILE" || info "Aucune entrée trouvée"
            ;;
        archive)
            local arch_path
            arch_path="${TRACE_FILE%.md}-$(date +%Y%m%d).md"
            cp "$TRACE_FILE" "$arch_path"
            echo "" > "$TRACE_FILE"
            ok "Trace archivée dans : ${arch_path}"
            ;;
        reset)
            warn "Suppression définitive de GRIMOIRE_TRACE.md"
            echo "" > "$TRACE_FILE"
            ok "Trace réinitialisée"
            ;;
    esac
    exit 0
}

# ─── Reset (remet l'installation à zero) ─────────────────────────────────────
# Usage: grimoire-init.sh reset [--hard] [--dry-run] [--yes]
cmd_reset() {
    shift  # retirer "reset"
    local hard=false
    local dry_run=false
    local yes=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --hard)     hard=true; shift ;;
            --dry-run)  dry_run=true; shift ;;
            --yes|-y)   yes=true; shift ;;
            --help)
                echo -e "${CYAN}Usage: $(basename "$0") reset [--hard] [--dry-run] [--yes]${NC}"
                echo ""
                echo "  Remet l'installation Grimoire à un état propre."
                echo ""
                echo "  Mode par défaut (soft) :"
                echo "    • Réinstalle le framework (agent-base, scripts, templates)"
                echo "    • Réinstalle les meta agents (Atlas, Sentinel, Mnemo)"
                echo "    • Préserve _memory/ (learnings, sessions, shared-context)"
                echo "    • Préserve les agents custom de l'archétype"
                echo "    • Préserve project-context.yaml"
                echo ""
                echo "  --hard      Supprime tout _grimoire/ et recrée depuis zéro"
                echo "              (ATTENTION: perte des learnings et de la mémoire)"
                echo "  --dry-run   Afficher les actions sans les exécuter"
                echo "  --yes       Ne pas demander de confirmation"
                exit 0
                ;;
            *)  error "Option reset inconnue: $1" ;;
        esac
    done

    local grimoire_dir="${TARGET_DIR}/_grimoire"

    if [[ ! -d "$grimoire_dir/_config/custom" ]]; then
        error "Pas de projet Grimoire détecté dans $(pwd)"
    fi

    echo -e "${CYAN}╔═══════════════════════════════════════════════════════${NC}"
    if $hard; then
        echo -e "${CYAN}║  Grimoire — HARD Reset${NC}"
    else
        echo -e "${CYAN}║  Grimoire — Soft Reset${NC}"
    fi
    echo -e "${CYAN}╙═══════════════════════════════════════════════════════${NC}"
    echo ""

    if $hard; then
        echo -e "  ${RED}⚠  HARD RESET : tout _grimoire/ sera supprimé et recréé${NC}"
        echo -e "  ${RED}   Les learnings, sessions et mémoire seront PERDUS${NC}"
    else
        echo -e "  ${GREEN}Soft reset : framework + meta agents réinstallés${NC}"
        echo -e "  ${GREEN}Mémoire et agents custom préservés${NC}"
    fi
    echo ""

    if [[ "$yes" != true && "$dry_run" != true ]]; then
        read -p "Continuer ? (y/N) " -n 1 -r
        echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    fi

    if $hard; then
        # Hard reset: supprimer et recréer la structure
        if [[ "$dry_run" == true ]]; then
            echo -e "  ${RED}✗${NC}  rm -rf $grimoire_dir"
            echo -e "  ${GREEN}+${NC}  Recréation de la structure de base"
            echo ""
            echo -e "  ${CYAN}Dry-run — aucune modification effectuée${NC}"
        else
            rm -rf "$grimoire_dir"
            mkdir -p "$grimoire_dir/_config/custom/agents"
            mkdir -p "$grimoire_dir/_config/custom/prompt-templates"
            mkdir -p "$grimoire_dir/_config/custom/workflows"
            mkdir -p "$grimoire_dir/_memory/agent-learnings"
            mkdir -p "$grimoire_dir/_memory/session-summaries"
            mkdir -p "$grimoire_dir/_memory/archives"
            mkdir -p "$grimoire_dir/_memory/backends"

            # Réinstaller le framework
            cp "$SCRIPT_DIR/framework/agent-base.md" "$grimoire_dir/_config/custom/agent-base.md"
            cp "$SCRIPT_DIR/framework/cc-verify.sh" "$grimoire_dir/_config/custom/cc-verify.sh"
            chmod +x "$grimoire_dir/_config/custom/cc-verify.sh"
            cp "$SCRIPT_DIR/framework/sil-collect.sh" "$grimoire_dir/_config/custom/sil-collect.sh"
            chmod +x "$grimoire_dir/_config/custom/sil-collect.sh"
            cp "$SCRIPT_DIR/framework/memory/maintenance.py" "$grimoire_dir/_memory/maintenance.py"
            cp "$SCRIPT_DIR/framework/memory/mem0-bridge.py" "$grimoire_dir/_memory/mem0-bridge.py"
            cp "$SCRIPT_DIR/framework/memory/session-save.py" "$grimoire_dir/_memory/session-save.py"
            cp -r "$SCRIPT_DIR/framework/memory/backends/"* "$grimoire_dir/_memory/backends/" 2>/dev/null || true
            cp -r "$SCRIPT_DIR/framework/prompt-templates/"* "$grimoire_dir/_config/custom/prompt-templates/" 2>/dev/null || true
            cp -r "$SCRIPT_DIR/framework/workflows/"* "$grimoire_dir/_config/custom/workflows/" 2>/dev/null || true

            # Meta agents
            cp "$SCRIPT_DIR/archetypes/meta/agents/"*.md "$grimoire_dir/_config/custom/agents/" 2>/dev/null || true

            # Fichiers mémoire vides
            touch "$grimoire_dir/_memory/decisions-log.md"
            touch "$grimoire_dir/_memory/handoff-log.md"
            touch "$grimoire_dir/_memory/agent-changelog.md"
            echo '[]' > "$grimoire_dir/_memory/memories.json"
            touch "$grimoire_dir/_memory/activity.jsonl"

            ok "Hard reset terminé — _grimoire/ recréé depuis zéro"
            info "Relancez 'grimoire setup --sync' pour reconfigurer"
        fi
    else
        # Soft reset: réinstaller framework sans toucher à _memory/ ni agents custom
        local reset_count=0
        _reset_file() {
            local src="$1" dst="$2" label="$3"
            [[ ! -f "$src" ]] && return
            if [[ "$dry_run" == true ]]; then
                echo -e "  ${GREEN}↻${NC}  $label"
            else
                mkdir -p "$(dirname "$dst")"
                cp "$src" "$dst"
            fi
            reset_count=$((reset_count + 1))
        }

        echo -e "${YELLOW}▶ Framework core${NC}"
        _reset_file "$SCRIPT_DIR/framework/agent-base.md" \
                    "$grimoire_dir/_config/custom/agent-base.md" "agent-base.md"
        _reset_file "$SCRIPT_DIR/framework/cc-verify.sh" \
                    "$grimoire_dir/_config/custom/cc-verify.sh" "cc-verify.sh"
        _reset_file "$SCRIPT_DIR/framework/sil-collect.sh" \
                    "$grimoire_dir/_config/custom/sil-collect.sh" "sil-collect.sh"

        echo -e "${YELLOW}▶ Memory scripts${NC}"
        _reset_file "$SCRIPT_DIR/framework/memory/maintenance.py" \
                    "$grimoire_dir/_memory/maintenance.py" "maintenance.py"
        _reset_file "$SCRIPT_DIR/framework/memory/mem0-bridge.py" \
                    "$grimoire_dir/_memory/mem0-bridge.py" "mem0-bridge.py"
        _reset_file "$SCRIPT_DIR/framework/memory/session-save.py" \
                    "$grimoire_dir/_memory/session-save.py" "session-save.py"

        echo -e "${YELLOW}▶ Meta agents${NC}"
        if [[ -d "$SCRIPT_DIR/archetypes/meta/agents" ]]; then
            for meta_agent in "$SCRIPT_DIR/archetypes/meta/agents/"*.md; do
                [[ -f "$meta_agent" ]] || continue
                local aname
                aname="$(basename "$meta_agent")"
                _reset_file "$meta_agent" \
                            "$grimoire_dir/_config/custom/agents/$aname" "meta/$aname"
            done
        fi

        echo -e "${YELLOW}▶ Templates & Workflows${NC}"
        for tpl in "$SCRIPT_DIR/framework/prompt-templates/"*; do
            [[ -f "$tpl" ]] || continue
            _reset_file "$tpl" \
                        "$grimoire_dir/_config/custom/prompt-templates/$(basename "$tpl")" \
                        "prompt-templates/$(basename "$tpl")"
        done
        for wf in "$SCRIPT_DIR/framework/workflows/"*; do
            [[ -f "$wf" ]] || continue
            _reset_file "$wf" \
                        "$grimoire_dir/_config/custom/workflows/$(basename "$wf")" \
                        "workflows/$(basename "$wf")"
        done

        echo ""
        if [[ "$dry_run" == true ]]; then
            echo -e "  ${CYAN}Dry-run : ${reset_count} fichier(s) seraient réinitialisés${NC}"
        else
            [[ -f "$grimoire_dir/_config/custom/cc-verify.sh" ]] && chmod +x "$grimoire_dir/_config/custom/cc-verify.sh"
            [[ -f "$grimoire_dir/_config/custom/sil-collect.sh" ]] && chmod +x "$grimoire_dir/_config/custom/sil-collect.sh"
            ok "Soft reset terminé : ${reset_count} fichier(s) réinitialisés"
            info "Mémoire et agents custom préservés"
        fi
    fi
    exit 0
}

# ─── Uninstall (suppression complète) ────────────────────────────────────────
# Usage: grimoire-init.sh uninstall [--yes] [--keep-config]
cmd_uninstall() {
    shift  # retirer "uninstall"
    local yes=false
    local keep_config=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --yes|-y)       yes=true; shift ;;
            --keep-config)  keep_config=true; shift ;;
            --help)
                echo -e "${CYAN}Usage: $(basename "$0") uninstall [--yes] [--keep-config]${NC}"
                echo ""
                echo "  Supprime complètement l'installation Grimoire du projet."
                echo ""
                echo "  Fichiers supprimés :"
                echo "    • _grimoire/           (agents, mémoire, config)"
                echo "    • _grimoire-output/    (artefacts, sessions, traces)"
                echo "    • .github/copilot-instructions.md (si généré par Grimoire)"
                echo ""
                echo "  --keep-config  Préserver project-context.yaml"
                echo "  --yes          Ne pas demander de confirmation"
                exit 0
                ;;
            *)  error "Option uninstall inconnue: $1" ;;
        esac
    done

    local grimoire_dir="${TARGET_DIR}/_grimoire"
    local output_dir="${TARGET_DIR}/_grimoire-output"
    local copilot_file="${TARGET_DIR}/.github/copilot-instructions.md"
    local project_ctx="${TARGET_DIR}/project-context.yaml"

    if [[ ! -d "$grimoire_dir" ]]; then
        error "Pas de projet Grimoire détecté dans $(pwd)"
    fi

    echo -e "${RED}╔═══════════════════════════════════════════════════════${NC}"
    echo -e "${RED}║  Grimoire — Désinstallation${NC}"
    echo -e "${RED}╙═══════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  Les éléments suivants seront supprimés :"
    echo ""

    local items=()
    if [[ -d "$grimoire_dir" ]]; then
        local file_count
        file_count=$(find "$grimoire_dir" -type f | wc -l)
        echo -e "  ${RED}✗${NC}  _grimoire/              ($file_count fichiers)"
        items+=("$grimoire_dir")
    fi
    if [[ -d "$output_dir" ]]; then
        local out_count
        out_count=$(find "$output_dir" -type f | wc -l)
        echo -e "  ${RED}✗${NC}  _grimoire-output/       ($out_count fichiers)"
        items+=("$output_dir")
    fi
    if [[ -f "$copilot_file" ]] && grep -q "Grimoire Custom Kit" "$copilot_file" 2>/dev/null; then
        echo -e "  ${RED}✗${NC}  .github/copilot-instructions.md"
        items+=("$copilot_file")
    fi
    if [[ "$keep_config" == false && -f "$project_ctx" ]] && grep -q "grimoire_kit_version" "$project_ctx" 2>/dev/null; then
        echo -e "  ${RED}✗${NC}  project-context.yaml"
        items+=("$project_ctx")
    elif [[ -f "$project_ctx" ]]; then
        echo -e "  ${GREEN}✓${NC}  project-context.yaml   (préservé)"
    fi

    # Git hooks
    local git_hooks_dir
    git_hooks_dir="$(git -C "$TARGET_DIR" rev-parse --git-dir 2>/dev/null)/hooks" 2>/dev/null || true
    if [[ -f "${git_hooks_dir:-/dev/null}/pre-commit" ]] && grep -q "Grimoire" "${git_hooks_dir}/pre-commit" 2>/dev/null; then
        echo -e "  ${RED}✗${NC}  git pre-commit hook (Grimoire CC)"
        items+=("${git_hooks_dir}/pre-commit")
    fi
    echo ""

    if [[ ${#items[@]} -eq 0 ]]; then
        info "Rien à supprimer"
        exit 0
    fi

    if [[ "$yes" != true ]]; then
        echo -e "  ${RED}⚠  Cette action est IRRÉVERSIBLE${NC}"
        read -p "  Confirmer la suppression ? (tapez 'oui' pour confirmer) " -r
        [[ "$REPLY" != "oui" ]] && { info "Annulé"; exit 1; }
    fi

    for item in "${items[@]}"; do
        rm -rf "$item"
    done

    # Nettoyer .github/ si vide
    if [[ -d "${TARGET_DIR}/.github" ]]; then
        rmdir "${TARGET_DIR}/.github" 2>/dev/null || true
    fi

    echo ""
    ok "Grimoire désinstallé avec succès"
    info "Le projet est revenu à un état pré-Grimoire"
    if [[ "$keep_config" == true ]]; then
        info "project-context.yaml a été préservé"
    fi
    exit 0
}

# ─── Quick Update (mise à jour rapide framework only) ────────────────────────
# Usage: grimoire-init.sh quick-update [--dry-run]
cmd_quickupdate() {
    shift  # retirer "quick-update"
    local dry_run=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)  dry_run=true; shift ;;
            --help)
                echo -e "${CYAN}Usage: $(basename "$0") quick-update [--dry-run]${NC}"
                echo ""
                echo "  Mise à jour rapide du framework sans prompts interactifs."
                echo "  Équivalent à 'upgrade' mais sans vérification de version"
                echo "  et avec un récapitulatif compact."
                echo ""
                echo "  Fichiers mis à jour :"
                echo "    • agent-base.md, cc-verify.sh, sil-collect.sh"
                echo "    • Scripts mémoire (maintenance, mem0-bridge, session-save)"
                echo "    • Meta agents (Atlas, Sentinel, Mnemo)"
                echo "    • Templates et workflows"
                echo ""
                echo "  Fichiers préservés :"
                echo "    • Agents custom de l'archétype"
                echo "    • _memory/ (learnings, sessions)"
                echo "    • project-context.yaml"
                echo ""
                echo "  --dry-run   Afficher les changements sans les appliquer"
                exit 0
                ;;
            *)  error "Option quick-update inconnue: $1" ;;
        esac
    done

    local grimoire_dir="${TARGET_DIR}/_grimoire"
    local project_ctx="${TARGET_DIR}/project-context.yaml"

    if [[ ! -d "$grimoire_dir/_config/custom" ]]; then
        error "Pas de projet Grimoire détecté dans $(pwd)"
    fi

    local installed_version="unknown"
    if [[ -f "$project_ctx" ]]; then
        installed_version="$(grep 'grimoire_kit_version:' "$project_ctx" 2>/dev/null | sed 's/.*: *//' | tr -d '"' || echo "unknown")"
    fi

    echo -e "  ${CYAN}⚡ Quick Update${NC} — ${installed_version} → ${GRIMOIRE_KIT_VERSION}"
    echo ""

    local updated=0
    local skipped=0

    _qu_file() {
        local src="$1" dst="$2"
        [[ ! -f "$src" ]] && return
        if [[ ! -f "$dst" ]]; then
            [[ "$dry_run" == false ]] && { mkdir -p "$(dirname "$dst")"; cp "$src" "$dst"; }
            updated=$((updated + 1)); return
        fi
        if diff -q "$src" "$dst" &>/dev/null; then
            skipped=$((skipped + 1)); return
        fi
        [[ "$dry_run" == false ]] && cp "$src" "$dst"
        updated=$((updated + 1))
    }

    # Framework core
    _qu_file "$SCRIPT_DIR/framework/agent-base.md" "$grimoire_dir/_config/custom/agent-base.md"
    _qu_file "$SCRIPT_DIR/framework/cc-verify.sh" "$grimoire_dir/_config/custom/cc-verify.sh"
    _qu_file "$SCRIPT_DIR/framework/sil-collect.sh" "$grimoire_dir/_config/custom/sil-collect.sh"

    # Memory scripts
    _qu_file "$SCRIPT_DIR/framework/memory/maintenance.py" "$grimoire_dir/_memory/maintenance.py"
    _qu_file "$SCRIPT_DIR/framework/memory/mem0-bridge.py" "$grimoire_dir/_memory/mem0-bridge.py"
    _qu_file "$SCRIPT_DIR/framework/memory/session-save.py" "$grimoire_dir/_memory/session-save.py"

    # Backends
    if [[ -d "$SCRIPT_DIR/framework/memory/backends" ]]; then
        for bf in "$SCRIPT_DIR/framework/memory/backends/"*.py; do
            [[ -f "$bf" ]] || continue
            _qu_file "$bf" "$grimoire_dir/_memory/backends/$(basename "$bf")"
        done
    fi

    # Meta agents
    if [[ -d "$SCRIPT_DIR/archetypes/meta/agents" ]]; then
        for ma in "$SCRIPT_DIR/archetypes/meta/agents/"*.md; do
            [[ -f "$ma" ]] || continue
            _qu_file "$ma" "$grimoire_dir/_config/custom/agents/$(basename "$ma")"
        done
    fi

    # Templates & workflows
    for tpl in "$SCRIPT_DIR/framework/prompt-templates/"*; do
        [[ -f "$tpl" ]] || continue
        _qu_file "$tpl" "$grimoire_dir/_config/custom/prompt-templates/$(basename "$tpl")"
    done
    for wf in "$SCRIPT_DIR/framework/workflows/"*; do
        [[ -f "$wf" ]] || continue
        _qu_file "$wf" "$grimoire_dir/_config/custom/workflows/$(basename "$wf")"
    done

    # Executable bits
    if [[ "$dry_run" == false ]]; then
        [[ -f "$grimoire_dir/_config/custom/cc-verify.sh" ]] && chmod +x "$grimoire_dir/_config/custom/cc-verify.sh"
        [[ -f "$grimoire_dir/_config/custom/sil-collect.sh" ]] && chmod +x "$grimoire_dir/_config/custom/sil-collect.sh"

        # Update version in project-context.yaml
        if [[ -f "$project_ctx" ]]; then
            if grep -q 'grimoire_kit_version:' "$project_ctx"; then
                sed -i "s/grimoire_kit_version:.*/grimoire_kit_version: \"$GRIMOIRE_KIT_VERSION\"/" "$project_ctx"
            fi
        fi
    fi

    echo -e "  ${GREEN}✅ ${updated} mis à jour${NC}, ${skipped} inchangé(s)"
    if [[ "$dry_run" == true ]]; then
        info "Dry-run — aucune modification effectuée"
    fi
    exit 0
}

# ─── Dispatch des sous-commandes ──────────────────────────────────────────────────
if [[ "${1:-}" == "reset" ]]; then
    cmd_reset "$@"
fi
if [[ "${1:-}" == "uninstall" ]]; then
    cmd_uninstall "$@"
fi
if [[ "${1:-}" == "quick-update" ]]; then
    cmd_quickupdate "$@"
fi
if [[ "${1:-}" == "session-branch" ]]; then
    cmd_session_branch "$@"
fi
if [[ "${1:-}" == "install" ]]; then
    cmd_install "$@"
fi
if [[ "${1:-}" == "resume" ]]; then
    cmd_resume "$@"
fi
if [[ "${1:-}" == "trace" ]]; then
    cmd_trace "$@"
fi
if [[ "${1:-}" == "doctor" ]]; then
    cmd_doctor "$@"
fi
if [[ "${1:-}" == "validate" ]]; then
    cmd_validate "$@"
fi
if [[ "${1:-}" == "changelog" ]]; then
    cmd_changelog "$@"
fi
if [[ "${1:-}" == "hooks" ]]; then
    cmd_hooks "$@"
fi
if [[ "${1:-}" == "bench" ]]; then
    cmd_bench "$@"
fi
if [[ "${1:-}" == "forge" ]]; then
    cmd_forge "$@"
fi
if [[ "${1:-}" == "guard" ]]; then
    cmd_guard "$@"
fi
if [[ "${1:-}" == "evolve" ]]; then
    cmd_evolve "$@"
fi
if [[ "${1:-}" == "upgrade" ]]; then
    cmd_upgrade "$@"
fi
if [[ "${1:-}" == "dream" ]]; then
    cmd_dream "$@"
fi
if [[ "${1:-}" == "consensus" ]]; then
    cmd_consensus "$@"
fi
if [[ "${1:-}" == "antifragile" ]]; then
    cmd_antifragile "$@"
fi
if [[ "${1:-}" == "reasoning" ]]; then
    cmd_reasoning "$@"
fi
if [[ "${1:-}" == "migrate" ]]; then
    cmd_migrate "$@"
fi
if [[ "${1:-}" == "darwinism" ]]; then
    cmd_darwinism "$@"
fi
if [[ "${1:-}" == "stigmergy" ]]; then
    cmd_stigmergy "$@"
fi
if [[ "${1:-}" == "status" ]]; then
    cmd_status "$@"
fi
if [[ "${1:-}" == "memory-lint" ]]; then
    cmd_memorylint "$@"
fi
if [[ "${1:-}" == "nso" ]]; then
    cmd_nso "$@"
fi
if [[ "${1:-}" == "schema-validate" ]]; then
    cmd_schemav "$@"
fi
if [[ "${1:-}" == "auto-doc" ]]; then
    cmd_autodoc "$@"
fi

# ─── Parsing arguments ──────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)     PROJECT_NAME="$2"; shift 2 ;;
        --user)     USER_NAME="$2"; shift 2 ;;
        --lang)     LANGUAGE="$2"; shift 2 ;;
        --archetype) ARCHETYPE="$2"; shift 2 ;;
        --target)   TARGET_DIR="$2"; shift 2 ;;
        --auto)     AUTO_DETECT=true; shift ;;
        --memory)   MEMORY_BACKEND="$2"; shift 2 ;;
        --force)    FORCE=true; shift ;;
        --version)  echo "Grimoire Custom Kit v${GRIMOIRE_KIT_VERSION}"; exit 0 ;;
        --help)     usage ;;
        *)          error "Option inconnue: $1. Utilisez --help." ;;
    esac
done

# ─── Détection automatique du stack ─────────────────────────────────────────
detect_stack() {
    local dir="${1:-$(pwd)}"
    local detected=()

    # Go
    [[ -f "$dir/go.mod" ]] && detected+=("go")

    # Terraform (racine ou sous-dossiers profonds, hors .terraform/)
    if find "$dir" -maxdepth 7 -name '*.tf' \
         -not -path '*/.terraform/*' \
         -not -path '*/node_modules/*' \
         -print -quit 2>/dev/null | grep -q .; then
        detected+=("terraform")
    fi

    # Frontend (React/Vue/Next/Vite) — chercher package.json jusqu'à depth 3
    if find "$dir" -maxdepth 3 -name 'package.json' \
         -not -path '*/node_modules/*' \
         -exec grep -qE '"(react|vue|next|vite)"' {} \; \
         -print -quit 2>/dev/null | grep -q .; then
        detected+=("frontend")
    # Node sans framework frontend
    elif [[ -f "$dir/package.json" ]] && \
         ! grep -qE '"(react|vue|next|vite)"' "$dir/package.json" 2>/dev/null; then
        detected+=("node")
    fi

    # Ansible
    if [[ -d "$dir/ansible" ]] || \
       find "$dir" -maxdepth 3 -name 'playbook*.yml' -print -quit 2>/dev/null | grep -q . || \
       find "$dir" -maxdepth 3 -name 'site.yml' -print -quit 2>/dev/null | grep -q . || \
       find "$dir" -maxdepth 3 -name 'ansible.cfg' -print -quit 2>/dev/null | grep -q .; then
        detected+=("ansible")
    fi

    # Kubernetes (manifests avec kind: Deployment/StatefulSet/Service)
    if [[ -d "$dir/k8s" ]] || [[ -d "$dir/kubernetes" ]] || \
       find "$dir" -maxdepth 4 -name '*.yaml' \
         -not -path '*/node_modules/*' \
         -not -path '*/.terraform/*' \
         -exec grep -qlE '^kind: (Deployment|StatefulSet|DaemonSet|Service|Ingress)' {} \; \
         -print -quit 2>/dev/null | grep -q .; then
        detected+=("k8s")
    fi

    # Python
    if [[ -f "$dir/requirements.txt" ]] || [[ -f "$dir/pyproject.toml" ]] || \
       find "$dir" -maxdepth 2 -name 'requirements*.txt' -print -quit 2>/dev/null | grep -q .; then
        detected+=("python")
    fi

    # Docker
    if [[ -f "$dir/Dockerfile" ]] || \
       find "$dir" -maxdepth 3 -name 'docker-compose*.yml' -print -quit 2>/dev/null | grep -q . || \
       find "$dir" -maxdepth 3 -name 'Dockerfile*' -print -quit 2>/dev/null | grep -q .; then
        detected+=("docker")
    fi

    echo "${detected[*]:-unknown}"
}

auto_select_archetype() {
    local stacks="$1"
    # infra-ops si terraform ou k8s ou ansible
    if echo "$stacks" | grep -qE '(terraform|k8s|ansible)'; then
        echo "infra-ops"
    # web-app si frontend + (go|node|python)
    elif echo "$stacks" | grep -qE 'frontend' && echo "$stacks" | grep -qE '(go|node|python)'; then
        echo "web-app"
    else
        echo "minimal"
    fi
}

# ─── Déploiement des agents stack (Modal Team Engine) ───────────────────────
# Copie les agents spécialisés correspondant aux stacks détectés dans le
# répertoire _grimoire/_config/custom/agents/ du projet cible.
deploy_stack_agents() {
    local stacks="$1"
    local target_agents_dir="$2"
    local stack_agents_dir="$SCRIPT_DIR/archetypes/stack/agents"
    local deployed=()

    [[ ! -d "$stack_agents_dir" ]] && { warn "archetypes/stack/agents/ non trouvé — agents stack ignorés"; return 0; }

    declare -A STACK_MAP=(
        ["go"]="go-expert.md"
        ["frontend"]="typescript-expert.md"
        ["node"]="typescript-expert.md"
        ["python"]="python-expert.md"
        ["docker"]="docker-expert.md"
        ["terraform"]="terraform-expert.md"
        ["k8s"]="k8s-expert.md"
        ["ansible"]="ansible-expert.md"
    )

    for stack in $stacks; do
        agent_file="${STACK_MAP[$stack]:-}"
        [[ -z "$agent_file" ]] && continue
        src="$stack_agents_dir/$agent_file"
        dst="$target_agents_dir/$agent_file"
        if [[ -f "$src" ]] && [[ ! -f "$dst" ]]; then
            cp "$src" "$dst"
            deployed+=("$agent_file")
        fi
    done

    if [[ ${#deployed[@]} -gt 0 ]]; then
        ok "Agents stack déployés : ${deployed[*]}"
    else
        info "Aucun agent stack supplémentaire (déjà présents ou stacks non reconnus)"
    fi
}

# ─── Détection automatique du backend mémoire ────────────────────────────────
detect_memory_backend() {
    # Qdrant local (docker)
    if curl -sf --connect-timeout 2 --max-time 3 http://localhost:6333/healthz &>/dev/null 2>&1; then
        echo "qdrant-local"
        return
    fi
    # Ollama
    if curl -sf --connect-timeout 2 --max-time 3 http://localhost:11434/api/tags &>/dev/null 2>&1; then
        echo "ollama"
        return
    fi
    # Fallback : local JSON
    echo "local"
}

# ─── Déploiement des agents features selon le backend mémoire ────────────────
# Copie les agents features (ex: vectus pour vector-memory) si le backend le
# supporte. Appelé uniquement pendant l'init.
deploy_feature_agents() {
    local backend="$1"
    local target_agents_dir="$2"
    local features_dir="$SCRIPT_DIR/archetypes/features"

    [[ ! -d "$features_dir" ]] && return 0

    # vector-memory : déployer Vectus si backend sémantique
    case "$backend" in
        qdrant-*|ollama|semantic)
            local vectus="$features_dir/vector-memory/vectus.md"
            if [[ -f "$vectus" ]] && [[ ! -f "$target_agents_dir/vectus.md" ]]; then
                cp "$vectus" "$target_agents_dir/vectus.md"
                ok "Agent feature déployé : vectus.md (vector-memory)"
            fi
            ;;
    esac
}

# ─── Validation ──────────────────────────────────────────────────────────────
[[ -z "$PROJECT_NAME" ]] && error "--name est requis"
[[ -z "$USER_NAME" ]]    && error "--user est requis"
[[ ! -d "$SCRIPT_DIR/framework" ]] && error "Le kit Grimoire n'est pas trouvé dans $SCRIPT_DIR"

# Auto-détection du stack si --auto
if $AUTO_DETECT; then
    info "Analyse automatique du stack..."
    DETECTED_STACKS=$(detect_stack "$TARGET_DIR")
    AUTO_ARCHETYPE=$(auto_select_archetype "$DETECTED_STACKS")
    [[ "$ARCHETYPE" == "minimal" ]] && ARCHETYPE="$AUTO_ARCHETYPE"
    ok "Stack détecté : ${DETECTED_STACKS:-aucun} → archétype : $ARCHETYPE"
fi

# Auto-détection du backend mémoire si "auto"
if [[ "$MEMORY_BACKEND" == "auto" ]]; then
    info "Détection du backend mémoire..."
    MEMORY_BACKEND=$(detect_memory_backend)
    ok "Backend mémoire détecté : $MEMORY_BACKEND"
fi

ARCHETYPE_DIR="$SCRIPT_DIR/archetypes/$ARCHETYPE"
[[ ! -d "$ARCHETYPE_DIR" ]] && error "Archétype '$ARCHETYPE' non trouvé. Disponibles: $(ls "$SCRIPT_DIR/archetypes/")"

# ─── Vérification cible ─────────────────────────────────────────────────────
GRIMOIRE_DIR="$TARGET_DIR/_grimoire"
if [[ -d "$GRIMOIRE_DIR/_config/custom" ]]; then
    if $FORCE; then
        warn "--force : écrasement de l'installation existante dans $TARGET_DIR"
    else
        warn "Un dossier _grimoire/custom existe déjà dans $TARGET_DIR"
        read -p "Continuer et écraser ? (y/N) " -n 1 -r
        echo
        [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# INSTALLATION
# ═══════════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${CYAN}🤖 Grimoire Custom Kit v${GRIMOIRE_KIT_VERSION} — Initialisation${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════${NC}"
echo -e "  Projet:     ${GREEN}$PROJECT_NAME${NC}"
echo -e "  Utilisateur: ${GREEN}$USER_NAME${NC}"
echo -e "  Langue:     ${GREEN}$LANGUAGE${NC}"
echo -e "  Archétype:  ${GREEN}$ARCHETYPE${NC}"
echo -e "  Mémoire:    ${GREEN}$MEMORY_BACKEND${NC}"
echo -e "  Cible:      ${GREEN}$TARGET_DIR${NC}"
echo ""

# ─── 1. Créer la structure de base ──────────────────────────────────────────
info "Création de la structure _grimoire..."

mkdir -p "$GRIMOIRE_DIR/_config/custom/agents"
mkdir -p "$GRIMOIRE_DIR/_config/custom/prompt-templates"
mkdir -p "$GRIMOIRE_DIR/_config/custom/workflows"
mkdir -p "$GRIMOIRE_DIR/_memory/agent-learnings"
mkdir -p "$GRIMOIRE_DIR/_memory/session-summaries"
mkdir -p "$GRIMOIRE_DIR/_memory/archives"

# ─── 2. Copier le framework ────────────────────────────────────────────────
info "Installation du framework..."

# Agent base protocol
cp "$SCRIPT_DIR/framework/agent-base.md" "$GRIMOIRE_DIR/_config/custom/agent-base.md"

# Completion Contract verifier
cp "$SCRIPT_DIR/framework/cc-verify.sh" "$GRIMOIRE_DIR/_config/custom/cc-verify.sh"
chmod +x "$GRIMOIRE_DIR/_config/custom/cc-verify.sh"

# Self-Improvement Loop collector
cp "$SCRIPT_DIR/framework/sil-collect.sh" "$GRIMOIRE_DIR/_config/custom/sil-collect.sh"
chmod +x "$GRIMOIRE_DIR/_config/custom/sil-collect.sh"

# Scripts mémoire
cp "$SCRIPT_DIR/framework/memory/maintenance.py" "$GRIMOIRE_DIR/_memory/maintenance.py"
cp "$SCRIPT_DIR/framework/memory/mem0-bridge.py" "$GRIMOIRE_DIR/_memory/mem0-bridge.py"
cp "$SCRIPT_DIR/framework/memory/session-save.py" "$GRIMOIRE_DIR/_memory/session-save.py"

# Backend memory — copier les backends/
mkdir -p "$GRIMOIRE_DIR/_memory/backends"
cp -r "$SCRIPT_DIR/framework/memory/backends/"* "$GRIMOIRE_DIR/_memory/backends/"

# Choisir le bon requirements selon le backend
case "$MEMORY_BACKEND" in
    ollama)          REQS_SRC="$SCRIPT_DIR/framework/memory/requirements/requirements-ollama.txt" ;;
    qdrant-*|semantic) REQS_SRC="$SCRIPT_DIR/framework/memory/requirements/requirements-qdrant.txt" ;;
    *)               REQS_SRC="$SCRIPT_DIR/framework/memory/requirements/requirements-minimal.txt" ;;
esac
cp "${REQS_SRC:-$SCRIPT_DIR/framework/memory/requirements/requirements-minimal.txt}" "$GRIMOIRE_DIR/_memory/requirements.txt"

# Copier aussi le requirements original complet (référence)
cp "$SCRIPT_DIR/framework/memory/requirements.txt" "$GRIMOIRE_DIR/_memory/requirements-full.txt"

# Prompt templates
cp -r "$SCRIPT_DIR/framework/prompt-templates/"* "$GRIMOIRE_DIR/_config/custom/prompt-templates/" 2>/dev/null || true

# Workflows
cp -r "$SCRIPT_DIR/framework/workflows/"* "$GRIMOIRE_DIR/_config/custom/workflows/" 2>/dev/null || true
# Ajuster les chemins relatifs pour la profondeur de destination (4 niveaux)
for _wf in "$GRIMOIRE_DIR/_config/custom/workflows/"*.md; do
    [[ -f "$_wf" ]] || continue
    sed -i -E \
        -e 's|href="(\.\./)+|href="../../../../|g' \
        -e 's|src="(\.\./)+|src="../../../../|g' \
        "$_wf"
done

ok "Framework installé"

# ─── 3. Copier les agents meta (toujours inclus) ────────────────────────────
info "Installation des agents meta (Atlas, Sentinel, Mnemo)..."

cp "$SCRIPT_DIR/archetypes/meta/agents/"*.md "$GRIMOIRE_DIR/_config/custom/agents/"
ok "Agents meta installés"

# ─── 4. Copier les agents de l'archétype ────────────────────────────────────
if [[ "$ARCHETYPE" != "meta" ]]; then
    info "Installation de l'archétype '$ARCHETYPE'..."
    cp "$ARCHETYPE_DIR/agents/"*.md "$GRIMOIRE_DIR/_config/custom/agents/" 2>/dev/null || true

    # Copier le template shared-context si disponible
    if [[ -f "$ARCHETYPE_DIR/shared-context.tpl.md" ]]; then
        cp "$ARCHETYPE_DIR/shared-context.tpl.md" "$GRIMOIRE_DIR/_memory/shared-context.md"
    fi

    ok "Archétype '$ARCHETYPE' installé"
fi

# ─── 4b. Déployer les agents stack via Modal Team Engine (MTE) ───────────────
if $AUTO_DETECT && [[ -n "${DETECTED_STACKS:-}" ]]; then
    info "Modal Team Engine — déploiement des agents stack..."
    deploy_stack_agents "$DETECTED_STACKS" "$GRIMOIRE_DIR/_config/custom/agents"
fi
# ─── 4c. Déployer les agents features selon le backend mémoire ────────────────────
deploy_feature_agents "$MEMORY_BACKEND" "$GRIMOIRE_DIR/_config/custom/agents"

# Ajuster les chemins relatifs agents pour la profondeur de destination (4 niveaux)
for _af in "$GRIMOIRE_DIR/_config/custom/agents/"*.md; do
    [[ -f "$_af" ]] || continue
    sed -i -E \
        -e 's|href="(\.\./)+|href="../../../../|g' \
        -e 's|src="(\.\./)+|src="../../../../|g' \
        "$_af"
done

# ─── 5. Générer project-context.yaml ────────────────────────────────────────
info "Génération de project-context.yaml..."

PROJECT_CONTEXT="$TARGET_DIR/project-context.yaml"
if [[ ! -f "$PROJECT_CONTEXT" ]]; then
    sed -e "s/\"Mon Projet\"/\"$PROJECT_NAME\"/" \
        -e "s/\"Votre Nom\"/\"$USER_NAME\"/" \
        -e "s/\"Français\"/\"$LANGUAGE\"/" \
        -e "s/\"minimal\"/\"$ARCHETYPE\"/" \
        -e "s/backend: \"auto\"/backend: \"$MEMORY_BACKEND\"/" \
        "$SCRIPT_DIR/project-context.tpl.yaml" > "$PROJECT_CONTEXT"
    # Ajouter la version du kit en fin de fichier
    echo "" >> "$PROJECT_CONTEXT"
    echo "# Installé par Grimoire Custom Kit" >> "$PROJECT_CONTEXT"
    echo "grimoire_kit_version: \"$GRIMOIRE_KIT_VERSION\"" >> "$PROJECT_CONTEXT"
    ok "project-context.yaml créé"
else
    warn "project-context.yaml existe déjà, pas écrasé"
fi

# ─── 6. Générer les configs Grimoire ────────────────────────────────────────────
info "Génération des fichiers de configuration..."

# Config mémoire
cat > "$GRIMOIRE_DIR/_memory/config.yaml" <<YAML
user_name: "$USER_NAME"
communication_language: "$LANGUAGE"
document_output_language: "$LANGUAGE"
output_folder: "{project-root}/_grimoire-output"
YAML

# Shared context par défaut (si pas fourni par l'archétype)
if [[ ! -f "$GRIMOIRE_DIR/_memory/shared-context.md" ]]; then
    cat > "$GRIMOIRE_DIR/_memory/shared-context.md" <<MD
# Contexte Partagé — $PROJECT_NAME

> Ce fichier est chargé par tous les agents au démarrage.
> Il contient les informations essentielles du projet.

## Projet

- **Nom** : $PROJECT_NAME
- **Type** : À compléter
- **Stack** : À compléter

## Équipe d'Agents Custom

| Agent | Nom | Icône | Domaine |
|-------|-----|-------|---------|
$(ls "$GRIMOIRE_DIR/_config/custom/agents/"*.md 2>/dev/null | while read -r f; do
    name=$(basename "$f" .md)
    echo "| $name | — | — | À compléter |"
done)

## Conventions

- À compléter selon les besoins du projet
MD
fi

# Fichiers mémoire vides
touch "$GRIMOIRE_DIR/_memory/decisions-log.md"
touch "$GRIMOIRE_DIR/_memory/handoff-log.md"
touch "$GRIMOIRE_DIR/_memory/agent-changelog.md"
echo '[]' > "$GRIMOIRE_DIR/_memory/memories.json"
touch "$GRIMOIRE_DIR/_memory/activity.jsonl"

# Contradiction log
sed "s/{{project_name}}/$PROJECT_NAME/g" \
    "$SCRIPT_DIR/framework/memory/contradiction-log.tpl.md" \
    > "$GRIMOIRE_DIR/_memory/contradiction-log.md"

# ─── Failure Museum (BM-03) ─────────────────────────────────────────────────
if [[ ! -f "$GRIMOIRE_DIR/_memory/failure-museum.md" ]]; then
    sed -e "s/{{project_name}}/$PROJECT_NAME/g" \
        -e "s/{{init_date}}/$(date +%Y-%m-%d)/g" \
        "$SCRIPT_DIR/framework/memory/failure-museum.tpl.md" \
        > "$GRIMOIRE_DIR/_memory/failure-museum.md"
fi

# ─── Session Branching — structure .runs/ (BM-16) ──────────────────────────
info "Initialisation de la structure de sessions..."
RUNS_DIR="$TARGET_DIR/_grimoire-output/.runs"
mkdir -p "$RUNS_DIR/main"
cat > "$RUNS_DIR/main/branch.json" << JSONEOF
{
  "branch": "main",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "created_by": "grimoire-init",
  "purpose": "Branche de session principale",
  "parent_branch": null,
  "status": "active"
}
JSONEOF

# ─── Team Context Directories (BM-17) ───────────────────────────────────────
mkdir -p "$TARGET_DIR/_grimoire-output/team-vision"
mkdir -p "$TARGET_DIR/_grimoire-output/team-build/stories"
mkdir -p "$TARGET_DIR/_grimoire-output/team-build/test-reports"
mkdir -p "$TARGET_DIR/_grimoire-output/team-ops"
mkdir -p "$TARGET_DIR/_grimoire-output/contracts"
ok "Structure de sessions et teams créée"

# Session state
cat > "$GRIMOIRE_DIR/_memory/session-state.md" <<MD
# État de la dernière session

> Mis à jour automatiquement par les agents en fin de session.

| Champ | Valeur |
|-------|--------|
| Agent | — |
| Date | — |
| Fichiers modifiés | — |
| État du travail | — |
| Prochaine étape | — |
MD

# Créer les fichiers learnings pour chaque agent
for agent_file in "$GRIMOIRE_DIR/_config/custom/agents/"*.md; do
    agent_name=$(basename "$agent_file" .md)
    learnings_file="$GRIMOIRE_DIR/_memory/agent-learnings/${agent_name}.md"
    if [[ ! -f "$learnings_file" ]]; then
        echo "# Learnings — $agent_name" > "$learnings_file"
        echo "" >> "$learnings_file"
        echo "> Apprentissages accumulés par cet agent." >> "$learnings_file"
    fi
done

ok "Configuration générée"

# ─── 7. Générer le manifest ─────────────────────────────────────────────────
info "Génération du manifest d'agents..."

MANIFEST="$GRIMOIRE_DIR/_config/agent-manifest.csv"
echo 'name,displayName,title,icon,role,module,path' > "$MANIFEST"
for agent_file in "$GRIMOIRE_DIR/_config/custom/agents/"*.md; do
    agent_name=$(basename "$agent_file" .md)
    echo "\"$agent_name\",\"$agent_name\",\"\",\"\",\"\",\"custom\",\"_grimoire/_config/custom/agents/$agent_name.md\"" >> "$MANIFEST"
done

ok "Manifest généré (à compléter avec les détails des agents)"

# ─── 7b. Générer .github/copilot-instructions.md ────────────────────────────
COPILOT_DIR="$TARGET_DIR/.github"
COPILOT_FILE="$COPILOT_DIR/copilot-instructions.md"
if [[ ! -f "$COPILOT_FILE" ]] || $FORCE; then
    mkdir -p "$COPILOT_DIR"
    # Collecter la liste des agents installés
    agents_table=""
    for agent_file in "$GRIMOIRE_DIR/_config/custom/agents/"*.md; do
        [[ -f "$agent_file" ]] || continue
        aname="$(basename "$agent_file" .md)"
        agents_table="${agents_table}| ${aname} | _grimoire/_config/custom/agents/${aname}.md |\n"
    done

    cat > "$COPILOT_FILE" <<COPILOT_EOF
# Copilot Instructions — ${PROJECT_NAME}

> Auto-généré par Grimoire Custom Kit v${GRIMOIRE_KIT_VERSION}
> Archétype : ${ARCHETYPE} | Backend mémoire : ${MEMORY_BACKEND}
> Date : $(date +%Y-%m-%d)

## Contexte Projet

- **Nom** : ${PROJECT_NAME}
- **Utilisateur** : ${USER_NAME}
- **Langue** : ${LANGUAGE}

## Structure Grimoire

\`\`\`
_grimoire/
  _config/
    custom/
      agents/          ← Agents IA du projet
      prompt-templates/ ← Templates de prompts
      workflows/       ← Workflows personnalisés
      agent-base.md    ← Protocole de base des agents
      cc-verify.sh     ← Completion Contract verifier
      sil-collect.sh   ← Self-Improvement Loop collector
    agent-manifest.csv ← Registre de tous les agents
  _memory/
    shared-context.md  ← Contexte partagé entre agents
    config.yaml        ← Configuration mémoire
    maintenance.py     ← Health check + maintenance mémoire
    mem0-bridge.py     ← Bridge vers mémoire vectorielle
    session-save.py    ← Sauvegarde de fin de session
    decisions-log.md   ← Journal des décisions architecturales
    failure-museum.md  ← Catalogue des erreurs résolues
_grimoire-output/
  .runs/               ← Sessions de travail
  team-vision/         ← Artefacts équipe vision
  team-build/          ← Artefacts équipe build
  contracts/           ← Contrats inter-agents
project-context.yaml   ← Configuration globale du projet
\`\`\`

## Agents Installés

| Agent | Fichier |
|-------|---------|
$(echo -e "$agents_table")

## Conventions

1. **Langue** : Toujours répondre en ${LANGUAGE}
2. **Completion Contract** : Avant chaque commit, vérifier avec \`bash _grimoire/_config/custom/cc-verify.sh\`
3. **Contexte** : Charger \`_grimoire/_memory/shared-context.md\` pour le contexte projet
4. **Décisions** : Logger les décisions architecturales dans \`_grimoire/_memory/decisions-log.md\`
5. **Erreurs** : Documenter les résolutions dans \`_grimoire/_memory/failure-museum.md\`
6. **Sessions** : Sauvegarder en fin de session via \`python3 _grimoire/_memory/session-save.py\`

## Commandes Utiles

\`\`\`bash
# Vérifier l'installation Grimoire
grimoire-init.sh doctor

# Vérifier le budget de contexte des agents
grimoire-init.sh guard

# Mettre à jour le framework
grimoire-init.sh upgrade

# Installer un archétype supplémentaire
grimoire-init.sh install --archetype ARCHETYPE
\`\`\`
COPILOT_EOF
    ok ".github/copilot-instructions.md généré"
else
    info ".github/copilot-instructions.md existe déjà"
fi

# ─── 8. Installer le git pre-commit hook (CC) ──────────────────────────────
GIT_DIR="$(git -C "$TARGET_DIR" rev-parse --git-dir 2>/dev/null || true)"
if [[ -n "$GIT_DIR" ]]; then
    HOOK_SRC="$SCRIPT_DIR/framework/hooks/pre-commit-cc.sh"
    HOOK_DST="$GIT_DIR/hooks/pre-commit"
    if [[ -f "$HOOK_SRC" ]]; then
        if [[ -f "$HOOK_DST" ]] && grep -q 'Grimoire Completion Contract' "$HOOK_DST" 2>/dev/null; then
            ok "Pre-commit hook CC déjà installé"
        elif [[ -f "$HOOK_DST" ]] && ! $FORCE; then
            warn "Un pre-commit hook non-Grimoire existe déjà — utilisez --force pour le chaîner"
        elif [[ -f "$HOOK_DST" ]]; then
            # Chaîner avec le hook existant
            cp "$HOOK_DST" "${HOOK_DST}.pre-grimoire"
            printf '#!/usr/bin/env bash\nbash "$(git rev-parse --git-dir)/hooks/pre-commit.pre-grimoire" || exit 1\nbash "%s" || exit 1\n' "$HOOK_SRC" > "$HOOK_DST"
            chmod +x "$HOOK_DST"
            ok "Pre-commit hook chaîné (existant + CC)"
        else
            cp "$HOOK_SRC" "$HOOK_DST"
            chmod +x "$HOOK_DST"
            ok "Pre-commit hook CC installé"
        fi
    fi
else
    info "Pas de dépôt git détecté — hook pre-commit non installé"
fi

# ─── 9. Générer docker-compose.memory.yml si backend = qdrant-docker ─────────────────
if [[ "$MEMORY_BACKEND" == "qdrant-docker" ]]; then
    DOCKER_MEM="$TARGET_DIR/docker-compose.memory.yml"
    if [[ ! -f "$DOCKER_MEM" || "$FORCE" == "true" ]]; then
        cp "$SCRIPT_DIR/framework/memory/docker-compose.memory.tpl.yml" "$DOCKER_MEM"
        ok "docker-compose.memory.yml généré — lancer : docker compose -f docker-compose.memory.yml up -d"
    fi
fi

# ─── 10. Installer les dépendances Python (optionnel) ────────────────────────────────
if command -v pip3 &>/dev/null; then
    info "Installation des dépendances Python (backend: $MEMORY_BACKEND)..."
    if pip3 install -q -r "$GRIMOIRE_DIR/_memory/requirements.txt" 2>/dev/null; then
        ok "Dépendances Python installées"
    else
        warn "Installation des dépendances échouée (non bloquant)"
    fi
else
    warn "pip3 non trouvé — installez les dépendances manuellement : pip install -r _grimoire/_memory/requirements.txt"
fi

# ─── 11. Initialiser les collections Qdrant structurées (BM-22) ─────────────
if command -v python3 &>/dev/null && [[ -f "$GRIMOIRE_DIR/_memory/mem0-bridge.py" ]]; then
    info "Initialisation des collections Qdrant structurées..."
    python3 "$GRIMOIRE_DIR/_memory/mem0-bridge.py" init-collections 2>/dev/null && true
fi

# ─── 12. Résumé ──────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🎉 Grimoire Custom Kit v${GRIMOIRE_KIT_VERSION} installé avec succès !${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "  Prochaines étapes :"
echo "  1. Éditer ${CYAN}project-context.yaml${NC} avec vos infos projet"
echo "  2. Compléter ${CYAN}_grimoire/_memory/shared-context.md${NC}"
echo "  3. Personnaliser les agents dans ${CYAN}_grimoire/_config/custom/agents/${NC}"
echo "  4. Installer Grimoire Framework si pas déjà fait :"
echo "     ${CYAN}# npx bmad-install (retiré)${NC}"
echo ""
echo "  Pour vérifier l'installation :"
echo "     ${CYAN}python3 _grimoire/_memory/maintenance.py health-check${NC}"
echo ""
echo "  Completion Contract — vérifier votre code :"
echo "     ${CYAN}bash _grimoire/_config/custom/cc-verify.sh${NC}"
echo ""

if $AUTO_DETECT && [[ -n "${DETECTED_STACKS:-}" ]]; then
    echo -e "  ${CYAN}Stack(s) détecté(s) : ${GREEN}$DETECTED_STACKS${NC}"
    echo ""
fi