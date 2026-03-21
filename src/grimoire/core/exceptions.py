"""Typed exception hierarchy for Grimoire Kit.

Every exception inherits from :class:`GrimoireError` so callers can
``except GrimoireError`` to catch all Grimoire-specific failures.
"""

from __future__ import annotations

__all__ = [
    "GrimoireAgentError",
    "GrimoireConfigError",
    "GrimoireError",
    "GrimoireMemoryError",
    "GrimoireMergeConflictError",
    "GrimoireMergeError",
    "GrimoireNetworkError",
    "GrimoireProjectError",
    "GrimoireRegistryError",
    "GrimoireTimeoutError",
    "GrimoireToolError",
    "GrimoireValidationError",
]


class GrimoireError(Exception):
    """Base exception for all Grimoire Kit errors.

    Attributes
    ----------
    error_code:
        Optional stable code (e.g. ``GR001``) for docs cross-reference.
    """

    error_code: str | None = None

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code

    def __str__(self) -> str:
        base = super().__str__()
        if self.error_code:
            return f"[{self.error_code}] {base}"
        return base


class GrimoireConfigError(GrimoireError):
    """Invalid or missing ``project-context.yaml`` configuration.

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

    Raised when an agent referenced in ``project-context.yaml`` cannot be loaded
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


class GrimoireMergeConflictError(GrimoireMergeError):
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


class GrimoireTimeoutError(GrimoireError):
    """Operation exceeded its time limit.

    Raised when a tool, registry call, or MCP operation takes longer
    than the configured timeout.
    """


class GrimoireNetworkError(GrimoireError):
    """Network communication failure.

    Raised when HTTP requests, MCP transport, or registry API calls
    fail due to connectivity or protocol errors.
    """
