---
audience: référence
---

# Les flows

## Besoins d'exécution : un flow déclare, le projet résout

Un node de flow ne référence pas `pytest` ni `npm test` en dur : son
acceptance peut déclarer un besoin (`{"run_need": "test-runner"}`) plutôt
qu'une commande (issue #205). `grimoire flow run`/`resume` résolvent ce
besoin à la commande réelle du projet au chargement du blueprint — en deux
temps, jamais un troisième : une déclaration explicite
(`needs.commands` dans `project-context.yaml`) l'emporte toujours sur la
détection par marqueur de projet (`pyproject.toml`, `package.json`,
`Cargo.toml`, `go.mod`). Un besoin qui ne se résout ni par l'un ni par
l'autre refuse le chargement du blueprint en le nommant, avant que le
premier node soit présenté à l'hôte — jamais une installation qui
échouerait au troisième node.

| Commande | Description |
| --- | --- |
| `grimoire needs resolve [--project-root .] [--json]` | Le verdict de résolution de chaque besoin du catalogue pour ce projet, et sa source (`declared`, `detected`, `unresolved`) |

Rétrocompatible : un blueprint à commandes en dur (`{"run": "pytest -q"}`)
reste valide sans aucun changement. `grimoire flow run` affiche un
avertissement (jamais un refus) quand une commande en dur correspond mot
pour mot à un besoin résolu pour ce projet — une invitation à migrer vers
`run_need`, pas une obligation.

## Blueprints et flows

Un blueprint décrit un flow agentique comme un graphe de nodes typés. Ces
commandes le manipulent sans passer par l'atelier web. Détail du format et de
l'éditeur : [Mode local et blueprints](../../guides/atelier-blueprints.md).

| Commande | Description |
| --- | --- |
| `grimoire blueprint new <id>` | Scaffolder un `.blueprint.json` valide depuis un modèle embarqué |
| `grimoire blueprint validate <fichier>` | Valider : JSON Schema, puis contrôles structurels de compilation |
| `grimoire blueprint compile <fichier>` | Compiler en mission pack, avec les mêmes règles fail-closed que l'atelier |
| `grimoire blueprint evals <fichier> --record <relevé>` | Rejouer les évals déclarées contre un relevé d'exécution |

Le rejeu ne fabrique aucun verdict : un cas absent du relevé est rapporté comme
**non exécuté**, jamais comme échoué.

`validate` et `compile` ont deux couches de contrôle, dont la première demande
le paquet `jsonschema`. Quand il manque, la couche ne s'exécute pas et les deux
commandes **refusent** : un contrôle qui n'a pas eu lieu ne peut pas conclure à
un succès. `--allow-skipped-schema` accepte explicitement le contrôle partiel.

### `grimoire flow` — le moteur conduit, l'hôte exécute un node

`blueprint compile` aplatit un blueprint en un unique prompt markdown : le
modèle improvise l'ordre. `grimoire flow` inverse cela (#204) : le
`RuntimeKernel` conduit un blueprint node par node, chaque node reçoit un
contrat borné (entrées, frontière d'outils, critères d'acceptation, contrat
de sortie), et **l'hôte** — jamais le kit — exécute le node. `compile` reste
un repli valide pour les hôtes sans exécuteur de node ; `flow` ajoute une
seconde sortie, il ne retire rien.

| Commande | Description |
| --- | --- |
| `grimoire flow run <fichier>` | Démarrer un run et présenter le contrat du premier node ; sans argument, liste les runs |
| `grimoire flow status <run-id>` | État du run : node courant, nodes faits, dernier refus, contrat courant |
| `grimoire flow next <run-id> [--wave]` | Le node courant ; avec `--wave`, tous les nodes prêts (non faits, prédécesseurs du DAG faits). Lecture seule |
| `grimoire flow resume <run-id> --result <fichier>` | Vérifier la sortie du node courant contre son contrat ; avance ou suspend |
| `grimoire flow abort <run-id> [--reason]` | Abandonner un run — terminal, jamais repris |
| `grimoire flow extract <run-id> [--out <fichier>]` | Extraire un blueprint brouillon de la séquence réellement exécutée par ce run |
| `grimoire flow list [--require-measure <blueprint-id>]` | Le registre local des flows : runs connus par blueprint, avec leur mesure de dispatch |

