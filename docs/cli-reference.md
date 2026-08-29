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

Sans ledger, la commande refuse et sort en erreur plutôt que d'écrire un board
vide — écraser le travail déclaré par du néant serait pire que ne rien faire.

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

Configuration interactive du projet.

```bash
grimoire setup [PATH]
```

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

## `grimoire serve`

Mode local UI + API — marketplace, éditeur de blueprints, wizard de setup.
Voir [Mode local & blueprints](serve-blueprints.md).

| Option | Rôle |
| --- | --- |
| `--port, -p` | Port d'écoute (défaut 4173, bind 127.0.0.1) |
| `--project-root` | Racine du projet servi (défaut : dossier courant) |
| `--open / --no-open` | Ouvrir (ou non) le navigateur sur l'atelier |

Pour une UI custom ou une racine de kit explicite (`--ui-dir`, `--kit-root`),
utiliser la forme longue : `python -m grimoire.tools.forge_server`.

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
