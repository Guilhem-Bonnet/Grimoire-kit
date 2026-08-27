"""Where a project stands in the governed standard.

Three questions a lifecycle hook must answer before it can decide anything:
is this project enrolled at all, under which profile, and which task is the
work charged to. They live here rather than in
:mod:`grimoire.core.agentic_standard` so that the hook path — run on every tool
call — imports a small module instead of the whole standard engine.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.agentic_standard import (
    STANDARD_PROFILE_FILE,
    normalize_task_id,
)

# ``STANDARD_DIR`` a déménagé dans ``standard_generation`` avec la frontière
# kit/overrides : on le prend à sa source plutôt qu'à travers un ré-export.
from grimoire.core.standard_generation import STANDARD_DIR

TASK_BOARD_RELPATH = STANDARD_DIR / "task-board.yaml"


def _load_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML mapping, or an empty one — never raise at a hook boundary."""
    if not path.is_file():
        return {}
    try:
        data = YAML(typ="safe").load(path)
    except (OSError, ValueError, YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def is_standard_enrolled(project_root: Path) -> bool:
    """True when *project_root* carries generated standard artifacts.

    Lifecycle hooks that fail closed on red gates must not be installed — nor
    fire — on a project that has no gates: they would block every closure on
    an absence. Callers gate on this.
    """
    root = project_root.resolve()
    return (root / STANDARD_PROFILE_FILE).is_file() or (root / TASK_BOARD_RELPATH).is_file()


def active_profile_id(project_root: Path) -> str:
    """Profile a project is enrolled in, ``starter`` when it declares none."""
    profile = _load_mapping(project_root.resolve() / STANDARD_PROFILE_FILE).get("profile")
    return str(profile) if profile else "starter"


def active_task_id(project_root: Path, *, env: Mapping[str, str] | None = None) -> str:
    """Task a lifecycle hook should evaluate.

    Resolution order: ``GRIMOIRE_TASK_ID`` (an operator saying which task this
    session is about), then the board's single ``in_progress`` task, then
    ``bootstrap``. Two concurrent in-progress tasks are ambiguous, so the board
    is ignored rather than guessed at.
    """
    environ = os.environ if env is None else env
    override = str(environ.get("GRIMOIRE_TASK_ID", "")).strip()
    if override:
        return normalize_task_id(override)
    tasks = _load_mapping(project_root.resolve() / TASK_BOARD_RELPATH).get("tasks")
    if not isinstance(tasks, list):
        return "bootstrap"
    in_progress = [
        str(task.get("task_id", ""))
        for task in tasks
        if isinstance(task, dict) and str(task.get("status", "")) == "in_progress" and task.get("task_id")
    ]
    if len(in_progress) == 1:
        return normalize_task_id(in_progress[0])
    return "bootstrap"
