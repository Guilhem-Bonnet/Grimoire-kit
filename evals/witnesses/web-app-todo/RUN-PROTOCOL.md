# Protocole de run — témoin `web-app-todo`

> Comment exécuter un run d'évaluation à partir de la baseline figée.
> Applique `docs/evals-protocol.md`. À lire une fois la baseline construite
> (voir `SPEC.md`).

## Unité de mesure

Un **run** = un triplet `(tâche, bras, répétition)`. Il part toujours d'une
**copie propre** de la baseline figée — jamais d'un état déjà modifié par un
run précédent.

```text
evals/runs/<date>/<task-id>/<arm>/rep-<n>/    # non committé (dans .gitignore)
```

## Préparation d'un run

```bash
# 1. Copie propre de la baseline
cp -r evals/witnesses/web-app-todo/app "$RUN_DIR"
cd "$RUN_DIR"

# 2. Enrôlement — pour les bras governed ET activated
#    (bras baseline : sauter cette étape, aucun artefact standard)
#    NB : `--yes` n'existe pas dans le CLI 3.18.0 ; archétype et backend
#    explicites pour un enrôlement déterministe et non interactif.
grimoire init . -a web-app -b local                 # projet Grimoire minimal
grimoire standard init . --needs solo-prototyping   # profil starter + gates

# 3. Activation — SEULEMENT pour le bras activated (voir ACTIVATION.md)
#    Installe .claude/settings.json + hook SessionStart dans la copie.
evals/witnesses/web-app-todo/activated/install.sh "$RUN_DIR"
```

Le bras `baseline` ne reçoit **aucun** artefact du standard. C'est la seule
différence entre les deux bras : même code de départ, même tâche, même modèle,
même budget.

## Exécution

L'agent (session Claude Code / assistant IDE) reçoit **exactement** le champ
`prompt` de la tâche depuis `evals/tasks/web-app-todo.yaml`, sans indice
supplémentaire. Il travaille jusqu'à ce qu'il déclare « terminé ».

- Bras `governed` : l'agent dispose des gates (`grimoire standard gate check`),
  du protocole de preuve, du score. Rien ne l'oblige à les utiliser — on mesure
  s'ils changent le résultat.
- Bras `baseline` : l'agent travaille sans aucun de ces garde-fous.

Fixer et **journaliser** : modèle, température, version du kit (doivent être
identiques entre les deux bras d'une même tâche).

## Collecte (fin de run)

```bash
# Métriques standard (verify / score / gate) — null pour le bras baseline
python evals/collect.py \
  --project "$RUN_DIR" --witness web-app-todo \
  --task "$TASK_ID" --arm "$ARM" \
  --out "evals/runs/<date>/$TASK_ID/$ARM/rep-$N.json"
```

Puis renseigner **à la main** dans le run-record les métriques externes que le
collecteur laisse à `null` :

| Champ | Comment le mesurer |
|---|---|
| `completed` | la tâche est-elle livrée ? Jugement selon la grille pré-enregistrée `JUDGING.md` (stricte) |
| `tests_green` | `go test ./... && npm test` sur le run — vert ? |
| `regressions` | nombre de tests de la baseline cassés par le diff |
| `tokens_cost` | tokens consommés par la session (registre LLM / rapport de session) |
| `human_interventions` | nombre de relances/corrections humaines nécessaires |

Le collecteur n'invente rien : ce qui n'est pas mesurable automatiquement reste
`null` jusqu'à saisie de l'opérateur.

## Taille du pilote vs campagne

- **Pilote (validation de mécanique)** : 2 tâches × 2 bras × 3 répétitions = 12
  runs. Objectif : vérifier que la boucle produit des run-records exploitables
  et que les deux bras sont réellement différents — **pas** de conclusion.
  Tâches suggérées pour le pilote : `fix-n-plus-one` (bug objectif, régression
  mesurable) et `sec-rate-limit` (feature sécurité, gate pertinent).
- **Campagne complète** : 8 tâches × 2 bras × ≥5 répétitions = ≥80 runs, selon
  le critère de succès pré-enregistré (`docs/evals-protocol.md` §Critère).

## Agrégation

En fin de campagne : `evals/reports/<date>/report.md` agrège **toutes** les
exécutions (pas de cherry-picking), compare les bras métrique par métrique, et
conclut honnêtement — y compris « effet non démontré » si c'est le cas.
