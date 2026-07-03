# Grimoire Kit — AGENTS Bridge

Use this file as the AGENTS.md-compatible entrypoint when `grimoire-kit/` is the workspace root inside the monorepo.

## Bootstrap

1. Read `../.github/copilot-instructions.md`.
2. Read `../.github/agents/grimoire-master.agent.md`.
3. From that agent file, load `../_grimoire-runtime/core/agents/grimoire-master.md` and follow its activation instructions exactly.
4. Treat `grimoire-master` as the single user-facing orchestrator. All other agents remain internal sub-agents.
5. If the first user message is already actionable, skip any menu/bootstrap and execute the request directly.

## Project Defaults

- Communication language: Francais
- Document output language: Francais
- SDK-specific coding rules: `.github/copilot-instructions.md`
- Shared agent runtime: `../_grimoire-runtime/`
- Shared workspace wrappers: `../.github/agents/`

## Workspace Conventions

- Follow `grimoire-kit/.github/copilot-instructions.md` for SDK code changes.
- For agent/runtime work, edit the canonical files in the parent workspace instead of creating local duplicates.
- Do not create a second agent tree under `grimoire-kit/` unless the user explicitly asks for a standalone fork of the agent system.
- This bridge assumes `grimoire-kit/` is opened from the monorepo and that the parent paths above exist.