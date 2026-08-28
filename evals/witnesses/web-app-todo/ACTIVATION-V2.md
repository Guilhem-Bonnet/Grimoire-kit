# Bras « activé v2 » — pré-enregistrement (design figé, exécution à venir)

Pré-enregistré le 2026-07-12, **avant toute exécution** du bras
`activated-v2`. Aucun run de ce bras n'existe à la date de ce commit.
Suite du bras `activated` du 2026-07-09 (`ACTIVATION.md`, rapport
`evals/reports/2026-07-09/report.md` — PR #71) ; premier bras évalué
sous le critère de coût amendé **A1** (`docs/evals-protocol.md`,
journal des amendements).

## Objet du bras

Deux questions, hiérarchisées :

1. **Décisionnelle** — l'activation par hook SessionStart est-elle
   « utile » au sens du critère composite en vigueur (A1 : régressions
   −30 % relatif, complétion non dégradée, coût par tâche complétée ≤
   baseline) ? Le bras 2026-07-09 échouait uniquement sur l'ancienne
   composante coût brut ; A1 ayant été adopté avant toute donnée v2, un
   verdict positif ici est le premier claim publiable.
2. **Exploratoire** — quelle part de l'écart de complétion restant est
   due à la non-divulgation des critères d'acceptation (recommandation 4
   du rapport 2026-07-09) ? Les échecs résiduels du bras activé étaient
   concentrés sur des critères non devinables depuis le prompt.

## Design

| Paramètre | Valeur |
|---|---|
| Bras décisionnel `activated-v2` | 8 tâches × 5 répétitions = 40 runs, prompt strictement identique aux bras 2026-07-03/07-09 |
| Bras exploratoire `activated-v2-disclosed` | 8 tâches × 2 répétitions = 16 runs, prompt = prompt de tâche + section « Critères d'acceptation » (formulation figée dans le YAML avant exécution) |
| Baseline de comparaison | bras `baseline` de la campagne 2026-07-03 (mêmes pins) |
| Mécanisme d'activation | hook SessionStart identique à `ACTIVATION.md` (directive verbatim, enveloppe `bootstrap`) |
| Kit | grimoire-kit **3.18.0 pinné** via `evals/.venv` — même pin que les trois bras précédents ; l'isolement de la variable prime sur la fraîcheur du kit |
| Runner / modèle | Claude Code CLI et modèle **identiques aux campagnes précédentes** (CLI 2.1.101, `claude-sonnet-4-6`, `--max-turns 100`, timeout 1800 s) ; si l'un des deux n'est plus disponible au lancement, le remplacement est consigné ici AVANT exécution et la comparabilité inter-campagnes est requalifiée dans le rapport |
| Jugement | grille `JUDGING.md` inchangée, appliquée strictement, cas limites tranchés `false` |
| Agrégation | tous les runs, zéro exclusion ; pilote éventuel exclu et journalisé |
| Coût estimé | ≈ 45 $ (56 runs × ≈ 0,81 $) — à confirmer par l'opérateur au lancement |

## Règles décisionnelles (figées)

- Le **verdict d'utilité** est prononcé uniquement sur le bras
  décisionnel (40 runs à prompt identique), comparé à la baseline
  2026-07-03, selon le critère A1. Les deux lectures de coût (brut par
  run, par tâche complétée) sont rapportées.
- Le bras divulgué est **exploratoire par construction** (le prompt
  diffère de la baseline) : aucune composante du verdict ne peut s'y
  appuyer. Il produit une mesure descriptive : Δ complétion divulgué vs
  non-divulgué, par tâche.
- Comptage des régressions : règle primaire 2026-07-03 inchangée +
  comptage secondaire « cassé/supprimé » vs « adapté vert » (identique
  au bras 2026-07-09).
