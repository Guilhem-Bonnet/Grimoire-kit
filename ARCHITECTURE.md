# Architecture — Grimoire Kit

> Version 3.0.0 — Composable AI agent platform.

## Vue d'ensemble

Grimoire Kit est une plate-forme de composition d'agents IA destinée à orchestrer personas, mémoire, flux de travail et automatisation qualité pour n'importe quel projet logiciel. Le SDK est distribué sous forme de paquet Python (`pip install grimoire-kit`) et s'accompagne d'un shell installer (`grimoire.sh`) pour les opérations framework côté projet.

```
┌─────────────────────────────────────────────────────────────┐
│                      Projet utilisateur                      │
│                                                              │
│  project-context.yaml    _grimoire/    _grimoire-output/     │
│                          ├─ _memory/                         │
│                          ├─ agents/                          │
│                          └─ workflows/                       │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
     ┌─────────▼──────────┐        ┌──────────▼──────────┐
     │   SDK Python        │        │  Shell Framework     │
     │  (grimoire-kit)     │        │  (grimoire.sh)       │
     │                     │        │                      │
     │  pip install        │        │  ~100 outils Python  │
     │  grimoire-kit       │        │  hooks, lifecycle     │
     └─────────────────────┘        └──────────────────────┘
```

---

## Distribution en 3 couches

### 1. SDK Python (`src/grimoire/`)

Paquet installable via pip. Fournit les primitives fondamentales :

| Module | Responsabilité |
|---|---|
| `core/` | Configuration (`GrimoireConfig`), projet (`GrimoireProject`), résolution de chemins (`PathResolver`), validation, scanning de stack, merge engine |
| `cli/` | CLI Typer — `grimoire init`, `grimoire doctor`, `grimoire status`, `grimoire merge`, `grimoire upgrade` |
| `mcp/` | Serveur MCP (Model Context Protocol) — expose les outils aux LLM (Claude, Copilot, etc.) |
| `memory/` | API mémoire unifiée (`MemoryManager`) + backends pluggables |
| `registry/` | Catalogue d'archétypes et agents bundlés |
| `tools/` | 7 outils importables : HarmonyCheck, ContextGuard, ContextRouter, MemoryLint, PreflightCheck, AgentForge, Stigmergy |
| `archetypes/` | Archétypes bundlés dans le wheel (via `importlib.resources`) |

**Points d'entrée :**
- `grimoire` → `grimoire.cli.app:cli`
- `grimoire-mcp` → `grimoire.mcp.server:main`

### 2. Shell Framework (`framework/`)

~100 outils Python exécutables en standalone, organisés autour du cycle de vie projet :

```
framework/
├── tools/           # ~100 scripts Python autonomes
├── hooks/           # Hooks lifecycle (pré/post-session)
├── workflows/       # Moteurs de workflow
├── teams/           # Orchestration multi-agents
├── mcp/             # Config MCP additionnelle
├── memory/          # Outils mémoire avancés
├── sessions/        # Gestion de sessions
├── registry/        # Registre runtime
├── copilot-extension/ # Extension Copilot
└── prompt-templates/  # Templates agents
```

Le point d'entrée shell est `grimoire.sh` qui route vers les sous-commandes (`doctor`, `status`, `tools`, `lifecycle`, `integrity`, etc.).

### 3. Archétypes (`archetypes/`)

Blueprints pré-configurés pour démarrer un projet. Chaque archétype contient un `archetype.dna.yaml`, des agents, et éventuellement un `shared-context.tpl.md` :

| Archétype | Cas d'usage |
|---|---|
| `minimal` | Démarrage rapide, 1 agent personnalisable |
| `web-app` | Application web full-stack |
| `creative-studio` | Création de contenu, branding |
| `fix-loop` | Boucle de correction automatisée |
| `infra-ops` | Infrastructure, DevOps, SRE |
| `meta` | Méta-agents (optimiseur, concierge, mémoire) |
| `platform-engineering` | Ingénierie plate-forme |
| `stack` | Multi-stack générique |
| `features` | Fonctionnalités additionnelles (ex: vector-memory) |

Les archétypes sont **bundlés dans le wheel** Python et accessibles via `grimoire.archetypes.bundled_path()`. En développement local, le dossier `archetypes/` du repo est utilisé en priorité.

---

## Couche Core — détail

### Configuration (`core/config.py`)

Charge `project-context.yaml` vers des dataclasses typées et immuables (`frozen=True, slots=True`) :

```
GrimoireConfig
├── ProjectConfig     # name, type, stack, repos
├── UserConfig        # name, language, skill_level
├── MemoryConfig      # backend, qdrant_url, ollama_url, collection
└── AgentsConfig      # archetype, custom_agents
```

Sections inconnues préservées dans `extra` pour extensibilité.

### Projet (`core/project.py`)

Point d'entrée unique pour interagir avec un projet Grimoire :

```python
project = GrimoireProject(Path("."))
project.config       # GrimoireConfig
project.status()     # ProjectStatus
project.agents()     # list[AgentInfo]
project.resolver     # PathResolver
```

### Résolution de chemins (`core/resolver.py`)

Résout `{project-root}`, `{user_name}` et autres variables dans les templates et chemins.

### Validation (`core/validator.py`)

Validation structurelle de `project-context.yaml` : types, contraintes, valeurs connues (archétypes, backends, skill levels).

### Merge Engine (`core/merge.py`)

Merge non-destructif : analyse source ↔ target, détecte les conflits, exécute le plan avec backup.

### Stack Scanner (`core/scanner.py`)

Détection automatique du stack technologique par marqueurs fichiers (pyproject.toml → Python, Cargo.toml → Rust, etc.).

---

## Mémoire

