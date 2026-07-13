# Grille de jugement pré-enregistrée — témoin `web-app-todo`

> Pré-enregistrée le 2026-07-03, avant la campagne complète, à partir des
> critères d'acceptation de `evals/tasks/web-app-todo.yaml`. L'opérateur
> applique ces listes **strictement** : un critère absent ou partiel donne
> `completed=false`, même si le code livré est techniquement correct.
> Motivation : le pilote du 2026-07-03 a montré une zone de jugement
> (fix correct sans le test exigé) qu'il faut trancher avant la campagne
> pour éliminer le biais de l'opérateur.
>
> **Amendée le 2026-07-12 (protocole v2)**, avant la deuxième campagne :
> comptage des régressions scindé en « dures » vs « adaptations » (aligné sur
> `docs/evals-protocol.md` §Comptage), et critère 3 de `refactor-handlers`
> requalifié de éliminatoire en pondéré — en juillet cette seule parenthèse
> décidait du 0/10 du lot alors que les refactorings étaient corrects.

## Critère de succès de la campagne (confirmation)

Le seuil pré-enregistré par `docs/evals-protocol.md` §Critère (v2) est
confirmé tel quel : le bras `activated` est déclaré « utile » si les
**régressions dures** baissent d'au moins 30 % relatif vs `baseline`, sans
baisse du taux de complétion ni hausse du coût tokens de plus de 25 %. Tout
autre résultat = « non démontré ».

## Règles transverses

- `completed` = conjonction de TOUS les points de la tâche (ci-dessous).
- `tests_green` = `go test ./...` (image Docker de la version du `go.mod` du
  run) et `npm test` verts sur l'état final du run.
- Régressions (v2) — deux compteurs exclusifs par run, sur les tests de la
  baseline (6 Go + 4 front) :
  - `regressions_hard` = tests ou builds baseline réellement cassés (suite
    rouge sur l'état final), supprimés, ou affaiblis (assertion retirée, test
    désactivé/skippé, tolérance élargie) — qu'ils aient été modifiés ou non ;
  - `regressions_adapted` = tests baseline modifiés avec suite complète verte
    et force du contrat préservée (mêmes comportements couverts, assertions
    équivalentes ou plus strictes).
  L'adaptation d'un test baseline n'est légitime que si la tâche change
  explicitement le contrat testé ; sur les tâches `refactor-*`, toute
  modification de test est au minimum comptée en `regressions_adapted`, et en
  `regressions_hard` si le contrat est affaibli. Le critère principal de la
  campagne ne porte que sur `regressions_hard`
  (`docs/evals-protocol.md` §Comptage).
- Preuve par inspection du diff + exécution ; les cas limites sont tranchés
  vers `false` et notés dans le journal de campagne.

## Par tâche

### feat-due-dates

1. Migration SQL nouvelle, numérotée, avec paire `.up.sql`/`.down.sql`.
2. Tests API création ET mise à jour, avec et sans échéance.
3. Tri par échéance côté SPA couvert par un test frontend.

### feat-bulk-complete

1. Endpoint batch idempotent, borné (rejet ou troncature au-delà de 100 ids),
   les trois propriétés testées.
2. État UI cohérent après échec partiel, couvert par un test frontend.

### fix-timezone-display

1. Stockage inchangé : aucune modification de schéma/migration ni du format
   UTC en base ; un test de non-régression du stockage existe.
2. Test d'affichage couvrant au moins deux fuseaux horaires.

### fix-n-plus-one

1. Nombre de requêtes constant vérifié par un test explicite (comptage).
2. Résultat identique à l'existant : tests baseline verts sans
   affaiblissement, Title-case des tags préservé.

### refactor-handlers

1. Package dédié de validation ; plus aucune validation inline dans les
   handlers.
2. Tests existants verts **sans modification**.
3. Couverture de la validation **non réduite** : les comportements de
   validation couverts par la baseline restent couverts sur l'état final
   (par les tests existants verts, par des tests déplacés, ou par des tests
   du nouveau package).

Amendement v2 (2026-07-12) : pour cette tâche, `completed` = points 1 à 3.
La présence de **tests dédiés dans le nouveau package** n'est plus une
condition de `completed` (en v1, cette parenthèse du point 3 décidait seule
du 0/10 du lot) ; elle est jugée et rapportée séparément comme métrique
secondaire `validation_tests_added` (booléen par run), sans entrer dans le
verdict. Les points 1 et 2 restent appliqués strictement, comme le reste de
la grille.

### refactor-api-client

1. Zéro `fetch` direct restant hors du client centralisé (vérifié par grep
   sur `web/src`, hors client lui-même).
2. Comportement d'erreur testé pour 401, 500 et erreur réseau.

### migrate-go-version

1. `go.mod` en 1.23 (ou plus), build et tests verts sur la nouvelle version
   (image `golang:1.23`).
2. Dépréciations traitées : plus d'usage de `io/ioutil` ni de `strings.Title`.
3. Changelog des dépréciations traitées présent (fichier ou section README).

### sec-rate-limit

1. Test de dépassement → 429 sur endpoint d'écriture.
2. Rate-limit configurable ET désactivable en test.
3. Configuration documentée (README ou commentaire de configuration dans le
   code — précédent du pilote accepté).
