# Référence CLI

Toutes les commandes disponibles via `grimoire [commande]`.

!!! tip "Auto-complétion"
    Installez l'auto-complétion : `grimoire completion install --shell bash` (ou `zsh`, `fish`).

!!! tip "Raccourcis"
    Des aliases courts sont disponibles : `i`=init, `d`=doctor, `s`=status, `v`=validate, `l`=lint, `ck`=check, `u`=up, `c`=config, `r`=registry.

## Commandes principales

### `grimoire init`

Initialise un nouveau projet Grimoire.

```bash
grimoire init [PATH] [OPTIONS]
```

| Option | Description | Défaut |
|--------|-------------|--------|
| `--name TEXT` | Nom du projet | Nom du répertoire |
| `--archetype, -a` | Archétype d'agents | `minimal` |
| `--backend, -b` | Backend mémoire (`auto`, `local`, `qdrant-local`, `qdrant-server`, `weaviate-server`, `mempalace`, `ollama`) | `auto` |
| `--memory-profile, -m` | Composition mémoire : `lexical`, `standard`, `graphe`, `complet` | déduite |
| `--force, -f` | Écraser la config existante | `false` |
| `--dry-run` | Afficher le plan sans écrire | `false` |
| `--output, -o` | Format de sortie : `text` ou `json` | `text` |

La mémoire se choisit comme une composition, pas comme un backend : le profil
fixe les sept couches du Memory OS d'un coup (mémoire courte, sémantique,
sidecar structuré, graphes, mémoire chaude, visualisation). Sans l'option, le
profil est déduit du store détecté et de l'accès réseau. Voir
[Système de mémoire](memory-system.md).

| Profil | Composition |
|--------|-------------|
| `lexical` | BM25 SQLite seul — aucun modèle, aucun service, aucun réseau |
| `standard` | Sémantique + BM25 fusionnés (RRF) et sidecar structuré |
| `graphe` | Standard + graphes Neo4j : connaissances, souvenirs, code, tâches |
| `complet` | Graphe + mémoire chaude Redis (TTL, baux, coordination multi-agents) |

### `grimoire doctor`

Diagnostique la santé du projet (8 vérifications).

```bash
grimoire doctor [PATH] [--fix]
```

| Option | Description |
|--------|-------------|
| `--fix` | Auto-correction des répertoires manquants |

Vérifie : config YAML, répertoires, archétype, backend, validation sémantique, dépendances optionnelles, version Python.
Avec `--fix`, les répertoires manquants sont créés automatiquement.

### `grimoire status`

Affiche le statut du projet (agents, mémoire, config).

```bash
grimoire status [PATH]
```

### `grimoire validate`

Valide `project-context.yaml` contre le schéma Grimoire.

```bash
grimoire validate [PATH]
```

### `grimoire lint`

Lint avancé : valide structure, types, contraintes et références dans le YAML config.

```bash
grimoire lint [PATH] [OPTIONS]
```

| Option | Description | Défaut |
|--------|-------------|--------|
| `--format, -f` | Format de sortie : `text` ou `json` | `text` |

Accepte un chemin vers un fichier `.yaml`/`.yml` ou un répertoire (cherche `project-context.yaml`).

```bash
# Lint du projet courant
grimoire lint .

# Lint d'un fichier spécifique
grimoire lint configs/project-context.yaml

# Sortie JSON (pour CI)
grimoire lint . --format json
```

### `grimoire up`

Amène le projet à la version du kit installé — c'est la commande de mise à jour.

```bash
grimoire up [PATH] [--dry-run]
```

Sur un projet neuf, elle initialise. Sur un projet existant, elle régénère les
artefacts appartenant au kit (`_grimoire/kit/`, wrappers d'agents, prompts,
instructions) et rafraîchit les artefacts du standard que le projet n'a pas
modifiés. Ce que le projet possède — `project-context.yaml`, `.mcp.json`, la
mémoire, les décisions de conformité, tout `_grimoire/overrides/` — n'est jamais
réécrit.

