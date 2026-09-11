"""Trace and Eval Ledger — persist, query, and export run traces.

Stores TraceRecords to JSONL. Supports OTel GenAI JSONL export
without requiring the opentelemetry-sdk package.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.traces.otel_conventions import (
    ATTR_AGENT_NAME,
    ATTR_CONVERSATION_ID,
    ATTR_INPUT_TOKENS,
    ATTR_OPERATION_NAME,
    ATTR_OUTPUT_TOKENS,
    ATTR_PROVIDER_NAME,
    ATTR_REQUEST_MODEL,
    ATTR_TOOL_CALL_ID,
    ATTR_TOOL_NAME,
    OP_EXECUTE_TOOL,
    OP_INVOKE_AGENT,
    OTEL_EXPORT_SCHEMA_VERSION,
    PROVIDER_NAME,
    agent_span_name,
    model_span_name,
    tool_span_name,
)
from grimoire.traces.schemas import (
    PolicyVerdictRef,
    TokenUsage,
    ToolCallTrace,
    TraceOutcome,
    TraceRecord,
)

__all__ = [
    "AGENT_DISPATCH_TAG",
    "AGENT_MISS_TAG",
    "AgentFreshness",
    "FreshnessReport",
    "TraceLedger",
    "compute_agent_freshness",
]

#: Tag qui marque un enregistrement comme « un agent a été choisi » plutôt
#: qu'un gate de tâche ou un appel modèle — le seul filtre dont
#: ``TraceLedger.agent_dispatch_counts`` a besoin. Défini ici, pas au point
#: d'écriture (``hosts.decisions``), pour qu'écriture et lecture partagent
#: la même constante plutôt que deux chaînes qui pourraient diverger.
AGENT_DISPATCH_TAG = "agent.dispatch"

#: Tag qui marque un enregistrement comme un « non-choix » : le concierge a
#: cherché un spécialiste et n'en a trouvé aucun, ou s'est rabattu sur un
#: généraliste. Symétrique d'``AGENT_DISPATCH_TAG`` (issue #366), pour le
#: signal inverse que #389 rend mesurable.
AGENT_MISS_TAG = "agent.miss"

#: Clé d'agrégation pour un non-choix dont la spécialité cherchée n'a pas pu
#: être nommée — jamais ignoré : le non-choix reste un signal même sans nom.
#: Public (sans ``_``) : le déclencheur de propositions (issue #395) doit
#: pouvoir l'exclure de son parcours sans dupliquer le littéral — une
#: spécialité sans nom ne peut pas devenir un slug de fichier.
UNNAMED_SPECIALTY = "(non nommée)"
_UNNAMED_SPECIALTY = UNNAMED_SPECIALTY

_OTEL_SPAN_KIND_INTERNAL = "SPAN_KIND_INTERNAL"
_OTEL_STATUS_OK = "STATUS_CODE_OK"
_OTEL_STATUS_ERROR = "STATUS_CODE_ERROR"


@dataclass(frozen=True, slots=True)
class AgentFreshness:
    """Fraîcheur d'un agent au regard du seuil configuré (issue #396).

    ``last_seen``/``days_since`` sont ``None`` quand l'agent n'apparaît dans
    aucun ``agent.dispatch`` du journal — « jamais », pas « il y a 0 jour ».
    ``stale`` ne vaut jamais ``True`` quand le rapport parent n'est pas
    :attr:`FreshnessReport.judged` : sans assez d'historique, aucun agent
    n'est déclaré périmé (voir le docstring de :func:`compute_agent_freshness`).
    """

    name: str
    last_seen: str | None
    days_since: int | None
    stale: bool


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    """Verdict de fraîcheur pour un ensemble d'agents, à un seuil donné.

    ``judged`` distingue « le journal a assez d'historique pour se prononcer »
    de « rien à signaler » : quand il vaut ``False`` (journal absent, vide, ou
    plus jeune que ``threshold_days``), :attr:`stale_entries` est toujours
    vide par construction — l'absence de données ne doit jamais se lire comme
    une absence d'usage.
    """

    threshold_days: int
    judged: bool
    journal_span_days: int | None
    entries: tuple[AgentFreshness, ...]

    @property
    def stale_entries(self) -> tuple[AgentFreshness, ...]:
        return tuple(e for e in self.entries if e.stale)


def _parse_iso(value: str) -> datetime | None:
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(value)
    return None


def compute_agent_freshness(
    agent_names: Iterable[str],
    dispatch_counts: Mapping[str, Mapping[str, Any]],
    *,
    threshold_days: int,
    oldest_started_at: str | None,
    now: datetime | None = None,
) -> FreshnessReport:
    """Croiser la liste des agents connus avec leurs derniers choix journalisés.

    Règle de fraîcheur de l'issue #396 : un agent livré ou en override qui
    n'apparaît dans aucun ``agent.dispatch`` depuis *threshold_days* est
    signalé — jamais retiré, jamais en échec (voir les appelants CLI/cockpit,
    ce module ne fait que calculer le verdict).

    L'absence de données ne vaut jamais absence d'usage : si le journal (tout
    tag confondu, via *oldest_started_at*) couvre moins de *threshold_days*
    jours, aucun agent n'est jugé — :attr:`FreshnessReport.judged` est
    ``False`` et ``stale`` reste ``False`` pour chaque entrée, quel que soit
    son ``last_seen``. Un projet dont le journal commence hier n'a aucun
    agent périmé, il n'a simplement pas encore eu l'occasion de les voir.

    Un agent est périmé quand sa dernière date de choix remonte à
    *threshold_days* jours ou plus (borne incluse, comme « depuis N jours »),
    ou qu'il n'a jamais été choisi.
    """
    now = now or datetime.now(tz=UTC)
    journal_span_days: int | None = None
    if oldest_started_at:
        oldest = _parse_iso(oldest_started_at)
        if oldest is not None:
            journal_span_days = (now - oldest).days

    judged = journal_span_days is not None and journal_span_days >= threshold_days

    entries: list[AgentFreshness] = []
    for name in sorted(set(agent_names)):
        stats = dispatch_counts.get(name)
        last_seen = str(stats["last_seen"]) if stats and stats.get("last_seen") else None
        days_since: int | None = None
        if last_seen:
            seen_at = _parse_iso(last_seen)
            if seen_at is not None:
                days_since = (now - seen_at).days
        stale = judged and (last_seen is None or (days_since is not None and days_since >= threshold_days))
        entries.append(AgentFreshness(name=name, last_seen=last_seen, days_since=days_since, stale=stale))

    return FreshnessReport(
        threshold_days=threshold_days,
        judged=judged,
        journal_span_days=journal_span_days,
        entries=tuple(entries),
    )


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _ns(iso: str) -> int:
    """Convert ISO timestamp to nanoseconds since epoch (OTel convention)."""
    try:
        dt = datetime.fromisoformat(iso)
        return int(dt.timestamp() * 1_000_000_000)
    except (ValueError, OSError):
        return 0


class TraceLedger:
    """Persist and query run traces.

    Usage::

        ledger = TraceLedger(Path("_grimoire-runtime-output/traces"))
        trace = ledger.record(
            run_id="RUN-abc123",
            workflow_instance_id="WFI-...",
            mission_id="MIS-...",
            task_id="GAO-...",
            recipe_id="recipe.pack.convert",
            outcome=TraceOutcome.SUCCESS,
            started_at=...,
        )
        ledger.export_otel_jsonl(Path("traces-otel.jsonl"))
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._traces_path = root / "traces.jsonl"

    # ── Private helpers ────────────────────────────────────────────────────

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self._traces_path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _load_all(self) -> list[TraceRecord]:
        records: list[TraceRecord] = []
        if not self._traces_path.exists():
            return records
        for line in self._traces_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError, KeyError):
                records.append(TraceRecord.from_dict(json.loads(line)))
        return records

    def _next_id(self, run_id: str) -> str:
        existing = [t for t in self._load_all() if t.run_id == run_id]
        return f"TRC-{run_id}-{len(existing) + 1:03d}"

    # ── Public write API ───────────────────────────────────────────────────

    def record(
        self,
        *,
        run_id: str,
        workflow_instance_id: str,
        mission_id: str,
        task_id: str,
        recipe_id: str,
        outcome: TraceOutcome,
        started_at: str,
        completed_at: str = "",
        agent_id: str = "",
        host_id: str = "",
        model: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        policy_verdicts: list[dict[str, Any]] | None = None,
        evidence_refs: list[str] | None = None,
        error_count: int = 0,
        retry_count: int = 0,
        quality_score: float = 0.0,
        latency_ms: float = 0.0,
        token_usage: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        trace_id: str | None = None,
    ) -> TraceRecord:
        trace = TraceRecord(
            id=trace_id or self._next_id(run_id),
            run_id=run_id,
            workflow_instance_id=workflow_instance_id,
            mission_id=mission_id,
            task_id=task_id,
            recipe_id=recipe_id,
            outcome=outcome,
            started_at=started_at,
            completed_at=completed_at or _now_iso(),
            agent_id=agent_id,
            host_id=host_id,
            model=model,
            tool_calls=tuple(ToolCallTrace.from_dict(tc) for tc in (tool_calls or [])),
            policy_verdicts=tuple(PolicyVerdictRef.from_dict(pv) for pv in (policy_verdicts or [])),
            evidence_refs=tuple(evidence_refs or []),
            error_count=error_count,
            retry_count=retry_count,
            quality_score=quality_score,
            latency_ms=latency_ms,
            token_usage=TokenUsage.from_dict(token_usage or {}),
            tags=tuple(tags or []),
        )
        self._append(trace.to_dict())
        return trace

    # ── Queries ────────────────────────────────────────────────────────────

    def get_trace(self, trace_id: str) -> TraceRecord | None:
        for t in self._load_all():
            if t.id == trace_id:
                return t
        return None

    def list_traces(
        self,
        *,
        mission_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        outcome: TraceOutcome | None = None,
    ) -> list[TraceRecord]:
        traces = self._load_all()
        if mission_id:
            traces = [t for t in traces if t.mission_id == mission_id]
        if task_id:
            traces = [t for t in traces if t.task_id == task_id]
        if run_id:
            traces = [t for t in traces if t.run_id == run_id]
        if outcome:
            traces = [t for t in traces if t.outcome == outcome]
        return traces

    def agent_dispatch_counts(self) -> dict[str, dict[str, Any]]:
        """Compter les choix d'agent journalisés par ``hosts.decisions._record_agent_dispatch``.

        Filtre sur le tag ``agent.dispatch`` — le seul type d'écriture de ce
        journal qui répond à « quel agent a été choisi », distinct des gates
        de tâche et des appels modèle qui vivent dans le même fichier.
        Retourne, par ``agent_id`` : le nombre d'occurrences et l'horodatage
        (``started_at``) de la plus récente — exactement ce que l'issue #365
        pose comme critère d'arrêt (« combien de fois, et quand pour la
        dernière fois »).
        """
        counts: dict[str, dict[str, Any]] = {}
        for trace in self._load_all():
            if AGENT_DISPATCH_TAG not in trace.tags or not trace.agent_id:
                continue
            entry = counts.setdefault(trace.agent_id, {"count": 0, "last_seen": ""})
            entry["count"] += 1
            if trace.started_at > entry["last_seen"]:
                entry["last_seen"] = trace.started_at
        return counts

    def agent_miss_counts(self) -> dict[str, dict[str, Any]]:
        """Compter les non-choix journalisés par ``hosts.decisions.record_agent_miss``.

        Filtre sur le tag ``agent.miss`` et agrège par spécialité cherchée
        (encodée en tag ``specialty:<nom>``) — la clé de lecture que l'issue
        #389 pose comme critère d'arrêt (« quelle spécialité a manqué et
        combien de fois »). Une entrée sans spécialité nommable est comptée
        sous ``UNNAMED_SPECIALTY`` plutôt qu'ignorée : le non-choix reste un
        signal même quand le concierge n'a pas su le nommer. Retourne, par
        spécialité : le nombre d'occurrences, l'horodatage de la plus récente,
        et — depuis l'issue #395 — la catégorie et l'agent de repli les plus
        récents (``category``/``fallback_agent``, chaîne vide si absents du
        dernier enregistrement) : le déclencheur de propositions d'artefact
        en a besoin pour rédiger un ``use_when``/``dont_use_when`` et choisir
        entre skill et agent sans jamais relire les traces lui-même. Ces deux
        champs s'ajoutent à la forme qu'``agent_dispatch_counts`` partage
        déjà — ils ne la remplacent pas.
        """
        counts: dict[str, dict[str, Any]] = {}
        for trace in self._load_all():
            if AGENT_MISS_TAG not in trace.tags:
                continue
            specialty = _UNNAMED_SPECIALTY
            category = ""
            for tag in trace.tags:
                if tag.startswith("specialty:"):
                    specialty = tag.removeprefix("specialty:") or _UNNAMED_SPECIALTY
                elif tag.startswith("category:"):
                    category = tag.removeprefix("category:")
            entry = counts.setdefault(
                specialty, {"count": 0, "last_seen": "", "category": "", "fallback_agent": ""}
            )
            entry["count"] += 1
            if trace.started_at > entry["last_seen"]:
                entry["category"] = category
                entry["fallback_agent"] = trace.agent_id or ""
                entry["last_seen"] = trace.started_at
        return counts

    def oldest_started_at(self) -> str | None:
        """Horodatage du plus ancien enregistrement du journal, tous tags confondus.

        Mesure jusqu'où remonte l'historique disponible — ce dont
        :func:`compute_agent_freshness` a besoin pour distinguer « aucun
        agent choisi depuis N jours » d'« aucune donnée sur N jours »
        (issue #396). ``None`` quand le journal est vide ou absent, jamais
        une erreur.
        """
        starts = [t.started_at for t in self._load_all() if t.started_at]
        return min(starts) if starts else None

    def agent_freshness_report(
        self,
        agent_names: Iterable[str],
        *,
        threshold_days: int,
        now: datetime | None = None,
    ) -> FreshnessReport:
        """Commodité : assemble :func:`compute_agent_freshness` depuis ce journal.

        Lit ``agent_dispatch_counts()`` et ``oldest_started_at()`` sur *self*
        plutôt que de les faire recalculer par chaque appelant (doctor,
        cockpit, ``registry dispatches``) — les trois lisent le même journal
        pour la même question.
        """
        return compute_agent_freshness(
            agent_names,
            self.agent_dispatch_counts(),
            threshold_days=threshold_days,
            oldest_started_at=self.oldest_started_at(),
            now=now,
        )

    def policy_block_rate(self, mission_id: str | None = None) -> float:
        """Fraction of tool calls that were blocked."""
        traces = self.list_traces(mission_id=mission_id)
        total = sum(len(t.tool_calls) for t in traces)
        if total == 0:
            return 0.0
        blocked = sum(1 for t in traces for tc in t.tool_calls if tc.verdict == "block")
        return blocked / total

    # ── OTel GenAI JSONL export ────────────────────────────────────────────

    def export_otel_jsonl(self, dest: Path, *, mission_id: str | None = None) -> int:
        """Export traces as OTel GenAI JSONL spans. Returns span count written.

        Format follows OpenTelemetry GenAI semantic conventions
        (``OTEL_EXPORT_SCHEMA_VERSION``, see ``otel_conventions``). One
        ``invoke_agent`` parent span per trace, plus one ``execute_tool``
        child span per recorded tool call — so the count returned is spans,
        not traces, whenever a trace carries tool calls.
        No opentelemetry-sdk dependency required — pure JSONL output.
        """
        traces = self.list_traces(mission_id=mission_id)
        lines: list[str] = []
        for trace in traces:
            for span in self._to_otel_spans(trace):
                lines.append(json.dumps(span, ensure_ascii=False))
        dest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines)

    # ── Langfuse JSONL export ──────────────────────────────────────────────

    def export_langfuse(self, dest: Path, *, mission_id: str | None = None) -> int:
        """Export traces as Langfuse trace objects (JSONL). Returns count written.

        Format follows Langfuse /api/public/traces REST contract.
        No langfuse SDK required — pure JSONL output for batch import.
        """
        traces = self.list_traces(mission_id=mission_id)
        lines: list[str] = []
        for trace in traces:
            obj = self._to_langfuse_trace(trace)
            lines.append(json.dumps(obj, ensure_ascii=False))
        dest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines)

    def _to_langfuse_trace(self, trace: TraceRecord) -> dict[str, Any]:
        tags = list(trace.tags)
        if trace.outcome.value not in tags:
            tags.append(trace.outcome.value)
        return {
            "id": trace.id,
            "name": f"grimoire.{trace.recipe_id}",
            "timestamp": trace.started_at,
            "input": {
                "run_id": trace.run_id,
                "mission_id": trace.mission_id,
                "task_id": trace.task_id,
                "recipe_id": trace.recipe_id,
                "workflow_instance_id": trace.workflow_instance_id,
            },
            "output": {
                "outcome": trace.outcome.value,
                "quality_score": trace.quality_score,
                "error_count": trace.error_count,
            },
            "metadata": {
                "agent_id": trace.agent_id,
                "host_id": trace.host_id,
                "model": trace.model,
                "latency_ms": trace.latency_ms,
                "retry_count": trace.retry_count,
                "policy_blocks": sum(1 for tc in trace.tool_calls if tc.verdict == "block"),
                "prompt_tokens": trace.token_usage.prompt_tokens,
                "completion_tokens": trace.token_usage.completion_tokens,
            },
            "tags": tags,
            "userId": trace.agent_id or None,
            "sessionId": trace.run_id,
            "endTime": trace.completed_at or None,
        }

    def _to_otel_spans(self, trace: TraceRecord) -> list[dict[str, Any]]:
        """Project one trace into an ``invoke_agent`` parent span plus one
        ``execute_tool`` child span per recorded tool call.

        Follows OTel GenAI conventions: ``gen_ai.provider.name`` (not the
        deprecated ``gen_ai.system``), ``gen_ai.operation.name``,
        ``gen_ai.conversation.id`` from ``mission_id`` (falling back to
        ``run_id``), ``gen_ai.agent.name`` from ``agent_id``. Each tool-call
        child carries real start/end timestamps derived from the call's own
        ``timestamp`` when the caller recorded one, or from the parent
        trace's ``started_at`` otherwise — never from a bug that hands the
        nanosecond converter a Python type name instead of an ISO string
        (the previous behaviour, which made every timestamp zero).
        """
        trace_id_hex = uuid.uuid5(uuid.NAMESPACE_URL, trace.run_id).hex
        span_id_hex = uuid.uuid5(uuid.NAMESPACE_URL, trace.id).hex[:16]
        status_code = _OTEL_STATUS_OK if trace.outcome == TraceOutcome.SUCCESS else _OTEL_STATUS_ERROR
        conversation_id = trace.mission_id or trace.run_id

        resource = {
            "attributes": {
                "service.name": "grimoire-kit",
                "grimoire.host_id": trace.host_id,
                "grimoire.agent_id": trace.agent_id,
                "grimoire.schema_version": OTEL_EXPORT_SCHEMA_VERSION,
            }
        }

        attrs: dict[str, Any] = {
            "grimoire.run_id": trace.run_id,
            "grimoire.mission_id": trace.mission_id,
            "grimoire.task_id": trace.task_id,
            "grimoire.recipe_id": trace.recipe_id,
            "grimoire.workflow_instance_id": trace.workflow_instance_id,
            "grimoire.outcome": trace.outcome.value,
            ATTR_PROVIDER_NAME: PROVIDER_NAME,
            ATTR_OPERATION_NAME: OP_INVOKE_AGENT,
            ATTR_CONVERSATION_ID: conversation_id,
            ATTR_REQUEST_MODEL: trace.model,
            ATTR_INPUT_TOKENS: trace.token_usage.prompt_tokens,
            ATTR_OUTPUT_TOKENS: trace.token_usage.completion_tokens,
            "grimoire.error_count": trace.error_count,
            "grimoire.retry_count": trace.retry_count,
            "grimoire.quality_score": trace.quality_score,
            "grimoire.policy_blocks": sum(1 for tc in trace.tool_calls if tc.verdict == "block"),
        }
        if trace.agent_id:
            attrs[ATTR_AGENT_NAME] = trace.agent_id

        parent_name = agent_span_name(trace.agent_id) if trace.agent_id else model_span_name(OP_INVOKE_AGENT, trace.recipe_id)

        spans: list[dict[str, Any]] = [
            {
                "traceId": trace_id_hex,
                "spanId": span_id_hex,
                "name": parent_name,
                "kind": _OTEL_SPAN_KIND_INTERNAL,
                "startTimeUnixNano": _ns(trace.started_at),
                "endTimeUnixNano": _ns(trace.completed_at),
                "status": {"code": status_code},
                "attributes": attrs,
                "resource": resource,
            }
        ]

        for index, tc in enumerate(trace.tool_calls):
            start_ns = _ns(tc.timestamp or trace.started_at)
            end_ns = start_ns + int(tc.latency_ms * 1_000_000) if start_ns else 0
            child_attrs: dict[str, Any] = {
                ATTR_PROVIDER_NAME: PROVIDER_NAME,
                ATTR_OPERATION_NAME: OP_EXECUTE_TOOL,
                ATTR_TOOL_NAME: tc.tool,
                "grimoire.verdict": tc.verdict,
                "grimoire.policy_verdict_id": tc.policy_verdict_id,
            }
            if tc.call_id:
                child_attrs[ATTR_TOOL_CALL_ID] = tc.call_id
            spans.append(
                {
                    "traceId": trace_id_hex,
                    "spanId": uuid.uuid5(uuid.NAMESPACE_URL, f"{trace.id}:tool:{index}").hex[:16],
                    "parentSpanId": span_id_hex,
                    "name": tool_span_name(tc.tool),
                    "kind": _OTEL_SPAN_KIND_INTERNAL,
                    "startTimeUnixNano": start_ns,
                    "endTimeUnixNano": end_ns,
                    "status": {"code": _OTEL_STATUS_ERROR if tc.verdict in ("block", "deny") else _OTEL_STATUS_OK},
                    "attributes": child_attrs,
                    "resource": resource,
                }
            )

        return spans
