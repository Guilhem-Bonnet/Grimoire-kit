"""Central project class — single entry point to a BMAD project.

Usage::

    from bmad.core.project import BmadProject

    project = BmadProject(Path("."))
    print(project.config.project.name)
    print(project.status())
    print(project.agents())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad.core.config import BmadConfig
from bmad.core.exceptions import BmadConfigError, BmadProjectError
from bmad.core.resolver import PathResolver

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


# ── Required directories ──────────────────────────────────────────────────────

_EXPECTED_DIRS = ("_bmad", "_bmad-output", "_bmad/_memory")


# ── Project class ─────────────────────────────────────────────────────────────


class BmadProject:
    """Entry point for interacting with a BMAD project.

    Parameters
    ----------
    root :
        Path to the project directory (must contain ``project-context.yaml``).
    strict :
        If ``True`` (default), raise :class:`BmadProjectError` when the
        project is not properly initialised.  Set to ``False`` for
        read-only inspection of partially-initialised projects.
    """

    def __init__(self, root: Path, *, strict: bool = True) -> None:
        self._root = root.resolve()
        self._config_path = self._root / "project-context.yaml"

        if not self._config_path.is_file():
            if strict:
                raise BmadProjectError(
                    f"Not a BMAD project: {self._root} "
                    "(no project-context.yaml found)"
                )
            self._config: BmadConfig | None = None
        else:
            try:
                self._config = BmadConfig.from_yaml(self._config_path)
            except BmadConfigError as exc:
                if strict:
                    raise BmadProjectError(
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
    def bmad_dir(self) -> Path:
        """Path to ``_bmad/``."""
        return self._root / "_bmad"

    @property
    def config_path(self) -> Path:
        """Path to ``project-context.yaml``."""
        return self._config_path

    @property
    def config(self) -> BmadConfig:
        """Loaded and validated config.

        Raises :class:`BmadProjectError` if the config is unavailable.
        """
        if self._config is None:
            raise BmadProjectError("Project configuration not loaded")
        return self._config

    @property
    def resolver(self) -> PathResolver:
        """Path / template resolver bound to this project."""
        return self._resolver

    # ── Query methods ─────────────────────────────────────────────────

    def is_initialized(self) -> bool:
        """Check whether the project has a valid BMAD installation."""
        return (
            self._config is not None
            and self._config_path.is_file()
            and self.bmad_dir.is_dir()
        )

    def agents(self) -> list[AgentInfo]:
        """List agents deployed in this project."""
        agents: list[AgentInfo] = []

        # Scan _bmad/agents/, legacy _bmad/_config/agents/, and _bmad/_config/custom/agents/
        for agents_dir_name in ("agents", "_config/agents", "_config/custom/agents"):
            agents_dir = self.bmad_dir / agents_dir_name
            if agents_dir.is_dir():
                for f in sorted(agents_dir.iterdir()):
                    if f.suffix == ".md" and f.is_file():
                        agents.append(AgentInfo(
                            id=f.stem,
                            name=f.stem.replace("-", " ").title(),
                            path=f,
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
        for d in _EXPECTED_DIRS:
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
            directories_ok=tuple(ok_dirs),
            directories_missing=tuple(missing_dirs),
        )

    def context(self) -> ProjectContext:
        """Build a context payload for agent consumption."""
        cfg = self.config  # raises if not loaded

        # Count files and directories (shallow, skip hidden/bmad dirs)
        file_count = 0
        dir_count = 0
        for item in self._root.iterdir():
            if item.name.startswith((".", "_bmad")):
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
