"""Projection helpers for syncing local Agent OS state into memory backends."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.codegraph.graph import CodeGraph
from grimoire.codegraph.schemas import CodeEdge, CodeNode, NodeKind
from grimoire.evidence.service import EvidenceService
from grimoire.missions.ledger import MissionLedger

if TYPE_CHECKING:
    from grimoire.memory.manager import MemoryManager
    from grimoire.memory.neo4j_graph import Neo4jMemoryGraph

__all__ = [
    "DEFAULT_CODE_VECTOR_GRANULARITY",
    "build_code_graph",
    "build_code_vector_entries",
    "build_docs_entries",
    "build_task_code_reference_projection",
    "build_task_vector_entries",
    "graph_projection_verify",
    "sync_code_graph_projection",
    "sync_code_vector_projection",
    "sync_docs_projection",
    "sync_task_memory_projection",
    "sync_task_vector_projection",
    "vector_projection_verify",
]

DEFAULT_CODE_VECTOR_GRANULARITY = ("file", "symbol", "method", "test", "contract")
_CODE_VECTOR_GRANULARITIES = frozenset(DEFAULT_CODE_VECTOR_GRANULARITY)


def _unique_code_edges(edges: Iterable[CodeEdge]) -> list[CodeEdge]:
    """Return relation-identity unique edges matching Neo4j MERGE semantics."""
    unique: dict[tuple[str, str, str], CodeEdge] = {}
    for edge in edges:
        unique.setdefault((edge.from_node, edge.to_node, edge.kind.value), edge)
    return list(unique.values())


def build_code_graph(
    project_root: Path,
    paths: Iterable[Path],
    *,
    exclude: set[str] | None = None,
) -> CodeGraph:
    """Build an in-memory code graph from project-relative files or directories."""
    graph = CodeGraph()
    root = project_root.resolve()
    for raw_path in paths:
        path = raw_path if raw_path.is_absolute() else root / raw_path
        if not path.exists():
            continue
        if path.is_file() and path.suffix == ".py":
            graph.index_file(path, root=str(root))
        elif path.is_dir():
            graph.index_directory(path, exclude=exclude, root=str(root))
    return graph


def sync_code_graph_projection(
    memory_graph: Neo4jMemoryGraph,
    *,
    project_root: Path,
    paths: Iterable[Path],
    exclude: set[str] | None = None,
) -> dict[str, int]:
    """Parse code and upsert its graph projection into Neo4j."""
    code_graph = build_code_graph(project_root, paths, exclude=exclude)
    nodes = list(code_graph.nodes)
    edges = _unique_code_edges(code_graph.edges)
    memory_graph.upsert_code_nodes(nodes)
    memory_graph.upsert_code_edges(edges)
    stats = code_graph.stats()
    return {
        "files": stats["files"],
        "code_nodes": len(nodes),
        "code_edges": len(edges),
        "test_nodes": stats["test_nodes"],
    }


def sync_task_memory_projection(
    memory_graph: Neo4jMemoryGraph,
    *,
    ledger: MissionLedger,
    evidence: EvidenceService | None = None,
    project_root: Path | None = None,
    code_paths: Iterable[Path] = (),
    code_exclude: set[str] | None = None,
) -> dict[str, int]:
    """Upsert mission, task, incident, event, evidence, and verdict projections."""
    missions = ledger.list_missions()
    tasks = ledger.list_tasks()
    events = ledger.list_events()
    incidents = ledger.list_incidents()
    packs = evidence.list_packs() if evidence is not None else []
    verdicts = evidence.list_verdicts() if evidence is not None else []

    for mission in missions:
        memory_graph.upsert_mission(mission)
    for task in tasks:
        memory_graph.upsert_task(task)
    for event in events:
        memory_graph.upsert_ledger_event(event)
    for incident in incidents:
        memory_graph.upsert_incident(incident)
    for pack in packs:
        memory_graph.upsert_evidence_pack(pack)
    for verdict in verdicts:
        memory_graph.upsert_verdict(verdict)

    task_code_links = 0
    evidence_code_links = 0
    if project_root is not None:
        refs = build_task_code_reference_projection(
            project_root,
            code_paths,
            ledger=ledger,
            evidence=evidence,
            code_exclude=code_exclude,
        )
        task_code_links = memory_graph.replace_task_code_references(
            [task.id for task in tasks],
            refs["tasks"],
        )
        evidence_code_links = memory_graph.replace_evidence_code_references(
            [pack.id for pack in packs],
            refs["evidence"],
        )

    return {
        "missions": len(missions),
        "tasks": len(tasks),
        "ledger_events": len(events),
        "incidents": len(incidents),
        "evidence_packs": len(packs),
        "verdicts": len(verdicts),
        "decisions": len(verdicts),
        "task_code_links": task_code_links,
        "evidence_code_links": evidence_code_links,
    }


def _content_hash(text: str) -> str:
    return "sha256-" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative_path(project_root: Path, file_path: str) -> str:
    path = Path(file_path)
    try:
        return str(path.relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


def _projection_entry(
    entry_id: str,
    text: str,
    *,
    tags: tuple[str, ...],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(metadata)
    payload["content_hash"] = _content_hash(text)
    payload.setdefault("source_kind", "projection")
    return {
        "id": entry_id,
        "text": text,
        "user_id": "system",
        "tags": list(tags),
        "metadata": payload,
    }


def _code_file_text(rel_path: str, nodes: list[CodeNode]) -> str:
    module = next((node for node in nodes if node.kind == NodeKind.MODULE), nodes[0])
    symbols = [node for node in nodes if node.kind != NodeKind.MODULE]
    public_symbols = [node for node in symbols if node.is_public]
    lines = [
        f"Code chunk: {rel_path}",
        f"Module: {module.module or module.name}",
        f"Test file: {'yes' if module.is_test else 'no'}",
        f"Public symbols: {len(public_symbols)}",
    ]
    if module.docstring:
        lines.extend(["Module docstring:", module.docstring])
    if symbols:
        lines.append("Symbols:")
        for symbol in sorted(symbols, key=lambda item: (item.line_start, item.name))[:80]:
            descriptor = f"- {symbol.kind.value} {symbol.name} lines {symbol.line_start}-{symbol.line_end}"
            if symbol.docstring:
                descriptor += f": {symbol.docstring}"
            lines.append(descriptor)
    return "\n".join(lines)


def _normalize_code_vector_granularity(granularity: Iterable[str] | None) -> tuple[str, ...]:
    requested = tuple(
        dict.fromkeys(str(item).strip().lower() for item in (granularity or DEFAULT_CODE_VECTOR_GRANULARITY) if str(item).strip())
    )
    values = requested or ("file",)
    invalid = sorted(value for value in values if value not in _CODE_VECTOR_GRANULARITIES)
    if invalid:
        allowed = ", ".join(sorted(_CODE_VECTOR_GRANULARITIES))
        raise ValueError(f"Unsupported code vector granularity: {', '.join(invalid)}. Allowed: {allowed}")
    return values


def _source_excerpt(project_root: Path, rel_path: str, node: CodeNode, *, max_lines: int = 80) -> str:
    try:
        lines = (project_root / rel_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    start = max(node.line_start, 1)
    end = min(max(node.line_end, start), len(lines))
    excerpt_end = min(end, start + max_lines - 1)
    excerpt = "\n".join(lines[start - 1:excerpt_end])
    if excerpt_end < end:
        excerpt += "\n# ... truncated ..."
    return excerpt


def _code_node_text(project_root: Path, rel_path: str, node: CodeNode, *, label: str) -> str:
    lines = [
        f"{label}: {node.name}",
        f"File: {rel_path}",
        f"Module: {node.module}",
        f"Kind: {node.kind.value}",
        f"Lines: {node.line_start}-{node.line_end}",
        f"Test: {'yes' if node.is_test else 'no'}",
        f"Public: {'yes' if node.is_public else 'no'}",
    ]
    if node.docstring:
        lines.extend(["Docstring:", node.docstring])
    excerpt = _source_excerpt(project_root, rel_path, node)
    if excerpt:
        lines.extend(["Source excerpt:", excerpt])
    return "\n".join(lines)


def _code_test_file_text(rel_path: str, module: CodeNode, nodes: list[CodeNode]) -> str:
    tests = [node for node in nodes if node.kind != NodeKind.MODULE and node.is_test]
    lines = [
        f"Test chunk: {rel_path}",
        f"Module: {module.module or module.name}",
        f"Test symbols: {len(tests)}",
    ]
    if module.docstring:
        lines.extend(["Module docstring:", module.docstring])
    for symbol in sorted(tests, key=lambda item: (item.line_start, item.name))[:120]:
        descriptor = f"- {symbol.kind.value} {symbol.name} lines {symbol.line_start}-{symbol.line_end}"
        if symbol.docstring:
            descriptor += f": {symbol.docstring}"
        lines.append(descriptor)
    return "\n".join(lines)


def _code_projection_metadata(
    *,
    memory_type: str,
    projection_kind: str,
    entry_id: str,
    node: CodeNode,
    rel_path: str,
) -> dict[str, Any]:
    return {
        "memory_type": memory_type,
        "projection_group": "code",
        "projection_kind": projection_kind,
        "projection_source_id": node.id,
        "code_node_id": node.id,
        "file_path": rel_path,
        "module": node.module,
        "symbol": node.name,
        "symbol_kind": node.kind.value,
        "line_start": node.line_start,
        "line_end": node.line_end,
        "is_test": node.is_test,
        "is_public": node.is_public,
        "entry_id": entry_id,
    }


def _code_entry_counts(entries: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(entry["metadata"].get("projection_kind", "")) for entry in entries)
    return {
        "code_files": counts.get("code_chunk", 0),
        "code_symbols": counts.get("code_symbol", 0),
        "code_methods": counts.get("code_method", 0),
        "code_tests": counts.get("code_test", 0),
        "code_contracts": counts.get("code_contract", 0),
    }


def build_code_vector_entries(
    project_root: Path,
    paths: Iterable[Path],
    *,
    exclude: set[str] | None = None,
    granularity: Iterable[str] | None = DEFAULT_CODE_VECTOR_GRANULARITY,
) -> list[dict[str, Any]]:
    """Build deterministic vector-memory documents for Python code projections."""
    requested = set(_normalize_code_vector_granularity(granularity))
    code_graph = build_code_graph(project_root, paths, exclude=exclude)
    nodes_by_file: dict[str, list[CodeNode]] = defaultdict(list)
    root = project_root.resolve()
    for node in code_graph.nodes:
        if node.file_path:
            nodes_by_file[_relative_path(root, node.file_path)].append(node)

    entries: list[dict[str, Any]] = []
    for rel_path, nodes in sorted(nodes_by_file.items()):
        if not nodes:
            continue
        module = next((node for node in nodes if node.kind == NodeKind.MODULE), nodes[0])
        if "file" in requested:
            text = _code_file_text(rel_path, nodes)
            entry_id = f"code:{rel_path}"
            entries.append(_projection_entry(
                entry_id,
                text,
                tags=("projection", "code", "code-chunk"),
                metadata={
                    "memory_type": "code_chunk",
                    "projection_group": "code",
                    "projection_kind": "code_chunk",
                    "projection_source_id": entry_id,
                    "code_node_id": module.id,
                    "file_path": rel_path,
                    "module": module.module or module.name,
                    "is_test": module.is_test,
                    "symbol_count": max(len(nodes) - 1, 0),
                },
            ))
        if "test" in requested and module.is_test:
            entry_id = f"code-test:{rel_path}"
            entries.append(_projection_entry(
                entry_id,
                _code_test_file_text(rel_path, module, nodes),
                tags=("projection", "code", "code-test"),
                metadata={
                    "memory_type": "code_test",
                    "projection_group": "code",
                    "projection_kind": "code_test",
                    "projection_source_id": module.id,
                    "code_node_id": module.id,
                    "file_path": rel_path,
                    "module": module.module or module.name,
                    "is_test": True,
                    "test_symbol_count": sum(1 for node in nodes if node.kind != NodeKind.MODULE and node.is_test),
                },
            ))

        for node in sorted(nodes, key=lambda item: (item.line_start, item.name)):
            if node.kind == NodeKind.MODULE or node.is_test or not node.is_public:
                continue
            if node.kind in (NodeKind.CLASS, NodeKind.FUNCTION) and "symbol" in requested:
                entry_id = f"code-symbol:{node.id}"
                entries.append(_projection_entry(
                    entry_id,
                    _code_node_text(root, rel_path, node, label="Code symbol"),
                    tags=("projection", "code", "code-symbol", node.kind.value),
                    metadata=_code_projection_metadata(
                        memory_type="code_symbol",
                        projection_kind="code_symbol",
                        entry_id=entry_id,
                        node=node,
                        rel_path=rel_path,
                    ),
                ))
            if node.kind == NodeKind.METHOD and "method" in requested:
                entry_id = f"code-method:{node.id}"
                entries.append(_projection_entry(
                    entry_id,
                    _code_node_text(root, rel_path, node, label="Code method"),
                    tags=("projection", "code", "code-method"),
                    metadata=_code_projection_metadata(
                        memory_type="code_method",
                        projection_kind="code_method",
                        entry_id=entry_id,
                        node=node,
                        rel_path=rel_path,
                    ),
                ))
            if "contract" in requested and node.docstring:
                entry_id = f"code-contract:{node.id}"
                entries.append(_projection_entry(
                    entry_id,
                    _code_node_text(root, rel_path, node, label="Code contract"),
                    tags=("projection", "code", "code-contract", node.kind.value),
                    metadata=_code_projection_metadata(
                        memory_type="code_contract",
                        projection_kind="code_contract",
                        entry_id=entry_id,
                        node=node,
                        rel_path=rel_path,
                    ),
                ))
    return entries


def sync_code_vector_projection(
    memory: MemoryManager,
    *,
    project_root: Path,
    paths: Iterable[Path],
    exclude: set[str] | None = None,
    granularity: Iterable[str] | None = DEFAULT_CODE_VECTOR_GRANULARITY,
) -> dict[str, int]:
    """Upsert deterministic semantic code chunks into the active memory backend."""
    entries = build_code_vector_entries(project_root, paths, exclude=exclude, granularity=granularity)
    actual_by_id = _actual_projection_entries(memory, groups={"code"})
    upserted = 0
    for entry in entries:
        existing = actual_by_id.get(str(entry["id"]))
        if existing is not None and existing.metadata.get("content_hash") == entry["metadata"].get("content_hash"):
            continue
        memory.upsert(
            str(entry["id"]),
            str(entry["text"]),
            user_id=str(entry["user_id"]),
            tags=tuple(entry["tags"]),
            metadata=dict(entry["metadata"]),
        )
        upserted += 1
    return {
        "vector_entries": len(entries),
        "upserted": upserted,
        "skipped": len(entries) - upserted,
        **_code_entry_counts(entries),
    }


def _json_compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _code_reference_candidates(project_root: Path, code_graph: CodeGraph) -> list[tuple[str, str]]:
    root = project_root.resolve()
    candidates: dict[tuple[str, str], None] = {}
    for node in code_graph.nodes:
        if not node.file_path:
            continue
        rel_path = _relative_path(root, node.file_path)
        if node.kind == NodeKind.MODULE:
            values = {rel_path, str((root / rel_path).resolve())}
        else:
            values = {node.id}
            if node.module and node.name:
                values.add(f"{node.module}.{node.name}")
            if rel_path and node.name:
                values.add(f"{rel_path}:{node.name}")
        for value in values:
            value = value.strip()
            if len(value) >= 4:
                candidates[(value, node.id)] = None
    return sorted(candidates, key=lambda item: len(item[0]), reverse=True)


def _match_code_node_ids(texts: Iterable[str], candidates: list[tuple[str, str]]) -> list[str]:
    haystack = "\n".join(str(text) for text in texts if str(text))
    if not haystack:
        return []
    matched = {node_id for candidate, node_id in candidates if candidate in haystack}
    return sorted(matched)


def _task_reference_texts(task: Any, events: Iterable[Any]) -> list[str]:
    texts = [
        task.id,
        task.title,
        task.description,
        task.surface,
        task.owner,
        *task.acceptance,
        *task.guardrails,
        *task.expected_evidence,
    ]
    if task.claim is not None:
        texts.extend(task.claim.exclusive_files)
    for event in events:
        texts.extend([event.event_type, _json_compact(event.payload)])
    return texts


def _evidence_reference_texts(pack: Any) -> list[str]:
    texts = [pack.id, pack.task_id, pack.workflow_instance_id]
    for item in pack.items:
        texts.extend([item.id, item.uri, item.summary])
    return texts


def build_task_code_reference_projection(
    project_root: Path,
    paths: Iterable[Path],
    *,
    ledger: MissionLedger,
    evidence: EvidenceService | None = None,
    code_exclude: set[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Infer deterministic task/evidence-to-code links from ledger text and evidence URIs."""
    code_graph = build_code_graph(project_root, paths, exclude=code_exclude)
    candidates = _code_reference_candidates(project_root, code_graph)
    events_by_task: dict[str, list[Any]] = defaultdict(list)
    for event in ledger.list_events():
        if event.entity_kind == "task":
            events_by_task[event.entity_id].append(event)

    task_refs: list[dict[str, str]] = []
    for task in ledger.list_tasks():
        for code_node_id in _match_code_node_ids(_task_reference_texts(task, events_by_task[task.id]), candidates):
            task_refs.append({
                "task_id": task.id,
                "code_node_id": code_node_id,
                "source": "task-context",
                "reason": "matched task text, claim, or ledger event payload",
            })

    evidence_refs: list[dict[str, str]] = []
    if evidence is not None:
        for pack in evidence.list_packs():
            for code_node_id in _match_code_node_ids(_evidence_reference_texts(pack), candidates):
                evidence_refs.append({
                    "evidence_pack_id": pack.id,
                    "code_node_id": code_node_id,
                    "source": "evidence-uri",
                    "reason": "matched evidence item uri or summary",
                })

    return {"tasks": task_refs, "evidence": evidence_refs}


