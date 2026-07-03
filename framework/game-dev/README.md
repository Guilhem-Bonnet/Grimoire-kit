# Game Dev Domain Bridge

This directory is the Grimoire Kit bridge between the upstream agentic **game-dev cluster** and concrete project artifacts.

It does not define the standard itself. The normative source remains the external corpus (`processus-developpement-agentique`), specifically its game-dev use-cases (UC-08 → UC-50), the skill aisles (A → AC), the genre lenses, and the capability/modality matrix (MOD-03). Grimoire Kit only turns that corpus into an installable archetype, discipline agents, templates, and evidence structures.

The game-dev domain is built **on top of** the agentic standard (`../agentic-standard/`). Game use-cases compose socle patterns (`KNO-*`, `QUA-*`, `GOV-*`, `COG-*`, `ORC-*`, `RUN-*`, `MOD-*`); this bridge is a **domain extension**, not a replacement.

## Responsibilities

| Layer | Role | Output in Grimoire Kit |
|---|---|---|
| Normative corpus | Defines game-dev use-cases, skill aisles, genre lenses, and routing rules | Referenced as an upstream source, bundled under `knowledge/` |
| Grimoire Forge | Selects a genre lens and assembles a game project | Domain map, init genre picker, archetype install |
| Grimoire Kit | Provides consumable flow assets for game teams | `game-dev` archetype, discipline agents, templates, checks |

## Files

- `domain-map.yaml` — machine-readable map: 9 normative rules, use-case clusters (UC-08→50), skill aisles (A→AC), genre lenses, the MOD-03 capability/modality matrix, artifact types, and disciplines.
- `templates/` — project-facing evidence documents copied into a target game project (GDD, content validation, balance regression, playtest, determinism replay, certification, telemetry decision, capability routing, asset budget).
- `knowledge/` — self-contained bundle of the upstream game-dev reference docs (guide, use-cases, catalogue, genre profiles, capability matrix, diagrams) with provenance headers.
- `../../archetypes/game-dev/` — the installable archetype for teams building a game.

## Design rules

1. The bridge must not copy normative obligations as if Grimoire Kit were the source of truth; bundled knowledge carries a provenance header.
2. Every generated artifact keeps a trace to the upstream use-case (UC-xx) or socle pattern it implements.
3. The GDD is the single source of truth; generated content traces back to it.
4. Tested simulation is deterministic (seed, fixed step, state hash); milestones and certification are reached by proof, never by declaration.
5. Out of an LLM's core competence (art, audio, 3D, video), route to the capable target (specialized model, DCC tool, or human) and record a capability routing record — never produce a mediocre final asset.
6. Evidence is part of the flow, not a post-hoc report.

## Install in a target project

```bash
grimoire init . --archetype game-dev
# or, into an existing Grimoire project:
grimoire-init.sh install --archetype game-dev
```

Then copy the templates you need per the selected genre lens (see `domain-map.yaml#genre_lenses`).
