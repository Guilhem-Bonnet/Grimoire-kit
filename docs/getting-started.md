# Getting Started — Grimoire Kit v3

> Ce guide vous accompagne de l'installation à votre premier projet Grimoire.

## Prérequis

- **Python 3.12+**
- **pip**, **pipx** (recommandé) ou **uv**
- Un éditeur avec support Copilot / LLM (VS Code recommandé)

## Installation

```bash
# Via pipx (recommandé — isolation automatique)
pipx install grimoire-kit

# Via pip dans un venv
python3 -m venv .venv && source .venv/bin/activate
pip install grimoire-kit

# Avec le support MCP (Model Context Protocol)
pip install grimoire-kit[mcp]

# Toutes les extensions (MCP + Qdrant + Ollama)
pip install grimoire-kit[all]
```

> **Note** : Sur Ubuntu/Debian, `pip install` en dehors d'un venv est bloqué (PEP 668).
> Utilisez `pipx` ou un venv.

Vérifiez l'installation :

```bash
grimoire --version
```

## Créer un projet

### En une commande (recommandé)

`grimoire up` enchaîne tout le parcours : init (mode express), propagation de
l'identité, standard agentique gouverné, puis diagnostic de santé. La commande
est idempotente — relancée sur un projet déjà en place, elle ne fait que
combler les manques.

```bash
# Nouveau projet
grimoire up mon-projet --archetype web-app --name "Mon Projet" --user "Alice"

# Projet existant
cd votre-projet/
grimoire up .

# Wizard interactif complet, ou sans le standard gouverné
grimoire up . --interactive
grimoire up . --no-standard
```

Archétypes disponibles : `minimal`, `web-app`, `infra-ops`, `platform-engineering`,
`agentic-standard`, `creative-studio`, `fix-loop`.

### Étape par étape (ce que `up` enchaîne)

```bash
grimoire init mon-projet --archetype web-app   # scaffold seul
cd mon-projet
grimoire setup                                 # propagation identité
grimoire standard init . --needs solo-prototyping
grimoire doctor
```

### Détecter vos projets existants

Pour recenser les projets d'une machine et les enrôler dans le cockpit :

```bash
grimoire cockpit scan ~/dev            # crawl récursif (profondeur 4 par défaut)
grimoire cockpit scan ~/dev --yes      # enrôle tous les projets Grimoire détectés
```

Les dépôts git non initialisés sont listés avec la suggestion `grimoire up <path>`.

## Structure générée

```
mon-projet/
├── project-context.yaml          # Configuration centralisée
├── _grimoire/
│   ├── _config/
│   │   ├── agents/               # Références agents installés
│   │   ├── manifest.yaml         # Manifeste du projet
│   │   └── custom/               # Surcharges locales
│   ├── _memory/
│   │   ├── shared-context.md     # Contexte partagé
│   │   ├── decisions-log.yaml    # Journal des décisions
│   │   └── learnings.yaml        # Apprentissages
│   └── core/
│       ├── agents/               # Agents déployés
│       └── workflows/            # Workflows actifs
└── .github/
    └── copilot-instructions.md   # Instructions VS Code Copilot
```

## Commandes CLI

| Commande | Description |
|----------|-------------|
| `grimoire up [path]` | Parcours complet : init + setup + standard + doctor (idempotent) |
| `grimoire init <path>` | Initialiser un projet (scaffold seul) |
| `grimoire setup` | Synchroniser la config utilisateur |
| `grimoire setup --check` | Auditer la synchronisation (CI-friendly) |
| `grimoire doctor [--fix]` | Vérifier la santé du projet et de l'environnement ; `--fix` régénère wrappers et `.mcp.json` manquants |
| `grimoire cockpit scan <racine>` | Détecter et enrôler les projets existants |
| `grimoire blueprint <cmd>` | Blueprints : `new`, `validate`, `compile` |
| `grimoire status` | Afficher l'état du projet |
| `grimoire add <agent>` | Ajouter un agent |
| `grimoire remove <agent>` | Retirer un agent |
| `grimoire validate` | Valider `project-context.yaml` |
| `grimoire check` | Lint + validate + doctor en une passe |
| `grimoire standard <cmd>` | Standard agentique gouverné (`needs`, `init`, `verify`, `audit`, `score`, `gate`) |
| `grimoire merge <source>` | Fusionner des fichiers Grimoire |
| `grimoire merge --undo` | Annuler le dernier merge |
| `grimoire upgrade` | Migrer un projet v2 → v3 |
| `grimoire registry list` | Lister les agents disponibles |
| `grimoire registry search <q>` | Chercher un agent |

## Configurer votre identité

Après `grimoire init`, configurez votre nom et votre langue. La commande `setup` propage ces valeurs dans tous les fichiers de configuration du projet (instructions Copilot).

