# ADR-007 — Finir ce qu'ADR-005 a décidé : le Mission Ledger comme seule source de tâches

- **Statut** : accepté (2026-09-17)
- **Contexte** : Plan 2026-Q4, phase 4, lot 4.1 (issue #559), préalable bloquant des
  lots 4.2 à 4.6 ; constat d'origine issue #521

## Contexte

ADR-005 (2026-08-27) a déjà tranché : **le `MissionLedger` est la source de
vérité, `_grimoire/standard/task-board.yaml` en est une projection exportée**.
La conversion d'états (9 → 8), la projection (`missions/board.py::build_board`/
`write_board`) et la réprojection à chaque écriture (`missions/service.py::TaskService`)
sont écrites, testées, et n'ont pas besoin d'être refaites.

Ce que le code montre, mesuré et non supposé (issue #521, audit cockpit sur la
Forge, kit 3.51.1) : **la décision d'ADR-005 n'est pas appliquée au point
d'entrée**. `grimoire standard init` scaffold `task-board.yaml` en copiant tel
quel `framework/agentic-standard/templates/task-board.yaml` — un fichier
statique portant une tâche `bootstrap` à l'état `proposed` — sans jamais
ouvrir de `MissionLedger`. Résultat vérifié dans le code : tout projet gouverné
par le standard (profil `governed`, dont la Forge) a un board sans ledger.

`src/grimoire/tools/workspace_api.py::tasks_view` (lu par l'espace Exécuter et
le panneau Preuves du cockpit) ne lit **que** le ledger via `TaskService` :
sans lui, elle rend `{"ledger": false, "note": "aucun Mission Ledger — \`grimoire
task add\` en ouvre un"}` — le message exact que #521 signale. Elle ne
retombe jamais sur `task-board.yaml`, ce qui est cohérent avec ADR-005 (le YAML
est une sortie, pas une entrée) mais suppose que le ledger existe — ce que
`standard init` ne garantit pas.

