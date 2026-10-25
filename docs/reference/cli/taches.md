---
audience: référence
---

# Les tâches

## Comptabilité du dispatch : coût par tâche résolue et pass^k

`grimoire dispatch stats` est la sœur de `providers history` : même idée
(compter, ne pas classifier), autre source et un autre angle. `providers
history` lit le Mission Ledger au grain de la *tentative* et compte des
taux d'escalade par couple (type de tâche, classe) ; `dispatch stats` lit le
journal de traces au grain de la *cascade entière* (un événement
`dispatch.outcome` par `grimoire task dispatch` ou par node de
`grimoire flow run --executor dispatch` réellement exécuté — jamais pour un
refus avant tout appel ni un `--dry-run`) et répond à deux questions que
seules des campagnes d'évals manuelles répondaient jusque-là (issue #442,
point 5 de l'audit de positionnement du 2026-09-12) : combien coûte une
tâche *résolue*, et à quel point une même tâche rejouée plusieurs fois
réussit-elle systématiquement ?

| Commande | Description |
| --- | --- |
| `grimoire dispatch stats [--since 30d] [--json]` | Coût par tâche résolue, taux d'escalade, part d'inexécutable — pour l'ensemble, par classe de vérifiabilité, par fournisseur et par flow — et pass^k sur les nœuds rejoués |

**Coût par tâche résolue** : le coût total de toutes les tentatives (vertes
et rouges confondues, un check rouge a quand même coûté un appel) divisé
par le nombre de dispatchs *résolus* — jamais divisé par le nombre total de
tentatives, qui sous-estimerait le coût réel d'un succès quand la cascade a
dû escalader. `None` sans aucun dispatch résolu, jamais une division par
zéro qui se lirait à tort comme un coût nul.

**pass^k** : quand un même nœud (le node d'un blueprint de flow, identifié
par blueprint + id de node — ou, hors flow, directement l'identifiant de la
tâche) a été dispatché au moins deux fois, cette répétition forme une
« série ». Une série compte comme entièrement verte seulement si *toutes*
ses exécutions ont résolu la tâche — la fiabilité au sens strict (k
exécutions, toutes réussies), pas une moyenne de taux de réussite
individuels. Le taux affiché est la part de séries entièrement vertes parmi
celles observées au moins deux fois ; une tâche jamais rejouée n'entre dans
aucun dénominateur.

`--since 30d` (ou `12h`) ne garde que les dispatchs dont l'horodatage est
plus récent que cette ancienneté. Sans journal de traces sous
`_grimoire-output/traces/`, la commande n'échoue jamais : elle affiche qu'il
n'y a rien à montrer (`{"overall": null}` en JSON).

Le contrôle du standard `dispatch.cost_slo` (voir
[Contrôles gouvernés](../../standard/controles-gouvernes.md)) lit exactement les
mêmes agrégats — cette commande en est la vue humaine, jamais un second
calcul.

## Tâches agentiques

Le task board gouverné est une **projection** du Mission Ledger, pas une source
(voir [ADR-005](../../explications/decisions/adr-005-mission-ledger-source-of-truth.md)). Éditer
`_grimoire/standard/task-board.yaml` à la main ne change rien au ledger, et le
prochain export l'écrase.

