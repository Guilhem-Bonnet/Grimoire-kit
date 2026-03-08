<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/boomerang.svg" width="32" height="32" alt=""> Boomerang Orchestration Workflow

> **BM-11** — Workflow d'orchestration hiérarchique inspiré de Roo-Code Boomerang Tasks.
>
> **Principe** : Un agent orchestrateur (SM) décompose une tâche complexe en sous-tâches atomiques,
> les délègue aux agents spécialisés, récupère les résultats, et synthétise.
>
> **Différence avec le Party Mode** : Le boomerang est structuré, tracé, et produit des artefacts.
> Le Party Mode est conversationnel. Le boomerang est opérationnel.

<img src="../../docs/assets/divider.svg" width="100%" alt="">


## <img src="../../docs/assets/icons/boomerang.svg" width="28" height="28" alt=""> Schéma d'Orchestration

```
USER demande : "Implémenter la feature Authentification JWT"
         │
         ▼
┌────────────────────┐
│  SM (Orchestrateur)│  Phase 1 — Décomposition
│  Bob               │  → Stories atomiques + ACs
└────────┬───────────┘
         │  Spawn sous-tâches
         │
    ┌────┴────────────────────────────┐
    │                                 │
    ▼                                 ▼
┌──────────┐                    ┌──────────┐
│  Dev     │                    │  Arch    │
│  Amelia  │                    │  Winston │
│ Story 1  │                    │ADR Auth  │
└────┬─────┘                    └────┬─────┘
     │ CC PASS                       │ ADR écrit
     │                               │
     └───────────────┬───────────────┘
                     ▼
              ┌──────────┐
              │  QA      │
              │  Quinn   │  Validation croisée
              │ Tests E2E│  → AC couverts ?
              └────┬─────┘
                   │ QA PASS
                   ▼
         ┌──────────────────┐
         │ SM (Orchestrateur)│  Phase N — Synthèse
         │  Rapport Final    │  → Feature livréé
         └──────────────────┘
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Fichier Workflow YAML

```yaml
# framework/workflows/boomerang-feature.yaml
name: "boomerang-feature"
description: "Implémentation complète d'une feature via orchestration boomerang SM→Dev+Arch→QA→SM"
version: "1.0.0"

inputs:
  - name: feature_description
    description: "Description de la feature à implémenter"
    required: true
  - name: prd_path
    description: "Chemin vers le PRD de référence"
    required: false

