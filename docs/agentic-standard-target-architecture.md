# Schéma et documentation cible du standard agentique

Cette page décrit la cible d’architecture du standard agentique Grimoire. Elle complète le guide d’intégration existant en décrivant les artefacts futurs, leurs relations et les règles de validation attendues.

## Contrat cible

Le contrat machine-readable de référence est :

```text
framework/agentic-standard/target-schema.yaml
```

Il définit :

- les principes normatifs ;
- les obligations par profil ;
- les artefacts cibles ;
- les checks attendus ;
- les commandes CLI prévues ;
- le flux runtime ;
- les critères de validation.

## Vue d’ensemble

```text
standard-profile.yaml
  ├─ mission-brief.md
  ├─ task-board.yaml
  │   └─ task-envelope.md
  │       └─ evidence-pack.md
  ├─ memory-policy.yaml
  ├─ knowledge-source-registry.yaml
  ├─ llm-provider-registry.yaml
  ├─ context-contract.yaml
  │   └─ context-bundle.yaml
  ├─ orchestration-policy.yaml
  ├─ evidence-gates.yaml
  ├─ pattern-catalog.yaml
  ├─ knowledge-graph-manifest.yaml
  ├─ compliance-score.yaml
  └─ remediation-plan.yaml
```

## Artefacts cibles

| Artefact | Chemin cible | Rôle |
|---|---|---|
| Task board | `_grimoire/standard/task-board.yaml` | Kanban normatif, cycle de vie, blockers, owners et evidence links. |
| Memory policy | `_grimoire/standard/memory-policy.yaml` | Règles mémoire par niveau : scope, fraîcheur, trust, rétention. |
| Context contract | `_grimoire/standard/context-contract.yaml` | Règles d’assemblage du contexte par tâche. |
| Context bundle | `_grimoire-output/context/{task_id}/context-bundle.yaml` | Contexte calculé, traçable, vérifiable. |
| Orchestration policy | `_grimoire/standard/orchestration-policy.yaml` | Rôles, routing, handoffs, escalation et review gates. |
| Evidence gates | `_grimoire/standard/evidence-gates.yaml` | FSM de transitions conditionnées par preuves. |
| Pattern catalog | `_grimoire/standard/pattern-catalog.yaml` | Catalogue exécutable de patterns normatifs. |
| Knowledge graph manifest | `_grimoire/standard/knowledge-graph-manifest.yaml` | Index documents → concepts → obligations → checks. |
| Compliance score | `_grimoire/standard/compliance-score.yaml` | Pondérations et seuils par profil. |
| Remediation plan | `_grimoire/standard/remediation-plan.yaml` | Corrections produites par audit. |

## Profils

### `minimal`

Profil d’adoption. Il exige les artefacts de base, un provider registry, un knowledge registry et un audit consultatif.

### `orchestrated`

Profil de travail réel. Il ajoute board, memory policy, context contract et evidence gates. Les gates peuvent avertir sans bloquer toutes les transitions.

### `governed`

Profil de release. Il ajoute orchestration policy, pattern catalog, knowledge graph, score, remediation et CI release gate. Les erreurs doivent bloquer la release.

## Flux runtime cible

1. Charger la mission et le profil.
2. Sélectionner une tâche depuis le board.
3. Résoudre memory, knowledge et provider policies.
4. Construire un context bundle.
5. Déduire l’orchestration applicable.
6. Vérifier les gates d’évidence.
7. Calculer l’audit et le score.
8. Produire un plan de remediation si nécessaire.
9. Bloquer ou autoriser la transition selon le profil.

## Règles de cohérence

- Un `task_id` doit être unique, stable et compatible avec les chemins sûrs.
- Une tâche en `review`, `accepted` ou `released` doit référencer un evidence pack.
- Un context bundle doit lister ses sources et leur ordre de priorité.
- Une source memory ou knowledge doit respecter freshness, trust et scope.
- Une route d’orchestration doit pointer vers un provider déclaré.
- Un score insuffisant doit générer une remediation.
- En profil `governed`, une erreur d’audit doit bloquer la release.

## CLI cible

```bash
grimoire standard board verify
grimoire standard task create
grimoire standard memory verify
grimoire standard context build --task-id bootstrap
grimoire standard context verify --task-id bootstrap
grimoire standard gate check --task-id bootstrap
grimoire standard score
grimoire standard fix --dry-run
grimoire standard fix --apply
grimoire pattern list
grimoire pattern show <pattern-id>
grimoire pattern apply <pattern-id>
```

## Adoption dans un projet consommateur

Un projet consommateur comme Grimoire Forge devra progressivement ajouter :

```text
_grimoire/standard/task-board.yaml
_grimoire/standard/memory-policy.yaml
_grimoire/standard/context-contract.yaml
_grimoire/standard/orchestration-policy.yaml
_grimoire/standard/evidence-gates.yaml
_grimoire/standard/pattern-catalog.yaml
_grimoire/standard/knowledge-graph-manifest.yaml
_grimoire/standard/compliance-score.yaml
```

La première cible stable est `orchestrated`. La cible finale est `governed`.

