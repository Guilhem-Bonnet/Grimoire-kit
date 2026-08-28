"""Primitives partagées par les vérificateurs du standard agentique.

Extrait de agentic_standard pour que les vérificateurs vivent dans leur propre
paquet sans cycle d'import : ce module ne dépend d'aucun symbole de son appelant.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

BOARD_STATES = {
    "proposed",
    "ready",
    "in_progress",
    "blocked",
    "review",
    "accepted",
    "released",
    "archived",
}

REQUIRED_MEMORY_TYPES = {
    "session",
    "task",
    "project",
    "workspace",
    "organization",
    "procedural",
    "semantic",
    "episodic",
    "long_term",
    "external_knowledge_cache",
}

REQUIRED_DECISION_TYPES = {
    "task_prioritization",
    "context_source_selection",
    "memory_injection",
    "provider_routing",
    "agent_role_routing",
    "tool_authorization",
    "state_transition",
    "release_authorization",
}

KNOWN_HOOK_PHASES = {
    "pre_context_build",
    "post_context_build",
    "pre_provider_call",
    "post_provider_call",
    "pre_tool_call",
    "post_tool_call",
    "pre_state_transition",
    "post_state_transition",
    "pre_release",
    "on_failure",
    "on_rollback",
}

KNOWN_HOOK_ACTIONS = {
    "allow",
    "warn",
    "block",
    "redact",
    "reroute",
    "require_evidence",
    "escalate",
    "create_remediation",
    "rollback",
}

MEMORY_OS_REQUIRED_TARGET = {
    "hot_memory": "redis",
    "semantic_memory": "weaviate-server",
    "graph_projection": "neo4j",
    "sidecar": "sqlite",
}

MEMORY_OS_LEGACY_VECTOR_SOURCES = {"qdrant-local", "qdrant-server"}

MEMORY_OS_REQUIRED_PROMOTION_GATES = {
    "hot_memory_ttl_declared",
    "semantic_write_has_evidence",
    "graph_projection_has_source_refs",
    "qdrant_migration_bundle_verified",
}

MEMORY_OS_REQUIRED_RUNTIME_COMMANDS = {
    "grimoire memory gate",
    "grimoire memory migrate verify",
    "grimoire memory graph verify",
}


@dataclass(frozen=True, slots=True)
class StandardProfile:
    """Operational profile declared in ``profile-map.yaml``."""

    id: str
    display_name: str
    required_artifacts: tuple[str, ...]
    mapped_capabilities: tuple[str, ...]
    minimum_evidence: tuple[str, ...]


@dataclass(slots=True)
class StandardCheck:
    """One content or structure check emitted by verification."""

    id: str
    severity: str
    message: str
    path: Path | None = None

    @property
    def is_error(self) -> bool:
        """True when this check must fail verification."""
        return self.severity == "error"


@dataclass(slots=True)
class StandardVerificationResult:
    """Verification result for a standard-aware project."""

    profile: str
    project_root: Path
    present: list[Path] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)
    invalid_yaml: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[StandardCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when mandatory files exist, parseable YAML is valid, and no check fails."""
        return not self.missing and not self.invalid_yaml and not any(check.is_error for check in self.checks)

    @property
    def warning_count(self) -> int:
        """Number of warning checks."""
        return sum(1 for check in self.checks if check.severity == "warning") + len(self.warnings)

    @property
    def error_count(self) -> int:
        """Number of error checks, including missing files and invalid YAML."""
        return len(self.missing) + len(self.invalid_yaml) + sum(1 for check in self.checks if check.is_error)


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.width = 4096
    return yaml


def _is_inside_root(root: Path, path: Path) -> bool:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _load_yaml_file(root: Path, rel_path: Path, result: StandardVerificationResult) -> Any | None:
    path = root / rel_path
    if not path.is_file():
        return None
    try:
        data = _yaml().load(path)
    except YAMLError as exc:
        result.invalid_yaml.append(rel_path)
        result.checks.append(
            StandardCheck(
                id="yaml.invalid",
                severity="error",
                path=rel_path,
                message=f"{rel_path}: {exc}",
            )
        )
        return None
    if data is None:
        result.invalid_yaml.append(rel_path)
        result.checks.append(
            StandardCheck(
                id="yaml.empty",
                severity="error",
                path=rel_path,
                message=f"{rel_path}: empty YAML document",
            )
        )
    return data


def _add_check(
    result: StandardVerificationResult,
    check_id: str,
    severity: str,
    message: str,
    *,
    path: Path | None = None,
) -> None:
    result.checks.append(StandardCheck(id=check_id, severity=severity, message=message, path=path))


def _text_file(root: Path, rel_path: Path) -> str:
    path = root / rel_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _require_keys(
    result: StandardVerificationResult,
    *,
    path: Path,
    check_prefix: str,
    data: dict[str, Any],
    keys: Iterable[str],
    severity: str = "error",
) -> None:
    for key in keys:
        if key not in data or data[key] in (None, ""):
            _add_check(
                result,
                f"{check_prefix}.{key}_missing",
                severity,
                f"{path}: required key {key!r} is missing.",
                path=path,
            )


def _strict_memory_os_profile(profile: StandardProfile) -> bool:
    return profile.id in {"governed", "production"}


def _memory_os_severity(profile: StandardProfile) -> str:
    return "error" if _strict_memory_os_profile(profile) else "warning"
