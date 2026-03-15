"""Non-destructive merge engine for Grimoire projects.

Analyses a source Grimoire installation against a target project to
detect conflicts before any files are modified.

Usage::

    engine = MergeEngine(source=Path("template"), target=Path("my-project"))
    plan = engine.analyze()
    if plan.conflicts:
        print("Conflicts found!")
    else:
        result = engine.execute(plan)
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.exceptions import GrimoireMergeError

# ── Data models ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MergeConflict:
    """A file-level conflict between source and target."""

    path: str  # relative path
    resolution: str = "skip"  # skip | overwrite | rename


@dataclass(frozen=True, slots=True)
class MergePlan:
    """Complete merge plan produced by :meth:`MergeEngine.analyze`."""

    files_to_create: tuple[str, ...]
    directories_to_create: tuple[str, ...]
    conflicts: tuple[MergeConflict, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Outcome of executing a merge plan."""

    files_created: tuple[str, ...]
    files_skipped: tuple[str, ...]
    directories_created: tuple[str, ...]
    log_path: Path | None = None


# ── Paths that commonly conflict ──────────────────────────────────────────────

_CONFLICT_SENSITIVE = frozenset({
    ".github/copilot-instructions.md",
    ".github/AGENTS.md",
    ".pre-commit-config.yaml",
})


# ── Engine ────────────────────────────────────────────────────────────────────


class MergeEngine:
    """Analyse and execute non-destructive merges.

    Parameters
    ----------
    source :
        Directory containing the Grimoire files to merge (e.g. archetype output).
    target :
        Target project directory.
    """

    def __init__(self, source: Path, target: Path) -> None:
        self._source = source.resolve()
        self._target = target.resolve()

        if not self._source.is_dir():
            raise GrimoireMergeError(f"Source directory does not exist: {self._source}")
        if not self._target.is_dir():
            raise GrimoireMergeError(f"Target directory does not exist: {self._target}")

    @property
    def source(self) -> Path:
        return self._source

    @property
    def target(self) -> Path:
        return self._target

    # ── Analysis ──────────────────────────────────────────────────────

    def analyze(self) -> MergePlan:
        """Scan source and target to build a merge plan.

        This method does NOT modify any files.
        """
        files_to_create: list[str] = []
        conflicts: list[MergeConflict] = []
        dirs_to_create: list[str] = []
        warnings: list[str] = []

        for src_file in sorted(self._source.rglob("*")):
            if not src_file.is_file():
                continue

            rel = str(src_file.relative_to(self._source))
            target_file = self._target / rel

            if target_file.exists():
                # Known sensitive file
                resolution = "skip"
                if rel in _CONFLICT_SENSITIVE:
                    warnings.append(
                        f"Sensitive file already exists: {rel} — will be skipped."
                    )
                conflicts.append(MergeConflict(path=rel, resolution=resolution))
            else:
                files_to_create.append(rel)
                # Check if parent dir needs creation
                rel_parent = str(target_file.parent.relative_to(self._target))
                if rel_parent != "." and not target_file.parent.is_dir() and rel_parent not in dirs_to_create:
                    dirs_to_create.append(rel_parent)

        return MergePlan(
            files_to_create=tuple(files_to_create),
            directories_to_create=tuple(dirs_to_create),
            conflicts=tuple(conflicts),
            warnings=tuple(warnings),
        )

    # ── Execution ─────────────────────────────────────────────────────

    def execute(
        self,
        plan: MergePlan,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> MergeResult:
        """Execute a merge plan.

        Parameters
        ----------
        plan :
            The plan from :meth:`analyze`.
        dry_run :
            If ``True``, return what would happen without writing files.
        force :
            If ``True``, overwrite conflicting files instead of skipping.
        """
        created: list[str] = []
        skipped: list[str] = []
        dirs_created: list[str] = []

        # Create directories
        for d in plan.directories_to_create:
            if not dry_run:
                (self._target / d).mkdir(parents=True, exist_ok=True)
            dirs_created.append(d)

        # Copy new files
        for rel in plan.files_to_create:
            if not dry_run:
                dest = self._target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self._source / rel, dest)
            created.append(rel)

        # Handle conflicts
        for conflict in plan.conflicts:
            if force:
                if not dry_run:
                    dest = self._target / conflict.path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self._source / conflict.path, dest)
                created.append(conflict.path)
            else:
                skipped.append(conflict.path)

        # Write merge log
        log_path: Path | None = None
        if not dry_run and (created or skipped):
            log_path = self._target / ".grimoire-merge-log.json"
            log_entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "source": str(self._source),
                "files_created": created,
                "files_skipped": skipped,
            }
            log_path.write_text(json.dumps(log_entry, indent=2))

        return MergeResult(
            files_created=tuple(created),
            files_skipped=tuple(skipped),
            directories_created=tuple(dirs_created),
            log_path=log_path,
        )

    # ── Rollback ──────────────────────────────────────────────────────

    @staticmethod
    def undo(log_path: Path) -> list[str]:
        """Undo a merge from its log file.

        Deletes only the files that were created during the merge.
        Returns list of deleted file paths.
        """
        if not log_path.is_file():
            raise GrimoireMergeError(f"Merge log not found: {log_path}")

        data = json.loads(log_path.read_text())
        target = log_path.parent
        deleted: list[str] = []

        for rel in data.get("files_created", []):
            f = target / rel
            if f.is_file():
                f.unlink()
                deleted.append(rel)

        log_path.unlink()
        return deleted
