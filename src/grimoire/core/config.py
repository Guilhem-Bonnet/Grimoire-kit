"""Typed configuration for Grimoire projects.

Loads and validates ``project-context.yaml`` into typed dataclasses.
Unknown sections are preserved in ``extra`` so downstream tools can
access them without requiring schema changes here.

Usage::

    from grimoire.core.config import GrimoireConfig

    cfg = GrimoireConfig.from_yaml(Path("project-context.yaml"))
    print(cfg.project.name)
    print(cfg.user.language)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.error_codes import CONFIG_MISSING_SECTION, CONFIG_NOT_FOUND, CONFIG_PARSE_ERROR
from grimoire.core.exceptions import GrimoireConfigError

__all__ = [
    "AgentsConfig",
    "GrimoireConfig",
    "MemoryConfig",
    "ProjectConfig",
    "RepoConfig",
    "UserConfig",
]

# ── Sub-sections ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class RepoConfig:
    """A single repository entry."""

    name: str
    path: str = "."
    default_branch: str = "main"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepoConfig:
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", ".")),
            default_branch=str(data.get("default_branch", "main")),
        )


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """The ``project:`` section."""

    name: str
    description: str = ""
    type: str = "webapp"
    metaphor: str = ""
    stack: tuple[str, ...] = ()
    repos: tuple[RepoConfig, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        raw_stack = data.get("stack") or []
        raw_repos = data.get("repos") or []
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            type=str(data.get("type", "webapp")),
            metaphor=str(data.get("metaphor", "")),
            stack=tuple(str(s) for s in raw_stack),
            repos=tuple(RepoConfig.from_dict(r) for r in raw_repos),
        )


_VALID_SKILL_LEVELS = frozenset({"beginner", "intermediate", "expert"})


@dataclass(frozen=True, slots=True)
class UserConfig:
    """The ``user:`` section."""

    name: str = ""
    language: str = "Français"
    document_language: str = "Français"
    skill_level: str = "intermediate"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserConfig:
        skill = str(data.get("skill_level", "intermediate"))
        if skill not in _VALID_SKILL_LEVELS:
            raise GrimoireConfigError(
                f"Invalid skill_level '{skill}', expected one of: {sorted(_VALID_SKILL_LEVELS)}",
                error_code=CONFIG_PARSE_ERROR.code,
            )
        return cls(
            name=str(data.get("name", "")),
            language=str(data.get("language", "Français")),
            document_language=str(data.get("document_language", "Français")),
            skill_level=skill,
        )


_VALID_BACKENDS = frozenset({
    "auto", "local", "qdrant-local", "qdrant-server", "ollama",
})


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    """The ``memory:`` section."""

    backend: str = "auto"
    collection_prefix: str = "grimoire"
    embedding_model: str = ""
    qdrant_url: str = ""
    ollama_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryConfig:
        backend = str(data.get("backend", "auto"))
        if backend not in _VALID_BACKENDS:
            raise GrimoireConfigError(
                f"Invalid memory backend '{backend}', expected one of: {sorted(_VALID_BACKENDS)}",
                error_code=CONFIG_PARSE_ERROR.code,
            )
        return cls(
            backend=backend,
            collection_prefix=str(data.get("collection_prefix", "grimoire")),
            embedding_model=str(data.get("embedding_model", "")),
            qdrant_url=str(data.get("qdrant_url", "")),
            ollama_url=str(data.get("ollama_url", "")),
        )


@dataclass(frozen=True, slots=True)
class AgentsConfig:
    """The ``agents:`` section."""

    archetype: str = "minimal"
    custom_agents: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentsConfig:
        raw = data.get("custom_agents") or []
        return cls(
            archetype=str(data.get("archetype", "minimal")),
            custom_agents=tuple(str(a) for a in raw),
        )


# ── Root Config ───────────────────────────────────────────────────────────────

_KNOWN_TOP_KEYS = frozenset({
    "project", "user", "memory", "agents", "installed_archetypes",
})


@dataclass(frozen=True, slots=True)
class GrimoireConfig:
    """Root Grimoire project configuration.

    Immutable after construction.  Unrecognised top-level keys are stored
    in ``extra`` so tools can access them without schema changes.
    """

    project: ProjectConfig
    user: UserConfig = field(default_factory=UserConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    installed_archetypes: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    # ── Validation ────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return a list of config warnings (empty means valid).

        Checks semantic consistency that goes beyond parse-time validation.
        """
        issues: list[str] = []

        if self.memory.backend == "qdrant-server" and not self.memory.qdrant_url:
            issues.append("Memory backend is 'qdrant-server' but qdrant_url is empty")
        if self.memory.backend == "ollama" and not self.memory.ollama_url:
            issues.append("Memory backend is 'ollama' but ollama_url is empty")
        if not self.project.name.strip():
            issues.append("Project name is blank")

        return issues

    # ── Factory methods ───────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GrimoireConfig:
        """Build a :class:`GrimoireConfig` from a parsed YAML dict.

        Raises :class:`GrimoireConfigError` on validation failures.
        """
        if not isinstance(data, dict):
            raise GrimoireConfigError("Config root must be a YAML mapping", error_code=CONFIG_PARSE_ERROR.code)

        raw_project = data.get("project")
        if not isinstance(raw_project, dict) or not raw_project.get("name"):
            raise GrimoireConfigError(
                "Config must contain a 'project' section with a 'name' field",
                error_code=CONFIG_MISSING_SECTION.code,
            )

        raw_archetypes = data.get("installed_archetypes") or []
        extra = {k: v for k, v in data.items() if k not in _KNOWN_TOP_KEYS}

        return cls(
            project=ProjectConfig.from_dict(raw_project),
            user=UserConfig.from_dict(data.get("user") or {}),
            memory=MemoryConfig.from_dict(data.get("memory") or {}),
            agents=AgentsConfig.from_dict(data.get("agents") or {}),
            installed_archetypes=tuple(str(a) for a in raw_archetypes),
            extra=extra,
        )

    @classmethod
    def from_yaml(cls, path: Path) -> GrimoireConfig:
        """Load config from a YAML file.

        Raises :class:`GrimoireConfigError` if the file is missing, unreadable,
        or contains invalid YAML.
        """
        if not path.is_file():
            raise GrimoireConfigError(f"Config file not found: {path}", error_code=CONFIG_NOT_FOUND.code)

        try:
            from ruamel.yaml import YAML

            yaml = YAML(typ="safe")
            raw = yaml.load(path)
        except ImportError:
            # Fallback to PyYAML if ruamel not available
            try:
                import yaml as pyyaml  # type: ignore[import-untyped]

                with open(path) as fh:
                    raw = pyyaml.safe_load(fh)
            except Exception as exc:
                raise GrimoireConfigError(f"Cannot parse '{path}': {exc}", error_code=CONFIG_PARSE_ERROR.code) from exc
        except Exception as exc:
            raise GrimoireConfigError(f"Cannot parse '{path}': {exc}", error_code=CONFIG_PARSE_ERROR.code) from exc

        if raw is None:
            raise GrimoireConfigError(f"Config file is empty: {path}", error_code=CONFIG_PARSE_ERROR.code)

        return cls.from_dict(raw)

    @classmethod
    def find_and_load(cls, start: Path | None = None) -> GrimoireConfig:
        """Walk up the directory tree to find ``project-context.yaml``.

        Starts from *start* (default: cwd) and searches upward.
        Raises :class:`GrimoireConfigError` if no config file is found.
        """
        current = (start or Path.cwd()).resolve()
        for parent in [current, *current.parents]:
            candidate = parent / "project-context.yaml"
            if candidate.is_file():
                return cls.from_yaml(candidate)
        raise GrimoireConfigError(
            f"No 'project-context.yaml' found in {current} or any parent directory",
            error_code=CONFIG_NOT_FOUND.code,
        )
