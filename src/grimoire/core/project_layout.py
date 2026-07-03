"""Project layout helpers for legacy and runtime Grimoire trees."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "LEGACY_PROJECT_LAYOUT",
    "RUNTIME_PROJECT_LAYOUT",
    "ProjectLayout",
    "detect_project_layout",
]


@dataclass(frozen=True, slots=True)
class ProjectLayout:
    """Resolved layout for a Grimoire project root."""

    name: str
    grimoire_dir: str
    output_dir: str
    memory_dir: str

    @property
    def required_dirs(self) -> tuple[str, ...]:
        """Directories required by this layout."""
        return (self.grimoire_dir, self.output_dir, self.memory_dir)

    def grimoire_path(self, root: Path) -> Path:
        """Return the active Grimoire directory path for a project root."""
        return root / self.grimoire_dir

    def memory_path(self, root: Path) -> Path:
        """Return the active memory directory path for a project root."""
        return root / self.memory_dir

    def agent_files(self, root: Path) -> list[Path]:
        """Return agent definition files visible for this layout."""
        grimoire_root = self.grimoire_path(root)
        if not grimoire_root.is_dir():
            return []

        if self.name == "runtime":
            return sorted(
                path
                for path in grimoire_root.rglob("*.md")
                if path.is_file() and path.parent.name == "agents"
            )

        agent_files: list[Path] = []
        for rel in ("agents", "_config/agents", "_config/custom/agents"):
            agent_dir = grimoire_root / rel
            if agent_dir.is_dir():
                agent_files.extend(
                    sorted(path for path in agent_dir.iterdir() if path.is_file() and path.suffix == ".md")
                )
        return agent_files


LEGACY_PROJECT_LAYOUT = ProjectLayout(
    name="legacy",
    grimoire_dir="_grimoire",
    output_dir="_grimoire-output",
    memory_dir="_grimoire/_memory",
)

RUNTIME_PROJECT_LAYOUT = ProjectLayout(
    name="runtime",
    grimoire_dir="_grimoire-runtime",
    output_dir="_grimoire-runtime-output",
    memory_dir="_grimoire-runtime/_memory",
)


def detect_project_layout(root: Path) -> ProjectLayout:
    """Return the preferred project layout for a given root.

    Runtime paths win whenever they are already present. This preserves
    compatibility with legacy projects while making active runtime projects the
    source of truth.
    """
    if any((root / rel).exists() for rel in RUNTIME_PROJECT_LAYOUT.required_dirs):
        return RUNTIME_PROJECT_LAYOUT
    return LEGACY_PROJECT_LAYOUT