#!/usr/bin/env zsh
# bmad-completion.zsh — BM-38 : Shell completions pour bmad-init.sh
#
# Installation (zsh) :
#   echo "source /path/to/grimoire-kit/framework/tools/bmad-completion.zsh" >> ~/.zshrc
#   source ~/.zshrc
#
# Installation (bash) :
#   # Le fichier bash-completion est à la fin de ce script
#   echo "source /path/to/grimoire-kit/framework/tools/bmad-completion.bash" >> ~/.bashrc

# ── Détection du répertoire du kit ──────────────────────────────────────────
_BMAD_KIT_DIR="${BMAD_KIT_DIR:-$(dirname "$(dirname "$(dirname "${(%):-%x}")")")}"

# ── Listage dynamique des archétypes ────────────────────────────────────────
_bmad_archetypes() {
    local archetypes_dir="${_BMAD_KIT_DIR}/archetypes"
    local -a archetypes
    
    # Archétypes principaux
    for d in "${archetypes_dir}"/*/; do
        [[ -d "$d" ]] || continue
        local name
        name="$(basename "$d")"
        [[ "$name" == "meta" || "$name" == "stack" ]] && continue
        archetypes+=("$name")
    done
    
    # Stack agents
    for f in "${archetypes_dir}/stack/agents/"*-expert.md; do
        [[ -f "$f" ]] || continue
        local lang
        lang="$(basename "$f" | sed 's/-expert.md//')"
        archetypes+=("stack/${lang}")
    done
    
    echo "${archetypes[@]}"
}

# ── Listage des checkpoints disponibles ─────────────────────────────────────
_bmad_checkpoints() {
    find "${PWD}/_bmad-output/.runs" -name "state.json" -exec grep -l '"checkpoint_id"' {} \; 2>/dev/null \
        | xargs -I{} sh -c 'grep "checkpoint_id" "$1" | grep -oP "[a-f0-9]{6}"' _ {} 2>/dev/null
}

# ── Listage des branches de session ─────────────────────────────────────────
_bmad_branches() {
    ls -d "${PWD}/_bmad-output/.runs"/*/ 2>/dev/null | xargs -I{} basename {}
}

