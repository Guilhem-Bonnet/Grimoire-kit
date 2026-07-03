"""JSON Schema generator for ``project-context.yaml``.

Produces a `JSON Schema Draft 2020-12 <https://json-schema.org/draft/2020-12>`_
document from the known config structure so external tools (IDEs, linters,
CI pipelines) can validate Grimoire configs.

Usage::

    from grimoire.core.schema import generate_schema
    import json; print(json.dumps(generate_schema(), indent=2))
"""

from __future__ import annotations

from typing import Any

from grimoire.core.project_types import VALID_PROJECT_TYPES

__all__ = ["generate_schema"]

_VALID_TYPES = list(VALID_PROJECT_TYPES)
_VALID_SKILL_LEVELS = sorted(["beginner", "intermediate", "expert"])
_VALID_BACKENDS = sorted(["auto", "local", "qdrant-local", "qdrant-server", "weaviate-server", "mempalace", "ollama"])
_VALID_SHORT_TERM_BACKENDS = sorted(["sqlite", "redis", "none"])
_VALID_LAYER_MODES = sorted(["disabled", "planned", "sqlite-sidecar", "qdrant", "weaviate", "neo4j", "runtime-dashboard"])
_KNOWN_ARCHETYPES = sorted([
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering", "agentic-standard",
    "game-dev",
])


def generate_schema() -> dict[str, Any]:
    """Return a JSON Schema dict for ``project-context.yaml``."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://grimoire-kit.dev/schemas/project-context.json",
        "title": "Grimoire project-context.yaml",
        "description": "Configuration schema for a Grimoire Kit project.",
        "type": "object",
        "required": ["project"],
        "additionalProperties": True,
        "properties": {
            "project": _project_schema(),
            "user": _user_schema(),
            "memory": _memory_schema(),
            "agents": _agents_schema(),
            "installed_archetypes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of installed archetype identifiers.",
            },
        },
    }


def _project_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Project metadata.",
        "required": ["name"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1, "description": "Project name."},
            "description": {"type": "string", "default": "", "description": "Short project description."},
            "type": {"type": "string", "enum": _VALID_TYPES, "default": "webapp", "description": "Project type."},
            "metaphor": {"type": "string", "default": "", "description": "Project metaphor for agents."},
            "stack": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Technology stack entries (e.g. python, docker).",
            },
            "repos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "description": "Repository name."},
                        "path": {"type": "string", "default": ".", "description": "Relative path."},
                        "default_branch": {"type": "string", "default": "main", "description": "Default branch."},
                    },
                    "additionalProperties": False,
                },
                "description": "Linked repositories.",
            },
        },
    }


def _user_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "User preferences.",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "default": "", "description": "User name."},
            "language": {"type": "string", "default": "Français", "description": "Communication language."},
            "document_language": {"type": "string", "default": "Français", "description": "Document output language."},
            "skill_level": {
                "type": "string",
                "enum": _VALID_SKILL_LEVELS,
                "default": "intermediate",
                "description": "User skill level.",
            },
        },
    }


def _memory_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Memory backend configuration.",
        "additionalProperties": False,
        "properties": {
            "backend": {
                "type": "string",
                "enum": _VALID_BACKENDS,
                "default": "auto",
                "description": "Memory storage backend.",
            },
            "collection_prefix": {"type": "string", "default": "grimoire", "description": "Collection name prefix."},
            "embedding_model": {"type": "string", "default": "", "description": "Embedding model name."},
            "qdrant_url": {"type": "string", "default": "", "description": "Qdrant server URL."},
            "weaviate_url": {"type": "string", "default": "", "description": "Weaviate server URL."},
            "weaviate_api_key_env": {
                "type": "string",
                "default": "GRIMOIRE_WEAVIATE_API_KEY",
                "description": "Environment variable that contains the Weaviate API key.",
            },
            "weaviate_collection": {
                "type": "string",
                "default": "",
                "description": "Optional Weaviate collection name. Defaults to a normalized collection_prefix.",
            },
            "neo4j_uri": {"type": "string", "default": "", "description": "Neo4j Bolt URI for graph memory layers."},
            "neo4j_user": {"type": "string", "default": "neo4j", "description": "Neo4j user name."},
            "neo4j_password_env": {
                "type": "string",
                "default": "GRIMOIRE_NEO4J_PASSWORD",
                "description": "Environment variable that contains the Neo4j password.",
            },
            "neo4j_database": {"type": "string", "default": "neo4j", "description": "Neo4j database name."},
            "migration_source_backend": {
                "type": "string",
                "default": "",
                "description": "Backend used as the source while migrating Memory OS data.",
            },
            "migration_target_backend": {
                "type": "string",
                "default": "",
                "description": "Backend targeted by the current Memory OS migration.",
            },
            "migration_bundle_path": {
                "type": "string",
                "default": "",
                "description": "Portable migration bundle path used to preserve vectors, payloads, and graph projections.",
            },
            "mempalace_path": {"type": "string", "default": "", "description": "Optional MemPalace / Chroma palace path."},
            "ollama_url": {"type": "string", "default": "", "description": "Ollama server URL."},
            "layer_profile": {
                "type": "string",
                "default": "standard",
                "description": "Named Memory OS profile exposed to status and visualisation surfaces.",
            },
            "short_term_backend": {
                "type": "string",
                "enum": _VALID_SHORT_TERM_BACKENDS,
                "default": "sqlite",
                "description": "Hot short-term memory backend. Use redis for distributed sessions.",
            },
            "redis_url": {"type": "string", "default": "", "description": "Redis URL for short-term memory when enabled."},
            "knowledge_graph": {
                "type": "string",
                "enum": _VALID_LAYER_MODES,
                "default": "sqlite-sidecar",
                "description": "Structured semantic knowledge graph layer.",
            },
            "memory_graph": {
                "type": "string",
                "enum": _VALID_LAYER_MODES,
                "default": "sqlite-sidecar",
                "description": "Semantic memory graph layer linking entities, facts, agents, and events.",
            },
            "code_graph": {
                "type": "string",
                "enum": _VALID_LAYER_MODES,
                "default": "planned",
                "description": "Semantic code graph layer for symbols, files, tests, and ownership.",
            },
            "task_memory": {
                "type": "string",
                "enum": _VALID_LAYER_MODES,
                "default": "planned",
                "description": "Kanban and task lifecycle memory layer.",
            },
            "visualization": {
                "type": "string",
                "enum": _VALID_LAYER_MODES,
                "default": "runtime-dashboard",
                "description": "Visualization surface for memory layers.",
            },
        },
    }


def _agents_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Agent configuration.",
        "additionalProperties": False,
        "properties": {
            "archetype": {
                "type": "string",
                "enum": _KNOWN_ARCHETYPES,
                "default": "minimal",
                "description": "Agent archetype to use.",
            },
            "custom_agents": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
                "default": [],
                "description": "Custom agent identifiers.",
            },
        },
    }
