# Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [2.1.1] — 2026-03-01

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

## [2.1.0] — 2026-03-01

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

## [2.0.0] — 2026-02-28

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

## [1.6.0] — 2026-02-27

### Ajouté

- **Vague 6** — 7 outils d'exploration avancée :
  - `digital-twin.py` — simulation de l'écosystème projet
  - `quantum-branch.py` — exploration parallèle de décisions
  - `time-travel.py` — machine à remonter le temps projet
  - `crispr-rules.py` — mutation ciblée de règles agents
  - `decision-log.py` — journal structuré des décisions
  - `mirror-agent.py` — audit croisé inter-agents
  - `sensory-buffer.py` — tampon sensoriel entre sessions

## [1.5.0] — 2026-02-26

### Ajouté

- **Vague 5** — Dream Nervous System :
  - `dream.py` v2 — mémoire cross-session, décroissance temporelle, bigram keywords
  - Boucle fermée nervous system avec feedback loop et trigger intelligent
  - `memory-lint.py` — vérificateur d'hygiène mémoire
  - `nso.py` — orchestrateur du système nerveux

## [1.4.0] — 2026-02-25

### Ajouté

- **Vague 4** — Stigmergy :
  - `stigmergy.py` — coordination indirecte par phéromones numériques

## [1.3.0] — 2026-02-24

### Ajouté

- **Vague 3** — Cross-Project Migration + Agent Darwinism :
  - `cross-migrate.py` — migration d'artefacts entre projets
  - `agent-darwinism.py` — sélection naturelle des agents

## [1.2.0] — 2026-02-23

### Ajouté

- **Vague 2** — Anti-Fragile Score + Reasoning Stream :
  - `antifragile-score.py` — scoring de résilience adaptative
  - `reasoning-stream.py` — flux de raisonnement structuré

## [1.1.0] — 2026-02-22

### Ajouté

- **Vague 1** — Dream Mode + Adversarial Consensus :
  - `dream.py` — consolidation hors-session et insights émergents
  - `adversarial-consensus.py` — protocole de consensus adversarial

## [1.0.0] — 2026-02-20

### Ajouté

- Vagues précédentes : 25 outils de base, protocole cognitif Completion Contract,
  Modal Team Engine, Self-Improvement Loop, Vector DB, web-app archetype
- Architecture framework : agent-base, agent-rules, hooks, mémoire, sessions,
  outils, registre, équipes, workflows
- Archetypes : web-app, infra-ops, minimal, stack, meta, features, fix-loop
- Documentation : getting-started, archetype-guide, memory-system, troubleshooting,
  workflow-design-patterns, creating-agents
- Tests : smoke-test.sh + suite de tests Python (122 tests)

## [0.1.0] — 2026-02-15

### Ajouté

- Initial commit — BMAD Custom Kit structure de base
