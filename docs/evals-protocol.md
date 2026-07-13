# Protocole d'évaluation — effet mesuré du standard gouverné

> **Statut : protocole, pas de résultats.** Ce document définit comment mesurer
> l'effet du standard agentique gouverné sur les résultats d'agents. Aucun
> chiffre d'efficacité ne doit être publié (README, site, annonces) tant qu'une
> campagne complète n'a pas été exécutée selon ce protocole — résultats négatifs
> inclus.

**Version du protocole : v2 — amendée le 2026-07-12**, avant toute exécution de
la deuxième campagne, sur la base du rapport `evals/reports/2026-07-03/report.md`
(campagne v1 : effet non démontré, zéro engagement du standard par les agents).

## Journal des amendements

| Version | Date | Changements |
| --- | --- | --- |
| v1 | 2026-07-02 | Pré-enregistrement initial (deux bras, critère régressions −30 %) |
| v2 | 2026-07-12 | Ajout du bras `activated` ; comptage des régressions scindé en « dures » vs « adaptations » avec critère principal sur les dures ; `fix-timezone-display` réduit à un run de contrôle par bras |

## Question mesurée

Un projet **enrôlé** dans le standard gouverné (profil + patterns + gates
fail-closed) produit-il de meilleurs résultats agents qu'un projet **témoin**
identique sans gouvernance, à modèle, tâches et budget égaux ?

## Design expérimental

- **Trois bras** par tâche (v2) :
  - `baseline` : même projet, standard non installé ;
  - `governed` : enrôlé (profil `starter` ou supérieur, gates disponibles),
    aucun mécanisme ne force leur usage — mesure la **présence passive**
    (réplication de la campagne v1) ;
  - `activated` : enrôlement identique à `governed` + mécanisme d'activation
    (hook `SessionStart` injectant l'obligation d'ouvrir l'enveloppe de tâche,
    hook `Stop` refusant la clôture tant que
    `grimoire standard gate check . --task-id bootstrap --target-state review`
    échoue) — mesure l'**usage forcé**. Artefacts :
    `evals/witnesses/web-app-todo/activated/`.
- **Contrastes pré-enregistrés** : principal `activated` vs `baseline` ;
  secondaires `governed` vs `baseline` (réplication v1) et `activated` vs
  `governed` (isole l'effet du mécanisme d'activation).
- **Projets témoins** : `examples/web-app-todo` (web) et
  `examples/terraform-houseserver` (infra). Un troisième témoin externe est
  souhaitable pour éviter le sur-ajustement aux exemples du kit.
- **Tâches** : suite fixe et versionnée de 8-12 tâches réalistes par témoin
  (feature, bugfix, refactor, migration), définies AVANT toute exécution dans
  `evals/tasks/<temoin>.yaml`.
- **Répétitions** : minimum 5 exécutions par tâche et par bras (variance LLM),
  même modèle, même version du kit, température documentée. Exception v2 :
  `fix-timezone-display` est retirée des répétitions pleines — la campagne v1 a
  montré un bruit nul sur ce lot (20/20 runs identiques, toutes métriques
  égales entre bras). Un **run de contrôle unique par bras** est conservé pour
  vérifier que le témoin de bruit nul le reste ; il est journalisé mais exclu
  des agrégats du critère principal.
- **Aveuglement** : l'évaluation humaine des livrables (si utilisée) se fait
  sans savoir de quel bras provient le diff.

## Métriques (toutes collectables depuis les artefacts du kit)

| Métrique | Source | Bras avantagé attendu |
|---|---|---|
| Taux de complétion (tâche livrée ET tests verts) | CI locale / `cc-verify.sh` | ? |
| Régressions **dures** (test/build baseline réellement cassé, supprimé ou affaibli) | suite de tests du témoin | activated |
| **Adaptations** de tests (test baseline modifié, suite verte, contrat préservé) | inspection du diff | descriptif — aucun bras attendu |
| Preuves manquantes au moment du « terminé » | `grimoire standard gate check` | governed (par construction) |
| Coût tokens / tâche | registre de coût LLM (`llm-cost-registry`) | baseline (hypothèse : la gouvernance a un surcoût) |
| Interventions humaines nécessaires | journal de session | ? |
| Score de conformité final | `grimoire standard score` | governed (par construction) |

Les deux dernières lignes « par construction » ne prouvent rien sur la qualité :
elles servent uniquement de contrôle de manipulation (les bras governed et
activated doivent effectivement être gouvernés ; pour `activated`, `gate_ok`
doit en plus être vrai, sinon le mécanisme d'activation n'a pas opéré).

## Comptage des régressions (pré-enregistré v2)

La campagne v1 a montré que le verdict dépendait entièrement de la règle de
comptage (analyse de sensibilité du rapport 2026-07-03 : +23 % avec la règle
v1, −42 % en ne comptant que les casses réelles). Le comptage v2 distingue
donc **deux catégories exclusives**, comptées séparément par run :

- **Régression dure** (`regressions_hard`) : un test ou un build de la
  baseline est réellement cassé (suite rouge sur l'état final), supprimé, ou
  affaibli (assertion retirée, test désactivé/skippé, tolérance élargie) —
  qu'il ait été modifié ou non. La perte de couverture du contrat compte comme
  affaiblissement même si la suite reste verte.
- **Adaptation** (`regressions_adapted`) : un test de la baseline est modifié,
  mais la suite complète reste verte et la force du contrat testé est
  préservée (mêmes comportements couverts, assertions équivalentes ou plus
  strictes).

Le **critère principal porte exclusivement sur les régressions dures**. Les
adaptations sont rapportées à titre descriptif ; sur les tâches `refactor-*`,
un volume élevé d'adaptations est discuté dans le rapport mais n'entre pas
dans le verdict. Les cas limites sont tranchés vers la catégorie la plus
sévère (dure) et journalisés.

## Règles d'honnêteté

1. **Pré-enregistrement** : tâches, métriques et seuils sont committés avant la
   première exécution ; tout changement ultérieur est journalisé.
2. **Résultats négatifs publiés** : si aucun bras enrôlé (governed, activated)
   ne montre d'avantage (ou montre un surcoût net), le résultat est publié tel
   quel.
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

## Critère de succès (pré-enregistré v2)

Le bras `activated` est déclaré « utile » si, sur le contraste principal
`activated` vs `baseline` (agrégé sur toutes les tâches à répétitions pleines,
run de contrôle `fix-timezone-display` exclu) :

1. les **régressions dures** baissent d'au moins 30 % relatif ;
2. SANS baisse du taux de complétion ;
3. SANS hausse du coût tokens de plus de 25 %.

Tout autre résultat = « non démontré » et le README ne fait aucun claim
d'efficacité. Les contrastes secondaires (`governed` vs `baseline`,
`activated` vs `governed`) sont rapportés avec les mêmes métriques mais ne
peuvent pas, seuls, fonder un claim.

Rappel historique (v1, campagne 2026-07-03) : le critère portait sur les
régressions toutes catégories confondues, sur le contraste `governed` vs
`baseline` ; verdict « effet non démontré », publié tel quel.
