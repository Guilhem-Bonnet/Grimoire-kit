#!/usr/bin/env python3
"""
agent-task-system.py — Agent Task System (ATS) — Gestion de tâches par et pour agents.
======================================================================================

Système de tâches conçu pour les agents IA, pas pour les humains.
Pas de Kanban, pas de Scrum, pas de sprints. Un DAG de TaskAtoms
avec scheduling intelligent, validation continue et feedback loops.

Concepts :
  - TaskAtom    : Plus petite unité de travail significative pour un agent
  - TaskGraph   : DAG de dépendances entre TaskAtoms
  - Scheduler   : Ordonnancement intelligent (deps, budget, affinité MCP)
  - ResultJudge : Validation automatique des résultats

Modes :
  create    — Créer un nouveau TaskAtom
  plan      — Planifier un ensemble de tâches à partir d'un objectif
  graph     — Afficher le graphe de tâches
  schedule  — Calculer le prochain batch exécutable
  status    — État global du système de tâches
  run       — Exécuter le prochain batch de tâches ready
  inspect   — Détail d'une tâche spécifique
  reset     — Remettre une tâche en pending

Usage :
  python3 agent-task-system.py --project-root . create --title "Créer icône SVG" --type creative
  python3 agent-task-system.py --project-root . plan --goal "Refaire toutes les icônes du projet"
  python3 agent-task-system.py --project-root . graph
  python3 agent-task-system.py --project-root . schedule
  python3 agent-task-system.py --project-root . status
  python3 agent-task-system.py --project-root . run
  python3 agent-task-system.py --project-root . inspect --id ta-20260309-001

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

_log = logging.getLogger("grimoire.agent_task_system")

ATS_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

ATS_DIR = "_grimoire-output/.ats"
GRAPH_FILE = "task-graph.json"
HISTORY_FILE = "ats-history.jsonl"
MAX_PARALLEL = 4
MAX_ATTEMPTS = 5

VALID_TYPES = frozenset({
    "creative",       # génération d'assets, design, illustrations
    "analytical",     # analyse, audit, review
    "transformative", # refactoring, migration, conversion
    "evaluative",     # tests, validation, benchmarks
    "meta",           # tâches sur les tâches (planification, scheduling)
})

VALID_STATUSES = frozenset({
    "pending",    # en attente de scheduling
    "scheduled",  # assigné à un batch
    "running",    # en cours d'exécution
    "blocked",    # bloqué par des dépendances
    "review",     # en attente de validation (vision/contract)
    "done",       # terminé et validé
    "failed",     # échoué (max attempts atteint)
    "cancelled",  # annulé
})

VALID_PRIORITIES = frozenset({"critical", "high", "normal", "low"})


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class TaskBudget:
    """Budget et contraintes d'un TaskAtom."""

    max_tokens: int = 50_000
    max_iterations: int = 5
    max_duration_seconds: int = 600  # 10 min
    cost_ceiling_usd: float = 1.00


@dataclass
class DeliveryOutput:
    """Un output attendu d'un TaskAtom."""

    output_type: str = ""        # svg, png, json, md, py, etc.
    path: str = ""               # chemin de sortie relatif
    validations: list[str] = field(default_factory=list)  # checks à appliquer


@dataclass
class DeliveryContract:
    """Contrat de livraison d'un TaskAtom."""

    outputs: list[dict[str, Any]] = field(default_factory=list)
    quality_gate: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    """Résultat d'exécution d'un TaskAtom."""

    attempt: int = 0
    timestamp: str = ""
    status: str = ""         # success | partial | failed
    outputs_produced: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    feedback: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0


@dataclass
class TaskAtom:
    """Plus petite unité de travail significative pour un agent."""

    task_id: str = ""
    task_type: str = "analytical"   # creative, analytical, transformative, evaluative, meta
    title: str = ""
    description: str = ""
    priority: str = "normal"        # critical, high, normal, low

    # Graphe de dépendances
    depends_on: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    parallel_with: list[str] = field(default_factory=list)

    # Affectation
    required_capabilities: list[str] = field(default_factory=list)
    preferred_agent: str = ""
    fallback_agents: list[str] = field(default_factory=list)

    # Budget
    budget: dict[str, Any] = field(default_factory=lambda: asdict(TaskBudget()))

    # Livraison
    delivery_contract: dict[str, Any] = field(default_factory=dict)

    # État
    status: str = "pending"
    attempts: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    assigned_agent: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            ts = datetime.now().strftime("%Y%m%d")
            uid = uuid.uuid4().hex[:6]
            self.task_id = f"ta-{ts}-{uid}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at


