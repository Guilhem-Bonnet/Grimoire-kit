# Créer un pack Grimoire — Quickstart

Ce guide explique comment créer un pack Grimoire simple depuis zéro.
Un pack est un ensemble d'agents, workflows, skills et politiques distribuable.

## Prérequis

```bash
pip install grimoire-kit
grimoire --version
```

## Étape 1 — Initialiser le projet

```bash
grimoire init my-pack
cd my-pack
```

Cette commande crée la structure suivante :

```
my-pack/
  pack.yaml          # manifest du pack
  .github/agents/    # définitions d'agents
  .github/skills/    # skills réutilisables
  .github/hooks/     # hooks lifecycle
  _grimoire-runtime/ # runtime et config
```

## Étape 2 — Déclarer le manifest

Éditez `pack.yaml` :

```yaml
name: my-pack
version: "1.0.0"
description: Mon premier pack Grimoire
author: votre-nom
schema_version: "grimoire.pack.v1"

components:
  - type: agent
    path: .github/agents/my-agent.agent.md
    role: dev

compatibility:
  grimoire_kit: ">=3.0.0"

policy:
  mutation_class: MUTATION_CONTROLLED
  requires_evidence: true
```

## Étape 3 — Valider le pack

```bash
grimoire doctor .
```

La commande `doctor` vérifie :

- le manifest est valide et complet
- tous les composants déclarés existent
- les dépendances sont satisfaites
- les digests sont cohérents

Exemple de sortie OK :

```
✅ pack.yaml — schema valid
✅ components — 1 found, 1 resolvable
✅ compatibility — grimoire-kit 3.4.2 >= 3.0.0
✅ policy — mutation_class acceptable
Doctor passed (0 errors, 0 warnings)
```

## Étape 4 — Tester en local

```bash
grimoire status .         # état du runtime local
grimoire missions list    # liste des missions actives
```

Pour tester un agent du pack :

```python
from grimoire import MissionLedger, EvidenceService, MissionState, TaskState

ledger = MissionLedger(".grimoire/ledger")
evidence = EvidenceService(".grimoire/evidence")

# Créer une mission de test
mission = ledger.create_mission("Pack smoke test", origin="my-pack")
ledger.transition_mission(mission.id, MissionState.OPEN, actor_id="test")

# Créer et progresser une tâche
task = ledger.create_task(mission.id, "Test agent", acceptance=("output verified",))
ledger.transition_task(task.id, TaskState.READY, actor_id="test")
ledger.claim_task(task.id, actor_id="test", host_id="claude-code-cli")
ledger.transition_task(task.id, TaskState.RUNNING, actor_id="test")
ledger.transition_task(task.id, TaskState.NEEDS_VERIFICATION, actor_id="test")

print(f"Task {task.id} ready for verification: {task.status}")
```

## Trust tiers

Les packs sont distribués avec un niveau de confiance :

| Tier | Prérequis | Droits |
|---|---|---|
| `untrusted` | digest + doctor | read, search uniquement |
| `community` | digest + doctor | mutation contrôlée |
| `verified` | digest + doctor + signature | mutation contrôlée |
| `internal` | aucun | tous droits |

Pour signer un pack (tier `verified`) :

```bash
grimoire pack sign --key ~/.grimoire/signing.key
```

## Référence

- [Configuration](config-reference.md) — `pack.yaml` champs complets
- [CLI](cli-reference.md) — toutes les commandes `grimoire`
- [API SDK](api-reference.md) — `MissionLedger`, `EvidenceService`, etc.