steps:
  - step: 1
    name: "Décomposition en Stories"
    type: task
    agent: sm
    instruction: |
      Lire le contexte du projet et la feature "{feature_description}".
      Décomposer en 2-4 stories atomiques avec :
        - Titre (format "As a {user}, I want {goal} so that {benefit}")
        - Acceptance Criteria (ACs) mesurables et testables
        - Story points estimés
        - Fichier story créé dans _grimoire-output/implementation-artifacts/stories/
      Retourner : stories: [{id, title, acs[], story_points, file_path}]
    output_key: stories
    checkpoint: true

  - step: 2
    name: "Architecture Decision Record"
    type: task
    agent: architect
    instruction: |
      Lire les stories {stories} et le contexte projet.
      Identifier les décisions architecturales nécessaires pour implémenter la feature.
      Écrire un ADR dans _grimoire-output/implementation-artifacts/adr-{feature_slug}.md.
      Format ADR : Status | Context | Decision | Consequences.
      [THINK] si multiple options viables.
      Retourner : adr_path, key_decisions: []
    depends_on: [stories]
    output_key: architecture
    checkpoint: true

  - step: 3
    name: "Implémentation Story 1"
    type: orchestrate
    spawn:
      - agent: dev
        task: |
          Lire la story {stories[0].file_path} ET l'ADR {architecture.adr_path}.
          Mode [ACT] — Implémenter la story en TDD :
          1. Tests d'abord (red → green → refactor)
          2. Code d'implémentation
          3. CC PASS obligatoire
          Retourner : {story_id, files_changed[], tests_added[], cc_result: "PASS|FAIL"}
        output_key: story_1_impl
    depends_on: [stories, architecture]
    checkpoint: true

  - step: 4
    name: "Implémentation Story 2"
    type: orchestrate
    spawn:
      - agent: dev
        task: |
          Lire la story {stories[1].file_path} ET le résultat {story_1_impl}.
          Mode [ACT] — Implémenter en TDD. CC PASS obligatoire.
          Retourner : {story_id, files_changed[], tests_added[], cc_result}
        output_key: story_2_impl
    depends_on: [story_1_impl]
    condition: "len(stories) >= 2"
    checkpoint: true

  - step: 5
    name: "QA Validation Croisée"
    type: orchestrate
    spawn:
      - agent: qa
        task: |
          Valider l'implémentation complète : {story_1_impl}, {story_2_impl}.
          Vérifier :
          1. Tous les ACs de chaque story sont couverts par des tests
          2. Edge cases et cas d'erreur testés
          3. Coverage analysis sur les fichiers modifiés
          Retourner : {acs_covered: [], gaps: [], recommendations: [], coverage_pct}
        output_key: qa_report
    depends_on: [story_1_impl, story_2_impl]
    checkpoint: true

  - step: 6
    name: "Synthèse & Rapport Final"
    type: task
    agent: sm
    instruction: |
      Synthétiser les résultats de toute l'orchestration :
      - Stories implémentées : {story_1_impl}, {story_2_impl}
      - Architecture : {architecture}
      - QA : {qa_report}

      Produire un rapport de livraison dans _grimoire-output/implementation-artifacts/boomerang-report-{date}.md.

      Si des gaps QA existent : créer des stories de correction et relancer les steps 3-5.
      Si QA PASS : marquer la feature comme livrée dans shared-context.md.

      Retourner : {status: "DELIVERED|NEEDS_FIXES", report_path, summary}
    depends_on: [qa_report]
    output_key: final_report
    checkpoint: true

outputs:
  - key: final_report
    description: "Rapport de livraison complet"
    save_to: "_grimoire-output/implementation-artifacts/boomerang-report-{date}.md"
  - key: stories
    description: "Stories créées et livrées"
  - key: architecture
    description: "ADR de la feature"
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/boomerang.svg" width="28" height="28" alt=""> Rapport de Livraison Boomerang

Template auto-généré en step 6 :

```markdown
# 🪃 Boomerang Report — {feature_description}

**Date** : {date}
**Orchestrateur** : SM (Bob)
**Statut** : ✅ DELIVERED / 🔴 NEEDS_FIXES

## Stories Livrées
| Story | Points | CC | ACs Couverts |
|-------|--------|-----|-------------|
| {story_1_title} | {points} | PASS | 5/5 |
| {story_2_title} | {points} | PASS | 3/3 |

## Architecture
ADR : {adr_path}
Décisions clés : {key_decisions}

## QA Summary
- Coverage : {coverage_pct}%
- ACs couverts : {acs_covered_count}/{acs_total_count}
- Gaps : {gaps_count} ({gaps_list})

## Fichiers Modifiés
{files_changed}

## Temps Total
{total_steps} steps | {estimated_tokens} tokens
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Références Croisées

- Hybrid Parallelism Engine : [framework/hybrid-parallelism-engine.md](../hybrid-parallelism-engine.md) (BM-58) — étend le boomerang avec DAG parallèle/séquentiel/opportuniste
- Subagent Orchestration : [framework/workflows/subagent-orchestration.md](subagent-orchestration.md) (BM-19)
- Orchestrator Gateway : [framework/orchestrator-gateway.md](../orchestrator-gateway.md) (BM-53) — supervision des boomerang steps
- Event Log : [framework/event-log-shared-state.md](../event-log-shared-state.md) (BM-59) — événements de step completion


*BM-11 Boomerang Orchestration Workflow | framework/workflows/boomerang-orchestration.md*
