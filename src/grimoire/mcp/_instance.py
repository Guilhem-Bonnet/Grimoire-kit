"""Grimoire MCP — shared FastMCP instance, dataclasses and constants."""

from __future__ import annotations

from dataclasses import dataclass

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _exc:
    msg = "MCP SDK not installed. Run: pip install grimoire-kit[mcp]"
    raise ImportError(msg) from _exc

# ── Singleton FastMCP instance ────────────────────────────────────────────────

mcp = FastMCP(
    name="grimoire",
    instructions="Grimoire Kit — Composable AI agent platform. "
    "Use these tools to inspect and manage Grimoire projects.",
)

# ── Shared dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _KnowledgeScope:
    """Curated scope definition for repo knowledge search."""

    name: str
    patterns: tuple[str, ...]
    weight: int


@dataclass(frozen=True, slots=True)
class _CommandRecommendation:
    """Structured validation or execution recommendation."""

    kind: str
    value: str
    reason: str
    priority: int


# ── Knowledge scope registry ──────────────────────────────────────────────────

_REPO_KNOWLEDGE_SCOPES: dict[str, _KnowledgeScope] = {
    "docs": _KnowledgeScope(
        name="docs",
        patterns=(
            "README.md",
            "CHANGELOG.md",
            "docs/**/*.md",
            "grimoire-kit/README.md",
            "grimoire-kit/ARCHITECTURE.md",
            "grimoire-kit/docs/**/*.md",
        ),
        weight=25,
    ),
    "plans": _KnowledgeScope(
        name="plans",
        patterns=(
            "docs/exploitation/**/*.md",
            "_grimoire-runtime-output/planning-artifacts/**/*.md",
            "_grimoire-runtime-output/implementation-artifacts/**/*.md",
        ),
        weight=35,
    ),
    "skills": _KnowledgeScope(
        name="skills",
        patterns=(".github/skills/**/SKILL.md",),
        weight=30,
    ),
    "instructions": _KnowledgeScope(
        name="instructions",
        patterns=(".github/instructions/**/*.md",),
        weight=28,
    ),
    "agents": _KnowledgeScope(
        name="agents",
        patterns=(".github/agents/**/*.md", "_grimoire-runtime/**/agents/**/*.md"),
        weight=20,
    ),
    "prompts": _KnowledgeScope(
        name="prompts",
        patterns=(".github/prompts/**/*.md", "_grimoire-runtime/**/workflows/**/*.md"),
        weight=18,
    ),
    "runtime": _KnowledgeScope(
        name="runtime",
        patterns=(
            "project-context.yaml",
            "grimoire-kit/project-context.yaml",
            "_grimoire-runtime/core/config.yaml",
            "_grimoire-runtime/_config/**/*.yaml",
            "_grimoire-runtime/_config/**/*.csv",
            ".vscode/*.json",
        ),
        weight=30,
    ),
}
_DEFAULT_REPO_KNOWLEDGE_SCOPES = ("docs", "plans", "skills", "instructions", "runtime")
_MAX_KNOWLEDGE_FILE_BYTES = 262_144

# ── MCP trusted remote hosts ──────────────────────────────────────────────────

_MCP_TRUSTED_REMOTE_HOSTS: dict[str, dict[str, str]] = {
    "api.githubcopilot.com": {
        "product": "GitHub MCP",
        "trust_level": "trusted-remote",
        "mutability": "read-write",
        "auth_mode": "client-managed",
    },
    "mcp.context7.com": {
        "product": "Context7",
        "trust_level": "trusted-remote",
        "mutability": "read-mostly",
        "auth_mode": "anonymous-or-rate-limited",
    },
}
_DEFAULT_MCP_POLICY_PATH = "_grimoire-runtime/_config/mcp-policy.yaml"
