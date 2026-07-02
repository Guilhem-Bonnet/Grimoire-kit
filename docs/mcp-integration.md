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
| ----- | ----------- |
| `grimoire_project_context` | Retourne le contexte projet complet (JSON) |
| `grimoire_status` | État du projet (agents, mémoire, santé) |
| `grimoire_agent_list` | Liste des agents installés |
| `grimoire_harmony_check` | Exécute un Harmony Check et retourne le rapport |
| `grimoire_config` | Configuration brute du projet |
| `grimoire_memory_store` | Stocker un texte en mémoire sémantique |
| `grimoire_memory_search` | Recherche sémantique dans la mémoire |
| `grimoire_add_agent` | Ajouter un agent au projet |
| `grimoire_preflight_check` | Vérifie la structure, les outils, Git et l'état mémoire du projet |
| `grimoire_quick_check` | Lance la validation rapide du kit (`ruff` + tests modifiés) |
| `grimoire_memory_lint` | Analyse les contradictions, doublons et incohérences mémoire |
| `grimoire_validate_skills` | Valide les skills `.github/skills` avec le validateur déterministe |
| `grimoire_assets_generate_complete_baseline` | Génère le baseline 2D curé et met à jour l'index assets |
| `grimoire_assets_generate_character_action_variants` | Génère les feuilles d'actions personnages et met à jour l'index |
| `grimoire_assets_extract_task_icons` | Extrait les icônes tâches depuis la planche source |
| `grimoire_assets_publish_to_observatory` | Publie les assets validés vers `_grimoire-output/assets` en `dry_run` par défaut |

## Exemples d'utilisation

Dans Copilot Chat ou Claude, les outils sont appelés automatiquement quand le LLM détecte le besoin :

**"Quel est le stack de ce projet ?"**
→ L'agent appelle `grimoire_project_context` et extrait la liste du stack.

**"Ajoute l'agent architect au projet"**
→ L'agent appelle `grimoire_add_agent("architect")`.

**"Y a-t-il des problèmes dans le projet ?"**
→ L'agent appelle `grimoire_harmony_check` et résume le rapport.

**"Fais un preflight avant de démarrer"**
→ L'agent appelle `grimoire_preflight_check` puis décide s'il peut continuer sans blocage.

**"Valide les skills du projet"**
→ L'agent appelle `grimoire_validate_skills` et s'appuie sur le rapport JSON structuré.

**"Prépare les assets 2D et publie-les vers l'observatory"**
→ L'agent enchaîne les outils `grimoire_assets_generate_complete_baseline`, `grimoire_assets_generate_character_action_variants`, `grimoire_assets_extract_task_icons`, puis `grimoire_assets_publish_to_observatory`.

**"Mémorise que nous avons choisi PostgreSQL"**
→ L'agent appelle `grimoire_memory_store("Décision: PostgreSQL comme base de données")`.

## Outils projet et assets

Le serveur MCP expose maintenant des wrappers orientés productivité pour éviter aux agents de relancer manuellement des scripts shell ou Python du dépôt.

- Les outils `grimoire_preflight_check`, `grimoire_quick_check`, `grimoire_memory_lint` et `grimoire_validate_skills` ciblent la validation et l'hygiène projet.
- Les outils `grimoire_assets_*` encapsulent le pipeline 2D du workspace avec résolution automatique de `grimoire-game-assets`.
- `grimoire_assets_publish_to_observatory` reste fail-closed et démarre en `dry_run` par défaut.

## Architecture

```text
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
- [Guide de démarrage](getting-started.md)
