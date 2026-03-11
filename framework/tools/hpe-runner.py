#!/usr/bin/env python3
"""
hpe-runner.py — Hybrid Parallelism Engine Runner.
==================================================

Runtime d'exécution pour le moteur de parallélisme hybride (BM-58).
Construit un DAG à partir d'une définition YAML, exécute les tâches
par vagues via le scheduler ATS, gère les checkpoints et les échecs.

Concepts :
  - HPEPlan     : DAG de tâches construit depuis une définition YAML
  - HPEState    : État d'exécution courant (running, paused, completed, failed)
  - WaveResult  : Résultat d'une vague d'exécution parallèle
  - Checkpoint  : Snapshot de l'état du DAG à un instant t

Modes d'exécution :
  parallel       — Tâches sans dépendances exécutées en parallèle
  sequential     — Chaque tâche attend ses dépendances
  opportunistic  — Démarrage anticipé avec dépendances partielles

Stratégies d'échec :
  stop-all              — Annuler toutes les tâches
  continue-others       — Continuer les indépendantes
  pause-and-escalate    — Pause + escalade utilisateur

Modes CLI :
  plan      — Construire un DAG depuis un fichier YAML
  run       — Exécuter un plan HPE
  status    — État d'un plan en cours
  resume    — Reprendre depuis un checkpoint
  critical  — Afficher le chemin critique

Usage :
  python3 hpe-runner.py --project-root . plan --file workflow.yaml
  python3 hpe-runner.py --project-root . run --plan-id hpe-abc123
  python3 hpe-runner.py --project-root . status --plan-id hpe-abc123
  python3 hpe-runner.py --project-root . resume --checkpoint cp-abc123
  python3 hpe-runner.py --project-root . critical --plan-id hpe-abc123

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.hpe_runner")

HPE_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

HPE_DIR = "_grimoire-output/.hpe"
PLANS_DIR = "plans"
CHECKPOINTS_DIR = "checkpoints"
HISTORY_FILE = "hpe-history.jsonl"
DEFAULT_MAX_PARALLEL = 5
DEFAULT_TIMEOUT_SEC = 300

VALID_MODES = frozenset({"parallel", "sequential", "opportunistic", "cross-validate"})
VALID_FAILURE_STRATEGIES = frozenset({"stop-all", "continue-others", "pause-and-escalate"})
VALID_PLAN_STATES = frozenset({"pending", "running", "paused", "completed", "failed"})
VALID_TASK_STATES = frozenset({
    "pending", "ready", "running", "done", "failed", "cancelled", "skipped",
})


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class HPETask:
    """Une tâche atomique dans le DAG HPE."""

    id: str = ""
    agent: str = ""
    task: str = ""
    depends_on: list[str] = field(default_factory=list)
    output_key: str = ""
    priority: str = "medium"
    mode: str = "parallel"
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""
    error: str = ""
    attempt: int = 0
    max_retries: int = 2


@dataclass
class HPEConfig:
    """Configuration globale d'un plan HPE."""

    max_parallel: int = DEFAULT_MAX_PARALLEL
    checkpoint_after: list[str] = field(default_factory=list)
    on_failure: str = "pause-and-escalate"
    timeout_per_task_sec: int = DEFAULT_TIMEOUT_SEC


@dataclass
class HPEPlan:
    """Plan d'exécution HPE — un DAG de tâches avec configuration."""

    plan_id: str = ""
    description: str = ""
    tasks: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    state: str = "pending"
    outputs: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    waves_completed: int = 0

    def __post_init__(self) -> None:
        if not self.plan_id:
            self.plan_id = f"hpe-{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at


@dataclass
class WaveResult:
    """Résultat d'une vague d'exécution."""

    wave_number: int = 0
    task_ids: list[str] = field(default_factory=list)
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class Checkpoint:
    """Snapshot de l'état du DAG."""

    checkpoint_id: str = ""
    plan_id: str = ""
    trigger_task: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.checkpoint_id:
            self.checkpoint_id = f"cp-{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# ── File I/O ─────────────────────────────────────────────────────────────────


