"""Migration idempotente et réversible : task-board.yaml -> Mission Ledger (ADR-007).

ADR-005 a décidé que le ``MissionLedger`` est la source de vérité et que
``_grimoire/standard/task-board.yaml`` en est une projection exportée
(:mod:`grimoire.missions.board`). ADR-007 constate que ``grimoire standard
init`` scaffold ce board sans jamais ouvrir de ledger : tout projet gouverné
ainsi initialisé a un board sans source. Ce module ferme l'écart pour les
projets déjà scaffoldés, sans toucher au point d'entrée d'init (PR 2/3) ni au
cockpit (PR 3/3).

Le patron snapshot/apply/restore reprend celui de ``grimoire migrate``
(:mod:`grimoire.cli.cmd_migrate`) : un instantané précède toute écriture, et
il est restaurable par horodatage.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.core.standard_state import invalidate_cache, is_standard_enrolled
from grimoire.missions.board import build_board, task_state_of, write_board
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionState, TaskState
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH

__all__ = [
    "SNAPSHOT_ROOT",
    "TASK_UNIFICATION_MISSION_ID",
    "TASK_UNIFICATION_MISSION_TITLE",
    "TaskUnificationReport",
    "migrate_standard_tasks",
    "restore_task_unification",
    "tasks_unification_status",
]

_log = logging.getLogger(__name__)

#: La mission de rattachement pour toute tâche importée depuis un board sans
#: ledger — même patron que ``_DEFAULT_MISSION_ID`` de ``task_flow_adapter.py``.
TASK_UNIFICATION_MISSION_ID = "MIS-standard-import-001"
TASK_UNIFICATION_MISSION_TITLE = "Standard task-board import"

#: Même valeur littérale que ``grimoire.cli.cmd_migrate.SNAPSHOT_ROOT``, déclarée
#: ici plutôt qu'importée : aucun module de ``missions/`` ne dépend de ``cli/``
#: ailleurs dans le kit, et ce module n'a pas besoin de Typer/Rich pour un
#: horodatage et un chemin. Si les deux constantes divergent un jour, c'est un
#: signal, pas un bug de ce module.
SNAPSHOT_ROOT = "_grimoire-output/.migrations"

#: Nom d'acteur/hôte pour les transitions écrites par la migration — jamais un
#: humain, jamais confondu avec un claim réel.
_ACTOR = "task-unification"
_HOST = "host-task-unification"

#: Chemin explicite, dans ``_TASK_TRANSITIONS`` (ledger.py), de PROPOSED vers
#: chaque état cible. Un solveur de graphe générique serait moins lisible pour
#: un graphe aussi petit et fini ; ce tableau EST la preuve qu'un chemin existe
#: pour les 8 colonnes du board (voir ``test_every_lifecycle_state_has_a_path``).
_STATE_PATH: dict[TaskState, tuple[TaskState, ...]] = {
    TaskState.PROPOSED: (),
    TaskState.READY: (TaskState.READY,),
    TaskState.CLAIMED: (TaskState.READY, TaskState.CLAIMED),
    TaskState.RUNNING: (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING),
    TaskState.BLOCKED: (TaskState.READY, TaskState.BLOCKED),
    TaskState.NEEDS_VERIFICATION: (
        TaskState.READY,
        TaskState.CLAIMED,
        TaskState.RUNNING,
        TaskState.NEEDS_VERIFICATION,
    ),
    TaskState.FAILED: (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.FAILED),
    TaskState.CLOSED: (
        TaskState.READY,
        TaskState.CLAIMED,
        TaskState.RUNNING,
        TaskState.NEEDS_VERIFICATION,
        TaskState.CLOSED,
    ),
    TaskState.CANCELLED: (TaskState.CANCELLED,),
}


@dataclass(frozen=True, slots=True)
class TaskUnificationReport:
    """Ce que la migration a lu, importé, ignoré — et où la retrouver/l'annuler."""

    tasks_read: int
    tasks_imported: int
    tasks_skipped: int
    mission_created: bool
    snapshot_path: str
    stamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks_read": self.tasks_read,
            "tasks_imported": self.tasks_imported,
            "tasks_skipped": self.tasks_skipped,
            "mission_created": self.mission_created,
            "snapshot_path": self.snapshot_path,
            "stamp": self.stamp,
        }


