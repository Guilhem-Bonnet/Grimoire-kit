# Presentation & Decouverte

Cette page est le point d'entree pour comprendre Grimoire Kit rapidement, sans ouvrir un diaporama.

Elle est pensee pour deux usages:

- presenter le projet en session live,
- guider un visiteur qui decouvre Grimoire pour la premiere fois.

## Pourquoi Grimoire

Grimoire Kit repond a un probleme simple: l'IA produit vite, mais la continuite de travail est souvent fragile.

Les equipes ont besoin d'un cadre qui combine:

- specialisation des roles agents,
- memoire inter-sessions,
- orchestration explicite,
- verification avant validation.

## Comment ca fonctionne

```mermaid
flowchart LR
    A[Intention] --> B[Routing]
    B --> C[Orchestration]
    C --> D[Execution]
    D --> E[Verification]
    E --> F[Memoire]
    F --> G[Amelioration continue]
```

## Deux modes d'usage

=== "Mode Presentation"

    Utilise cette page comme fil narratif lors d'une demo:

    - commence par le probleme,
    - enchaine sur la boucle de fonctionnement,
    - termine par le comparatif et le parcours de mise en action.

=== "Mode Decouverte"

    Si tu visites le site pour comprendre Grimoire:

    - lis le "Pourquoi Grimoire",
    - parcours le schema "Comment ca fonctionne",
    - choisis un chemin adapte a ton profil dans "Parcours recommandes".

## Positionnement: Grimoire, BMAD, Devkit, OpenClaw

| Criteres | Grimoire Kit | BMAD | Devkit | OpenClaw |
| --- | --- | --- | --- | --- |
| Nature principale | Systeme operationnel agentique complet | Methode de cadrage | Boite a outils technique | Runtime oriente execution |
| Point fort | Continuite intention -> execution -> verification | Structuration methodologique | Flexibilite de construction | Execution sandboxee |
| Memoire projet | Nativement centrale | Variable selon implementation | Souvent a assembler | Plutot orientee run |
| Gouvernance qualite | Integree au workflow | Forte sur le cadre | Depend de l'equipe | Souvent centree execution |
| Usage ideal | Delivery fiable et durable | Organisation des pratiques | Assemblage sur mesure | Automatisation ciblee |

## Philosophie Grimoire

Grimoire privilegie la clarte operationnelle:

- expliciter qui fait quoi,
- rendre visibles les preuves de completion,
- conserver la memoire utile,
- reduire la re-fragmentation du travail.

## Parcours recommandes

| Profil | Point de depart | Suite recommandee |
| --- | --- | --- |
| Dev solo | [Quick Start](getting-started.md) | [Concepts](concepts.md), [Workflow Design Patterns](workflow-design-patterns.md) |
| Equipe produit | [Concepts](concepts.md) | [Taxonomie Workflows](workflow-taxonomy.md), [Memoire](memory-system.md) |
| Manager / Sponsor | Cette page | [Architecture](concepts.md), [FAQ](faq.md), [Troubleshooting](troubleshooting.md) |

## Tutoriel visuel guide

Pour un parcours explicatif complet:

1. comprendre la boucle de fonctionnement sur cette page,
2. voir les concepts d'architecture et de memoire,
3. executer un premier scenario via le quick start,
4. valider les pratiques de qualite via les guides workflows.

## Aller plus loin

- [Architecture & Concepts](concepts.md)
- [Memoire](memory-system.md)
- [Workflow Design Patterns](workflow-design-patterns.md)
- [Quick Start](getting-started.md)
- [FAQ](faq.md)
