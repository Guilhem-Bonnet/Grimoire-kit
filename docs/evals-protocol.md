# Protocole d'évaluation — effet mesuré du standard gouverné

> **Statut : protocole, pas de résultats.** Ce document définit comment mesurer
> l'effet du standard agentique gouverné sur les résultats d'agents. Aucun
> chiffre d'efficacité ne doit être publié (README, site, annonces) tant qu'une
> campagne complète n'a pas été exécutée selon ce protocole — résultats négatifs
> inclus.

## Question mesurée

Un projet **enrôlé** dans le standard gouverné (profil + patterns + gates
fail-closed) produit-il de meilleurs résultats agents qu'un projet **témoin**
identique sans gouvernance, à modèle, tâches et budget égaux ?

## Design expérimental

- **Deux bras** par tâche : `governed` (profil `starter` ou supérieur, gates
  actifs) vs `baseline` (même projet, standard non installé).
- **Projets témoins** : `examples/web-app-todo` (web) et
  `examples/terraform-houseserver` (infra). Un troisième témoin externe est
  souhaitable pour éviter le sur-ajustement aux exemples du kit.
- **Tâches** : suite fixe et versionnée de 8-12 tâches réalistes par témoin
  (feature, bugfix, refactor, migration), définies AVANT toute exécution dans
  `evals/tasks/<temoin>.yaml`.
- **Répétitions** : minimum 5 exécutions par tâche et par bras (variance LLM),
  même modèle, même version du kit, température documentée.
- **Aveuglement** : l'évaluation humaine des livrables (si utilisée) se fait
  sans savoir de quel bras provient le diff.

## Métriques (toutes collectables depuis les artefacts du kit)

| Métrique | Source | Bras avantagé attendu |
|---|---|---|
| Taux de complétion (tâche livrée ET tests verts) | CI locale / `cc-verify.sh` | ? |
| Régressions introduites (tests cassés post-merge) | suite de tests du témoin | governed |
| Preuves manquantes au moment du « terminé » | `grimoire standard gate check` | governed (par construction) |
| Coût tokens / tâche | registre de coût LLM (`llm-cost-registry`) | baseline (hypothèse : la gouvernance a un surcoût) |
| Interventions humaines nécessaires | journal de session | ? |
| Score de conformité final | `grimoire standard score` | governed (par construction) |

Les deux dernières lignes « par construction » ne prouvent rien sur la qualité :
elles servent uniquement de contrôle de manipulation (le bras governed doit
effectivement être gouverné).

## Règles d'honnêteté

1. **Pré-enregistrement** : tâches, métriques et seuils sont committés avant la
   première exécution ; tout changement ultérieur est journalisé.
2. **Résultats négatifs publiés** : si le bras governed ne montre pas d'avantage
   (ou montre un surcoût net), le résultat est publié tel quel.
3. **Pas de cherry-picking** : le rapport agrège TOUTES les exécutions, pas les
   meilleures.
4. **Reproductibilité** : chaque campagne fige la version du kit, du modèle et
   des tâches dans son rapport (`evals/reports/<date>/`).

## Layout attendu

```text
evals/
├── tasks/
│   ├── web-app-todo.yaml          # suite de tâches pré-enregistrée
│   └── terraform-houseserver.yaml
├── runs/                          # sorties brutes par exécution (non committées)
└── reports/
    └── 2026-XX/report.md          # agrégat, stats, conclusion honnête
```

## Critère de succès (à pré-enregistrer)

Critère composite en vigueur (composante coût amendée par **A1**, voir le
journal des amendements) : le bras testé est déclaré « utile » si

1. les régressions baissent d'au moins 30 % relatif vs baseline ;
2. le taux de complétion ne baisse pas ;
3. **le coût par tâche complétée ne dépasse pas celui du bras baseline de
   référence.** Garde-fou de validité : si le taux de complétion du bras testé
   est inférieur à 25 %, le coût par tâche complétée est jugé non interprétable
   et la composante coût est déclarée échouée.

Le coût brut par run reste rapporté dans chaque rapport, à titre informatif
(les deux lectures sont toujours publiées), mais n'est plus décisionnel.
Tout autre résultat = « non démontré » et le README ne fait aucun claim
d'efficacité.

> Critère original (campagnes 2026-07-03 et 2026-07-09, conservé pour
> lecture des rapports correspondants) : régressions −30 % relatif SANS
> baisse du taux de complétion ni hausse du coût tokens de plus de 25 %.

## Journal des amendements

### A1 — 2026-07-12 — composante coût : du coût brut par run au coût par tâche complétée

- **Moment** : après les campagnes 2026-07-03 (baseline/governed) et
  2026-07-09 (activated), **avant** toute exécution d'un bras ultérieur.
  Aucune donnée du bras suivant n'existait au moment de cet amendement.
- **Contexte** : le bras `activated` du 2026-07-09 passe les composantes
  régressions (−61,5 %) et complétion (15/40 vs 6/40) mais échoue sur le
  plafond de coût brut (+47,4 % > +25 %), d'où le verdict « non démontré ».
  Le rapport documente que le coût brut par run pénalise mécaniquement un
  bras qui complète 2,5× plus de tâches (chaque run accompli plus de
  travail réel) et que le coût par unité de valeur est la métrique
  économique pertinente (3,68 $/tâche complétée en baseline contre 2,17 $
  en activated). Il recommande explicitement de trancher ce choix *avant*
  la campagne suivante.
- **Décision** : pour toute campagne exécutée après le 2026-07-12, la
  composante coût du critère composite est « coût par tâche complétée ≤
  celui du bras baseline de référence », avec le garde-fou de validité
  ci-dessus (complétion < 25 % ⇒ composante échouée). Le coût brut par
  run est rapporté mais non décisionnel.
- **Portée** : non rétroactif. Le verdict « non démontré » de la campagne
  2026-07-09 reste inchangé et ne sera pas réinterprété avec le critère
  amendé ; seule une nouvelle campagne peut produire un verdict positif.
