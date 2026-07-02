"""Central project class — single entry point to a Grimoire project.

Usage::

    from grimoire.core.project import GrimoireProject

    project = GrimoireProject(Path("."))
    print(project.config.project.name)
    print(project.status())
    print(project.agents())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError, GrimoireProjectError
from grimoire.core.project_layout import ProjectLayout, detect_project_layout
from grimoire.core.resolver import PathResolver

__all__ = ["AgentInfo", "GrimoireProject", "ProjectContext", "ProjectStatus"]

# ── Data models ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AgentInfo:
    """Lightweight descriptor for a deployed agent."""

    id: str
    name: str
    path: Path
    source: str = "local"  # local | builtin | registry


@dataclass(frozen=True, slots=True)
class ProjectStatus:
    """Snapshot of a project's operational state."""

    initialized: bool
    config_valid: bool
    agents_count: int
    custom_agents_count: int
    memory_backend: str
    archetype: str
    layout: str
    directories_ok: tuple[str, ...]
    directories_missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """Context payload for agents — project metadata + structure."""

    name: str
    project_type: str
    stack: tuple[str, ...]
    user_name: str
    language: str
    archetype: str
    file_count: int
    directory_count: int
    extra: dict[str, Any] = field(default_factory=dict)


# ── Project class ─────────────────────────────────────────────────────────────


class GrimoireProject:
    """Entry point for interacting with a Grimoire project.

    Parameters
    ----------
    root :
        Path to the project directory (must contain ``project-context.yaml``).
    strict :
        If ``True`` (default), raise :class:`GrimoireProjectError` when the
        project is not properly initialised.  Set to ``False`` for
        read-only inspection of partially-initialised projects.
    """

    def __init__(self, root: Path, *, strict: bool = True) -> None:
        self._root = root.resolve()
        self._config_path = self._root / "project-context.yaml"
        self._layout = detect_project_layout(self._root)

        if not self._config_path.is_file():
            if strict:
                raise GrimoireProjectError(
                    f"Not a Grimoire project: {self._root} "
                    "(no project-context.yaml found)"
                )
            self._config: GrimoireConfig | None = None
        else:
            try:
                self._config = GrimoireConfig.from_yaml(self._config_path)
            except GrimoireConfigError as exc:
                if strict:
                    raise GrimoireProjectError(
                        f"Invalid project config: {exc}"
                    ) from exc
                self._config = None

        self._resolver = PathResolver(self._root)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        """Project root directory."""
        return self._root

    @property
    def layout(self) -> ProjectLayout:
        """Resolved layout for this project root."""
        return self._layout

    @property
    def grimoire_dir(self) -> Path:
        """Path to the active Grimoire runtime directory."""
        return self._layout.grimoire_path(self._root)

    @property
    def config_path(self) -> Path:
        """Path to ``project-context.yaml``."""
        return self._config_path

    @property
    def config(self) -> GrimoireConfig:
        """Loaded and validated config.

        Raises :class:`GrimoireProjectError` if the config is unavailable.
        """
        if self._config is None:
            raise GrimoireProjectError("Project configuration not loaded")
        return self._config

    @property
    def resolver(self) -> PathResolver:
        """Path / template resolver bound to this project."""
        return self._resolver

    # ── Query methods ─────────────────────────────────────────────────

    def is_initialized(self) -> bool:
        """Check whether the project has a valid Grimoire installation."""
        return (
            self._config is not None
            and self._config_path.is_file()
            and self.grimoire_dir.is_dir()
        )

    def agents(self) -> list[AgentInfo]:
        """List agents deployed in this project."""
        agents: list[AgentInfo] = []
        seen_ids: set[str] = set()

        for agent_file in self._layout.agent_files(self._root):
            if agent_file.stem in seen_ids:
                continue
            seen_ids.add(agent_file.stem)
            agents.append(AgentInfo(
                id=agent_file.stem,
                name=agent_file.stem.replace("-", " ").title(),
                path=agent_file,
                source="local",
            ))

        # Custom agents from config
        if self._config is not None:
            for agent_id in self._config.agents.custom_agents:
                if not any(a.id == agent_id for a in agents):
                    agents.append(AgentInfo(
                        id=agent_id,
                        name=agent_id.replace("-", " ").title(),
                        path=self._root / agent_id,
                        source="custom",
                    ))

        return agents

    def status(self) -> ProjectStatus:
        """Return a full status snapshot of this project."""
        ok_dirs: list[str] = []
        missing_dirs: list[str] = []
        for d in self._layout.required_dirs:
            if (self._root / d).is_dir():
                ok_dirs.append(d)
            else:
                missing_dirs.append(d)

        agent_list = self.agents()
        custom_count = sum(1 for a in agent_list if a.source == "custom")

        return ProjectStatus(
            initialized=self.is_initialized(),
            config_valid=self._config is not None,
            agents_count=len(agent_list),
            custom_agents_count=custom_count,
            memory_backend=self._config.memory.backend if self._config else "unknown",
            archetype=self._config.agents.archetype if self._config else "unknown",
            layout=self._layout.name,
            directories_ok=tuple(ok_dirs),
            directories_missing=tuple(missing_dirs),
        )

    def context(self) -> ProjectContext:
        """Build a context payload for agent consumption."""
        cfg = self.config  # raises if not loaded

        # Count files and directories (shallow, skip hidden/runtime dirs)
        file_count = 0
        dir_count = 0
        for item in self._root.iterdir():
            if item.name.startswith((".", "_grimoire")):
                continue
            if item.is_file():
                file_count += 1
            elif item.is_dir():
                dir_count += 1

        return ProjectContext(
            name=cfg.project.name,
            project_type=cfg.project.type,
            stack=cfg.project.stack,
            user_name=cfg.user.name,
            language=cfg.user.language,
            archetype=cfg.agents.archetype,
            file_count=file_count,
            directory_count=dir_count,
            extra=cfg.extra,
        )