```bash
# Synchroniser depuis project-context.yaml
grimoire setup

# Ou spécifier directement
grimoire setup --user "Alice" --lang "English" --skill-level intermediate

# Vérifier la synchronisation (utile en CI)
grimoire setup --check
```

**Source de vérité** : `project-context.yaml` (section `user`). La commande `setup` propage les valeurs vers :

- `.github/copilot-instructions.md` — instructions injectées dans Copilot Chat

## Vérifier votre projet

```bash
grimoire doctor
```

Sortie attendue (extrait) :

```text
  OK  project-context.yaml found
  OK  Config valid — project: Demo
  OK  _grimoire/ present
  OK  Archetype configured: minimal
  OK  7 VS Code agent wrapper(s) in .github/agents/
  OK  uv available (/home/user/.local/bin/uv)
  OK  docker daemon reachable (server 29.6.1)
  OK  Qdrant reachable at http://localhost:6333
  OK  Ollama reachable at http://localhost:11434
  OK  .mcp.json server 'grimoire' resolves (grimoire-mcp)

17/17 checks passed
```

Les checks d'environnement (uv, docker, Qdrant, Ollama) sont optionnels : un
avertissement affiche la commande de remédiation exacte sans bloquer. Une
référence cassée dans `.mcp.json` est en revanche une erreur.

Pour réparer un projet (wrappers agents ou `.mcp.json` manquants) :

```bash
grimoire doctor . --fix
```

## Adopter le standard agentique gouverné

Au-delà du scaffold, Grimoire fournit un **standard agentique** : un besoin projet mappe sur
un profil (`starter → controlled → orchestrated → governed → production`) qui active des
**patterns gouvernés vérifiables** (36 au catalogue).

```bash
# Choisir par besoin (commencer petit)
grimoire standard needs
grimoire standard init . --needs solo-prototyping

# Vérifier / auditer / scorer / gater la conformité (fail-closed)
grimoire standard verify
grimoire standard audit
grimoire standard score
grimoire standard gate
```

Référence des contrôles : [Contrôles gouvernés](governed-controls.md) · intégration :
[Standard agentique](agentic-standard-integration.md) · installation par besoins :
[Installation par besoins](agentic-standard-install-by-needs.md).

## Portabilité multi-assistant

`grimoire init` génère des entrypoints portables — `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
`.cursorrules` (pointant vers `.github/copilot-instructions.md`) et un `.mcp.json` OS-neutre —
pour que le projet fonctionne avec Copilot, Claude Code, Codex, Gemini CLI et Cursor sans
configuration manuelle.

## Créer un blueprint

Un blueprint décrit un pipeline d'agents (nodes, edges, contrats) compilable en
mission pack. Le CLI couvre tout le cycle :

```bash
grimoire blueprint new mon-pipeline            # scaffold un .blueprint.json valide
grimoire blueprint validate mon-pipeline.blueprint.json
grimoire blueprint compile mon-pipeline.blueprint.json
```

- Schéma de référence : `schemas/blueprint-v1.schema.json`
- Exemples prêts à l'emploi : `registry/blueprints/` (`minimal`, `web-pipeline`)
- L'atelier visuel reste disponible via `grimoire serve` (le cockpit
  multi-projets, lui, est servi par `grimoire cockpit`)

Chaque erreur de validation indique le chemin JSON fautif, la valeur attendue et
la remédiation ; une extension manquante à la compilation affiche la commande
`grimoire ext add` exacte.

## Utiliser le SDK Python

```python
from grimoire.core.config import GrimoireConfig
from grimoire.core.project import GrimoireProject

config = GrimoireConfig.from_yaml("project-context.yaml")
project = GrimoireProject(config)

# Lister les agents
for agent in project.status().agents:
    print(f"{agent.id}: {agent.name}")
```

## Serveur MCP

Si vous avez installé `grimoire-kit[mcp]` :

```bash
grimoire-mcp
```

Configurez dans VS Code (`.vscode/mcp.json`) :

```json
{
  "servers": {
    "grimoire": {
      "command": "grimoire-mcp"
    }
  }
}
```

## Auto-complétion shell

Grimoire Kit supporte l'auto-complétion pour Bash, Zsh et Fish :

```bash
# Installer l'auto-complétion pour votre shell
grimoire --install-completion

# Afficher le script sans l'installer
grimoire --show-completion
```

Après installation, redémarrez votre terminal. Tapez `grimoire <TAB>` pour voir les commandes disponibles.

## Prochaines étapes

- [Référence YAML](grimoire-yaml-reference.md) — schéma complet de `project-context.yaml`
- [Guide SDK](sdk-guide.md) — utilisation du SDK Python
- [Intégration MCP](mcp-integration.md) — serveur MCP pour Copilot
- [Migration v2 → v3](migration-v2-v3.md) — migrer un projet existant
- [Concepts](concepts.md) — architecture et principes