def _new_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _load_board(path: Path) -> dict[str, Any]:
    """Lit un board YAML, ou ``{}`` — jamais d'exception (frontière de migration).

    Même discipline que ``standard_state._load_mapping`` (import ``ruamel``
    paresseux, échec silencieux) mais déclarée ici plutôt qu'importée : cette
    fonction privée n'est pas exportée par ce module-là.
    """
    if not path.is_file():
        return {}
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(path)
    except (OSError, ValueError, YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _board_task_ids(board: dict[str, Any]) -> list[str]:
    tasks = board.get("tasks")
    if not isinstance(tasks, list):
        return []
    return [str(t["task_id"]) for t in tasks if isinstance(t, dict) and t.get("task_id")]


def _resolve_ledger_root(project_root: Path, ledger_root: Path | None) -> Path:
    if ledger_root is None:
        return project_root / DEFAULT_LEDGER_RELPATH
    return ledger_root if ledger_root.is_absolute() else project_root / ledger_root


def _board_path(project_root: Path) -> Path:
    return project_root / STANDARD_DIR / "task-board.yaml"


def _ledger_has_data(ledger_root: Path) -> bool:
    return (ledger_root / "events.jsonl").is_file()


# ── Le moteur de migration, partagé entre le run réel et le dry-run ──────────


def _ensure_mission(ledger: MissionLedger) -> bool:
    if ledger.get_mission(TASK_UNIFICATION_MISSION_ID) is not None:
        return False
    mission = ledger.create_mission(
        TASK_UNIFICATION_MISSION_TITLE,
        origin="standard-task-board",
        description="Tâches importées depuis _grimoire/standard/task-board.yaml (ADR-007).",
        created_by=_ACTOR,
        mission_id=TASK_UNIFICATION_MISSION_ID,
    )
    ledger.transition_mission(mission.id, MissionState.OPEN, actor_id=_ACTOR, reason="import task-board.yaml")
    return True


def _walk_to_state(ledger: MissionLedger, task_id: str, target: TaskState) -> None:
    path = _STATE_PATH.get(target)
    if path is None:  # pragma: no cover — tous les TaskState ont un chemin déclaré ci-dessus
        _log.warning("task_unification: aucun chemin connu vers %s pour %s — laissé à PROPOSED", target, task_id)
        return
    for step in path:
        try:
            if step is TaskState.CLAIMED:
                ledger.claim_task(task_id, actor_id=_ACTOR, host_id=_HOST)
            else:
                ledger.transition_task(task_id, step, actor_id=_ACTOR, reason="import depuis task-board.yaml")
        except GrimoireMissionError:
            _log.warning(
                "task_unification: transition vers %s impossible pour %s — laissé à l'état atteint",
                step,
                task_id,
            )
            return


#: Clés de board déjà portées ailleurs (champ dédié de `MissionTask`, ou
#: recalculées à chaque projection par `board.py` à partir du ledger — les y
#: reporter telles quelles ne ferait que les désynchroniser du ledger qui les
#: régénère). Tout ce qui n'est pas ici est une clé que ce schéma ne modélise
#: pas encore : elle part dans `MissionTask.extra` plutôt que d'être jetée.
_KNOWN_BOARD_KEYS = frozenset(
    {
        "task_id",
        "title",
        "status",
        "acceptance_criteria",
        "owner",
        "description",
        "guardrails",
        "expected_evidence",
        "priority",
        "agent_roles",
        "remediation_ref",
        # Recalculées par `board.py::_task_entry` à chaque projection.
        "context_bundle_ref",
        "decision_trace_ref",
        "evidence_pack_ref",
        "verifiability",
        "blockers",
    }
)


def _import_one_task(ledger: MissionLedger, entry: dict[str, Any]) -> None:
    task_id = str(entry["task_id"])
    acceptance = tuple(entry.get("acceptance_criteria") or ["Migré depuis task-board.yaml — critère à préciser"])
    extra = {k: v for k, v in entry.items() if k not in _KNOWN_BOARD_KEYS}
    task = ledger.create_task(
        TASK_UNIFICATION_MISSION_ID,
        str(entry.get("title") or task_id),
        acceptance=acceptance,
        owner=str(entry.get("owner") or ""),
        description=str(entry.get("description") or ""),
        guardrails=tuple(entry.get("guardrails") or ()),
        expected_evidence=tuple(entry.get("expected_evidence") or ()),
        priority=str(entry.get("priority") or ""),
        agent_roles=tuple(str(role) for role in (entry.get("agent_roles") or ())),
        remediation_ref=str(entry.get("remediation_ref") or ""),
        extra=extra,
        task_id=task_id,
    )
    try:
        target = task_state_of(str(entry.get("status", "proposed")))
    except ValueError:
        _log.warning("task_unification: statut de board inconnu pour %s — laissé à proposed", task_id)
        return
    _walk_to_state(ledger, task.id, target)


def _import_tasks(ledger: MissionLedger, tasks: list[dict[str, Any]]) -> tuple[int, int]:
    """Importe chaque tâche absente du ledger. Renvoie ``(importées, ignorées)``."""
    imported = 0
    skipped = 0
    for entry in tasks:
        if not isinstance(entry, dict) or not entry.get("task_id"):
            continue
        task_id = str(entry["task_id"])
        if ledger.get_task(task_id) is not None:
            skipped += 1
            continue
        _import_one_task(ledger, entry)
        imported += 1
    return imported, skipped


# ── Snapshot / restore ────────────────────────────────────────────────────────


def _snapshot(project_root: Path, board_path: Path, ledger_root: Path, stamp: str) -> Path:
    """Copie ``task-board.yaml`` et le ledger (s'il porte déjà des données) avant écriture."""
    snap = project_root / SNAPSHOT_ROOT / f"{stamp}-tasks-unification"
    files_dir = snap / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "stamp": stamp,
        "board_existed": False,
        "ledger_existed": False,
        "board_relpath": (
            board_path.relative_to(project_root).as_posix()
            if board_path.is_relative_to(project_root)
            else str(board_path)
        ),
    }

    if board_path.is_file():
        shutil.copy2(board_path, files_dir / "task-board.yaml")
        manifest["board_existed"] = True

    manifest["ledger_relpath"] = (
        ledger_root.relative_to(project_root).as_posix() if ledger_root.is_relative_to(project_root) else str(ledger_root)
    )
    if _ledger_has_data(ledger_root):
        shutil.copytree(ledger_root, files_dir / "ledger")
        manifest["ledger_existed"] = True

    (snap / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return snap


def restore_task_unification(project_root: Path, stamp: str) -> list[str]:
    """Remet ``task-board.yaml`` et le ledger dans l'état capturé par le snapshot *stamp*."""
    root = project_root.resolve()
    snap = root / SNAPSHOT_ROOT / f"{stamp}-tasks-unification"
    manifest_path = snap / "manifest.json"
    if not manifest_path.is_file():
        msg = f"no task-unification snapshot manifest at {manifest_path}"
        raise FileNotFoundError(msg)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_dir = snap / "files"
    restored: list[str] = []

    board_relpath = str(manifest.get("board_relpath", ""))
    if board_relpath:
        board_dest = Path(board_relpath) if Path(board_relpath).is_absolute() else root / board_relpath
        if manifest.get("board_existed"):
            shutil.copy2(files_dir / "task-board.yaml", board_dest)
            restored.append(str(board_dest))
        else:
            board_dest.unlink(missing_ok=True)

    ledger_relpath = str(manifest.get("ledger_relpath", ""))
    if ledger_relpath:
        ledger_dest = Path(ledger_relpath) if Path(ledger_relpath).is_absolute() else root / ledger_relpath
        if ledger_dest.exists():
            shutil.rmtree(ledger_dest)
        if manifest.get("ledger_existed"):
            shutil.copytree(files_dir / "ledger", ledger_dest)
            restored.append(str(ledger_dest))

    invalidate_cache(root)
    return restored


# ── Dry-run : mêmes comptages, aucune écriture réelle ─────────────────────────


def _dry_run_report(ledger_root: Path, tasks: list[dict[str, Any]], stamp: str) -> TaskUnificationReport:
    """Rejoue la migration dans un ledger jetable pour compter sans rien écrire.

    Le ledger réel (s'il existe déjà) est copié dans le répertoire jetable
    d'abord, pour que l'idempotence se vérifie correctement même en dry-run :
    une tâche déjà migrée doit compter comme ignorée, pas comme importable.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="grimoire-task-unification-dry-"))
    try:
        tmp_ledger_root = tmp_root / "ledger"
        if _ledger_has_data(ledger_root):
            shutil.copytree(ledger_root, tmp_ledger_root)
        else:
            tmp_ledger_root.mkdir(parents=True, exist_ok=True)
        ledger = MissionLedger(tmp_ledger_root)
        mission_created = _ensure_mission(ledger)
        imported, skipped = _import_tasks(ledger, tasks)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return TaskUnificationReport(
        tasks_read=len(tasks),
        tasks_imported=imported,
        tasks_skipped=skipped,
        mission_created=mission_created,
        snapshot_path="",
        stamp=stamp,
    )


# ── Point d'entrée ─────────────────────────────────────────────────────────────


def migrate_standard_tasks(
    project_root: Path,
    *,
    ledger_root: Path | None = None,
    stamp: str | None = None,
    dry_run: bool = False,
) -> TaskUnificationReport:
    """Importe dans le Mission Ledger toute tâche de ``task-board.yaml`` qui lui manque.

    Idempotent (rejouer sur un projet déjà migré n'importe rien de plus) et
    réversible (:func:`restore_task_unification`). N'échoue jamais sur un
    projet sans board : c'est un no-op déclaré, pas une erreur.
    """
    root = project_root.resolve()
    resolved_ledger_root = _resolve_ledger_root(root, ledger_root)
    resolved_stamp = stamp or _new_stamp()
    board_path = _board_path(root)
    board = _load_board(board_path)
    tasks_raw = board.get("tasks")
    tasks: list[dict[str, Any]] = tasks_raw if isinstance(tasks_raw, list) else []

    if not board_path.is_file() or not tasks:
        return TaskUnificationReport(
            tasks_read=0,
            tasks_imported=0,
            tasks_skipped=0,
            mission_created=False,
            snapshot_path="",
            stamp=resolved_stamp,
        )

    if dry_run:
        return _dry_run_report(resolved_ledger_root, tasks, resolved_stamp)

    snapshot = _snapshot(root, board_path, resolved_ledger_root, resolved_stamp)
    ledger = MissionLedger(resolved_ledger_root)
    mission_created = _ensure_mission(ledger)
    imported, skipped = _import_tasks(ledger, tasks)

    if imported > 0:
        write_board(board_path, build_board(ledger, project=root.name, mission_id=TASK_UNIFICATION_MISSION_ID))
        invalidate_cache(root)

    snapshot_path = str(snapshot.relative_to(root)) if snapshot.is_relative_to(root) else str(snapshot)
    return TaskUnificationReport(
        tasks_read=len(tasks),
        tasks_imported=imported,
        tasks_skipped=skipped,
        mission_created=mission_created,
        snapshot_path=snapshot_path,
        stamp=resolved_stamp,
    )


def tasks_unification_status(project_root: Path) -> dict[str, Any]:
    """État de la divergence board/ledger — terrain du futur check doctor (PR 2/3).

    Un projet non enrôlé au standard rend toujours ``diverged=False`` : cette
    fonction ne doit jamais faire échouer un projet qui n'utilise pas le
    standard.
    """
    root = project_root.resolve()
    if not is_standard_enrolled(root):
        return {
            "enrolled": False,
            "board_exists": False,
            "ledger_exists": False,
            "board_task_ids": [],
            "ledger_task_ids": [],
            "missing_in_ledger": [],
            "diverged": False,
        }

    board_path = _board_path(root)
    board_exists = board_path.is_file()
    board_task_ids = _board_task_ids(_load_board(board_path))

    ledger_root = _resolve_ledger_root(root, None)
    ledger_exists = _ledger_has_data(ledger_root)
    ledger_task_ids: list[str] = []
    if ledger_exists:
        try:
            ledger_task_ids = [t.id for t in MissionLedger(ledger_root).list_tasks()]
        except Exception:  # frontière de diagnostic : jamais casser un statut sur un ledger illisible
            ledger_task_ids = []

    ledger_ids = set(ledger_task_ids)
    missing_in_ledger = [task_id for task_id in board_task_ids if task_id not in ledger_ids]
    diverged = board_exists and (not ledger_exists or bool(missing_in_ledger))

    return {
        "enrolled": True,
        "board_exists": board_exists,
        "ledger_exists": ledger_exists,
        "board_task_ids": board_task_ids,
        "ledger_task_ids": ledger_task_ids,
        "missing_in_ledger": missing_in_ledger,
        "diverged": diverged,
    }
