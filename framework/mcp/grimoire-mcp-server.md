<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/server.svg" width="32" height="32" alt=""> Grimoire MCP Server — Spécification

> **BM-20** — Grimoire expose un serveur MCP (Model Context Protocol) local.
>
> **Ce que ça change** : N'importe quel IDE compatible MCP (Cursor, Cline, VS Code avec MCP,
> Claude Desktop) peut appeler les tools Grimoire directement — Grimoire devient cross-IDE natif.
>
> **Standard** : Model Context Protocol v1 (Anthropic + Microsoft + GitHub, 2025)
> **Transport** : stdio (par défaut) ou HTTP (optionnel pour multi-IDE simultané)

<img src="../../docs/assets/divider.svg" width="100%" alt="">


## <img src="../../docs/assets/icons/temple.svg" width="28" height="28" alt=""> Architecture

```
┌─────────────────────────────────────────────────┐
│           IDE / LLM Client                      │
│  (VS Code Copilot, Cursor, Cline, Claude Desktop)│
└───────────────────┬─────────────────────────────┘
                    │ MCP Protocol (stdio / HTTP)
                    ▼
┌─────────────────────────────────────────────────┐
│            Grimoire MCP Server                      │
│         (Node.js ou Python)                     │
│                                                  │
│  Tools exposés :                                 │
│  ├── get_project_context                        │
│  ├── get_agent_memory                           │
│  ├── run_completion_contract                    │
│  ├── get_workflow_status                        │
│  ├── list_sessions                              │
│  ├── get_failure_museum                         │
│  ├── spawn_subagent_task                        │
│  ├── remember_structured          (BM-22) NEW   │
│  └── recall_structured            (BM-22) NEW   │
└───────────────────┬─────────────────────────────┘
                    │ File system + subprocess
                    ▼
┌─────────────────────────────────────────────────┐
│           Grimoire Framework                        │
│  _grimoire/_memory/ | _grimoire-output/ | cc-verify.sh  │
└─────────────────────────────────────────────────┘
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Tools Exposés

### `get_project_context`

```json
{
  "name": "get_project_context",
  "description": "Retourne le contexte complet du projet Grimoire : nom, stack, agents actifs, décisions récentes",
  "inputSchema": {
    "type": "object",
    "properties": {
      "section": {
        "type": "string",
        "enum": ["all", "stack", "agents", "decisions", "architecture"],
        "description": "Section spécifique à retourner (défaut: all)"
      }
    }
  }
}
```

**Implémentation** : Lit `_grimoire/_memory/shared-context.md` + `_grimoire/_memory/project-context.yaml`, retourne JSON structuré.


### `get_agent_memory`

```json
{
  "name": "get_agent_memory",
  "description": "Retourne les learnings et mémoire sémantique d'un agent spécifique",
  "inputSchema": {
    "type": "object",
    "properties": {
      "agent": {"type": "string", "description": "ID de l'agent (ex: dev, architect, qa)"},
      "query": {"type": "string", "description": "Recherche sémantique dans les learnings (optionnel)"},
      "last_n": {"type": "number", "description": "Retourner les N derniers learnings"}
    },
    "required": ["agent"]
  }
}
```

**Implémentation** : Lit `_grimoire/_memory/agent-learnings/{agent}.md`, appelle optionnellement `mem0-bridge.py query`.


### `run_completion_contract`

```json
{
  "name": "run_completion_contract",
  "description": "Exécute le Completion Contract (cc-verify.sh) et retourne PASS ou FAIL avec output",
  "inputSchema": {
    "type": "object",
    "properties": {
      "stack": {
        "type": "string",
        "enum": ["auto", "go", "typescript", "python", "terraform", "ansible", "docker", "k8s"],
        "description": "Stack à vérifier (auto = détection automatique)"
      },
      "working_dir": {"type": "string", "description": "Répertoire à vérifier (défaut: racine projet)"}
    }
  }
}
```

**Implémentation** : Exécute `bash _grimoire/_config/custom/cc-verify.sh`, parse stdout, retourne `{status: "PASS"|"FAIL", output: string, duration_ms: number}`.


### `get_workflow_status`

```json
{
  "name": "get_workflow_status",
  "description": "Retourne le statut du workflow en cours ou d'un run spécifique",
  "inputSchema": {
    "type": "object",
    "properties": {
      "run_id": {"type": "string", "description": "ID spécifique (défaut: run le plus récent)"},
      "branch": {"type": "string", "description": "Branche de session (défaut: main)"}
    }
  }
}
```

**Implémentation** : Lit `_grimoire-output/.runs/{branch}/{run_id}/state.json`.


### `list_sessions`

```json
{
  "name": "list_sessions",
  "description": "Liste toutes les branches de session Grimoire actives",
  "inputSchema": {
    "type": "object",
    "properties": {
      "status": {
        "type": "string",
        "enum": ["all", "active", "completed", "failed"],
        "description": "Filtrer par statut"
      }
    }
  }
}
```


### `get_failure_museum`

```json
{
  "name": "get_failure_museum",
  "description": "Retourne les erreurs passées du projet pour éviter de les répéter",
  "inputSchema": {
    "type": "object",
    "properties": {
      "category": {
        "type": "string",
        "enum": ["all", "CC-FAIL", "WRONG-ASSUMPTION", "CONTEXT-LOSS", "HALLUCINATION", "ARCH-MISTAKE", "PROCESS-SKIP"]
      },
      "top_n": {"type": "number", "description": "Retourner les N erreurs les plus critiques"}
    }
  }
}
```


### `spawn_subagent_task`

```json
{
  "name": "spawn_subagent_task",
  "description": "Enregistre une sous-tâche dans la queue de l'orchestrateur pour exécution",
  "inputSchema": {
    "type": "object",
    "properties": {
      "agent": {"type": "string", "description": "Agent à spawner"},
      "task": {"type": "string", "description": "Description complète de la tâche"},
      "context_files": {"type": "array", "items": {"type": "string"}},
      "output_key": {"type": "string"},
      "run_id": {"type": "string"}
    },
    "required": ["agent", "task", "output_key"]
  }
}
```

### `remember_structured` (BM-22)

Upsert une mémoire dans la collection Qdrant typée. Idempotent via UUID5.

```json
{
  "name": "remember_structured",
  "description": "Mémoriser dans la collection Qdrant typée (agent-learnings, decisions, shared-context, failures, stories)",
  "inputSchema": {
    "type": "object",
    "properties": {
      "type": {
        "type": "string",
        "enum": ["shared-context", "decisions", "agent-learnings", "failures", "stories"],
        "description": "Collection cible"
      },
      "agent": {"type": "string", "description": "Agent tag (ex: forge, atlas, dev)"},
      "text": {"type": "string", "description": "Contenu à mémoriser"},
      "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags optionnels"}
    },
    "required": ["type", "agent", "text"]
  }
}
```

### `recall_structured` (BM-22)

Recherche sémantique cross-collection avec filtres optionnels.

```json
{
  "name": "recall_structured",
  "description": "Recherche sémantique dans les collections Qdrant Grimoire",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Question ou requête en langage naturel"},
      "type": {
        "type": "string",
        "enum": ["shared-context", "decisions", "agent-learnings", "failures", "stories"],
        "description": "Filtrer par collection (optionnel, sinon recherche cross-collection)"
      },
      "agent": {"type": "string", "description": "Filtrer par agent (optionnel)"},
      "limit": {"type": "integer", "default": 5}
    },
    "required": ["query"]
  }
}
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/bolt.svg" width="28" height="28" alt=""> Installation & Lancement

