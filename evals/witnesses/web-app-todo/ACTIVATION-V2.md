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
