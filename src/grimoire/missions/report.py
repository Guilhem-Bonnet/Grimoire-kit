"""Rapport de mission : coût, acceptance, palier, preuves et rejeu, chaque ligne avec sa source (W4-04, OBS-6).

Les faits d'une mission vivent dans trois journaux qui ne se lisaient pas ensemble :
le Mission Ledger (tentatives de la cascade, ``task.dispatched``), le TraceLedger
(résumé de cascade ``dispatch.outcome``, usage de tokens replié du journal de
session, W4-01) et l'EvidenceService (packs et verdicts). Ce module les assemble en
un dict déterministe, rendu en JSON ou en Markdown, en **lecture seule** : aucun
dossier n'est créé, un journal absent est une source absente et le rapport le dit.

Règles :

* **Jamais de chiffre inventé.** Un coût inconnu vaut ``None`` / ``unknown``. Un
  agrégat partiel est une borne basse (``lower_bound``). Sans aucun coût connu, le
  rapport n'a aucun total numérique et écrit « coût inconnu ».
* **Deux vues d'une même dépense ne s'additionnent pas.** Le journal de session
  (``transcript``) et les tentatives de dispatch (``dispatch``) décrivent les mêmes
  appels vus de deux côtés : une tâche retient le journal de session s'il existe,
  sinon les tentatives, et les deux vues restent affichées côte à côte. Une tâche
  sans aucune donnée compte comme inconnue dans le total de la mission (borne basse).
* **Le lien tentative -> trace est exact ou absent.** Les événements ``task.dispatched``
  ne portent pas l'identifiant de la trace de cascade. Une cascade (suite de tentatives
  qui commence à ``attempt == 1``) est rattachée à l'unique trace ``dispatch.outcome``
  de la même tâche écrite après sa dernière tentative et avant la première de la
  cascade suivante ; zéro ou plusieurs candidates : ``trace_id`` est ``None`` et la
  ligne le dit (``trace_link``).
* **Déterministe.** Aucune date de génération, aucun identifiant tiré au hasard,
  tris explicites : deux exécutions sur les mêmes journaux donnent les mêmes octets.

Limite : le rapport lit ce que les journaux ont gardé. Un événement écrit avant un
champ (``acceptance_status``, W4-04 #428) est rendu ``unrecorded``, jamais complété.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.costs import Cost, format_usd
from grimoire.missions.ledger import MissionLedger
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger
from grimoire.traces.transcript_usage import task_usage

if TYPE_CHECKING:
    from grimoire.missions.schemas import LedgerEvent, MissionTask
    from grimoire.traces.schemas import TraceRecord

__all__ = [
    "DEFAULT_EVIDENCE_RELPATH",
    "DEFAULT_LEDGER_RELPATH",
    "REPORT_SCHEMA",
    "build_mission_report",
    "render_json",
    "render_markdown",
]

REPORT_SCHEMA = "grimoire.mission_report.v1"
DEFAULT_LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
DEFAULT_EVIDENCE_RELPATH = Path("_grimoire-runtime-output/evidence")

#: Verdicts d'une tentative dont l'appel au fournisseur a échoué : le check n'a pas tourné.
_CALL_FAILED = frozenset({"rate_limit", "quota", "overloaded", "timeout", "error"})
_OUTCOME_BY_VERDICT = {"green": "passed", "red": "failed", "unrunnable": "unrunnable"}


def _cost_dict(cost: Cost) -> dict[str, Any]:
    return {"usd": cost.usd, "status": cost.status, "unpriced_calls": cost.unpriced_calls}


def _instant(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _tag(tags: tuple[str, ...], prefix: str) -> str | None:
    for tag in tags:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return None


def _attempt_cost(payload: dict[str, Any]) -> Cost:
    """Le coût d'une tentative : inconnu quand le fournisseur ne l'a pas rendu, jamais ``0.0``."""
    raw = payload.get("cost_usd")
    return Cost.unpriced() if isinstance(raw, bool) or not isinstance(raw, int | float) else Cost.exact(raw)


def _acceptance(payload: dict[str, Any]) -> dict[str, Any]:
    verdict = str(payload.get("verdict", ""))
    status = "unrecorded" if "acceptance_status" not in payload else payload["acceptance_status"] or "not_run"
    outcome = "not_run" if verdict in _CALL_FAILED else _OUTCOME_BY_VERDICT.get(verdict, "unknown")
    checks = [c for c in payload.get("checks") or [] if isinstance(c, dict)]
    return {
        "status": status,
        "outcome": outcome,
        "checks_total": len(checks),
        "checks_passed": sum(1 for c in checks if c.get("ok") is True),
    }


def _cascades(events: list[LedgerEvent]) -> list[list[LedgerEvent]]:
    """Découpe les ``task.dispatched`` d'une tâche en cascades : ``attempt == 1`` ouvre la suivante."""
    groups: list[list[LedgerEvent]] = []
    for event in events:
        if event.event_type != "task.dispatched":
            continue
        if not groups or event.payload.get("attempt") == 1:
            groups.append([])
        groups[-1].append(event)
    return groups


