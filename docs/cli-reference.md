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
| `--backend, -b` | Backend mémoire (`auto`, `local`, `qdrant-local`, `qdrant-server`, `ollama`) | `auto` |
| `--force, -f` | Écraser la config existante | `false` |
| `--dry-run` | Afficher le plan sans écrire | `false` |
| `--output, -o` | Format de sortie : `text` ou `json` | `text` |

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

Réconcilie l'état du projet avec la configuration.

```bash
grimoire up [PATH] [--dry-run]
```

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