def _hpe_dir(project_root: Path) -> Path:
    return project_root / HPE_DIR


def _plans_dir(project_root: Path) -> Path:
    return _hpe_dir(project_root) / PLANS_DIR


def _checkpoints_dir(project_root: Path) -> Path:
    return _hpe_dir(project_root) / CHECKPOINTS_DIR


def _plan_path(project_root: Path, plan_id: str) -> Path:
    return _plans_dir(project_root) / f"{plan_id}.json"


def _checkpoint_path(project_root: Path, checkpoint_id: str) -> Path:
    return _checkpoints_dir(project_root) / f"{checkpoint_id}.json"


def _history_path(project_root: Path) -> Path:
    return _hpe_dir(project_root) / HISTORY_FILE


def save_plan(project_root: Path, plan: HPEPlan) -> None:
    """Sauvegarde un plan HPE."""
    pp = _plan_path(project_root, plan.plan_id)
    pp.parent.mkdir(parents=True, exist_ok=True)
    plan.updated_at = datetime.now().isoformat()
    pp.write_text(
        json.dumps(asdict(plan), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_plan(project_root: Path, plan_id: str) -> HPEPlan | None:
    """Charge un plan HPE."""
    pp = _plan_path(project_root, plan_id)
    if not pp.exists():
        return None
    try:
        data = json.loads(pp.read_text(encoding="utf-8"))
        return HPEPlan(**data)
    except (json.JSONDecodeError, TypeError):
        _log.warning("Corrupted plan %s", plan_id)
        return None


def save_checkpoint(project_root: Path, checkpoint: Checkpoint) -> None:
    """Sauvegarde un checkpoint."""
    cp = _checkpoint_path(project_root, checkpoint.checkpoint_id)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(
        json.dumps(asdict(checkpoint), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_checkpoint(project_root: Path, checkpoint_id: str) -> Checkpoint | None:
    """Charge un checkpoint."""
    cp = _checkpoint_path(project_root, checkpoint_id)
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        return Checkpoint(**data)
    except (json.JSONDecodeError, TypeError):
        _log.warning("Corrupted checkpoint %s", checkpoint_id)
        return None


def append_history(project_root: Path, event: dict[str, Any]) -> None:
    """Ajoute un événement à l'historique HPE."""
    hp = _history_path(project_root)
    hp.parent.mkdir(parents=True, exist_ok=True)
    event["timestamp"] = datetime.now().isoformat()
    with open(hp, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── DAG Builder ──────────────────────────────────────────────────────────────


def build_plan_from_definition(definition: dict[str, Any]) -> HPEPlan:
    """Construit un HPEPlan depuis une définition YAML (dict parsé).

    La définition attendue suit le format BM-58 :
    {
      "description": "...",
      "dag": {
        "tasks": [ {id, agent, task, depends_on, output_key, priority, mode}, ... ],
        "config": { max_parallel, checkpoint_after, on_failure, timeout_per_task_sec }
      }
    }
    """
    dag = definition.get("dag", {})
    raw_tasks = dag.get("tasks", [])
    raw_config = dag.get("config", {})

    tasks = []
    for rt in raw_tasks:
        t = HPETask(
            id=rt.get("id", f"task-{uuid.uuid4().hex[:6]}"),
            agent=rt.get("agent", ""),
            task=rt.get("task", ""),
            depends_on=rt.get("depends_on", []),
            output_key=rt.get("output_key", ""),
            priority=rt.get("priority", "medium"),
            mode=rt.get("mode", "parallel") if rt.get("mode") in VALID_MODES else "parallel",
        )
        tasks.append(asdict(t))

    config = HPEConfig(
        max_parallel=raw_config.get("max_parallel", DEFAULT_MAX_PARALLEL),
        checkpoint_after=raw_config.get("checkpoint_after", []),
        on_failure=raw_config.get("on_failure", "pause-and-escalate"),
        timeout_per_task_sec=raw_config.get("timeout_per_task_sec", DEFAULT_TIMEOUT_SEC),
    )

    return HPEPlan(
        description=definition.get("description", ""),
        tasks=tasks,
        config=asdict(config),
    )


def validate_plan(plan: HPEPlan) -> list[str]:
    """Valide un plan HPE. Retourne les erreurs trouvées."""
    errors = []

    if not plan.tasks:
        errors.append("Plan has no tasks")
        return errors

    task_ids = {t["id"] for t in plan.tasks}

    # Vérifier les IDs uniques
    if len(task_ids) != len(plan.tasks):
        errors.append("Duplicate task IDs found")

    # Vérifier les dépendances
    for t in plan.tasks:
        for dep in t.get("depends_on", []):
            if dep not in task_ids:
                errors.append(f"Task '{t['id']}' depends on unknown task '{dep}'")

    # Vérifier les cycles
    cycles = detect_cycles(plan)
    if cycles:
        errors.append(f"Cycles detected: {cycles}")

    # Vérifier checkpoint_after
    cp_after = plan.config.get("checkpoint_after", [])
    for cp_id in cp_after:
        if cp_id not in task_ids:
            errors.append(f"Checkpoint after unknown task '{cp_id}'")

    return errors


# ── DAG Analysis ─────────────────────────────────────────────────────────────


def get_dependency_map(plan: HPEPlan) -> dict[str, set[str]]:
    """Construit la map des dépendances."""
    return {t["id"]: set(t.get("depends_on", [])) for t in plan.tasks}


def detect_cycles(plan: HPEPlan) -> list[list[str]]:
    """Détecte les cycles dans le DAG."""
    dep_map = get_dependency_map(plan)
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()
    cycles: list[list[str]] = []

    def _dfs(node: str) -> None:
        if node in path_set:
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        path_set.add(node)
        for dep in dep_map.get(node, set()):
            _dfs(dep)
        path.pop()
        path_set.discard(node)

    for tid in dep_map:
        _dfs(tid)

    return cycles


def topological_layers(plan: HPEPlan) -> list[list[str]]:
    """Retourne les couches d'exécution (waves) du DAG.

    Chaque couche contient les tâches qui peuvent s'exécuter en parallèle
    une fois les couches précédentes terminées.
    """
    dep_map = get_dependency_map(plan)
    task_ids = set(dep_map.keys())
    completed: set[str] = set()
    layers: list[list[str]] = []

    while completed != task_ids:
        layer = []
        for tid in sorted(task_ids - completed):
            deps = dep_map.get(tid, set())
            if deps.issubset(completed):
                layer.append(tid)
        if not layer:
            break  # stuck — cycle or bad deps
        layers.append(layer)
        completed.update(layer)

    return layers


def critical_path(plan: HPEPlan) -> list[str]:
    """Calcule le chemin critique du DAG."""
    dep_map = get_dependency_map(plan)
    task_map = {t["id"]: t for t in plan.tasks}
    priority_weight = {"critical": 4, "high": 3, "medium": 2, "low": 1}

    layers = topological_layers(plan)
    flat_order = [tid for layer in layers for tid in layer]

    longest: dict[str, tuple[float, list[str]]] = {}

    for tid in flat_order:
        task = task_map.get(tid, {})
        weight = priority_weight.get(task.get("priority", "medium"), 2)
        deps = dep_map.get(tid, set())

        if not deps:
            longest[tid] = (weight, [tid])
        else:
            best = max(
                (longest.get(d, (0, [])) for d in deps if d in longest),
                key=lambda x: x[0],
                default=(0, []),
            )
            longest[tid] = (best[0] + weight, best[1] + [tid])

    if not longest:
        return []
    return max(longest.values(), key=lambda x: x[0])[1]


# ── Scheduler ────────────────────────────────────────────────────────────────


def get_ready_tasks(plan: HPEPlan) -> list[dict[str, Any]]:
    """Retourne les tâches prêtes à exécuter."""
    terminal = {t["id"] for t in plan.tasks if t.get("status") in ("done", "cancelled", "skipped")}

    ready = []
    for t in plan.tasks:
        if t.get("status") != "pending":
            continue
        deps = set(t.get("depends_on", []))
        if deps.issubset(terminal):
            ready.append(t)

    return ready


def get_opportunistic_tasks(plan: HPEPlan) -> list[dict[str, Any]]:
    """Retourne les tâches en mode opportunistic prêtes partiellement.

    Une tâche opportunistic peut démarrer si au moins une dépendance
    est satisfaite (hard dep disponible).
    """
    terminal = {t["id"] for t in plan.tasks if t.get("status") in ("done", "cancelled", "skipped")}

    opportunistic = []
    for t in plan.tasks:
        if t.get("status") != "pending":
            continue
        if t.get("mode") != "opportunistic":
            continue
        deps = set(t.get("depends_on", []))
        if not deps:
            continue
        satisfied = deps & terminal
        if satisfied and satisfied != deps:
            opportunistic.append(t)

    return opportunistic


def schedule_wave(plan: HPEPlan) -> list[str]:
    """Calcule la prochaine vague de tâches à exécuter.

    Combine les tâches ready (deps satisfaites) et les tâches opportunistic
    (deps partiellement satisfaites). Respecte max_parallel.
    """
    max_parallel = plan.config.get("max_parallel", DEFAULT_MAX_PARALLEL)
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    ready = get_ready_tasks(plan)
    opportunistic = get_opportunistic_tasks(plan)
    all_candidates = ready + opportunistic

    # Trier par priorité
    all_candidates.sort(key=lambda t: priority_order.get(t.get("priority", "medium"), 2))

    # Limiter au max_parallel
    wave = [t["id"] for t in all_candidates[:max_parallel]]
    return wave


# ── Execution Engine ─────────────────────────────────────────────────────────


def _find_task(plan: HPEPlan, task_id: str) -> dict[str, Any] | None:
    for t in plan.tasks:
        if t["id"] == task_id:
            return t
    return None


def mark_task_running(plan: HPEPlan, task_id: str) -> HPEPlan:
    """Marque une tâche comme running."""
    task = _find_task(plan, task_id)
    if task:
        task["status"] = "running"
        task["started_at"] = datetime.now().isoformat()
        task["attempt"] = task.get("attempt", 0) + 1
    return plan


def mark_task_done(plan: HPEPlan, task_id: str, result: dict[str, Any] | None = None) -> HPEPlan:
    """Marque une tâche comme done avec son résultat."""
    task = _find_task(plan, task_id)
    if task:
        task["status"] = "done"
        task["completed_at"] = datetime.now().isoformat()
        if result:
            task["result"] = result
            if task.get("output_key"):
                plan.outputs[task["output_key"]] = result
    return plan


def mark_task_failed(plan: HPEPlan, task_id: str, error: str = "") -> HPEPlan:
    """Marque une tâche comme failed."""
    task = _find_task(plan, task_id)
    if task:
        task["status"] = "failed"
        task["completed_at"] = datetime.now().isoformat()
        task["error"] = error
    return plan


def apply_failure_strategy(
    plan: HPEPlan,
    failed_task_id: str,
) -> tuple[HPEPlan, str]:
    """Applique la stratégie d'échec configurée.

    Returns:
        (plan mis à jour, action prise)
    """
    strategy = plan.config.get("on_failure", "pause-and-escalate")
    dep_map = get_dependency_map(plan)
    failed_task = _find_task(plan, failed_task_id)

    if strategy == "stop-all":
        for t in plan.tasks:
            if t["status"] in ("pending", "ready", "running"):
                t["status"] = "cancelled"
        plan.state = "failed"
        return plan, "stop-all: all tasks cancelled"

    # Trouver les tâches qui dépendent directement ou transitivement de la tâche échouée
    def _get_dependents(task_id: str) -> set[str]:
        dependents: set[str] = set()
        for tid, deps in dep_map.items():
            if task_id in deps:
                dependents.add(tid)
                dependents.update(_get_dependents(tid))
        return dependents

    dependents = _get_dependents(failed_task_id)

    if strategy == "continue-others":
        for t in plan.tasks:
            if t["id"] in dependents and t["status"] == "pending":
                t["status"] = "cancelled"
        return plan, f"continue-others: cancelled {len(dependents)} dependent tasks"

    # pause-and-escalate (default)
    for t in plan.tasks:
        if t["id"] in dependents and t["status"] == "pending":
            t["status"] = "pending"  # keep pending, don't schedule
    plan.state = "paused"

    can_retry = (failed_task and
                 failed_task.get("attempt", 0) < failed_task.get("max_retries", 2))
    action = f"pause-and-escalate: paused plan, {len(dependents)} tasks blocked"
    if can_retry:
        action += ", retry available"

    return plan, action


def retry_task(plan: HPEPlan, task_id: str) -> HPEPlan:
    """Remet une tâche failed en pending pour réessai."""
    task = _find_task(plan, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    if task["status"] != "failed":
        raise ValueError(f"Task {task_id} is not failed (status: {task['status']})")
    if task.get("attempt", 0) >= task.get("max_retries", 2):
        raise ValueError(f"Task {task_id} has exceeded max retries")
    task["status"] = "pending"
    task["error"] = ""
    if plan.state == "paused":
        plan.state = "running"
    return plan


def skip_task(plan: HPEPlan, task_id: str) -> HPEPlan:
    """Marque une tâche failed comme skipped et débloque ses dépendants."""
    task = _find_task(plan, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    task["status"] = "skipped"
    if plan.state == "paused":
        plan.state = "running"
    return plan


def create_checkpoint(plan: HPEPlan, trigger_task: str = "") -> Checkpoint:
    """Crée un checkpoint de l'état actuel du plan."""
    task_states = {}
    for t in plan.tasks:
        task_states[t["id"]] = {
            "status": t["status"],
            "attempt": t.get("attempt", 0),
            "result": t.get("result", {}),
        }

    return Checkpoint(
        plan_id=plan.plan_id,
        trigger_task=trigger_task,
        state={
            "plan_state": plan.state,
            "task_states": task_states,
            "waves_completed": plan.waves_completed,
        },
        outputs=dict(plan.outputs),
    )


def restore_from_checkpoint(plan: HPEPlan, checkpoint: Checkpoint) -> HPEPlan:
    """Restaure un plan depuis un checkpoint."""
    task_states = checkpoint.state.get("task_states", {})

    for t in plan.tasks:
        ts = task_states.get(t["id"])
        if ts:
            t["status"] = ts["status"]
            t["attempt"] = ts.get("attempt", 0)
            t["result"] = ts.get("result", {})

    plan.state = checkpoint.state.get("plan_state", "pending")
    plan.waves_completed = checkpoint.state.get("waves_completed", 0)
    plan.outputs = dict(checkpoint.outputs)

    return plan


def execute_wave(
    plan: HPEPlan,
    project_root: Path,
    executor: Any = None,
) -> tuple[HPEPlan, WaveResult]:
    """Exécute une vague de tâches.

    L'executor est un callable(task_dict, plan_outputs) → (success: bool, result: dict).
    Si None, les tâches sont marquées comme done avec un résultat vide (dry-run).
    """
    wave_ids = schedule_wave(plan)
    wave_result = WaveResult(
        wave_number=plan.waves_completed + 1,
        task_ids=list(wave_ids),
    )

    if not wave_ids:
        return plan, wave_result

    plan.state = "running"

    for task_id in wave_ids:
        plan = mark_task_running(plan, task_id)

    for task_id in wave_ids:
        task = _find_task(plan, task_id)
        if not task:
            continue

        try:
            if executor:
                success, result = executor(task, plan.outputs)
            else:
                # Dry-run: mark as success
                success, result = True, {"dry_run": True}

            if success:
                plan = mark_task_done(plan, task_id, result)
                wave_result.succeeded.append(task_id)
            else:
                plan = mark_task_failed(plan, task_id, result.get("error", "unknown"))
                wave_result.failed.append(task_id)
        except Exception as e:
            plan = mark_task_failed(plan, task_id, str(e))
            wave_result.failed.append(task_id)

    # Gestion des échecs
    for failed_id in wave_result.failed:
        plan, _action = apply_failure_strategy(plan, failed_id)
        if plan.state in ("failed", "paused"):
            break

    # Checkpoints
    cp_after = plan.config.get("checkpoint_after", [])
    for task_id in wave_result.succeeded:
        if task_id in cp_after:
            cp = create_checkpoint(plan, trigger_task=task_id)
            save_checkpoint(project_root, cp)
            append_history(project_root, {
                "event": "checkpoint",
                "plan_id": plan.plan_id,
                "checkpoint_id": cp.checkpoint_id,
                "trigger": task_id,
            })

    plan.waves_completed += 1
    save_plan(project_root, plan)

    append_history(project_root, {
        "event": "wave_complete",
        "plan_id": plan.plan_id,
        "wave": wave_result.wave_number,
        "succeeded": wave_result.succeeded,
        "failed": wave_result.failed,
    })

    return plan, wave_result


def run_plan(
    plan: HPEPlan,
    project_root: Path,
    executor: Any = None,
    max_waves: int = 100,
) -> tuple[HPEPlan, list[WaveResult]]:
    """Exécute un plan complet, vague par vague.

    Args:
        plan: Plan HPE à exécuter.
        project_root: Racine du projet.
        executor: Callable(task, outputs) → (success, result). None = dry-run.
        max_waves: Limite de sécurité sur le nombre de vagues.

    Returns:
        (plan final, liste de WaveResults)
    """
    errors = validate_plan(plan)
    if errors:
        raise ValueError(f"Invalid plan: {errors}")

    plan.state = "running"
    save_plan(project_root, plan)
    append_history(project_root, {"event": "plan_start", "plan_id": plan.plan_id})

    results: list[WaveResult] = []

    for _i in range(max_waves):
        wave_ids = schedule_wave(plan)
        if not wave_ids:
            break
        if plan.state in ("failed", "paused"):
            break

        plan, wave_result = execute_wave(plan, project_root, executor)
        results.append(wave_result)

        if plan.state in ("failed", "paused"):
            break

    # Déterminer l'état final
    if plan.state not in ("failed", "paused"):
        all_done = all(
            t["status"] in ("done", "cancelled", "skipped")
            for t in plan.tasks
        )
        plan.state = "completed" if all_done else plan.state

    save_plan(project_root, plan)
    append_history(project_root, {
        "event": "plan_end",
        "plan_id": plan.plan_id,
        "state": plan.state,
        "waves": len(results),
    })

    return plan, results


def get_plan_status(plan: HPEPlan) -> dict[str, Any]:
    """Retourne un résumé de l'état du plan."""
    by_status: dict[str, int] = {}
    for t in plan.tasks:
        st = t.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1

    total = len(plan.tasks)
    done = by_status.get("done", 0) + by_status.get("skipped", 0)
    progress = round(done / total * 100, 1) if total else 0

    ready = get_ready_tasks(plan)
    opportunistic = get_opportunistic_tasks(plan)
    cp = critical_path(plan)

    return {
        "plan_id": plan.plan_id,
        "state": plan.state,
        "description": plan.description,
        "total_tasks": total,
        "by_status": by_status,
        "progress_pct": progress,
        "waves_completed": plan.waves_completed,
        "ready_count": len(ready),
        "opportunistic_count": len(opportunistic),
        "critical_path": cp,
        "outputs_available": list(plan.outputs.keys()),
    }


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_hpe_plan(
    definition_json: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: construit et sauvegarde un plan HPE.

    Args:
        definition_json: Définition JSON du plan (format BM-58).
        project_root: Racine du projet.

    Returns:
        Plan créé avec validation.
    """
    root = Path(project_root).resolve()
    definition = json.loads(definition_json)
    plan = build_plan_from_definition(definition)
    errors = validate_plan(plan)

    if errors:
        return {"error": "Validation failed", "errors": errors}

    save_plan(root, plan)
    append_history(root, {"event": "plan_created", "plan_id": plan.plan_id})

    return {
        "plan_id": plan.plan_id,
        "task_count": len(plan.tasks),
        "layers": topological_layers(plan),
        "critical_path": critical_path(plan),
        "validation": "OK",
    }


def mcp_hpe_run(
    plan_id: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: exécute un plan HPE en dry-run.

    Args:
        plan_id: ID du plan à exécuter.
        project_root: Racine du projet.

    Returns:
        Résultat d'exécution.
    """
    root = Path(project_root).resolve()
    plan = load_plan(root, plan_id)
    if not plan:
        return {"error": f"Plan {plan_id} not found"}

    plan, results = run_plan(plan, root)

    return {
        "plan_id": plan.plan_id,
        "final_state": plan.state,
        "waves": len(results),
        "outputs": plan.outputs,
        "status": get_plan_status(plan),
    }


def mcp_hpe_status(
    plan_id: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: état d'un plan HPE.

    Args:
        plan_id: ID du plan.
        project_root: Racine du projet.

    Returns:
        État détaillé du plan.
    """
    root = Path(project_root).resolve()
    plan = load_plan(root, plan_id)
    if not plan:
        return {"error": f"Plan {plan_id} not found"}

    return get_plan_status(plan)


# ── CLI Commands ─────────────────────────────────────────────────────────────


def _status_icon(s: str) -> str:
    return {
        "pending": "⏳", "ready": "🟢", "running": "🔄",
        "done": "✅", "failed": "❌", "cancelled": "⛔",
        "skipped": "⏭️", "paused": "⏸️", "completed": "🏁",
    }.get(s, "❓")


def cmd_plan(args: argparse.Namespace) -> int:
    fpath = Path(args.file)
    if not fpath.exists():
        print(f"  ❌ File not found: {fpath}", file=sys.stderr)
        return 1

    content = fpath.read_text(encoding="utf-8")
    try:
        definition = json.loads(content)
    except json.JSONDecodeError:
        # Try YAML if json fails
        try:
            import yaml  # noqa: F811
            definition = yaml.safe_load(content)
        except ImportError:
            print("  ❌ File is not valid JSON and PyYAML not available", file=sys.stderr)
            return 1
        except Exception:
            print("  ❌ Cannot parse file as JSON or YAML", file=sys.stderr)
            return 1

    root = Path(args.project_root).resolve()
    plan = build_plan_from_definition(definition)
    errors = validate_plan(plan)

    if errors:
        print("  ❌ Validation errors:")
        for e in errors:
            print(f"    → {e}")
        return 1

    save_plan(root, plan)

    if args.json:
        print(json.dumps({"plan_id": plan.plan_id, "tasks": len(plan.tasks)}, indent=2))
    else:
        layers = topological_layers(plan)
        cp = critical_path(plan)
        print(f"\n  📐 HPE Plan créé : {plan.plan_id}")
        print(f"  📝 {plan.description}")
        print(f"  📊 {len(plan.tasks)} tasks, {len(layers)} waves")
        print("\n  Waves :")
        for i, layer in enumerate(layers):
            print(f"    Wave {i}: {', '.join(layer)}")
        if cp:
            print(f"\n  🔥 Critical path : {' → '.join(cp)}")

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    plan = load_plan(root, args.plan_id)
    if not plan:
        print(f"  ❌ Plan {args.plan_id} not found", file=sys.stderr)
        return 1

    plan, results = run_plan(plan, root)

    if args.json:
        print(json.dumps(get_plan_status(plan), indent=2, ensure_ascii=False))
    else:
        status = get_plan_status(plan)
        print(f"\n  🏗️  HPE Run — {plan.plan_id}")
        print(f"  État : {_status_icon(status['state'])} {status['state']}")
        print(f"  Progression : {status['progress_pct']}%")
        print(f"  Vagues : {len(results)}")
        for wr in results:
            s_count = len(wr.succeeded)
            f_count = len(wr.failed)
            print(f"    Wave {wr.wave_number}: ✅ {s_count} / ❌ {f_count}")
        if status["outputs_available"]:
            print(f"  Outputs : {', '.join(status['outputs_available'])}")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    plan = load_plan(root, args.plan_id)
    if not plan:
        print(f"  ❌ Plan {args.plan_id} not found", file=sys.stderr)
        return 1

    status = get_plan_status(plan)

    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(f"\n  📊 HPE Status — {plan.plan_id}")
        print(f"  État : {_status_icon(status['state'])} {status['state']}")
        print(f"  Progression : {status['progress_pct']}%")
        print(f"  Tâches : {status['total_tasks']} total")
        for st, count in sorted(status["by_status"].items()):
            print(f"    {_status_icon(st)} {st}: {count}")
        if status["critical_path"]:
            print(f"\n  🔥 Critical path : {' → '.join(status['critical_path'])}")

    return 0


def cmd_critical(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    plan = load_plan(root, args.plan_id)
    if not plan:
        print(f"  ❌ Plan {args.plan_id} not found", file=sys.stderr)
        return 1

    cp = critical_path(plan)
    layers = topological_layers(plan)

    if args.json:
        print(json.dumps({"critical_path": cp, "layers": layers}, indent=2))
    else:
        print(f"\n  🔥 Critical Path — {plan.plan_id}")
        if cp:
            for i, tid in enumerate(cp):
                task = _find_task(plan, tid)
                agent = task.get("agent", "?") if task else "?"
                print(f"    {i+1}. {tid} ({agent})")
        else:
            print("    (empty)")

    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    checkpoint = load_checkpoint(root, args.checkpoint)
    if not checkpoint:
        print(f"  ❌ Checkpoint {args.checkpoint} not found", file=sys.stderr)
        return 1

    plan = load_plan(root, checkpoint.plan_id)
    if not plan:
        print(f"  ❌ Plan {checkpoint.plan_id} not found", file=sys.stderr)
        return 1

    plan = restore_from_checkpoint(plan, checkpoint)
    plan, results = run_plan(plan, root)

    if args.json:
        print(json.dumps(get_plan_status(plan), indent=2, ensure_ascii=False))
    else:
        print(f"  🔄 Reprise depuis {args.checkpoint}")
        print(f"  État : {_status_icon(plan.state)} {plan.state}")
        print(f"  Vagues supplémentaires : {len(results)}")

    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HPE Runner — Hybrid Parallelism Engine execution runtime",
    )
    parser.add_argument("--project-root", default=".", help="Project root")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--version", action="version", version=f"hpe-runner {HPE_VERSION}")

    sub = parser.add_subparsers(dest="command")

    p_plan = sub.add_parser("plan", help="Build a plan from definition file")
    p_plan.add_argument("--file", required=True, help="Path to YAML/JSON definition")

    p_run = sub.add_parser("run", help="Execute an HPE plan")
    p_run.add_argument("--plan-id", required=True)

    p_status = sub.add_parser("status", help="Plan status")
    p_status.add_argument("--plan-id", required=True)

    p_critical = sub.add_parser("critical", help="Show critical path")
    p_critical.add_argument("--plan-id", required=True)

    p_resume = sub.add_parser("resume", help="Resume from checkpoint")
    p_resume.add_argument("--checkpoint", required=True)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "plan": cmd_plan,
        "run": cmd_run,
        "status": cmd_status,
        "critical": cmd_critical,
        "resume": cmd_resume,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