| Commande | Description |
| --- | --- |
| `grimoire task board export [.]` | Régénérer le board depuis le Mission Ledger |
| `grimoire task board export . --dry-run` | Afficher la projection sans écrire |
| `grimoire task board export . --mission <id>` | N'exporter qu'une mission |
| `grimoire task board export . -o <chemin>` | Écrire ailleurs que dans le board du standard |
| `grimoire task add "<titre>" -a "<critère>" [--owner <qui>] [--ready]` | Ouvrir une tâche (un critère d'acceptation au moins) ; `--ready` la rend réclamable tout de suite, gate `proposed_to_ready` compris |
| `grimoire task add "<titre>" -a "<critère>" --description "<brief>"` ou `--description-file <fichier>` | Donner à la tâche son contexte (pourquoi, pistes) au-delà du titre : texte brut, 4000 caractères au plus ; rendu par `task show`, l'outil MCP `task_show`, `task_context` (bundle) et la carte du cockpit (#714) |
| `grimoire task add "<titre>" -a "<critère>" --finition maquette\|peaufine` | Poser la finition du livrable (R24). Sans l'option, la tâche hérite de la phase de `project-context.yaml` (clé `phase` ou `project.phase` : `exploration` → `maquette`, `construction` et `exploitation` → `peaufine`) ; sans phase déclarée ou reconnue, le champ reste vide (le kit ne devine pas). `task show` rend `finition`. La valeur machine est sans accent ; `peaufiné` est accepté en entrée. Une valeur inconnue est refusée avant toute écriture. L'outil MCP `task_add` porte le même paramètre `finition` |
| `grimoire task add --intake "<texte>"` | Proposer, **sans rien écrire**, le type de mission, le risque, le périmètre, les contrôles de politique attendus et les tâches d'une demande (JSON sur stdout, classement déterministe sans modèle) ; ne se combine ni avec un titre ni avec `-a` (R07). Les tâches se créent ensuite une à une par `task add` |
| `grimoire task import <plan.json> [--mission <id>] [--dry-run]` | Importer un plan JSON (P50) : crée les tâches (titre, critères, preuves, priorité) et leurs dépendances (`task link`), puis rend la table clé du plan → clé du ledger sur stdout (`clé<TAB>GAO-…<TAB>action`). Le champ `"plan"` (identité du plan, sans blanc) est obligatoire ; les clés de tâche n'ont ni blanc ni caractère de contrôle (100 caractères au plus) ; au plus 200 tâches et 1 Mo par fichier. Le plan est validé en entier avant d'écrire : toute anomalie (champ inconnu, critère manquant, clé en double, dépendance absente, cycle, mission inconnue, plafond) refuse tout, rien n'est écrit, code 1. **Idempotent et sûr en concurrence** : la relecture du ledger, les créations et les liens se font sous un verrou inter-processus, donc N imports simultanés d'un même plan créent un seul jeu de tâches ; une entrée déjà importée (`extra.import = {plan, key}`) n'est pas recréée. Si son titre ou ses critères diffèrent du ledger (plan modifié, ou autre plan qui réutilise l'identifiant), le plan entier est refusé (code 1, rien d'écrit) : aucune tâche n'est écrasée. Une entrée `{"key": …, "existing": "GAO-…"}` désigne une tâche déjà au ledger, sautée (jamais modifiée) mais utilisable comme cible de `depends_on`. La relance **complète les liens manquants** des tâches importées par ce plan (reprise après interruption) et ne touche à aucun autre champ. `--dry-run` montre ce qui serait créé sans toucher au ledger |
| `grimoire task list [--status <état>] [--mission <id>]` | Lister les tâches et leur colonne de board |
| `grimoire task wave-checkpoint <mission> <vague> [--action status\|request\|require\|approve\|reject] [--fingerprint <empreinte>]` | Checkpoint humain d'une vague, lié à l'empreinte du contenu fusionné (arbres base, tête et merge de chaque tâche). `request` présente l'empreinte, `approve`/`reject` la citent et exigent un tour humain attesté postérieur (W2-01) ; `require` ferme le claim des vagues suivantes tant que la vague n'est pas `approved`. Une vague dont le contenu change devient `stale`. Le numéro de vague est celui de `task waves` (niveau absolu, vagues closes comprises) ; le verrou s'applique à toute transition vers `claimed` |
| `grimoire task show <id>` | Détailler une tâche et ce que chaque prochain pas exigera |
| `grimoire task claim <id> [--actor <qui>] [--host <où>]` | Réclamer une tâche prête (`ready → claimed`). Une tâche `claimed`/`running` dont le bail est perdu (`expires_at` passé, battement de cœur perdu) se réclame directement : barrière du bail, retour `ready` journalisé (`reclaimed`), nouveau claim au nouvel epoch ; un autre acteur que le titulaire seulement si l'hôte du claim a un frein vérifié, sinon `grimoire task reap` pose la question à l'humain. Le WIP ne compte que les claims vivants |
| `grimoire task move <id> --to <état>` | Déplacer une tâche, si la machine à états et le gate le permettent |
| `grimoire task block <id> --reason "<motif>"` | Bloquer en disant pourquoi |
| `grimoire task close <id>` | Fermer une tâche vérifiée (verdict accepté exigé ; depuis `needs_verification`, reçu de revue frais exigé) |
| `grimoire task review <id> --verdict READY\|NOT-READY [--finding "résumé :: commande"]` | Écrire le reçu de revue, lié à l'empreinte présente de l'arbre (`_grimoire-output/reviews/<id>/review-receipt.json`) |
| `grimoire task verify <id> [--evidence <fichier> ...]` | Lancer les commandes déclarées de la tâche, écrire le pack et le verdict (0 `passed`, 1 `failed`, 2 aucune commande, 3 commandes non approuvées). Pour une tâche **sans** commande, `--evidence` (répétable) écrit un pack **déclaratif** : chaque fichier doit exister sous la racine du projet, il entre haché (SHA-256, taille, URI `file:<chemin>`), le verdict est `skipped` (`created_by: declarative`, contrôle `declarative-only`) et le code de sortie 4 : la revue peut s'ouvrir (`needs_verification`), la clôture reste refusée (`review_gate`) — une preuve déclarative n'est jamais un vert mécanique |
| `grimoire task accept <id> --reason "<ce qui a été relu>" [--no-close]` | Accepter la preuve **déclarative** d'une tâche en revue (canal humain seulement, refusé depuis une session d'agent comme `task answer`) : pack d'attestation (les éléments du dernier pack plus l'acceptation, qui nomme chaque critère), verdict `passed` signé `created_by: human:<acteur>`, reçu de revue `READY` (`reviewer_id: human:<acteur>`), puis clôture ; `--no-close` s'arrête avant (issue #865) |
| `grimoire task prioritize <id> --to low\|medium\|high\|critical [--reason "<motif>"]` | Changer la priorité (issue #638) — l'historique reste au ledger, le board se retrie |
| `grimoire task comment <id> "<texte>" [--kind comment\|directive]` | Poser un commentaire ou une consigne ; la session qui tient la tâche la lit au prochain `UserPromptSubmit` |
| `grimoire task cancel <id> --reason "<motif>" [--force]` | Annuler avec sa raison (obligatoire) ; tenue par une autre session, elle exige `--force` |
| `grimoire task ack <id> <dir-…>` | Accuser réception d'une consigne de l'orchestrateur |
| `grimoire task link <id> --depends-on <id>` | Déclarer une dépendance |
| `grimoire task unlink <id> --depends-on <id> --reason "<motif>" [--kind <nature>]` | Retirer une dépendance, motif journalisé. Une dépendance annulée bloque le claim pour toujours (elle ne sera jamais close) : si elle a été remplacée, on retire le lien puis on relie la remplaçante avec `task link` |
| `grimoire task ask <id> "<question>" [--kind <nature>] [-o <option>] [--non-blocking] [--why "<pourquoi>"] [--key <clé>]` | Poser une question à l'humain : une bloquante gare la tâche en `waiting(human)` et remonte au cockpit ; `--non-blocking` l'inscrit sans garer la tâche |
| `grimoire task ask <id> --from <lot.yaml\|->` | Poser un lot de questions (JSON ou YAML) en **une** interruption et une notification ; une question déjà répondue (même clé) n'est pas reposée |
| `grimoire task answer <id> "<réponse>" [--question <q-…>]` | Répondre (canal humain seulement) ; la tâche n'est rendue que quand toutes les bloquantes du lot ont leur réponse |
| `grimoire task questions [<id>] [--open]` | Le registre des questions : statut, bloquante ou non, pourquoi, qui a répondu, par quel canal ou sur quelle source, et le nombre d'interruptions |
| `grimoire task resolve <id> <q-…> "<réponse>" --source <d'où>` | Déduire la réponse d'une question **non bloquante**, source citée ; une bloquante est refusée |
| `grimoire task decisions` | Les décisions qui attendent un humain (un lot = une décision, ses questions ouvertes listées) |
| `grimoire task context <id>` | Produire le context bundle d'une tâche réelle |
| `grimoire task trace <id> [--causes]` | Timeline unifiée d'une tâche : transitions, outils refusés, gates rouges, checkpoints, abort, preuves, incidents |
| `grimoire task approve-check <id> [--cmd "<commande>"] [--allow-unattested]` | Approuver par empreinte les commandes de vérification de la tâche, pour cette tâche seule (governed/production : sans approbation, `task verify` et le dispatch de flow ne lancent rien) ; l'acteur est attesté par un tour humain postérieur au challenge de cet ensemble exact de commandes ; sans attestation (terminal nu), l'approbation est refusée sauf `--allow-unattested` (journalisée `unattested`) ; les caractères cachés (U+202E, U+200B) sont refusés |
| `grimoire task trace-export [<dest>] [--format otel\|langfuse\|otlp] [--endpoint <url>] [--header NOM=VALEUR] [--mission-id <id>]` | Exporte le TraceLedger (plus, pour OTel et OTLP, les spans des runs de flow lus dans le journal du kernel, avec `parentSpanId`) : JSONL aux conventions OTel GenAI ou Langfuse, ou `--format otlp --endpoint` vers un collecteur OTLP/HTTP (échoue si le lot n'est pas accepté) |
| `grimoire task recall <id>` | Ce que la mémoire du projet sait de cette tâche et de ses voisines — borné en tokens |
| `grimoire task dispatch <id> --check "<commande>" [--check ...] [--dry-run] [--max-tier cheap\|mid\|strong] [--start-tier cheap\|mid\|strong] [--provider <id>] [--timeout <s>]` | Déléguer la tâche en cascade par palier de fournisseur |

### Une attente porte sur sa tâche, pas sur la session

`grimoire task wait <id> --reason ci|quota|paused` et `grimoire task ask <id> "<question>"` garent
**la tâche** (`waiting`). Ils ne gèlent ni la session ni ses sous-agents : le frein du bail
(hook `PreToolUse`) se tait sur une session dont la tâche est garée, qui est jugée comme une session
sans tâche — mêmes consignes que le repli normal (`grimoire task claim|attach <autre-tâche>`).
Restent possibles `grimoire task …` sur n'importe quelle tâche et `grimoire cockpit serve` (pour que
l'humain réponde). Ce qui ne change pas : aucune écriture ne se fait au nom de la tâche garée, et une
question humaine bloquante ne se lève que par l'humain — `task resume` est refusé sur un
`waiting(human)`. La réponse se donne ainsi, exécutable telle quelle (à lancer avec le binaire du
projet, `.venv/bin/grimoire` si `grimoire` n'est pas dans le PATH) :

```bash
grimoire task answer <id> "<réponse>" --question <q-…>
```

`task wait` et `task ask` (et l'action MCP `wait` / `ask`) préviennent quand des sous-agents de la
session tiennent encore leurs propres claims : garer le coordinateur ne les arrête pas. L'action MCP
`task_update(action="resume")` reprend sans epoch quand la session de l'environnement est celle qui
porte le claim (comme la CLI avec `--epoch` omis) ; sinon l'erreur « aucun epoch à présenter » reste.

Sans ledger, la commande d'export refuse et sort en erreur plutôt que d'écrire un
board vide — écraser le travail déclaré par du néant serait pire que ne rien faire.

Chaque transition d'état (`claim`, `move`, `block`, `close`, `cancel`) franchit deux
portes avant de toucher le ledger : la machine à états, puis le gate de preuve de
`_grimoire/standard/evidence-gates.yaml`. Un refus nomme la preuve manquante et le
remède ; rien n'est écrit. Après une écriture acceptée, le board du standard est
reprojeté depuis le ledger si le projet est enrôlé (`_grimoire/standard/` présent)
— plus besoin de relancer `task board export` pour qu'un claim se voie.

Les mêmes gestes sont exposés aux agents par le serveur MCP (`task_add`,
`task_intake` pour `add --intake`, `task_list_ready`, `task_show`, `task_waves`, `task_claim`, `task_update`, `task_context`) :
même service (`TaskService.add` pour l'ouverture), même gate, même refus. Voir
[Intégration MCP](../../guides/mcp.md).

### Questions structurées

Une question posée à l'humain est un objet du Mission Ledger, pas une phrase dans le
fil de discussion (W6-07, issue #564). Chaque tâche porte le registre de ses questions
(`task show --json`, champ `questions` ; `task questions` le rend à plat avec son statut) :

- **bloquante ou non** : une bloquante gare la tâche en `waiting(human)` ; une non
  bloquante ne la gare pas, l'humain y répond quand il veut ;
- **par lot** : `task ask --from` (ou l'outil MCP `task_update` avec `questions`) pose
  plusieurs questions en **une** attente, une carte du cockpit et une notification ;
  la tâche n'est rendue qu'une fois toutes les bloquantes du lot tranchées ;
- **pourquoi** (`why`) : la raison de la question, montrée au cockpit et dans le registre ;
- **qui a répondu** : `answered` vient du canal humain (cockpit, CLI interactive) ;
  `deduced` vient de l'agent, **source citée** (`task resolve`, ou `answer` et `source`
  dans le lot), et n'est permis que pour une non bloquante ; `not_awaited` est une
  bloquante restée sans réponse que la tâche n'attend pas ou plus (run de flow abandonné,
  tâche annulée) : elle n'est pas comptée comme répondue ;
- **reprise** : une question du lot dont la clé (`key`, ou le texte normalisé) est déjà
  répondue ou déduite n'est pas reposée ; sa réponse est rendue dans `reused`.

```yaml
# lot.yaml — grimoire task ask <id> --from lot.yaml
questions:
  - text: Maquette ou version peaufinée ?
    options: [maquette, peaufiné]
    why: la finition n'est pas déterminable depuis le dépôt
  - text: Supprimer la table users_v1 ?
    kind: permission
    why: arbitrage irréversible
  - text: Quel gestionnaire de paquets ?
    blocking: false
    answer: uv
    source: uv.lock
```

Un agent ne répond jamais à une question bloquante, ni par la CLI hors terminal humain,
ni par le MCP : `task ask` sans les options du lot garde le comportement d'origine
(une question, reposée telle quelle si on la repose).

### Vérifiabilité d'une tâche

`grimoire task show <id>` et la projection du board (`verifiability`) portent
une classe dérivée — jamais déclarée — depuis le texte des critères
d'acceptation et des preuves attendues : **V0** si tous nomment un verdict
qu'un programme rend seul (test, lint, schéma, gate, code de sortie, build/CI,
fichier attendu), **V1** si au moins un critère nomme une revue ou une
validation par une personne ou un agent sans qu'aucun autre ne reste ambigu,
**V2** sinon — y compris une tâche sans aucun critère. Un critère formulé de
façon vague (« le code est propre ») ne peut jamais produire un V0 : un seul
critère ambigu suffit à faire monter la classe, jamais à la faire descendre.
`task show` affiche, critère par critère, le motif reconnu — c'est ce qui rend
lisible pourquoi une tâche n'est pas au niveau qu'on lui prêtait.

### Route de cérémonie et profondeur de test

`grimoire ceremony suggest` calcule, sans LLM, la cérémonie qu'une tâche
mérite : une route parmi cinq, et la profondeur de test exigée à la fermeture.
Deux appels sur les mêmes entrées rendent les mêmes octets (`--json`, avec
l'empreinte `inputs_digest` des entrées et des seuils).

| Commande | Description |
| --- | --- |
| `grimoire ceremony suggest <id> [-f <fichier>…] [--diff-base <ref>] [--impact <n> \| --measure-impact] [--json]` | Route d'une tâche du ledger |
| `grimoire ceremony suggest --title "<titre>" -a "<critère>" [--check "<cmd>"] [--risk <profil>] …` | Route d'une tâche décrite en ligne |
| `grimoire ceremony budget [--file evals/ceremony-budget.yaml] [--json]` | Vérifier un budget de cérémonie ; sortie 1 au premier écart (job CI du kit) |
| `grimoire task add "<titre>" -a "<critère>" --test-depth <floor\|standard\|strict\|auto>` | Exiger une profondeur de test à la fermeture (`auto` : celle de la route) |

Les routes, de la plus légère à la plus lourde : `express` (verdict mécanique
seul, profondeur `floor`), `checked` (tests puis relecture par un agent,
`standard`), `reviewed` (plus une relecture humaine, `standard`), `guarded`
(tests et analyse statique, double relecture, checkpoint humain, `strict`),
`session` (jugement ou conception en session avec une personne, `strict`).
Chaque règle donne un plancher et la route est le maximum des planchers :
V1 → `checked`, V2 → `session`, risque `strict` → `reviewed`, risque
`security_critical` ou `release` → `guarded`, fichier de secret, de workflow CI,
de migration ou de publication → `guarded`, plus de `files_wide` fichiers ou
plus de `impact_wide` importeurs directs des `.py` touchés (`--measure-impact`, lu par `ast` ; le code graph du kit ne résout pas ses arêtes d'import) → `checked`. Des fichiers
inconnus montent la route à `checked` ; un impact non mesuré ne la monte pas,
et la sortie le dit dans `limits`. Les seuils portent `calibrated` et leur
date : aucun n'est calibré sur des runs réels aujourd'hui.

La profondeur se mesure sur les commandes du **dernier verdict**
`grimoire task verify`, pas sur celles que la tâche promet : `floor` (une
commande verte), `standard` (dont une commande de test : celle que le projet
déclare en `needs.commands.test-runner`, ou un outil reconnu comme `pytest`,
`go test`, `npm test`), `strict` (en plus une analyse statique verte : `lint`,
`typecheck` déclarés, ou `ruff`, `mypy`, `eslint`, `tsc`…). Une tâche qui
déclare `test_depth` ne passe en `accepted` ou `released` qu'à cette profondeur ;
sinon le gate refuse avec `acceptance.depth_floor_unmet` (bloquant selon la
sévérité du profil, comme les autres preuves). Une profondeur inconnue est un
refus. Le routeur ne choisit pas de modèle : il choisit la cérémonie.

### Ouvrir une mission

| Commande | Description |
| --- | --- |
| `grimoire mission open "<titre>" [-d "<visée>"] [--scope-repo <dépôt>] [--scope-surface <surface>] [--scope-pack <pack>]` | Ouvre une mission dans le Mission Ledger et rend son identifiant (`MIS-…`) sur stdout ; les `--scope-*` sont répétables |

`task add --mission <identifiant>` y rattache ensuite des tâches (une mission
inconnue est refusée). Deux titres homonymes — à la casse et aux espaces près —
sont refusés avec l'identifiant de la mission existante, y compris entre deux processus simultanés (la vérification et la création se font sous un même verrou) ; un titre vide aussi ; rien
n'est écrit sur un refus (code 1). Le périmètre (`scope.repos`, `scope.surfaces`,
`scope.packs`) est écrit au ledger sans doublon ni entrée blanche, et l'outil MCP
`task_context` le rend sous `mission_scope` avec la tâche (R25, P49).

### Rapport de mission

| Commande | Description |
| --- | --- |
| `grimoire mission report <mission> [--json]` | Coût, acceptance, palier, preuves et rejeu d'une mission, chaque ligne avec sa source |

`grimoire mission report` rassemble ce que trois journaux gardaient chacun de leur
côté : les tentatives de la cascade (`task.dispatched`, Mission Ledger), le résumé
de cascade et l'usage de tokens replié (TraceLedger) et les packs et verdicts
(EvidenceService). Chaque tentative porte son `cost.status` (`exact` ou
`unknown`), l'issue de l'acceptance (`passed`, `failed`, `unrunnable`, `not_run`)
et le `trace_id` de sa cascade ; chaque ligne nomme sa source (`ledger:`,
`traces:`, `evidence:`).

- **Jamais de chiffre inventé.** Sans aucun coût connu, le rapport n'a aucun total
  numérique et écrit « coût inconnu ». Un agrégat partiel est une borne basse
  (`>= X USD`), et une tâche sans donnée de coût est comptée à part.
- **Le journal de session et les tentatives ne s'additionnent pas.** Ce sont deux
  vues des mêmes appels : une tâche retient le journal replié par `task fold-usage`
  s'il existe, sinon les tentatives, et les deux vues restent affichées.
- **Le lien tentative vers trace est exact ou absent.** Une cascade est rattachée à
  l'unique trace `dispatch.outcome` écrite entre sa dernière tentative et la
  première de la suivante ; sinon `trace_id` vaut `null` (`trace_link: none`).
- **Lecture seule et déterministe.** Aucun dossier n'est créé, aucune date de
  génération n'est écrite : deux exécutions sur les mêmes journaux donnent les
  mêmes octets. Mission inconnue ou ledger absent : sortie 1.

### Registre des plans

| Commande | Description |
| --- | --- |
| `grimoire plans list [--status active\|absorbed\|archive\|incubator] [--check] [--registry <fichier>]` | Lister les plans du registre (`deprecated-plans-registry.yaml`) : identifiant, statut, titre, et le plan qui absorbe un plan absorbé (R08) |

Lecture seule. Le registre se cherche à
`_grimoire-runtime-output/planning-artifacts/deprecated-plans-registry.yaml` sous la
racine du projet (`--registry` en désigne un autre) ; absent ou illisible, la commande
le dit et sort en 1, elle n'en invente pas un. La liste part sur stdout (colonnes
séparées par une tabulation), `grimoire -o json plans list` rend le registre entier (l'option `-o json` est globale, il n'y a pas de `--json` propre à la commande). `--check` valide aussi
le registre (ids uniques, statuts connus, `absorbed_by` renseigné, chemins présents,
au moins un plan actif) et sort en 1 s'il est incohérent.

### Plafonds MAST par instance de workflow

Chaque instance créée par le RuntimeKernel (`create_instance`) porte deux
plafonds indépendants, surchargeables à la création (`max_tool_calls=50`,
`max_budget=100` par défaut) : le nombre d'appels d'outils que
`mediate_tool` médiera — la seule unité de « tour » que le kernel observe —
et un budget d'unités de coût consommées par ces mêmes appels (1 unité par
appel par défaut ; un appelant qui connaît un coût réel, en tokens ou en
devise, peut passer `cost=` à chaque appel). Le kernel ne voit aujourd'hui
passer ni tokens ni coût ailleurs, d'où ce choix par défaut. Au premier appel
qui dépasserait l'un des deux plafonds, l'instance passe dans l'état terminal
`refused` : un checkpoint est écrit, un événement `workflow.refused` est
journalisé, et l'appel — comme tout appel suivant sur cette instance — rend
`False` sans jamais lever d'exception ni laisser l'instance continuer en
silence. Voir `src/grimoire/runtime/kernel.py` et `schemas.py`
(`WorkflowStatus.REFUSED`, `RunEventType.WORKFLOW_REFUSED`).

### Pourquoi une tâche s'est arrêtée

`grimoire task trace <id>` lit les journaux qui portent chacun le `task_id`
— le Mission Ledger (transitions, incidents), le TraceLedger des hooks (outils
autorisés ou **refusés par la policy**, clôtures refusées, **gates de
transition rouges**, **dispatch d'agent** ou non-choix), le RuntimeKernel (run
events, checkpoints, **abort/refus et leur raison**), l'EvidenceService
(packs, verdicts) et, s'il existe, l'export OTel du TraceLedger (`grimoire
task trace-export --format otel`, #322) — et les trie dans le temps. Cette
dernière source lit un fichier déjà écrit par `trace-export` (emplacement
conventionnel : `<traces>/otel-export.jsonl`) ; elle ne relance jamais l'export
elle-même. Les entrées qui expliquent un arrêt sont marquées et reprises dans
une section « Cause(s) d'arrêt » ; `--causes` n'affiche qu'elles ; `--output
json` rend la timeline complète avec ses sources. Une source absente est
nommée comme telle ; rien n'est créé, rien n'est inventé. `bootstrap` se trace
aussi — c'est là que les hooks écrivent tant qu'aucune tâche n'est réclamée.

Le cockpit et l'atelier affichent la même timeline dans l'espace **Exécuter**
de la vue de travail (onglet Timeline, drill-down depuis une carte du board),
et l'espace **Observer** y renvoie depuis un span OTel qui porte un
`grimoire.task_id` — voir [la page du cockpit](../../guides/atelier-blueprints.md), section
« Le board (espace Exécuter) ».

```text
GAO-livrer-la-ti-001 — Livrer la timeline  (running)
    21:14:39  ledger   ready → claimed par claude (claim claude@claude-code)
  ✗ 21:14:40  hooks    Bash refusé par la policy (args 3f2a9c1e0b7d4e11)
  ✗ 21:14:41  gate     gate « in_progress_to_review » refuse la transition — manque : evidence_pack, decision_trace
  ✗ 21:14:42  runtime  workflow WFI-recipe-livraison-001 abandonné — raison : suite pytest rouge

Cause(s) d'arrêt : 3
```

### Dispatch par cascade

`grimoire task dispatch <id>` délègue une tâche à un fournisseur LLM du
registre (`llm-provider-registry.yaml`), en cascadant par palier de coût
(`cheap → mid → strong`) selon la classe de vérifiabilité de la tâche : une
tâche **V2** est refusée avant tout appel (aucun verdict mécanique ne peut la
juger), une **V0** cascade depuis `cheap`, une **V1** cascade depuis `mid` et,
au vert, passe en `needs_verification` au lieu d'être considérée close — le
check mécanique n'y est qu'un indice, la classe exige encore un regard
humain. Au moins un `--check <commande>` est obligatoire (code de sortie 0 =
vert, toutes doivent l'être) ; sans lui, la classe dit que le verdict est
mécanique, encore faut-il dire lequel.

Un échec d'appel (429, timeout, code de sortie non nul) passe au fournisseur
suivant du même palier et pose un refroidissement (`grimoire providers
status`) ; un `--check` rouge passe au palier suivant. Chaque tentative laisse
un événement `task.dispatched` au Mission Ledger (palier, fournisseur,
modèle, durée, verdict, coût si la sortie est un JSON portant
`total_cost_usd`, type de tâche, classe de vérifiabilité, palier de départ et
sa raison). `--dry-run` montre la classe, la chaîne de paliers prévue et le
prompt sans rien appeler ; `--max-tier` borne la cascade ; `--provider` la
restreint à un seul fournisseur.

Code de sortie : `0` si la chaîne finit au vert, `1` si elle s'épuise sans
verdict vert, `2` sur un refus (V2, aucun `--check`, aucun fournisseur
invocable). Ce lot ne worktree pas automatiquement : **travaillez sur une
branche propre** avant de dispatcher — le fournisseur délégué écrit dans le
dépôt courant.

#### Palier de départ ajusté par l'historique

Le palier de départ de la cascade n'est plus fixe : sauf `--start-tier`
explicite, il vient de l'historique des dispatchs passés pour le couple
(type de tâche, classe de vérifiabilité) — voir [`grimoire providers
history`](fournisseurs.md#fournisseurs-llm) (issue #312). Par défaut, `cheap` pour V0 et
`mid` pour V1 (le plancher que la classe impose, jamais franchi vers le
bas) ; ajusté vers `mid`, puis `strong`, quand un couple escalade trop
souvent depuis son palier habituel, et re-sondé vers le bas quand il cesse
d'escalader et que le palier inférieur n'a pas été retenté depuis un
moment. Le rapport (`start_tier`, `start_tier_reason`) et l'événement
`task.dispatched` disent tous deux quel palier a été retenu et pourquoi ;
`--start-tier cheap|mid|strong` l'impose sans consulter l'historique.

#### Le pilote : combien dépenser par node (issue #209)

Une fonction de décision — jamais une couche — appelée avant chaque node
dispatché par `flow run --executor dispatch` : le graphe dit *quoi*, le
pilote dit *combien*. Une politique de projet optionnelle,
`_grimoire/standard/pilot.yaml`, la paramètre ; absente, le comportement est
exactement celui décrit ci-dessus (palier de départ ajusté par
l'historique, aucun plafond).

```yaml
start_tier:
  V0: mid          # ne peut jamais descendre sous le plancher de la classe
max_escalations: 1  # 0 = aucune escalade permise, absent = pas de plafond posé ici
max_cost_usd_per_node: 0.50
on_unknown_cost: stop  # défaut ; ou continue_flagged
```

`max_cost_usd_per_node` s'applique dans `missions.dispatch.run_dispatch`
lui-même (`max_cost_usd`) : entre deux paliers, jamais au milieu d'une
tentative ni après un vert déjà acquis — seule l'escalade vers un palier
*plus cher* est abandonnée. Le rapport porte alors `cost_capped` (un message
nommé) plutôt qu'une chaîne simplement épuisée ; côté `flow status`, le node
apparaît avec le verdict `cost_capped`. Un `pilot.yaml` malformé refuse
nommément le chargement plutôt que de retomber en silence sur « aucun
plafond » — contrairement à `orchestration-policy.yaml`, un plafond de coût
est un mécanisme de sécurité.

Un fournisseur qui ne rend pas son coût (aucun JSON portant `total_cost_usd`,
le cas de la plupart des fournisseurs headless) laisse ce coût *inconnu* :
il n'est jamais compté pour zéro. `on_unknown_cost: stop` (défaut,
fail-closed) arrête l'escalade dès qu'un palier a répondu sans coût alors
qu'un plafond est posé — le rapport porte `cost_capped` avec la raison
`cost_unknown` (`cost_cap_reason`). `continue_flagged` laisse escalader ;
le coût reste alors signalé `lower_bound` (`>= 0.4200 USD (1 non pricés)`) ou
`unknown` dans le rapport, la trace `dispatch.outcome`, `grimoire dispatch
stats`, `flow status`, `flow list` et le contrôle `dispatch.cost_slo`
(tous rendent le même format, avec le décompte des appels sans prix). Dans un
flow, `cost_unknown` ne sert qu'à l'escalade, à l'enchaînement des nodes d'un
sous-flow et aux passes optionnelles de `budget` (non lancées sous `stop`,
`skipped_reason: cost_unknown`) : un coût inconnu ne jette jamais un node déjà vert ni les éléments
frères d'un `fanout`, `verify-panel`, `loop-until-dry`, `judge` ou `replay-diff`
(seul un montant connu qui atteint le plafond, `cost_reached`, les arrête).
Ces nodes continuent donc sous `stop` malgré un coût inconnu, mais ne passent pas pour
« sous le plafond » : le node porte `cost_cap_unevaluated: true` (plafond posé, politique
`stop`, appels sans prix — le plafond n'a pas pu être jugé), à lire avec `cost_status`.
Le node porte `cost_cap_reason` (`cost_reached` | `cost_unknown`). La politique
`assume_tier_ceiling` (estimer au plafond du palier) n'existe pas encore.

#### Classe de relisibilité

Un dispatch qui finit au vert classe aussi le diff qu'il a produit
(`git diff --name-only` dans le projet) : `review_required` s'il touche une
surface sensible, `review_optional` sinon. Surfaces par défaut, génériques : exports publics (`*/__init__.py`), surfaces d'entrée (`*/cli/*`, `*/mcp/*`, `*/api/*`), schémas (`*schema*`), standard du projet (`_grimoire/standard/*`, `framework/agentic-standard/*`), vocabulaire de décision (`*/verifiability.py`), politiques, sécurité et hooks (`*/policies/*`, `*/security/*`, `*/hooks/*`). Un diff confiné à `tests/` ou
`docs/` reste toujours `review_optional`. La liste se surcharge projet par
projet dans `_grimoire/standard/orchestration-policy.yaml` (clé
`review_surfaces`, une liste de globs). Un projet qui n'est pas un dépôt git
ne permet pas de calculer le diff : la classe reste `review_optional`, avec
une note qui le dit plutôt qu'un silence trompeur. Le rapport (`review`,
`review_files`) et l'événement `task.dispatched` portent tous deux le
résultat ; `task show` affiche celui du dernier dispatch.

#### Incertitudes déclarées

Le prompt envoyé à l'ouvrier délégué se termine par une consigne : rendre un
bloc délimité ```` ```grimoire-uncertainties ```` contenant une liste JSON
d'objets `{"where": ..., "what": ..., "why": ...}` — le canal d'escalade le
moins cher qui existe, lu ici mécaniquement plutôt qu'en prose. Le dispatch
l'extrait de la sortie de l'ouvrier (le texte brut, ou le champ `result` si la
sortie est un JSON qui l'enveloppe) et stocke le résultat sous `uncertainties`
dans le rapport et dans l'événement `task.dispatched`. Un bloc absent est une
liste vide, silencieusement — la plupart des ouvriers n'y répondront pas
encore ; un bloc présent mais illisible (JSON invalide, pas une liste) est
aussi une liste vide, mais avec un avertissement dans `uncertainty_warnings` ;
un objet du bloc sans les trois clés est ignoré avec son propre avertissement,
les autres objets du même bloc restent gardés. Rien de tout cela ne change le
verdict ni le code de sortie du dispatch. `task show` affiche les incertitudes
du dernier dispatch.

### Mandat de l'orchestrateur (#876, lot 1)

Les gestes de validation (`task accept`, `task answer`, `task approve-check`, `orchestrate resume`,
`queue release`) exigent l'origine humaine. Un **mandat** ajoute un second principal, `orchestrator`, qui
n'agit qu'à l'intérieur de ses bornes. Sans mandat, rien ne change.

```bash
grimoire autonomy mandate grant --gesture accept --gesture approve-check --task-type implementation \
    --max-sessions-per-day 5 --until +7d       # attesté par un tour humain, comme `autonomy grant`
grimoire autonomy mandate status
grimoire autonomy mandate revoke --reason "fin du pilote"
```

- **Borné** : gestes couverts, types de tâche, plafond de sessions de worker par jour, durée. Un mandat
  expiré, révoqué, hors plafond ou qui ne couvre pas le geste est refusé.
- **Journal d'autorité** : `~/.grimoire/control/mandates.authority.jsonl`, chaîné par hash. Un journal
  illisible ou rompu ne porte aucun mandat. Chaque geste pris sous mandat y est inscrit avec
  `origin: orchestrator`, l'identifiant du mandat et sa provenance (règle, verdict ou reçu).
  La troncature de la queue est vue par une tête (`<journal>.head`) et un marqueur par révocation
  (`revoked/<mandat>`) : un journal plus court que sa tête, ou sans tête, est rompu. Pour repartir d'un journal
  rompu, l'humain supprime lui-même les fichiers `mandates.*` et le dossier `revoked/` de `~/.grimoire/control/`.
- **Jamais couverts** : modifier le mandat et modifier la politique d'escalade.
- **Politique d'escalade** : section `escalation` de `_grimoire/standard/orchestration-policy.yaml`, par
  catégories génériques (`release`, `secrets`, `paid`, `security`, `merge`, `accept`), chacune `human` ou
  `orchestrator`. Le gabarit livré garde tout à l'humain. Le dépôt du kit délègue `merge` et `accept` et
  garde `release`, `secrets` et `paid` : cette configuration vit dans
  `_grimoire/standard/orchestration-policy.yaml` du dépôt qui porte le pilote (le dépôt du kit n'en
  versionne pas aujourd'hui). La modifier après l'octroi révoque le mandat.
- **Sous mandat** : un reçu de revue ne vaut que si son relecteur diffère de l'auteur et si la session du
  relecteur figure au journal d'autorité ; les `--check` doivent être ceux scellés à la création de la
  tâche, un check modifié depuis ouvre un événement « disjoncteur ».
- **Principal** : en CLI, `GRIMOIRE_PRINCIPAL=orchestrator` *revendique* le principal et
  `GRIMOIRE_PROVENANCE=<rule|verdict|receipt>:<réf>` cite la provenance ; la revendication ne vaut que si la
  session appelante est inscrite au journal d'autorité en rôle `orchestrator`
  (`register_orchestrator_session(projet, session_id)`, appelée par le lanceur) et n'est celle d'aucun worker ni
  relecteur de la tâche. Sans session inscrite : refus. Le tick, qui est du code et non une session, appelle
  l'API Python dans `with acting_as_orchestrator():` (`grimoire.missions.mandate`), jamais par l'environnement.
- **Cockpit** : `GET /api/workspace/autonomy/mandate`, `POST …/mandate/grant` (jeton de session **et** clé de
  contrôle `~/.grimoire/control/cockpit-key`) et `POST …/mandate/revoke`.
- **Zone protégée** : `~/.grimoire/control/` (journal d'autorité, preuve de contrat d'hôte, clé du cockpit) est
  refusée en écriture aux outils d'agent par le frein `PreToolUse`, et la clé en lecture. Les chemins sont
  résolus (`$HOME`, `..`, `cd` puis chemin relatif, globs, liens symboliques existants, composants cités).
  Limite : un agent qui lance un script arbitraire, ou dont le chemin est assemblé par le shell (`$X`),
  contourne une garde de chemin.

### Fusion par le merger (#876, lot 4)

Le rôle `merger` est une étape de `orchestrate tick --apply`, après le relecteur. Il est désactivé par défaut :

```yaml
# _grimoire/standard/orchestration-policy.yaml
orchestrator:
  roles:
    merger:
      enabled: true
      realign_max: 1                # PR réalignées sur la cible à la fois (le débit CI)
      ignored_checks: ["codecov/*"] # hors du verdict, seulement quand gh dit qu'aucun check n'est requis
      max_cycles: 2                 # retours au worker (CI rouge, conflit) avant l'escalade
      protected_paths: []           # chemins qui reviennent à l'humain, en plus des défauts
```

La file prend les tâches lancées par le tick, en revue, dont la dernière relecture est jugée `READY`, dans
l'ordre des jugements. Le merger n'ouvre pas de PR : il attend celle du worker sur `task/<id>`. Le dépôt GitHub de la
tâche et l'URL de `origin` sont figés au journal d'autorité au lancement, avant le worker : chaque `gh` du merger vise
ce dépôt (`-R`). La tête et la base de la PR viennent de `gh pr view`, ses fichiers de l'API REST, anciens noms d'un
renommage compris ; jamais de refs locales. À chaque tick, le merger vérifie
dans cet ordre, et chaque garde échoue fermée :

| Étape | Ce qui est vérifié | Sinon |
|---|---|---|
| Worktree | celui du ledger, sur `task/<id>`, du même dépôt que le projet | refus |
| PR | ouverte, du même dépôt (pas d'un fork), pas en brouillon, vers la cible du worktree | attente, ou refus |
| READY | la tête GitHub est la tête **scellée** au jugement (ou un réalignement noté au même journal) ; reçu et pack inchangés à l'octet | tête relue pas encore poussée : attente ; sinon nouvelle relecture |
| CI | checks requis (`gh pr checks --required`) ; tous hors `ignored_checks` seulement si gh dit qu'aucun n'est requis ; au moins un `pass` ; vert au tick précédent aussi | rouge : retour au worker ; en attente, sans check ou gh en panne : attente |
| Cible | la base GitHub contenue dans la tête | réalignement en plomberie, poussé sans forcer, puis nouvelle CI |
| Fusion | tête de file seulement (WIP 1), mandat qui couvre `merge`, politique qui le délègue, aucune catégorie gardée, aucun fichier qui escalade, reçu attesté au journal d'autorité | question non bloquante « fusionner ? » : l'humain fusionne lui-même |
| Acceptation | fusion revérifiée, tête fusionnée couverte par le sceau, aucun fichier qui escalade, mandat qui couvre `accept` | question non bloquante « accepter ? » avec la commande `task accept` |

- **Sceau du READY** (`missions/review_seal.py`) : au lancement du relecteur, la tête de `task/<id>` est notée au journal
  d'autorité (`~/.grimoire/control/`). Au jugement, sont scellés la même tête (une branche qui a bougé pendant la
  relecture n'est pas scellée), son arbre git, un worktree propre, et les empreintes du reçu et du pack. Le merger et
  `task accept` sous mandat comparent une tête git à ce sceau, jamais le fichier reçu seul.
- **Chemins qui escaladent** : le code des gardes (`src/grimoire/missions/*`, `src/grimoire/hosts/decisions/*`,
  `src/grimoire/core/*` et l'attestation des flows), la politique (`_grimoire/standard/*`), les workflows et hooks (`.github/*`,
  `.githooks/*`, `.husky/*`), le packaging (`pyproject.toml`, `setup.cfg` et le script setup de setuptools), `.gitattributes`,
  `.gitmodules`, les fichiers conftest de pytest, `.claude/*`, `.mcp.json`, `.vscode/*`, `.pre-commit-config.yaml` et les
  fichiers compilés. La clé `protected_paths` s'y ajoute. La
  comparaison se fait en `casefold()`, sur la liste de fichiers que GitHub annonce ; une liste tronquée fait escalader.
- **Réalignement** : `git merge-tree`, puis `commit-tree -p <tête GitHub> -p <base GitHub>`, sur objets : le worktree du
  worker n'est pas touché. Les objets sont récupérés à refspec explicite vers `refs/grimoire/tick/…`, depuis l'URL de
  `origin` vérifiée égale au dépôt de la PR. Un conflit limité à `CHANGELOG.md` est résolu par l'union des deux côtés,
  refusée si une ligne de la cible manque.
- **Git du tick** (`grimoire.core.safe_git`) : seulement quand un rôle autonome est activé, et le temps du tick,
  l'environnement neutralise pour tous les git du processus ce qui exécute du code. Sont toujours coupés : les hooks, le
  fsmonitor, les signatures et les transports `ext::` et `fd::`. Sont aussi neutralisés, quand le dépôt ou un worktree les déclare localement : les
  filtres, les pilotes de fusion, `core.sshCommand` et `credential.helper` (remis aux valeurs globales de
  l'utilisateur). Rien n'est refusé pour une configuration ordinaire, et l'identité et les identifiants globaux ne sont
  pas touchés. Hors de ce cas, les git du kit sont ceux de toujours.
- **Le geste humain** : le merger ne lit aucune réponse du ledger. Ce qu'il ne fait pas sous mandat, l'humain le fait
  lui-même : il fusionne la PR, puis lance `grimoire task accept <id> --project-root <worktree> --reason "…"`.
- **Limites** :
  - les sessions tournent sous le même utilisateur du système : un processus qui écrit ce que le tick lit avant lui
    contourne une garde de fichier ; seule une isolation par utilisateur du système la fermerait ;
  - un filtre posé par le worker pendant le tick tourne le temps de ce tick ;
  - les pilotes de diff ne sont pas neutralisés, et les git que `gh` lance lui-même n'héritent que de la neutralisation
    par l'environnement ;
  - la liste des chemins qui escaladent est courte ;
  - la branche locale du worker n'est pas avancée par le réalignement ;
  - une PR sans aucun check attend indéfiniment.

### Ce que le claim rappelle

`grimoire task recall <id>` (#141) assemble ce qu'une tâche et ses voisines ont
déjà traversé : son propre historique (tentatives précédentes bloquées ou
échouées), ses tâches voisines — liées par `task link`, de la même mission, ou
au titre proche — avec la cause de leur arrêt même si elles ont depuis été
rouvertes, et ce que la mémoire du projet a consolidé sur des sujets voisins
quand un backend mémoire est configuré (`project-context.yaml`). Sans lui, le
rappel reste utile depuis le seul Mission Ledger. Le texte rendu est borné en
tokens : c'est un rappel, pas un rapport.

```text
[Grimoire — rappel de tâche] GAO-migrer-le-se-002 :
Tâches voisines :
  - GAO-migrer-le-se-001 (même mission, failed)
      cause : connexion refusée par la base cible : pool épuisé
```

Le même rappel est injecté par le hook `SessionStart`, entre la persona
d'entrée et la directive du standard, **seulement pour une tâche réclamée**
(`task.claim` posé — une tâche `proposed` ou `ready` ne rappelle rien) ; et par
l'outil MCP `task_recall`, qui résout la tâche active de la même manière que
`task_context` quand aucun identifiant n'est donné. À la clôture ou au
blocage d'une tâche, `TaskService` consolide ce qui a été appris dans la
mémoire du projet (`decisions` à la clôture, `failures` au blocage) — jamais
sur un mouvement ordinaire.

### Quelle tâche la session porte

Les hooks (`SessionStart`, `UserPromptSubmit`, `Stop`) et `grimoire standard
activation-context` résolvent la tâche courante dans cet ordre :

1. `GRIMOIRE_TASK_ID` — un opérateur dit de quelle tâche la session parle ;
2. le **claim actif du Mission Ledger** : l'unique tâche `claimed` ou `running`,
   restreinte aux claims de `GRIMOIRE_ACTOR` quand cette variable est posée ;
3. l'unique carte `in_progress` du board (projet sans ledger) ;
4. `bootstrap`.

Deux claims simultanés (ou deux cartes `in_progress`) sont ambigus : le niveau
est sauté plutôt que deviné, et `GRIMOIRE_TASK_ID` tranche. `task_context` sans
argument, côté MCP, rend la tâche résolue et la règle qui l'a choisie.

Neuf états côté ledger se projettent sur les huit colonnes du standard. Les
fusions sont décidées et testées : `claimed` et `running` deviennent
`in_progress`, `needs_verification` devient `review`, `failed` devient `blocked`
(avec un motif), `closed` devient `accepted`, `cancelled` devient `archived`.

---
