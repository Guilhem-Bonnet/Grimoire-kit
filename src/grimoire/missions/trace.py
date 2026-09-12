"""Timeline unifiée d'une tâche — ce qui lui est arrivé, toutes sources confondues.

Le matériel de troubleshooting existait déjà, dispersé dans des journaux qui
portent chacun le ``task_id`` : le Mission Ledger (transitions, incidents), le
TraceLedger (ce que les hooks ont autorisé ou refusé, les gates de tâche
rouges, les décisions de dispatch d'agent), le RuntimeKernel (run events,
checkpoints, ``abort_reason``), l'EvidenceService (packs, verdicts) et,
lorsqu'il existe, l'export OTel GenAI du TraceLedger (``grimoire task
trace-export --format otel``, #322). Rien ne les lisait ensemble : trouver
pourquoi une tâche a échoué voulait dire ouvrir plusieurs fichiers JSONL.

Ce module les indexe par tâche et les trie dans le temps. Il **lit** ; il ne
crée aucun dossier — un journal absent est une source absente, dite comme
telle, jamais un dossier vide semé au passage. Il ne passe ni par la stack
legacy ``observatory.py`` ni par une donnée de démonstration : une timeline
vide est une timeline vide.

La corrélation ne se fait jamais par heuristique textuelle : chaque source
porte déjà ``task_id`` (ou, pour l'export OTel, l'attribut
``grimoire.task_id`` sur le span parent, propagé aux spans enfants par leur
``traceId`` partagé).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.standard_generation import TRACES_DIR
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import IncidentStatus, MissionTask, TaskState

__all__ = [
    "DEFAULT_EVIDENCE_RELPATH",
    "DEFAULT_KERNEL_RELPATH",
    "DEFAULT_LEDGER_RELPATH",
    "DEFAULT_OTEL_EXPORT_RELPATH",
    "TaskTimeline",
    "TimelineEntry",
    "build_task_timeline",
]

DEFAULT_LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
DEFAULT_KERNEL_RELPATH = Path("_grimoire-runtime-output/runtime")
DEFAULT_EVIDENCE_RELPATH = Path("_grimoire-runtime-output/evidence")
#: Emplacement conventionnel de l'export OTel du TraceLedger. ``grimoire task
#: trace-export`` accepte n'importe quelle destination ; écrire ici la rend
#: visible dans la timeline sans configuration supplémentaire. Un fichier
#: absent est une source absente comme les autres — jamais une erreur.
DEFAULT_OTEL_EXPORT_RELPATH = TRACES_DIR / "otel-export.jsonl"

#: États du ledger qui sont, en eux-mêmes, une cause d'arrêt.
_STOPPED_STATES = {TaskState.BLOCKED.value, TaskState.FAILED.value}
#: Recette que le gateway de hooks écrit quand une clôture est refusée.
_EVIDENCE_GATE_RECIPE = "grimoire.evidence-gate"
#: Recette que le service des tâches écrit quand un gate de transition refuse.
TASK_GATE_RECIPE = "grimoire.task-gate"


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """Un fait daté, avec sa source et s'il explique un échec."""

    at: str
    source: str
    kind: str
    summary: str
    failure: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "source": self.source,
            "kind": self.kind,
            "summary": self.summary,
            "failure": self.failure,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class TaskTimeline:
    task_id: str
    task: MissionTask | None
    entries: tuple[TimelineEntry, ...]
    #: Source → chemin lu, ou ``None`` quand le journal n'existe pas.
    sources: dict[str, str | None]

    @property
    def causes(self) -> tuple[TimelineEntry, ...]:
        """Les entrées qui expliquent un arrêt : refus, gate rouge, abort, verdict échoué."""
        return tuple(e for e in self.entries if e.failure)

    @property
    def is_empty(self) -> bool:
        return not self.entries and self.task is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task.to_dict() if self.task is not None else None,
            "sources": self.sources,
            "entries": [e.to_dict() for e in self.entries],
            "causes": [e.to_dict() for e in self.causes],
        }


def _abs(project_root: Path, rel: Path) -> Path:
    return rel if rel.is_absolute() else project_root / rel