# ── Completion principale zsh ────────────────────────────────────────────────
_bmad_init_complete() {
    local state
    local -a subcommands
    
    subcommands=(
        'session-branch:Gestion des branches de session'
        'install:Installer un archétype'
        'resume:Reprendre un workflow interrompu'
        'trace:Gérer le BMAD Trace audit trail'
        'doctor:Diagnostic et health check du kit'
        'validate:Valider des fichiers DNA archétype'
        'changelog:Générer CHANGELOG.md depuis BMAD_TRACE'
        'hooks:Installer les hooks Git du kit'
        'bench:Mesurer et améliorer les performances des agents'
        'forge:Générer des squelettes d'agents depuis le besoin projet'
        'guard:Analyser le budget de contexte LLM des agents'
        'evolve:Faire évoluer la DNA archétype depuis l'usage réel'
        'dream:Dream Mode — consolidation hors-session'
        'consensus:Protocole de consensus adversarial'
        'antifragile:Score d'anti-fragilité — résilience adaptative'
        'reasoning:Flux de raisonnement structuré'
        'migrate:Migration cross-projet d'artefacts BMAD'
        'darwinism:Sélection naturelle des agents — fitness et évolution'
        'stigmergy:Coordination stigmergique — phéromones numériques entre agents'
    )
    
    local context curcontext="$curcontext" line state
    typeset -A opt_args
    
    _arguments -C \
        '1:subcommand:->subcommand' \
        '*::options:->options' \
        '(--help -h)'{--help,-h}'[Afficher l'"'"'aide]' \
        '(--name)--name[Nom du projet]:nom projet:' \
        '(--user)--user[Nom utilisateur]:utilisateur:' \
        '(--lang)--lang[Langue]:langue:(Français English Español Deutsch)' \
        '(--archetype)--archetype[Archétype]:archetype:->archetypes' \
        '(--target)--target[Répertoire cible]:directory:_directories' \
        '(--auto)--auto[Détection automatique]' \
        '(--memory)--memory[Backend mémoire]:backend:(local ollama openai qdrant)' \
        '(--force)--force[Forcer écrasement]' \
        && return
    
    case "$state" in
        subcommand)
            _describe 'sous-commande' subcommands
            ;;
        archetypes)
            local -a arch_list
            arch_list=($(_bmad_archetypes))
            _describe 'archétype' arch_list
            ;;
        options)
            case "${line[1]}" in
                session-branch)
                    _arguments \
                        '--name[Créer une branche]:nom branche:' \
                        '--list[Lister les branches]' \
                        '--diff[Comparer deux branches]:branche:->branches' \
                        '--merge[Merger une branche dans main]:branche:->branches' \
                        '--archive[Archiver une branche]:branche:->branches'
                    ;;
                install)
                    _arguments \
                        '--list[Lister les archétypes disponibles]' \
                        '--archetype[Archétype à installer]:archetype:->archetypes' \
                        '--inspect[Inspecter un archétype]:archetype:->archetypes' \
                        '--target[Répertoire BMAD cible]:directory:_directories' \
                        '--force[Forcer la réinstallation]'
                    case "$state" in
                        archetypes)
                            local -a arch_list
                            arch_list=($(_bmad_archetypes))
                            _describe 'archétype' arch_list
                            ;;
                    esac
                    ;;
                resume)
                    _arguments \
                        '--list[Lister les checkpoints]' \
                        '--checkpoint[Reprendre depuis un checkpoint]:checkpoint_id:->checkpoints'
                    case "$state" in
                        checkpoints)
                            local -a cp_list
                            cp_list=($(_bmad_checkpoints))
                            _describe 'checkpoint' cp_list
                            ;;
                    esac
                    ;;
                trace)
                    _arguments \
                        '--tail[Dernières N entrées]:nombre:(20 50 100)' \
                        '--agent[Filtrer par agent]:agent:(dev qa architect pm sm bmad-master)' \
                        '--type[Filtrer par type]:type:(DECISION REMEMBER HANDOFF CHECKPOINT ACTION ERROR WARN)' \
                        '--branch[Filtrer par session]:branch:->branches' \
                        '--archive[Archiver la trace]' \
                        '--reset[Réinitialiser la trace]' \
                        '--confirm[Confirmer les opérations destructives]'
                    case "$state" in
                        branches)
                            local -a br_list
                            br_list=($(_bmad_branches))
                            _describe 'branche' br_list
                            ;;
                    esac
                    ;;
                doctor)
                    _arguments \
                        '--fix[Corriger automatiquement les problèmes simples]'
                    ;;
                validate)
                    _arguments \
                        '--dna[Fichier DNA à valider]:dna_file:_files -g "*.dna.yaml *.dna.yml"' \
                        '--all[Valider tous les DNA]'
                    ;;
                changelog)
                    _arguments \
                        '--output[Fichier de sortie]:output_file:_files' \
                        '--since[Depuis une date (YYYY-MM-DD)]:date:'
                    ;;
                hooks)
                    _arguments \
                        '--install[Installer les hooks dans .git/hooks du projet cible]' \
                        '--target[Répertoire cible]:directory:_directories' \
                        '--list[Lister les hooks disponibles]'
                    ;;
                bench)
                    _arguments \
                        '--summary[Résumé des scores agents]' \
                        '--report[Rapport détaillé par agent]' \
                        '--improve[Générer bench-context.md pour amélioration]' \
                        '--since[Filtrer depuis une date (YYYY-MM-DD)]:date:' \
                        '--agent[Agent spécifique]:agent_id:'
                    ;;
                forge)
                    _arguments \
                        '--from[Générer depuis une description textuelle]:description:' \
                        '--from-gap[Générer depuis les lacunes détectées dans BMAD_TRACE]' \
                        '--from-trace[Analyser la trace pour proposer des agents]' \
                        '--list[Lister les proposals existants]' \
                        '--install[Installer un agent proposé]:tag:'
                    ;;
                guard)
                    local -a models
                    models=(claude-opus-4 claude-sonnet-4 claude-3-7-sonnet claude-haiku gpt-4o gpt-4o-mini gpt-4-turbo o1 o3 codex gemini-1.5-pro gemini-2.0-flash codestral llama3 mistral qwen2.5 copilot)
                    _arguments \
                        '--agent[Agent spécifique à analyser]:agent_id:' \
                        '--detail[Détail fichier par fichier]' \
                        '--model[Modèle cible]:model:->models' \
                        '--threshold[Seuil alerte en %]:pct:(20 30 40 50 60 70)' \
                        '--suggest[Afficher recommandations de réduction]' \
                        '--optimize[Analyser les fichiers pour optimisations de tokens]' \
                        '--recommend-models[Recommander le meilleur LLM par agent]' \
                        '--list-models[Lister les modèles supportés]' \
                        '--json[Sortie JSON pour CI]'
                    case "$state" in
                        models)
                            _describe 'modèle' models
                        ;;
                    esac
                    ;;
                evolve)
                    _arguments \
                        '--dna[Fichier DNA source]:dna_file:_files -g "*.dna.yaml"' \
                        '--trace[Fichier BMAD_TRACE source]:trace_file:_files' \
                        '--since[Analyser depuis une date (YYYY-MM-DD)]:date:' \
                        '--report[Générer seulement le rapport Markdown]' \
                        '--apply[Appliquer le dernier patch DNA généré]' \
                        '--project-root[Racine du projet cible]:directory:_directories'
                    ;;
                dream)
                    _arguments \
                        '--since[Depuis une date (YYYY-MM-DD)]:date:' \
                        '--agent[Agent spécifique]:agent_id:' \
                        '--validate[Valider les insights]' \
                        '--dry-run[Preview sans écrire]' \
                        '--json[Sortie JSON]'
                    ;;
                consensus)
                    _arguments \
                        '--proposal[Proposition à évaluer]:proposition:' \
                        '--proposal-file[Charger depuis un fichier]:file:_files' \
                        '--threshold[Seuil de consensus]:pct:(0.5 0.66 0.75 0.8)' \
                        '--history[Afficher l'historique]' \
                        '--stats[Statistiques]' \
                        '--json[Sortie JSON]' \
                        '--dry-run[Ne pas sauvegarder]'
                    ;;
                antifragile)
                    _arguments \
                        '--since[Depuis une date (YYYY-MM-DD)]:date:' \
                        '--detail[Rapport détaillé avec recommandations]' \
                        '--trend[Tendance historique des scores]' \
                        '--json[Sortie JSON]' \
                        '--dry-run[Calculer sans sauvegarder]'
                    ;;
                reasoning)
                    local -a reasoning_cmds
                    reasoning_cmds=('log:Ajouter une entrée' 'query:Interroger' 'analyze:Analyser' 'compact:Compacter' 'stats:Statistiques' 'resolve:Changer statut')
                    _arguments \
                        '1:sous-commande:->reasoning_sub' \
                        '*::options:->reasoning_opts'
                    case "$state" in
                        reasoning_sub)
                            _describe 'sous-commande reasoning' reasoning_cmds
                            ;;
                    esac
                    ;;
                migrate)
                    local -a migrate_cmds
                    migrate_cmds=('export:Exporter un bundle' 'import:Importer un bundle' 'inspect:Inspecter un bundle' 'diff:Comparer bundle vs projet')
                    _arguments \
                        '1:sous-commande:->migrate_sub' \
                        '--output[Fichier de sortie]:output_file:_files' \
                        '--only[Types à exporter]:types:(learnings rules dna_patches agents consensus antifragile)' \
                        '--since[Depuis une date]:date:' \
                        '--bundle[Fichier bundle]:bundle_file:_files -g "*.json"' \
                        '--dry-run[Preview sans modifier]'
                    case "$state" in
                        migrate_sub)
                            _describe 'sous-commande migrate' migrate_cmds
                            ;;
                    esac
                    ;;
                darwinism)
                    local -a darwinism_cmds
                    darwinism_cmds=('evaluate:Évaluer la fitness' 'leaderboard:Classement' 'evolve:Actions évolutives' 'history:Historique' 'lineage:Lignée d'un agent')
                    _arguments \
                        '1:sous-commande:->darwinism_sub' \
                        '--since[Depuis une date]:date:' \
                        '--agent[Agent spécifique]:agent_id:' \
                        '--dry-run[Preview sans sauvegarder]' \
                        '--json[Sortie JSON]'
                    case "$state" in
                        darwinism_sub)
                            _describe 'sous-commande darwinism' darwinism_cmds
                            ;;
                    esac
                    ;;
                stigmergy)
                    local -a stigmergy_cmds
                    stigmergy_cmds=('emit:Émettre une phéromone' 'sense:Détecter les phéromones actives' 'amplify:Renforcer un signal' 'resolve:Résoudre un signal' 'landscape:Carte phéromonique' 'trails:Patterns émergents' 'evaporate:Nettoyer les signaux morts' 'stats:Statistiques rapides')
                    _arguments \
                        '1:sous-commande:->stigmergy_sub' \
                        '--type[Type de phéromone]:type:(NEED ALERT OPPORTUNITY PROGRESS COMPLETE BLOCK)' \
                        '--location[Zone affectée]:location:' \
                        '--text[Description du signal]:text:' \
                        '--agent[Agent]:agent:' \
                        '--tag[Filtrer par tag]:tag:' \
                        '--emitter[Filtrer par émetteur]:emitter:' \
                        '--id[ID phéromone]:id:' \
                        '--tags[Tags comma-sep]:tags:' \
                        '--intensity[Intensité initiale]:intensity:' \
                        '--include-resolved[Inclure les résolues]' \
                        '--dry-run[Preview sans modifier]' \
                        '--json[Sortie JSON]'
                    case "$state" in
                        stigmergy_sub)
                            _describe 'sous-commande stigmergy' stigmergy_cmds
                            ;;
                    esac
                    ;;
            esac
            ;;
    esac
}