Un checkpoint par node : un run interrompu reprend exactement au node
courant, jamais du début. Une sortie non conforme au contrat de sortie du
node **suspend** le run en nommant le node et la pin fautifs, avec un code de
sortie non nul — jamais un échec muet.

#### `flow extract` — un flow s'extrait d'un run, il ne se dessine pas (#210)

Créer un flow devient éditer quelque chose qui a déjà fonctionné plutôt que
partir d'un canevas vide. `grimoire flow extract <run-id>` joint les
métadonnées du run (ordre topologique, blueprint source) au Mission Ledger
(commande réellement exécutée par node, via `--executor dispatch`) pour
produire un blueprint brouillon dans le même format `.blueprint.json` — texte
d'abord, lisible et diffable, que le Studio peut ensuite éditer.

Par node, trois choses observées, jamais devinées : l'acceptance déclarée
d'un node **complété** par la cascade est remplacée par la commande
réellement exécutée (`{"run": "..."}`), réinférée en `{"run_need": "..."}`
quand elle correspond caractère pour caractère à un besoin résolu pour ce
projet (issue #205) ; un node **jamais dispatché** (classe V2, ou exécuté par
un hôte interactif sans passer par la cascade) garde son acceptance d'origine
verbatim ; un node **jamais atteint** (run incomplet ou abandonné) est marqué
`not_reached`, sans qu'aucune commande n'y soit inventée. Chaque node du
brouillon porte un objet `"extraction"` (`status`, et `note` sur un refus)
qui dit lequel de ces trois cas s'est produit.

Le brouillon extrait est rejouable : `flow run`/`resume` le chargent comme
n'importe quel blueprint, et reproduisent la même séquence de nodes que le
run source sur le même projet.

#### `kind: "composite"` — un flow est un node (lot 3, issue #206)

Le schéma réservait déjà `kind: "composite"` pour un sous-flow (`ref` =
`use-case:<id>` ou un chemin `.blueprint.json`), mais seul le compilateur
Studio le validait — le moteur de flows ne l'exécutait pas. Depuis cette
issue, un node composite expose des pins comme n'importe quel node, et son
exécution (sous `--executor dispatch`) lance la `ref` comme un **run à part
entière** : son propre `run_id`, ses propres checkpoints, son propre Mission
Ledger — lié au parent par `FlowRunMeta.parent_run_id`/`parent_node_id`.

`ref` reconnaît trois formes, jamais une quatrième inventée :

- un chemin se terminant par `.blueprint.json`, résolu relativement au
  blueprint qui le référence puis à la racine du projet ;
- un id de flow nu, résolu contre le registre local
  (`registry/blueprints/<id>.blueprint.json`) ;
- `use-case:<id>` — réservé à l'expansion Studio/catalogue : le moteur de
  flows **refuse** cette forme au chargement, il ne l'exécute pas.

Trois refus, tous **au chargement du blueprint parent**, jamais quand le node
composite serait présenté à un exécuteur :

- référence introuvable (fichier absent, id absent du registre) ;
- cycle de composition — un blueprint qui se référence lui-même, directement
  ou via un intermédiaire ;
- profondeur de composition supérieure à 3 (un flow racine qui référence un
  sous-flow qui en référence un troisième est la limite).

Le coût du sous-flow est remonté au node composite : `flow status` et le
rapport de `--executor dispatch` montrent son `cost_usd` comme la somme des
coûts de tous les nodes de l'enfant. Le plafond du pilote (issue #209,
`max_cost_usd_per_node`) s'applique à ce **total**, vérifié entre chaque node
de l'enfant — jamais après un node déjà vert : un dépassement abandonne le
run enfant (`flow status <child-run-id>` le montre `aborted`) et ferme le
node composite en `cost_capped`, comme un node simple dont la cascade aurait
renoncé au palier suivant.

