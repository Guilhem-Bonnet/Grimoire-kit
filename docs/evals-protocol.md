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

Exemple de seuil raisonnable — à confirmer avant la campagne : le bras governed
est déclaré « utile » si les régressions baissent d'au moins 30 % relatif SANS
que le taux de complétion ne baisse ni que le coût tokens n'augmente de plus de
25 %. Tout autre résultat = « non démontré » et le README ne fait aucun claim
d'efficacité.
