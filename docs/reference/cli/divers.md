---
audience: référence
---

# Configuration, registre, options globales et divers

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

### `grimoire registry dispatches`

Compte les choix d'agent (#366) et les non-choix (#389) observés, lus dans le
TraceLedger du projet — pas la carte statique des agents déclarés.

```bash
grimoire registry dispatches
grimoire -o json registry dispatches
```

Deux tableaux : les agents réellement choisis comme persona d'entrée (combien
de fois, la dernière fois), et les spécialités cherchées sans spécialiste
trouvé (même forme). Voir [Persona d'entrée → Non-choix](../hotes.md#non-choix).

### `grimoire agent-miss`

Journalise un non-choix : le concierge a cherché un spécialiste et n'en a
trouvé aucun, ou s'est rabattu sur un généraliste.

```bash
grimoire agent-miss --category tests
grimoire agent-miss --category infra --specialty terraform \
  --fallback backend-engineer --reason "aucun agent terraform déclaré"
```

| Option | Description |
|--------|-------------|
| `--category` | Catégorie de la demande telle que comprise (obligatoire, jamais son contenu) |
| `--specialty` | Spécialité cherchée, si elle est nommable |
| `--fallback` | Agent de repli retenu, s'il y en a un |
| `--reason` | Pourquoi aucun spécialiste ne convenait |

Appelée par la persona concierge (`archetypes/meta/agents/concierge.md`), pas
par du code du kit : cette résolution se fait dans son raisonnement. Écriture
best-effort — un journal indisponible ou illisible ne fait jamais échouer la
commande. Détail : [Persona d'entrée → Non-choix](../hotes.md#non-choix).

### `grimoire proposals`

Le déclencheur d'artefact (issue #395) : à la répétition d'une même
spécialité manquante (jamais au premier non-choix), propose un agent — ou un
skill à attacher à un porteur existant — mais ne crée jamais rien sans
acceptation explicite.

```bash
grimoire proposals list
grimoire proposals accept terraform-specialist
grimoire proposals reject terraform-specialist
grimoire -o json proposals list
```

| Commande | Description |
|----------|-------------|
| `grimoire proposals list` | Propositions en attente, rafraîchies depuis `grimoire registry dispatches` |
| `grimoire proposals accept <slug>` | Écrit l'artefact réel dans `_grimoire/overrides/` (même chemin que l'outil d'ajout d'agent) |
| `grimoire proposals reject <slug>` | Marque la proposition refusée — reproposée seulement si son compte double depuis ce refus |

Seuil configurable via `proposals.threshold` dans `project-context.yaml`
(défaut 2, jamais 1). Aucune option `--auto` : accepter reste toujours un
geste explicite. Même moteur que le cockpit (espace Piloter, section
Agents), qui montre et actionne les mêmes propositions.

**Choix du porteur (issue #402)** — la persona d'entrée du projet (le
`concierge`, ou l'agent que `agents.entry` désigne dans
`project-context.yaml`) route les demandes vers un spécialiste ; elle n'en
exécute aucune elle-même, donc elle n'est jamais retenue comme porteuse d'un
skill, même quand c'est elle que le ledger enregistre comme agent de repli
(`grimoire agent-miss --fallback ...`). La colonne « Porteur » de
`grimoire proposals list` (et le champ `target_agent` en JSON) montre
l'agent réellement retenu, distinct au besoin du `fallback_agent` brut ; la
colonne « Raison » (`carrier_reason`) explique le choix :

| `carrier_reason` | Sens |
|---|---|
| `repli observé` | Le repli enregistré n'est pas la persona d'entrée — il est utilisé tel quel |
| `porteur par catégorie : <agent>` | Le repli était vide ou était la persona d'entrée ; exactement un agent déclaré couvre la catégorie observée (`use_when`, ou faisceau `execute` pour une catégorie d'exécution) |
| `persona d'entrée exclue, aucun porteur : agent` | Le repli était vide ou était la persona d'entrée, et zéro ou plusieurs agents couvrent la catégorie — la proposition devient un agent plutôt qu'un skill ambigu |

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
| **Agents** | `add`, `remove`, `agent-miss`, `registry` |
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
[Extensions & marketplace](../../guides/extensions.md).

| Commande | Rôle |
| --- | --- |
| `grimoire ext add <dossier>` | Installer depuis un dossier local |
| `grimoire ext add <id> --registry <clone> [--version X.Y.Z]` | Installer depuis le registry, checksum vérifié |
| `grimoire ext list` | Extensions installées et leurs patterns |
| `grimoire ext verify <id>` | Vérification post-installation |
| `grimoire ext remove <id>` | Désinstallation (hooks retirés du registre de sécurité) |
| `grimoire ext publish <source> --registry <clone>` | Publier une extension ou un `.blueprint.json` |
| `grimoire ext add-blueprint <id> --registry <clone>` | Installer un blueprint publié |

## `grimoire features`

Canaux de features : **stable** (contrat SemVer), **beta** (opt-in par projet,
journalisées, promues par décision des mainteneurs), **experimental** (surface R&D).
État persisté dans `_grimoire/features.json`. La page **Labs** de l'atelier
(`grimoire serve`) expose les mêmes bascules.

| Sous-commande | Rôle |
| --- | --- |
| `list` | Lister les features à canal et leur état pour le projet |
| `enable <id>` · `disable <id>` | Basculer une feature beta (certaines portent une action réelle, ex. `stigmergy-hooks` installe/retire les hooks) |

## `grimoire stigmergy` *(beta)*

Coordination indirecte par phéromones (canal beta — voir
[R&D expérimental](../../explications/experimental.md)). Tableau local `_grimoire-output/pheromone-board.json`.

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