Sous l'exécuteur `interactive` (par défaut), un node composite n'est **pas**
auto-lancé — son contrat affiche `composite:<ref>` comme frontière d'outils,
et l'hôte lance lui-même `grimoire flow run <ref>` avant de soumettre un
résultat conforme aux pins déclarées, comme pour tout autre node interactif.

`flow extract` (#210) ne l'aplatit jamais : le node composite reste
`kind: "composite"` avec sa `ref` d'origine dans le brouillon, et son
`extraction` porte le `child_run_id` du sous-flow qu'il a lancé quand il y en
a un — le sous-flow s'extrait séparément, sur son propre `run_id`.

`grimoire.flows.registry.describe_flow` (utilisé par `flow list`, ci-dessous)
lit, en plus, `version`/`kitMin`/`kitMax` au niveau du blueprint (tous
optionnels — `version` retombe sur `0.0.0`) et l'union des `run_need`
déclarés par le flow et par tous ses sous-flows composite.

#### Les sept genres de node (lot 4, issue #207)

`composite` (ci-dessus) est le cas dégénéré : un sous-flow, une tentative,
aucune agrégation. Sept genres de plus généralisent le même mécanisme —
lancer un ou plusieurs sous-flows enfants — avec chacun sa propre politique
d'agrégation. Aucun n'est exprimable dans un document de consignes statique ;
tous deviennent directs dès que le moteur tient l'état. Chacun n'existe que
sous `--executor dispatch` (`grimoire.flows.genres`, fonctions
`_execute_<genre>` — module compagnon de `grimoire.flows.dispatch_executor`,
extrait de lui pour rester sous le seuil du cliquet de lignes) ; sous
`interactive`, le contrat affiche le genre et sa
`ref` comme frontière d'outils, l'hôte orchestre lui-même.

Paramétrage refusé nommément **au chargement du blueprint**
(`grimoire.flows.blueprint_loader._validate_genre_config`) — jamais un
`TypeError`/`KeyError` anonyme à l'exécution :

| Genre | `ref` | `config.<genre>` requis |
| --- | --- | --- |
| `fanout` | sous-flow modèle | — (voir ci-dessous) |
| `verify-panel` | sous-flow vérificateur | `k` (entier ≥ 2), `angles` (liste de `k` chaînes) ; `class` optionnelle (`V1` ou `V2`, défaut `V2`) |
| `loop-until-dry` | sous-flow | `maxRounds` (entier ≥ 1) |
| `judge` | sous-flow candidat | `n` (entier ≥ 2), `angles` (liste de `n` chaînes) |
| `checkpoint` | ignorée (chaîne vide par convention) | — |
| `budget` | ignorée — voir `config.budget.passes` | `maxCostUsd` (nombre > 0), `passes` (liste non vide de refs) |
| `replay-diff` | sous-flow | — |

**`fanout`** — un node produit N éléments, N sous-flows instanciés, les
résultats sont recollés. Deux temps : le node est d'abord dispatché
normalement (comme un node ordinaire), sa sortie doit porter, en plus de ses
pins déclarées, une clé `fanout_items` (liste non vide) ; le moteur instancie
alors un sous-flow (`ref`) par élément. N est borné par
`pilot.max_fanout_n` (`_grimoire/standard/pilot.yaml`) — **absent**, le
fan-out est refusé : jamais une politique manquante ne vaut un plafond
infini implicite. Vert seulement si les N enfants finissent tous verts ;
recoller un résultat partiel comme s'il était le tout serait l'invention que
l'issue refuse.

`pilot.max_parallel` (défaut `1`, séquentiel) borne le nombre d'enfants actifs en
même temps. Au-delà de 1, le plafond `max_cost_usd_per_node` est relu avant
chaque lancement et un coût inconnu bloque les lancements suivants, comme un
enfant suspendu à l'hôte (aucun lancement après la suspension) ; les
enfants déjà en vol ne sont pas rétractés (dépassement borné par
`max_parallel - 1` enfants). Les enfants partagent le même répertoire : à
n'activer que pour des enfants qui n'écrivent pas les mêmes fichiers.

**`verify-panel`** — k vérificateurs indépendants (`config.verifyPanel.k`),
chacun sous un angle distinct (`.angles`, instruit de chercher à réfuter,
jamais de confirmer par défaut). Chaque vérificateur vote par un `verdict`
structuré (`READY` ou `NOT-READY`) dans sa sortie ; sans verdict, il
s'abstient (finir n'est pas approuver). `config.verifyPanel.class` : `V1`
exige la majorité stricte des votes exprimés, `V2` (défaut) leur unanimité.
Si les votes exprimés ne sont pas une majorité de k, le node rend
`inconclusive` ; des `tree_fingerprint` différentes parmi les votants le
rendent `invalid`. Coût = k tentatives,
**toujours** — jamais une cascade qui s'arrête au premier consensus
apparent : un plafond de coût qui empêche de compléter les k tentatives
referme le node en `cost_capped`, jamais sur un vote partiel (« jamais un
vert sur 1/k »).