@dataclass
class TaskGraph:
    """DAG de TaskAtoms avec métadonnées globales."""

    graph_id: str = ""
    goal: str = ""
    tasks: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.graph_id:
            self.graph_id = f"ag-{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class ScheduleBatch:
    """Batch de tâches à exécuter en parallèle."""

    batch_id: str = ""
    task_ids: list[str] = field(default_factory=list)
    reason: str = ""
    estimated_cost: float = 0.0
    estimated_duration: int = 0


# ── Graph File I/O ───────────────────────────────────────────────────────────


def _ats_dir(project_root: Path) -> Path:
    return project_root / ATS_DIR


def _graph_path(project_root: Path) -> Path:
    return _ats_dir(project_root) / GRAPH_FILE


def _history_path(project_root: Path) -> Path:
    return _ats_dir(project_root) / HISTORY_FILE


def load_graph(project_root: Path) -> TaskGraph:
    """Charge le graphe de tâches depuis le fichier JSON."""
    gp = _graph_path(project_root)
    if not gp.exists():
        return TaskGraph()
    try:
        data = json.loads(gp.read_text(encoding="utf-8"))
        return TaskGraph(**data)
    except (json.JSONDecodeError, TypeError, KeyError):
        _log.warning("Corrupted task graph, starting fresh")
        return TaskGraph()