def build_task_timeline(
    project_root: Path,
    task_id: str,
    *,
    ledger_root: Path = DEFAULT_LEDGER_RELPATH,
    kernel_root: Path = DEFAULT_KERNEL_RELPATH,
    traces_root: Path = TRACES_DIR,
    evidence_root: Path = DEFAULT_EVIDENCE_RELPATH,
    otel_export_relpath: Path = DEFAULT_OTEL_EXPORT_RELPATH,
) -> TaskTimeline:
    """Assemble la timeline de *task_id* depuis les journaux présents sous *project_root*."""
    root = project_root.resolve()
    entries: list[TimelineEntry] = []
    sources: dict[str, str | None] = {}
    task: MissionTask | None = None

    ledger_path = _abs(root, ledger_root)
    if (ledger_path / "events.jsonl").is_file():
        sources["ledger"] = str(ledger_path)
        ledger = MissionLedger(ledger_path)
        task = ledger.get_task(task_id)
        entries.extend(_ledger_entries(ledger, task_id))
    else:
        sources["ledger"] = None

    traces_path = _abs(root, traces_root)
    if (traces_path / "traces.jsonl").is_file():
        sources["hooks"] = str(traces_path)
        entries.extend(_trace_entries(traces_path, task_id))
    else:
        sources["hooks"] = None

    kernel_path = _abs(root, kernel_root)
    if (kernel_path / "instances.jsonl").is_file():
        sources["runtime"] = str(kernel_path)
        entries.extend(_runtime_entries(kernel_path, task_id))
    else:
        sources["runtime"] = None

    evidence_path = _abs(root, evidence_root)
    if evidence_path.is_dir() and any(evidence_path.iterdir()):
        sources["evidence"] = str(evidence_path)
        entries.extend(_evidence_entries(evidence_path, task_id))
    else:
        sources["evidence"] = None

    otel_path = _abs(root, otel_export_relpath)
    if otel_path.is_file():
        sources["otel"] = str(otel_path)
        entries.extend(_otel_entries(otel_path, task_id))
    else:
        sources["otel"] = None

    # Tri stable : à date égale, l'ordre d'écriture est conservé.
    entries.sort(key=lambda e: e.at)
    return TaskTimeline(task_id=task_id, task=task, entries=tuple(entries), sources=sources)


# ── Mission Ledger ──────────────────────────────────────────────────────────

def _ledger_entries(ledger: MissionLedger, task_id: str) -> list[TimelineEntry]:
    out: list[TimelineEntry] = []
    for event in ledger.list_events(task_id):
        payload = event.payload
        if event.event_type == "task.created":
            out.append(TimelineEntry(event.created_at, "ledger", event.event_type,
                                     f"tâche ouverte par {event.actor_id} : {payload.get('title', '')}"))
        elif event.event_type == "task.transitioned":
            to_state = str(payload.get("to_state", ""))
            reason = str(payload.get("reason", "") or "")
            summary = f"{payload.get('from_state', '?')} → {to_state} par {event.actor_id}"
            if reason:
                summary += f" — {reason}"
            claim = payload.get("claim")
            if isinstance(claim, dict):
                summary += f" (claim {claim.get('actor_id', '?')}@{claim.get('host_id', '?')})"
            out.append(TimelineEntry(event.created_at, "ledger", event.event_type, summary,
                                     failure=to_state in _STOPPED_STATES,
                                     detail={"to_state": to_state, "reason": reason}))
        elif event.event_type == "task.linked":
            out.append(TimelineEntry(event.created_at, "ledger", event.event_type,
                                     f"dépend de {payload.get('depends_on', '?')} ({payload.get('kind', '?')})"))
        else:
            out.append(TimelineEntry(event.created_at, "ledger", event.event_type, event.event_type))
    for incident in ledger.list_incidents(task_id):
        ouvert = incident.status is IncidentStatus.OPEN
        causes = "; ".join(incident.causes)
        out.append(TimelineEntry(
            incident.created_at, "incident", f"incident.{incident.status.value}",
            f"[{incident.severity.value}] {incident.kind} : {incident.summary}" + (f" — causes : {causes}" if causes else ""),
            failure=ouvert,
            detail={"id": incident.id, "recommended_actions": list(incident.recommended_actions)},
        ))
    return out


# ── TraceLedger : hooks et gates de tâche ───────────────────────────────────

#: Étiquette lisible pour les tags de dispatch d'agent (issue #366/#389) —
#: c'est le seul type d'écriture de :mod:`grimoire.hosts.decisions` qui ne
#: porte ni un des deux recipe_id de gate ci-dessus, ni un appel d'outil.
_DISPATCH_LABEL = {
    "agent.dispatch": "agent choisi",
    "agent.miss": "non-choix d'agent (aucun spécialiste trouvé)",
}


