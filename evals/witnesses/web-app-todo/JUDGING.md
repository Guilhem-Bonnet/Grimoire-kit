# Grille de jugement pré-enregistrée — témoin `web-app-todo`

> Pré-enregistrée le 2026-07-03, avant la campagne complète, à partir des
> critères d'acceptation de `evals/tasks/web-app-todo.yaml`. L'opérateur
> applique ces listes **strictement** : un critère absent ou partiel donne
> `completed=false`, même si le code livré est techniquement correct.
> Motivation : le pilote du 2026-07-03 a montré une zone de jugement
> (fix correct sans le test exigé) qu'il faut trancher avant la campagne
> pour éliminer le biais de l'opérateur.

## Critère de succès de la campagne (confirmation)

Le seuil proposé par `docs/evals-protocol.md` §Critère est confirmé tel quel :
le bras `governed` est déclaré « utile » si les régressions baissent d'au
moins 30 % relatif, sans baisse du taux de complétion ni hausse du coût
tokens de plus de 25 %. Tout autre résultat = « non démontré ».

## Règles transverses

- `completed` = conjonction de TOUS les points de la tâche (ci-dessous).
- `tests_green` = `go test ./...` (image Docker de la version du `go.mod` du
  run) et `npm test` verts sur l'état final du run.
- `regressions` = nombre de tests de la baseline (6 Go + 4 front) cassés,
  supprimés ou affaiblis par le diff. L'adaptation d'un test baseline n'est
  légitime que si la tâche change explicitement le contrat testé ; pour les
  tâches `refactor-*`, toute modification des tests existants compte comme
  régression (le contrat ne doit pas changer).
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
3. Couverture de la validation conservée ou accrue (tests du nouveau package).

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