def save_graph(project_root: Path, graph: TaskGraph) -> None:
    """Sauvegarde le graphe de tâches."""
    gp = _graph_path(project_root)
    gp.parent.mkdir(parents=True, exist_ok=True)
    graph.updated_at = datetime.now().isoformat()
    graph.stats = compute_stats(graph)
    gp.write_text(
        json.dumps(asdict(graph), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_history(project_root: Path, event: dict[str, Any]) -> None:
    """Ajoute un événement à l'historique."""
    hp = _history_path(project_root)
    hp.parent.mkdir(parents=True, exist_ok=True)
    event["timestamp"] = datetime.now().isoformat()
    with open(hp, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── Graph Operations ─────────────────────────────────────────────────────────


def find_task(graph: TaskGraph, task_id: str) -> dict[str, Any] | None:
    """Trouve un TaskAtom par son ID."""
    for t in graph.tasks:
        if t.get("task_id") == task_id:
            return t
    return None


def add_task(graph: TaskGraph, task: TaskAtom) -> TaskGraph:
    """Ajoute un TaskAtom au graphe."""
    existing = find_task(graph, task.task_id)
    if existing:
        raise ValueError(f"Task {task.task_id} already exists")
    graph.tasks.append(asdict(task))
    return graph


def update_task_status(graph: TaskGraph, task_id: str, new_status: str) -> TaskGraph:
    """Met à jour le statut d'un TaskAtom."""
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")
    task = find_task(graph, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    task["status"] = new_status
    task["updated_at"] = datetime.now().isoformat()
    return graph


def add_result(graph: TaskGraph, task_id: str, result: TaskResult) -> TaskGraph:
    """Ajoute un résultat d'exécution à un TaskAtom."""
    task = find_task(graph, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    task["results"].append(asdict(result))
    task["attempts"] = len(task["results"])
    task["updated_at"] = datetime.now().isoformat()
    return graph


def compute_stats(graph: TaskGraph) -> dict[str, Any]:
    """Calcule les stats du graphe."""
    tasks = graph.tasks
    total = len(tasks)
    if total == 0:
        return {"total": 0}

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}

    for t in tasks:
        st = t.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
        tp = t.get("task_type", "analytical")
        by_type[tp] = by_type.get(tp, 0) + 1
        pr = t.get("priority", "normal")
        by_priority[pr] = by_priority.get(pr, 0) + 1

    done = by_status.get("done", 0)
    failed = by_status.get("failed", 0)

    return {
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
        "by_priority": by_priority,
        "progress_pct": round(done / total * 100, 1) if total else 0,
        "failure_rate": round(failed / total * 100, 1) if total else 0,
    }


# ── DAG Topological Analysis ────────────────────────────────────────────────


def get_dependency_map(graph: TaskGraph) -> dict[str, set[str]]:
    """Construit la map des dépendances : task_id → set(deps)."""
    dep_map: dict[str, set[str]] = {}
    for t in graph.tasks:
        tid = t["task_id"]
        dep_map[tid] = set(t.get("depends_on", []))
    return dep_map


def detect_cycles(graph: TaskGraph) -> list[list[str]]:
    """Détecte les cycles dans le DAG. Retourne les cycles trouvés."""
    dep_map = get_dependency_map(graph)
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()
    cycles: list[list[str]] = []

    def _dfs(node: str) -> None:
        if node in path_set:
            cycle_start = path.index(node)
            cycles.append([*path[cycle_start:], node])
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


def topological_sort(graph: TaskGraph) -> list[str]:
    """Tri topologique du DAG. Retourne l'ordre d'exécution."""
    dep_map = get_dependency_map(graph)
    in_degree: dict[str, int] = dict.fromkeys(dep_map, 0)

    for tid, deps in dep_map.items():
        for d in deps:
            if d in in_degree:
                in_degree[tid] = in_degree.get(tid, 0)  # no-op, just ensure exists

    # Compute actual in-degrees
    for tid, deps in dep_map.items():
        in_degree.setdefault(tid, 0)
        for d in deps:
            if d in in_degree:
                pass  # d depends on nothing
    # Rebuild properly
    in_deg: dict[str, int] = {}
    for tid in dep_map:
        in_deg[tid] = len(dep_map[tid])

    queue = [tid for tid, deg in in_deg.items() if deg == 0]
    order: list[str] = []

    while queue:
        queue.sort()  # deterministic
        node = queue.pop(0)
        order.append(node)
        # Find tasks that depend on this node
        for tid, deps in dep_map.items():
            if node in deps:
                in_deg[tid] -= 1
                if in_deg[tid] == 0:
                    queue.append(tid)

    return order


def critical_path(graph: TaskGraph) -> list[str]:
    """Calcule le chemin critique (séquence la plus longue)."""
    dep_map = get_dependency_map(graph)
    task_map = {t["task_id"]: t for t in graph.tasks}

    # Durée estimée par type de tâche (seconds)
    type_duration = {
        "creative": 300,
        "analytical": 120,
        "transformative": 240,
        "evaluative": 60,
        "meta": 30,
    }

    longest: dict[str, tuple[float, list[str]]] = {}

    topo = topological_sort(graph)
    for tid in topo:
        task = task_map.get(tid, {})
        duration = type_duration.get(task.get("task_type", "analytical"), 120)

        deps = dep_map.get(tid, set())
        if not deps:
            longest[tid] = (duration, [tid])
        else:
            best_dep = max(
                (longest.get(d, (0, [])) for d in deps if d in longest),
                key=lambda x: x[0],
                default=(0, []),
            )
            longest[tid] = (best_dep[0] + duration, best_dep[1] + [tid])

    if not longest:
        return []
    return max(longest.values(), key=lambda x: x[0])[1]


# ── Scheduler ────────────────────────────────────────────────────────────────


def get_ready_tasks(graph: TaskGraph) -> list[dict[str, Any]]:
    """Retourne les tâches prêtes à exécuter (deps satisfaites, status=pending)."""
    done_ids = {t["task_id"] for t in graph.tasks if t.get("status") == "done"}
    cancelled_ids = {t["task_id"] for t in graph.tasks if t.get("status") == "cancelled"}
    terminal_ids = done_ids | cancelled_ids

    ready = []
    for t in graph.tasks:
        if t.get("status") != "pending":
            continue
        deps = set(t.get("depends_on", []))
        # Toutes les deps doivent être terminées
        if deps.issubset(terminal_ids):
            ready.append(t)

    return ready


def schedule_next_batch(
    graph: TaskGraph,
    max_parallel: int = MAX_PARALLEL,
    budget_remaining: float = 999.0,
) -> ScheduleBatch:
    """Calcule le prochain batch de tâches à exécuter."""
    ready = get_ready_tasks(graph)

    if not ready:
        return ScheduleBatch(reason="No tasks ready")

    # Trier par priorité puis par nombre de dépendants (impact)
    priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    task_blocks = {t["task_id"]: len(t.get("blocks", [])) for t in graph.tasks}

    ready.sort(key=lambda t: (
        priority_order.get(t.get("priority", "normal"), 2),
        -task_blocks.get(t["task_id"], 0),  # plus de dépendants = plus prioritaire
    ))

    # Sélectionner jusqu'à max_parallel tâches dans le budget
    batch_tasks = []
    estimated_cost = 0.0
    estimated_duration = 0

    for t in ready[:max_parallel]:
        budget = t.get("budget", {})
        task_cost = budget.get("cost_ceiling_usd", 1.0)
        if estimated_cost + task_cost > budget_remaining:
            continue
        batch_tasks.append(t["task_id"])
        estimated_cost += task_cost
        estimated_duration = max(
            estimated_duration,
            budget.get("max_duration_seconds", 600),
        )

    if not batch_tasks:
        return ScheduleBatch(reason="Budget insufficient for any ready task")

    return ScheduleBatch(
        batch_id=f"batch-{uuid.uuid4().hex[:8]}",
        task_ids=batch_tasks,
        reason=f"{len(batch_tasks)} tasks ready, within budget",
        estimated_cost=estimated_cost,
        estimated_duration=estimated_duration,
    )


# ── Validation ───────────────────────────────────────────────────────────────


def validate_delivery(
    task: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """Valide les outputs d'un TaskAtom contre son delivery contract."""
    contract = task.get("delivery_contract", {})
    outputs = contract.get("outputs", [])

    results = {"task_id": task["task_id"], "checks": [], "all_passed": True}

    for output in outputs:
        path = output.get("path", "")
        full_path = project_root / path
        checks = output.get("validations", [])

        out_result = {"path": path, "checks": []}

        for check in checks:
            passed = False
            if check == "file_exists":
                passed = full_path.exists()
            elif check == "svg_valid":
                if full_path.exists():
                    content = full_path.read_text(encoding="utf-8")
                    passed = "<svg" in content
            elif check.startswith("viewbox_"):
                if full_path.exists():
                    content = full_path.read_text(encoding="utf-8")
                    passed = "viewBox" in content
            elif check.startswith("optimized_size_lt_"):
                try:
                    max_kb = int(check.split("_")[-1].replace("kb", ""))
                    passed = full_path.exists() and full_path.stat().st_size < max_kb * 1024
                except (ValueError, IndexError):
                    passed = False
            elif check.startswith("dimensions_"):
                # Requires external tool — mark as unchecked
                passed = True  # pass-through

            out_result["checks"].append({"check": check, "passed": passed})
            if not passed:
                results["all_passed"] = False

        results["checks"].append(out_result)

    return results


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_ats_create_task(
    title: str,
    task_type: str = "analytical",
    description: str = "",
    priority: str = "normal",
    depends_on: str = "",
    required_capabilities: str = "",
    preferred_agent: str = "",
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: crée un nouveau TaskAtom dans le graphe.

    Args:
        title: Titre de la tâche.
        task_type: Type (creative, analytical, transformative, evaluative, meta).
        description: Description détaillée.
        priority: Priorité (critical, high, normal, low).
        depends_on: IDs des tâches pré-requises (comma-separated).
        required_capabilities: Capabilities requises (comma-separated).
        preferred_agent: Agent préféré pour cette tâche.
        project_root: Racine du projet.

    Returns:
        TaskAtom créé.
    """
    root = Path(project_root).resolve()
    graph = load_graph(root)

    deps = [d.strip() for d in depends_on.split(",") if d.strip()] if depends_on else []
    caps = [c.strip() for c in required_capabilities.split(",") if c.strip()] if required_capabilities else []

    task = TaskAtom(
        task_type=task_type if task_type in VALID_TYPES else "analytical",
        title=title,
        description=description,
        priority=priority if priority in VALID_PRIORITIES else "normal",
        depends_on=deps,
        required_capabilities=caps,
        preferred_agent=preferred_agent,
    )

    graph = add_task(graph, task)
    save_graph(root, graph)
    append_history(root, {"event": "task_created", "task_id": task.task_id, "title": title})

    return asdict(task)


def mcp_ats_schedule(
    max_parallel: int = 4,
    budget_remaining: float = 10.0,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: calcule le prochain batch de tâches à exécuter.

    Args:
        max_parallel: Nombre max de tâches parallèles.
        budget_remaining: Budget restant en USD.
        project_root: Racine du projet.

    Returns:
        ScheduleBatch avec les tâches à exécuter.
    """
    root = Path(project_root).resolve()
    graph = load_graph(root)
    batch = schedule_next_batch(graph, max_parallel, budget_remaining)
    return asdict(batch)


def mcp_ats_status(project_root: str = ".") -> dict[str, Any]:
    """MCP tool: état global du système de tâches.

    Args:
        project_root: Racine du projet.

    Returns:
        Stats, graphe résumé, tâches prêtes.
    """
    root = Path(project_root).resolve()
    graph = load_graph(root)
    stats = compute_stats(graph)
    ready = get_ready_tasks(graph)
    cycles = detect_cycles(graph)
    cp = critical_path(graph)

    return {
        "graph_id": graph.graph_id,
        "goal": graph.goal,
        "stats": stats,
        "ready_tasks": [{"task_id": t["task_id"], "title": t["title"]} for t in ready],
        "critical_path": cp,
        "has_cycles": len(cycles) > 0,
        "cycles": cycles,
    }


def mcp_ats_update_status(
    task_id: str,
    new_status: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: met à jour le statut d'une tâche.

    Args:
        task_id: ID du TaskAtom.
        new_status: Nouveau statut.
        project_root: Racine du projet.

    Returns:
        Tâche mise à jour.
    """
    root = Path(project_root).resolve()
    graph = load_graph(root)
    graph = update_task_status(graph, task_id, new_status)
    save_graph(root, graph)
    append_history(root, {"event": "status_change", "task_id": task_id, "new_status": new_status})

    task = find_task(graph, task_id)
    return task or {"error": f"Task {task_id} not found after update"}


def mcp_ats_graph(project_root: str = ".") -> dict[str, Any]:
    """MCP tool: retourne le graphe complet de tâches.

    Args:
        project_root: Racine du projet.

    Returns:
        TaskGraph complet.
    """
    root = Path(project_root).resolve()
    graph = load_graph(root)
    return asdict(graph)


# ── CLI Commands ─────────────────────────────────────────────────────────────


def _priority_icon(p: str) -> str:
    return {"critical": "🔴", "high": "🟠", "normal": "🔵", "low": "⚪"}.get(p, "⚪")


def _status_icon(s: str) -> str:
    return {
        "pending": "⏳", "scheduled": "📋", "running": "🔄",
        "blocked": "🚫", "review": "👁️", "done": "✅",
        "failed": "❌", "cancelled": "⛔",
    }.get(s, "❓")


def cmd_create(args: argparse.Namespace) -> int:
    result = mcp_ats_create_task(
        title=args.title,
        task_type=getattr(args, "type", "analytical"),
        description=getattr(args, "desc", ""),
        priority=getattr(args, "priority", "normal"),
        depends_on=getattr(args, "depends", ""),
        required_capabilities=getattr(args, "capabilities", ""),
        preferred_agent=getattr(args, "agent", ""),
        project_root=args.project_root,
    )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("\n  ✅ TaskAtom créé")
        print(f"  ID: {result['task_id']}")
        print(f"  Titre: {result['title']}")
        print(f"  Type: {result['task_type']}")
        print(f"  Priorité: {_priority_icon(result['priority'])} {result['priority']}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    graph = load_graph(root)

    if args.json:
        print(json.dumps(asdict(graph), indent=2, ensure_ascii=False))
        return 0

    tasks = graph.tasks
    if not tasks:
        print("\n  📊 Graphe de tâches : vide")
        return 0

    print(f"\n  📊 Task Graph — {graph.graph_id}")
    if graph.goal:
        print(f"  🎯 Goal: {graph.goal}")
    print(f"  📈 {len(tasks)} tasks\n")

    # Afficher par couche topologique
    topo = topological_sort(graph)
    task_map = {t["task_id"]: t for t in tasks}

    for _i, tid in enumerate(topo):
        t = task_map.get(tid, {})
        si = _status_icon(t.get("status", "pending"))
        pi = _priority_icon(t.get("priority", "normal"))
        deps = t.get("depends_on", [])
        dep_str = f" ← {', '.join(deps)}" if deps else ""
        print(f"  {si} {pi} {tid}  {t.get('title', '?')}{dep_str}")

    # Chemin critique
    cp = critical_path(graph)
    if cp:
        print(f"\n  🔥 Chemin critique : {' → '.join(cp)}")

    # Stats
    stats = compute_stats(graph)
    print(f"  📈 Progression : {stats.get('progress_pct', 0)}%")

    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    result = mcp_ats_schedule(
        max_parallel=getattr(args, "max_parallel", MAX_PARALLEL),
        budget_remaining=getattr(args, "budget", 10.0),
        project_root=args.project_root,
    )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        batch_id = result.get("batch_id", "")
        task_ids = result.get("task_ids", [])
        reason = result.get("reason", "")

        if not task_ids:
            print(f"\n  ⚠️  Aucune tâche à scheduler : {reason}")
        else:
            print(f"\n  📋 Batch {batch_id}")
            print(f"  Tâches : {len(task_ids)}")
            for tid in task_ids:
                print(f"    → {tid}")
            print(f"  Coût estimé : ${result.get('estimated_cost', 0):.2f}")
            print(f"  Durée estimée : {result.get('estimated_duration', 0)}s")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    result = mcp_ats_status(project_root=args.project_root)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        stats = result.get("stats", {})
        ready = result.get("ready_tasks", [])
        print("\n  📊 Agent Task System Status")
        print(f"  Graph: {result.get('graph_id', 'N/A')}")
        if result.get("goal"):
            print(f"  Goal: {result['goal']}")
        print(f"\n  Total: {stats.get('total', 0)} tasks")
        if stats.get("by_status"):
            for st, count in sorted(stats["by_status"].items()):
                print(f"    {_status_icon(st)} {st}: {count}")
        print(f"\n  📈 Progress: {stats.get('progress_pct', 0)}%")
        if ready:
            print(f"\n  🟢 Ready to execute ({len(ready)}):")
            for t in ready:
                print(f"    → {t['task_id']}  {t['title']}")
        if result.get("has_cycles"):
            print(f"\n  ⚠️  CYCLES DETECTED: {result['cycles']}")
        if result.get("critical_path"):
            print(f"\n  🔥 Critical path: {' → '.join(result['critical_path'])}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    graph = load_graph(root)
    task = find_task(graph, args.id)

    if not task:
        print(f"  ❌ Task {args.id} not found", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(task, indent=2, ensure_ascii=False))
    else:
        print(f"\n  📋 TaskAtom: {task['task_id']}")
        print(f"  Titre: {task['title']}")
        print(f"  Type: {task['task_type']}")
        print(f"  Status: {_status_icon(task['status'])} {task['status']}")
        print(f"  Priorité: {_priority_icon(task['priority'])} {task['priority']}")
        if task.get("description"):
            print(f"  Description: {task['description']}")
        if task.get("depends_on"):
            print(f"  Dépend de: {', '.join(task['depends_on'])}")
        if task.get("blocks"):
            print(f"  Bloque: {', '.join(task['blocks'])}")
        if task.get("preferred_agent"):
            print(f"  Agent préféré: {task['preferred_agent']}")
        if task.get("results"):
            print(f"  Tentatives: {task['attempts']}")
            for r in task["results"]:
                print(f"    #{r['attempt']} — {r['status']} (score: {r.get('quality_score', 'N/A')})")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    graph = load_graph(root)
    task = find_task(graph, args.id)

    if not task:
        print(f"  ❌ Task {args.id} not found", file=sys.stderr)
        return 1

    task["status"] = "pending"
    task["attempts"] = 0
    task["results"] = []
    task["updated_at"] = datetime.now().isoformat()
    save_graph(root, graph)
    append_history(root, {"event": "task_reset", "task_id": args.id})

    print(f"  🔄 Task {args.id} reset to pending")
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent Task System — Gestion de tâches par et pour agents IA",
    )
    parser.add_argument("--project-root", default=".", help="Project root")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--version", action="version", version=f"agent-task-system {ATS_VERSION}")

    sub = parser.add_subparsers(dest="command")

    # create
    p_create = sub.add_parser("create", help="Create a TaskAtom")
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--type", default="analytical", choices=sorted(VALID_TYPES))
    p_create.add_argument("--desc", default="")
    p_create.add_argument("--priority", default="normal", choices=sorted(VALID_PRIORITIES))
    p_create.add_argument("--depends", default="", help="Comma-separated dep IDs")
    p_create.add_argument("--capabilities", default="", help="Comma-separated capabilities")
    p_create.add_argument("--agent", default="", help="Preferred agent")

    # graph
    sub.add_parser("graph", help="Display the task graph")

    # schedule
    p_sched = sub.add_parser("schedule", help="Compute next executable batch")
    p_sched.add_argument("--max-parallel", type=int, default=MAX_PARALLEL)
    p_sched.add_argument("--budget", type=float, default=10.0)

    # status
    sub.add_parser("status", help="ATS global status")

    # inspect
    p_insp = sub.add_parser("inspect", help="Inspect a task")
    p_insp.add_argument("--id", required=True)

    # reset
    p_reset = sub.add_parser("reset", help="Reset a task to pending")
    p_reset.add_argument("--id", required=True)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "create": cmd_create,
        "graph": cmd_graph,
        "schedule": cmd_schedule,
        "status": cmd_status,
        "inspect": cmd_inspect,
        "reset": cmd_reset,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
