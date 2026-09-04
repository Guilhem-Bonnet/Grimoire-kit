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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# Tout vient de ``standard_generation``, le module léger : ce lecteur tourne dans
# le chemin des hooks, à chaque appel d'outil, et importer le moteur du standard
# pour lire deux chemins et un identifiant coûtait 48 ms par appel.
from grimoire.core.standard_generation import (
    STANDARD_DIR,
    STANDARD_PROFILE_FILE,
    normalize_task_id,
)

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


def task_from_board(board: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    """The board entry for *task_id*, or an empty mapping when it has none."""
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list):
        return {}
    for task in tasks:
        if isinstance(task, dict) and str(task.get("task_id", "")) == task_id:
            return task
    return {}


def board_omits_task(project_root: Path, task: Mapping[str, Any]) -> bool:
    """True when a board exists and does not declare the task being checked.

    The distinction matters at the evidence gate. Every gate requirement is
    indexed on a named board state, so a task the board ignores has an empty
    state and owes nothing: the gate answers ``ok`` on an identifier that does
    not exist, and a typo in a hook or a CI job turns the gate decorative.

    A project with no board at all is a different case, deliberately left
    alone: the ``starter`` profile generates none, and the Stop hook relies on
    an unknown state owing no evidence (see ``_STATES_WITHOUT_EVIDENCE`` in
    :mod:`grimoire.hosts.decisions`).
    """
    if task:
        return False
    return (project_root / TASK_BOARD_RELPATH).is_file()


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


#: Le Mission Ledger, source des tâches (ADR-005). Lu ici sans importer le
#: module missions tant que le fichier n'existe pas : le chemin des hooks reste
#: léger sur un projet qui n'a jamais ouvert de tâche.
LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
#: Un opérateur ou un agent dit qui il est ; les claims des autres ne comptent plus.
ACTOR_ENV = "GRIMOIRE_ACTOR"
TASK_ENV = "GRIMOIRE_TASK_ID"


@dataclass(frozen=True, slots=True)
class ActiveTask:
    """La tâche qu'une session porte, et d'où la réponse vient.

    ``source`` vaut ``env`` (``GRIMOIRE_TASK_ID``), ``ledger_claim`` (claim actif
    du Mission Ledger), ``board`` (unique carte ``in_progress`` du board) ou
    ``bootstrap`` (rien ne désigne de tâche). Une réponse qui ne dit pas d'où
    elle vient ne se vérifie pas.
    """

    task_id: str
    source: str


def claimed_task_ids(project_root: Path, *, actor: str = "") -> list[str]:
    """Tâches ``claimed`` ou ``running`` du ledger — celles de *actor* seulement s'il est nommé.

    Ne lève jamais : un ledger illisible vaut « aucun claim », et la résolution
    continue sur le board.
    """
    events = project_root.resolve() / LEDGER_RELPATH / "events.jsonl"
    if not events.is_file():
        return []
    try:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.schemas import TaskState

        tasks = MissionLedger(events.parent).list_tasks()
    except Exception:  # frontière de hook : ne jamais casser une session
        return []
    active = [t for t in tasks if t.status in (TaskState.CLAIMED, TaskState.RUNNING)]
    if actor:
        active = [t for t in active if t.claim is not None and t.claim.actor_id == actor]
    return [t.id for t in active]


def resolve_active_task(project_root: Path, *, env: Mapping[str, str] | None = None) -> ActiveTask:
    """Task a lifecycle hook should evaluate, with the rule that chose it.

    Resolution order:

    1. ``GRIMOIRE_TASK_ID`` — an operator saying which task this session is about.
    2. The Mission Ledger's active claim: the single ``claimed``/``running``
       task, restricted to ``GRIMOIRE_ACTOR``'s claims when that is set. The
       ledger is the source (ADR-005); a claim is visible here the moment it is
       written, whether or not the board has been re-projected since.
    3. The board's single ``in_progress`` task — a project whose board was
       written by hand, or imported, and has no ledger.
    4. ``bootstrap``.

    Two concurrent claims (or two in-progress cards) are ambiguous: that level
    is skipped rather than guessed at, and ``GRIMOIRE_TASK_ID`` decides.
    """
    environ = os.environ if env is None else env
    override = str(environ.get(TASK_ENV, "")).strip()
    if override:
        return ActiveTask(normalize_task_id(override), "env")

    claimed = claimed_task_ids(project_root, actor=str(environ.get(ACTOR_ENV, "")).strip())
    if len(claimed) == 1:
        try:
            return ActiveTask(normalize_task_id(claimed[0]), "ledger_claim")
        except ValueError:
            pass

    tasks = _load_mapping(project_root.resolve() / TASK_BOARD_RELPATH).get("tasks")
    if isinstance(tasks, list):
        in_progress = [
            str(task.get("task_id", ""))
            for task in tasks
            if isinstance(task, dict) and str(task.get("status", "")) == "in_progress" and task.get("task_id")
        ]
        if len(in_progress) == 1:
            return ActiveTask(normalize_task_id(in_progress[0]), "board")
    return ActiveTask("bootstrap", "bootstrap")


def active_task_id(project_root: Path, *, env: Mapping[str, str] | None = None) -> str:
    """Task a lifecycle hook should evaluate — see :func:`resolve_active_task`."""
    return resolve_active_task(project_root, env=env).task_id
