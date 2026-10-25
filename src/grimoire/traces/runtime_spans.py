"""Les spans OTel du journal du RuntimeKernel (R26).

Un run de flow n'écrit aucun ``TraceRecord`` : sa trace vit dans
``run_events.jsonl``, où chaque ``RunEvent`` porte déjà ``span_id`` et
``parent_span_id``. Plutôt que de recopier ces événements dans le TraceLedger
(deux vérités à tenir d'accord), l'export OTel lit le journal du kernel là où il
est et le projette en spans — une seule source par donnée. Rien n'est écrit
dans le journal ; un projet sans run n'ajoute aucun span.

Les identifiants du kernel (``span-workflow-<id>``) ne sont pas de l'hex OTel :
ils sont dérivés de façon déterministe (uuid5), pour qu'un parent et ses
enfants se retrouvent d'un export à l'autre. ``traceId`` suit la même règle que
les spans du TraceLedger (uuid5 du ``run_id``), donc un run qui a aussi un
``TraceRecord`` partage son trace.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from grimoire.runtime.kernel import RuntimeKernel
from grimoire.runtime.schemas import RunEvent, RunEventType

__all__ = ["DEFAULT_RUNTIME_RELPATH", "runtime_otel_spans"]

DEFAULT_RUNTIME_RELPATH = Path("_grimoire-runtime-output") / "runtime"

_FAILING = frozenset({
    RunEventType.TOOL_BLOCKED, RunEventType.STEP_FAILED,
    RunEventType.WORKFLOW_ABORTED, RunEventType.WORKFLOW_REFUSED,
})


def _ns(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)
    except (ValueError, OSError):
        return 0


def _span_hex(span_id: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"grimoire-runtime-span:{span_id}").hex[:16]


def _to_span(event: RunEvent) -> dict[str, Any]:
    at = _ns(event.created_at)
    attrs: dict[str, Any] = {
        "grimoire.source": "runtime-kernel",
        "grimoire.run_id": event.run_id,
        "grimoire.mission_id": event.mission_id,
        "grimoire.task_id": event.task_id,
        "grimoire.workflow_instance_id": event.workflow_instance_id,
        "grimoire.run_event.type": event.event_type.value,
        "grimoire.run_event.id": event.id,
    }
    step_id = event.payload.get("step_id")
    if step_id:
        attrs["grimoire.step_id"] = str(step_id)
    span: dict[str, Any] = {
        "traceId": uuid.uuid5(uuid.NAMESPACE_URL, event.run_id).hex,
        "spanId": _span_hex(event.span_id or event.id),
        "name": f"grimoire.run.{event.event_type.value}",
        "kind": "SPAN_KIND_INTERNAL",
        "startTimeUnixNano": at,
        "endTimeUnixNano": at,
        "status": {"code": "STATUS_CODE_ERROR" if event.event_type in _FAILING else "STATUS_CODE_OK"},
        "attributes": attrs,
        "resource": {
            "attributes": {
                "service.name": "grimoire-kit",
                "grimoire.host_id": event.host_id,
                "grimoire.agent_id": event.actor_id,
            }
        },
    }
    if event.parent_span_id:
        span["parentSpanId"] = _span_hex(event.parent_span_id)
    return span


def runtime_otel_spans(runtime_root: Path, *, mission_id: str | None = None) -> list[dict[str, Any]]:
    """Un span par événement du journal du kernel ; vide, sans rien créer, s'il n'y a pas de journal."""
    if not (runtime_root / "run_events.jsonl").is_file():
        return []
    events = RuntimeKernel(runtime_root).get_run_events()
    return [_to_span(e) for e in events if mission_id is None or e.mission_id == mission_id]
