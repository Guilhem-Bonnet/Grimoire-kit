"""Schema validation for project-context.yaml.

Validates structure, types, constraints, and references with
actionable error messages.

Usage::

    from grimoire.core.validator import validate_config

    errors = validate_config(data, project_root=Path("."))
    if errors:
        for e in errors:
            print(f"  {e}")

Backend
-------
The checks below (``_validate_config_python``) are the reference
implementation and the only one guaranteed to exist. ``grimoire_schema_core``
is an optional, PyO3-compiled Rust port of the same structural checks (see
``rust/grimoire-schema-core/``, issue #354). It is never required: nothing in
the published distribution depends on it, it ships no compiled wheel, and if
the import below fails ``validate_config`` runs the pure-Python path exactly
as before — silently, with no warning, same as the first port
(``grimoire.policies.engine``).

When the compiled module *is* present, ``validate_config()`` uses it by
default. ``GRIMOIRE_SCHEMA_BACKEND`` (sibling of ``GRIMOIRE_POLICIES_BACKEND``)
overrides that choice in both directions — ``"python"`` forces the reference
implementation, ``"rust"`` forces the compiled module and raises
:class:`~grimoire.core.exceptions.GrimoireValidationError` if it is not
available. See ``tests/unit/test_schema_validator_rust_parity.py``.

What crosses the PyO3 boundary and what does not
--------------------------------------------------
The Rust side receives the raw config data (whatever shape it has — a
mapping, a list, a scalar, even a malformed one: that is the entire point,
see the module docstring of ``rust/grimoire-schema-core/src/lib.rs``) and
returns a list of 5-tuples ``(path, message, suggestion, unknown_key,
keyset_id)``. For every error except an "unknown key" one, ``suggestion`` is
already the final string. For an unknown key, ``suggestion`` is left empty
and ``unknown_key``/``keyset_id`` are filled instead: the Rust core does not
reimplement ``difflib``-based "did you mean?" fuzzy matching (a UX nicety,
not validation logic — see the crate docstring), so
:func:`_validate_config_rust` below recomputes it with the very same
``_suggest_key`` used by the Python path, keyed off ``keyset_id`` via
``_KEYSETS``.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireValidationError
from grimoire.core.project_types import VALID_PROJECT_TYPES

__all__ = ["ValidationError", "rust_backend_available", "validate_config"]

try:
    import grimoire_schema_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None

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

_VALID_TYPES = frozenset(VALID_PROJECT_TYPES)

_VALID_SKILL_LEVELS = frozenset({"beginner", "intermediate", "expert"})

_VALID_BACKENDS = frozenset({
    "auto", "local", "lexical", "tantivy-local", "qdrant-local", "qdrant-server", "weaviate-server", "mempalace", "ollama",
})
_VALID_SHORT_TERM_BACKENDS = frozenset({"sqlite", "redis", "none"})
_VALID_LAYER_MODES = frozenset({
    "disabled", "planned", "sqlite-sidecar", "qdrant", "weaviate", "neo4j", "runtime-dashboard",
})

_KNOWN_ARCHETYPES = frozenset({
    "minimal", "web-app", "creative-studio", "fix-loop",
    "infra-ops", "meta", "stack", "features", "platform-engineering",
})

# Known keys per section for unknown-key detection
_KNOWN_TOP_KEYS = frozenset({
    "project", "user", "memory", "agents", "installed_archetypes", "proposals",
})

_KNOWN_PROJECT_KEYS = frozenset({
    "name", "description", "type", "metaphor", "stack", "repos",
})

_KNOWN_USER_KEYS = frozenset({
    "name", "language", "document_language", "skill_level",
})

_KNOWN_MEMORY_KEYS = frozenset({
    "backend", "vector_database", "retrieval_mode", "collection_prefix", "embedding_model", "qdrant_url",
    "weaviate_url", "weaviate_api_key_env", "weaviate_collection",
    "neo4j_uri", "neo4j_user", "neo4j_password_env", "neo4j_database",
    "migration_source_backend", "migration_target_backend", "migration_bundle_path",
    "mempalace_path", "ollama_url",
    "layer_profile", "short_term_backend", "redis_url", "knowledge_graph", "memory_graph", "code_graph",
    "task_memory", "visualization",
})

_KNOWN_AGENTS_KEYS = frozenset({
    "archetype", "custom_agents", "entry", "freshness_threshold_days",
})

_KNOWN_PROPOSALS_KEYS = frozenset({
    "threshold",
})

# Cle -> jeu de cles connues, indexe par le `keyset_id` que
# `grimoire_schema_core.validate_config` renvoie pour chaque erreur "Unknown
# key" (voir le docstring de module). "" ne devrait jamais etre utilise comme
# cle ici : reserve a `keyset_id == ""`, qui signifie "pas une erreur de cle
# inconnue" et ne passe jamais par ce dictionnaire (voir
# `_validate_config_rust`).
_KEYSETS: dict[str, frozenset[str]] = {
    "top": _KNOWN_TOP_KEYS,
    "project": _KNOWN_PROJECT_KEYS,
    "user": _KNOWN_USER_KEYS,
    "memory": _KNOWN_MEMORY_KEYS,
    "agents": _KNOWN_AGENTS_KEYS,
    "proposals": _KNOWN_PROPOSALS_KEYS,
}


def rust_backend_available() -> bool:
    """Whether the compiled ``grimoire_schema_core`` module is importable.

    Purely informational (used by tests and diagnostics) — ``validate_config``
    itself decides its backend on every call via :func:`_use_rust_backend`.
    """
    return _rust_core is not None


def _use_rust_backend() -> bool:
    """Resolve which backend ``validate_config`` should use for this call.

    Reads ``GRIMOIRE_SCHEMA_BACKEND`` fresh every time rather than once at
    import time, so tests can flip it with ``monkeypatch.setenv`` around a
    single call without reloading the module.
    """
    override = os.environ.get("GRIMOIRE_SCHEMA_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoireValidationError(
                "GRIMOIRE_SCHEMA_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_schema_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-schema-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoireValidationError(f"GRIMOIRE_SCHEMA_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None


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


def _check_enum_field(
    value: Any,
    valid: frozenset[str],
    path: str,
    unknown_label: str,
    valid_label: str,
    errors: list[ValidationError],
) -> None:
    """Validate a scalar "string enum" field (``project.type``,
    ``user.skill_level``, ``memory.backend``, the memory layer modes,
    ``agents.archetype``) without crashing on an unhashable value.

    ``value not in valid`` — the plain membership test this replaces —
    raises ``TypeError: unhashable type`` when ``value`` is a ``list`` or a
    ``dict``, before the comparison is even attempted. Mirrors
    ``check_enum_field`` in ``rust/grimoire-schema-core/src/lib.rs``, the
    Rust oracle this Python path must match (issue #354): a list or a
    mapping becomes an explicit "must be a string" validation error instead
    of an unhandled exception. Any other hashable scalar (bool/int/float)
    still crosses the membership test as-is — it was never the source of
    the crash.
    """
    if isinstance(value, (list, dict)):
        kind = "a list" if isinstance(value, list) else "a mapping"
        errors.append(ValidationError(
            path=path,
            message=f"'{path}' must be a string (got {kind}).",
        ))
        return
    if value not in valid:
        errors.append(ValidationError(
            path=path,
            message=f"{unknown_label} '{value}'.",
            suggestion=f"{valid_label}: {', '.join(sorted(valid))}",
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
    if _use_rust_backend():
        return _validate_config_rust(data)
    return _validate_config_python(data, project_root=project_root)


def _validate_config_rust(data: Any) -> list[ValidationError]:
    """Same contract as :func:`_validate_config_python`, delegated to the
    compiled core. See the module docstring for the tuple shape and why
    "did you mean?" suggestions are recomputed here rather than in Rust."""
    assert _rust_core is not None  # guarded by _use_rust_backend before this is called
    errors: list[ValidationError] = []
    for path, message, suggestion, unknown_key, keyset_id in _rust_core.validate_config(data):
        if keyset_id:
            suggestion = _suggest_key(unknown_key, _KEYSETS[keyset_id])
        errors.append(ValidationError(path=path, message=message, suggestion=suggestion))
    return errors


def _validate_config_python(
    data: Any,
    *,
    project_root: Path | None = None,
) -> list[ValidationError]:
    """Reference implementation of ``validate_config``, in pure Python."""
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

    if "proposals" in data:
        _validate_proposals(data["proposals"], errors)

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
    if ptype is not None:
        _check_enum_field(ptype, _VALID_TYPES, "project.type", "Unknown project type", "Valid types", errors)

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
                    continue
                repo_name = repo.get("name")
                if not repo_name:
                    errors.append(ValidationError(
                        path=f"project.repos[{i}].name",
                        message="Repo must have a 'name' field.",
                    ))
                elif not isinstance(repo_name, str):
                    # Present and truthy but not a string (e.g. `name: 123`) —
                    # schema.py declares `repos[].name` as a string.
                    errors.append(ValidationError(
                        path=f"project.repos[{i}].name",
                        message="Repo 'name' must be a string.",
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
    if skill is not None:
        _check_enum_field(skill, _VALID_SKILL_LEVELS, "user.skill_level", "Invalid skill level", "Valid levels", errors)

    # schema.py declares name/language/document_language as strings, but
    # unlike skill_level they were never type-checked at all.
    for key in ("name", "language", "document_language"):
        value = section.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(ValidationError(
                path=f"user.{key}",
                message=f"'user.{key}' must be a string.",
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
    if backend is not None:
        _check_enum_field(backend, _VALID_BACKENDS, "memory.backend", "Unknown memory backend", "Valid backends", errors)
    short_term_backend = section.get("short_term_backend")
    if short_term_backend is not None:
        _check_enum_field(
            short_term_backend,
            _VALID_SHORT_TERM_BACKENDS,
            "memory.short_term_backend",
            "Unknown short-term memory backend",
            "Valid short-term backends",
            errors,
        )
    for key in ("knowledge_graph", "memory_graph", "code_graph", "task_memory", "visualization"):
        mode = section.get(key)
        if mode is not None:
            _check_enum_field(mode, _VALID_LAYER_MODES, f"memory.{key}", "Unknown memory layer mode", "Valid modes", errors)

    _check_unknown_keys(section, _KNOWN_MEMORY_KEYS, "memory", errors)


def _validate_agents(section: Any, errors: list[ValidationError]) -> None:
    if not isinstance(section, dict):
        errors.append(ValidationError(
            path="agents",
            message="'agents' must be a mapping.",
        ))
        return

    archetype = section.get("archetype")
    if archetype is not None:
        _check_enum_field(
            archetype, _KNOWN_ARCHETYPES, "agents.archetype", "Unknown archetype", "Valid archetypes", errors
        )

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

    threshold = section.get("freshness_threshold_days")
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1):
        errors.append(ValidationError(
            path="agents.freshness_threshold_days",
            message="'agents.freshness_threshold_days' must be a positive integer (days).",
        ))

    _check_unknown_keys(section, _KNOWN_AGENTS_KEYS, "agents", errors)


def _validate_proposals(section: Any, errors: list[ValidationError]) -> None:
    """``proposals.threshold`` — the repetition count the déclencheur (#395) waits for.

    Never 1: a threshold that low would turn a single non-choice into a
    proposal, exactly what the issue refuses. An out-of-range value is a
    config error here, not silently clamped — the runtime clamp in
    :mod:`grimoire.proposals` is a last line of defence, not a substitute
    for telling the author their config does not mean what they wrote.
    """
    if not isinstance(section, dict):
        errors.append(ValidationError(
            path="proposals",
            message="'proposals' must be a mapping.",
        ))
        return

    threshold = section.get("threshold")
    if threshold is not None and (not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 2):
        errors.append(ValidationError(
            path="proposals.threshold",
            message="'proposals.threshold' must be an integer of at least 2.",
            suggestion="A single non-choice never earns a proposal — pick 2 or higher.",
        ))

    _check_unknown_keys(section, _KNOWN_PROPOSALS_KEYS, "proposals", errors)


def _validate_installed_archetypes(
    section: Any, errors: list[ValidationError]
) -> None:
    if not isinstance(section, list):
        errors.append(ValidationError(
            path="installed_archetypes",
            message="'installed_archetypes' must be a list of strings.",
        ))
        return

    # schema.py declares items as strings, but the container check above
    # never verified them.
    for i, item in enumerate(section):
        if not isinstance(item, str):
            errors.append(ValidationError(
                path=f"installed_archetypes[{i}]",
                message="Archetype identifier must be a string.",
            ))
