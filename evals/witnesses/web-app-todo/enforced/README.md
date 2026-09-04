# Bras « enforced » — mécanisme committé

Artefacts reproductibles du bras `enforced` pré-enregistré dans
`../../../reports/2026-09-04/PREREGISTRATION.md`. Un run `enforced` doit
pouvoir être rejoué sans rien reconstruire à la main.

## Principe

`enforced` = le bras `activated` (même directive verbatim injectée par le hook
SessionStart d'`../ACTIVATION.md`, même prompt de tâche) **plus** les deux
hooks bloquants que le kit émet pour un projet enrôlé (`grimoire host sync
--host claude`, `hosts/collect.py::governance_hooks`) :

| Hook | Décision du kit | Effet |
| --- | --- | --- |
| `PreToolUse` (Bash, Edit, Write, MultiEdit, NotebookEdit) | `grimoire.tool-policy` | `permissionDecision: deny` sur les mutations destructrices et les accès secrets |
| `Stop` | `grimoire.evidence-gate` | `decision: block` tant que les gates de preuve de la tâche courante sont rouges |

Deux conditions rendent le gate Stop réellement opposable, et l'installateur
les pose toutes deux :

- **profil `governed`** (`grimoire standard init . --profile governed`) : au
  profil `starter` du bras `activated`, le gate Stop se contente d'un message
  non bloquant (`decide_evidence_gate`) ;
- **tâche `bootstrap` en `in_progress`** dans `_grimoire/standard/task-board.yaml` :
  à l'état `proposed`, aucune preuve n'est due et le gate ne protège rien.

Les hooks consultatifs du kit (SessionStart persona, UserPromptSubmit,
PostToolUse, PreCompact, SubagentStop) et le bloc `permissions` émis par
`host sync` ne sont **pas** installés : la variable mesurée est le blocage.

## Installation dans une copie baseline

```bash
cp -r evals/witnesses/web-app-todo/app "$RUN_DIR"
cd "$RUN_DIR"
grimoire init . -a web-app -b local
grimoire standard init . --profile governed
cd -
evals/witnesses/web-app-todo/enforced/install.sh "$RUN_DIR"
```

L'installateur refuse une copie non enrôlée ou hors profil `governed`. Les
commandes `grimoire-hook` doivent être résolues par le `PATH` de la session
de run (venv du kit épinglé).

## Lecture

Le ledger `_grimoire-output/traces/traces.jsonl` du run porte chaque décision
(`pre_tool_use` allow/block, `stop` block) : c'est ce que `evals/runner.py`
agrège dans `governance.json`. Vérifié à blanc le 2026-09-04 (voir le journal
de la pré-inscription) : deny effectif sous `--dangerously-skip-permissions`,
Stop bloqué puis reprise de l'agent.
