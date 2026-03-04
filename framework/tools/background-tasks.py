#!/usr/bin/env python3
"""
background-tasks.py — Background Agent Tasks BMAD (BM-44 Story 5.3).
============================================================

Permet à des agents de travailler en arrière-plan (analyse,
consolidation, veille) pendant que l'utilisateur interagit
avec d'autres agents.

Les tâches background écrivent dans _bmad-output/.background/<task-id>/
et notifient quand elles sont terminées.

Modes :
  start     — Lance une tâche background
  status    — Affiche l'état des tâches en cours
  check-in  — Affiche les résultats intermédiaires d'une tâche
  cancel    — Annule une tâche en cours
  list      — Liste l'historique des tâches
  clean     — Supprime les tâches terminées

Usage :
  python3 background-tasks.py --project-root . start --agent architect \\
    --task "Analyze codebase architecture patterns"
  python3 background-tasks.py --project-root . status
  python3 background-tasks.py --project-root . check-in --id bg-abc123
  python3 background-tasks.py --project-root . cancel --id bg-abc123

Stdlib only.

Références :
  - Celery task queue: https://docs.celeryq.dev/
  - Claude computer use background tasks: https://docs.anthropic.com/en/docs/agents-and-tools/computer-use
  - AutoGen async agents: https://microsoft.github.io/autogen/docs/tutorial/conversation-patterns/
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

BACKGROUND_TASKS_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

BACKGROUND_DIR = "_bmad-output/.background"
MAX_CONCURRENT_TASKS = 2
TASK_TIMEOUT_SECONDS = 3600  # 1 hour default
HEARTBEAT_INTERVAL = 30  # seconds

VALID_TASK_TYPES = frozenset({
    "analysis",
    "consolidation",
    "indexing",
    "testing",
    "documentation",
    "review",
    "monitoring",
    "custom",
})

VALID_STATUSES = frozenset({
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timeout",
})


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class TaskProgress:
    """Progrès d'une tâche."""

    percentage: float = 0.0
    current_step: str = ""
    total_steps: int = 0
    completed_steps: int = 0
    last_update: str = ""
    intermediate_results: list[str] = field(default_factory=list)


@dataclass
class BackgroundTask:
    """Définition d'une tâche background."""

    task_id: str = ""
    agent: str = ""
    task_type: str = "custom"
    description: str = ""
    status: str = "pending"
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    pid: int = 0
    progress: TaskProgress = field(default_factory=TaskProgress)
    result_path: str = ""
    error: str = ""
    timeout_seconds: int = TASK_TIMEOUT_SECONDS
    context: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"bg-{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> BackgroundTask:
        progress_data = data.get("progress", {})
        progress = TaskProgress(**{
            k: v for k, v in progress_data.items()
            if k in TaskProgress.__dataclass_fields__
        })
        return cls(
            task_id=data.get("task_id", ""),
            agent=data.get("agent", ""),
            task_type=data.get("task_type", "custom"),
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            pid=data.get("pid", 0),
            progress=progress,
            result_path=data.get("result_path", ""),
            error=data.get("error", ""),
            timeout_seconds=data.get("timeout_seconds", TASK_TIMEOUT_SECONDS),
            context=data.get("context", {}),
        )

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "cancelled", "timeout")

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0.0
        try:
            start = time.mktime(time.strptime(self.started_at, "%Y-%m-%dT%H:%M:%SZ"))
            if self.completed_at:
                end = time.mktime(time.strptime(self.completed_at, "%Y-%m-%dT%H:%M:%SZ"))
            else:
                end = time.time()
            return max(0.0, end - start)
        except (ValueError, OverflowError):
            return 0.0


@dataclass
class TaskList:
    """Liste des tâches background."""

    tasks: list[BackgroundTask] = field(default_factory=list)
    running_count: int = 0
    total_count: int = 0
    max_concurrent: int = MAX_CONCURRENT_TASKS


# ── Background Task Manager ─────────────────────────────────────────────────


