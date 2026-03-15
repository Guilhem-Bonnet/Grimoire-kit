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

__all__ = ["generate_schema"]

_VALID_TYPES = sorted(["webapp", "api", "service", "infrastructure", "library", "cli", "generic"])
_VALID_SKILL_LEVELS = sorted(["beginner", "intermediate", "expert"])
_VALID_BACKENDS = sorted(["auto", "local", "qdrant-local", "qdrant-server", "ollama"])
_KNOWN_ARCHETYPES = sorted([
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
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
            "ollama_url": {"type": "string", "default": "", "description": "Ollama server URL."},
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
