# Example blueprints

Versioned, working examples of the v1 blueprint format (schema: `schemas/blueprint-v1.schema.json`).

- `minimal.blueprint.json` — the smallest valid blueprint; every constraint is explained in its `description` fields.
- `web-pipeline.blueprint.json` — a multi-node flow chaining patterns and extension nodes (`crewai`, `langgraph`) published in this registry.

## Usage

```bash
# Validate (JSON Schema + compile-level structural checks)
grimoire blueprint validate registry/blueprints/minimal.blueprint.json

# Compile into a mission pack (fail-closed: missing extensions are listed with the install command)
grimoire blueprint compile registry/blueprints/web-pipeline.blueprint.json --project-root .

# Publish to a registry, then install into a project
grimoire ext publish registry/blueprints/web-pipeline.blueprint.json --registry <registry-dir>
grimoire ext add-blueprint web-pipeline --registry <registry-dir> --project-root .
```

Start your own flow from a template with `grimoire blueprint new <id> --template minimal|pipeline`.
