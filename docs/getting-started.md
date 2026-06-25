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

### Nouveau projet

```bash
grimoire init mon-projet --archetype web-app
cd mon-projet
```

Archétypes disponibles : `minimal`, `web-app`, `infra-ops`, `creative-studio`, `fix-loop`.

### Projet existant

```bash
cd votre-projet/
grimoire init . --name "Mon Projet"
```

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
| `grimoire init <path>` | Initialiser un projet |
| `grimoire setup` | Synchroniser la config utilisateur |
| `grimoire setup --check` | Auditer la synchronisation (CI-friendly) |
| `grimoire doctor` | Vérifier la santé du projet |
| `grimoire status` | Afficher l'état du projet |
| `grimoire add <agent>` | Ajouter un agent |
| `grimoire remove <agent>` | Retirer un agent |
| `grimoire validate` | Valider `project-context.yaml` |
| `grimoire up` | Déployer les agents configurés |
| `grimoire merge <source>` | Fusionner des fichiers Grimoire |
| `grimoire merge --undo` | Annuler le dernier merge |
| `grimoire upgrade` | Migrer un projet v2 → v3 |
| `grimoire registry list` | Lister les agents disponibles |
| `grimoire registry search <q>` | Chercher un agent |

## Configurer votre identité

Après `grimoire init`, configurez votre nom et votre langue. La commande `setup` propage ces valeurs dans tous les fichiers de configuration du projet (configs BMAD, instructions Copilot).

```bash
# Synchroniser depuis project-context.yaml
grimoire setup

# Ou spécifier directement
grimoire setup --user "Alice" --lang "English" --skill-level intermediate

# Vérifier la synchronisation (utile en CI)
grimoire setup --check
```

**Source de vérité** : `project-context.yaml` (section `user`). La commande `setup` propage les valeurs vers :

- `_bmad/*/config.yaml` — configuration des modules BMAD
- `.github/copilot-instructions.md` — instructions injectées dans Copilot Chat

## Vérifier votre projet

```bash
grimoire doctor
```

Sortie attendue :

```
✔ project-context.yaml found
✔ YAML valid
✔ _grimoire directory exists
✔ At least one agent configured
✔ Memory directory exists
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
