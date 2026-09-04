from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any


def logical_follow_through_report_file(project_root: Path) -> Path:
    return project_root / "_grimoire-runtime-output" / "task-flow" / "logical-follow-through.json"


def logical_follow_through_signature(objective: str, task_labels: list[str]) -> str:
    normalized = "||".join(_compact_text(part, 180).lower() for part in [objective, *task_labels] if part.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]


def execute_logical_follow_through_tasks(
    project_root: Path,
    objective: str,
    logical_next_tasks: list[dict[str, Any]],
    task_specs: dict[str, dict[str, Any]],
    report_loader: Callable[[Path], dict[str, Any]],
    report_saver: Callable[[Path, dict[str, Any]], None],
    compact_text: Callable[[str, int], str],
    timestamp_now: Callable[[], str],
    kit_root_resolver: Callable[[Path], Path],
    task_flow_script_resolver: Callable[[Path], Path],
    python_resolver: Callable[[Path], str],
    subprocess_run: Callable[..., Any],
    timeout_error_cls: type[BaseException],
) -> dict[str, Any]:
    task_labels = [str(item.get("task") or "") for item in logical_next_tasks if str(item.get("task") or "")]
    if not task_labels:
        return {}

    signature = logical_follow_through_signature(objective, task_labels)
    report_path = logical_follow_through_report_file(project_root)
    existing_report = report_loader(report_path)
    if str(existing_report.get("signature") or "") == signature and str(existing_report.get("status") or "") == "completed":
        return {
            "signature": signature,
            "status": "already-satisfied",
            "executedTasks": list(existing_report.get("executedTasks", []) or task_labels),
            "failedTask": "",
            "results": list(existing_report.get("results", []) or []),
            "updatedAt": timestamp_now(),
        }

    task_flow_script = task_flow_script_resolver(project_root)
    if not task_flow_script.exists():
        report = {
            "signature": signature,
            "status": "unavailable",
            "executedTasks": [],
            "failedTask": "",
            "results": [],
            "updatedAt": timestamp_now(),
            "reason": "task-flow-script-missing",
        }
        report_saver(report_path, report)
        return report

    kit_root = kit_root_resolver(project_root)
    python_executable = python_resolver(kit_root)
    results: list[dict[str, Any]] = []
    executed_tasks: list[str] = []
    failed_task = ""
    status = "completed"

    for task_label in task_labels:
        spec = dict(task_specs.get(task_label, {}))
        if not spec:
            failed_task = task_label
            status = "unsupported-task"
            results.append(
                {
                    "task": task_label,
                    "returnCode": 1,
                    "stdout": "",
                    "stderr": "unsupported logical follow-through task",
                }
            )
            break

        command = list(spec.get("command", []))
        if command and command[0] == ".venv/bin/python":
            command[0] = python_executable

        wrapped_command = [
            "bash",
            str(task_flow_script),
            "--task",
            task_label,
            "--flow",
            str(spec.get("flow") or "quality"),
            "--kind",
            "task",
            "--",
            *command,
        ]

        try:
            completed = subprocess_run(
                wrapped_command,
                cwd=str(kit_root),
                capture_output=True,
                text=True,
                timeout=int(spec.get("timeoutSeconds") or 240),
            )
            result = {
                "task": task_label,
                "returnCode": int(completed.returncode),
                "stdout": compact_text(str(completed.stdout or ""), 400),
                "stderr": compact_text(str(completed.stderr or ""), 400),
            }
        except timeout_error_cls as exc:  # type: ignore[misc]
            failed_task = task_label
            status = "timeout"
            result = {
                "task": task_label,
                "returnCode": 124,
                "stdout": compact_text(str(getattr(exc, "stdout", "") or ""), 400),
                "stderr": compact_text(str(getattr(exc, "stderr", "") or ""), 400),
            }
            results.append(result)
            break

        results.append(result)
        if int(result["returnCode"]) != 0:
            failed_task = task_label
            status = "failed"
            break
        executed_tasks.append(task_label)

    if status == "completed":
        executed_tasks = task_labels

    report = {
        "signature": signature,
        "status": status,
        "executedTasks": executed_tasks,
        "failedTask": failed_task,
        "results": results,
        "updatedAt": timestamp_now(),
    }
    report_saver(report_path, report)
    return report


def _compact_text(text: str, limit: int = 240) -> str:
    cleaned = " ".join(text.split())
    if limit > 0:
        return cleaned[:limit]
    return cleaned