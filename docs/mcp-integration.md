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
| `grimoire_standard_verify` / `_audit` / `_score` / `_gate` | Le standard agentique : vérifier, auditer, scorer, opposer les gates |
| `grimoire_host_status` / `grimoire_skill` / `grimoire_command` | Les surfaces hôtes, pour un client sans émetteur |
| `task_list_ready` | Les tâches qu'un agent peut réclamer maintenant |
| `task_show` | Une tâche : état, acceptation, claim, et ce que chaque prochain pas exigera |
| `task_claim` | Réclamer une tâche prête (`ready → claimed`) |
| `task_update` | Déplacer (`move`), bloquer (`block`) ou fermer (`close`) une tâche |
| `task_context` | Sur quelle tâche la session est, et son context bundle |

### Les tâches : un outil, pas du texte dans un prompt

Les cinq outils `task_*` appellent le même service que `grimoire task`
(`grimoire.missions.service.TaskService`), donc le même gate de preuve
(`_grimoire/standard/evidence-gates.yaml`). Une transition que le CLI refuse,
MCP la refuse pour la même raison, et le refus est structuré :

```json
{
  "blocked": true,
  "task_id": "GAO-exposer-les-001",
  "transition": "ready_to_in_progress",
  "strictness": "hard_fail",
  "refusals": [
    {
      "evidence": "context_bundle",
      "reason": "context bundle absent",
      "remedy": "attendu : _grimoire-output/context/GAO-exposer-les-001/context-bundle.yaml"
    }
  ]
}
```

Rien n'est écrit au ledger sur un refus. Après une transition acceptée, le board
`_grimoire/standard/task-board.yaml` est reprojeté, et le hook `SessionStart`
de la session suivante nomme la tâche réclamée : `task_context` sans argument
rend `{"task_id": ..., "resolved_from": "ledger_claim"}`. Pour qu'un agent ne
se voie attribuer que ses propres claims, poser `GRIMOIRE_ACTOR` à la valeur
passée en `actor` à `task_claim` (règle complète dans la
[référence CLI](cli-reference.md#quelle-tâche-la-session-porte)).

Le parcours nominal d'un agent : `task_list_ready` → `task_context(task_id)`
(produit le bundle que le gate exige) → `task_claim` → `task_update(move,
running)` → travail et preuves → `task_update(move, needs_verification)` →
`task_update(close)`.

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
