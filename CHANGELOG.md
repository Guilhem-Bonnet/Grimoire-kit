# <img src="docs/assets/icons/chart.svg" width="32" height="32" alt=""> Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.1] — 2026-03-03

### Corrigé — Bug hunt (3 fichiers, 10 corrections)

- **cognitive-flywheel.py** — `cmd_analyze()` n'écrivait pas dans l'historique →
 la tendance (trend) restait toujours "stable" car `compute_score` n'avait
 jamais de cycle précédent. Ajout d'un `append_history()` à chaque analyse.
- **cognitive-flywheel.py** — Variable morte `high` dans `apply_gates()` :
 construite mais jamais utilisée dans le return (dead code supprimé).
- **tests/test_maintenance_advanced.py** — 6 appels `open()` sans
 `encoding="utf-8"` : crash potentiel sur Windows/locales non-UTF8.

### Vérifié — Aucun problème trouvé

- Division par zéro : 12 sites vérifiés, tous protégés (max, or, if guards)
- Pyflakes (F) : 0 erreur sur 48 outils + 53 tests
- Bare except : 0 (tous les except ont un type)
- eval/exec : 0 appel dangereux
- assert en production : 0
- Fonctions dupliquées : 0
- Mutable default args : 0
- Shadowing builtins : 0
- 82 swallowed-exception (`except ... pass`) : tous intentionnels (graceful degradation)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.0] — 2026-03-02

### Ajouté — Cross-pollination depuis zav-sandbox (GSANE)