def _trace_entries(traces_path: Path, task_id: str) -> list[TimelineEntry]:
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.schemas import TraceOutcome

    out: list[TimelineEntry] = []
    for trace in TraceLedger(traces_path).list_traces(task_id=task_id):
        failed = trace.outcome is TraceOutcome.FAILURE
        before = len(out)
        if trace.recipe_id == TASK_GATE_RECIPE:
            manque = ", ".join(pv.verdict_id for pv in trace.policy_verdicts) or "preuve manquante"
            transition = next((t for t in trace.tags if t != "task.gate"), "")
            out.append(TimelineEntry(
                trace.started_at, "gate", "task.gate.refused",
                f"gate « {transition} » refuse la transition — manque : {manque}",
                failure=True, detail={"trace_id": trace.id, "missing": [pv.verdict_id for pv in trace.policy_verdicts]},
            ))
            continue
        if trace.recipe_id == _EVIDENCE_GATE_RECIPE:
            out.append(TimelineEntry(
                trace.started_at, "hooks", "stop.gate",
                "clôture de session refusée : gates de preuve rouges" if failed else "clôture de session autorisée",
                failure=failed, detail={"trace_id": trace.id, "host": trace.host_id},
            ))
        for call in trace.tool_calls:
            blocked = call.verdict == "block"
            verbe = {"block": "refusé par la policy", "ask": "soumis à confirmation"}.get(call.verdict, "autorisé")
            out.append(TimelineEntry(
                trace.started_at, "hooks", f"tool.{call.verdict}",
                f"{call.tool} {verbe} (args {call.args_hash})",
                failure=blocked, detail={"trace_id": trace.id, "host": trace.host_id, "tool": call.tool},
            ))
        if len(out) == before:
            # Ni gate, ni appel d'outil : un dispatch d'agent (`agent.dispatch`,
            # `agent.miss`) ou tout autre fait futur écrit au TraceLedger sous
            # cette tâche. Sans ce repli, la trace existait au disque et
            # disparaissait en silence de la timeline — le trou de corrélation
            # que #139 cite pour `GrimoireEvent` existait tout autant ici.
            label = next((v for k, v in _DISPATCH_LABEL.items() if k in trace.tags), trace.recipe_id or "hook")
            agent = f" — {trace.agent_id}" if trace.agent_id else ""
            out.append(TimelineEntry(
                trace.started_at, "hooks", trace.recipe_id or "hooks.trace", f"{label}{agent}",
                failure=failed, detail={"trace_id": trace.id, "tags": list(trace.tags), "agent_id": trace.agent_id},
            ))
    return out


# ── Export OTel du TraceLedger (#322) ───────────────────────────────────────

def _iso_from_ns(nanoseconds: int) -> str:
    """Inverse de ``grimoire.traces.ledger._ns`` : nanosecondes → ISO 8601 UTC."""
    if not nanoseconds:
        return ""
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, tz=UTC).isoformat()


def _otel_entries(otel_path: Path, task_id: str) -> list[TimelineEntry]:
    """Lit un export OTel GenAI (``grimoire task trace-export --format otel``)
    et n'en retient que les spans qui concernent *task_id*.

    Corrélation par identifiants, jamais par heuristique textuelle : l'attribut
    ``grimoire.task_id`` porté par le span ``invoke_agent`` parent (voir
    ``TraceLedger._to_otel_spans``), puis le ``traceId`` que ce parent partage
    avec ses spans ``execute_tool`` enfants, qui eux ne portent pas le
    ``task_id`` directement.
    """
    try:
        raw_lines = otel_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    spans: list[dict[str, Any]] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            span = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(span, dict):
            spans.append(span)

    matched_trace_ids = {
        span.get("traceId")
        for span in spans
        if (span.get("attributes") or {}).get("grimoire.task_id") == task_id
    }

    out: list[TimelineEntry] = []
    for span in spans:
        attrs = span.get("attributes") or {}
        own_task_id = attrs.get("grimoire.task_id")
        if own_task_id != task_id and span.get("traceId") not in matched_trace_ids:
            continue
        status_code = str((span.get("status") or {}).get("code", ""))
        failed = status_code == "STATUS_CODE_ERROR"
        operation = attrs.get("gen_ai.operation.name", "span")
        name = span.get("name") or operation
        tool = attrs.get("gen_ai.tool.name")
        summary = f"span {name}" + (f" — outil {tool}" if tool else "")
        if failed:
            summary += " — erreur"
        out.append(TimelineEntry(
            _iso_from_ns(span.get("startTimeUnixNano") or 0), "otel", f"otel.{operation}", summary,
            failure=failed,
            detail={"traceId": span.get("traceId"), "spanId": span.get("spanId"), "attributes": attrs},
        ))
    return out