Le profil du standard suit la même règle : les `--needs` passés à l'installation
sont persistés dans `_grimoire/standard/install-manifest.yaml` et réutilisés à
chaque `up` sans argument — un `up` nu ne rétrograde jamais un profil vers
`starter` (#344). Si le profil déjà en place est plus large que celui qu'un
`up` nu résoudrait, il est préservé et l'étape rapporte `skipped`. Seul un
`--needs` explicite qui réduit le périmètre est honoré, et il est alors
rapporté comme un changement de profil, jamais comme `done`.

L'écriture est différentielle : sans nouvelle version du kit, aucun fichier
n'est touché et le rapport indique `kit artifacts already up to date`. La
commande est donc sûre à lancer à tout moment.

### `grimoire migrate`

Fait passer un projet créé avant la frontière kit/overrides sur cette frontière.
Opération unique : une fois faite, `grimoire up` suffit.

```bash
grimoire migrate [PATH]              # montre ce qui bougerait, n'écrit rien
grimoire migrate [PATH] --apply      # exécute, après snapshot
grimoire migrate [PATH] --adopt-kit  # reprend la version du kit sur les fichiers qui la masquaient
grimoire migrate [PATH] --restore 20260826T193210Z
```

Chaque fichier de l'ancien emplacement est classé par son contenu : s'il
correspond à quelque chose que le kit a livré un jour (catalogue d'empreintes
embarqué), il est régénéré dans `_grimoire/kit/` ; sinon il est considéré comme
étant celui du projet et déplacé dans `_grimoire/overrides/`. Un contenu non
reconnu n'est jamais supprimé.

Le plan signale les fichiers qui vont **masquer** un fichier du kit : ceux-là
cesseront de recevoir les mises à jour. S'ils ne contiennent aucune
customisation, `--adopt-kit` les remet sous la responsabilité du kit.

### `grimoire diff`

Affiche les différences entre la config actuelle et les défauts de l'archétype.

```bash
grimoire diff [PATH]
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` |

### `grimoire env`

Affiche les informations d'environnement (version, Python, dépendances, OS).

```bash
grimoire env
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` |

### `grimoire version`

Affiche des informations étendues sur la version (grimoire, Python, plateforme, projet actif).

```bash
grimoire version
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` |

### `grimoire schema`

Exporte le JSON Schema (Draft 2020-12) pour `project-context.yaml`.

```bash
grimoire schema
```

Utile pour l'autocomplétion YAML dans les IDE (VS Code, JetBrains) et la validation CI.

### `grimoire check`

Exécute lint + validate + vérification de structure en une seule passe.

```bash
grimoire check [PATH]
```

Phases :

1. **Lint** — validation du schéma YAML
2. **Validate** — vérification de la config chargée (`GrimoireConfig.validate()`)
3. **Structure** — vérification des répertoires requis (`_grimoire/`, `_grimoire/_memory/`, `_grimoire-output/`)

Code de sortie `1` si un problème est détecté.

---

## Surfaces hôtes

Le groupe `grimoire host` projette le projet sur ce que chaque hôte sait
exécuter. Détail et dégradations par hôte : [Surfaces hôtes](hosts.md).

| Commande | Description |
|---|---|
| `grimoire host list` | Hôtes connus, surfaces natives de chacun, hôte détecté |
| `grimoire host surface` | Description host-neutre du projet |
| `grimoire host sync --host all` | Générer les surfaces (`--dry-run`, `--force`) |
| `grimoire host status` | Écart entre ce que le projet déclare et ce que l'hôte exécute |
| `grimoire host run <slug>` | Corps d'une commande, pour un hôte sans commandes natives |
| `grimoire host hook --host <h> --event <e>` | Point d'entrée des hooks générés (stdin vers stdout) |

`grimoire init`, `grimoire up --fix` et `grimoire standard init` synchronisent
automatiquement ; l'appel manuel sert après l'ajout ou la modification d'une
persona.

`host list` remonte aussi ce qu'un hôte ne sait pas exécuter, avec le substitut
retenu — dont l'ouverture d'une session dans un agent, qu'aucun hôte ne sait
faire : la persona d'entrée est alors remise à la boucle principale par le hook
`session_start`. Voir [Persona d'entrée](hosts.md#persona-dentree).

## Fournisseurs LLM

Le groupe `grimoire providers` croise le registre déclaratif
(`llm-provider-registry.yaml`, voir [Compatibilité multi-provider](standard/integration.md#compatibilité-multi-provider-llm))
et l'état de refroidissement/audit runtime pour répondre à : quel fournisseur
appeler maintenant, pour quel palier de coût (`cheap`/`mid`/`strong`) ?

| Commande | Description |
| --- | --- |
| `grimoire providers status [--json]` | Fournisseurs activés, modèles par palier, disponibilité (refroidissement + dernier audit), prochain choix |
| `grimoire providers audit [--json]` | Sonder chaque fournisseur activé sans dépenser (PATH, `--version`, modèles Ollama) et journaliser `available`/`models_seen`/`probe_note` dans l'état |
| `grimoire providers cooldown <id> --reason rate_limit\|timeout` | Enregistrer un échec à la main (429, timeout) |
| `grimoire providers history [--ledger-root <chemin>] [--json]` | Historique des dispatchs par couple (type de tâche, classe) et palier de départ recommandé |

`providers audit` ne fait jamais l'appel réel : présence du premier mot de
`invocation` sur le `PATH`, `--version` pour un exécutable reconnu (claude,
gemini, copilot, codex, ollama), et pour un fournisseur local (invocation
`ollama ...` ou `provider_type: local`) la liste des modèles via
`GET /api/tags` (hôte surchargeable par `OLLAMA_HOST`, timeout 3 s). Un
fournisseur jugé `available: false` est écarté par `choose`/`candidates`
jusqu'au prochain audit qui le retrouve — l'audit n'active ni ne désactive
jamais un fournisseur, `enabled` reste une décision de gouvernance.

`providers history` (issue #312) lit les événements `task.dispatched` du
Mission Ledger et compte, par couple (type de tâche, classe de
vérifiabilité) : le nombre de dispatchs et le taux d'escalade depuis
`cheap` et depuis `mid` (part des dispatchs où le palier de départ n'a pas
suffi). Pas de classifieur, pas d'entraînement — des compteurs. Avec au
moins 5 observations pour un couple, un taux d'escalade dépassant 40 %
depuis `cheap` fait recommander `mid`, dépassant 40 % depuis `mid` fait
recommander `strong` ; un couple monté à `mid` dont les 5 dernières
observations à ce palier n'ont plus escaladé, et qui n'a pas retenté
`cheap` depuis autant d'observations, voit `cheap` recommandé à nouveau
(re-sondage) — jamais sous le plancher qu'impose la classe (`mid` reste le
plancher d'une tâche V1). La colonne « départ recommandé » est exactement
ce que `grimoire task dispatch` retient par défaut pour ce couple.

## Web

Le groupe `grimoire web` est le chemin par lequel un agent atteint le web. Il
existe parce que le chemin nu ne suffit pas : `web-browser.py` rend son flux tel
quel, et un agent qui colle ce flux dans son contexte ne distingue plus ce qu'il
a lu de ce qu'on lui a demandé (OWASP LLM01).

| Commande | Description |
| --- | --- |
| `grimoire web fetch <url> [--selector CSS] [--timeout N] [--json]` | Récupérer une page et la rendre enveloppée « donnée externe, pas instruction » |

La sortie est encadrée par deux marqueurs portant un identifiant tiré au hasard
à chaque appel, que la page ne peut pas connaître ; une source qui tente de
recopier le marqueur le voit neutralisé et signalé. En `--json`, le résultat
porte `origin: external` : il ne peut pas relayer une approbation.

Le navigateur tourne en sous-processus — le script est en zone gelée et n'est
jamais importé. Voir
[Contenu externe : donnée, pas instruction](standard/integration.md#contenu-externe--donnée-pas-instruction).

## Standard agentique gouverné

Le groupe `grimoire standard` pilote le standard agentique (profils, patterns gouvernés, preuves). Référence des patterns : [Contrôles gouvernés](standard/controles-gouvernes.md).

| Commande | Description |
| --- | --- |
| `grimoire standard profiles` | Lister les profils (`starter → production`) |
| `grimoire standard needs` | Lister les besoins projet (groupés par tier) |
| `grimoire standard plan --needs <id>` | Prévisualiser le plan (profil + patterns + extras) sans rien écrire |
| `grimoire standard init [.] --needs <id>` | Générer les artefacts standard du projet |
| `grimoire standard verify` | Vérifier les artefacts (fail-closed) |
| `grimoire standard audit` | Rapport de conformité + gaps restants |
| `grimoire standard score` | Calculer et persister un score de conformité |
| `grimoire standard gate check` | Gate CI : échoue si une preuve obligatoire manque |
| `grimoire standard fix [--apply]` | Planifier / appliquer des correctifs sûrs |
| `grimoire standard doctor` | Vérifier la disponibilité des extras technologiques |
| `grimoire standard pattern` | Lister / inspecter les patterns |
| `grimoire standard detect-providers` | Détecter les providers LLM disponibles |
| `grimoire standard traceability [projet]` | Matrice exigences / contrôles / preuves de la norme ; avec un projet, le verdict de `verify` par artefact |
| `grimoire standard upstream` | Le standard amont a-t-il avancé depuis la révision épinglée (sortie 0/2/3) |

Sous-groupes (chacun avec `--help`) : `board`, `memory`, `context`, `decision`, `rules`, `hooks`, `gate`, `events`, `pattern`, `knowledge`.

---

## Tâches agentiques

Le task board gouverné est une **projection** du Mission Ledger, pas une source
(voir [ADR-005](adr-005-mission-ledger-source-of-truth.md)). Éditer
`_grimoire/standard/task-board.yaml` à la main ne change rien au ledger, et le
prochain export l'écrase.

| Commande | Description |
| --- | --- |
| `grimoire task board export [.]` | Régénérer le board depuis le Mission Ledger |
| `grimoire task board export . --dry-run` | Afficher la projection sans écrire |
| `grimoire task board export . --mission <id>` | N'exporter qu'une mission |
| `grimoire task board export . -o <chemin>` | Écrire ailleurs que dans le board du standard |
| `grimoire task add "<titre>" -a "<critère>" [--owner <qui>]` | Ouvrir une tâche (un critère d'acceptation au moins) |
| `grimoire task list [--status <état>] [--mission <id>]` | Lister les tâches et leur colonne de board |
| `grimoire task show <id>` | Détailler une tâche et ce que chaque prochain pas exigera |
| `grimoire task claim <id> [--actor <qui>] [--host <où>]` | Réclamer une tâche prête (`ready → claimed`) |
| `grimoire task move <id> --to <état>` | Déplacer une tâche, si la machine à états et le gate le permettent |
| `grimoire task block <id> --reason "<motif>"` | Bloquer en disant pourquoi |
| `grimoire task close <id>` | Fermer une tâche vérifiée (verdict accepté exigé) |
| `grimoire task link <id> --depends-on <id>` | Déclarer une dépendance |
| `grimoire task context <id>` | Produire le context bundle d'une tâche réelle |
| `grimoire task trace <id> [--causes]` | Timeline unifiée d'une tâche : transitions, outils refusés, gates rouges, checkpoints, abort, preuves, incidents |
| `grimoire task trace-export <dest> [--format otel\|langfuse] [--mission-id <id>]` | Exporte le TraceLedger en JSONL, conventions sémantiques OTel GenAI ou contrat REST Langfuse |
| `grimoire task recall <id>` | Ce que la mémoire du projet sait de cette tâche et de ses voisines — borné en tokens |
| `grimoire task dispatch <id> --check "<commande>" [--check ...] [--dry-run] [--max-tier cheap\|mid\|strong] [--start-tier cheap\|mid\|strong] [--provider <id>] [--timeout <s>]` | Déléguer la tâche en cascade par palier de fournisseur |

Sans ledger, la commande d'export refuse et sort en erreur plutôt que d'écrire un
board vide — écraser le travail déclaré par du néant serait pire que ne rien faire.

Chaque écriture (`add`, `claim`, `move`, `block`, `close`, `link`) franchit deux
portes avant de toucher le ledger : la machine à états, puis le gate de preuve de
`_grimoire/standard/evidence-gates.yaml`. Un refus nomme la preuve manquante et le
remède ; rien n'est écrit. Après une écriture acceptée, le board du standard est
reprojeté depuis le ledger si le projet est enrôlé (`_grimoire/standard/` présent)
— plus besoin de relancer `task board export` pour qu'un claim se voie.

Les mêmes gestes sont exposés aux agents par le serveur MCP (`task_list_ready`,
`task_show`, `task_claim`, `task_update`, `task_context`) : même service, même
gate, même refus. Voir [Intégration MCP](mcp-integration.md).

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

`grimoire task trace <id>` lit quatre journaux qui portent chacun le `task_id`
— le Mission Ledger (transitions, incidents), le TraceLedger des hooks (outils
autorisés ou **refusés par la policy**, clôtures refusées, **gates de
transition rouges**), le RuntimeKernel (run events, checkpoints, **abort/refus
et leur raison**) et l'EvidenceService (packs, verdicts) — et les trie dans le temps.
Les entrées qui expliquent un arrêt sont marquées et reprises dans une section
« Cause(s) d'arrêt » ; `--causes` n'affiche qu'elles ; `--output json` rend la
timeline complète avec ses sources. Une source absente est nommée comme telle ;
rien n'est créé, rien n'est inventé. `bootstrap` se trace aussi — c'est là que
les hooks écrivent tant qu'aucune tâche n'est réclamée.

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
history`](#fournisseurs-llm) (issue #312). Par défaut, `cheap` pour V0 et
`mid` pour V1 (le plancher que la classe impose, jamais franchi vers le
bas) ; ajusté vers `mid`, puis `strong`, quand un couple escalade trop
souvent depuis son palier habituel, et re-sondé vers le bas quand il cesse
d'escalader et que le palier inférieur n'a pas été retenté depuis un
moment. Le rapport (`start_tier`, `start_tier_reason`) et l'événement
`task.dispatched` disent tous deux quel palier a été retenu et pourquoi ;
`--start-tier cheap|mid|strong` l'impose sans consulter l'historique.

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

## Mémoire

Le groupe `grimoire memory` pilote le sous-système mémoire. Concepts, backends
et projections : [Système de mémoire](memory-system.md).

| Commande | Description |
| --- | --- |
| `grimoire memory status` | Santé du backend, nombre d'entrées, configuration |
| `grimoire memory up` | Mettre en place la stack mémoire complète, plan par plan |
| `grimoire memory remember <texte>` | Écriture typée idempotente — même texte et même agent n'écrivent qu'une fois |
| `grimoire memory recall <requête>` | Rechercher parmi les mémoires typées |
| `grimoire memory search <requête>` | Recherche par mot-clé ou similarité sémantique — fusionne vectoriel et BM25 (RRF) dès que le projet possède les deux ; `--no-hybrid` force le backend seul |
| `grimoire memory list` | Lister les mémoires stockées, paginées |
| `grimoire memory delete <id>` | Supprimer une entrée |
| `grimoire memory gc` | Consolider et compacter |
| `grimoire memory export` / `import` | Exporter vers JSON, réimporter |
| `grimoire memory reindex-lexical` | Reconstruire l'index lexical compagnon |
| `grimoire memory gate` | Gate de parité Memory OS entre Weaviate et Neo4j |
| `grimoire memory graph` / `vector` | Synchroniser et vérifier les projections |
| `grimoire memory bundle` | Construire, installer et vérifier les bundles de modèles d'embedding |
| `grimoire memory shared` | Mémoire transverse : ce qui reste vrai d'un projet à l'autre |
| `grimoire memory facts` / `diary` | Graphe de faits temporels, journaux d'agents |
| `grimoire memory migrate` | Planifier et exporter les migrations Memory OS |
| `grimoire memory taxonomy` | Taxonomie aile / salle / pièce |

---

## Hooks git

| Commande | Description |
| --- | --- |
| `grimoire hooks list` | Lister les hooks disponibles et leur état d'installation |
| `grimoire hooks status` | Résumer l'état d'installation — sortie `1` si incomplet |
| `grimoire hooks install` | Installer les hooks git Grimoire dans le dépôt |

---

## Blueprints et flows

Un blueprint décrit un flow agentique comme un graphe de nodes typés. Ces
commandes le manipulent sans passer par l'atelier web. Détail du format et de
l'éditeur : [Mode local et blueprints](serve-blueprints.md).

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
| `grimoire flow resume <run-id> --result <fichier>` | Vérifier la sortie du node courant contre son contrat ; avance ou suspend |
| `grimoire flow abort <run-id> [--reason]` | Abandonner un run — terminal, jamais repris |

Un checkpoint par node : un run interrompu reprend exactement au node
courant, jamais du début. Une sortie non conforme au contrat de sortie du
node **suspend** le run en nommant le node et la pin fautifs, avec un code de
sortie non nul — jamais un échec muet.

#### `--executor dispatch` — la cascade par classe de vérifiabilité (#311)

`run` et `resume` acceptent `--executor dispatch` (défaut : `interactive`,
inchangé). Au lieu d'attendre un `--result` de l'hôte, chaque node est confié
à `grimoire.flows.dispatch_executor` : une tâche de mission est créée (ou
retrouvée) au ledger, liée au run et au node, avec pour critères
d'acceptation ceux du node et pour preuve attendue son contrat de sortie ;
elle est dispatchée en cascade (`run_dispatch`, #323) — l'ouvrier écrit sa
sortie dans un fichier JSON que le kit vérifie contre les pins du node, avec
la même fonction que `flow resume`.

La chaîne suit la classe de vérifiabilité du node (#309) :

- **V0** : `cheap` → `mid` → `strong`.
- **V1** : démarre à `mid` ; un vert marque le node **à relire**
  (`needs_verification` côté ledger), jamais une fermeture.
- **V2** : refusé avant tout appel — le node **revient à l'hôte**, exactement
  comme `flow run`/`flow resume` interactifs : le contrat est présenté,
  `flow resume --result` reprend la main.

`run --executor dispatch` enchaîne les nodes tant que la cascade reste
verte, checkpointe après chacun, et s'arrête au premier node rouge (chaîne
épuisée) ou V2, avec un rapport par node (fournisseur, tentatives, verdict,
coût, escalades) plutôt qu'un contrat. `resume --executor dispatch` (sans
`--result`) reprend de la même façon le node que le run avait laissé ouvert —
utile après un crash comme après une suspension V2 corrigée autrement.
`flow status` affiche, pour tout node dispatché, fournisseur, tentatives,
verdict, relecture requise et incertitudes déclarées — lus depuis
l'événement `task.dispatched` du Mission Ledger, valables même dans un
process qui n'a pas fait le dispatch. `--max-tier` et `--timeout` bornent la
cascade comme sur `grimoire task dispatch`.

---

## Cadrage produit

Cinq phases posées sous `_grimoire/cadrage/` : brief, brainstorm, compréhension,
exigences, cahier des charges.

| Commande | Description |
| --- | --- |
| `grimoire cadrage init` | Poser les cinq phases du cadrage |
| `grimoire cadrage status` | Progression, phase par phase |
| `grimoire cadrage check` | Gate de complétude : exigences et cahier des charges doivent être renseignés |

---

## Cockpit multi-projets

Cockpit local de gouvernance, servi sur `127.0.0.1` uniquement. Le registre vit
dans `~/.grimoire/cockpit/registry.json` ; `grimoire init` y inscrit le projet
et l'annonce, et `GRIMOIRE_NO_COCKPIT` désactive cette inscription.

Le cockpit **ne scanne jamais le disque** : `list` lit le registre tel quel,
jamais le système de fichiers. Un projet qui porte les marqueurs Grimoire mais
n'a jamais été enregistré n'apparaît donc pas dans `list` — ce n'est pas un
défaut de détection, `grimoire cockpit list` signale « ce dossier ressemble à
un projet Grimoire mais n'est pas enregistré » et donne la commande
`grimoire cockpit add <chemin>` à lancer.

`serve` et `start` font l'exception (#351) : lancés depuis un dossier qui
porte un marqueur Grimoire, ils l'enregistrent automatiquement s'il ne l'était
pas encore et l'ouvrent sélectionné d'emblée — sans cesser d'être
multi-projets, la bascule vers un autre projet reste dans l'UI. C'est
l'intention exprimée en lançant la commande depuis ce dossier qui justifie
l'ajout automatique, pas une décision prise à la place de l'utilisateur : le
registre reste modifiable (`remove`, `prune`). Un registre vide, ou un
lancement hors de tout projet Grimoire, nomme `grimoire cockpit scan <racine>`.

| Commande | Description |
| --- | --- |
| `grimoire cockpit add <chemin>` | Enregistrer un projet local |
| `grimoire cockpit list` | Lister les projets gouvernés |
| `grimoire cockpit remove <slug>` | Retirer un projet du registre |
| `grimoire cockpit prune` | Retirer les projets dont le chemin a disparu |
| `grimoire cockpit scan <racine>` | Découvrir les projets sous une racine et les enrôler |
| `grimoire cockpit refresh` | Régénérer la couche de données sans servir |
| `grimoire cockpit serve` | Servir le cockpit (bloquant) |
| `grimoire cockpit start` / `stop` / `status` | Lancer en arrière-plan, arrêter, interroger |
| `grimoire cockpit open` | Ouvrir le cockpit en cours d'exécution dans le navigateur |

`serve`, `start` et `open` ouvrent `workspace/index.html` — la même coque que
`grimoire serve`, avec le niveau Flotte et le sélecteur de projet en plus
(`?project=<slug>`). `portfolio.html` (et les autres pages historiques
partagées avec l'atelier) reste servie mais redirige vers l'espace
correspondant ; `?legacy=1` sur l'ancienne URL l'ouvre sans redirection. Voir
[l'ADR de la vue de travail](adr-006-vue-de-travail.md).

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
| `grimoire workflows teams` | Lister les équipes : membres, spécialité, chaîne de handoff |
| `grimoire workflows search <terme>` | Chercher par slug, description, et optionnellement contenu |
| `grimoire workflows show <slug>` | Afficher un workflow, l'équipe qu'il déclare, et son contenu |
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
team: team-build
triggers:
  - la situation qui appelle ce workflow
---
```

`agents`, `team` et `patterns` sont facultatifs ; `description` ne l'est pas en
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

## Utilitaires

| Commande | Description |
| --- | --- |
| `grimoire context-pack` | Écrire un context-pack durable (contrat catalogue) pour le dépôt |
| `grimoire update` | Mettre à jour grimoire-kit — alias de `grimoire self update` |
| `grimoire debugger status` | État de la réalité et du débogage des agents |
| `grimoire debugger claims` | Affirmations enregistrées par les agents |
| `grimoire debugger plan` | Plan de débogage courant |
| `grimoire debugger generate` | Générer les artefacts de débogage |
| `grimoire debugger serve` | Servir la vue de débogage |

L'alias court `grimoire dbg` existe pour `debugger`.

---

## Sous-commandes

### `grimoire config show`

Affiche la configuration du projet (lecture seule).

```bash
grimoire config show [KEY]
```

- Sans argument : affiche le YAML complet
- Avec clé dot-notation : `grimoire config show project.name`
- Avec `--output json` : sortie JSON

### `grimoire config get`

Récupère une valeur de configuration par clé dot-notation.

```bash
grimoire config get project.name
grimoire config get user.skill_level
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` |

### `grimoire config path`

Affiche le chemin résolu vers `project-context.yaml`.

```bash
grimoire config path
```

### `grimoire config set`

Modifie une valeur de configuration par dot-notation.

```bash
grimoire config set KEY VALUE [--dry-run, -n]
```

| Option | Description |
|--------|-------------|
| `--dry-run, -n` | Affiche la modification sans l'appliquer |
| `--output, -o` | Format de sortie : `text` ou `json` |

Exemples :

```bash
grimoire config set project.name "mon-projet"
grimoire config set memory.backend qdrant-local --dry-run
grimoire -o json config set project.description "My app"
```

> **Note** : les valeurs de type liste ne peuvent pas être définies via `config set`. Utilisez `grimoire config edit` pour modifier directement le fichier YAML.

### `grimoire config list`

Liste toutes les clés de configuration avec leurs valeurs actuelles.

```bash
grimoire config list
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` (table Rich) ou `json` |

### `grimoire config edit`

Ouvre `project-context.yaml` dans l'éditeur système.

```bash
grimoire config edit
```

Utilise `$VISUAL`, puis `$EDITOR`, puis `vi` par défaut.

### `grimoire config validate`

Valide le fichier `project-context.yaml` contre le schéma Grimoire.

```bash
grimoire config validate
grimoire -o json config validate
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` (`{valid, warnings}`) |

Code de sortie 1 si la configuration est invalide.

### `grimoire self version`

Affiche la version installée et vérifie les mises à jour sur PyPI.

```bash
grimoire self version
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` |

### `grimoire self diagnose`

Exécute un auto-diagnostic de l'installation grimoire-kit.

```bash
grimoire self diagnose
```

Vérifie : dépendances requises/optionnelles, version Python, entry point CLI.

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` |

### `grimoire registry list`

Liste les agents disponibles dans les archétypes installés.

```bash
grimoire registry list
```

### `grimoire registry search`

Recherche un agent par mot-clé.

```bash
grimoire registry search QUERY
```

### `grimoire plugins list`

Liste les plugins installés (tools et backends).

```bash
grimoire plugins list
```

| Option | Description |
|--------|-------------|
| `--output, -o` | Format de sortie : `text` ou `json` |

### `grimoire completion install`

Installe l'auto-complétion pour le shell.

```bash
grimoire completion install --shell bash|zsh|fish
```

### `grimoire completion export`

Exporte le script de complétion vers stdout (pour piping/redirection).

```bash
grimoire completion export --shell bash > ~/.local/share/bash-completion/grimoire
grimoire completion export --shell zsh > _grimoire
```

Utile pour la configuration dotfiles et les environnements CI.

---

## Gestion de projet

### `grimoire add`

Ajoute un agent au projet.

```bash
grimoire add AGENT_ID [PATH] [--dry-run, -n]
```

| Option | Description |
|--------|-------------|
| `--dry-run, -n` | Affiche le plan sans modifier la configuration |

### `grimoire remove`

Retire un agent du projet.

```bash
grimoire remove AGENT_ID [PATH] [--dry-run, -n]
```

| Option | Description |
|--------|-------------|
| `--dry-run, -n` | Affiche le plan sans modifier la configuration |

!!! warning "Confirmation"
    La commande `remove` demande une confirmation interactive. Utilisez `--yes/-y` pour la bypasser (CI/scripting), ou `-o json` (confirmation implicite).

Migre la structure du projet vers la dernière version.

```bash
grimoire upgrade [PATH] [--dry-run, -n] [-o json]
```

### `grimoire merge`

Fusionne la configuration de deux projets.

```bash
grimoire merge SOURCE TARGET [--dry-run, -n] [--force] [--undo]
```

!!! warning "Confirmation"
    `merge --undo` demande une confirmation interactive. Utilisez `--yes/-y` pour la bypasser.

### `grimoire history`

Affiche l'historique des opérations CLI récentes (audit trail).

```bash
grimoire history [-n LIMIT] [-f FILTER] [-o json]
```

| Option | Description | Défaut |
|--------|-------------|--------|
| `--limit, -n` | Nombre d'entrées à afficher | `20` |
| `--filter, -f` | Filtrer par nom de commande (ex: `add`, `init`) | — |
| `--output, -o` | Format de sortie : `text` ou `json` | `text` |

Les opérations sont enregistrées automatiquement dans `_grimoire/_memory/.grimoire-audit.jsonl` lors de l'exécution de `init`, `add`, `remove`, `config set`, `upgrade`, et `merge`.

### `grimoire setup`

Synchronise l'identité du projet — utilisateur, langues, niveau — entre
`project-context.yaml`, la source de vérité, et les fichiers qui la reflètent
(`.github/copilot-instructions.md`).

```bash
grimoire setup [PATH] --check                       # audit seul, sortie 1 si un miroir diverge
grimoire setup [PATH] --sync                        # réécrire les miroirs depuis project-context.yaml
grimoire setup [PATH] --user Guilhem --skill-level expert --lang Français --doc-lang Français
```

Une option écrit d'abord la section `user:` de `project-context.yaml` — créée
si le projet est antérieur à son introduction — puis les miroirs ; la
vérification finale relit le fichier et compare les miroirs à lui. Un
« All config files are in sync » n'est donc annoncé que s'il est vrai.

### `grimoire repair`

Auto-réparation des problèmes courants détectés par `grimoire doctor`.

```bash
grimoire repair [PATH] [--dry-run] [-o json]
```

| Option | Description | Défaut |
|--------|-------------|--------|
| `--dry-run, -n` | Prévisualise sans modifier | `False` |
| `--output, -o` | Format de sortie : `text` ou `json` | `text` |

Actions de réparation :
- Création des répertoires manquants (`_grimoire/`, `_grimoire-output/`, `_grimoire/_memory/`)
- Nettoyage des entrées d'audit de plus de 90 jours

---

## Flags globaux

| Flag | Description |
|------|-------------|
| `--version, -V` | Affiche la version et quitte |
| `--verbose, -v` | Augmente la verbosité (`-v` = INFO, `-vv` = DEBUG) |
| `--quiet, -q` | Supprime les sorties hors erreurs |
| `--no-color` | Désactive la coloration (utile en CI) |
| `--log-format` | Format des logs : `text` ou `json` |
| `--output, -o` | Format de sortie : `text` ou `json` |
| `--time` | Affiche le temps d'exécution en ms |
| `--profile` | Affiche le breakdown timing par phase (arbre Rich) |
| `--yes, -y` | Saute les confirmations interactives |
| `--help` | Affiche l'aide |

---

## Sortie JSON pour le scripting

La plupart des commandes supportent `--output json` (ou `-o json`) pour une sortie machine-readable.

| Commande | JSON | Exemple |
|----------|------|---------|
| `status` | ✓ | `grimoire -o json status . \| jq .project` |
| `init` | ✓ | `grimoire -o json init myproject` |
| `doctor` | ✓ | `grimoire -o json doctor . \| jq .failed` |
| `validate` | ✓ | `grimoire -o json validate . \| jq .valid` |
| `check` | ✓ | `grimoire -o json check . \| jq .all_ok` |
| `lint` | ✓ | `grimoire lint . --format json \| jq .count` |
| `diff` | ✓ | `grimoire -o json diff .` |
| `env` | ✓ | `grimoire -o json env` |
| `version` | ✓ | `grimoire -o json version` |
| `config show` | ✓ | `grimoire -o json config show .` |
| `config get` | ✓ | `grimoire -o json config get project.name` |
| `config set` | ✓ | `grimoire -o json config set project.name "new"` |
| `config list` | ✓ | `grimoire -o json config list` |
| `config validate` | ✓ | `grimoire -o json config validate` |
| `add` | ✓ | `grimoire -o json add my-agent .` |
| `remove` | ✓ | `grimoire -o json remove my-agent .` |
| `self version` | ✓ | `grimoire -o json self version` |
| `self diagnose` | ✓ | `grimoire -o json self diagnose` |
| `registry list` | ✓ | `grimoire -o json registry list` |
| `registry search` | ✓ | `grimoire -o json registry search web` |
| `plugins list` | ✓ | `grimoire -o json plugins list` |
| `up` | ✓ | `grimoire -o json up .` |
| `upgrade` | ✓ | `grimoire -o json upgrade .` |
| `schema` | ✓ | `grimoire schema` (toujours JSON) |
| `history` | ✓ | `grimoire -o json history -n 50` |
| `repair` | ✓ | `grimoire -o json repair .` |

---

## Variables d'environnement

| Variable | Description |
|----------|-------------|
| `GRIMOIRE_DEBUG` | `1` pour activer le traceback complet sur erreur |
| `GRIMOIRE_LOG_LEVEL` | Niveau de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `GRIMOIRE_LOG_FORMAT` | Format de log (`text`, `json`) |
| `GRIMOIRE_OUTPUT` | Format de sortie par défaut (`json` pour l'activer sans `-o json`) |
| `GRIMOIRE_QUIET` | `1` ou `true` pour activer le mode silencieux sans `--quiet` |
| `NO_COLOR` | Toute valeur non vide désactive la couleur ([no-color.org](https://no-color.org/)) |
| `GRIMOIRE_OFFLINE` | `1` ou `true` pour forcer le mode hors-ligne (pas de vérification réseau) |

---

## Organisation de l'aide

Les commandes sont regroupées par catégorie dans `grimoire --help` :

| Panneau | Commandes |
|---------|-----------|
| **Project** | `init`, `doctor`, `status`, `up` |
| **Agents** | `add`, `remove`, `registry` |
| **Validation** | `validate`, `lint`, `check`, `schema` |
| **Configuration** | `config`, `diff` |
| **Utilities** | `upgrade`, `merge`, `setup`, `repair`, `completion`, `plugins` |
| **Info** | `version`, `env`, `self`, `history` |

---

## Codes d'erreur

Les erreurs Grimoire incluent un code stable et une suggestion de récupération :

| Code | Signification | Action suggérée |
|------|---------------|-----------------|
| `GR001` | Erreur de configuration YAML | `grimoire validate` |
| `GR002` | Projet non initialisé | `grimoire init <path>` |
| `GR003` | Agent introuvable | `grimoire registry search <name>` |
| `GR004` | Erreur d'exécution d'un outil | `grimoire doctor` |
| `GR005` | Conflit de merge non résolu | Résolution manuelle puis retry |
| `GR010` | Erreur réseau | Vérifier la connexion |
| `GR050` | Erreur de validation schéma | `grimoire validate` |


## `grimoire ext`

Gestion des extensions (bundles d'artefacts gouvernés). Voir
[Extensions & marketplace](extensions-marketplace.md).

| Commande | Rôle |
| --- | --- |
| `grimoire ext add <dossier>` | Installer depuis un dossier local |
| `grimoire ext add <id> --registry <clone> [--version X.Y.Z]` | Installer depuis le registry, checksum vérifié |
| `grimoire ext list` | Extensions installées et leurs patterns |
| `grimoire ext verify <id>` | Vérification post-installation |
| `grimoire ext remove <id>` | Désinstallation (hooks retirés du registre de sécurité) |
| `grimoire ext publish <source> --registry <clone>` | Publier une extension ou un `.blueprint.json` |
| `grimoire ext add-blueprint <id> --registry <clone>` | Installer un blueprint publié |

## `grimoire serve` *(alias déprécié)*

`grimoire serve` est un alias de [`grimoire cockpit serve`](#cockpit-multi-projets)
(#351) : les deux commandes ouvraient la même UI, l'une en mono-projet, l'autre
en multi-projets — `cockpit serve` fait maintenant les deux, en s'ouvrant
directement sur le projet courant quand on le lance depuis un projet Grimoire.
`serve` reste utilisable pour ne pas casser les scripts existants ; il affiche
un avertissement et délègue à `cockpit serve`. `--project-root` est ignoré :
lancer la commande depuis le projet visé suffit.

Pour une UI custom ou une racine de kit explicite (`--ui-dir`, `--kit-root`),
utiliser la forme longue : `python -m grimoire.tools.forge_server`.

### La vue de travail, page par défaut

`--open` ouvre `workspace/index.html` — la coque unique décrite par
`web/DESIGN-SPEC-workspace-2026-09.md` et son
[ADR](adr-006-vue-de-travail.md). Les pages historiques
(`atelier.html`, `kanban.html`, `observability.html`, `memory.html`,
`blueprints.html`, `patterns.html`, `extensions.html`, `labs.html`,
`documentation.html`) restent servies mais redirigent désormais vers l'espace
de la coque qui les remplace ; ajouter `?legacy=1` à l'URL sert encore
l'ancienne page, sans redirection.

#### Source : IntelliSense déterministe (issue 280)

L'éditeur de l'espace Source colorie le frontmatter, les blocs `<agent>`/
`<activation>`/`<step>`, les placeholders (`{project-root}`, `{{…}}`) et les
identifiants d'agents, de patterns et de workflows, sans dépendance ni
bundler (surcouche par recouvrement sur la `<textarea>`, voir
[ADR-006](adr-006-vue-de-travail.md) D2). Un correcteur signale un chemin du
kit qui ne se résout pas (même logique que `grimoire doctor`), un agent
routé mais absent du manifeste, une clé de frontmatter inconnue, un terme du
glossaire cité mais non défini, ou un pattern/workflow inconnu du catalogue —
recalculé à l'enregistrement et au repos de saisie, et repris dans l'onglet
Problèmes du dock. La complétion (`Ctrl+Espace`, ou déclenchée par `{`, `@`,
`/`) propose agents, workflows, patterns, chemins du kit et clés de schéma ;
le survol d'un identifiant lié à une entrée du glossaire ouvre la même bulle
épinglable que le reste de la vue de travail. Tout vient de
`GET /api/workspace/language` (`src/grimoire/tools/workspace_language.py`),
servi en lecture seule sur les deux hôtes — voir le tableau des routes dans
`web/workspace/README.md`. La piste d'un petit modèle local (Ollama) pour des
suggestions de contenu resterait derrière cette IntelliSense déterministe,
jamais à sa place.

### Projets de la machine

Le bouton de projet, en haut de la barre latérale, ouvre le sélecteur. Trois
entrées, parce qu'il y a trois situations :

| Entrée | Ce qu'elle fait |
| --- | --- |
| Liste | Les projets déjà connus de la machine (registre partagé avec `grimoire cockpit`). Un clic re-route le serveur sur le projet choisi. |
| Parcourir | Navigation dossier par dossier depuis `$HOME`, ou chemin absolu collé. Les projets sont signalés au passage. |
| Scanner | Parcours borné d'une racine (profondeur 4 par défaut). Le scan **propose** : rien n'est enrôlé sans sélection explicite. |

Le projet servi est enrôlé au registre à l'ouverture, s'il porte un marqueur
(`.git`, `project-context.yaml`, `_grimoire/`). Ouvrir un projet n'écrit rien
dans son arbre : registre et couche de données vivent sous
`~/.grimoire/`. Purger les entrées mortes : `grimoire cockpit prune`.

### Données affichées : celles du projet, ou rien

Observatoire, Mémoire et Kanban lisent une couche générée sur **le projet
servi** (`gen-site-data.py`, régénérée en arrière-plan au démarrage et à chaque
changement de projet). Tant qu'elle n'existe pas, ces pages sont vides et le
disent.

Le site embarqué dans la wheel contient aussi l'instantané de la vitrine
publique — des projets de démonstration et les chiffres du dépôt du kit. Il
n'est jamais servi en local : afficher 141 entrées mémoire et des traces
d'agents vieilles de deux minutes pour un projet qui n'a rien lancé est pire
qu'une page vide. Seules les références du kit (catalogue de patterns,
marketplace, anatomie) restent servies telles quelles.

L'état de la couche est visible sur le tableau de bord, chip **données** ; un
clic la régénère (`POST /api/data/refresh`).

## `grimoire features`

Canaux de features : **stable** (contrat SemVer), **beta** (opt-in par projet,
journalisées, promues sur métriques d'usage), **experimental** (surface R&D).
État persisté dans `_grimoire/features.json`. La page **Labs** de l'atelier
(`grimoire serve`) expose les mêmes bascules.

| Sous-commande | Rôle |
| --- | --- |
| `list` | Lister les features à canal et leur état pour le projet |
| `enable <id>` · `disable <id>` | Basculer une feature beta (certaines portent une action réelle, ex. `stigmergy-hooks` installe/retire les hooks) |

## `grimoire stigmergy` *(beta)*

Coordination indirecte par phéromones (canal beta — voir
[R&D expérimental](rnd.md)). Tableau local `_grimoire-output/pheromone-board.json`.

| Sous-commande | Rôle |
| --- | --- |
| `emit --type --location --text --agent [--tags] [--intensity]` | Déposer un signal typé (NEED, ALERT, OPPORTUNITY, PROGRESS, COMPLETE, BLOCK) |
| `sense [--type] [--location] [--json]` | Détecter les signaux actifs (au-dessus du seuil) |
| `amplify --id --agent` · `resolve --id --agent` | Renforcer / résoudre un signal |
| `trails` | Patterns émergents (hot-zone, convergence, bottleneck, relay) |
| `evaporate [--dry-run]` · `stats` | Purge des signaux morts / statistiques |
| `install-hooks` · `uninstall-hooks` | Câbler / retirer l'émission + captation automatiques |

Chaque sous-commande accepte `--project-root` (défaut : dossier courant).
L'intensité décroît par demi-vie (72 h), calculée à la lecture — aucun démon.

### Boucle vivante (hooks)

`grimoire stigmergy install-hooks` rend le système autonome, en copiant trois
hooks **non bloquants** dans `.github/hooks/` du projet :

- **SessionStart** — injecte les signaux actifs dans le contexte de l'agent.
- **PostToolUse** — une édition dépose (ou *renforce*, anti-bruit) un signal
  `PROGRESS` sur la zone touchée.
- **Stop** — marque `COMPLETE` la zone la plus active et purge les signaux morts.

Ces hooks sont **safe par construction** : ils n'émettent que du contexte ou
rien, jamais de décision de blocage. S'il existe, ils sont journalisés en
mode `shadow` dans `_grimoire-runtime/_config/hook-safety-registry.json`.