class BackgroundTaskManager:
    """
    Gère les tâches background BMAD.

    Chaque tâche a son propre répertoire dans _bmad-output/.background/<task-id>/
    avec un manifest.json, un result.md et des logs.
    """

    def __init__(self, project_root: Path, max_concurrent: int = MAX_CONCURRENT_TASKS):
        self.project_root = project_root
        self.bg_dir = project_root / BACKGROUND_DIR
        self.bg_dir.mkdir(parents=True, exist_ok=True)
        self.max_concurrent = max_concurrent

    def _task_dir(self, task_id: str) -> Path:
        return self.bg_dir / task_id

    def _save_task(self, task: BackgroundTask) -> None:
        task_dir = self._task_dir(task.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        manifest = task_dir / "manifest.json"
        manifest.write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_task(self, task_id: str) -> BackgroundTask | None:
        manifest = self._task_dir(task_id) / "manifest.json"
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return BackgroundTask.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def _is_process_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _count_running(self) -> int:
        count = 0
        if self.bg_dir.exists():
            for d in self.bg_dir.iterdir():
                if d.is_dir():
                    task = self._load_task(d.name)
                    if task and task.status == "running":
                        if self._is_process_alive(task.pid):
                            count += 1
                        else:
                            # Process died — mark as failed
                            task.status = "failed"
                            task.error = "Process died unexpectedly"
                            task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            self._save_task(task)
        return count

    def start(
        self,
        agent: str,
        description: str,
        task_type: str = "custom",
        context: dict | None = None,
        timeout_seconds: int = TASK_TIMEOUT_SECONDS,
    ) -> BackgroundTask:
        """
        Lance une nouvelle tâche background.

        La tâche crée un répertoire de résultats et démarre
        un processus simulé (en production, exécuterait un agent réel).
        """
        # Check concurrent limit
        running = self._count_running()
        if running >= self.max_concurrent:
            raise RuntimeError(
                f"Maximum {self.max_concurrent} tâches simultanées atteint "
                f"({running} en cours). Annulez une tâche ou attendez."
            )

        task = BackgroundTask(
            agent=agent,
            task_type=task_type,
            description=description,
            status="running",
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            timeout_seconds=timeout_seconds,
            context=context or {},
        )

        task_dir = self._task_dir(task.task_id)
        task.result_path = str(task_dir / "result.md")

        # Create initial result file
        task_dir.mkdir(parents=True, exist_ok=True)
        result_file = task_dir / "result.md"
        result_file.write_text(
            f"# Background Task: {task.description}\n\n"
            f"Agent: {task.agent}\n"
            f"Type: {task.task_type}\n"
            f"Started: {task.started_at}\n"
            f"Status: running\n\n"
            f"---\n\n"
            f"*En cours de traitement...*\n",
            encoding="utf-8",
        )

        # Save manifest
        self._save_task(task)

        return task

    def get_status(self, task_id: str) -> BackgroundTask | None:
        """Récupère le statut d'une tâche."""
        task = self._load_task(task_id)
        if not task:
            return None

        # Check if process is still alive
        if task.status == "running" and task.pid > 0:
            if not self._is_process_alive(task.pid):
                task.status = "completed"
                task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save_task(task)

        # Check timeout
        if task.status == "running" and task.elapsed_seconds > task.timeout_seconds:
            task.status = "timeout"
            task.error = f"Timeout après {task.timeout_seconds}s"
            task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save_task(task)

        return task

    def check_in(self, task_id: str) -> dict:
        """
        Check-in sur une tâche — retourne les résultats intermédiaires.
        """
        task = self._load_task(task_id)
        if not task:
            return {"error": f"Tâche '{task_id}' non trouvée"}

        result = {
            "task_id": task.task_id,
            "status": task.status,
            "agent": task.agent,
            "elapsed_seconds": round(task.elapsed_seconds, 1),
            "progress": asdict(task.progress),
        }

        # Read result file if exists
        result_file = self._task_dir(task_id) / "result.md"
        if result_file.exists():
            result["result_content"] = result_file.read_text(encoding="utf-8")

        # Read log file if exists
        log_file = self._task_dir(task_id) / "task.log"
        if log_file.exists():
            result["log_tail"] = log_file.read_text(encoding="utf-8")[-2000:]

        return result

    def cancel(self, task_id: str) -> BackgroundTask | None:
        """Annule une tâche en cours."""
        task = self._load_task(task_id)
        if not task:
            return None

        if task.is_terminal:
            return task  # Already finished

        # Kill process if running
        if task.pid > 0 and self._is_process_alive(task.pid):
            try:
                os.kill(task.pid, signal.SIGTERM)
                time.sleep(0.5)
                if self._is_process_alive(task.pid):
                    os.kill(task.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

        task.status = "cancelled"
        task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save_task(task)

        return task

    def complete(self, task_id: str, result_content: str = "") -> BackgroundTask | None:
        """Marque une tâche comme terminée (appelé par l'agent)."""
        task = self._load_task(task_id)
        if not task:
            return None

        task.status = "completed"
        task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        task.progress.percentage = 100.0

        if result_content:
            result_file = self._task_dir(task_id) / "result.md"
            result_file.write_text(result_content, encoding="utf-8")

        self._save_task(task)
        return task

    def update_progress(
        self,
        task_id: str,
        percentage: float = 0.0,
        current_step: str = "",
        intermediate_result: str = "",
    ) -> BackgroundTask | None:
        """Met à jour le progrès d'une tâche."""
        task = self._load_task(task_id)
        if not task or task.is_terminal:
            return None

        task.progress.percentage = min(100.0, percentage)
        if current_step:
            task.progress.current_step = current_step
        task.progress.last_update = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if intermediate_result:
            task.progress.intermediate_results.append(intermediate_result)

        self._save_task(task)
        return task

    def list_tasks(self, include_completed: bool = True) -> TaskList:
        """Liste toutes les tâches."""
        tasks = []
        running_count = 0

        if self.bg_dir.exists():
            for d in sorted(self.bg_dir.iterdir()):
                if d.is_dir():
                    task = self._load_task(d.name)
                    if task:
                        # Refresh status
                        task = self.get_status(task.task_id)
                        if task:
                            if not include_completed and task.is_terminal:
                                continue
                            tasks.append(task)
                            if task.status == "running":
                                running_count += 1

        return TaskList(
            tasks=tasks,
            running_count=running_count,
            total_count=len(tasks),
            max_concurrent=self.max_concurrent,
        )

    def clean(self, keep_days: int = 7) -> int:
        """Supprime les tâches terminées plus anciennes que keep_days."""
        cleaned = 0
        cutoff = time.time() - (keep_days * 86400)

        if self.bg_dir.exists():
            for d in list(self.bg_dir.iterdir()):
                if d.is_dir():
                    task = self._load_task(d.name)
                    if task and task.is_terminal:
                        try:
                            completed_time = time.mktime(
                                time.strptime(task.completed_at, "%Y-%m-%dT%H:%M:%SZ")
                            ) if task.completed_at else 0
                            if completed_time < cutoff:
                                import shutil
                                shutil.rmtree(d)
                                cleaned += 1
                        except (ValueError, OSError):
                            continue
        return cleaned


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_background_task(
    project_root: str,
    action: str = "status",
    agent: str = "",
    description: str = "",
    task_id: str = "",
    task_type: str = "custom",
) -> dict:
    """
    MCP tool `bmad_background_task` — gère les tâches background.
    """
    root = Path(project_root).resolve()
    manager = BackgroundTaskManager(root)

    if action == "start":
        if not agent or not description:
            return {"error": "Les paramètres 'agent' et 'description' sont requis"}
        try:
            task = manager.start(agent=agent, description=description, task_type=task_type)
            return {"success": True, "task": task.to_dict()}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
    elif action == "status":
        if task_id:
            task = manager.get_status(task_id)
            return task.to_dict() if task else {"error": f"Tâche '{task_id}' non trouvée"}
        task_list = manager.list_tasks()
        return {
            "running": task_list.running_count,
            "total": task_list.total_count,
            "max_concurrent": task_list.max_concurrent,
            "tasks": [t.to_dict() for t in task_list.tasks],
        }
    elif action == "check-in":
        if not task_id:
            return {"error": "Le paramètre 'task_id' est requis"}
        return manager.check_in(task_id)
    elif action == "cancel":
        if not task_id:
            return {"error": "Le paramètre 'task_id' est requis"}
        task = manager.cancel(task_id)
        return task.to_dict() if task else {"error": f"Tâche '{task_id}' non trouvée"}
    else:
        return {"error": f"Action inconnue: {action}"}


# ── CLI ─────────────────────────────────────────────────────────────────────


def _status_icon(status: str) -> str:
    return {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "cancelled": "🚫",
        "timeout": "⏰",
    }.get(status, "❓")


def _print_task(task: BackgroundTask, verbose: bool = False) -> None:
    icon = _status_icon(task.status)
    elapsed = f" ({task.elapsed_seconds:.0f}s)" if task.elapsed_seconds > 0 else ""
    progress = f" [{task.progress.percentage:.0f}%]" if task.progress.percentage > 0 else ""
    print(f"    {icon} {task.task_id} │ {task.agent:>10s} │ "
          f"{task.status:>10s}{elapsed}{progress}")
    if verbose:
        print(f"      Description : {task.description}")
        if task.progress.current_step:
            print(f"      Step        : {task.progress.current_step}")
        if task.error:
            print(f"      Erreur      : {task.error}")


def _print_task_list(task_list: TaskList) -> None:
    print(f"\n  📋 Tâches Background ({task_list.total_count})")
    print(f"  {'─' * 60}")
    print(f"  En cours  : {task_list.running_count}/{task_list.max_concurrent}")
    print()

    if not task_list.tasks:
        print("  (aucune tâche)\n")
        return

    for task in task_list.tasks:
        _print_task(task)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Background Tasks — Tâches agents en arrière-plan BMAD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"background-tasks {BACKGROUND_TASKS_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # start
    start_p = sub.add_parser("start", help="Lancer une tâche background")
    start_p.add_argument("--agent", required=True, help="Agent exécutant")
    start_p.add_argument("--task", dest="description", required=True, help="Description de la tâche")
    start_p.add_argument("--type", dest="task_type", default="custom",
                         choices=sorted(VALID_TASK_TYPES), help="Type de tâche")
    start_p.add_argument("--timeout", type=int, default=TASK_TIMEOUT_SECONDS,
                         help=f"Timeout en secondes (défaut: {TASK_TIMEOUT_SECONDS})")
    start_p.add_argument("--json", action="store_true", help="Output JSON")

    # status
    status_p = sub.add_parser("status", help="Afficher l'état des tâches")
    status_p.add_argument("--json", action="store_true", help="Output JSON")

    # check-in
    ci_p = sub.add_parser("check-in", help="Résultats intermédiaires d'une tâche")
    ci_p.add_argument("--id", dest="task_id", required=True, help="ID de la tâche")
    ci_p.add_argument("--json", action="store_true", help="Output JSON")

    # cancel
    cancel_p = sub.add_parser("cancel", help="Annuler une tâche")
    cancel_p.add_argument("--id", dest="task_id", required=True, help="ID de la tâche")

    # list
    list_p = sub.add_parser("list", help="Historique des tâches")
    list_p.add_argument("--all", action="store_true", help="Inclure les terminées")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # clean
    clean_p = sub.add_parser("clean", help="Nettoyer les tâches terminées")
    clean_p.add_argument("--keep-days", type=int, default=7, help="Conserver N jours")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()
    manager = BackgroundTaskManager(project_root)

    if args.command == "start":
        try:
            task = manager.start(
                agent=args.agent,
                description=args.description,
                task_type=args.task_type,
                timeout_seconds=args.timeout,
            )
            if getattr(args, "json", False):
                print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"\n  🚀 Tâche lancée : {task.task_id}")
                print(f"    Agent : {task.agent}")
                print(f"    Type  : {task.task_type}")
                print(f"    Desc  : {task.description}")
                print(f"    Dir   : {task.result_path}\n")
        except RuntimeError as e:
            print(f"  ❌ {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "status":
        task_list = manager.list_tasks(include_completed=True)
        if getattr(args, "json", False):
            print(json.dumps({
                "running": task_list.running_count,
                "total": task_list.total_count,
                "tasks": [t.to_dict() for t in task_list.tasks],
            }, ensure_ascii=False, indent=2))
        else:
            _print_task_list(task_list)

    elif args.command == "check-in":
        result = manager.check_in(args.task_id)
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if "error" in result:
                print(f"  ❌ {result['error']}", file=sys.stderr)
                sys.exit(1)
            print(f"\n  📊 Check-in : {result['task_id']}")
            print(f"  Status  : {result['status']}")
            print(f"  Elapsed : {result['elapsed_seconds']}s")
            if result.get("result_content"):
                print("\n  Résultat :")
                print(f"  {'─' * 50}")
                print(result["result_content"][:2000])
            print()

    elif args.command == "cancel":
        task = manager.cancel(args.task_id)
        if task:
            print(f"\n  🚫 Tâche '{args.task_id}' annulée\n")
        else:
            print(f"  ❌ Tâche '{args.task_id}' non trouvée", file=sys.stderr)
            sys.exit(1)

    elif args.command == "list":
        include_all = getattr(args, "all", False)
        task_list = manager.list_tasks(include_completed=include_all)
        if getattr(args, "json", False):
            print(json.dumps({
                "tasks": [t.to_dict() for t in task_list.tasks],
                "total": task_list.total_count,
            }, ensure_ascii=False, indent=2))
        else:
            _print_task_list(task_list)

    elif args.command == "clean":
        cleaned = manager.clean(keep_days=args.keep_days)
        print(f"\n  🧹 {cleaned} tâches nettoyées\n")


if __name__ == "__main__":
    main()
