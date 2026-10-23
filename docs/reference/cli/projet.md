---
audience: référence
---

# Le projet : création, état et diagnostic

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
| `--backend, -b` | Backend mémoire (`auto`, `local`, `lexical`, `qdrant-local`, `qdrant-server`, `weaviate-server`, `mempalace`, `ollama`) | `auto` |
| `--memory-profile, -m` | Composition mémoire : `lexical`, `standard`, `graphe`, `complet` | déduite |
| `--memory-collection TEXT` | Nom explicite de la collection, pour un backend partagé | déduit du nom du projet |
| `--memory-stack` | `up` démarre (Docker) les services que le profil recommandé nécessite — même consentement que `-y`, pour un run sans `-y` | (aucun) |
| `--force, -f` | Écraser la config existante | `false` |
| `--dry-run` | Afficher le plan sans écrire | `false` |
| `--output, -o` | Format de sortie : `text` ou `json` | `text` |

La mémoire se choisit comme une composition, pas comme un backend : le profil
fixe les sept couches du Memory OS d'un coup (mémoire courte, sémantique,
sidecar structuré, graphes, mémoire chaude, visualisation).

`--backend auto` (le défaut, y compris avec `-y`) n'attache **jamais**
silencieusement le projet à un service mémoire trouvé sur la machine
(issue #496). L'étape Mémoire pose plutôt la composition la plus riche que
cette machine peut servir : `complet` (Weaviate + Neo4j + Redis) dès que Docker
répond, sinon `standard` (Qdrant embarqué, aucun service) avec un moteur
d'embedding local installé (fastembed/sentence-transformers), `lexical` sinon.
`-y` vaut consentement et démarre la pile `complet` ; `--memory-stack up` donne
le même consentement hors `-y`. Sans l'un ni l'autre, `complet` est configuré
sans être démarré et `grimoire doctor` le signale. Sur un
backend partagé choisi explicitement, la collection est nommée d'après le
projet (son slug) ; pour s'attacher à une collection déjà peuplée,
`--memory-collection` est requis. Voir
[Système de mémoire](../../explications/memoire.md).

`--lite`/`--profile lite` sont **dépréciés** : le profil léger n'existe plus
(l'expérience installée est toujours complète, le cœur s'adapte à chaque
tâche). Les deux drapeaux restent acceptés — un avertissement est affiché —
et se comportent désormais exactement comme le défaut.

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
exécuter. Détail et dégradations par hôte : [Surfaces hôtes](../hotes.md).

| Commande | Description |
|---|---|
| `grimoire host list` | Hôtes connus, surfaces natives de chacun, hôte détecté |
| `grimoire host surface` | Description host-neutre du projet |
| `grimoire host sync --host all` | Générer les surfaces (`--dry-run`, `--force`). Les règles `team` et `project` partent en `.cursor/rules/grimoire-rules*.mdc` (`globs:`) et `.github/instructions/grimoire-rules*.instructions.md` (`applyTo:`) ; le fichier d'une règle retirée est retiré |
| `grimoire host status` | Écart entre ce que le projet déclare et ce que l'hôte exécute |
| `grimoire host matrix` | Matrice règle × hôte calculée depuis ce que chaque émetteur écrit ; sort en 1 si une règle n'est ni appliquée ni déclarée dégradée |
| `grimoire host probe` | Vérifie sur le disque que les fichiers et hooks émis sont présents et à jour, sans rien écrire ; sort en 1 si un fichier manque, est périmé ou édité à la main |
| `grimoire host run <slug>` | Corps d'une commande, pour un hôte sans commandes natives |
| `grimoire host hook --host <h> --event <e>` | Point d'entrée des hooks générés (stdin vers stdout) |

`grimoire init`, `grimoire up --fix` et `grimoire standard init` synchronisent
automatiquement ; l'appel manuel sert après l'ajout ou la modification d'une
persona.

`host list` remonte aussi ce qu'un hôte ne sait pas exécuter, avec le substitut
retenu — dont l'ouverture d'une session dans un agent, qu'aucun hôte ne sait
faire : la persona d'entrée est alors remise à la boucle principale par le hook
`session_start`. Voir [Persona d'entrée](../hotes.md#persona-dentree).

## Cadrage produit

Cinq phases posées sous `_grimoire/cadrage/` : brief, brainstorm, compréhension,
exigences, cahier des charges. Le need `--needs project-discovery` (`grimoire
up`/`grimoire standard init`) scaffolde ce dossier par appel de code direct
(`cmd_up.py::_step_cadrage`), pas par une suggestion textuelle. Détail complet,
sévérités et intégration au gate : [docs/cadrage.md](../../guides/cadrage-produit.md).

| Commande | Description |
| --- | --- |
| `grimoire cadrage init` | Poser les cinq phases du cadrage |
| `grimoire cadrage status` | Progression, phase par phase |
| `grimoire cadrage check` | Gate de complétude : exigences et cahier des charges doivent être renseignés |
| `grimoire cadrage plan` | Une tâche du ledger par exigence `REQ-NNN`, ambiguïtés posées en questions (aperçu ; `--yes` écrit, `--check` couverture) |

Le même gate est aussi visible dans `grimoire standard verify`/`gate`/`score`/
`audit` : `error` sur les phases exigences/cahier-des-charges quand le need
`project-discovery` a été choisi, `warning`/`info` sinon (avis, jamais
bloquant).

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