```bash
# 1. Installer les dépendances (Node.js ou Python)
cd grimoire-kit/framework/mcp/
npm install   # ou pip install -r requirements.txt

# 2. Configurer dans votre IDE compatible MCP
# Exemple : Claude Desktop → claude_desktop_config.json
{
  "mcpServers": {
    "grimoire": {
      "command": "node",
      "args": ["/chemin/vers/grimoire-kit/framework/mcp/server.js"],
      "env": {
        "Grimoire_PROJECT_ROOT": "/chemin/vers/votre-projet"
      }
    }
  }
}

# 3. Vérifier que le serveur démarre
node framework/mcp/server.js --test
# → Grimoire MCP Server v1.0 — 7 tools registered — Ready
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Implémentation Référence — `server.js`

> Fichier à créer : `framework/mcp/server.js`
> Stack : Node.js + `@modelcontextprotocol/sdk`

```javascript
#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { readFileSync, existsSync } from "fs";
import { execSync } from "child_process";
import { resolve, join } from "path";

const PROJECT_ROOT = process.env.Grimoire_PROJECT_ROOT || process.cwd();
const Grimoire_MEMORY = join(PROJECT_ROOT, "_grimoire/_memory");
const Grimoire_OUTPUT = join(PROJECT_ROOT, "_grimoire-output");

