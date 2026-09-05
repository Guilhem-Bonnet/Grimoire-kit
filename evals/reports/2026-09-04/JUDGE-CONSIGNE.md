# Consigne de jugement — campagne 2026-09-04

Consigne unique remise à chaque juge (agent LLM), identique pour tous les
runs, **sans mention du bras**. Le juge reçoit : l'identifiant de tâche, le
prompt de la tâche, la section correspondante de `JUDGING.md`, le
`diff.patch` du run (baseline → état final, hors artefacts du standard et
configuration d'hôte) et le `mechanical.json` (suites réellement exécutées,
surcouche des tests baseline).

## Ce que le juge rend

Un JSON strict :

```json
{
  "completed": false,
  "criteria": {"1": false, "2": true, "3": null},
  "regressions_primary": 0,
  "regressions_hard": 0,
  "regressions_adapted": 0,
  "notes": "une ligne par critère, factuelle, citant le diff"
}
```

## Règles

- `completed` = conjonction de TOUS les critères de la tâche dans
  `JUDGING.md`. Un critère absent, partiel ou non prouvé par le diff donne
  `false`. Les cas limites sont tranchés `false` et notés.
- Un critère « testé » exige un test présent dans le diff ET des suites
  vertes dans `mechanical.json` (`tests_green`). Suites rouges ⇒ `completed`
  `false`.
- `regressions_primary` (règle 2026-07-03) : nombre de tests de la baseline
  (6 Go : `api/*_test.go` ; 4 front : `web/src/**/*.test.tsx`) cassés,
  supprimés ou affaiblis par le diff ; sur les tâches `refactor-*`, toute
  modification d'un test baseline compte.
- `regressions_hard` : tests baseline cassés sur l'état final du run (suite
  rouge, compilation impossible) ou supprimés (fichier ou fonction de test
  absents) — `mechanical.json.baseline_summary` fait foi pour le mécanique ;
  le juge complète par lecture du diff (test vidé, assertion retirée). La
  surcouche `overlay_green` est informative : elle est rouge dès que le
  contrat `Store` s'étend (fakeStore complété par l'agent), ce qui n'est pas
  une casse.
- `regressions_adapted` : tests baseline modifiés, suites vertes, contrat
  préservé ou explicitement changé par la tâche.
- Le juge ne lit jamais les artefacts du standard ni la configuration d'hôte
  (exclus du diff) et ne reçoit aucune information sur le bras.
