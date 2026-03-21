# GitHub Copilot — Instructions for Grimoire Kit SDK

## Project Context

This repository is **Grimoire Kit** — a Python SDK that orchestrates AI agents,
workflows, structured memory, and quality tools for any software project.

Distributed as `grimoire-kit` on PyPI. CLI entrypoint: `grimoire`.

**Key directories:**
- `src/grimoire/` — SDK sources
  - `core/` — Config, Project, Scanner, Merge, Validator
  - `cli/` — CLI Typer (`app.py`, `cmd_upgrade.py`, `cmd_merge.py`)
  - `memory/` — MemoryManager + backends (JSON, Qdrant, Ollama)
  - `mcp/` — MCP server
  - `registry/` — AgentRegistry, LocalRegistry
  - `tools/` — HarmonyCheck, PreflightCheck, MemoryLint, etc.
- `tests/unit/` — pytest test suite (640+ tests)
- `framework/tools/` — standalone Python tools (distill.py, context-summarizer.py, etc.)
- `archetypes/` — project archetypes (minimal, web-app, infra-ops, creative-studio, fix-loop)
- `docs/` — MkDocs documentation
- `pyproject.toml` — package configuration and dependencies

## Code Style Rules

### Python (`src/grimoire/**`, `framework/tools/*.py`)
- Type hints on **all** functions and methods
- `frozen=True, slots=True` on dataclasses
- `pathlib.Path` over `os.path`
- Ruff-compatible — no bare `except`, explicit error types, no `Any` without reason
- `if __name__ == "__main__":` guard on all standalone tools
- Test coverage target: > 70% on `src/grimoire/`

### Shell (`grimoire.sh`, `.github/hooks/scripts/*.sh`)
- Bash — use `[[ ]]`, `local`, arrays
- Always `set -euo pipefail` in standalone scripts
- Colors: `RED/GREEN/YELLOW/BLUE/NC` variables defined at top
- Subcommand functions: `cmd_<subcommand>()`

### Markdown (`docs/*.md`, `*.md`)
- Language: French (except code/commands)
- No manual heading numbering
- Relative links from current file

## CLI Convention

- CLI: `grimoire <command>` (Typer-based)
- All subcommands in `src/grimoire/cli/`
- `grimoire --version` must always work

## Testing

- Framework: `pytest` with `pytest-xdist` (`-n auto`)
- Unit tests in `tests/unit/`, organised by module
- Mocks via `unittest.mock` or `pytest` fixtures
- Never import private functions directly — test via public API

## Git Commit Convention

Conventional Commits: `feat|fix|chore|docs|refactor|perf|test(<scope>): <desc>`
Scopes: `cli`, `core`, `memory`, `mcp`, `registry`, `tools`, `ci`, `docs`, `deps`

## Security

- Never hardcode secrets or tokens
- Always validate user input at CLI boundaries
- Follow OWASP Top 10 principles

## Important Anti-Patterns

- ❌ Never `pip install` without activating `.venv` first (PEP 668 on Ubuntu)
- ❌ Never modify `grimoire --version` exit code (CI contract: 0 = OK)
- ❌ Never add a framework tool without a corresponding test
- ❌ Never use `os.path` — use `pathlib.Path`
- ❌ Never hardcode `/home/user/` — use `Path.home()` or env vars