def build_task_vector_entries(
    ledger: MissionLedger,
    *,
    evidence: EvidenceService | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic vector-memory documents for mission/task operations."""
    entries: list[dict[str, Any]] = []
    for mission in ledger.list_missions():
        text = "\n".join([
            f"Mission {mission.id}: {mission.title}",
            f"Status: {mission.status.value}",
            f"Risk: {mission.risk_profile.value}",
            f"Origin: {mission.origin}",
            f"Description: {mission.description}",
            f"Scope repos: {', '.join(mission.scope_repos)}",
            f"Scope surfaces: {', '.join(mission.scope_surfaces)}",
        ])
        entries.append(_projection_entry(
            f"mission:{mission.id}",
            text,
            tags=("projection", "task-memory", "mission"),
            metadata={
                "memory_type": "mission",
                "projection_group": "task_memory",
                "projection_kind": "mission",
                "projection_source_id": mission.id,
                "mission_id": mission.id,
                "status": mission.status.value,
                "risk_profile": mission.risk_profile.value,
            },
        ))

    for task in ledger.list_tasks():
        text = "\n".join([
            f"Task {task.id}: {task.title}",
            f"Mission: {task.mission_id}",
            f"Status: {task.status.value}",
            f"Type: {task.type.value}",
            f"Risk: {task.risk_profile.value}",
            f"Surface: {task.surface}",
            f"Owner: {task.owner}",
            f"Description: {task.description}",
            "Acceptance:",
            *[f"- {item}" for item in task.acceptance],
            "Guardrails:",
            *[f"- {item}" for item in task.guardrails],
            "Expected evidence:",
            *[f"- {item}" for item in task.expected_evidence],
        ])
        entries.append(_projection_entry(
            f"task:{task.id}",
            text,
            tags=("projection", "task-memory", "task"),
            metadata={
                "memory_type": "task",
                "projection_group": "task_memory",
                "projection_kind": "task",
                "projection_source_id": task.id,
                "mission_id": task.mission_id,
                "task_id": task.id,
                "status": task.status.value,
                "task_type": task.type.value,
                "risk_profile": task.risk_profile.value,
                "surface": task.surface,
            },
        ))

    for event in ledger.list_events():
        text = "\n".join([
            f"Ledger event {event.id}: {event.event_type}",
            f"Entity: {event.entity_kind} {event.entity_id}",
            f"Actor: {event.actor_id}",
            f"Payload: {_json_compact(event.payload)}",
        ])
        entries.append(_projection_entry(
            f"ledger-event:{event.id}",
            text,
            tags=("projection", "task-memory", "ledger-event"),
            metadata={
                "memory_type": "ledger_event",
                "projection_group": "task_memory",
                "projection_kind": "ledger_event",
                "projection_source_id": event.id,
                "entity_id": event.entity_id,
                "entity_kind": event.entity_kind,
                "event_type": event.event_type,
            },
        ))

    for incident in ledger.list_incidents():
        text = "\n".join([
            f"Incident {incident.id}: {incident.summary}",
            f"Mission: {incident.mission_id}",
            f"Task: {incident.task_id}",
            f"Kind: {incident.kind}",
            f"Severity: {incident.severity.value}",
            f"Status: {incident.status.value}",
            "Causes:",
            *[f"- {item}" for item in incident.causes],
            "Recommended actions:",
            *[f"- {item}" for item in incident.recommended_actions],
        ])
        entries.append(_projection_entry(
            f"incident:{incident.id}",
            text,
            tags=("projection", "task-memory", "incident"),
            metadata={
                "memory_type": "incident",
                "projection_group": "task_memory",
                "projection_kind": "incident",
                "projection_source_id": incident.id,
                "mission_id": incident.mission_id,
                "task_id": incident.task_id,
                "severity": incident.severity.value,
                "status": incident.status.value,
            },
        ))

    if evidence is None:
        return entries

    for pack in evidence.list_packs():
        item_lines = [f"- {item.kind.value} {item.id}: {item.summary or item.uri}" for item in pack.items]
        text = "\n".join([
            f"Evidence pack {pack.id}",
            f"Task: {pack.task_id}",
            f"Profile: {pack.profile.value}",
            "Items:",
            *item_lines,
        ])
        entries.append(_projection_entry(
            f"evidence-pack:{pack.id}",
            text,
            tags=("projection", "task-memory", "evidence"),
            metadata={
                "memory_type": "evidence_pack",
                "projection_group": "task_memory",
                "projection_kind": "evidence_pack",
                "projection_source_id": pack.id,
                "task_id": pack.task_id,
                "profile": pack.profile.value,
            },
        ))

    for verdict in evidence.list_verdicts():
        check_lines = [f"- {check.id}: {check.result.value} {check.reason}".strip() for check in verdict.checks]
        text = "\n".join([
            f"Verification verdict {verdict.id}: {verdict.verdict.value}",
            f"Task: {verdict.task_id}",
            f"Evidence pack: {verdict.evidence_pack_id}",
            f"Profile: {verdict.profile.value}",
            "Checks:",
            *check_lines,
        ])
        entries.append(_projection_entry(
            f"verdict:{verdict.id}",
            text,
            tags=("projection", "task-memory", "verdict"),
            metadata={
                "memory_type": "verdict",
                "projection_group": "task_memory",
                "projection_kind": "verdict",
                "projection_source_id": verdict.id,
                "task_id": verdict.task_id,
                "evidence_pack_id": verdict.evidence_pack_id,
                "verdict": verdict.verdict.value,
            },
        ))

    return entries


def sync_task_vector_projection(
    memory: MemoryManager,
    *,
    ledger: MissionLedger,
    evidence: EvidenceService | None = None,
) -> dict[str, int]:
    """Upsert deterministic semantic task-memory documents into the active backend."""
    entries = build_task_vector_entries(ledger, evidence=evidence)
    actual_by_id = _actual_projection_entries(memory, groups={"task_memory"})
    upserted = 0
    for entry in entries:
        existing = actual_by_id.get(str(entry["id"]))
        if existing is not None and existing.metadata.get("content_hash") == entry["metadata"].get("content_hash"):
            continue
        memory.upsert(
            str(entry["id"]),
            str(entry["text"]),
            user_id=str(entry["user_id"]),
            tags=tuple(entry["tags"]),
            metadata=dict(entry["metadata"]),
        )
        upserted += 1
    return {"vector_entries": len(entries), "upserted": upserted, "skipped": len(entries) - upserted}


def _actual_projection_entries(memory: MemoryManager, *, groups: set[str]) -> dict[str, Any]:
    return {
        entry.id: entry
        for entry in memory.get_all()
        if str(entry.metadata.get("projection_group", "")) in groups
    }


def vector_projection_verify(
    memory: MemoryManager,
    expected_entries: Iterable[dict[str, Any]],
    *,
    groups: set[str] | None = None,
) -> dict[str, object]:
    """Verify deterministic vector projection entries exist and match content hashes."""
    expected = {str(entry["id"]): entry for entry in expected_entries}
    allowed_groups = groups or {"code", "task_memory"}
    actual_by_id = _actual_projection_entries(memory, groups=allowed_groups)

    missing = sorted(entry_id for entry_id in expected if entry_id not in actual_by_id)
    stale = sorted(
        entry_id
        for entry_id, entry in expected.items()
        if entry_id in actual_by_id
        and actual_by_id[entry_id].metadata.get("content_hash") != entry["metadata"].get("content_hash")
    )
    extra = sorted(entry_id for entry_id in actual_by_id if entry_id not in expected)
    issues = []
    if missing:
        issues.append(f"Missing vector projection entries: {len(missing)}")
    if stale:
        issues.append(f"Stale vector projection entries: {len(stale)}")
    return {
        "ok": not missing and not stale,
        "expected": len(expected),
        "actual": len(actual_by_id),
        "missing": missing[:20],
        "stale": stale[:20],
        "extra": extra[:20],
        "issues": issues,
    }


# ── Docs projection (scope docs — markdown knowledge base) ───────────────────

# Cap per-entry text so oversized pages do not blow up embedding backends.
_DOCS_TEXT_MAX_CHARS = 8000


def _docs_page_title(content: str, rel_path: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or rel_path
    return rel_path


def build_docs_entries(
    project_root: Path,
    paths: Iterable[Path],
    *,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic memory documents for markdown documentation pages.

    One entry per ``*.md`` file found under ``paths`` (files or directories,
    relative to ``project_root``), skipping any path component in ``exclude``.
    """
    excluded = exclude or set()
    root = project_root.resolve()
    files: set[Path] = set()
    for path in paths:
        target = (root / path).resolve() if not path.is_absolute() else path
        if target.is_file() and target.suffix == ".md":
            files.add(target)
        elif target.is_dir():
            files.update(p for p in target.rglob("*.md") if p.is_file())

    entries: list[dict[str, Any]] = []
    for file_path in sorted(files):
        rel_path = _relative_path(root, str(file_path))
        if any(part in excluded for part in Path(rel_path).parts):
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not content.strip():
            continue
        title = _docs_page_title(content, rel_path)
        text = f"{title}\n{rel_path}\n\n{content[:_DOCS_TEXT_MAX_CHARS]}"
        entry_id = f"docs:{rel_path}"
        entries.append(_projection_entry(
            entry_id,
            text,
            tags=("projection", "docs", "docs-page"),
            metadata={
                "memory_type": "docs_page",
                "projection_group": "docs",
                "projection_kind": "docs_page",
                "projection_source_id": entry_id,
                "file_path": rel_path,
                "title": title,
                "truncated": len(content) > _DOCS_TEXT_MAX_CHARS,
            },
        ))
    return entries


def sync_docs_projection(
    memory: MemoryManager,
    *,
    project_root: Path,
    paths: Iterable[Path],
    exclude: set[str] | None = None,
) -> dict[str, int]:
    """Upsert deterministic docs pages into the active memory backend."""
    entries = build_docs_entries(project_root, paths, exclude=exclude)
    actual_by_id = _actual_projection_entries(memory, groups={"docs"})
    upserted = 0
    for entry in entries:
        existing = actual_by_id.get(str(entry["id"]))
        if existing is not None and existing.metadata.get("content_hash") == entry["metadata"]["content_hash"]:
            continue
        memory.upsert(
            str(entry["id"]),
            str(entry["text"]),
            user_id=str(entry["user_id"]),
            tags=tuple(entry["tags"]),
            metadata=dict(entry["metadata"]),
        )
        upserted += 1
    return {
        "expected": len(entries),
        "upserted": upserted,
        "unchanged": len(entries) - upserted,
    }


def graph_projection_verify(
    memory_graph: Neo4jMemoryGraph,
    *,
    project_root: Path | None = None,
    code_paths: Iterable[Path] = (),
    code_exclude: set[str] | None = None,
    ledger: MissionLedger | None = None,
    evidence: EvidenceService | None = None,
) -> dict[str, object]:
    """Verify local projection sources are represented in Neo4j counts."""
    expected: dict[str, int] = {}
    if project_root is not None:
        code_graph = build_code_graph(project_root, code_paths, exclude=code_exclude)
        expected["code_nodes"] = len(code_graph.nodes)
        expected["code_edges"] = len(_unique_code_edges(code_graph.edges))
    if ledger is not None:
        expected["missions"] = len(ledger.list_missions())
        expected["tasks"] = len(ledger.list_tasks())
        expected["ledger_events"] = len(ledger.list_events())
        expected["incidents"] = len(ledger.list_incidents())
    if evidence is not None:
        expected["evidence_packs"] = len(evidence.list_packs())
        expected["verdicts"] = len(evidence.list_verdicts())
        expected["decisions"] = len(evidence.list_verdicts())
    if project_root is not None and ledger is not None:
        refs = build_task_code_reference_projection(
            project_root,
            code_paths,
            ledger=ledger,
            evidence=evidence,
            code_exclude=code_exclude,
        )
        expected["task_code_links"] = len(refs["tasks"])
        if evidence is not None:
            expected["evidence_code_links"] = len(refs["evidence"])

    actual = memory_graph.stats()
    issues = [
        f"Neo4j {key}={actual.get(key, 0)} but local source expects at least {value}"
        for key, value in expected.items()
        if actual.get(key, 0) < value
    ]
    return {
        "ok": not issues,
        "expected": expected,
        "actual": actual,
        "issues": issues,
    }