# ── RuntimeKernel ───────────────────────────────────────────────────────────

def _runtime_entries(kernel_path: Path, task_id: str) -> list[TimelineEntry]:
    from grimoire.runtime.kernel import RuntimeKernel
    from grimoire.runtime.schemas import RunEventType

    failing = {RunEventType.TOOL_BLOCKED, RunEventType.STEP_FAILED, RunEventType.WORKFLOW_ABORTED, RunEventType.WORKFLOW_REFUSED}
    kernel = RuntimeKernel(kernel_path)
    out: list[TimelineEntry] = []
    for wfi in kernel.list_instances(task_id):
        for event in kernel.get_run_events(wfi.id):
            payload = event.payload
            summary = f"{event.event_type.value} ({wfi.recipe_id})"
            if event.event_type is RunEventType.WORKFLOW_ABORTED:
                summary = f"workflow {wfi.id} abandonné — raison : {payload.get('reason') or wfi.abort_reason or 'non renseignée'}"
            elif event.event_type is RunEventType.WORKFLOW_REFUSED:
                summary = f"workflow {wfi.id} refusé (plafond MAST) — raison : {payload.get('reason') or wfi.abort_reason or 'non renseignée'}"
            elif event.event_type in (RunEventType.TOOL_BLOCKED, RunEventType.TOOL_COMPLETED, RunEventType.TOOL_REQUESTED):
                summary = f"outil {payload.get('tool_name', '?')} — {event.event_type.value.split('.')[1]}"
            elif event.event_type in (RunEventType.STEP_STARTED, RunEventType.STEP_COMPLETED, RunEventType.STEP_FAILED):
                summary = f"étape {payload.get('step_id', event.payload.get('step', '?'))} — {event.event_type.value.split('.')[1]}"
            elif event.event_type is RunEventType.CHECKPOINT_SAVED:
                summary = f"checkpoint {payload.get('checkpoint_id', '?')} sauvé (étape {payload.get('step_id', '?')})"
            elif event.event_type is RunEventType.CHECKPOINT_RESUMED:
                summary = f"reprise depuis le checkpoint {payload.get('checkpoint_id') or 'aucun'}"
            out.append(TimelineEntry(
                event.created_at, "runtime", event.event_type.value, summary,
                failure=event.event_type in failing,
                detail={"workflow_instance_id": wfi.id, "run_id": event.run_id, "payload": payload},
            ))
        for chk in kernel.list_checkpoints(wfi.id):
            reprise = "reprise sûre" if chk.safe_to_resume else "reprise non sûre"
            out.append(TimelineEntry(
                chk.created_at, "runtime", "checkpoint",
                f"checkpoint {chk.id} à l'étape {chk.step_id} — {reprise}, "
                f"{len(chk.state.completed_steps)} étape(s) faite(s), {len(chk.state.pending_steps)} restante(s)",
                detail={"workflow_instance_id": wfi.id, "checkpoint_id": chk.id, "safe_to_resume": chk.safe_to_resume},
            ))
    return out


# ── EvidenceService ─────────────────────────────────────────────────────────

def _evidence_entries(evidence_path: Path, task_id: str) -> list[TimelineEntry]:
    from grimoire.evidence import EvidenceService, VerdictResult

    service = EvidenceService(evidence_path)
    out: list[TimelineEntry] = []
    for pack in service.list_packs(task_id):
        couverture = ""
        if pack.coverage is not None and pack.coverage.acceptance_missing:
            couverture = " — critères non couverts : " + ", ".join(pack.coverage.acceptance_missing)
        out.append(TimelineEntry(
            pack.created_at, "evidence", "evidence.pack",
            f"pack {pack.id} : {len(pack.items)} preuve(s), profil {pack.profile.value}{couverture}",
            detail={"pack_id": pack.id},
        ))
    for verdict in service.list_verdicts(task_id):
        passed = verdict.verdict is VerdictResult.PASSED
        echecs = [c for c in verdict.checks if c.result is not VerdictResult.PASSED]
        raisons = "; ".join(f"{c.id} : {c.reason}" for c in echecs if c.reason) or "; ".join(c.id for c in echecs)
        out.append(TimelineEntry(
            verdict.created_at, "evidence", f"verdict.{verdict.verdict.value}",
            f"verdict {verdict.verdict.value} sur {verdict.evidence_pack_id}" + (f" — {raisons}" if raisons else ""),
            failure=not passed, detail={"verdict_id": verdict.id, "pack_id": verdict.evidence_pack_id},
        ))
    return out
