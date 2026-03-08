"""Typed exception hierarchy for Grimoire Kit.

Every exception inherits from :class:`GrimoireError` so callers can
``except GrimoireError`` to catch all Grimoire-specific failures.
"""

from __future__ import annotations


class GrimoireError(Exception):
    """Base exception for all Grimoire Kit errors."""


class GrimoireConfigError(GrimoireError):
    """Invalid or missing ``bmad.yaml`` configuration.

    Raised when the config file cannot be parsed, contains unknown keys,
    or fails schema validation.
    """


class GrimoireProjectError(GrimoireError):
    """Project not initialised or has an invalid structure.

    Raised when ``grimoire up`` or other project-scoped commands are run
    outside of a properly initialised Grimoire project.
    """


class GrimoireAgentError(GrimoireError):
    """Agent not found, persona invalid, or activation failure.

    Raised when an agent referenced in ``bmad.yaml`` cannot be loaded
    from built-in archetypes, the registry, or custom paths.
    """


class GrimoireToolError(GrimoireError):
    """A Grimoire tool failed during execution.

    Raised when a tool (context-router, harmony-check, etc.) encounters
    a runtime error that prevents it from producing a result.
    """


class GrimoireMergeError(GrimoireError):
    """General merge failure.

    Raised when ``grimoire merge`` or ``grimoire init`` on an existing project
    encounters an unrecoverable error during file merging.
    """


class GrimoireMergeConflict(GrimoireMergeError):  # noqa: N818 — semantic: it's a conflict, not an error
    """Unresolved merge conflict that requires user intervention.

    Carries the list of conflicting paths so the caller can present
    them to the user.
    """

    def __init__(self, message: str, conflicts: list[str] | None = None) -> None:
        super().__init__(message)
        self.conflicts: list[str] = conflicts or []


class GrimoireRegistryError(GrimoireError):
    """Registry operation failure (network, auth, package not found).

    Raised by the registry client when a search, install, or publish
    operation fails.
    """


class GrimoireMemoryError(GrimoireError):
    """Memory backend error (connection, serialisation, query failure).

    Raised when the memory system cannot read, write, or consolidate
    memories due to a backend issue.
    """


class GrimoireValidationError(GrimoireError):
    """Schema or data validation failure.

    Raised when DNA files, agent definitions, or other structured data
    fail validation against their expected schema.
    """