def _link_traces(cascades: list[list[LedgerEvent]], outcomes: list[TraceRecord]) -> list[TraceRecord | None]:
    """Une trace ``dispatch.outcome`` par cascade, ou ``None`` : écrite après sa dernière tentative, avant la suivante."""
    stamped = sorted(((_instant(t.started_at), t) for t in outcomes), key=lambda pair: (pair[1].started_at, pair[1].id))
    linked: list[TraceRecord | None] = []
    for index, group in enumerate(cascades):
        last = _instant(group[-1].created_at)
        following = _instant(cascades[index + 1][0].created_at) if index + 1 < len(cascades) else None
        if last is None or (index + 1 < len(cascades) and following is None):
            linked.append(None)
            continue
        candidates = [
            trace
            for moment, trace in stamped
            if moment is not None and moment >= last and (following is None or moment < following)
        ]
        linked.append(candidates[0] if len(candidates) == 1 else None)
    return linked


def _attempt_rows(task_id: str, groups: list[list[LedgerEvent]], links: list[TraceRecord | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, (group, trace) in enumerate(zip(groups, links, strict=True), start=1):
        for event in group:
            payload = event.payload
            rows.append(
                {
                    "task_id": task_id,
                    "cascade": number,
                    "attempt": payload.get("attempt"),
                    "at": event.created_at,
                    "tier": payload.get("tier"),
                    "start_tier": payload.get("start_tier"),
                    "start_tier_reason": payload.get("start_tier_reason"),
                    "provider": payload.get("provider"),
                    "model": payload.get("model"),
                    "verdict": payload.get("verdict"),
                    "cost": _cost_dict(_attempt_cost(payload)),
                    "acceptance": _acceptance(payload),
                    "trace_id": trace.id if trace is not None else None,
                    "trace_link": "matched" if trace is not None else "none",
                    "source": f"ledger:events.jsonl#{event.id}",
                }
            )
    return rows


def _cascade_rows(groups: list[list[LedgerEvent]], links: list[TraceRecord | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, (group, trace) in enumerate(zip(groups, links, strict=True), start=1):
        rows.append(
            {
                "cascade": number,
                "attempts": len(group),
                "resolved": any(e.payload.get("verdict") == "green" for e in group),
                "replay_key": _tag(trace.tags, "replay:") if trace is not None else None,
                "trace_id": trace.id if trace is not None else None,
                "source": f"traces:traces.jsonl#{trace.id}" if trace is not None else f"ledger:events.jsonl#{group[0].id}",
            }
        )
    return rows


def _evidence(task: MissionTask, service: Any | None) -> dict[str, Any]:
    packs: list[dict[str, Any]] = []
    verdicts: list[dict[str, Any]] = []
    if service is not None:
        for pack in sorted(service.list_packs(task.id), key=lambda p: (p.created_at, p.id)):
            missing = pack.coverage.acceptance_missing if pack.coverage is not None else ()
            packs.append(
                {
                    "id": pack.id,
                    "profile": pack.profile.value,
                    "items": len(pack.items),
                    "acceptance_missing": list(missing),
                    "created_at": pack.created_at,
                    "source": f"evidence:packs.jsonl#{pack.id}",
                }
            )
        for verdict in sorted(service.list_verdicts(task.id), key=lambda v: (v.created_at, v.id)):
            verdicts.append(
                {
                    "id": verdict.id,
                    "verdict": verdict.verdict.value,
                    "pack_id": verdict.evidence_pack_id,
                    "failed_checks": [c.id for c in verdict.checks if c.result.value != "passed"],
                    "created_at": verdict.created_at,
                    "source": f"evidence:verdicts.jsonl#{verdict.id}",
                }
            )
    return {"expected": list(task.expected_evidence), "packs": packs, "verdicts": verdicts}


def _task_cost(usage: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    transcript = Cost.from_parts(usage["cost_usd"], usage["unpriced_calls"]) if usage["calls"] else None
    dispatch = Cost.total(Cost.coerce(a["cost"]["usd"]) for a in attempts) if attempts else None
    chosen = transcript if transcript is not None else dispatch
    source = "transcript" if transcript is not None else ("dispatch" if dispatch is not None else "none")
    body = _cost_dict(chosen) if chosen is not None else {"usd": None, "status": "unknown", "unpriced_calls": 0}
    return {
        "source": source,
        **body,
        "views": {
            "transcript": _cost_dict(transcript) if transcript is not None else None,
            "dispatch": _cost_dict(dispatch) if dispatch is not None else None,
        },
    }


def _mission_cost(task_costs: list[dict[str, Any]]) -> dict[str, Any]:
    without = sum(1 for c in task_costs if c["source"] == "none")
    chosen = [Cost.from_parts(c["usd"], c["unpriced_calls"]) for c in task_costs if c["source"] != "none"]
    total = Cost.total([*chosen, *([Cost.unpriced(without)] if without else [])])
    if not task_costs:
        return {"usd": None, "status": "unknown", "unpriced_calls": 0, "tasks_without_data": 0}
    body = _cost_dict(total)
    body["unpriced_calls"] = sum(c["unpriced_calls"] for c in task_costs)
    return {**body, "tasks_without_data": without}


def _replay(cascades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, list[bool]] = defaultdict(list)
    for cascade in cascades:
        if cascade["replay_key"] is not None:
            by_key[cascade["replay_key"]].append(bool(cascade["resolved"]))
    return [
        {"key": key, "runs": len(results), "resolved": sum(results), "all_resolved": all(results)}
        for key, results in sorted(by_key.items())
    ]


def _relative(path: Path) -> str:
    return path.as_posix()


def build_mission_report(
    project_root: Path,
    mission_id: str,
    *,
    ledger_root: Path = DEFAULT_LEDGER_RELPATH,
    traces_root: Path | None = None,
    evidence_root: Path = DEFAULT_EVIDENCE_RELPATH,
) -> dict[str, Any] | None:
    """Assemble le rapport de *mission_id* ; ``None`` si le ledger ne connaît pas cette mission.

    Lecture seule : un journal absent n'est jamais créé, il apparaît ``None`` sous ``sources``.
    """
    from grimoire.core.standard_generation import TRACES_DIR

    root = project_root.resolve()
    traces_rel = traces_root if traces_root is not None else TRACES_DIR
    ledger_path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    traces_path = traces_rel if traces_rel.is_absolute() else root / traces_rel
    evidence_path = evidence_root if evidence_root.is_absolute() else root / evidence_root

    if not (ledger_path / "events.jsonl").is_file():
        return None
    ledger = MissionLedger(ledger_path)
    mission = ledger.get_mission(mission_id)
    if mission is None:
        return None

    have_traces = (traces_path / "traces.jsonl").is_file()
    have_evidence = evidence_path.is_dir()
    all_traces = TraceLedger(traces_path).list_traces() if have_traces else []
    traces_by_task: dict[str, list[TraceRecord]] = defaultdict(list)
    for trace in all_traces:
        traces_by_task[trace.task_id].append(trace)
    evidence_service: Any | None = None
    if have_evidence:
        from grimoire.evidence import EvidenceService

        evidence_service = EvidenceService(evidence_path)

    tasks_out: list[dict[str, Any]] = []
    all_attempts = 0
    all_cascades: list[dict[str, Any]] = []
    for task in sorted(ledger.list_tasks(mission_id), key=lambda t: t.id):
        task_traces = traces_by_task.get(task.id, [])
        outcomes = [t for t in task_traces if DISPATCH_OUTCOME_TAG in t.tags]
        groups = _cascades(ledger.list_events(task.id))
        links = _link_traces(groups, outcomes)
        attempts = _attempt_rows(task.id, groups, links)
        cascades = _cascade_rows(groups, links)
        usage = task_usage(task_traces)
        all_attempts += len(attempts)
        all_cascades.extend(cascades)
        tasks_out.append(
            {
                "id": task.id,
                "title": task.title,
                "status": task.status_label,
                "type": task.type.value,
                "source": f"ledger:events.jsonl#{task.id}",
                "cost": _task_cost(usage, attempts),
                "usage": usage,
                "attempts": attempts,
                "cascades": cascades,
                "evidence": _evidence(task, evidence_service),
            }
        )

    return {
        "schema": REPORT_SCHEMA,
        "mission": {
            "id": mission.id,
            "title": mission.title,
            "status": mission.status.value,
            "origin": mission.origin,
            "risk_profile": mission.risk_profile.value,
            "source": f"ledger:events.jsonl#{mission.id}",
        },
        "sources": {
            "ledger": _relative(ledger_root),
            "traces": _relative(traces_rel) if have_traces else None,
            "evidence": _relative(evidence_root) if have_evidence else None,
        },
        "cost": _mission_cost([t["cost"] for t in tasks_out]),
        "summary": {
            "tasks": len(tasks_out),
            "attempts": all_attempts,
            "cascades": len(all_cascades),
            "cascades_resolved": sum(1 for c in all_cascades if c["resolved"]),
            "cascades_without_trace": sum(1 for c in all_cascades if c["trace_id"] is None),
        },
        "replay": _replay(all_cascades),
        "tasks": tasks_out,
    }


def _clean(text: str) -> str:
    """Un texte encodable en UTF-8 strict (un substitut isolé d'un journal devient ``?``)."""
    return text.encode("utf-8", "replace").decode("utf-8")


def render_json(report: dict[str, Any]) -> str:
    return _clean(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)) + "\n"


def _cell(value: Any) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _money(block: dict[str, Any]) -> str:
    if block["status"] == "unknown":
        return "coût inconnu"
    return format_usd(block["usd"], block["status"], block["unpriced_calls"])


def render_markdown(report: dict[str, Any]) -> str:
    mission = report["mission"]
    cost = report["cost"]
    summary = report["summary"]
    lines = [
        f"# Rapport de mission {mission['id']} : {_cell(mission['title'])}",
        "",
        f"- État : {mission['status']} (origine {_cell(mission['origin'])}, risque {mission['risk_profile']})",
        f"- Sources : ledger `{report['sources']['ledger']}`, "
        f"traces {_source_label(report['sources']['traces'])}, preuves {_source_label(report['sources']['evidence'])}",
        f"- Tâches : {summary['tasks']}, tentatives : {summary['attempts']}, cascades : {summary['cascades']} "
        f"({summary['cascades_resolved']} résolue(s), {summary['cascades_without_trace']} sans trace)",
        "",
        "## Coût",
        "",
        f"- Mission : {_money(cost)}"
        + (f" ({cost['tasks_without_data']} tâche(s) sans donnée de coût)" if cost["tasks_without_data"] else ""),
        "- Une tâche retient le journal de session s'il existe, sinon les tentatives de dispatch : "
        "ces deux vues décrivent les mêmes appels et ne s'additionnent pas.",
    ]
    if report["replay"]:
        lines += ["", "## Rejeu", "", "| Clé | Exécutions | Résolues | Toutes résolues |", "| --- | --- | --- | --- |"]
        lines += [
            f"| {_cell(r['key'])} | {r['runs']} | {r['resolved']} | {'oui' if r['all_resolved'] else 'non'} |"
            for r in report["replay"]
        ]
    for task in report["tasks"]:
        lines += _task_lines(task)
    return _clean("\n".join(lines)) + "\n"


def _source_label(value: str | None) -> str:
    return f"`{value}`" if value else "absentes"


def _task_lines(task: dict[str, Any]) -> list[str]:
    cost = task["cost"]
    usage = task["usage"]
    lines = [
        "",
        f"## {task['id']} : {_cell(task['title'])}",
        "",
        f"- État : {task['status']} (type {task['type']}) - source {task['source']}",
        f"- Coût : {_money(cost)} (vue retenue : {cost['source']})",
    ]
    for name in ("transcript", "dispatch"):
        view = cost["views"][name]
        if view is not None:
            lines.append(f"  - vue {name} : {_money(view)}")
    if usage["calls"]:
        lines.append(
            f"- Usage : {usage['calls']} appel(s) de journal, {usage['input_tokens']} in / {usage['output_tokens']} out "
            f"(source {usage['usage_source']})"
        )
    if usage["discrepancy"] is not None:
        lines.append(f"- Écart journal / coût rapporté : {usage['discrepancy']['relative_gap']:.0%}")
    if task["attempts"]:
        lines += [
            "",
            "| Cascade | Essai | Palier | Fournisseur / modèle | Verdict | Acceptance | Coût | Trace | Source |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for a in task["attempts"]:
            acceptance = a["acceptance"]
            lines.append(
                f"| {a['cascade']} | {_cell(a['attempt'])} | {_cell(a['tier'])} | {_cell(a['provider'])} / {_cell(a['model'])} "
                f"| {_cell(a['verdict'])} | {acceptance['status']} : {acceptance['outcome']} "
                f"| {_money(a['cost'])} | {_cell(a['trace_id'])} | {a['source']} |"
            )
        lines.append("")
    else:
        lines.append("- Aucune tentative de dispatch dans le ledger.")
    evidence = task["evidence"]
    if evidence["packs"] or evidence["verdicts"]:
        lines += ["", "Preuves :"]
        lines += [
            f"- pack {p['id']} ({p['profile']}, {p['items']} élément(s)"
            + (f", critères non couverts : {_cell(', '.join(p['acceptance_missing']))}" if p["acceptance_missing"] else "")
            + f") - {p['source']}"
            for p in evidence["packs"]
        ]
        lines += [
            f"- verdict {v['id']} : {v['verdict']} sur {v['pack_id']}"
            + (f", contrôles en échec : {_cell(', '.join(v['failed_checks']))}" if v["failed_checks"] else "")
            + f" - {v['source']}"
            for v in evidence["verdicts"]
        ]
    else:
        expected = f" (attendues : {_cell(', '.join(evidence['expected']))})" if evidence["expected"] else ""
        lines.append(f"- Aucune preuve enregistrée{expected}.")
    return lines