Architecture backend pluggable derrière `MemoryManager` :

```
MemoryManager.from_config(cfg, project_root)
     │
     ├── LocalMemoryBackend     # JSON file, scoring BM25-like
     ├── QdrantMemoryBackend    # Qdrant + sentence-transformers
     └── OllamaMemoryBackend   # Ollama embeddings
```

Chaque backend implémente 5 méthodes : `store`, `recall`, `search`, `consolidate`, `health_check`.

Sélection automatique (`backend: auto`) : Ollama si URL configurée → Qdrant si URL → Local sinon.

---

## Outils SDK (`tools/`)

Tous héritent de `GrimoireTool` et exposent une méthode `run()` typée :

| Outil | Fonction |
|---|---|
| **HarmonyCheck** | Détecte les dissonances architecturales (fichiers longs, nommage, etc.) |
| **ContextGuard** | Analyse le budget tokens consommé par un agent au démarrage |
| **ContextRouter** | Planifie le chargement intelligent de contexte (P0→P4) |
| **MemoryLint** | Vérifie la santé du système mémoire |
| **PreflightCheck** | Validation pré-session complète |
| **AgentForge** | Génère des scaffolds d'agents à partir de descriptions |
| **Stigmergy** | Tableau de coordination inter-agents par phéromones |

Tous sont exposés via le serveur MCP et la CLI.

---

## Serveur MCP

Expose les outils Grimoire aux LLM via le Model Context Protocol :

```json
{
  "mcpServers": {
    "grimoire": {
      "command": "grimoire-mcp",
      "cwd": "/path/to/project"
    }
  }
}
```

Fonctions exposées : `grimoire_project_context`, `grimoire_harmony_check`, `grimoire_context_guard`, `grimoire_context_router`, `grimoire_memory_lint`, `grimoire_preflight_check`, `grimoire_agent_forge`, `grimoire_stigmergy_board`, etc.

---

## Registre d'agents

```
AgentRegistry (agents.py)
     │
     ├── Indexe archetypes/*.dna.yaml
     ├── Parse agents Markdown → metadata
     └── Fallback: local → bundled (via importlib.resources)

LocalRegistry (local.py)
     │
     ├── Wraps AgentRegistry
     ├── list_agents() → RegistryItem[]
     └── search(query) → résultats filtrés
```

---

## Flux de données typique

```
1. Utilisateur lance `grimoire init --archetype web-app`
   → Crée project-context.yaml + _grimoire/ + _grimoire-output/

2. Agent IA charge le contexte via MCP
   → grimoire_project_context() → JSON config
   → grimoire_context_router() → plan de chargement optimisé

3. Pendant la session, l'agent utilise les outils
   → grimoire_harmony_check() → désaccords architecturaux
   → grimoire_stigmergy_board() → signaux inter-agents
   → grimoire_memory_lint() → santé mémoire

4. En fin de session, hooks lifecycle
   → grimoire.sh lifecycle post → consolidation mémoire
```

---

## Build & CI

| Composant | Technologie |
|---|---|
| Build backend | Hatchling |
| Linter | Ruff (`E, F, I, UP, B, N`) |
| Type checker | mypy `--strict` |
| Tests | pytest (~700 tests) |
| CI | GitHub Actions (6 workflows) |
| Publication | PyPI via Trusted Publisher |

**Workflows CI :**
- `ci-sdk.yml` : lint + mypy + tests Python 3.12/3.13
- `ci-validate.yml` : bash, shellcheck, YAML, DNA, doctor, context-guard, tests framework
- `publish.yml` : publication PyPI sur tag `v*`
- `release.yml` : création de release GitHub
- `codeql.yml` : analyse de sécurité
- `dependabot.yml` : mises à jour automatiques des dépendances

---

## Décisions architecturales clés

1. **Pas de multi-LLM dans le SDK** — Le SDK est agnostique au fournisseur LLM. L'interaction se fait via MCP ou Copilot instructions. Voir [ADR-001](docs/adr-001-no-multi-llm.md).

2. **Dataclasses immuables** — Toutes les structures de données sont `frozen=True, slots=True` pour la sûreté et la performance.

3. **Écritures atomiques** — Le `MemoryManager` utilise des écritures atomiques (write → rename) pour éviter la corruption.

4. **Fallback bundled ↔ local** — L'`AgentRegistry` utilise les archétypes locaux s'ils existent, sinon ceux bundlés dans le wheel.

5. **Backend mémoire pluggable** — Interface abstraite `MemoryBackend` avec sélection automatique selon la configuration.

6. **Séparation SDK / Framework** — Le SDK (pip) contient les primitives testées et typées. Le framework (shell) contient les outils avancés exécutés en contexte projet.

---

## Arbre des dépendances

```
grimoire-kit
├── ruamel.yaml >=0.18    # Parse YAML avec comments
├── typer >=0.12          # CLI
└── rich >=13.0           # Formatage console

[extras]
├── mcp >=1.0             # Serveur MCP
├── qdrant-client >=1.9   # Backend vecteur
├── sentence-transformers >=3.0  # Embeddings
└── ollama >=0.3          # Backend Ollama
```

---

## Structure du projet utilisateur

Après `grimoire init`, un projet contient :

```
mon-projet/
├── project-context.yaml      # Configuration centrale
├── _grimoire/
│   ├── _memory/              # Données mémoire
│   │   ├── shared-context.md
│   │   └── *.json
│   ├── _config/
│   │   ├── agent-manifest.csv
│   │   └── workflow-manifest.csv
│   ├── agents/               # Agents déployés (.md)
│   └── workflows/            # Workflows actifs
└── _grimoire-output/
    ├── planning-artifacts/
    └── implementation-artifacts/
```
