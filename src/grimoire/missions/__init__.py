"""Mission Ledger — append-only JSONL-based mission and task tracking.

Source of truth for all mission/task state in the Grimoire Agent OS.

Les exports sont paresseux (PEP 562) : importer un sous-module léger
(``grimoire.missions.lease``, ``grimoire.missions.pause``) passe par ce paquet,
et l'import eager du ledger coûtait ~60 ms à chaque appel de ``grimoire-hook``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import (
        Incident,
        LedgerEvent,
        Mission,
        MissionState,
        MissionTask,
        RiskProfile,
        TaskDependency,
        TaskState,
        TaskType,
    )
    from grimoire.missions.task_flow_adapter import TaskFlowImportReport, import_task_flow_events

_EXPORTS = {
    "Incident": "grimoire.missions.schemas",
    "LedgerEvent": "grimoire.missions.schemas",
    "Mission": "grimoire.missions.schemas",
    "MissionLedger": "grimoire.missions.ledger",
    "MissionState": "grimoire.missions.schemas",
    "MissionTask": "grimoire.missions.schemas",
    "RiskProfile": "grimoire.missions.schemas",
    "TaskDependency": "grimoire.missions.schemas",
    "TaskFlowImportReport": "grimoire.missions.task_flow_adapter",
    "TaskState": "grimoire.missions.schemas",
    "TaskType": "grimoire.missions.schemas",
    "import_task_flow_events": "grimoire.missions.task_flow_adapter",
}

__all__ = [
    "Incident",
    "LedgerEvent",
    "Mission",
    "MissionLedger",
    "MissionState",
    "MissionTask",
    "RiskProfile",
    "TaskDependency",
    "TaskFlowImportReport",
    "TaskState",
    "TaskType",
    "import_task_flow_events",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