En parallèle, `src/grimoire/missions/task_flow_adapter.py::import_task_flow_events`
importe déjà, de façon idempotente (empreinte SHA-256 par événement), des
événements runtime (`_grimoire-runtime-output/task-flow/events.jsonl`, hooks
et tâches VS Code) vers le ledger. C'est un troisième flux, orthogonal aux deux
systèmes de tâches proprement dits (il ne lit ni n'écrit `task-board.yaml`) ;
il ne change pas de comportement dans cette ADR.

## Décision

**On ne réouvre pas ADR-005.** Le Mission Ledger reste l'unique source ; le
board du standard reste sa projection. Le lot 4.1 ferme l'écart d'implémentation
qui empêche cette décision de produire son effet :

1. **`grimoire standard init` écrit désormais la tâche `bootstrap` via
   `TaskService`/`MissionLedger`** (`ledger.create_mission` +
   `ledger.create_task`, puis `write_board(build_board(...))`) au lieu de
   copier le template YAML statique. Tout projet nouvellement enrôlé a un
   ledger et un board cohérents dès l'init.

2. **Migration idempotente et réversible pour les projets déjà scaffoldés**
   (`src/grimoire/missions/task_unification.py`, nouveau module) : pour
   chaque tâche du board absente du ledger (appariée par `task_id`), reconstruit
   un `MissionTask` via `task_state_of()` — la fonction inverse, lossy par
   construction, qu'ADR-005 avait déjà écrite pour cet usage — en conservant
   `task_id`, titre, `acceptance_criteria`, propriétaire, et les chemins
   d'enveloppe déjà déterministes (`_grimoire-output/{context,decisions,evidence}/<task_id>/...`).
   Une `Mission` de rattachement est créée si nécessaire (même patron que
   `_DEFAULT_MISSION_ID` de `task_flow_adapter.py`). Le board est ensuite
   régénéré depuis le ledger (`build_board`/`write_board`) pour que les deux
   concordent à l'octet près à partir de cet instant.

   Snapshot avant écriture et restauration par horodatage suivent exactement
   le patron déjà éprouvé de `grimoire migrate`
   (`cmd_migrate.py::_snapshot`/`apply_migration`/`restore_migration`,
   manifeste JSON) plutôt que d'en inventer un nouveau.

3. **`grimoire up` déclenche la migration** quand un projet est enrôlé
   (`standard_state.is_standard_enrolled`) avec un board mais sans ledger
   complet — en meilleur effort, comme le reste de son pipeline.

4. **`grimoire doctor` signale la divergence** (board sans ledger, ou ledger
   incomplet par rapport au board) par un `FAIL` nommé et un remède, jamais
   une correction automatique.

5. **Aucun changement requis côté cockpit ou CLI facades.**
   `workspace_api.tasks_view`/`task_view` (Exécuter, Preuves), `grimoire task`
   et `grimoire standard gate check` lisent déjà la bonne source (le ledger
   pour les tâches, `task-board.yaml` pour le gate) ; ils étaient corrects,
   seule l'alimentation manquait. Un test le vérifie explicitement plutôt que
   de le supposer.

6. **Champ `finition` préparé, non exploité.** `MissionTask` gagne un champ
   optionnel `finition: str = ""`, restreint à `{"", "maquette", "peaufine"}`,
   propagé dans la projection board. Aucune interface ne le lit encore — c'est
   le terrain du lot 4.3 (issue #561).

## Invariants opposables

- **I1 — Une tâche du standard a toujours une enveloppe et des gates.** Les
  chemins `_grimoire-output/{context,decisions,evidence}/<task_id>/...` et les
  portes de `_TRANSITIONS` ne changent pas ; la migration ne déplace ni ne
  supprime aucun pack de preuve existant.
- **I2 — Une tâche Mission Ledger a toujours un id, un mission_id (board) et un
  historique.** La migration crée de vraies tâches via `ledger.create_task`
  (qui journalise sa propre création) et une `Mission` de rattachement — jamais
  une tâche orpheline.
- **I3 — Aucune perte de donnée à la migration.** Chaque tâche du board devient
  une tâche du ledger ; un instantané précède toute écriture (`_snapshot`) ;
  rien sous `_grimoire-output/evidence/**` n'est lu en écriture.
- **I4 — Les hooks et `gate check` gardent leur comportement.** Le format et le
  schéma de `task-board.yaml` ne changent pas (mêmes clés, même cycle de vie) ;
  les suites `test_standard_state*.py`, `test_agentic_standard*.py`,
  `test_evidence_gate*.py` existantes restent vertes sans modification.
- **I5 — Idempotence et réversibilité.** Rejouer la migration sur un projet
  déjà migré ne change rien (tâches déjà présentes dans le ledger, appariées
  par id, ignorées) ; `restore` par horodatage remet le board d'avant en place
  et retire les tâches importées du ledger.

## Alternatives écartées

**Faire de `task-board.yaml` la source (à nouveau).** ADR-005 l'a déjà écarté
et rien n'a changé qui invaliderait ce constat : le YAML n'a ni claims, ni
dépendances, ni incidents, ni journal — il perdrait l'historique à chaque
écriture.

**Synchronisation continue bidirectionnelle.** Toujours sans autorité de
résolution de conflit définissable ; ADR-005 l'a déjà écarté pour cette raison,
qui tient encore.

**Un troisième modèle qui engloberait les deux.** `MissionTask` est déjà un
sur-ensemble strict des champs du board (table d'ADR-005). En construire un
troisième rouvrirait des surfaces d'écriture stables aujourd'hui (hooks, `gate
check`, vérificateurs du standard) pour un gain nul : le problème n'est pas le
modèle, c'est qu'un point d'entrée ne l'alimentait pas.

## Conséquences

- Tout projet gouverné existant doit passer par la migration une fois — faite
  automatiquement par `grimoire up`, ou à la main par la commande exposée par
  ce lot, avant que l'espace Exécuter et le panneau Preuves du cockpit
  affichent ses tâches.
- `doctor` peut désormais échouer sur un projet enrôlé jamais migré — attendu,
  nommé, avec un remède, pas un changement silencieux.
- Le lot 4.3 (finition propagée au kanban) trouve le champ déjà présent sur le
  schéma et la projection : il n'a plus qu'à le lire et l'écrire.

## Références

- Issues #559 (ce lot), #521 (constat d'origine), #554 (épic phase 4)
- ADR-005 — Le Mission Ledger est la source, le task board une projection
- `src/grimoire/missions/{ledger.py,board.py,schemas.py,service.py,task_flow_adapter.py}`
- `src/grimoire/core/standard_state.py`, `src/grimoire/tools/workspace_api.py`
- `src/grimoire/cli/cmd_migrate.py` — patron snapshot/apply/restore réutilisé
