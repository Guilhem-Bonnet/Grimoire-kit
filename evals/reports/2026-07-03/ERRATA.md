# Errata — rapport de campagne 2026-07-03

Le rapport `report.md` est publié et n'est pas réécrit (discipline de
pré-enregistrement) ; les corrections sont consignées ici.

## 2026-07-13 — `verify_ok`/`gate_ok` collectés sous le mauvais `task_id`

**Constat.** `evals/collect.py` (version de la campagne, schéma
`grimoire-evals-run-record/v1`) passait le **label de la tâche
d'évaluation** (`feat-due-dates`, `fix-n-plus-one`, ...) comme `task_id` à
`verify_standard_profile` et `check_evidence_gates`. Or les artefacts du
standard dans une copie enrôlée vivent sous l'id **`bootstrap`** (celui que
scaffolde `grimoire standard init`). Verify et gate étaient donc évalués
contre des enveloppes inexistantes.

**Impact.** Les compteurs instrumentaux du bras governed sont contaminés :
le `verify_ok=false` avec « 2 erreurs missing evidence » rapporté partout
(§Le constat central) reflète en partie l'artefact de collecte, pas
uniquement l'état des copies de run. Ces compteurs ne doivent pas être
réutilisés tels quels pour des comparaisons inter-campagnes.

**Ce qui tient.** Le constat « zéro engagement du standard » reste étayé
indépendamment du bug, par l'inspection directe des artefacts des 40 runs
governed : aucune enveloppe de tâche ni evidence-pack rempli, aucun appel
aux gates dans les sessions. Le verdict pré-enregistré de la campagne
(« effet non démontré ») repose sur les métriques externes (complétion,
régressions, coût), non affectées par le bug.

**Correctif.** `evals/collect.py` évalue désormais verify/gate sur un
`standard_task_id` distinct du label d'évaluation (défaut `bootstrap`,
flag `--standard-task-id`) — schéma `grimoire-evals-run-record/v2`.