const server = new Server(
  { name: "grimoire-mcp-server", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// Tool : get_project_context
server.setRequestHandler("tools/call", async (request) => {
  const { name, arguments: args } = request.params;
  switch (name) {
    case "get_project_context": {
      const sharedCtx = join(Grimoire_MEMORY, "shared-context.md");
      const content = existsSync(sharedCtx) ? readFileSync(sharedCtx, "utf-8") : "No shared-context.md found";
      return { content: [{ type: "text", text: content }] };
    }
    case "run_completion_contract": {
      const ccScript = join(PROJECT_ROOT, "_grimoire/_config/custom/cc-verify.sh");
      if (!existsSync(ccScript)) return { content: [{ type: "text", text: "CC script not found" }] };
      try {
        const output = execSync(`bash ${ccScript}`, { encoding: "utf-8", timeout: 120000 });
        return { content: [{ type: "text", text: `CC PASS\n${output}` }] };
      } catch (e) {
        return { content: [{ type: "text", text: `CC FAIL\n${e.stdout}` }] };
      }
    }
    case "remember_structured": {
      const { type, agent, text, tags = [] } = args;
      const tagStr = tags.length ? ` --tags ${tags.join(",")}` : "";
      const cmd = `python3 ${join(Grimoire_MEMORY, "mem0-bridge.py")} remember --type ${type} --agent ${agent} "${text.replace(/"/g, '\\"')}"${tagStr}`;
      try {
        const out = execSync(cmd, { encoding: "utf-8", timeout: 30000 });
        return { content: [{ type: "text", text: out.trim() }] };
      } catch (e) {
        return { content: [{ type: "text", text: `Error: ${e.message}` }] };
      }
    }
    case "recall_structured": {
      const { query, type, agent, limit = 5 } = args;
      const typeFlag = type ? ` --type ${type}` : "";
      const agentFlag = agent ? ` --agent ${agent}` : "";
      const cmd = `python3 ${join(Grimoire_MEMORY, "mem0-bridge.py")} recall "${query.replace(/"/g, '\\"')}"${typeFlag}${agentFlag} --limit ${limit}`;
      try {
        const out = execSync(cmd, { encoding: "utf-8", timeout: 30000 });
        return { content: [{ type: "text", text: out.trim() }] };
      } catch (e) {
        return { content: [{ type: "text", text: `Error: ${e.message}` }] };
      }
    }
    // ... autres tools
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/server.svg" width="28" height="28" alt=""> MCP v2 — Sampling (BM-31)

> MCP v2 introduit la capability `sampling` : le serveur MCP peut demander au **client LLM**
> d'effectuer un appel LLM imbriqué. Cela permet à Grimoire de déléguer une sous-question
> à un agent spécialisé sans quitter le contexte courant.

### Déclaration de la capability

```javascript
const server = new Server(
  { name: "grimoire-mcp-server", version: "2.0.0" },
  { capabilities: { tools: {}, sampling: {} } }  // ← sampling activé
);
```

### Tool : `delegate_to_agent` (BM-31)

```json
{
  "name": "delegate_to_agent",
  "description": "Délègue une sous-question à un agent Grimoire spécialisé via MCP sampling. Le LLM client exécute l'appel avec le persona de l'agent cible.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "agent_id": {
        "type": "string",
        "enum": ["analyst", "architect", "qa", "sm", "tech-writer"],
        "description": "Agent cible pour la délégation"
      },
      "task": {"type": "string", "description": "Tâche ou question à déléguer"},
      "context": {"type": "string", "description": "Contexte pertinent pour l'agent"},
      "max_tokens": {"type": "integer", "default": 2048}
    },
    "required": ["agent_id", "task"]
  }
}
```

**Implémentation (MCP sampling)** :

```javascript
case "delegate_to_agent": {
  const { agent_id, task, context, max_tokens = 2048 } = args;
  // Charger le persona de l'agent
  const agentFile = join(PROJECT_ROOT, `_grimoire/bmm/agents/${agent_id}.md`);
  const persona = existsSync(agentFile) ? readFileSync(agentFile, "utf-8").substring(0, 800) : "";
  // Déléguer via MCP sampling — le LLM client exécute l'appel
  const response = await server.createMessage({
    messages: [{
      role: "user",
      content: { type: "text", text: `${context}\n\nTâche: ${task}` }
    }],
    system: `Tu es ${agent_id} — ${persona}`,
    maxTokens: max_tokens,
  });
  return { content: [{ type: "text", text: response.content.text }] };
}
```

> **Note** : MCP sampling nécessite que le client LLM supporte la capability. Supporté par
> Claude Desktop, Cursor (roadmap), VS Code MCP extension (roadmap).

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/rocket.svg" width="28" height="28" alt=""> Roadmap

- [ ] **v1.0** — 9 tools core (get_context, get_memory, run_cc, get_status, list_sessions, get_museum, spawn_task, remember_structured, recall_structured)
- [ ] **v1.1** — HTTP transport pour multi-IDE simultané
- [ ] **v1.2** — Tool `search_codebase` (grep sémantique via mem0)
- [ ] **v1.3** — Tool `create_delivery_contract` (génère un contrat à partir des outputs de la session)
- [ ] **v2.0** — MCP v2 Sampling : `delegate_to_agent` (BM-31) + `capabilities: sampling`
- [ ] **v2.1** — MCP Resources (expose les fichiers Grimoire comme resources navigables dans l'IDE)
- [ ] **v2.2** — Agent2Agent Protocol integration (BM-32)


*BM-20 Grimoire MCP Server Specification | framework/mcp/grimoire-mcp-server.md*
