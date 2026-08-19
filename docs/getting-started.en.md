# Getting Started — Grimoire Kit v3

<sub>English — <a href="getting-started.md">version française</a></sub>

> This guide takes you from installation to your first Grimoire project.

## Requirements

- **Python 3.12+**
- **pip**, **pipx** (recommended) or **uv**
- An editor with Copilot / LLM support (VS Code recommended)

## Installation

```bash
# Via pipx (recommended — automatic isolation)
pipx install grimoire-kit

# Via pip inside a venv
python3 -m venv .venv && source .venv/bin/activate
pip install grimoire-kit

# With MCP (Model Context Protocol) support
pip install grimoire-kit[mcp]

# All extras (MCP + Qdrant + Ollama)
pip install grimoire-kit[all]
```

> **Note**: On Ubuntu/Debian, `pip install` outside a venv is blocked (PEP 668).
> Use `pipx` or a venv.

Check the installation:

```bash
grimoire --version
```

## Create a project

### In one command (recommended)

`grimoire up` runs the whole journey: init (express mode), identity
propagation, governed agentic standard, then a health check. The command is
idempotent — run again on an existing project, it only fills the gaps.

```bash
# New project
grimoire up mon-projet --archetype web-app --name "Mon Projet" --user "Alice"

# Existing project
cd votre-projet/
grimoire up .

# Full interactive wizard, or without the governed standard
grimoire up . --interactive
grimoire up . --no-standard
```

Available archetypes: `minimal`, `web-app`, `infra-ops`, `platform-engineering`,
`agentic-standard`, `creative-studio`, `fix-loop`.

### Step by step (what `up` chains together)

```bash
grimoire init mon-projet --archetype web-app   # scaffold only
cd mon-projet
grimoire setup                                 # identity propagation
grimoire standard init . --needs solo-prototyping
grimoire doctor
```

### Detect your existing projects

To list the projects on a machine and enrol them in the cockpit:

```bash
grimoire cockpit scan ~/dev            # recursive crawl (depth 4 by default)
grimoire cockpit scan ~/dev --yes      # enrols every Grimoire project found
```

Git repositories that are not initialised are listed with the suggestion
`grimoire up <path>`.

## Generated structure

```
mon-projet/
├── project-context.yaml          # Centralised configuration
├── _grimoire/
│   ├── _config/
│   │   ├── agents/               # Installed agent references
│   │   ├── manifest.yaml         # Project manifest
│   │   └── custom/               # Local overrides
│   ├── _memory/
│   │   ├── shared-context.md     # Shared context
│   │   ├── decisions-log.yaml    # Decision log
│   │   └── learnings.yaml        # Learnings
│   └── core/
│       ├── agents/               # Deployed agents
│       └── workflows/            # Active workflows
└── .github/
    └── copilot-instructions.md   # VS Code Copilot instructions
```

## CLI commands

| Command | Description |
|---------|-------------|
| `grimoire up [path]` | Full journey: init + setup + standard + doctor (idempotent) |
| `grimoire init <path>` | Initialise a project (scaffold only) |
| `grimoire setup` | Synchronise user configuration |
| `grimoire setup --check` | Audit the synchronisation (CI-friendly) |
| `grimoire doctor [--fix]` | Check project and environment health; `--fix` regenerates missing wrappers and `.mcp.json` |
| `grimoire cockpit scan <root>` | Detect and enrol existing projects |
| `grimoire blueprint <cmd>` | Blueprints: `new`, `validate`, `compile` |
| `grimoire status` | Show project state |
| `grimoire add <agent>` | Add an agent |
| `grimoire remove <agent>` | Remove an agent |
| `grimoire validate` | Validate `project-context.yaml` |
| `grimoire check` | Lint + validate + doctor in one pass |
| `grimoire standard <cmd>` | Governed agentic standard (`needs`, `init`, `verify`, `audit`, `score`, `gate`) |
| `grimoire merge <source>` | Merge Grimoire files |
| `grimoire merge --undo` | Undo the last merge |
| `grimoire upgrade` | Migrate a project from v2 to v3 |
| `grimoire registry list` | List available agents |
| `grimoire registry search <q>` | Search for an agent |

## Configure your identity

After `grimoire init`, configure your name and language. The `setup` command
propagates these values into every configuration file of the project (Copilot
instructions).