**`loop-until-dry`** — relance jusqu'à `config.loopUntilDry.maxRounds` tours
sans nouveauté. Chaque tour doit porter une clé `novelty_key` (chaîne) dans
sa sortie ; la déduplication porte contre **tout le vu** depuis le premier
tour (un ensemble cumulatif), jamais seulement contre le dernier tour
retenu. `maxRounds` est une borne dure, distincte d'un séchage réel
(`loop_until_dry_stop_reason` : `"dry"` / `"max_rounds"` / `"cost_capped"`).

**`judge`** — N tentatives sous angles imposés (`config.judge.n`/`.angles`),
puis le node lui-même est dispatché une seconde fois comme juge (V1 —
« un humain choisit »), avec le résumé des N tentatives dans son contexte de
tâche ; sa sortie doit porter `judge_winner` (index 0..N-1). La trace des N
tentatives survit dans leurs propres runs (Mission Ledger de chaque enfant),
jamais seulement dans un champ éphémère du node.

**`checkpoint`** — approuver, refuser, amender. Toujours une décision
d'hôte forcée, **jamais auto-décidée par la cascade** (même sous
`--executor dispatch`, ce genre pend systématiquement, comme un node V2).
La reprise (`flow resume --result`, forme interactive, quel que soit
l'exécuteur qui a conduit le reste du run) doit porter
`checkpoint_decision` (`approve`/`reject`/`amend`) ; `reject` bloque le run
en nommant le motif (`checkpoint_reason`) dans l'événement de refus du
kernel — « le motif dans le graphe de décision » — une reprise ultérieure
rouvre exactement ce même node. `amend` (W2-10) demande une révision : le
node reste ouvert, la décision et son motif sont inscrits dans le journal du
run (relu par `flow status`) et comptés. `accept_as_is` n'est accepté qu'après
`config.checkpoint.max_amendments` amendements (entier >= 1, 3 par défaut) ; il
exige `checkpoint_reason` (le risque nommé), inscrit `accepted_risk`, et pose
`task.risk_accepted` sur la tâche du ledger liée au run (`--task-id`) si elle
existe ; le ledger le rejoue dans la tâche (`accepted_risks`) et `classify` la remonte
au moins en V1 pour toute surface (`task show`, MCP `task_show`/`task_claim`,
`dispatch_advice`, transition `needs_verification` de `task dispatch`).
Limite : le compteur est celui du journal du run, `amend` non attesté compris.
`config.checkpoint.plan` (W2-11, fournisseur connu : `upgrade-removals`) lie la
décision à la liste projetée de ce qu'elle engage : hachée à la présentation
(inscrite dans le challenge), recalculée à la décision. `approve` et
`accept_as_is` dont le plan a changé — ou dont la projection est impossible —
sont refusés en `plan_drifted` avec les écarts, sans consommer le tour humain ;
le challenge suivant porte le plan courant et exige un nouveau tour. Limite :
l'empreinte couvre la projection du fournisseur, pas ce qu'un exécuteur ferait
après la décision. `grimoire upgrade-flow review` affiche le plan présenté.

**Octroi d'autonomie (`grimoire autonomy`, W5-06)** — la seule voie pour
faire franchir un checkpoint destructif sans humain (CI, banc) ;
`GRIMOIRE_UNATTENDED=1` seul n'ouvre rien.

| Commande | Effet |
|---|---|
| `grimoire autonomy grant --scope <sel> --max-cost-usd <usd> --until <échéance>` | Demander un octroi. `<sel>` : `run:<id>` ou `blueprint:<id>`, `/<node>` en option, répétable, aucun joker ; `<échéance>` : `+30m`, `+2h`, `+1d` ou un instant ISO 8601 avec fuseau. Les trois bornes sont obligatoires. |
| `grimoire autonomy status` | Chaque octroi réévalué maintenant : actif ou révoqué (motif), coût dépensé, échéance ; `-o json` ajoute les événements. |
| `grimoire autonomy revoke <grant-id> [--reason]` | Révoquer à la main, toujours permis. |

L'octroi est lui-même une décision attestée : la première demande émet un
challenge lié à la session et sort en code 3 (décision humaine requise) ; un
tour humain de cette session, puis la même commande relancée, l'inscrit et
consomme ce tour. Seul un challenge émis par la session courante est repris : un
challenge ouvert sans session ou par une autre session n'empêche pas la demande
d'une session, qui reçoit alors le sien. Un `/<node>` restreint le seul run que
désigne le sélecteur, pas ses sous-flows ni les nodes homonymes d'un autre
blueprint ; sans `/<node>`, les sous-flows sont couverts. Tant qu'il est valide, un
`approve` ou `accept_as_is` non attesté sur un checkpoint du périmètre est accepté avec
`attestation=granted:<grant-id>`, distinct d'`attested`. Il est révoqué, une
fois pour toutes, à l'échéance (`expired`), quand le coût atteint le plafond
(`cost_reached`), au premier coût inconnu (`cost_unknown`, politique `stop`
sans alternative), et au premier gate rouge du périmètre (`gate_red` : une
sortie refusée par son contrat, ou une tentative de dispatch `red` ou
`unrunnable`). Le checkpoint suivant repasse en `awaiting_human` et le refus
nomme l'octroi révoqué. Chaque challenge, octroi et révocation est une ligne de
`_grimoire-runtime-output/flows/authority/@autonomy-grants.authority.jsonl`,
journal que la garde d'autorité interdit d'écrire à l'agent.
Limite : le coût mesuré est celui des tentatives `task.dispatched` des runs
couverts, postérieures à l'octroi ; le coût de la session hôte n'est mesuré
nulle part dans le kit et n'y entre pas (`cost_scope` dans `status`).

**`budget`** — plafond de coût déclaré (`config.budget.maxCostUsd`),
abandon des passes optionnelles avant dépassement. `passes` est une liste
de refs de sous-flows ; la première est obligatoire (son échec fait échouer
le node) ; chacune des suivantes n'est lancée que si le coût cumulé reste
sous le plafond — sinon abandonnée, jamais tentée, sans faire échouer le
node. Distinct de `pilot.max_cost_usd_per_node` : ce plafond-ci est déclaré
par le blueprint, propre à ce node.

**`replay-diff`** — rejoue le même flow (`ref`) sur la même entrée (deux
runs enfants, sans variation d'un à l'autre) et compare les traces.
`replay_diverged` (booléen, dans la sortie) dit si les deux traces
(statut + nodes complétés) divergent — une divergence est un signal,
jamais un défaut : le verdict reste vert tant que les deux relectures
finissent, qu'elles divergent ou non.

**Coût et pilote, pour les sept genres** : `pilot.max_cost_usd_per_node`
s'applique au **total** de chaque genre, jamais à une seule tentative —
vérifié avant chaque nouveau sous-flow lancé, jamais après un enfant déjà
vert (même garantie que pour `composite`). **Extraction** : `flow extract`
ne les aplatit jamais — `kind`, `ref` et `config.<genre>` survivent
verbatim dans le brouillon, exactement comme `composite`. **Rust** : aucun
changement — les sept genres sont de l'orchestration Python (E/S, lancement
de runs enfants), hors périmètre des fonctions pures déjà portées par
`grimoire-flows-core` ; la grille de transitions et les contrats de node
n'ont pas changé.

