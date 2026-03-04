# BMAD Copilot Extension — `@bmad` — BM-23

> Intégration native de BMAD dans GitHub Copilot Chat via l'API GitHub Copilot Extensions.

## Concept

L'extension `@bmad` permet d'activer les agents et workflows BMAD directement depuis l'interface Copilot Chat dans VS Code, sans quitter l'éditeur, sans copier-coller de contexte.

```
@bmad /activate atlas
@bmad /status
@bmad /run workflow boomerang-feature.yaml
@bmad /session branch feature-auth
```

## Architecture

```
VS Code Copilot Chat
        │
        │ @bmad /command
        ▼
GitHub Copilot Extensions API
        │
        │ Webhook POST /api/copilot-extension
        ▼
BMAD MCP Server (local ou distant)
        │
        ├─ get_project_context()
        ├─ get_agent_memory()
        ├─ run_completion_contract()
        └─ spawn_subagent_task()
```

## Commandes disponibles

| Commande | Description |
|---------|-------------|
| `@bmad /activate <agent>` | Activer un agent spécifique avec son contexte projet |
| `@bmad /status` | État du projet courant (workflow en cours, session active) |
| `@bmad /run workflow <file>` | Démarrer un workflow YAML via le workflow engine |
| `@bmad /session branch <name>` | Créer une nouvelle branche de session |
| `@bmad /session list` | Lister les branches de session actives |
| `@bmad /memory show` | Afficher le contenu de la mémoire partagée |
| `@bmad /memory update` | Déclencher une mise à jour mémoire via Atlas |
| `@bmad /team <team-id> start` | Démarrer un pipeline Team of Teams |
| `@bmad /repo-map` | Générer/afficher la Repo Map |
| `@bmad /think <question>` | Activer le mode Extended Thinking [THINK] |
| `@bmad /failure-museum` | Consulter le Failure Museum du projet |
| `@bmad /install archetype <id>` | Installer un archétype dans le projet courant |
| `@bmad /help` | Afficher l'aide complète |

## Structure du projet d'extension

```
bmad-copilot-extension/
├── package.json
├── server.ts               # Point d'entrée Express
├── src/
│   ├── copilot-handler.ts  # Traitement des messages Copilot
│   ├── commands/
│   │   ├── activate.ts
│   │   ├── status.ts
│   │   ├── run-workflow.ts
│   │   ├── session.ts
│   │   ├── memory.ts
│   │   ├── team.ts
│   │   └── repo-map.ts
│   ├── mcp-client.ts       # Client vers BMAD MCP Server
│   └── context-builder.ts  # Construction du contexte projet
├── .github/
│   └── copilot-extension/
│       └── agent.yml       # Manifest de l'extension
└── README.md
```

## Manifest de l'extension (`agent.yml`)

```yaml
name: BMAD
description: "BMAD Agent Framework — activate agents, run workflows, manage sessions"
homepage: https://github.com/grimoire-kit
capabilities:
  - chat
  - context
  - tools
tools:
  - name: activate_agent
    description: "Activate a BMAD agent with full project context"
    parameters:
      - name: agent_id
        type: string
        required: true
  - name: get_project_status
    description: "Get current workflow and session status"
  - name: run_workflow
    description: "Execute a BMAD YAML workflow"
    parameters:
      - name: workflow_file
        type: string
        required: true
  - name: query_memory
    description: "Query the BMAD memory system"
    parameters:
      - name: query
        type: string
        required: true
```

## Implémentation du handler (`copilot-handler.ts`)

```typescript
import { CopilotExtensionsAPI } from "@github/copilot-extensions";
import { BMadMCPClient } from "./mcp-client";

export async function handleCopilotMessage(
  message: string,
  context: CopilotContext
) {
  const mcp = new BMadMCPClient({ transport: "stdio" });

  // Parser la commande
  const [cmd, ...args] = parseCommand(message);

  switch (cmd) {
    case "/activate": {
      const agentId = args[0];
      const projectCtx = await mcp.call("get_project_context", {});
      const memory = await mcp.call("get_agent_memory", { agent_id: agentId });

      return buildAgentActivationPrompt(agentId, projectCtx, memory);
    }

    case "/repo-map": {
      const repoMap = await generateRepoMap(context.workspaceRoot);
      return `\`\`\`markdown\n${repoMap}\n\`\`\``;
    }

    case "/think": {
      const question = args.join(" ");
      return buildExtendedThinkingPrompt(question);
    }

    default:
      return `Commande inconnue: ${cmd}. Try @bmad /help`;
  }
}
```

## Installation (développement)

```bash
# Prérequis : Node.js 18+, compte GitHub Developer Program
npm install -g @github/copilot-extensions-cli

# Créer le projet extension
copilot-extension init bmad-copilot-extension

# Configurer le webhook vers le MCP Server local
# Dans .env :
BMAD_MCP_HOST=http://localhost:3001
GITHUB_TOKEN=ghp_...

# Démarrer en mode dev
npm run dev

# Enregistrer l'extension dans GitHub Settings > Developer settings > Copilot Extensions
```

## Roadmap

| Version | Features |
|---------|---------|
| v0.1 | `/activate`, `/status`, `/memory show` |
| v0.2 | `/run workflow`, `/session branch` |
| v0.3 | `/repo-map`, `/think`, `/team start` |
| v1.0 | `/install archetype`, publication GitHub Marketplace |

## Dépendances avec d'autres BM

- **BM-20 (MCP Server)** : prérequis — le MCP Server doit être opérationnel
- **BM-05 (Repo Map)** : utilisé par `/repo-map`
- **BM-16 (Session Branching)** : utilisé par `/session branch`
- **BM-17 (Team of Teams)** : utilisé par `/team start`