compdef _bmad_init_complete bmad-init.sh
compdef _bmad_init_complete bmad-init

# Alias optionnel pour utilisation raccourcie
if [[ -n "${BMAD_ALIAS:-}" ]]; then
    alias bmad="$_BMAD_KIT_DIR/bmad-init.sh"
    compdef _bmad_init_complete bmad
fi

# ────────────────────────────────────────────────────────────────────────────
# Fichier bash-completion séparé (créer comme bmad-completion.bash)
# ────────────────────────────────────────────────────────────────────────────
# #!/usr/bin/env bash
# # Installation : source /path/to/bmad-completion.bash
#
# _bmad_init_bash() {
#     local cur prev words cword
#     _init_completion || return
#     local subcommands="session-branch install resume trace doctor validate changelog"
#     local archetypes_dir="$(dirname "$0")/../../archetypes"
#
#     case "$prev" in
#         bmad-init.sh|bmad-init)
#             COMPREPLY=($(compgen -W "$subcommands --name --user --lang --archetype --auto --memory --force --help" -- "$cur"))
#             ;;
#         --archetype)
#             local archs=""
#             for d in "$archetypes_dir"/*/; do
#                 archs="$archs $(basename "$d")"
#             done
#             for f in "$archetypes_dir/stack/agents/"*-expert.md; do
#                 archs="$archs stack/$(basename "$f" | sed 's/-expert.md//')"
#             done
#             COMPREPLY=($(compgen -W "$archs" -- "$cur"))
#             ;;
#         --type)
#             COMPREPLY=($(compgen -W "DECISION REMEMBER HANDOFF CHECKPOINT ACTION ERROR WARN" -- "$cur"))
#             ;;
#         --memory)
#             COMPREPLY=($(compgen -W "local ollama openai qdrant" -- "$cur"))
#             ;;
#         *)
#             case "${words[1]}" in
#                 doctor) COMPREPLY=($(compgen -W "--fix" -- "$cur")) ;;
#                 validate) COMPREPLY=($(compgen -W "--dna --all" -- "$cur")) ;;
#                 install) COMPREPLY=($(compgen -W "--list --archetype --inspect --target --force" -- "$cur")) ;;
#                 resume) COMPREPLY=($(compgen -W "--list --checkpoint" -- "$cur")) ;;
#                 trace) COMPREPLY=($(compgen -W "--tail --agent --type --branch --archive --reset --confirm" -- "$cur")) ;;
#                 changelog) COMPREPLY=($(compgen -W "--output --since" -- "$cur")) ;;
#                 session-branch) COMPREPLY=($(compgen -W "--name --list --diff --merge --archive --cherry-pick" -- "$cur")) ;;
#             esac
#             ;;
#     esac
# }
#
# complete -F _bmad_init_bash bmad-init.sh bmad-init bmad