- **cognitive-flywheel.py** (outil #47) — Boucle d'auto-amélioration continue :
 analyse BMAD_TRACE.md pour détecter les patterns récurrents (failures,
 AC-FAIL), calcule un score de santé (A+ à D), génère des corrections
 automatiques avec système de gates (max 5 corrections, collision → escalade).
 6 commandes CLI : `analyze`, `report`, `apply`, `history`, `score`, `dashboard`
- **failure-museum.py** (outil #48) — Catalogue structuré des échecs :
 enregistre chaque failure avec root-cause, règle ajoutée, sévérité et tags.
 Persistance JSONL + sync markdown automatique.
 7 commandes CLI : `add`, `list`, `search`, `stats`, `export`, `lessons`, `check`
- **cleanup-branches.yml** — Workflow CI GitHub Actions pour supprimer
 automatiquement les branches mergées (protège main/develop/release/*)
- **tests/test_cognitive_flywheel.py** — 45 tests couvrant dataclasses,
 parsing trace, extraction de patterns, scoring, corrections, gates,
 persistence report/history, scoreboard, commandes, CLI, constantes
- **tests/test_failure_museum.py** — 43 tests couvrant dataclasses,
 persistence JSONL, markdown sync, commandes, CLI, intégration, constantes
- Total : **1 875 tests**, 0 échecs

### Inspiré par

- [zav-sandbox](https://github.com/zavrocKk/zav-sandbox) (framework GSANE) :
 Cognitive Flywheel, Failure Museum, branch cleanup CI

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.3.0] — 2026-03-02

### Ajouté — Couverture de tests complète (31 fichiers, +787 tests)

- **31 fichiers de tests** générés pour couvrir les 46 outils du framework :
 `bias-toolkit`, `context-guard`, `context-router`, `crescendo`, `crispr`,
 `dark-matter`, `dashboard`, `decision-log`, `desire-paths`, `digital-twin`,
 `distill`, `early-warning`, `harmony-check`, `immune-system`, `incubator`,
 `mirror-agent`, `mycelium`, `new-game-plus`, `nudge-engine`, `oracle`,
 `preflight-check`, `project-graph`, `quantum-branch`, `r-and-d`, `rosetta`,
 `self-healing`, `semantic-chain`, `sensory-buffer`, `swarm-consensus`,
 `time-travel`, `workflow-adapt`
- Chaque fichier teste : dataclasses, fonctions pures, fonctions projet,
 formats de sortie, constantes, parser CLI, intégration CLI
- **_gen_tests.py** — générateur automatique de tests par analyse AST
- Total : **1 787 tests**, 0 échecs, ~130 s d'exécution

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.1] — 2026-03-02

### Corrigé — Audit multi-cycles (15 cycles, 3 fichiers)

- **nso.py:323** — condition morte `status = "ok" if ... else "ok"` corrigée
 en `"warn"` — le NSO signale désormais correctement les erreurs memory-lint
- **gen-tests.py** — ajout `encoding="utf-8"` sur 2 appels `open()` (L167, L260)
 — évite les erreurs d'encodage sur Windows avec des fichiers contenant des
 accents/emojis
- **r-and-d.py:849** — ajout `encoding="utf-8"` sur `tool_file.open()` dans
 l'analyse de gap (même correctif portabilité Windows)

### Vérifié — Aucun problème trouvé

- Division par zéro : 15+ sites vérifiés, tous protégés par des gardes
- Regex : toutes les regex compilées valides
- Références croisées `_load_tool()` : 8 appels, tous vers des fichiers existants
- Aucun `open()` sans `with`, aucun chemin absolu hardcodé
- Aucune variable non-initialisée dans `finally`, aucun dict muté pendant itération
- Aucun import inutilisé, aucune variable morte (ruff F401/F841 clean)
- Chemins mémoire/output cohérents entre tous les outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.0] — 2026-03-02

### Corrigé — Fuites mémoire Python

- **r-and-d.py** — 5 correctifs mémoire :
 - `save_memory()` : ajout cap `MAX_MEMORY_SIZE = 500` — tronque aux N entrées
 les plus récentes au lieu de grossir indéfiniment
 - `_load_tool()` : cache via `sys.modules` — évite de recréer le module à chaque
 appel (14 exec_module/cycle → 1 par outil)
 - `load_cycle_reports()` : paramètre `last_n` — ne charge que les N derniers
 rapports au lieu de tout l'historique
 - `next_cycle_id()` : extraction directe depuis le nom du dernier fichier
 au lieu de charger et parser tous les rapports JSON
 - `tool_file.open()` L817 : ajout `with` context manager (file descriptor leak)
- **nso.py** — `_load_tool()` : même cache `sys.modules`
- **dream.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy
- **memory-lint.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.1] — 2026-03-01

### Supprimé

- **workflow-snippets.py** (389 lignes) — aucune intégration CLI, aucun test, aucune
 cross-référence. Overlap avec `workflow-design-patterns.md`
- **quorum.py** (400 lignes) — aucune intégration CLI, aucun test. Overlap fonctionnel
 avec `antifragile-score.py` (signaux SIL) et `stigmergy.py` (seuils phéromoniques)
- **confidence-scores.py** (572 lignes) — aucune intégration CLI, aucun test. Heuristiques
 simplistes, overlap avec `reasoning-stream.py` (niveaux de confiance)

### Corrigé

- Nettoyage des références aux 3 outils supprimés dans `docs/concepts.md`
- Total : **−1361 lignes** de dead code, 49 → 46 outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.0] — 2026-03-01

### Ajouté

- **CHANGELOG.md** — suivi formel des changements (issue R&D oracle-swot)
- **Multi-projet** pour `antifragile-score.py` — comparer la santé entre projets
 via `--multi-project dir1 dir2 ...`
- **Multi-projet** pour `dream.py` — croiser les insights entre projets
 via `--multi-project dir1 dir2 ...`
- **Moteur R&D v2.1** — filtre anti-chaîne de mutations + pénalité actionnabilité

### Corrigé

- Nettoyage du TODO orphelin dans le template prototype de `r-and-d.py`
- Moteur R&D : les mutations de mutations (profondeur > 1) sont progressivement
 pénalisées dans le challenge, réduisant le bruit combinatoire

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.0.0] — 2026-02-28

### Ajouté

- **r-and-d.py v2.0** — Moteur d'Innovation R&D avec apprentissage par renforcement
 - Closed-loop reward via health snapshots du projet réel
 - Challenge durci : GO threshold 0.60, CONDITIONAL 0.40, quota 20% rejet
 - Générateur de mutations des gagnants passés (transposition, escalade,
 inverse, fusion)
 - Générateur gap-driven (gaps réels : tests manquants, docs absentes,
 domaines sous-représentés, dépendances fragiles)
 - Commande `seed` pour initialiser la mémoire
 - Commande `health` — snapshot de santé du projet
 - Commande `prototype` — génération de squelettes Python
 - 13 sources de récolte (dream, oracle-swot, oracle-attract, early-warning,
 dna-drift, workflow-adapt, antifragile, harmony, stigmergy, incubator,
 synthetic, mutation, gap-analysis)

### Corrigé

- Déduplication inter-cycles dans le moteur R&D (idées recyclées filtrées)
- Générateur synthétique enrichi (21 templates de concept blending)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.6.0] — 2026-02-27

### Ajouté

- **Vague 6** — 7 outils d'exploration avancée :
 - `digital-twin.py` — simulation de l'écosystème projet
 - `quantum-branch.py` — exploration parallèle de décisions
 - `time-travel.py` — machine à remonter le temps projet
 - `crispr-rules.py` — mutation ciblée de règles agents
 - `decision-log.py` — journal structuré des décisions
 - `mirror-agent.py` — audit croisé inter-agents
 - `sensory-buffer.py` — tampon sensoriel entre sessions

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.5.0] — 2026-02-26

### Ajouté

- **Vague 5** — Dream Nervous System :
 - `dream.py` v2 — mémoire cross-session, décroissance temporelle, bigram keywords
 - Boucle fermée nervous system avec feedback loop et trigger intelligent
 - `memory-lint.py` — vérificateur d'hygiène mémoire
 - `nso.py` — orchestrateur du système nerveux

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.4.0] — 2026-02-25

### Ajouté

- **Vague 4** — Stigmergy :
 - `stigmergy.py` — coordination indirecte par phéromones numériques

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.3.0] — 2026-02-24

### Ajouté

- **Vague 3** — Cross-Project Migration + Agent Darwinism :
 - `cross-migrate.py` — migration d'artefacts entre projets
 - `agent-darwinism.py` — sélection naturelle des agents

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.2.0] — 2026-02-23

### Ajouté

- **Vague 2** — Anti-Fragile Score + Reasoning Stream :
 - `antifragile-score.py` — scoring de résilience adaptative
 - `reasoning-stream.py` — flux de raisonnement structuré

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.1.0] — 2026-02-22

### Ajouté

- **Vague 1** — Dream Mode + Adversarial Consensus :
 - `dream.py` — consolidation hors-session et insights émergents
 - `adversarial-consensus.py` — protocole de consensus adversarial

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.0.0] — 2026-02-20

### Ajouté

- Vagues précédentes : 25 outils de base, protocole cognitif Completion Contract,
 Modal Team Engine, Self-Improvement Loop, Vector DB, web-app archetype
- Architecture framework : agent-base, agent-rules, hooks, mémoire, sessions,
 outils, registre, équipes, workflows
- Archetypes : web-app, infra-ops, minimal, stack, meta, features, fix-loop
- Documentation : getting-started, archetype-guide, memory-system, troubleshooting,
 workflow-design-patterns, creating-agents
- Tests : smoke-test.sh + suite de tests Python (122 tests)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [0.1.0] — 2026-02-15

### Ajouté

- Initial commit — BMAD Custom Kit structure de base
