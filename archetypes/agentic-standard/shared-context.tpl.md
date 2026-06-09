# Agentic Standard Shared Context

This project uses the `agentic-standard` Grimoire archetype.

## Source of truth

- The external normative corpus defines obligations and requirement language.
- Grimoire Kit provides generated or copied implementation artifacts.
- Project-specific files may declare conformity, gaps, and decisions, but they do not redefine the standard.

## Required distinctions

- Agent memory: persistent learning about this project or agent behavior.
- Session context: bounded information selected for the current task.
- Knowledge base: indexed external documentation declared in `knowledge-source-registry.yaml`.
- Source of truth: only a source explicitly marked authoritative for the current scope.

## Execution rule

For non-trivial tasks, use a Task Envelope before execution and an Evidence Pack before completion. If a requirement is not applicable, state why instead of silently skipping it.

## LLM provider rule

Use provider-first routing. The selected provider, model or capability, fallback, and data policy must be declared before becoming part of the repeatable flow.
