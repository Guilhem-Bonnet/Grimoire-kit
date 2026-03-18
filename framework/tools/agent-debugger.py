#!/usr/bin/env python3
"""
agent-debugger.py — Débogueur de réalité pour l'écosystème agentique Grimoire.
=============================================================================

But : mesurer ce qui est réellement observable localement, et distinguer :
  - ce qui est actif,
  - ce qui est initialisé mais vide,
  - ce qui est seulement prévu par le code,
  - ce qui est contredit par les artefacts runtime.

Le debugger ne "prouve" pas l'absence d'hallucination ; il classe des claims
à partir de preuves locales vérifiables.

Usage :
  python3 agent-debugger.py --project-root . status
  python3 agent-debugger.py --project-root . report --format json
  python3 agent-debugger.py --project-root . claims
  python3 agent-debugger.py --project-root . vector
  python3 agent-debugger.py --project-root . ants

Stdlib only.
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
import threading
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEBUGGER_VERSION = "1.0.0"

MCP_AUDIT_FILE = "_grimoire/_memory/mcp-audit.jsonl"
TOKEN_USAGE_FILE = "_grimoire/_memory/token-usage.jsonl"
ROUTER_STATS_FILE = "_grimoire-output/.router-stats.jsonl"
EVENT_LOG_FILE = "_grimoire-output/.event-log.jsonl"
PHEROMONE_FILE = "_grimoire-output/pheromone-board.json"
QDRANT_META_FILE = "_grimoire-output/.qdrant_data/meta.json"
QDRANT_DIR = "_grimoire-output/.qdrant_data"
SESSION_CHAIN_FILE = "_grimoire/_memory/session-chain.jsonl"
LIFECYCLE_STATE_FILE = "_grimoire-output/.session-lifecycle/current-session.json"
DEFAULT_DASHBOARD_FILE = "_grimoire-output/agent-debugger.html"
LOCAL_MEMORY_FILES = (
    "_grimoire/_memory/grimoire.json",
    "_grimoire/_memory/memories.json",
    "_memory/memories.json",
)

STATE_ACTIVE = "active"
STATE_INITIALIZED_EMPTY = "initialized_empty"
STATE_PLANNED_ONLY = "planned_only"
STATE_CONTRADICTED = "contradicted"
STATE_UNKNOWN = "unknown"
STATE_DEGRADED = "degraded"
STATE_NOT_INTEGRATED = "not_integrated"


@dataclass
class RuntimePath:
    name: str
    relative_path: str
    exists: bool
    size_bytes: int = 0
    modified_at: str = ""


@dataclass
class CapabilityProbe:
    name: str
    state: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)


@dataclass
class ClaimCheck:
    claim: str
    verdict: str
    reason: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class HealthScore:
    total: int
    observability: int
    sessions: int
    vector_db: int
    stigmergy: int
    traceability: int


@dataclass
class PlanTask:
    title: str
    priority: str
    rationale: str
    actions: list[str] = field(default_factory=list)
    related_capabilities: list[str] = field(default_factory=list)


@dataclass
class DebugPlan:
    title: str
    tasks: list[PlanTask] = field(default_factory=list)


@dataclass
class DiagnosticSnapshot:
    version: str
    generated_at: str
    project_root: str
    paths: list[RuntimePath] = field(default_factory=list)
    capabilities: list[CapabilityProbe] = field(default_factory=list)
    claims: list[ClaimCheck] = field(default_factory=list)
    score: HealthScore | None = None
    plan: DebugPlan | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "project_root": self.project_root,
            "paths": [asdict(path) for path in self.paths],
            "capabilities": [asdict(capability) for capability in self.capabilities],
            "claims": [asdict(claim) for claim in self.claims],
            "score": asdict(self.score) if self.score else None,
            "plan": asdict(self.plan) if self.plan else None,
            "warnings": self.warnings,
        }


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_info(root: Path, relative_path: str) -> RuntimePath:
    path = root / relative_path
    exists = path.exists()
    stat = path.stat() if exists else None
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if stat else ""
    size_bytes = stat.st_size if stat else 0
    return RuntimePath(
        name=Path(relative_path).name,
        relative_path=relative_path,
        exists=exists,
        size_bytes=size_bytes,
        modified_at=modified_at,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows


def _scan_text(path: Path, needles: tuple[str, ...]) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _probe_stream(name: str, path: Path) -> CapabilityProbe:
    rows = _read_jsonl(path)
    if not path.exists():
        return CapabilityProbe(
            name=name,
            state=STATE_PLANNED_ONLY,
            summary=f"{path.name} absent.",
            evidence=[str(path)],
            warnings=["Aucune preuve runtime collectée."],
        )
    if not rows:
        return CapabilityProbe(
            name=name,
            state=STATE_INITIALIZED_EMPTY,
            summary=f"{path.name} existe mais ne contient aucun événement exploitable.",
            evidence=[str(path)],
            metrics={"entries": 0},
        )
    return CapabilityProbe(
        name=name,
        state=STATE_ACTIVE,
        summary=f"{len(rows)} événement(s) observés dans {path.name}.",
        evidence=[str(path)],
        metrics={"entries": len(rows), "latest": rows[-1].get("ts") or rows[-1].get("timestamp", "")},
    )


def probe_stigmergy(root: Path) -> CapabilityProbe:
    board_path = root / PHEROMONE_FILE
    if not board_path.exists():
        return CapabilityProbe(
            name="stigmergy",
            state=STATE_PLANNED_ONLY,
            summary="Le système de phéromones existe dans le code, mais aucun pheromone-board n'a été produit.",
            evidence=[str(board_path)],
            warnings=["Pas de preuve que la stigmergie soit réellement utilisée dans ce runtime."],
            opportunities=[
                "Prioriser la réindexation vectorielle à partir des zones chaudes.",
                "Renforcer les chemins de retrieval qui ont déjà produit de bons résultats.",
                "Utiliser les phéromones comme signal de re-ranking des chunks Qdrant.",
            ],
        )

    data = _read_json(board_path) or {}
    pheromones = data.get("pheromones", [])
    active = [item for item in pheromones if not item.get("resolved", False)]
    state = STATE_ACTIVE if active else STATE_INITIALIZED_EMPTY
    summary = (
        f"{len(active)} phéromone(s) actives, {len(pheromones)} totale(s)."
        if active else
        f"Board présent mais sans signal actif ({len(pheromones)} totale(s))."
    )
    return CapabilityProbe(
        name="stigmergy",
        state=state,
        summary=summary,
        evidence=[str(board_path)],
        metrics={"pheromones_total": len(pheromones), "pheromones_active": len(active)},
        opportunities=[
            "Piloter le garbage collection vectoriel par évaporation des signaux.",
            "Faire remonter les hot-zones du code dans les priorités d'indexation.",
        ],
    )


def probe_session_chain(root: Path) -> CapabilityProbe:
    chain_path = root / SESSION_CHAIN_FILE
    lifecycle_path = root / LIFECYCLE_STATE_FILE
    rows = _read_jsonl(chain_path)
    lifecycle = _read_json(lifecycle_path) if lifecycle_path.exists() else None

    if rows:
        completed_hooks = len([hook for hook in lifecycle.get("hooks", []) if hook.get("status") == "completed"]) if lifecycle else 0
        return CapabilityProbe(
            name="session_chain",
            state=STATE_ACTIVE,
            summary=f"{len(rows)} résumé(s) de session persisté(s).",
            evidence=[str(chain_path), str(lifecycle_path)],
            metrics={"entries": len(rows), "completed_hooks_last_run": completed_hooks},
        )

    if lifecycle:
        return CapabilityProbe(
            name="session_chain",
            state=STATE_DEGRADED,
            summary="Le lifecycle a un état enregistré, mais aucune chaîne de session n'a été persistée.",
            evidence=[str(lifecycle_path), str(chain_path)],
            warnings=["L'instrumentation de session est partielle."],
        )

    return CapabilityProbe(
        name="session_chain",
        state=STATE_PLANNED_ONLY,
        summary="Aucune chaîne de session observable.",
        evidence=[str(chain_path)],
    )


def probe_vector_db(root: Path) -> CapabilityProbe:
    meta_path = root / QDRANT_META_FILE
    qdrant_dir = root / QDRANT_DIR
    if not qdrant_dir.exists():
        return CapabilityProbe(
            name="vector_db",
            state=STATE_PLANNED_ONLY,
            summary="Aucun répertoire Qdrant local détecté.",
            evidence=[str(qdrant_dir)],
            warnings=["La DB vectorielle n'est pas initialisée localement."],
        )

    meta = _read_json(meta_path) or {}
    collections = meta.get("collections", {})
    aliases = meta.get("aliases", {})

    if collections:
        total_points = 0
        for value in collections.values():
            if isinstance(value, dict):
                total_points += int(value.get("points_count", 0) or 0)
        return CapabilityProbe(
            name="vector_db",
            state=STATE_ACTIVE,
            summary=f"Qdrant initialisé avec {len(collections)} collection(s).",
            evidence=[str(meta_path)],
            metrics={"collections": len(collections), "aliases": len(aliases), "points": total_points},
        )

    file_count = len(list(qdrant_dir.iterdir())) if qdrant_dir.exists() else 0
    return CapabilityProbe(
        name="vector_db",
        state=STATE_INITIALIZED_EMPTY,
        summary="Qdrant est initialisé localement mais aucune collection active n'est visible.",
        evidence=[str(meta_path), str(qdrant_dir)],
        metrics={"collections": 0, "aliases": len(aliases), "files": file_count},
        warnings=["Une DB vectorielle vide ne prouve aucun usage en retrieval."],
    )


def probe_local_memory(root: Path) -> CapabilityProbe:
    for relative_path in LOCAL_MEMORY_FILES:
        path = root / relative_path
        if not path.exists():
            continue
        data = _read_json(path)
        entries = data.get("entries", []) if isinstance(data, dict) else []
        return CapabilityProbe(
            name="local_memory",
            state=STATE_ACTIVE if entries else STATE_INITIALIZED_EMPTY,
            summary=(
                f"Backend mémoire local avec {len(entries)} entrée(s)."
                if entries else
                "Backend mémoire local présent mais vide."
            ),
            evidence=[str(path)],
            metrics={"entries": len(entries)},
        )

    return CapabilityProbe(
        name="local_memory",
        state=STATE_PLANNED_ONLY,
        summary="Aucun backend mémoire JSON local détecté.",
        evidence=list(LOCAL_MEMORY_FILES),
    )


def probe_ant_vector_integration(root: Path) -> CapabilityProbe:
    stigmergy_path = root / "framework" / "tools" / "stigmergy.py"
    vector_paths = (
        root / "framework" / "tools" / "rag-indexer.py",
        root / "framework" / "tools" / "memory-sync.py",
        root / "framework" / "tools" / "rag-retriever.py",
    )
    vector_refs_stigmergy = _scan_text(stigmergy_path, ("qdrant", "vector", "embedding", "retrieval"))
    stigmergy_refs_vector = any(_scan_text(path, ("stigmergy", "pheromone", "ant system")) for path in vector_paths if path.exists())
    board = _read_json(root / PHEROMONE_FILE) or {}
    pheromones = board.get("pheromones", []) if isinstance(board, dict) else []
    active_pheromones = [item for item in pheromones if isinstance(item, dict) and not item.get("resolved", False)]

    if vector_refs_stigmergy or stigmergy_refs_vector:
        state = STATE_ACTIVE if active_pheromones else STATE_DEGRADED
        summary = (
            "L'intégration code entre stigmergy et la couche vectorielle est présente et des signaux actifs existent."
            if active_pheromones else
            "L'intégration code existe, mais aucun signal phéromonique actif ne peut encore influencer la couche vectorielle."
        )
        return CapabilityProbe(
            name="ant_vector_integration",
            state=state,
            summary=summary,
            evidence=[str(stigmergy_path), *[str(path) for path in vector_paths if path.exists()]],
            metrics={"active_pheromones": len(active_pheromones)},
        )

    return CapabilityProbe(
        name="ant_vector_integration",
        state=STATE_NOT_INTEGRATED,
        summary="Aucune intégration observable entre le système de phéromones et la DB vectorielle.",
        evidence=[str(stigmergy_path), *[str(path) for path in vector_paths if path.exists()]],
        opportunities=[
            "Réindexer d'abord les fichiers avec forte intensité phéromonique.",
            "Booster le score des chunks issus des zones marquées NEED/ALERT.",
            "Apprendre quels chemins de retrieval résolvent réellement les tickets, puis les renforcer.",
            "Diriger l'éviction du cache sémantique selon l'évaporation des signaux.",
            "Utiliser les phéromones comme signal de choix de collection (memory/docs/code).",
        ],
    )


def compute_health_score(capabilities: list[CapabilityProbe]) -> HealthScore:
    by_name = {capability.name: capability for capability in capabilities}

    def _score_for(name: str, active: int, empty: int, degraded: int, planned: int) -> int:
        state = by_name.get(name, CapabilityProbe(name=name, state=STATE_UNKNOWN, summary="")).state
        if state == STATE_ACTIVE:
            return active
        if state == STATE_INITIALIZED_EMPTY:
            return empty
        if state == STATE_DEGRADED:
            return degraded
        if state == STATE_NOT_INTEGRATED:
            return planned
        if state == STATE_PLANNED_ONLY:
            return planned
        if state == STATE_CONTRADICTED:
            return 0
        return max(planned - 10, 0)

    observability = round((
        _score_for("mcp_audit", 100, 50, 30, 10)
        + _score_for("router_stats", 100, 50, 30, 10)
        + _score_for("token_usage", 100, 50, 30, 10)
    ) / 3)
    sessions = _score_for("session_chain", 100, 45, 30, 10)
    vector_db = _score_for("vector_db", 100, 35, 25, 10)
    stigmergy = _score_for("stigmergy", 100, 35, 25, 10)
    traceability = round((observability + sessions) / 2)
    total = round((observability + sessions + vector_db + stigmergy + traceability) / 5)
    return HealthScore(
        total=total,
        observability=observability,
        sessions=sessions,
        vector_db=vector_db,
        stigmergy=stigmergy,
        traceability=traceability,
    )


def build_claims(capabilities: list[CapabilityProbe]) -> list[ClaimCheck]:
    by_name = {capability.name: capability for capability in capabilities}

    def _verdict(name: str, positive_claim: str, negative_reason: str) -> ClaimCheck:
        capability = by_name[name]
        if capability.state == STATE_ACTIVE:
            return ClaimCheck(claim=positive_claim, verdict="supported", reason=capability.summary, evidence=capability.evidence)
        if capability.state in {STATE_INITIALIZED_EMPTY, STATE_DEGRADED, STATE_NOT_INTEGRATED}:
            return ClaimCheck(claim=positive_claim, verdict="contradicted", reason=negative_reason, evidence=capability.evidence)
        return ClaimCheck(claim=positive_claim, verdict="unverifiable", reason=capability.summary, evidence=capability.evidence)

    claims = [
        _verdict("vector_db", "La DB vectorielle est réellement utilisée.", "Aucune preuve d'usage runtime effectif de la DB vectorielle n'est visible."),
        _verdict("stigmergy", "Le système de phéromones est actif.", "Le système de phéromones n'a pas produit de signal runtime exploitable."),
        _verdict("session_chain", "Les sessions sont réellement chaînées.", "Aucune chaîne de session active n'est observable."),
        _verdict("ant_vector_integration", "L'ant system pilote la couche vectorielle.", "Aucune intégration observable entre l'ant system et la DB vectorielle."),
    ]

    observability = by_name["mcp_audit"].state == STATE_ACTIVE and by_name["router_stats"].state == STATE_ACTIVE
    claims.append(
        ClaimCheck(
            claim="On peut réduire le risque d'hallucination par preuve locale.",
            verdict="supported" if observability else "unverifiable",
            reason=(
                "Les traces MCP et le routage modèle sont observables ; on peut vérifier certains faits localement."
                if observability else
                "Sans traces d'audit suffisantes, on ne peut pas vérifier les affirmations du runtime."
            ),
            evidence=by_name["mcp_audit"].evidence + by_name["router_stats"].evidence,
        ),
    )
    claims.append(
        ClaimCheck(
            claim="Des sessions parallèles multi-agents sont prouvées.",
            verdict="contradicted",
            reason="Les artefacts locaux n'apportent aucune preuve de parallélisme agentique réel dans ce runtime Copilot.",
            evidence=by_name["session_chain"].evidence + by_name["mcp_audit"].evidence,
        ),
    )
    return claims


def build_plan(capabilities: list[CapabilityProbe]) -> DebugPlan:
    by_name = {capability.name: capability for capability in capabilities}
    tasks: list[PlanTask] = []

    if by_name["vector_db"].state != STATE_ACTIVE:
        tasks.append(PlanTask(
            title="Activer réellement la couche vectorielle",
            priority="P1",
            rationale="Qdrant est vide ou non prouvé ; tant qu'il n'y a pas de collections remplies, aucune réponse ne peut être attribuée au retrieval vectoriel.",
            actions=[
                "Lancer rag-indexer.py status puis index incremental/full.",
                "Créer au moins une collection non vide memory/docs/code.",
                "Ajouter un témoin d'usage runtime du retrieval dans les traces.",
            ],
            related_capabilities=["vector_db", "local_memory"],
        ))

    if by_name["stigmergy"].state != STATE_ACTIVE:
        tasks.append(PlanTask(
            title="Faire exister des signaux phéromoniques utiles",
            priority="P1",
            rationale="Le code de stigmergie existe, mais l'environnement ne contient pas encore de signal vivant exploitable.",
            actions=[
                "Émettre des signaux NEED/ALERT/PROGRESS depuis les outils qui détectent une situation intéressante.",
                "Relancer le lifecycle post-session pour produire et évaporer proprement les artefacts.",
                "Ajouter un check CI qui échoue si le board n'est jamais mis à jour sur un scénario instrumenté.",
            ],
            related_capabilities=["stigmergy", "session_chain"],
        ))

    if by_name["ant_vector_integration"].state != STATE_ACTIVE:
        tasks.append(PlanTask(
            title="Brancher l'ant system sur la DB vectorielle",
            priority="P2",
            rationale="L'ant system et Qdrant vivent séparément ; il manque le pont qui transforme les signaux en priorités de retrieval/indexation.",
            actions=[
                "Booster le ranking des chunks issus des zones NEED/ALERT.",
                "Prioriser la réindexation des fichiers dans les hot-zones phéromoniques.",
                "Tracer explicitement quand une phéromone influence une décision vectorielle.",
            ],
            related_capabilities=["ant_vector_integration", "vector_db", "stigmergy"],
        ))

    if by_name["session_chain"].state != STATE_ACTIVE:
        tasks.append(PlanTask(
            title="Rendre la continuité de session falsifiable",
            priority="P2",
            rationale="Sans chaîne de session exploitable, impossible de savoir si la continuité a réellement été utilisée ou seulement décrite.",
            actions=[
                "Vérifier qu'un post-session ajoute bien une entrée JSONL.",
                "Corréler les résumés de session aux décisions réellement prises ensuite.",
            ],
            related_capabilities=["session_chain"],
        ))

    tasks.append(PlanTask(
        title="Durcir la preuve anti-hallucination",
        priority="P0",
        rationale="Le bon niveau de garantie n'est pas la confiance dans le modèle, mais la corrélation entre affirmation et artefacts observables.",
        actions=[
            "Conserver mcp-audit et router-stats comme sources de vérité minimales.",
            "Ajouter des witnesses append-only pour chaque hook critique.",
            "Vérifier les claims du modèle via agent-debugger.py claims avant de les considérer comme acquis.",
        ],
        related_capabilities=["mcp_audit", "router_stats", "token_usage"],
    ))

    tasks.sort(key=lambda task: task.priority)
    return DebugPlan(title="Plan de fiabilisation agentique", tasks=tasks)


def build_snapshot(project_root: Path) -> DiagnosticSnapshot:
    root = project_root.resolve()
    paths = [
        _file_info(root, MCP_AUDIT_FILE),
        _file_info(root, TOKEN_USAGE_FILE),
        _file_info(root, ROUTER_STATS_FILE),
        _file_info(root, EVENT_LOG_FILE),
        _file_info(root, PHEROMONE_FILE),
        _file_info(root, QDRANT_META_FILE),
        _file_info(root, SESSION_CHAIN_FILE),
        _file_info(root, LIFECYCLE_STATE_FILE),
    ]
    capabilities = [
        _probe_stream("mcp_audit", root / MCP_AUDIT_FILE),
        _probe_stream("router_stats", root / ROUTER_STATS_FILE),
        _probe_stream("token_usage", root / TOKEN_USAGE_FILE),
        _probe_stream("event_log", root / EVENT_LOG_FILE),
        probe_stigmergy(root),
        probe_session_chain(root),
        probe_vector_db(root),
        probe_local_memory(root),
        probe_ant_vector_integration(root),
    ]
    score = compute_health_score(capabilities)
    claims = build_claims(capabilities)
    plan = build_plan(capabilities)
    warnings = [claim.reason for claim in claims if claim.verdict == "contradicted"]
    return DiagnosticSnapshot(
        version=DEBUGGER_VERSION,
        generated_at=_now_iso(),
        project_root=str(root),
        paths=paths,
        capabilities=capabilities,
        claims=claims,
        score=score,
        plan=plan,
        warnings=warnings,
    )


def _print_capability(capability: CapabilityProbe) -> None:
    print(f"- {capability.name}: {capability.state}")
    print(f"  {capability.summary}")
    if capability.metrics:
        metrics = ", ".join(f"{key}={value}" for key, value in capability.metrics.items())
        print(f"  metrics: {metrics}")
    for warning in capability.warnings:
        print(f"  warning: {warning}")
    for opportunity in capability.opportunities[:3]:
        print(f"  idea: {opportunity}")


def print_status(snapshot: DiagnosticSnapshot) -> None:
    score = snapshot.score.total if snapshot.score else 0
    print(f"Agent debugger — health score: {score}/100")
    print(f"Generated at: {snapshot.generated_at}")
    print()
    for capability in snapshot.capabilities:
        if capability.name in {"mcp_audit", "router_stats", "token_usage", "session_chain", "vector_db", "stigmergy", "ant_vector_integration"}:
            _print_capability(capability)


def print_claims(snapshot: DiagnosticSnapshot) -> None:
    for claim in snapshot.claims:
        print(f"- {claim.verdict}: {claim.claim}")
        print(f"  {claim.reason}")


def print_plan(snapshot: DiagnosticSnapshot) -> None:
    if not snapshot.plan or not snapshot.plan.tasks:
        print("Aucun plan généré.")
        return
    print(snapshot.plan.title)
    for task in snapshot.plan.tasks:
        print(f"- [{task.priority}] {task.title}")
        print(f"  {task.rationale}")
        for action in task.actions:
            print(f"  -> {action}")


def print_vector(snapshot: DiagnosticSnapshot) -> None:
    for name in ("vector_db", "local_memory", "ant_vector_integration"):
        _print_capability(next(capability for capability in snapshot.capabilities if capability.name == name))


def print_ants(snapshot: DiagnosticSnapshot) -> None:
    for name in ("stigmergy", "ant_vector_integration"):
        _print_capability(next(capability for capability in snapshot.capabilities if capability.name == name))


def generate_html(snapshot: DiagnosticSnapshot) -> str:
        data_json = json.dumps(snapshot.to_dict(), ensure_ascii=False)
        return _HTML_TEMPLATE.replace("__AGENT_DEBUGGER_DATA__", data_json)


def write_dashboard(project_root: Path, output: Path | None = None) -> Path:
        snapshot = build_snapshot(project_root)
        target = (output or (project_root / DEFAULT_DASHBOARD_FILE)).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generate_html(snapshot), encoding="utf-8")
        return target


def serve_dashboard(project_root: Path, output: Path | None = None, port: int = 8765, open_browser: bool = False) -> int:
        target = write_dashboard(project_root, output)
        directory = target.parent

        class Handler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args: Any, **kwargs: Any) -> None:
                        super().__init__(*args, directory=str(directory), **kwargs)

        with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
                url = f"http://127.0.0.1:{port}/{target.name}"
                print(url)
                if open_browser:
                        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
                try:
                        httpd.serve_forever()
                except KeyboardInterrupt:
                        return 0


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang=\"fr\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Agent Debugger</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#0d1117;color:#e6edf3}header{padding:24px 28px;border-bottom:1px solid #30363d;background:linear-gradient(135deg,#161b22,#0d1117)}h1{margin:0;font-size:28px}header p{margin:8px 0 0;color:#8b949e;max-width:900px}.wrap{padding:24px;display:grid;gap:24px}.hero{display:grid;grid-template-columns:300px 1fr;gap:20px}.score{background:#161b22;border:1px solid #30363d;border-radius:18px;padding:20px;display:flex;flex-direction:column;justify-content:center;align-items:center}.score .n{font-size:72px;font-weight:800;line-height:1;color:#58a6ff}.score .l{margin-top:8px;color:#8b949e;text-transform:uppercase;letter-spacing:.08em;font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.card{background:#161b22;border:1px solid #30363d;border-radius:18px;padding:18px}.card h2,.card h3{margin:0 0 10px}.muted{color:#8b949e}.pill{display:inline-flex;align-items:center;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}.active{background:rgba(63,185,80,.15);color:#3fb950}.initialized_empty{background:rgba(210,153,34,.18);color:#d29922}.planned_only,.not_integrated{background:rgba(248,81,73,.15);color:#f85149}.degraded{background:rgba(188,140,255,.16);color:#bc8cff}.contradicted{background:rgba(248,81,73,.15);color:#f85149}.supported{background:rgba(63,185,80,.15);color:#3fb950}.unverifiable{background:rgba(210,153,34,.18);color:#d29922}.kpis{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:10px}.kpi{background:#0d1117;border:1px solid #30363d;border-radius:14px;padding:12px}.kpi .v{font-size:28px;font-weight:700}.bars{display:grid;gap:10px}.bar{display:grid;grid-template-columns:180px 1fr 55px;gap:10px;align-items:center}.track{height:12px;border-radius:999px;background:#0d1117;border:1px solid #30363d;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,#f0883e,#58a6ff,#3fb950)}ul{padding-left:18px}li{margin:6px 0}.cols{display:grid;grid-template-columns:1.1fr .9fr;gap:20px}.list{display:grid;gap:12px}.claim{padding:14px;border-radius:14px;border:1px solid #30363d;background:#0d1117}.claim h4{margin:0 0 8px;font-size:15px}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:10px 12px;border-bottom:1px solid #30363d;text-align:left;vertical-align:top}.table th{color:#8b949e;font-weight:600}.task{padding:14px;border-radius:16px;border:1px solid #30363d;background:#0d1117}.task .top{display:flex;align-items:center;gap:10px;justify-content:space-between}.prio{font-weight:800;color:#58a6ff}.small{font-size:13px}.footer{padding:0 24px 24px;color:#8b949e}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;word-break:break-all}@media (max-width: 980px){.hero,.cols{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,minmax(120px,1fr))}.bar{grid-template-columns:1fr}.bar .track{order:3}.bar .pct{order:2}}
</style>
</head>
<body>
<div id=\"app\"></div>
<script>
const data = __AGENT_DEBUGGER_DATA__;
const esc = (value) => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const stateClass = (value) => esc(String(value).replaceAll(' ','_'));
const pct = (value) => Math.max(0, Math.min(100, Number(value) || 0));

function renderKpis(score){
    return `
        <div class=\"kpis\">
            <div class=\"kpi\"><div class=\"muted small\">Observabilité</div><div class=\"v\">${pct(score.observability)}</div></div>
            <div class=\"kpi\"><div class=\"muted small\">Sessions</div><div class=\"v\">${pct(score.sessions)}</div></div>
            <div class=\"kpi\"><div class=\"muted small\">Vector DB</div><div class=\"v\">${pct(score.vector_db)}</div></div>
            <div class=\"kpi\"><div class=\"muted small\">Stigmergy</div><div class=\"v\">${pct(score.stigmergy)}</div></div>
            <div class=\"kpi\"><div class=\"muted small\">Traçabilité</div><div class=\"v\">${pct(score.traceability)}</div></div>
        </div>`;
}

function renderBars(score){
    const items = [
        ['Observabilité', score.observability],
        ['Sessions', score.sessions],
        ['Vector DB', score.vector_db],
        ['Stigmergy', score.stigmergy],
        ['Traçabilité', score.traceability],
    ];
    return `<div class=\"bars\">${items.map(([label, value]) => `
        <div class=\"bar\">
            <div>${esc(label)}</div>
            <div class=\"track\"><div class=\"fill\" style=\"width:${pct(value)}%\"></div></div>
            <div class=\"pct\">${pct(value)}%</div>
        </div>`).join('')}</div>`;
}

function renderCapabilities(capabilities){
    return `<div class=\"grid\">${capabilities.map(cap => `
        <div class=\"card\">
            <div style=\"display:flex;justify-content:space-between;gap:10px;align-items:flex-start\">
                <h3>${esc(cap.name)}</h3>
                <span class=\"pill ${stateClass(cap.state)}\">${esc(cap.state)}</span>
            </div>
            <p class=\"small\">${esc(cap.summary)}</p>
            ${Object.keys(cap.metrics || {}).length ? `<p class=\"muted small\">${Object.entries(cap.metrics).map(([k,v]) => `${esc(k)}=${esc(v)}`).join(' · ')}</p>` : ''}
            ${cap.warnings?.length ? `<ul>${cap.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}
            ${cap.opportunities?.length ? `<div class=\"muted small\">Idées</div><ul>${cap.opportunities.slice(0,3).map(o => `<li>${esc(o)}</li>`).join('')}</ul>` : ''}
        </div>`).join('')}</div>`;
}

function renderClaims(claims){
    return `<div class=\"list\">${claims.map(claim => `
        <div class=\"claim\">
            <div style=\"display:flex;justify-content:space-between;gap:10px;align-items:flex-start\">
                <h4>${esc(claim.claim)}</h4>
                <span class=\"pill ${stateClass(claim.verdict)}\">${esc(claim.verdict)}</span>
            </div>
            <p class=\"small\">${esc(claim.reason)}</p>
            ${claim.evidence?.length ? `<div class=\"mono\">${claim.evidence.map(esc).join('<br>')}</div>` : ''}
        </div>`).join('')}</div>`;
}

function renderPlan(plan){
    if (!plan || !plan.tasks?.length){ return '<div class=\"task\">Aucun plan généré.</div>'; }
    return `<div class=\"list\">${plan.tasks.map(task => `
        <div class=\"task\">
            <div class=\"top\"><strong>${esc(task.title)}</strong><span class=\"prio\">${esc(task.priority)}</span></div>
            <p class=\"small\">${esc(task.rationale)}</p>
            <ul>${(task.actions || []).map(action => `<li>${esc(action)}</li>`).join('')}</ul>
            ${task.related_capabilities?.length ? `<div class=\"muted small\">Capacités liées : ${task.related_capabilities.map(esc).join(', ')}</div>` : ''}
        </div>`).join('')}</div>`;
}

function renderPaths(paths){
    return `<table class=\"table\"><thead><tr><th>Fichier</th><th>Présent</th><th>Taille</th><th>Modifié</th></tr></thead><tbody>${paths.map(path => `
        <tr>
            <td class=\"mono\">${esc(path.relative_path)}</td>
            <td>${path.exists ? 'oui' : 'non'}</td>
            <td>${esc(path.size_bytes)}</td>
            <td>${esc(path.modified_at || '—')}</td>
        </tr>`).join('')}</tbody></table>`;
}

document.getElementById('app').innerHTML = `
    <header>
        <h1>Agent Debugger Visuel</h1>
        <p>Vue humaine des preuves runtime : ce qui fonctionne réellement, ce qui est vide, ce qui est seulement promis, et ce qui est contredit.</p>
    </header>
    <main class=\"wrap\">
        <section class=\"hero\">
            <div class=\"score\">
                <div class=\"n\">${pct(data.score.total)}</div>
                <div class=\"l\">health score</div>
                <div class=\"muted small\" style=\"margin-top:12px;text-align:center\">${esc(data.generated_at)}<br>${esc(data.project_root)}</div>
            </div>
            <div class=\"card\">
                <h2>Vue d'ensemble</h2>
                <p class=\"muted\">Le score n'est pas un jugement de valeur, mais une mesure de la falsifiabilité du système.</p>
                ${renderKpis(data.score)}
                <div style=\"margin-top:14px\">${renderBars(data.score)}</div>
            </div>
        </section>

        <section class=\"cols\">
            <div class=\"card\">
                <h2>Claims vérifiés</h2>
                ${renderClaims(data.claims)}
            </div>
            <div class=\"card\">
                <h2>Plan priorisé</h2>
                ${renderPlan(data.plan)}
            </div>
        </section>

        <section class=\"card\">
            <h2>Capacités observées</h2>
            ${renderCapabilities(data.capabilities)}
        </section>

        <section class=\"card\">
            <h2>Artefacts inspectés</h2>
            ${renderPaths(data.paths)}
        </section>
    </main>
    <div class=\"footer\">Sortie générée par agent-debugger.py — les verdicts sont basés sur des artefacts locaux, pas sur une promesse de prompt.</div>`;
</script>
</body>
</html>"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-debugger",
        description="Reality-first debugger for Grimoire agent observability",
    )
    parser.add_argument("--project-root", type=Path, default=Path(), help="Project root")
    parser.add_argument("--version", action="version", version=f"%(prog)s {DEBUGGER_VERSION}")

    subs = parser.add_subparsers(dest="command", help="Command")
    report = subs.add_parser("report", help="Full report")
    report.add_argument("--format", choices=("text", "json"), default="text")
    subs.add_parser("status", help="Short status overview")
    subs.add_parser("claims", help="Claim verification summary")
    subs.add_parser("vector", help="Vector DB reality check")
    subs.add_parser("ants", help="Stigmergy / ant-system reality check")
    subs.add_parser("plan", help="Generated remediation plan")
    generate = subs.add_parser("generate", help="Generate the visual HTML debugger")
    generate.add_argument("--output", type=Path, default=None, help="Output HTML path")
    serve = subs.add_parser("serve", help="Serve the visual debugger locally")
    serve.add_argument("--output", type=Path, default=None, help="Output HTML path")
    serve.add_argument("--port", type=int, default=8765, help="Local port")
    serve.add_argument("--open-browser", action="store_true", help="Open browser automatically")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    snapshot = build_snapshot(args.project_root)
    if args.command == "report":
        if args.format == "json":
            print(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False))
        else:
            print_status(snapshot)
            print()
            print_claims(snapshot)
        return 0
    if args.command == "status":
        print_status(snapshot)
        return 0
    if args.command == "claims":
        print_claims(snapshot)
        return 0
    if args.command == "vector":
        print_vector(snapshot)
        return 0
    if args.command == "ants":
        print_ants(snapshot)
        return 0
    if args.command == "plan":
        print_plan(snapshot)
        return 0
    if args.command == "generate":
        target = write_dashboard(args.project_root, args.output)
        print(str(target))
        return 0
    if args.command == "serve":
        return serve_dashboard(args.project_root, args.output, args.port, args.open_browser)
    return 1


if __name__ == "__main__":
    sys.exit(main())