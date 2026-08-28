# Campagne web-app-todo 2026-08-27 — bras activé v2, divulgué et baseline contemporaine

Exécution des pré-enregistrements `ACTIVATION-V2.md` (PR #74, complété par les
gels PR #160 et PR #190) : bras décisionnel `activated-v2` (8 tâches × 5
répétitions), bras exploratoire `activated-v2-disclosed` (8 × 2, prompt +
critères d'acceptation verbatim), bras témoin `baseline-v3` (8 × 5, aucun
artefact du standard, pré-enregistré après jugement du bras traité et avant
toute exécution du témoin — ordre divulgué). Runs des 2026-08-27 et 2026-08-28.

## Pins et environnement

| Paramètre | Valeur |
| --- | --- |
| Kit | grimoire-kit 3.18.0 (PyPI, `evals/.venv`) — identique aux campagnes 07-03 et 07-09 |
| Runner | Claude Code CLI 2.1.101, réinstallé épinglé (npm local, autoupdate désactivé) |
| Modèle | `claude-sonnet-4-6`, `--max-turns 100`, timeout 1800 s |
| Baseline applicative | copie committée `evals/witnesses/web-app-todo/app` |
| Mécanisme d'activation | `activated/` committé (PR #79) — hook SessionStart seul, directive verbatim ACTIVATION.md |
| Toolchain | Go 1.22.12 épinglé (tarball officiel), Node 22 — voir incident ci-dessous |
| Jugement | grille `JUDGING.md` inchangée, stricte, cas limites `false` ; jugement mécanique (suites go/npm réellement exécutées par run) + jugement qualitatif sur diffs |

## Incidents opérationnels (tous antérieurs aux données retenues)

- **Toolchain Go absent** : découvert après 15 runs du bras décisionnel — la
  machine avait perdu Go depuis juillet, les agents ne pouvaient pas
  auto-vérifier leur code. Les 15 runs (16,67 USD) sont invalidés et écartés,
  Go 1.22.12 restauré, bras redémarré à zéro. Consigné au journal de
  lancement avant relance.
- **Collisions d'instances** : des relances de campagne ont brièvement
  cohabité avec des instances survivantes d'interruptions de session
  (environ 3 USD de runs purgés). Le bras baseline-v3 retenu provient d'une
  exécution unique vérifiée par inventaire processus.
- Aucune exclusion dans les données retenues : 40 + 16 + 40 runs, tous jugés.

## Résultats

### Totaux par bras

| Métrique | activated-v2 | baseline-v3 | activated-v2-disclosed |
| --- | --- | --- | --- |
| Runs | 40 | 40 | 16 (exploratoire) |
| Completed (grille stricte) | **10** | 7 | **10** |
| Tests verts (go et npm) | **40/40** | 37/40 | 16/16 |
| Régressions primaires (règle 07-03) | 8 | 10 | 0 |
| — dont dures (cassé/supprimé) | **0** | **9** | 0 |
| — dont adaptées vertes | 8 | 1 | 0 |
| Adaptations vertes hors règle primaire | 6 | 1 | 2 |
| Coût total | 33,82 USD | 23,29 USD | 15,82 USD |
| Coût par tâche complétée | 3,38 USD | 3,33 USD | **1,58 USD** |

### Par tâche (completed, activated-v2 / baseline-v3 / disclosed)

| Tâche | act-v2 | base-v3 | disclosed |
| --- | --- | --- | --- |
| feat-due-dates | 1/5 | 0/5 | 2/2 |
| feat-bulk-complete | 0/5 | 0/5 | 2/2 |
| fix-timezone-display | 0/5 | 0/5 | 0/2 |
| fix-n-plus-one | 4/5 | 1/5 | 2/2 |
| refactor-handlers | 0/5 | 0/5 | 1/2 |
| refactor-api-client | 0/5 | 0/5 | 2/2 |
| migrate-go-version | 0/5 | 1/5 | 0/2 |
| sec-rate-limit | 5/5 | 5/5 | 1/2 |

