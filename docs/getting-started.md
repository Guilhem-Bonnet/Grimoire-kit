# Getting Started — BMAD Kit v3

> Ce guide vous accompagne de l'installation à votre premier projet BMAD.

## Prérequis

- **Python 3.12+**
- **pip** ou **uv** (recommandé)
- Un éditeur avec support Copilot / LLM (VS Code recommandé)

## Installation

```bash
# Via pip
pip install bmad-kit

# Avec le support MCP (Model Context Protocol)
pip install bmad-kit[mcp]

# Toutes les extensions (MCP + Qdrant + Ollama)
pip install bmad-kit[all]
```

Vérifiez l'installation :

```bash
bmad --version
```

## Créer un projet

### Nouveau projet

```bash
bmad init mon-projet --archetype web-app
cd mon-projet
```

Archétypes disponibles : `minimal`, `web-app`, `infra-ops`, `creative-studio`, `fix-loop`.

### Projet existant

```bash
cd votre-projet/
bmad init . --name "Mon Projet"
```

## Structure générée

```
mon-projet/
├── project-context.yaml          # Configuration centralisée
├── _bmad/
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
| `bmad init <path>` | Initialiser un projet |
| `bmad doctor` | Vérifier la santé du projet |
| `bmad status` | Afficher l'état du projet |
| `bmad add <agent>` | Ajouter un agent |
| `bmad remove <agent>` | Retirer un agent |
| `bmad validate` | Valider `project-context.yaml` |
| `bmad up` | Déployer les agents configurés |
| `bmad merge <source>` | Fusionner des fichiers BMAD |
| `bmad merge --undo` | Annuler le dernier merge |
| `bmad upgrade` | Migrer un projet v2 → v3 |
| `bmad registry list` | Lister les agents disponibles |
| `bmad registry search <q>` | Chercher un agent |

## Vérifier votre projet

```bash
bmad doctor
```

Sortie attendue :

```
✔ project-context.yaml found
✔ YAML valid
✔ _bmad directory exists
✔ At least one agent configured
✔ Memory directory exists
```

## Utiliser le SDK Python

```python
from bmad.core.config import BmadConfig
from bmad.core.project import BmadProject

config = BmadConfig.from_yaml("project-context.yaml")
project = BmadProject(config)

# Lister les agents
for agent in project.status().agents:
    print(f"{agent.id}: {agent.name}")
```

## Serveur MCP

Si vous avez installé `bmad-kit[mcp]` :

```bash
bmad-mcp
```

Configurez dans VS Code (`.vscode/mcp.json`) :

```json
{
  "servers": {
    "bmad": {
      "command": "bmad-mcp"
    }
  }
}
```

## Auto-complétion shell

BMAD Kit supporte l'auto-complétion pour Bash, Zsh et Fish :

```bash
# Installer l'auto-complétion pour votre shell
bmad --install-completion

# Afficher le script sans l'installer
bmad --show-completion
```

Après installation, redémarrez votre terminal. Tapez `bmad <TAB>` pour voir les commandes disponibles.

## Prochaines étapes

- [Référence YAML](bmad-yaml-reference.md) — schéma complet de `project-context.yaml`
- [Guide SDK](sdk-guide.md) — utilisation du SDK Python
- [Intégration MCP](mcp-integration.md) — serveur MCP pour Copilot
- [Migration v2 → v3](migration-v2-v3.md) — migrer un projet existant
- [Concepts](concepts.md) — architecture et principes
