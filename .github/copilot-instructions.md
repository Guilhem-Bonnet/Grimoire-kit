# GitHub Copilot — Instructions for BMAD Custom Kit

## Project Context

This repository is the **BMAD Custom Kit** — a framework for bootstrapping AI-assisted software projects with specialized agents, archetype-based DNA constraints, and structured memory.

**Key directories:**
- `grimoire-init.sh` — main CLI: `session-branch`, `install`, `doctor`, `validate`, `changelog`, `hooks`, `trace`, `resume`, `bench`, `forge`, `guard`, `evolve`
- `archetypes/` — project archetypes (minimal, web-app, infra-ops, fix-loop, stack)
- `framework/` — DNA schema, hooks, tools (gen-tests.py, agent-bench.py, agent-forge.py, context-guard.py, dna-evolve.py, grimoire-completion.zsh), agent-rules, context-router, MCP server, BMAD_TRACE
- `_bmad/` — BMAD runtime (agents, workflows, memory)
- `docs/` — human-readable documentation

## Code Style Rules

### Shell (grimoire-init.sh, framework/hooks/*.sh)
- Bash only (not POSIX sh) — use `[[ ]]`, `local`, heredocs, arrays
- Always `set -euo pipefail` in standalone scripts (hooks)
- Colors: `RED/GREEN/YELLOW/BLUE/NC` variables already defined in grimoire-init.sh
- Functions: `cmd_<subcommand>()` pattern for subcommands
- Error output: `error "message"` function (exits 1), `warn "msg"` (continues), `info "msg"` (neutral)
- Always add `shift` at start of subcommand functions to remove subcommand arg
- Python inline via heredoc uses `PYEOF` as delimiter

### YAML (*.dna.yaml)
- Schema ref: `# yaml-language-server: $schema=../../framework/archetype-dna.schema.yaml`
- Required fields: `$schema: grimoire-archetype-dna/v1`, `id`, `name`, `version`, `description`, `archetype_type`
- `acceptance_criteria[].blocking: true` = critique, `false` = soft warning
- `tools_required[].required: true` = install vérifiée par `doctor`

### Python (framework/tools/*.py)
- Type hints on all functions
- Ruff-compatible (no `any`, explicit error types)
- `if __name__ == "__main__"` guard always present
- Prefer `pathlib.Path` over `os.path`

### Markdown (docs/*.md, archetypes/**/*.md)
- Langue : Français (sauf code/commandes)
- Titres : pas de numérotation manuelle
- Liens relatifs depuis le fichier courant

## Agent DNA Constraints

Respect `acceptance_criteria` per archetype:
- `stack/go`: table-driven tests, `go vet`, no goroutine leak
- `stack/typescript`: `strict: true`, no `any`, typed props
- `stack/python`: type hints, `ruff`, `pytest`
- `stack/terraform`: `terraform validate` + `fmt`, `tfsec`
- `stack/k8s`: resource limits, readiness/liveness probes
- `stack/docker`: multi-stage, non-root (`USER 1000`)
- `stack/ansible`: idempotent, `ansible-lint`
- `infra-ops`/`minimal`: CC (`cc-verify.sh`) before "done"
- `fix-loop`: FER YAML for every fix session

## BMAD_TRACE Convention

Non-trivial decisions → `[DECISION] <desc> | agent:copilot | <rationale>` in BMAD_TRACE.md

## Git Commit Convention

Conventional Commits: `feat|fix|chore|docs|refactor(<scope>): <desc>` or `BM-XX: <desc>`
Scopes: `init`, `hooks`, `dna`, `stack`, `docs`, `vscode`, `ci`, `memory`, `mcp`, `tools`

## Rate Limit Best Practices

- Keep conversation context minimal — start fresh chats after ~20 exchanges
- Reference only immediately needed files (avoid broad `@workspace`)
- Use local tools (`guard`, `bench`, `evolve`) for analysis — they consume zero API quota
- If rate-limited on one model, switch to another (quotas are per-model)

## Important Anti-Patterns to Avoid

- ❌ Never suggest `rm -rf` without guard on path variables
- ❌ Never write DNA files without `$schema` reference
- ❌ Never mark an acceptance criterion `blocking: true` without a `test_command`
- ❌ Never add `--force` to git operations without explicit user confirmation
- ❌ Never hardcode `/home/user/` paths — use `$HOME` or relative paths
- ❌ Never generate Python without type hints in this project
- ❌ Never create a `framework/tools/*.py` without a `cmd_<name>()` wrapper in `grimoire-init.sh`
- ❌ Never add a new `grimoire-init.sh` subcommand without updating `grimoire-completion.zsh`
- ❌ Never modify `guard` exit codes — 0/1/2 are CI-contract (0=OK, 1=warn, 2=critical)
