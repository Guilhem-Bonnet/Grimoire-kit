# Intégration MCP — Grimoire Kit v3

> Exposer les outils Grimoire via le Model Context Protocol pour Copilot, Claude Desktop, et tout client MCP.

## Prérequis

```bash
pip install grimoire-kit[mcp]
```

## Démarrage rapide

```bash
# Lancer le serveur MCP
grimoire-mcp

# Ou directement via Python
python -m grimoire.mcp.server
```

## Configuration VS Code

Créez `.vscode/mcp.json` à la racine de votre projet :

```json
{
  "servers": {
    "grimoire": {
      "command": "grimoire-mcp"
    }
  }
}
```

Ou avec un chemin Python explicite :

```json
{
  "servers": {
    "grimoire": {
      "command": "python",
      "args": ["-m", "grimoire.mcp.server"],
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
    "grimoire": {
      "command": "python",
      "args": ["-m", "grimoire.mcp.server"],
      "cwd": "/chemin/vers/projet"
    }
  }
}
```

## Outils exposés

| Outil | Description |
|-------|-------------|
| `grimoire_project_context` | Retourne le contexte projet complet (JSON) |
| `grimoire_status` | État du projet (agents, mémoire, santé) |
| `grimoire_agent_list` | Liste des agents installés |
| `grimoire_harmony_check` | Exécute un Harmony Check et retourne le rapport |
| `grimoire_config` | Configuration brute du projet |
| `grimoire_memory_store` | Stocker un texte en mémoire sémantique |
| `grimoire_memory_search` | Recherche sémantique dans la mémoire |
| `grimoire_add_agent` | Ajouter un agent au projet |

## Exemples d'utilisation

Dans Copilot Chat ou Claude, les outils sont appelés automatiquement quand le LLM détecte le besoin :

**"Quel est le stack de ce projet ?"**
→ L'agent appelle `grimoire_project_context` et extrait la liste du stack.

**"Ajoute l'agent architect au projet"**
→ L'agent appelle `grimoire_add_agent("architect")`.

**"Y a-t-il des problèmes dans le projet ?"**
→ L'agent appelle `grimoire_harmony_check` et résume le rapport.

**"Mémorise que nous avons choisi PostgreSQL"**
→ L'agent appelle `grimoire_memory_store("Décision: PostgreSQL comme base de données")`.

## Architecture

```
┌─────────────────────┐
│  LLM (Copilot/Claude) │
└──────────┬──────────┘
           │ MCP Protocol (stdio)
┌──────────▼──────────┐
│  grimoire-mcp server    │  ← FastMCP
│  (grimoire.mcp.server)  │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  Grimoire SDK           │
│  config / project   │
│  tools / memory     │
└─────────────────────┘
```

Le serveur MCP est un pont entre le protocole MCP (stdin/stdout JSON-RPC) et le SDK Python Grimoire.

## Voir aussi

- [Guide SDK](sdk-guide.md)
- [Référence YAML](grimoire-yaml-reference.md)
- [Getting Started](getting-started.md)
