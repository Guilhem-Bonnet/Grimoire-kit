<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/branch.svg" width="32" height="32" alt=""> State Checkpoint & Resume Protocol

> **BM-06** — Persistence d'état entre sessions pour les workflows longs.
>
> **Problème résolu** : Un workflow en 8 steps qui crash au step 5 repart de zéro.
> Avec les checkpoints, il repart du step 5 avec toutes les variables en mémoire.
>
> **Inspiré de** : LangGraph state persistence, Redis checkpointing.

<img src="../../docs/assets/divider.svg" width="100%" alt="">


## <img src="../../docs/assets/icons/brain.svg" width="28" height="28" alt=""> Structure d'un State File

Chaque run de workflow `workflow.xml` crée automatiquement :

```
_grimoire-output/.runs/
└── {workflow-name}-{timestamp}/
    ├── state.json          ← Checkpoint machine-readable
    ├── workflow-status.md  ← Status human-readable (BM-02)
    └── artefacts/          ← Copies des outputs intermédiaires
        ├── step-01-output.md
        ├── step-02-output.md
        └── ...
```

### Format `state.json`

```json
{
  "run_id": "{{workflow_name}}-{{timestamp}}",
  "workflow_path": "{{workflow_yaml_path}}",
  "session_branch": "{{branch_name}}",
  "started_at": "{{iso_datetime}}",
  "last_checkpoint": "{{iso_datetime}}",
  "status": "running | completed | failed | paused",

  "steps_completed": [1, 2, 3],
  "current_step": 4,
  "total_steps": 8,
  "resume_from_step": 4,

  "session_variables": {
    "user_name": "{{user_name}}",
    "project_name": "{{project_name}}",
    "workflow_specific_var_1": "{{value}}",
    "workflow_specific_var_2": "{{value}}"
  },

  "artefacts_produced": [
    {"step": 1, "name": "{{artefact_name}}", "path": "{{artefact_path}}"},
    {"step": 2, "name": "{{artefact_name}}", "path": "{{artefact_path}}"}
  ],

  "checkpoint_id": "{{sha256_short}}",

  "issues": [],

  "agent_notes": "Notes libres de l'agent sur l'exécution"
}
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/branch.svg" width="28" height="28" alt=""> Checkpoint ID (BM-26)

Chaque checkpoint est identifié par un `checkpoint_id` = `sha256(run_id + step + variables_json)[:6]`.

En bash :
```bash
checkpoint_id=$(echo "${run_id}:${step}:${variables_json}" | sha256sum | cut -c1-6)
```

En Python :
```python
import hashlib, json
def make_checkpoint_id(run_id: str, step: int, variables: dict) -> str:
    payload = f"{run_id}:{step}:{json.dumps(variables, sort_keys=True)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:6]
```

### Reprise par `checkpoint_id`

```bash
# Reprendre un workflow depuis un checkpoint précis
grimoire-init.sh resume --checkpoint a3f9b2

# Lister tous les checkpoints disponibles
grimoire-init.sh resume --list

# Reprendre le dernier run non-terminé (sans checkpoint_id)
grimoire-init.sh resume
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/branch.svg" width="28" height="28" alt=""> Protocole de Checkpoint

### Quand créer un checkpoint ?

Le workflow engine `workflow.xml` crée un checkpoint **après chaque step complété**.

Dans un step YAML, ajouter en fin de step :

```yaml
- step: "create-architecture"
  type: generate
  # ... instructions du step ...
  checkpoint:
    save: true
    variables_to_persist:
      - "architecture_doc_path"
      - "tech_decisions"
      - "selected_patterns"
```

### Quand reprendre depuis un checkpoint ?

L'agent détecte automatiquement un run non-terminé si :
1. Un fichier `state.json` avec `"status": "running"` ou `"status": "failed"` existe
2. L'utilisateur dit "reprendre" / "continue" / "resume"

**Procédure de reprise :**

```
1. Lire _grimoire-output/.runs/{run_id}/state.json
2. Restaurer session_variables dans la session courante
3. Charger les artefacts produits (pour contexte)
4. Reprendre à l'étape steps_completed[-1] + 1
5. Afficher : "▶️ Reprise du run {run_id} au step {resume_step}/{total_steps}"
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Intégration dans `workflow.xml`

Le workflow engine doit effectuer ces actions automatiquement :

```xml
<!-- Avant chaque step -->
<checkpoint-save>
  <run-id>{{run_id}}</run-id>
  <step-number>{{current_step}}</step-number>
  <variables>{{session_variables}}</variables>
  <status>running</status>
</checkpoint-save>

<!-- Après le step terminé -->
<checkpoint-update>
  <step-completed>{{current_step}}</step-completed>
  <artefact>{{step_output_path}}</artefact>
  <next-step>{{current_step + 1}}</next-step>
</checkpoint-update>

<!-- À la fin du workflow -->
<checkpoint-close>
  <status>completed</status>
  <summary>{{run_summary}}</summary>
</checkpoint-close>
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/moon.svg" width="28" height="28" alt=""> Gestion des Branches de Session (BM-16)

Les checkpoints sont scoped par session branch :

```bash
# Branch par défaut : main
_grimoire-output/.runs/main/dev-story-20260227-143022/state.json

# Branch feature
_grimoire-output/.runs/feature-auth/dev-story-20260227-150000/state.json

# On peut avoir deux runs du même workflow en parallèle sur des branches différentes
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Cleanup

Les runs complétés (`"status": "completed"`) sont conservés **7 jours** puis archivés dans `_grimoire-output/.runs/archive/`. 
Les runs failed sont conservés **30 jours** pour forensics.

Nettoyage manuel :
```bash
# Lister tous les runs
ls -la _grimoire-output/.runs/

# Nettoyer les runs complétés > 7 jours
find _grimoire-output/.runs -name "state.json" -mtime +7 -path "*/main/*" | xargs -I{} dirname {} | xargs rm -rf
```


*BM-06 State Checkpoint & Resume Protocol | framework/workflows/state-checkpoint.md*