#### `--executor dispatch` — la cascade par classe de vérifiabilité (#311)

`run` et `resume` acceptent `--executor dispatch` (défaut : `interactive`,
inchangé). Au lieu d'attendre un `--result` de l'hôte, chaque node est confié
à `grimoire.flows.dispatch_executor` : une tâche de mission est créée (ou
retrouvée) au ledger, liée au run et au node, avec pour critères
d'acceptation ceux du node et pour preuve attendue son contrat de sortie ;
elle est dispatchée en cascade (`run_dispatch`, #323) — l'ouvrier écrit sa
sortie dans un fichier JSON que le kit vérifie contre les pins du node, avec
la même fonction que `flow resume`, **et** exécute l'acceptance structurée du
node quand le blueprint en déclare une (voir « Acceptance structurée d'un
node », ci-dessous, issue #428) : une enveloppe conforme ne suffit plus seule
à fermer un node V0 dont l'acceptance déclare une commande.

La chaîne suit la classe de vérifiabilité du node (#309) :

- **V0** : `cheap` → `mid` → `strong`.
- **V1** : démarre à `mid` ; un vert marque le node **à relire**
  (`needs_verification` côté ledger), jamais une fermeture.
- **V2** : refusé avant tout appel — le node **revient à l'hôte**, exactement
  comme `flow run`/`flow resume` interactifs : le contrat est présenté,
  `flow resume --result` reprend la main.

`run --executor dispatch` enchaîne les nodes tant que la cascade reste
verte, checkpointe après chacun, et s'arrête au premier node rouge (chaîne
épuisée), acceptance inexécutable, ou V2, avec un rapport par node
(fournisseur, tentatives, verdict, coût, escalades, statut d'acceptance)
plutôt qu'un contrat. `resume --executor dispatch` (sans `--result`) reprend
de la même façon le node que le run avait laissé ouvert — utile après un
crash comme après une suspension V2 corrigée autrement. `flow status`
affiche, pour tout node dispatché, fournisseur, tentatives, verdict,
relecture requise, incertitudes déclarées et **statut d'acceptance**
(`exécutée`/`inexécutable`/`jugée`) — lus depuis l'événement
`task.dispatched` du Mission Ledger, valables même dans un process qui n'a
pas fait le dispatch. `--max-tier` et `--timeout` bornent la cascade comme
sur `grimoire task dispatch`.

#### Acceptance structurée d'un node (issue #428)

Un node V0 est « vérifiable mécaniquement » : par défaut, le vocabulaire de
son `acceptance` textuel (« la suite de tests passe », …) sert uniquement à
dériver sa classe de vérifiabilité (#309) — le gate de la cascade ne vérifie
que la conformité de l'enveloppe JSON au contrat de sortie du node, jamais le
texte lui-même. Sur un flow réel rejoué le 2026-09-11 (épic #307, lot 3),
cela a laissé fermer vert un node dont la vraie suite `pytest` ne pouvait pas
même être collectée (dépendance absente) : l'enveloppe était conforme, rien
d'autre n'avait tourné.

`node.acceptance` accepte désormais, en plus d'une chaîne libre
(rétrocompatible, comportement inchangé), une entrée structurée que le gate
exécute réellement après la réponse de l'ouvrier — dans le projet, en
sous-processus, délai borné :

```json
{"run": "pytest -q tests/test_x.py", "expect_exit": 0, "cwd": ".", "timeout_s": 120, "expect_stdout_contains": "passed"}
```

Seul `run` est requis ; `expect_exit` (0 par défaut), `cwd` (`.` par défaut),
`timeout_s` (120 par défaut) et `expect_stdout_contains` (aucun par défaut)
sont optionnels. Deux formes d'evidence mécanique existent aussi, pour éviter
d'écrire une commande à la main :

- `{"path_exists": "dist/rapport.json"}` — un fichier doit exister.
- `{"test": "tests/test_x.py::test_y"}` — un test nommé doit passer (traduit
  en une invocation `pytest` du seul identifiant déclaré).

`acceptance` peut mélanger texte libre et entrées structurées dans la même
liste. Une forme structurée invalide (clé inconnue, plusieurs clés sur la
même entrée, type incorrect) est un refus nommé **au chargement du
blueprint** (`GrimoireRuntimeError`), jamais un gate qui échouerait ouvert
plus tard — et le kit **n'infère jamais** de commande depuis la prose : sans
entrée structurée, un node V0 garde le comportement historique (enveloppe
seule), documenté ci-dessous comme limite connue.

Le verdict d'un node dont l'acceptance est exécutée suit trois issues,
jamais une quatrième :

- **exécutée, verte** — toutes les commandes déclarées ont rendu le code de
  sortie attendu (et la sous-chaîne attendue, si `expect_stdout_contains` est
  posé). Le node ferme normalement.
- **exécutée, rouge** — au moins une commande a rendu un code de sortie
  inattendu, sans qu'aucun signal d'inexécutable (ci-dessous) n'ait été
  détecté. Suit la cascade normale : réessai au fournisseur suivant du même
  palier, puis escalade au palier suivant, comme un check d'enveloppe rouge.
- **acceptance inexécutable** — refus nommé, distinct d'un échec, **jamais un
  succès** : la commande n'a pas pu rendre de verdict exploitable (binaire
  introuvable — code de sortie 127 ; `pytest` qui rend 5 « no tests
  collected » ou 4 « usage error » ; une erreur d'import détectée dans la
  sortie, quel que soit le code de sortie). La cascade s'arrête **net** sur
  ce node — changer de fournisseur ou de palier ne répare pas un
  environnement cassé — et `flow status` nomme le node en faute.

#### La classe V0 exige une acceptance structurée

Un node dont le texte seul suffirait à le classer V0 (#309 — « la suite de
tests passe », par exemple) mais qui ne déclare **aucune** entrée structurée
est **rétrogradé en V1** : la cascade démarre à `mid` (pas `cheap`), et un
vert ne ferme jamais le node — il le marque **à relire**
(`needs_verification`), exactement comme un vrai V1 déclaré. Sans cette
règle, le texte mécanique seul aurait rouvert le fossé même que l'issue #428
corrige : le gate n'aurait toujours que le check d'enveloppe à faire tourner
pour ce node, et fermerait vert sur la seule foi de l'ouvrier — la
rétrogradation, pas seulement le libellé du rapport, est ce qui l'empêche.

Cette dérivation est appliquée **une seule fois**, au chargement du
blueprint, et transmise à la fois au démarrage de la cascade (`run_dispatch`)
et au rapport (`DispatchExecutor`) — `flow status` et `flow run` s'accordent
donc sur la même classe pour le même node, jamais recalculée deux fois de
façon divergente. Un avertissement nommé accompagne la rétrogradation, à la
fois dans `flow status`/le rapport de dispatch et dans les logs :

```text
nœud n classé V0 sans acceptance exécutable : traité comme V1
```

Le kit **n'infère jamais** de commande depuis la prose pour éviter cet
avertissement : la seule sortie est de déclarer une entrée structurée
(`run`/`path_exists`/`test`) sur le node.

Un node dont l'acceptance est V1 **déclarée** (vocabulaire de revue, « revue
humaine avant fusion ») n'est pas concerné par cette règle — il n'a jamais
prétendu être V0 — et garde le comportement actuel du kit, documenté ici sans
être étendu : un vert V1 ferme la cascade en marquant le node à relire, sans
qu'aucun juge automatisé n'intervienne. Faire arbitrer une classe V1 par un
palier supérieur plutôt que par une revue humaine est hors périmètre de
l'issue #428 (voir son texte : « ce que ce ticket refuse »), à traiter par un
ticket séparé s'il devient nécessaire.

Dans les deux cas (V1 déclaré, ou V0 rétrogradé), `flow status` rapporte le
statut d'acceptance **« jugée »** : le mode conservateur qui ne prétend
jamais à une exécution qui n'a pas eu lieu.

La trace du node (visible dans `flow status --output json` et le rapport de
`flow run --executor dispatch`) porte, pour chaque commande exécutée, le code
de sortie et la sortie tronquée à 2 Ko (stdout puis stderr) — de quoi lire un
message d'erreur `pytest` sans relancer la commande à la main.

---

## Workflows

Un workflow a une **nature**. Les commandes d'hygiène (`command`) doublent un
diagnostic CLI ; les orchestrations (`orchestration`) coordonnent plusieurs
agents sur plusieurs tours et déclarent parfois l'équipe avec laquelle elles
tournent. Les deux familles vivent dans des répertoires différents — les
premières sous `.github/prompts/`, les secondes sous le tier kit — et le
catalogue indexe les deux.

| Commande | Description |
| --- | --- |
| `grimoire workflows list` | Lister les workflows — nature, agents, provenance |
| `grimoire workflows list -k orchestration` | Ne garder que les workflows multi-agents |
| `grimoire workflows list --all` | Inclure ceux qu'une commande CLI remplace |
| `grimoire workflows search <terme>` | Chercher par slug, description, et optionnellement contenu |
| `grimoire workflows show <slug>` | Afficher un workflow, ce qu'il déclare, et son contenu |
| `grimoire workflows install <slug>` | Installer un workflow du framework — la destination suit sa nature |
| `grimoire workflows sync` | Synchroniser les prompts du framework vers le projet |
| `grimoire workflows diff` | Différences entre framework et projet |
| `grimoire workflows doctor` | Auditer les prompts du projet contre les défauts du framework |
| `grimoire workflows prune` | Retirer les prompts propres au projet absents du framework |

### Workflows remplacés par une commande

Quatre des sept prompts livrés redisaient une commande du SDK :

| Workflow | Commande qui le remplace |
|----------|--------------------------|
| `/grimoire-status` | `grimoire status` |
| `/grimoire-health-check` | `grimoire doctor` |
| `/grimoire-self-heal` | `grimoire doctor --fix` |
| `/grimoire-pre-push` | `grimoire check` |

Ils occupaient la moitié du catalogue. Ils restent livrés et installables — un
projet qui les a ne les perd pas — mais ne sont plus déployés dans les projets
neufs ni listés par défaut : `--all` les montre, avec la commande à utiliser.

Les trois autres restent de plein droit : `/grimoire-changelog`,
`/grimoire-dream` et `/grimoire-session-bootstrap` lisent l'historique et la
mémoire pour en tirer une synthèse, ce qu'aucune commande ne fait.

### Se déclarer pour entrer au catalogue

Un workflow d'orchestration entre au catalogue en le disant, dans son
frontmatter — pas en étant deviné depuis son emplacement, parce que sous
`workflows/` vivent aussi des gabarits de rapport rendus à chaque run :

```yaml
---
kind: orchestration
description: "Ce que fait ce workflow"
agents: [sm, architect, dev, qa]
triggers:
  - la situation qui appelle ce workflow
---
```

`agents` et `patterns` sont facultatifs ; `description` ne l'est pas en
pratique, c'est ce que le catalogue affiche. Un fichier sans frontmatter reste
sur disque et n'est pas listé.

`patterns` cite les identifiants du catalogue de patterns (`ORC-01`, `KNO-02`…)
que le workflow instancie, et `memory` les couches du Memory OS qu'il lit ou
écrit. Aucun workflow livré ne déclare de couche mémoire : leurs fichiers ne le
disent pas, et l'inventer contredirait la règle du dépôt — ce qui décrit un
artefact doit en être dérivé. Le champ existe pour les workflows que vous
écrivez.

Un prompt qui redit une commande du SDK le déclare avec `deprecated_by:`,
suivi de la commande.

---