```bash
# Synchronise from project-context.yaml
grimoire setup

# Or specify directly
grimoire setup --user "Alice" --lang "English" --skill-level intermediate

# Check the synchronisation (useful in CI)
grimoire setup --check
```

**Source of truth**: `project-context.yaml` (`user` section). The `setup`
command propagates the values to:

- `.github/copilot-instructions.md` — instructions injected into Copilot Chat

## Check your project

```bash
grimoire doctor
```

Expected output (excerpt):

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

Environment checks (uv, docker, Qdrant, Ollama) are optional: a warning shows
the exact remediation command without blocking. A broken reference in
`.mcp.json`, on the other hand, is an error.

To repair a project (missing agent wrappers or `.mcp.json`):

```bash
grimoire doctor . --fix
```

## Adopt the governed agentic standard

Beyond scaffolding, Grimoire provides an **agentic standard**: a project need
maps to a profile (`starter → controlled → orchestrated → governed →
production`) which activates **verifiable governed patterns** (36 in the
catalogue).

```bash
# Choose by need (start small)
grimoire standard needs
grimoire standard init . --needs solo-prototyping

# Verify / audit / score / gate compliance (fail-closed)
grimoire standard verify
grimoire standard audit
grimoire standard score
grimoire standard gate
```

Control reference: [Governed controls](governed-controls.md) · integration:
[Agentic standard](agentic-standard-integration.md) · install by needs:
[Install by needs](agentic-standard-install-by-needs.md).

## Multi-assistant portability

`grimoire init` generates portable entrypoints — `CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`, `.cursorrules` (pointing to `.github/copilot-instructions.md`) and
an OS-neutral `.mcp.json` — so the project works with Copilot, Claude Code,
Codex, Gemini CLI and Cursor without manual configuration.

## Create a blueprint

A blueprint describes an agent pipeline (nodes, edges, contracts) that compiles
into a mission pack. The CLI covers the whole cycle:

```bash
grimoire blueprint new mon-pipeline            # scaffold a valid .blueprint.json
grimoire blueprint validate mon-pipeline.blueprint.json
grimoire blueprint compile mon-pipeline.blueprint.json
grimoire blueprint evals mon-pipeline.blueprint.json --record trace.json
```

Evals attached to a blueprint describe what the flow must produce, not just the
shape it must have. The Studio never runs them: your host runs the flow once and
records what happened (contract returned, tokens spent, verdict, path taken),
then `blueprint evals` checks the recording. A case missing from the record is
reported as "not executed" — never as a failure.

- Reference schema: `schemas/blueprint-v1.schema.json`
- Ready-to-use examples: `registry/blueprints/` (`minimal`, `web-pipeline`)
- The visual studio remains available through `grimoire serve` (the
  multi-project cockpit is served by `grimoire cockpit`)

Every validation error reports the offending JSON path, the expected value and
the remediation; a missing extension at compile time prints the exact
`grimoire ext add` command.

## Use the Python SDK

```python
from grimoire.core.config import GrimoireConfig
from grimoire.core.project import GrimoireProject

config = GrimoireConfig.from_yaml("project-context.yaml")
project = GrimoireProject(config)

# List the agents
for agent in project.status().agents:
    print(f"{agent.id}: {agent.name}")
```

## MCP server

If you installed `grimoire-kit[mcp]`:

```bash
grimoire-mcp
```

Configure it in VS Code (`.vscode/mcp.json`):

```json
{
  "servers": {
    "grimoire": {
      "command": "grimoire-mcp"
    }
  }
}
```

## Shell completion

Grimoire Kit supports completion for Bash, Zsh and Fish:

```bash
# Install completion for your shell
grimoire --install-completion

# Print the script without installing it
grimoire --show-completion
```

After installing, restart your terminal. Type `grimoire <TAB>` to see the
available commands.

## Next steps

- [YAML reference](grimoire-yaml-reference.md) — full schema of `project-context.yaml`
- [SDK guide](sdk-guide.md) — using the Python SDK
- [MCP integration](mcp-integration.md) — MCP server for Copilot
- [Migration v2 to v3](migration-v2-v3.md) — migrate an existing project
- [Concepts](concepts.md) — architecture and principles
