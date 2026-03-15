"""Schema validation for project-context.yaml.

Validates structure, types, constraints, and references with
actionable error messages.

Usage::

    from grimoire.core.validator import validate_config

    errors = validate_config(data, project_root=Path("."))
    if errors:
        for e in errors:
            print(f"  {e}")
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ValidationError", "validate_config"]

# ── Validation result ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ValidationError:
    """A single validation problem."""

    path: str  # YAML path like "project.name"
    message: str
    suggestion: str = ""

    def __str__(self) -> str:
        s = f"[{self.path}] {self.message}"
        if self.suggestion:
            s += f"  → {self.suggestion}"
        return s


# ── Known enums ───────────────────────────────────────────────────────────────

_VALID_TYPES = frozenset({
    "webapp", "api", "service", "infrastructure", "library", "cli", "generic",
})

_VALID_SKILL_LEVELS = frozenset({"beginner", "intermediate", "expert"})

_VALID_BACKENDS = frozenset({
    "auto", "local", "qdrant-local", "qdrant-server", "ollama",
})

_KNOWN_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
})

# Known keys per section for unknown-key detection
_KNOWN_TOP_KEYS = frozenset({
    "project", "user", "memory", "agents", "installed_archetypes",
})

_KNOWN_PROJECT_KEYS = frozenset({
    "name", "description", "type", "metaphor", "stack", "repos",
})

_KNOWN_USER_KEYS = frozenset({
    "name", "language", "document_language", "skill_level",
})

_KNOWN_MEMORY_KEYS = frozenset({
    "backend", "collection_prefix", "embedding_model", "qdrant_url", "ollama_url",
})

_KNOWN_AGENTS_KEYS = frozenset({
    "archetype", "custom_agents",
})


def _suggest_key(unknown: str, known: frozenset[str]) -> str:
    """Return 'Did you mean X?' if a close match exists, else ''."""
    matches = difflib.get_close_matches(unknown, sorted(known), n=1, cutoff=0.6)
    return f"Did you mean '{matches[0]}'?" if matches else ""


def _check_unknown_keys(
    section: dict[str, Any],
    known: frozenset[str],
    path: str,
    errors: list[ValidationError],
) -> None:
    """Emit warnings for unrecognised keys in a config section."""
    for key in section:
        if key not in known:
            errors.append(ValidationError(
                path=f"{path}.{key}" if path else key,
                message=f"Unknown key '{key}'.",
                suggestion=_suggest_key(key, known),
            ))


# ── Validators ────────────────────────────────────────────────────────────────


def validate_config(
    data: Any,
    *,
    project_root: Path | None = None,
) -> list[ValidationError]:
    """Validate a parsed YAML dict against the Grimoire schema.

    Returns a list of :class:`ValidationError`; empty list means valid.
    """
    errors: list[ValidationError] = []

    if not isinstance(data, dict):
        errors.append(ValidationError(
            path="(root)",
            message="Config must be a YAML mapping.",
            suggestion="Ensure the file starts with key-value pairs.",
        ))
        return errors

    # Required: project section
    if "project" not in data:
        errors.append(ValidationError(
            path="project",
            message="Missing required 'project' section.",
            suggestion="Add: project:\\n  name: \"my-project\"",
        ))
    else:
        _validate_project(data["project"], errors)

    # Optional sections
    if "user" in data:
        _validate_user(data["user"], errors)

    if "memory" in data:
        _validate_memory(data["memory"], errors)

    if "agents" in data:
        _validate_agents(data["agents"], errors)

    if "installed_archetypes" in data:
        _validate_installed_archetypes(data["installed_archetypes"], errors)

    # Unknown top-level keys
    _check_unknown_keys(data, _KNOWN_TOP_KEYS, "", errors)

    return errors


def _validate_project(section: Any, errors: list[ValidationError]) -> None:
    if not isinstance(section, dict):
        errors.append(ValidationError(
            path="project",
            message="'project' must be a mapping.",
        ))
        return

    # project.name required
    name = section.get("name")
    if not name or not isinstance(name, str):
        errors.append(ValidationError(
            path="project.name",
            message="'project.name' is required and must be a non-empty string.",
        ))

    # project.type
    ptype = section.get("type")
    if ptype is not None and ptype not in _VALID_TYPES:
        errors.append(ValidationError(
            path="project.type",
            message=f"Unknown project type '{ptype}'.",
            suggestion=f"Valid types: {', '.join(sorted(_VALID_TYPES))}",
        ))

    # project.stack
    stack = section.get("stack")
    if stack is not None:
        if not isinstance(stack, list):
            errors.append(ValidationError(
                path="project.stack",
                message="'project.stack' must be a list of strings.",
            ))
        elif not all(isinstance(s, str) for s in stack):
            errors.append(ValidationError(
                path="project.stack",
                message="All stack entries must be strings.",
            ))

    # project.repos
    repos = section.get("repos")
    if repos is not None:
        if not isinstance(repos, list):
            errors.append(ValidationError(
                path="project.repos",
                message="'project.repos' must be a list.",
            ))
        else:
            for i, repo in enumerate(repos):
                if not isinstance(repo, dict):
                    errors.append(ValidationError(
                        path=f"project.repos[{i}]",
                        message="Each repo must be a mapping with 'name'.",
                    ))
                elif not repo.get("name"):
                    errors.append(ValidationError(
                        path=f"project.repos[{i}].name",
                        message="Repo must have a 'name' field.",
                    ))

    _check_unknown_keys(section, _KNOWN_PROJECT_KEYS, "project", errors)


def _validate_user(section: Any, errors: list[ValidationError]) -> None:
    if not isinstance(section, dict):
        errors.append(ValidationError(
            path="user",
            message="'user' must be a mapping.",
        ))
        return

    skill = section.get("skill_level")
    if skill is not None and skill not in _VALID_SKILL_LEVELS:
        errors.append(ValidationError(
            path="user.skill_level",
            message=f"Invalid skill level '{skill}'.",
            suggestion=f"Valid levels: {', '.join(sorted(_VALID_SKILL_LEVELS))}",
        ))

    _check_unknown_keys(section, _KNOWN_USER_KEYS, "user", errors)


def _validate_memory(section: Any, errors: list[ValidationError]) -> None:
    if not isinstance(section, dict):
        errors.append(ValidationError(
            path="memory",
            message="'memory' must be a mapping.",
        ))
        return

    backend = section.get("backend")
    if backend is not None and backend not in _VALID_BACKENDS:
        errors.append(ValidationError(
            path="memory.backend",
            message=f"Unknown memory backend '{backend}'.",
            suggestion=f"Valid backends: {', '.join(sorted(_VALID_BACKENDS))}",
        ))

    _check_unknown_keys(section, _KNOWN_MEMORY_KEYS, "memory", errors)


def _validate_agents(section: Any, errors: list[ValidationError]) -> None:
    if not isinstance(section, dict):
        errors.append(ValidationError(
            path="agents",
            message="'agents' must be a mapping.",
        ))
        return

    archetype = section.get("archetype")
    if archetype is not None and archetype not in _KNOWN_ARCHETYPES:
        errors.append(ValidationError(
            path="agents.archetype",
            message=f"Unknown archetype '{archetype}'.",
            suggestion=f"Valid archetypes: {', '.join(sorted(_KNOWN_ARCHETYPES))}",
        ))

    custom = section.get("custom_agents")
    if custom is not None:
        if not isinstance(custom, list):
            errors.append(ValidationError(
                path="agents.custom_agents",
                message="'agents.custom_agents' must be a list of strings.",
            ))
        else:
            seen: set[str] = set()
            for i, agent_id in enumerate(custom):
                if not isinstance(agent_id, str):
                    errors.append(ValidationError(
                        path=f"agents.custom_agents[{i}]",
                        message="Agent ID must be a string.",
                    ))
                elif agent_id in seen:
                    errors.append(ValidationError(
                        path=f"agents.custom_agents[{i}]",
                        message=f"Duplicate agent ID '{agent_id}'.",
                        suggestion="Remove the duplicate entry.",
                    ))
                else:
                    seen.add(agent_id)

    _check_unknown_keys(section, _KNOWN_AGENTS_KEYS, "agents", errors)


def _validate_installed_archetypes(
    section: Any, errors: list[ValidationError]
) -> None:
    if not isinstance(section, list):
        errors.append(ValidationError(
            path="installed_archetypes",
            message="'installed_archetypes' must be a list of strings.",
        ))
