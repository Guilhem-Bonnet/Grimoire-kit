# Agentic Standard Bridge

This directory is the Grimoire Kit bridge between an external agentic standard and concrete project artifacts.

It does not define the standard itself. The normative source remains the external corpus (`processus-developpement-agentique`). Grimoire Kit only turns that corpus into installable profiles, templates, prompts, controls, and evidence structures.

## Responsibilities

| Layer | Role | Output in Grimoire Kit |
|---|---|---|
| Normative corpus | Defines obligations, controls, pattern families, profiles, and evidence expectations | Referenced as an upstream source |
| Grimoire Forge | Selects a target profile and assembles project artifacts | Profile map, generator logic, validation flow |
| Grimoire Kit | Provides consumable flow assets for project teams | Archetypes, templates, manifests, checks |

## Files

- `profile-map.yaml` maps standard capabilities to Grimoire profiles and concrete kit artifacts.
- `templates/` contains project-facing documents generated or copied into a target project.
- `../../archetypes/agentic-standard/` is the installable archetype for teams that want the standard-aware flow.

## Design rules

1. The bridge must not copy normative obligations as if Grimoire Kit were the source of truth.
2. Every generated artifact must keep a trace to the upstream requirement, control, or pattern family it implements.
3. External knowledge indexing is declared as a knowledge base layer, not as memory and not as session context.
4. LLM compatibility is provider-first: provider, model, capability, data policy, and fallback must be explicit.
5. Evidence is part of the flow, not a post-hoc report.
