"""Grimoire error codes — stable identifiers for documentation and tooling.

Each code follows the pattern ``GRxyz`` where *x* is the category,
*yz* the specific error.  These codes can be referenced in docs,
error messages, and ``--help`` output.

Categories::

    GR0xx  Configuration
    GR1xx  Agents / Registry
    GR2xx  Memory
    GR3xx  Network / MCP
    GR4xx  Tools
    GR5xx  Validation / Merge
"""

from __future__ import annotations

__all__ = ["CODES", "ErrorCode"]


class ErrorCode:
    """Lightweight error code descriptor."""

    __slots__ = ("code", "summary")

    def __init__(self, code: str, summary: str) -> None:
        self.code = code
        self.summary = summary

    def __repr__(self) -> str:
        return f"ErrorCode({self.code!r}, {self.summary!r})"

    def __str__(self) -> str:
        return f"{self.code}: {self.summary}"


# ── Configuration GR0xx ───────────────────────────────────────────────────────

CONFIG_NOT_FOUND = ErrorCode("GR001", "project-context.yaml not found")
CONFIG_PARSE_ERROR = ErrorCode("GR002", "Invalid YAML syntax in config")
CONFIG_MISSING_SECTION = ErrorCode("GR003", "Required section missing from config")

# ── Agents / Registry GR1xx ───────────────────────────────────────────────────

AGENT_NOT_FOUND = ErrorCode("GR101", "Agent not found in registry")
ARCHETYPE_NOT_FOUND = ErrorCode("GR102", "Archetype not found")
REGISTRY_LOAD_FAILED = ErrorCode("GR103", "Failed to load registry")

# ── Memory GR2xx ──────────────────────────────────────────────────────────────

MEMORY_BACKEND_ERROR = ErrorCode("GR201", "Memory backend operation failed")
MEMORY_CONNECTION_FAILED = ErrorCode("GR202", "Cannot connect to memory backend")

# ── Network / MCP GR3xx ───────────────────────────────────────────────────────

NETWORK_TIMEOUT = ErrorCode("GR301", "Network operation timed out")
NETWORK_ERROR = ErrorCode("GR302", "Network communication failed")
MCP_CONNECTION_FAILED = ErrorCode("GR303", "MCP server connection failed")

# ── Tools GR4xx ───────────────────────────────────────────────────────────────

TOOL_EXECUTION_FAILED = ErrorCode("GR401", "Tool execution failed")
TOOL_NOT_FOUND = ErrorCode("GR402", "Tool not found")

# ── Validation / Merge GR5xx ──────────────────────────────────────────────────

VALIDATION_FAILED = ErrorCode("GR501", "Schema or data validation failure")
MERGE_CONFLICT = ErrorCode("GR502", "Unresolved merge conflict")

# All codes for introspection
CODES: dict[str, ErrorCode] = {
    v.code: v for k, v in globals().items() if isinstance(v, ErrorCode)
}