Régressions dures de la baseline : feat-due-dates rep-2 (interface `Store`
modifiée sans toucher le fakeStore — les 6 tests Go baseline ne compilent
plus), fix-n-plus-one rep-1 (contrat `ListTasks` changé, test baseline cassé,
l'agent a livré sur « compile sans erreur » sans exécuter les tests),
refactor-api-client rep-2 (client incompatible avec les mocks — 2 tests
baseline cassés). Zéro équivalent dans les 56 runs activés.

## Verdict selon la règle décisionnelle A1-v3 (figée avant baseline-v3)

| Composante | Valeur | Seuil | Résultat |
| --- | --- | --- | --- |
| Régressions primaires | 8 vs 10 (−20,0 %) | ≤ −30 % relatif | ÉCHEC |
| Complétion | 10 vs 7 (+42,9 %) | non dégradée | ATTEINT |
| Coût par tâche complétée | 3,38 vs 3,33 USD | ≤ baseline | ÉCHEC (à 0,05 USD) |

**Verdict formel : effet non démontré selon A1-v3.** Publié tel quel,
conformément au protocole.

### Lecture des composantes en échec

Les 8 régressions primaires du bras activé sont **exclusivement des
adaptations vertes** de tests baseline sur les tâches refactor (suites
vertes, assertions adaptées au nouveau contrat) ; les 10 de la baseline
comprennent **9 suites réellement cassées**. La règle primaire de 2026-07-03,
conservée pour la comparabilité, traite ces deux réalités comme équivalentes.
Sur la métrique secondaire pré-enregistrée (cassé/supprimé vs adapté vert),
l'écart est de **0 contre 9 (−100 %)** — mais cette lecture n'est pas la
règle décisionnelle et ne fonde aucun claim.

Le coût par tâche complétée échoue de 0,05 USD ; le bras activé paie
l'enrôlement et l'enveloppe sur les 40 runs mais ne les convertit en
complétion que sur 10 (grille stricte, critères non divulgués).

## Hypothèses pré-enregistrées

- **H1 (réplication inter-campagnes)** : NON satisfaite pour la complétion
  (10/40 contre 15/40 le 2026-07-09, seuil ≥ 12) ; satisfaite pour les
  régressions dures (0 ≤ 2). La baseline contemporaine réplique en revanche
  bien la baseline de juillet (completed 7 vs 6, dures 9 vs 12, coût/run
  0,58 vs 0,55 USD) : la dérive du modèle servi est modeste côté témoin,
  et n'explique qu'en partie la baisse de complétion du bras activé.
- **H2 (non-divulgation)** : SOUTENUE. Le bras divulgué passe de 25 % à
  62,5 % de complétion (10/16), et les gains se concentrent sur 4 des 5
  tâches pré-enregistrées comme « à critères non devinables »
  (feat-due-dates, feat-bulk-complete, refactor-api-client,
  refactor-handlers). Deux contre-exemples instructifs : fix-timezone-display
  (0/2 — tests tautologiques même critères en main) et migrate-go-version
  (0/2 — le changelog exigé par la grille reste absent).
- **H3 (coût)** : coût par tâche complétée 3,38 USD ≤ 3,68 USD (référence
  baseline 07-03) — satisfaite telle qu'écrite ; défaite de 0,05 USD contre
  la baseline contemporaine (3,33 USD).

## Ce que la campagne établit (hors claim)

1. **Le mécanisme d'activation élimine les régressions dures** : 0 sur 96
  runs activés cumulés (40 + 16 de cette campagne, 40 du 2026-07-09),
  contre 9/40 (baseline contemporaine), 12/40 (baseline 07-03) et 7/40
  (governed passif 07-03). C'est l'effet le plus stable mesuré sur ce
  témoin, répliqué trois fois.
2. **La divulgation des critères d'acceptation est le levier de complétion
  dominant** : ×2,5 sur la complétion, coût par tâche complétée divisé par
  deux (1,58 USD). Effet mesuré sur un bras exploratoire — un bras
  décisionnel divulgué 8 × 5 est le candidat naturel de la prochaine
  campagne.
3. **La règle primaire de comptage est anti-corrélée au dommage réel** sur
  les bras activés : elle compte les adaptations vertes (comportement
  correct exigé par la tâche) au même titre que les suites cassées.
  Candidat d'amendement A2, à pré-enregistrer avant toute campagne future :
  régressions dures en règle primaire, adaptations vertes en secondaire.
  (Note de méthode : cet amendement était proposé par la PR #78 et a été
  écarté au profit de la comparabilité — ce rapport en fournit la
  justification empirique a posteriori.)

## Menaces à la validité

- Baseline-v3 pré-enregistrée après lecture des résultats du bras traité
  (ordre divulgué dans le gel) ; les règles de jugement et de comptage
  étaient figées avant les deux exécutions.
- n = 5 par cellule décisionnelle : signaux, pas de tests statistiques.
- Jugement qualitatif par agents LLM sur grille stricte (mêmes juges et
  mêmes conventions pour les trois bras) ; le jugement mécanique
  (exécution réelle des suites) est indépendant du jugement qualitatif.
- Runs des deux bras exécutés le même jour, séquentiellement, sur la même
  machine — l'ordre (activated puis baseline) n'est pas randomisé.

## Coûts

| Poste | Montant |
| --- | --- |
| activated-v2 (40 runs) | 33,82 USD |
| activated-v2-disclosed (16 runs) | 15,82 USD |
| baseline-v3 (40 runs) | 23,29 USD |
| Smoke pilote (hors agrégation, journalisé) | 0,59 USD |
| Runs invalidés (incident toolchain) | 16,67 USD |
| Collisions d'instances (purgés) | ≈ 3 USD |
| **Total campagne** | **≈ 93 USD** |

## Recommandations

1. Pré-enregistrer l'amendement A2 (règle primaire = régressions dures) et
  un bras décisionnel divulgué 8 × 5 pour la prochaine campagne — c'est la
  configuration produit réelle si les critères d'acceptation entrent dans
  l'enveloppe de tâche.
2. Traduction produit de H2 : faire des critères d'acceptation un champ de
  première classe de l'enveloppe de tâche du standard, injecté par le hook
  d'activation (`grimoire standard init` l'installe depuis la 3.24, PR #73).
3. Publier uniquement le constat borné répliqué : « sur ce témoin, avec ce
  runner et ce modèle, l'activation du standard élimine les régressions
  dures (0/96 runs activés contre 9/40 en baseline contemporaine) » — le
  claim composite A1 reste non démontré.