- Engagement : mesuré par artefacts (enveloppe remplie, lignes
  d'inventaire concrètes), critère mécanisme ≥ 80 % — reconduction de la
  mesure 2026-07-09, `gate_check_invoked` restant non mesurable depuis
  `claude -p --output-format json`.

## Hypothèses pré-enregistrées

- **H1 (réplication)** : le bras décisionnel réplique l'ordre de
  grandeur du bras 2026-07-09 (complétion ≥ 12/40, régressions dures ≤ 2).
  Une non-réplication franche invalide la stabilité du mécanisme et
  bloque tout claim, quel que soit le résultat du critère.
- **H2 (non-divulgation)** : le gain de complétion du bras divulgué se
  concentre sur les tâches à critères non devinables identifiées par le
  rapport 2026-07-09 : `refactor-handlers`, `fix-timezone-display`,
  `feat-due-dates`, `feat-bulk-complete`, `refactor-api-client`.
- **H3 (coût)** : sous A1, le coût par tâche complétée du bras
  décisionnel reste ≤ 3,68 $ (valeur baseline 2026-07-03).

## Menaces à la validité (anticipées)

- Comparaison inter-campagnes (pins identiques mais dates différentes) :
  une dérive du modèle servi ne peut être exclue — même limite que le
  rapport 2026-07-09, assumée et rappelée dans le rapport final.
- n = 5 par cellule décisionnelle : signal, pas de test statistique.
- Le bras divulgué (n = 2 par tâche) est descriptif uniquement.

## Publication

Rapport dans `evals/reports/<date>/report.md`, toutes exécutions
agrégées, verdict prononcé selon A1, résultat négatif publié tel quel.
Si le verdict est positif, le claim publiable est strictement borné à :
« sur ce témoin, avec ce runner et ce modèle, l'activation du standard
réduit les régressions et augmente la complétion à coût par tâche
complétée inférieur à la baseline » — pas de généralisation.

## Journal de lancement

- **2026-08-27** — Lancement de l'exécution. Vérification des pins :
  kit 3.18.0 (PyPI, `evals/.venv` reconstruit), runner Claude Code CLI
  2.1.101 (réinstallé épinglé via npm local, autoupdate désactivé),
  modèle `claude-sonnet-4-6` (disponibilité vérifiée par ping, 0,03 USD).
  **Aucun remplacement** — comparabilité inter-campagnes intacte.
  Formulation du prompt divulgué figée dans
  `evals/tasks/web-app-todo.yaml` (`disclosed_prompt_template`) par le
  même commit, avant tout run. Bras `activated-v2` et
  `activated-v2-disclosed` ajoutés à `arms` et au collecteur.
- **2026-08-27 (incident toolchain, avant relance)** — Après 15 runs du
  bras décisionnel, découverte que le toolchain **Go était absent de la
  machine** (retiré depuis juillet ; les campagnes 07-03/07-09 l'avaient).
  Les agents de ces 15 runs ne pouvaient pas exécuter `go test`
  (auto-vérification impossible) : environnement non comparable aux bras
  de référence. Décision : **15 runs invalidés et écartés** (archivés hors
  campagne, ~16 USD), Go **1.22.12** épinglé restauré (tarball officiel,
  `GOTOOLCHAIN=auto`), baseline revérifiée verte (6 tests), **bras
  décisionnel redémarré à zéro** avec l'environnement complet. Le smoke
  pilote (fix-n-plus-one, 0,59 USD) reste hors agrégation comme prévu.
  Coût total consigné au rapport : runs valides + runs invalidés + pilote.

## Bras complémentaire pré-enregistré : baseline-v3 (2026-08-27)

Conçu et figé APRÈS la lecture des résultats du bras décisionnel
`activated-v2` (jugés le 2026-08-27 : completed 10/40, régressions
primaires 8, dures 0) et AVANT toute exécution de `baseline-v3` — la
divulgation de cet ordre fait partie du pré-enregistrement.

Motif : le verdict `activated-v2` vs baseline 2026-07-03 est bloqué par
H1 (complétion 10/40 < 12) alors que les trois composantes A1 passent.
La cause candidate est la menace n° 1 anticipée : dérive du modèle
servi entre les campagnes (coût et tours par run sensiblement
différents à pins identiques). Une baseline contemporaine élimine
cette menace.

Design figé :

- Bras `baseline-v3` : 8 tâches × 5 répétitions = 40 runs, prompt de
  tâche inchangé, AUCUN artefact du standard (ni enrôlement ni hook) —
  réplique exacte du bras `baseline` 2026-07-03.
- Pins identiques aux runs `activated-v2` du jour : CLI 2.1.101 épinglé,
  `claude-sonnet-4-6`, `--max-turns 100`, timeout 1800 s, même machine,
  Go 1.22.12 restauré (même environnement que les runs activated-v2).
- Jugement : grille `JUDGING.md` inchangée, mêmes juges mécaniques et
  qualitatifs, cas limites `false`.
- Règle décisionnelle A1-v3 (figée avant exécution de baseline-v3) : le
  verdict d'utilité compare `activated-v2` (déjà exécuté, comptages déjà
  jugés et non modifiables) à `baseline-v3` sous le critère A1 :
  régressions primaires −30 % relatif, complétion non dégradée, coût par
  tâche complétée ≤ baseline-v3. H1 est requalifiée : la comparaison
  same-day remplace la comparaison inter-campagnes comme base du claim ;
  la non-réplication de la complétion inter-campagnes reste rapportée.
- Si `baseline-v3` diverge fortement de la baseline 2026-07-03 (signe de
  dérive du modèle), cette divergence est elle-même un résultat publié.
- Coût estimé : ≈ 20-25 USD (40 runs sans enrôlement).
