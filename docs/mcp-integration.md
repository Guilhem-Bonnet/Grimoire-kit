# Intégration MCP — BMAD Kit v3

> Exposer les outils BMAD via le Model Context Protocol pour Copilot, Claude Desktop, et tout client MCP.

## Prérequis

```bash
pip install bmad-kit[mcp]
```

## Démarrage rapide

```bash
# Lancer le serveur MCP
bmad-mcp

# Ou directement via Python
python -m bmad.mcp.server
```

## Configuration VS Code

Créez `.vscode/mcp.json` à la racine de votre projet :

```json
{
  "servers": {
    "bmad": {
      "command": "bmad-mcp"
    }
  }
}
```

Ou avec un chemin Python explicite :

```json
{
  "servers": {
    "bmad": {
      "command": "python",
      "args": ["-m", "bmad.mcp.server"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

## Configuration Claude Desktop

Dans `claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "bmad": {
      "command": "python",
      "args": ["-m", "bmad.mcp.server"],
      "cwd": "/chemin/vers/projet"
    }
  }
}
```

## Outils exposés

| Outil | Description |
|-------|-------------|
| `bmad_project_context` | Retourne le contexte projet complet (JSON) |
| `bmad_status` | État du projet (agents, mémoire, santé) |
| `bmad_agent_list` | Liste des agents installés |
| `bmad_harmony_check` | Exécute un Harmony Check et retourne le rapport |
| `bmad_config` | Configuration brute du projet |
| `bmad_memory_store` | Stocker un texte en mémoire sémantique |
| `bmad_memory_search` | Recherche sémantique dans la mémoire |
| `bmad_add_agent` | Ajouter un agent au projet |

## Exemples d'utilisation

Dans Copilot Chat ou Claude, les outils sont appelés automatiquement quand le LLM détecte le besoin :

**"Quel est le stack de ce projet ?"**
→ L'agent appelle `bmad_project_context` et extrait la liste du stack.

**"Ajoute l'agent architect au projet"**
→ L'agent appelle `bmad_add_agent("architect")`.

**"Y a-t-il des problèmes dans le projet ?"**
→ L'agent appelle `bmad_harmony_check` et résume le rapport.

**"Mémorise que nous avons choisi PostgreSQL"**
→ L'agent appelle `bmad_memory_store("Décision: PostgreSQL comme base de données")`.

## Architecture

```
┌─────────────────────┐
│  LLM (Copilot/Claude) │
└──────────┬──────────┘
           │ MCP Protocol (stdio)
┌──────────▼──────────┐
│  bmad-mcp server    │  ← FastMCP
│  (bmad.mcp.server)  │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  BMAD SDK           │
│  config / project   │
│  tools / memory     │
└─────────────────────┘
```

Le serveur MCP est un pont entre le protocole MCP (stdin/stdout JSON-RPC) et le SDK Python BMAD.

## Voir aussi

- [Guide SDK](sdk-guide.md)
- [Référence YAML](bmad-yaml-reference.md)
- [Getting Started](getting-started.md)
