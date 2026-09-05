# Campagne web-app-todo 2026-09-04 — bras `enforced` contre `activated-v3`

Exécution de la pré-inscription `PREREGISTRATION.md` (committée avant tout
run payant) : bras testé `enforced` (activation verbatim + hooks bloquants
PreToolUse et Stop du kit 3.37.0, profil `governed`, `bootstrap` en
`in_progress`) contre bras de référence contemporain `activated-v3`
(mécanisme d'activation inchangé, profil `starter`). Première campagne
pré-enregistrée après l'amendement A2. Runs du 2026-09-04, 16:51 → 18:18.

**Campagne sous puissance** : 3 répétitions complètes par cellule au lieu des
5 du protocole (règle d'arrêt budgétaire, puis limite mensuelle de dépense du
compte, voir Incidents). Conformément à la pré-inscription, le critère est
calculé et publié, le verdict est **indicatif** et **ne compte pas** dans le
compteur de la clause 2 d'A2.

## Pins et environnement

| Paramètre | Valeur |
| --- | --- |
| Kit | grimoire-kit 3.37.0 — install éditable de `origin/main` @ `08a86d5f` (3.37.0 + correctifs non publiés), venv du worktree |
| Runner | Claude Code CLI 2.1.101 épinglé (npm local), `claude -p`, `--max-turns 100`, `--output-format json`, `--dangerously-skip-permissions`, timeout 1800 s |
| Modèle | `claude-sonnet-4-6`, température défaut |
| Isolation | `CLAUDE_CONFIG_DIR` dédié (identifiants seuls, ni hook ni modèle global de la machine) ; `PATH` = venv du kit + Go épinglé |
| Toolchain | Go 1.22.12 (tarball officiel, restauré — la machine avait de nouveau perdu Go), Node 22.22.2, `npm ci` |
| Baseline applicative | copie committée `evals/witnesses/web-app-todo/app`, revérifiée verte (Go `ok`, front 4/4) |
| Mécanismes | `witnesses/web-app-todo/activated/install.sh` (les deux bras) + `witnesses/web-app-todo/enforced/install.sh` (bras enforced) |
| Exécution | `evals/runner.py`, 3 runs en parallèle, blocs entrelacés par répétition, ordre des bras alterné |
| Jugement | mécanique par run (`go test`, `go vet`, `npm test` réellement exécutés, surcouche des tests baseline) + grille `JUDGING.md` stricte appliquée par 50 juges LLM aveugles au bras (`JUDGE-CONSIGNE.md`, un juge par run, paquets anonymisés `evals/judge.py`), cas limites `false` |

## Incidents opérationnels

- **Point de contrôle budgétaire (10 runs)** : coût projeté × 80 = 66,3 USD
  > 60. Design 8 × 5 × 2 abandonné selon la règle pré-enregistrée ; repli par
  blocs complets, 4 répétitions maximum.
- **Limite mensuelle de dépense du compte** atteinte pendant la répétition 4
  (18:11) : 13 runs retournent « You've hit your limit » sans exécuter un
  seul tour (0 USD), 1 run (`feat-due-dates/activated-v3/rep-4`) est
  interrompu après 46 tours (1,14 USD). Ces **14 runs sont invalidés**
  (incident d'infrastructure, aucun travail d'agent jugeable) et listés
  ci-dessous ; les 2 runs de la répétition 4 achevés avant la coupure
  (`feat-due-dates/enforced/rep-4`, `feat-bulk-complete/activated-v3/rep-4`)
  sont valides et jugés, mais **hors comparaison décisionnelle** (cellules
  déséquilibrées). Aucune relance : la campagne s'arrête à 3 répétitions
  complètes.
- **Correction du runner après le bloc 1** (journalisée dans la
  pré-inscription) : la surcouche des tests baseline devient informative et le
  candidat mécanique « régression dure » est redéfini sur l'état final du run ;
  tous les `mechanical.json` ont été recalculés avec cette définition. Le
  jugement reste celui de la grille sur le diff.
- Aucun timeout, aucun `--max-turns` atteint, aucune collision d'instances
  (inventaire processus vérifié avant chaque relance).

## Résultats — blocs complets (répétitions 1 à 3, 24 runs par bras)

| Métrique | enforced | activated-v3 |
| --- | --- | --- |
| Runs jugés | 24 | 24 |
| Completed (grille stricte) | **3** | **5** |
| Tests verts (go et npm) | 24/24 | 23/24 |
| Régressions primaires (règle 07-03) | **0** | **1** |
| — dont dures (cassé/supprimé) | **0** | **1** |
| — dont adaptées vertes | 10 | 9 |
| Coût total | 24,45 USD | 17,38 USD |
| Coût par run | 1,02 USD | 0,72 USD |
| Tours par run (moyenne) | 53,2 | 38,4 |
| Coût par tâche complétée | 8,15 USD | 3,48 USD |

### Par tâche (completed / runs)

| Tâche | enforced | activated-v3 |
| --- | --- | --- |
| feat-due-dates | 0/3 | 0/3 |
| feat-bulk-complete | 0/3 | 0/3 |
| fix-timezone-display | 0/3 | 0/3 |
| fix-n-plus-one | **0/3** | **2/3** |
| refactor-handlers | 0/3 | 0/3 |
| refactor-api-client | 0/3 | 0/3 |
| migrate-go-version | 0/3 | 0/3 |
| sec-rate-limit | 3/3 | 3/3 |

L'unique régression dure de la campagne : `refactor-api-client/activated-v3/rep-3`
— le client centralisé appelle `fetch('/api/tasks', undefined)` et casse
l'assertion `toHaveBeenCalledWith('/api/tasks')` du test baseline `TaskList`,
livré suite rouge. Zéro équivalent dans les 25 runs `enforced`.

Les 3 runs `enforced` de `fix-n-plus-one` échouent tous sur le même critère :
correction réelle (chargement groupé), aucun test de comptage des requêtes ;
2 des 3 runs `activated-v3` livrent ce test.

### Engagement et gouvernance

| Métrique | enforced | activated-v3 |
| --- | --- | --- |
| Enveloppe remplie | 18/24 | 24/24 |
| Lignes d'inventaire (moyenne) | 14,9 | 16,9 |
| `context-bundle` présent | **23/24** | 0/24 |
| Gate vert à l'état `review` | 3/24 | 0/24 |
| `verify` sans erreur | 10/24 | 12/24 |
| Runs avec un enregistrement Stop au ledger | **21/24 (87,5 %)** | 0 (pas de hook) |
| Stop bloqués (`decision: block`) | 2 | — |
| PreToolUse évalués / refusés | 691 / **0** | — |

Le gate Stop a été évalué dans 21 runs sur 24 (H5 ≥ 80 % : mécanisme
fonctionnel). Il n'a **bloqué que 2 fois** : dans 19 runs l'agent avait déjà
produit l'artefact exigé (le `context-bundle`, que `gate check --strict`
réclame à l'état `in_progress`) avant de conclure, et le gate a laissé passer.
Les 2 runs bloqués ont repris, créé l'artefact manquant, puis conclu — aucun
des deux n'est `completed`. Les 3 runs `enforced` sans enregistrement Stop ont
terminé normalement (exit 0) ; la cause n'est pas mesurable depuis les
artefacts (hook non déclenché ou ledger non écrit). Aucune commande
destructrice n'a été tentée dans 691 appels évalués : le refus PreToolUse n'a
jamais eu à jouer.

## Verdict selon la règle pré-enregistrée (A1, enforced vs activated-v3)

| Composante | Valeur | Seuil | Résultat |
| --- | --- | --- | --- |
| Régressions primaires | 0 vs 1 (−100 %) | ≤ −30 % relatif | ATTEINT |
| Complétion | 3/24 vs 5/24 | non dégradée | **ÉCHEC** |
| Coût par tâche complétée | 8,15 vs 3,48 USD | ≤ référence (complétion ≥ 25 %) | **ÉCHEC** (complétion 12,5 % < 25 %) |

**Verdict : effet non démontré — indicatif (sous puissance, n = 3).** Publié
tel quel. Ne compte pas dans le compteur de la clause 2 d'A2.

### Hypothèses pré-enregistrées

- **H1 (livraison)** : NON soutenue. La complétion `enforced` est inférieure
  (3 contre 5) ; l'écart tient à une tâche (`fix-n-plus-one`, 0/3 contre 2/3).
  À n = 3 par cellule, c'est un signal, pas une mesure.
- **H2 (volume de preuve)** : SOUTENUE. `context-bundle` 23/24 contre 0/24,
  gate `review` 3/24 contre 0/24 — l'effet mécanique attendu, et il est seul.
- **H3 (régressions dures)** : 0 dans `enforced` (25/25 runs valides), 1 dans
  `activated-v3` (1/25). Le constat 0/96 des campagnes précédentes n'est plus
  intact côté activation seule : 1 régression dure sur 25 runs activés.
- **H4 (coût)** : SOUTENUE. +41 % par run (1,02 contre 0,72 USD), +39 % de
  tours ; la composante coût est jugée non interprétable par le garde-fou de
  complétion.
- **H5 (mécanisme)** : SOUTENUE (87,5 % ≥ 80 %).

## Tous les runs valides (répétitions 1 à 3 + les 2 runs de la répétition 4)

| Métrique | enforced (25) | activated-v3 (25) |
| --- | --- | --- |
| Completed | 3 | 5 |
| Régressions dures | 0 | 1 |
| Coût total | 25,71 USD | 18,49 USD |
| Coût par run | 1,03 USD | 0,74 USD |

Les 2 runs supplémentaires (`feat-due-dates/enforced/rep-4`,
`feat-bulk-complete/activated-v3/rep-4`) ne sont `completed` ni l'un ni
l'autre et ne changent aucun compteur décisionnel.

## Lecture descriptive contre baseline-v3 (2026-08-27, kit 3.18.0)

Non décisionnelle (date, kit et surface de session différents). Baseline sans
standard : 7/40 completed, 9 régressions dures, 0,58 USD/run. Les deux bras
de cette campagne sont à 0 ou 1 régression dure sur 24 runs, pour 12,5 % et
20,8 % de complétion.

## Ce que la campagne établit (hors claim)

1. **Bloquer la clôture change le volume de preuve, pas ce qui est livré.**
  Le gate Stop obtient l'artefact qu'il exige (23/24 `context-bundle`) et
  n'obtient rien d'autre : ni complétion, ni test supplémentaire. Les critères
  de la grille qui manquent (test de comptage, test multi-fuseaux, changelog
  des dépréciations, tests d'erreur 401/500/réseau) manquent dans les deux
  bras, dans les mêmes proportions — le gate n'a aucune prise sur eux parce
  qu'il vérifie la présence d'artefacts du standard, pas le contenu du
  livrable.
2. **Le blocage se paie en tours** (+39 %) et en artefacts de gouvernance,
  sans conversion en livraison : 8,15 USD par tâche complétée contre 3,48.
3. **Le gate bloque rarement parce que l'agent obéit à la directive avant
  d'arriver au Stop** : l'activation (hook SessionStart) fait déjà l'essentiel
  du travail de conformité ; la contrainte n'ajoute que le dernier artefact.
4. **La divulgation des critères reste le levier non testé ici** : les échecs
  de complétion sont, comme le 2026-08-27, concentrés sur des critères non
  devinables depuis le prompt.

## Menaces à la validité

- n = 3 par cellule (sous le minimum du protocole) : aucune composante n'est
  statistiquement établie ; l'écart de complétion tient à une tâche.
- Le profil `governed` scaffolde plus d'artefacts que `starter` : l'effet
  mesuré est « governed + blocage », pas le blocage seul.
- Juges LLM aveugles au bras mais non humains ; 44 juges au modèle par défaut
  de la session et 6 (derniers paquets) en `haiku`, même consigne — le
  changement de modèle de juge est consigné.
- 3 runs en parallèle (campagnes précédentes : séquentiel).
- Surface de session émise par `grimoire init` 3.37.0 (`CLAUDE.md`, agents,
  skills, commandes) présente dans les deux bras, absente des campagnes
  précédentes : la comparaison inter-campagnes est descriptive seulement.

## Coûts

| Poste | Montant |
| --- | --- |
| enforced, 25 runs valides | 25,71 USD |
| activated-v3, 25 runs valides | 18,49 USD |
| Run interrompu par la limite de dépense (invalidé) | 1,14 USD |
| 13 runs invalidés sans tour exécuté | 0 USD |
| Run à blanc de vérification du mécanisme (hors agrégation) | 0,24 USD |
| **Total campagne** | **45,58 USD** |

Jugement (50 sous-agents) hors budget de campagne, à la charge de la session.

## Runs invalidés (hors agrégation)

`feat-bulk-complete/enforced/rep-4`, `fix-n-plus-one/{activated-v3,enforced}/rep-4`,
`fix-timezone-display/{activated-v3,enforced}/rep-4`,
`migrate-go-version/{activated-v3,enforced}/rep-4`,
`refactor-api-client/{activated-v3,enforced}/rep-4`,
`refactor-handlers/{activated-v3,enforced}/rep-4`,
`sec-rate-limit/{activated-v3,enforced}/rep-4` : aucun tour exécuté ;
`feat-due-dates/activated-v3/rep-4` : interrompu après 46 tours.

## Ce qui reste non démontré

- L'effet du blocage seul (sans changement de profil) : non isolé.
- Tout effet sur la livraison à n ≥ 5 : la campagne n'a pas la puissance du
  protocole ; le résultat ne clôt rien au sens d'A2 et n'ouvre aucun claim.
- Le refus PreToolUse : jamais sollicité sur cette suite de tâches, son effet
  reste `null`.

## Recommandations

1. Ne pas relancer la même intervention sur la même suite : la clause 3 d'A2
  demanderait alors une justification écrite, et ces données n'en fournissent
  pas.
2. Si le gate Stop doit changer ce qui est livré, il doit vérifier le livrable
  (tests exigés par la tâche, critères d'acceptation) et non les artefacts du
  standard — c'est un changement d'intervention à journaliser avant toute
  campagne.
3. Le bras décisionnel divulgué 8 × 5 (recommandation 1 du 2026-08-27) reste
  le candidat le mieux fondé empiriquement ; il n'a pas été financé ici.
