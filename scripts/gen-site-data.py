#!/usr/bin/env python3
"""Generate the web/ data layer from the real project.

Writes JSON consumed by the static site (web/data/*.json):
  - meta.json          real metrics (version, counts, links)
  - architecture.json  layered architecture map (source of truth for anatomy)

The site reads these via fetch. For the vitrine they are committed snapshots;
the local "view mode" regenerates them against the current project.

Usage:
    python scripts/gen-site-data.py [--root .] [--with-tests]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _num(s: object) -> float | int:
    """Extrait le premier nombre d'une chaîne (entier ou flottant)."""
    m = re.search(r"-?\d+(?:\.\d+)?", str(s))
    if not m:
        return 0
    g = m.group()
    return float(g) if "." in g else int(g)


def _parse_iso(s: str):
    """Parse un timestamp ISO (le suffixe Z est géré par fromisoformat ≥ 3.11). None si invalide."""
    try:
        return _dt.datetime.fromisoformat(str(s))
    except (ValueError, AttributeError):
        return None


def _days_since(s: str) -> int | None:
    d = _parse_iso(s)
    if not d:
        return None
    now = _dt.datetime.now(_dt.UTC)
    return (now - d).days if d.tzinfo else (now.replace(tzinfo=None) - d).days

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _read_version(root: Path) -> str:
    txt = (root / "src/grimoire/__version__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', txt)
    return m.group(1) if m else "unknown"


def _count_tools(root: Path) -> int:
    return len(list((root / "framework/tools").glob("*.py")))


def _count_agents(root: Path) -> int:
    stems: set[str] = set()
    for p in root.glob("archetypes/*/agents/*.md"):
        stems.add(p.stem)
    for p in (root / "_grimoire/_config/custom/agents").glob("*.md"):
        if not p.stem.endswith(".tpl"):
            stems.add(p.stem)
    return len(stems)


def _list_archetypes(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "archetypes").iterdir() if p.is_dir())


def _agents_by_archetype(root: Path) -> dict[str, list[str]]:
    """Inventaire réel des agents (lesquels), groupés par archétype."""
    out: dict[str, list[str]] = {}
    for p in sorted(root.glob("archetypes/*/agents/*.md")):
        out.setdefault(p.parent.parent.name, []).append(p.stem)
    return out


def _standard_counts(root: Path) -> dict:
    out = {"patterns": None, "pattern_categories": None, "profiles": None, "artifact_types": None}
    if yaml is None:
        return out
    cap_path = root / "framework/agentic-standard/capability-map.yaml"
    pro_path = root / "framework/agentic-standard/profile-map.yaml"
    if cap_path.is_file():
        cap = yaml.safe_load(cap_path.read_text(encoding="utf-8")) or {}
        patterns = cap.get("patterns") or {}
        out["patterns"] = len(patterns)
        cats = {
            (v.get("category") or v.get("dimension"))
            for v in patterns.values()
            if isinstance(v, dict) and (v.get("category") or v.get("dimension"))
        }
        out["pattern_categories"] = len(cats) or None
    if pro_path.is_file():
        pro = yaml.safe_load(pro_path.read_text(encoding="utf-8")) or {}
        out["profiles"] = len(pro.get("profiles") or {})
        out["artifact_types"] = len(pro.get("artifact_types") or {})
    return out


def _count_tests(root: Path, run_pytest: bool) -> int | None:
    if run_pytest:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q"],
                cwd=root, capture_output=True, text=True, timeout=300,
            )
            m = re.search(r"(\d+)\s+tests?\s+collected", r.stdout + r.stderr)
            if m:
                return int(m.group(1))
            n = len([ln for ln in r.stdout.splitlines() if "::" in ln])
            if n:
                return n
        except Exception:  # noqa: S110 — best-effort; fall back to a static count
            pass
    # Fallback: count test functions (floor — excludes parametrize expansion)
    n = 0
    for p in root.glob("tests/**/*.py"):
        n += len(re.findall(r"^\s*(?:async\s+)?def test_", p.read_text(encoding="utf-8"), re.MULTILINE))
    return n or None


def build_meta(root: Path, with_tests: bool) -> dict:
    std = _standard_counts(root)
    return {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "version": _read_version(root),
        "counts": {
            "tools": _count_tools(root),
            "agents": _count_agents(root),
            "archetypes": len(_list_archetypes(root)),
            "patterns": std["patterns"],
            "pattern_categories": std["pattern_categories"],
            "profiles": std["profiles"],
            "artifact_types": std["artifact_types"],
            "tests": _count_tests(root, with_tests),
        },
        "archetypes": _list_archetypes(root),
        "profiles": ["starter", "controlled", "orchestrated", "governed", "production"],
        "inventory": {
            "agents_by_archetype": _agents_by_archetype(root),
        },
        "links": {
            "github": "https://github.com/Guilhem-Bonnet/Grimoire-kit",
            "pypi": "https://pypi.org/project/grimoire-kit/",
            "docs": "https://guilhem-bonnet.github.io/Grimoire-kit/",
            "license": "MIT",
        },
    }


# Representative demo cards for the vitrine board (only used when no real
# task-board.yaml exists). Local "view mode" replaces these with the real board.
_DEMO_TASKS = [
    {"task_id": "kb-index", "title": "Indexer la base de connaissances externe", "status": "proposed",
     "priority": "medium", "owner": "project-maintainer", "agent_roles": ["planner", "context_orchestrator"],
     "blockers": []},
    {"task_id": "verify-failclosed", "title": "Verify fail-closed sur capability-map", "status": "ready",
     "priority": "high", "owner": "qa", "agent_roles": ["qa", "planner"],
     "acceptance_criteria": ["verify retourne non-zéro si un pattern requis manque"], "blockers": []},
    {"task_id": "mcp-bridge", "title": "Bridge MCP pour Claude Desktop", "status": "ready",
     "priority": "medium", "owner": "dev", "agent_roles": ["dev"], "blockers": []},
    {"task_id": "mem-weaviate", "title": "Migration mémoire Weaviate (depuis qdrant)", "status": "in_progress",
     "priority": "high", "owner": "dev", "agent_roles": ["dev", "memory_keeper"],
     "evidence_pack_ref": "_grimoire-output/evidence/mem-weaviate/evidence-pack.md", "blockers": []},
    {"task_id": "obs-graph", "title": "Observatory — export du graphe d'agents", "status": "in_progress",
     "priority": "medium", "owner": "dev", "agent_roles": ["dev"], "blockers": []},
    {"task_id": "release-gates", "title": "Release gates v3.17", "status": "blocked",
     "priority": "high", "owner": "dev", "agent_roles": ["dev", "qa"],
     "blockers": ["compliance_score sous le seuil governed"],
     "remediation_ref": "_grimoire/standard/remediation-plan.yaml"},
    {"task_id": "adv-review-cli", "title": "Revue adversariale du fix CLI (marqueurs [OK])", "status": "review",
     "priority": "medium", "owner": "challenger", "agent_roles": ["challenger", "qa"],
     "evidence_pack_ref": "_grimoire-output/evidence/cli-markers/evidence-pack.md",
     "decision_trace_ref": "_grimoire-output/decisions/cli-markers/decision-trace.yaml", "blockers": []},
    {"task_id": "governed-policies", "title": "Profil governed — politiques par environnement", "status": "accepted",
     "priority": "medium", "owner": "planner", "agent_roles": ["planner"], "blockers": []},
    {"task_id": "rel-3160", "title": "v3.16 — purge BMAD + standard agentique gouverné", "status": "released",
     "priority": "high", "owner": "project-maintainer", "agent_roles": ["dev", "qa"], "blockers": []},
    {"task_id": "scaffold-v2v3", "title": "Scaffold migration v2 → v3", "status": "archived",
     "priority": "low", "owner": "dev", "agent_roles": ["dev"], "blockers": []},
]


def build_taskboard(root: Path) -> dict | None:
    """Governed agentic kanban — from the project's task-board.yaml if present,
    else the standard template (flagged is_demo for the vitrine)."""
    if yaml is None:
        return None
    candidates = [
        root / "_grimoire/standard/task-board.yaml",
        root / "_grimoire/_config/standard/task-board.yaml",
        *sorted(root.glob("_grimoire-output/**/task-board.yaml")),
        root / "framework/agentic-standard/templates/task-board.yaml",  # fallback = template/demo
    ]
    src = next((c for c in candidates if c.is_file()), None)
    if src is None:
        return None
    data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    is_demo = "templates/" in src.as_posix()
    tasks = data.get("tasks", [])
    if is_demo:  # populate the showcase board (the template ships only a bootstrap task)
        tasks = tasks + _DEMO_TASKS
    return {
        "source": str(src.relative_to(root)),
        "is_demo": is_demo,
        "states": data.get("states", []),
        "transitions": data.get("transitions", {}),
        "tasks": tasks,
    }


# Representative runtime snapshot for the vitrine (real grimoire agents + event types).
# Local "view mode" replaces this with the live `observatory.py export`.
_DEMO_OBSERVATORY = {
    "agents": [
        {"id": "atlas", "persona": "Project Navigator", "capabilities": ["navigation", "coordination"], "metrics": {"traces": 9}},
        {"id": "mnemo", "persona": "Memory Keeper", "capabilities": ["memory", "contradiction-detection"], "metrics": {"traces": 6}},
        {"id": "sentinel", "persona": "Agent Optimizer", "capabilities": ["quality", "self-healing"], "metrics": {"traces": 4}},
        {"id": "dev", "persona": "Amelia", "capabilities": ["implement", "refactor", "test"], "metrics": {"traces": 12}},
        {"id": "qa", "persona": "Quinn", "capabilities": ["test", "review"], "metrics": {"traces": 7}},
        {"id": "pm", "persona": "John", "capabilities": ["spec", "prd"], "metrics": {"traces": 5}},
        {"id": "tech-writer", "persona": "Paige", "capabilities": ["docs"], "metrics": {"traces": 3}},
    ],
    "relationships": [
        {"from_agent": "atlas", "to_agent": "dev", "type": "handoff", "strength": 0.9, "interactions": 8, "avg_trust": 0.92},
        {"from_agent": "dev", "to_agent": "qa", "type": "handoff", "strength": 0.85, "interactions": 6, "avg_trust": 0.88},
        {"from_agent": "pm", "to_agent": "dev", "type": "spec", "strength": 0.7, "interactions": 4, "avg_trust": 0.8},
        {"from_agent": "dev", "to_agent": "mnemo", "type": "memory", "strength": 0.6, "interactions": 5, "avg_trust": 0.9},
        {"from_agent": "qa", "to_agent": "sentinel", "type": "escalation", "strength": 0.5, "interactions": 2, "avg_trust": 0.75},
        {"from_agent": "dev", "to_agent": "tech-writer", "type": "handoff", "strength": 0.5, "interactions": 3, "avg_trust": 0.82},
    ],
    "event_types": ["ACTION", "DECISION", "HANDOFF", "CHECKPOINT", "REMEMBER", "WARN", "ERROR"],
    "sessions": ["main", "work/standard-bridge", "work/memory-runtime"],
}
_DEMO_TRACES = [
    ("dev", "ACTION", "main"), ("dev", "DECISION", "main"), ("qa", "ACTION", "main"),
    ("dev", "HANDOFF", "main"), ("mnemo", "REMEMBER", "main"), ("atlas", "CHECKPOINT", "main"),
    ("pm", "ACTION", "work/standard-bridge"), ("dev", "ACTION", "work/standard-bridge"),
    ("qa", "WARN", "work/standard-bridge"), ("sentinel", "DECISION", "work/standard-bridge"),
    ("dev", "ACTION", "work/memory-runtime"), ("mnemo", "REMEMBER", "work/memory-runtime"),
    ("dev", "HANDOFF", "work/memory-runtime"), ("tech-writer", "ACTION", "work/memory-runtime"),
    ("qa", "ACTION", "work/memory-runtime"), ("atlas", "CHECKPOINT", "work/memory-runtime"),
    ("dev", "ERROR", "work/memory-runtime"), ("dev", "DECISION", "work/memory-runtime"),
]

# Spans causaux représentatifs (3 arbres parent→enfant) — surfacés sur la page
# observability. Le mode vue local remplace par `synapse-trace export`.
_COST_PER_1K = 0.003  # USD / 1k tokens (estimation indicative, cf. synapse-trace.py)
_DEMO_SPANS = [
    # trace « main » — orchestration d'une action dev
    {"span_id": "a1", "parent_span_id": "", "trace_id": "a1", "tool": "orchestrator", "operation": "execute", "agent": "dev", "duration_ms": 4200, "tokens": 1800, "retries": 0, "status": "ok"},
    {"span_id": "a2", "parent_span_id": "a1", "trace_id": "a1", "tool": "router", "operation": "classify", "agent": "dev", "duration_ms": 180, "tokens": 240, "retries": 0, "status": "ok"},
    {"span_id": "a3", "parent_span_id": "a1", "trace_id": "a1", "tool": "memory", "operation": "recall", "agent": "mnemo", "duration_ms": 320, "tokens": 90, "retries": 0, "status": "ok"},
    {"span_id": "a4", "parent_span_id": "a1", "trace_id": "a1", "tool": "llm", "operation": "generate", "agent": "dev", "duration_ms": 3100, "input_tokens": 1400, "output_tokens": 2800, "model": "claude-opus-4-8", "provider": "anthropic", "retries": 0, "status": "ok"},
    {"span_id": "a5", "parent_span_id": "a1", "trace_id": "a1", "tool": "verify", "operation": "check", "agent": "qa", "duration_ms": 260, "tokens": 180, "retries": 0, "status": "ok"},
    # trace « standard-bridge » — gate de vérification
    {"span_id": "b1", "parent_span_id": "", "trace_id": "b1", "tool": "verify", "operation": "gate", "agent": "qa", "duration_ms": 1500, "tokens": 600, "retries": 0, "status": "ok"},
    {"span_id": "b2", "parent_span_id": "b1", "trace_id": "b1", "tool": "capability", "operation": "scan", "agent": "qa", "duration_ms": 240, "tokens": 150, "retries": 0, "status": "ok"},
    {"span_id": "b3", "parent_span_id": "b1", "trace_id": "b1", "tool": "llm", "operation": "review", "agent": "qa", "duration_ms": 1100, "input_tokens": 2000, "output_tokens": 800, "model": "gpt-5.3-codex", "provider": "openai", "retries": 1, "status": "ok"},
    # trace « memory-runtime » — migration mémoire (avec un échec gated)
    {"span_id": "c1", "parent_span_id": "", "trace_id": "c1", "tool": "migrate", "operation": "run", "agent": "dev", "duration_ms": 5200, "tokens": 900, "retries": 0, "status": "ok"},
    {"span_id": "c2", "parent_span_id": "c1", "trace_id": "c1", "tool": "weaviate", "operation": "write", "agent": "dev", "duration_ms": 1800, "tokens": 300, "retries": 0, "status": "ok"},
    {"span_id": "c3", "parent_span_id": "c1", "trace_id": "c1", "tool": "llm", "operation": "embed", "agent": "mnemo", "duration_ms": 2600, "input_tokens": 5200, "output_tokens": 0, "model": "gemini-3-pro", "provider": "google", "retries": 0, "status": "ok"},
    {"span_id": "c4", "parent_span_id": "c1", "trace_id": "c1", "tool": "verify", "operation": "failclosed", "agent": "qa", "duration_ms": 140, "tokens": 120, "retries": 0, "status": "error"},
]


# Prix indicatif par modèle (USD/1k) : (input, output) — miroir de synapse-trace.py.
_MODEL_PRICING = {
    "claude-opus": (0.015, 0.075), "claude-sonnet": (0.003, 0.015), "claude-haiku": (0.0008, 0.004),
    "gpt-5": (0.005, 0.015), "gpt-": (0.005, 0.015), "o3": (0.01, 0.04),
    "gemini": (0.00125, 0.005), "qwen": (0.0, 0.0),
}


def _est_cost(tokens: int) -> float:
    return round(max(0, int(tokens or 0)) / 1000.0 * _COST_PER_1K, 6)


def _cost_io(input_tokens: int, output_tokens: int, model: str) -> float:
    """Coût par modèle (in/out) si connu, sinon taux plat sur le total."""
    m = (model or "").lower()
    best = max((p for p in _MODEL_PRICING if m.startswith(p)), key=len, default="")
    if best:
        in_r, out_r = _MODEL_PRICING[best]
        return round(int(input_tokens or 0) / 1000.0 * in_r + int(output_tokens or 0) / 1000.0 * out_r, 6)
    return _est_cost(int(input_tokens or 0) + int(output_tokens or 0))


def _norm_span(e: dict) -> dict:
    """Normalise une entrée (synapse export ou démo) au schéma de la page."""
    in_tok = e.get("input_tokens", 0) or 0
    out_tok = e.get("output_tokens", 0) or 0
    tokens = e.get("tokens", e.get("tokens_estimated", 0)) or (in_tok + out_tok)
    return {
        "span_id": e.get("span_id", ""),
        "parent_span_id": e.get("parent_span_id", ""),
        "trace_id": e.get("trace_id", "") or e.get("span_id", ""),
        "tool": e.get("tool", ""),
        "operation": e.get("operation", ""),
        "agent": e.get("agent", ""),
        "duration_ms": e.get("duration_ms", 0) or 0,
        "tokens": tokens,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "model": e.get("model", ""),
        "provider": e.get("provider", ""),
        "cost_usd": e.get("cost_usd", 0) or 0,
        "retries": e.get("retries", 0) or 0,
        "status": e.get("status", "ok") or "ok",
        "timestamp": e.get("timestamp", ""),
    }


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 1)


def _compute_metrics(traces: list[dict], spans: list[dict], rels: list[dict]) -> dict:
    """Agrège les métriques runtime (coût, latence, throughput, trust) pour la page."""
    import datetime as _d
    durs = [sp.get("duration_ms", 0) for sp in spans]
    tokens = sum(sp.get("tokens", 0) for sp in spans)
    cost = round(sum(sp.get("cost_usd", 0) for sp in spans), 6)
    roots = {sp["trace_id"] for sp in spans if not sp.get("parent_span_id")}
    errors = sum(1 for sp in spans if sp.get("status") not in ("ok", "", None))
    tput = 0.0
    ts = [t.get("timestamp", "") for t in traces if t.get("timestamp")]
    if len(ts) >= 2:
        try:
            parsed = [_d.datetime.fromisoformat(x) for x in ts]
            mins = max((max(parsed) - min(parsed)).total_seconds() / 60.0, 1e-9)
            tput = round(len(traces) / mins, 2)
        except ValueError:
            tput = 0.0
    trusts = [r.get("avg_trust", 0) for r in rels if r.get("avg_trust")]
    n_spans = len(spans)
    n_traces = len(roots)
    retries = sum(sp.get("retries", 0) for sp in spans)
    in_tok = sum(sp.get("input_tokens", 0) for sp in spans)
    out_tok = sum(sp.get("output_tokens", 0) for sp in spans)
    by_model: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    cost_by_model: dict[str, float] = {}
    for sp in spans:
        mdl = sp.get("model")
        if mdl:
            by_model[mdl] = by_model.get(mdl, 0) + 1
            cost_by_model[mdl] = round(cost_by_model.get(mdl, 0.0) + sp.get("cost_usd", 0), 6)
        prov = sp.get("provider")
        if prov:
            by_provider[prov] = by_provider.get(prov, 0) + 1
    return {
        "total_tokens": tokens,
        "total_cost_usd": cost,
        "total_duration_ms": round(sum(durs), 1),
        "span_count": n_spans,
        "trace_count": n_traces,
        "p50_latency_ms": _percentile(durs, 50),
        "p95_latency_ms": _percentile(durs, 95),
        "p99_latency_ms": _percentile(durs, 99),
        "throughput_per_min": tput,
        "error_rate": round(errors / n_spans, 3) if n_spans else 0.0,
        "avg_trust": round(sum(trusts) / len(trusts), 3) if trusts else 0.0,
        # moyennes
        "avg_cost_per_trace": round(cost / n_traces, 6) if n_traces else 0.0,
        "avg_tokens_per_span": round(tokens / n_spans) if n_spans else 0,
        "avg_duration_ms": round(sum(durs) / n_spans, 1) if n_spans else 0.0,
        "avg_spans_per_trace": round(n_spans / n_traces, 1) if n_traces else 0.0,
        "retry_rate": round(retries / n_spans, 3) if n_spans else 0.0,
        # multi-LLM
        "total_input_tokens": in_tok,
        "total_output_tokens": out_tok,
        "by_model": by_model,
        "by_provider": by_provider,
        "cost_by_model": cost_by_model,
    }


def _perf_metrics(spans: list[dict]) -> dict:
    """Performances dérivées des spans : latences p50/p95/p99, par outil/agent/modèle, outliers."""
    durs = [sp.get("duration_ms", 0) for sp in spans]

    def agg(key: str) -> dict:
        groups: dict[str, dict] = {}
        for sp in spans:
            k = sp.get(key)
            if not k:
                continue
            e = groups.setdefault(k, {"count": 0, "total_ms": 0.0, "_durs": []})
            e["count"] += 1
            e["total_ms"] += sp.get("duration_ms", 0)
            e["_durs"].append(sp.get("duration_ms", 0))
        out = {}
        for k, e in groups.items():
            out[k] = {
                "count": e["count"],
                "total_ms": round(e["total_ms"], 1),
                "avg_ms": round(e["total_ms"] / e["count"], 1) if e["count"] else 0.0,
                "p50_ms": _percentile(e["_durs"], 50),
                "p95_ms": _percentile(e["_durs"], 95),
            }
        return out

    slowest = sorted(spans, key=lambda s: s.get("duration_ms", 0), reverse=True)[:6]
    return {
        "p50_ms": _percentile(durs, 50),
        "p95_ms": _percentile(durs, 95),
        "p99_ms": _percentile(durs, 99),
        "avg_ms": round(sum(durs) / len(durs), 1) if durs else 0.0,
        "by_tool": agg("tool"),
        "by_agent": agg("agent"),
        "by_model": agg("model"),
        "slowest": [{
            "label": f'{s.get("tool", "")}.{s.get("operation", "")}',
            "agent": s.get("agent", ""),
            "model": s.get("model", ""),
            "duration_ms": s.get("duration_ms", 0),
            "cost_usd": s.get("cost_usd", 0),
            "status": s.get("status", "ok"),
        } for s in slowest],
    }


def _graph_stats(rels: list[dict], agent_ids: list[str]) -> dict:
    """Métriques du graphe d'agents : densité, degré moyen, agent le plus central."""
    if not rels:
        return {}
    degree: dict[str, int] = {}
    for r in rels:
        for a in (r.get("from_agent"), r.get("to_agent")):
            if a:
                degree[a] = degree.get(a, 0) + 1
    nodes = len(set(agent_ids) | set(degree))
    edges = len(rels)
    most = max(degree.items(), key=lambda x: x[1]) if degree else ("", 0)
    return {
        "nodes": nodes,
        "edges": edges,
        "avg_degree": round(sum(degree.values()) / nodes, 2) if nodes else 0.0,
        "density": round(edges / (nodes * (nodes - 1) / 2), 3) if nodes > 1 else 0.0,
        "most_central": {"agent": most[0], "degree": most[1]},
    }


def _live_spans(root: Path) -> list[dict]:
    """Spans réels via `synapse-trace.py export` (best-effort)."""
    syn = root / "framework/tools/synapse-trace.py"
    if not syn.is_file():
        return []
    try:
        r = subprocess.run(
            [sys.executable, str(syn), "--project-root", str(root), "export", "--format", "json"],
            cwd=root, capture_output=True, text=True, timeout=60,
        )
        raw = json.loads(r.stdout) if r.stdout.strip().startswith("[") else []
        return [_norm_span(e) for e in raw]
    except Exception:
        return []


def build_observatory(root: Path) -> dict | None:
    """Runtime data for observability/game-ui — from `observatory.py export`
    if the project has runtime data, else a representative demo snapshot."""
    obs_tool = root / "framework/tools/observatory.py"
    data = None
    if obs_tool.is_file():
        try:
            r = subprocess.run(
                [sys.executable, str(obs_tool), "--project-root", str(root), "export"],
                cwd=root, capture_output=True, text=True, timeout=60,
            )
            data = json.loads(r.stdout) if r.stdout.strip().startswith("{") else None
        except Exception:  # best-effort export; fall back to the demo snapshot
            data = None

    import datetime as _d
    has_runtime = bool(data and data.get("traces"))
    if has_runtime:
        out = data
        out["is_demo"] = False
        out["spans"] = _live_spans(root)
    else:
        base = _d.datetime.now(_d.UTC)
        traces = [
            {"timestamp": (base - _d.timedelta(minutes=2 * (len(_DEMO_TRACES) - i))).isoformat(timespec="seconds"),
             "agent": a, "event_type": t, "session": s, "payload": ""}
            for i, (a, t, s) in enumerate(_DEMO_TRACES)
        ]
        # Spans démo : un horodatage par arbre, coût dérivé des tokens.
        _trace_age = {"a": 6, "b": 4, "c": 2}  # minutes avant `base`, par trace
        spans = []
        for sp in _DEMO_SPANS:
            sp = _norm_span(sp)
            sp["cost_usd"] = (_cost_io(sp["input_tokens"], sp["output_tokens"], sp["model"])
                              if sp["model"] else _est_cost(sp["tokens"]))
            mins = _trace_age.get(sp["trace_id"][:1], 1)
            sp["timestamp"] = (base - _d.timedelta(minutes=mins)).isoformat(timespec="seconds")
            spans.append(sp)
        out = {
            "is_demo": True,
            "traces": traces,
            "events": [],
            "agents": _DEMO_OBSERVATORY["agents"],
            "relationships": _DEMO_OBSERVATORY["relationships"],
            "shared_state": {},
            "sessions": _DEMO_OBSERVATORY["sessions"],
            "agent_ids": [a["id"] for a in _DEMO_OBSERVATORY["agents"]],
            "event_types": _DEMO_OBSERVATORY["event_types"],
            "spans": spans,
        }
    out["metrics"] = _compute_metrics(out.get("traces", []), out.get("spans", []), out.get("relationships", []))
    out["perf"] = _perf_metrics(out.get("spans", []))
    out["graph_stats"] = _graph_stats(out.get("relationships", []), out.get("agent_ids", []))
    return out


# ── Project activity (git + GitHub, best-effort, données 100% réelles) ───────

def _run(args: list[str], root: Path, timeout: int = 25, *, check: bool = True) -> str:
    """Exécute une commande (best-effort). check=False : renvoie stdout même si exit≠0
    (certains outils sortent non-zéro sur un seuil tout en émettant un JSON valide)."""
    try:
        r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=timeout)
        return r.stdout if (not check or r.returncode == 0) else ""
    except Exception:
        return ""


def _git_activity(root: Path) -> dict:
    out: dict = {}
    total = _run(["git", "rev-list", "--count", "HEAD"], root).strip()
    out["commits_total"] = int(total) if total.isdigit() else 0

    raw = _run(["git", "log", "--since=30 days ago", "--date=short", "--pretty=%ad"], root)
    counts: dict[str, int] = {}
    for line in raw.splitlines():
        d = line.strip()
        if d:
            counts[d] = counts.get(d, 0) + 1
    today = _dt.date.today()
    per_day = [{"date": (today - _dt.timedelta(days=i)).isoformat(),
                "count": counts.get((today - _dt.timedelta(days=i)).isoformat(), 0)}
               for i in range(29, -1, -1)]
    out["per_day"] = per_day
    out["commits_30d"] = sum(c["count"] for c in per_day)
    out["commits_7d"] = sum(c["count"] for c in per_day[-7:])
    out["avg_per_day_30d"] = round(out["commits_30d"] / 30.0, 2)

    contributors = []
    for line in _run(["git", "shortlog", "-sne", "HEAD"], root).splitlines():
        m = re.match(r"\s*(\d+)\s+(.+?)\s+<", line)
        if m:
            contributors.append({"name": m.group(2), "commits": int(m.group(1))})
    out["contributor_count"] = len(contributors)
    out["contributors"] = contributors[:6]

    last = _run(["git", "log", "-1", "--pretty=%h|%s|%aI|%an"], root).strip()
    if "|" in last:
        parts = [*last.split("|", 3), "", "", ""][:4]
        out["last_commit"] = {"sha": parts[0], "message": parts[1], "date": parts[2], "author": parts[3]}
    return out


def _gh_json(args: list[str], root: Path):
    raw = _run(["gh", *args], root, timeout=25)
    try:
        return json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        return None


def _github(root: Path) -> dict:
    out: dict = {"pulls": [], "repo": {}, "pulls_open": 0}
    prs = _gh_json(["pr", "list", "--state", "all", "--limit", "8",
                    "--json", "number,title,url,state,author,createdAt"], root)
    if isinstance(prs, list):
        out["pulls"] = [{
            "number": p.get("number"),
            "title": p.get("title", ""),
            "url": p.get("url", ""),
            "state": (p.get("state") or "").lower(),
            "author": (p.get("author") or {}).get("login", ""),
            "created_at": p.get("createdAt", ""),
        } for p in prs]
    open_prs = _gh_json(["pr", "list", "--state", "open", "--json", "number"], root)
    out["pulls_open"] = len(open_prs) if isinstance(open_prs, list) else 0
    repo = _gh_json(["repo", "view", "--json", "stargazerCount,forkCount,url,nameWithOwner"], root)
    if isinstance(repo, dict):
        out["repo"] = {
            "stars": repo.get("stargazerCount", 0),
            "forks": repo.get("forkCount", 0),
            "url": repo.get("url", ""),
            "name": repo.get("nameWithOwner", ""),
        }
    return out


def _git_releases(root: Path) -> list[dict]:
    raw = _run(["git", "for-each-ref", "--sort=-creatordate", "--count=6",
                "--format=%(refname:short)|%(creatordate:short)", "refs/tags"], root)
    rels = []
    for line in raw.splitlines():
        if "|" in line:
            tag, date = line.split("|", 1)
            rels.append({"tag": tag.strip(), "date": date.strip()})
    return rels


def _context_pressure(root: Path) -> dict:
    """Pression de la fenêtre de contexte (réelle) depuis token-usage.jsonl.

    NB : ce log contient des SNAPSHOTS d'occupation de contexte (used/window/pct),
    PAS de la consommation par appel — on en tire le pic et la moyenne d'occupation.
    La vraie consommation de tokens vient désormais des spans Synapse (in/out par modèle).
    """
    path = root / "_grimoire/_memory/token-usage.jsonl"
    if not path.is_file():
        return {}
    by_day: dict[str, list[float]] = {}
    models: dict[str, int] = {}
    window = 0
    rows = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        pct = float(r.get("pct") or 0)
        day = (r.get("ts") or "")[:10]
        if day:
            by_day.setdefault(day, []).append(pct)
        window = max(window, int(r.get("window") or 0))
        model = (r.get("model") or "").split("-2025")[0] or "?"
        models[model] = models.get(model, 0) + 1
        rows += 1
    all_pct = [p for v in by_day.values() for p in v]
    return {
        "samples": rows,
        "active_days": len(by_day),
        "window": window,
        "peak_pct": round(max(all_pct), 4) if all_pct else 0.0,
        "avg_pct": round(sum(all_pct) / len(all_pct), 4) if all_pct else 0.0,
        "models": models,
        "by_day": [{
            "date": d,
            "peak_pct": round(max(by_day[d]), 4),
            "avg_pct": round(sum(by_day[d]) / len(by_day[d]), 4),
            "samples": len(by_day[d]),
        } for d in sorted(by_day)],
    }


def _rtk_economy(root: Path) -> dict:
    """Économie de tokens via `rtk gain --format json` (réel, best-effort)."""
    raw = _run(["rtk", "gain", "--format", "json", "--all"], root, timeout=30)
    try:
        d = json.loads(raw) if raw.strip().startswith("{") else {}
    except json.JSONDecodeError:
        return {}
    s = d.get("summary") or {}
    if not s:
        return {}
    return {
        "total_commands": s.get("total_commands", 0),
        "input_tokens": s.get("total_input", 0),
        "output_tokens": s.get("total_output", 0),
        "saved_tokens": s.get("total_saved", 0),
        "savings_pct": round(s.get("avg_savings_pct", 0), 1),
        "total_time_ms": s.get("total_time_ms", 0),
        "monthly": [{
            "month": m.get("month"),
            "commands": m.get("commands", 0),
            "saved_tokens": m.get("saved_tokens", 0),
            "savings_pct": round(m.get("savings_pct", 0), 1),
        } for m in (d.get("monthly") or [])],
        "weekly": [{
            "week": w.get("week"),
            "saved_tokens": w.get("saved_tokens", 0),
            "savings_pct": round(w.get("savings_pct", 0), 1),
        } for w in (d.get("weekly") or [])],
        "daily": [{
            "date": x.get("day") or x.get("date"),
            "saved_tokens": x.get("saved_tokens", 0),
            "savings_pct": round(x.get("savings_pct", 0), 1),
        } for x in (d.get("daily") or [])][-30:],
    }


def _ccusage(root: Path) -> dict:
    """Coût/usage Claude réel via `ccusage` — OPT-IN (données personnelles globales).

    Désactivé par défaut : ccusage agrège la dépense IA de TOUS les projets de
    l'utilisateur ; on ne la publie pas sur la vitrine publique sans opt-in explicite
    (GRIMOIRE_SITE_INCLUDE_CCUSAGE=1, typiquement pour le mode vue local).
    """
    if os.environ.get("GRIMOIRE_SITE_INCLUDE_CCUSAGE") != "1":
        return {}
    raw = _run(["ccusage", "daily", "--json"], root, timeout=60)
    try:
        d = json.loads(raw) if raw.strip().startswith("{") else {}
    except json.JSONDecodeError:
        return {}
    days = d.get("daily") or []
    if not days:
        return {}
    by_model: dict[str, dict] = {}
    total_cost = 0.0
    cache_read = cache_create = input_tok = 0
    series = []
    for day in days:
        total_cost += day.get("totalCost", 0)
        cache_read += day.get("cacheReadTokens", 0)
        cache_create += day.get("cacheCreationTokens", 0)
        input_tok += day.get("inputTokens", 0)
        series.append({
            "date": day.get("period"),
            "cost": round(day.get("totalCost", 0), 4),
            "input": day.get("inputTokens", 0),
            "output": day.get("outputTokens", 0),
            "total": day.get("totalTokens", 0),
        })
        for mb in day.get("modelBreakdowns") or []:
            e = by_model.setdefault(mb.get("modelName", "?"), {"cost": 0.0, "input": 0, "output": 0})
            e["cost"] += mb.get("cost", 0)
            e["input"] += mb.get("inputTokens", 0)
            e["output"] += mb.get("outputTokens", 0)
    for e in by_model.values():
        e["cost"] = round(e["cost"], 4)
    cache_denom = input_tok + cache_read
    return {
        "total_cost": round(total_cost, 2),
        "by_model": by_model,
        "models_used": sorted(by_model),
        "days": series[-60:],
        "cache": {
            "read_tokens": cache_read,
            "creation_tokens": cache_create,
            "hit_ratio": round(cache_read / cache_denom, 4) if cache_denom else 0.0,
        },
    }


def build_economy(root: Path) -> dict:
    """Économie & coût réels : RTK (publiable) + ccusage (opt-in, personnel)."""
    return {"rtk": _rtk_economy(root), "ccusage": _ccusage(root)}


def _ci_runs(root: Path) -> list[dict]:
    """Derniers runs CI (réels, via gh)."""
    data = _gh_json(["run", "list", "--limit", "6", "--json",
                     "name,status,conclusion,createdAt,url,headBranch,event"], root)
    if not isinstance(data, list):
        return []
    return [{
        "name": r.get("name", ""),
        "status": r.get("status", ""),
        "conclusion": r.get("conclusion", ""),
        "event": r.get("event", ""),
        "branch": r.get("headBranch", ""),
        "created_at": r.get("createdAt", ""),
        "url": r.get("url", ""),
    } for r in data]


def _ci_status(runs: list[dict]) -> str:
    """Statut global : dernier run par workflow → pire conclusion."""
    latest: dict[str, dict] = {}
    for r in runs:
        latest.setdefault(r["name"], r)
    runs_l = list(latest.values())
    if any(r["status"] != "completed" for r in runs_l):
        return "in_progress"
    concls = [r["conclusion"] for r in runs_l if r["conclusion"]]
    if "failure" in concls:
        return "failure"
    return "success" if concls else "unknown"


def _pypi_downloads(pkg: str = "grimoire-kit") -> dict:
    """Téléchargements PyPI récents (réels, API pypistats, best-effort)."""
    try:
        req = urllib.request.Request(
            f"https://pypistats.org/api/packages/{pkg}/recent",
            headers={"User-Agent": "grimoire-site-data"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 — URL fixe pypistats
            return json.loads(resp.read().decode()).get("data", {})
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}


def _coverage(root: Path) -> dict:
    """Couverture de tests depuis .coverage (best-effort, sans relancer la suite)."""
    if not (root / ".coverage").exists():
        return {}
    # --fail-under=0 : force un exit 0 (sinon le seuil du projet fait échouer la commande)
    raw = _run([sys.executable, "-m", "coverage", "report", "--fail-under=0"], root, timeout=60)
    m = re.search(r"^TOTAL\s+.*?(\d+(?:\.\d+)?)%", raw, re.MULTILINE)
    return {"percent": float(m.group(1))} if m else {}


def build_tracking(root: Path) -> dict:
    """Travail suivi : CI, couverture, PyPI (réels, best-effort)."""
    runs = _ci_runs(root)
    return {
        "ci": runs,
        "ci_status": _ci_status(runs),
        "coverage": _coverage(root),
        "pypi": _pypi_downloads(),
    }


def _delivery(root: Path) -> dict:
    """Métriques de livraison type DORA (réelles, via gh)."""
    runs = _gh_json(["run", "list", "--limit", "50", "--json",
                     "workflowName,conclusion,status,startedAt,updatedAt"], root)
    out: dict = {}
    if isinstance(runs, list) and runs:
        completed = [r for r in runs if r.get("status") == "completed"]
        concl = [r.get("conclusion") for r in completed if r.get("conclusion")]
        fails = sum(1 for c in concl if c in ("failure", "timed_out", "cancelled"))
        out["change_failure_rate"] = round(fails / len(concl), 3) if concl else 0.0
        durs = []
        for r in completed:
            a, b = _parse_iso(r.get("startedAt")), _parse_iso(r.get("updatedAt"))
            if a and b:
                durs.append((b - a).total_seconds())
        out["ci_avg_duration_s"] = round(sum(durs) / len(durs), 1) if durs else 0.0
        deploys = [r for r in runs if "deploy" in (r.get("workflowName") or "").lower()
                   and r.get("conclusion") == "success"]
        recent = [r for r in deploys if (_days_since(r.get("updatedAt")) or 999) <= 7]
        out["deploy_freq_7d"] = len(recent)
    merged = _gh_json(["pr", "list", "--state", "merged", "--limit", "30", "--json",
                       "createdAt,mergedAt"], root)
    if isinstance(merged, list) and merged:
        leads = []
        for p in merged:
            a, b = _parse_iso(p.get("createdAt")), _parse_iso(p.get("mergedAt"))
            if a and b:
                leads.append((b - a).total_seconds() / 3600.0)
        if leads:
            leads.sort()
            out["pr_lead_time_median_h"] = round(leads[len(leads) // 2], 2)
            out["pr_merged_sample"] = len(leads)
    return out


def build_activity(root: Path) -> dict:
    """Signaux projet 100% réels (git + GitHub + usage tokens) pour la page observability."""
    gh = _github(root)
    return {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "git": _git_activity(root),
        "pulls": gh["pulls"],
        "pulls_open": gh.get("pulls_open", 0),
        "repo": gh["repo"],
        "releases": _git_releases(root),
        "context_pressure": _context_pressure(root),
        "economy": build_economy(root),
        "tracking": build_tracking(root),
        "delivery": _delivery(root),
    }


# ── Insights : gouvernance, bench agents, routing, mémoire, code (réels) ─────

def _routing(root: Path) -> dict:
    """Décisions de routing réelles depuis _grimoire-output/.router-stats.jsonl."""
    path = root / "_grimoire-output/.router-stats.jsonl"
    if not path.is_file():
        return {}
    by_model: dict[str, int] = {}
    by_task: dict[str, int] = {}
    by_complexity: dict[str, int] = {}
    est_cost = 0.0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        for key, bucket in (("model", by_model), ("task_type", by_task), ("complexity", by_complexity)):
            v = r.get(key)
            if v:
                bucket[v] = bucket.get(v, 0) + 1
        est_cost += r.get("estimated_cost", 0) or 0
    return {"samples": n, "by_model": by_model, "by_task_type": by_task,
            "by_complexity": by_complexity, "est_cost_total": round(est_cost, 6)}


def _bench(root: Path) -> dict:
    """Scores de bench par agent (latest.md) + historique des rapports datés."""
    bdir = root / "_grimoire-output/bench-reports"
    if not bdir.is_dir():
        return {}
    reports = sorted(p.stem for p in bdir.glob("20*.md"))

    def _parse(path: Path) -> list[dict]:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|") or "Agent" in line or "---" in line or ":--" in line:
                continue
            cells = [c.strip().strip("`*") for c in line.strip().strip("|").split("|")]
            # ligne d'agent : 1ʳᵉ cellule non numérique, 2ᵉ cellule = score numérique
            if len(cells) >= 6 and cells[0] and re.search(r"\d", cells[1]):
                rows.append({
                    "agent": cells[0],
                    "score": _num(cells[1]),
                    "failures": _num(cells[4]),
                    "ac_pass": cells[5],
                })
        return rows

    # latest.md d'abord ; sinon le rapport daté le plus récent qui a des agents
    agents, as_of = [], ""
    candidates = ([bdir / "latest.md"] if (bdir / "latest.md").exists() else []) + \
                 [bdir / f"{d}.md" for d in reversed(reports)]
    for f in candidates:
        if not f.exists():
            continue
        agents = _parse(f)
        if agents:
            as_of = "latest" if f.name == "latest.md" else f.stem
            break
    return {"report_count": len(reports), "reports": reports[-8:], "latest": agents, "as_of": as_of}


def _memory_health(root: Path) -> dict:
    """Santé Memory OS : contradictions, failures, learnings, backends."""
    mdir = root / "_grimoire/_memory"
    if not mdir.is_dir():
        return {}

    def _entries(name: str) -> int:
        p = mdir / name
        if not p.is_file():
            return 0
        return len(re.findall(r"^(?:##\s|-\s)", p.read_text(encoding="utf-8"), re.MULTILINE))

    learn_dir = mdir / "agent-learnings"
    backends_dir = mdir / "backends"
    return {
        "contradictions": _entries("contradiction-log.md"),
        "failures": _entries("failure-museum.md"),
        "decisions": _entries("decisions-log.md"),
        "learnings_files": len(list(learn_dir.glob("*"))) if learn_dir.is_dir() else 0,
        "backends": sorted(p.name for p in backends_dir.iterdir() if p.is_dir()) if backends_dir.is_dir() else [],
    }


def _code_health(root: Path) -> dict:
    """Volume de code, ratio tests, cadence de releases, churn, issues."""
    def _loc(paths: list[str]) -> tuple[int, int]:
        loc = files = 0
        for base in paths:
            for p in (root / base).rglob("*.py"):
                if ".venv" in p.parts or "__pycache__" in p.parts:
                    continue
                files += 1
                with contextlib.suppress(OSError):
                    loc += sum(1 for _ in p.open(encoding="utf-8", errors="ignore"))
        return loc, files

    src_loc, src_files = _loc(["framework", "src"])
    test_loc, test_files = _loc(["tests"])
    # releases : total + sur 30 jours (la cadence par diff est trompeuse, releases en rafales)
    all_dates = []
    for d in _run(["git", "for-each-ref", "--format=%(creatordate:short)", "refs/tags"], root).splitlines():
        with contextlib.suppress(ValueError):
            all_dates.append(_dt.date.fromisoformat(d.strip()))
    today = _dt.date.today()
    releases_30d = sum(1 for d in all_dates if (today - d).days <= 30)
    tags_total = len(all_dates)
    # churn 60j
    churn_raw = _run(["git", "log", "--since=60 days ago", "--name-only", "--pretty=format:"], root)
    churn: dict[str, int] = {}
    for f in churn_raw.splitlines():
        f = f.strip()
        if f:
            churn[f] = churn.get(f, 0) + 1
    top_churn = [{"file": f, "changes": c} for f, c in sorted(churn.items(), key=lambda x: -x[1])[:8]]
    # issues
    open_i = _gh_json(["issue", "list", "-s", "open", "--limit", "200", "--json", "number"], root)
    closed_i = _gh_json(["issue", "list", "-s", "closed", "--limit", "200", "--json", "number"], root)
    return {
        "loc": src_loc, "py_files": src_files,
        "test_loc": test_loc, "test_files": test_files,
        "tests_code_ratio": round(test_loc / src_loc, 3) if src_loc else 0.0,
        "tags_total": tags_total, "releases_30d": releases_30d,
        "churn_top": top_churn,
        "issues_open": len(open_i) if isinstance(open_i, list) else 0,
        "issues_closed": len(closed_i) if isinstance(closed_i, list) else 0,
    }


def _governance(root: Path) -> dict:
    """Score d'antifragilité (run best-effort) + décisions loggées."""
    out: dict = {}
    # --dry-run : ne pas écrire antifragile-history.json (effet de bord) ;
    # l'outil émet le JSON suivi d'une ligne de log → on extrait le bloc {...}.
    raw = _run([sys.executable, str(root / "framework/tools/antifragile-score.py"),
                "--project-root", str(root), "--json", "--dry-run"], root, timeout=60, check=False)
    mjson = re.search(r"\{.*\}", raw, re.DOTALL)
    if mjson:
        try:
            d = json.loads(mjson.group())
        except json.JSONDecodeError:
            d = {}
        if d:
            out["antifragile"] = {
                "score": d.get("score"),
                "level": d.get("level"),
                "evidence": d.get("evidence"),
                "summary": d.get("summary", ""),
                "dimensions": {k: v.get("score") for k, v in (d.get("dimensions") or {}).items()},
            }
    return out


def build_insights(root: Path) -> dict:
    """Métriques avancées réelles : gouvernance, bench, routing, mémoire, code."""
    return {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "governance": _governance(root),
        "bench": _bench(root),
        "routing": _routing(root),
        "memory": _memory_health(root),
        "code": _code_health(root),
    }


def _efficiency(meta: dict, act: dict, ins: dict) -> dict:
    """Ratios d'efficience dérivés (réels) — calculés depuis les données déjà bâties."""
    git = act.get("git", {})
    code = ins.get("code", {})
    rtk = act.get("economy", {}).get("rtk", {})
    commits = git.get("commits_total", 0)
    tags = code.get("tags_total", 0)
    loc = code.get("loc", 0)
    tests = meta.get("counts", {}).get("tests", 0)
    cmds = rtk.get("total_commands", 0)
    return {
        "commits_per_release": round(commits / tags, 1) if tags else 0.0,
        "tests_per_kloc": round(tests / (loc / 1000), 1) if loc else 0.0,
        "tests_code_loc_ratio": code.get("tests_code_ratio", 0.0),
        "rtk_saved_per_command": round(rtk.get("saved_tokens", 0) / cmds) if cmds else 0,
        "rtk_savings_pct": rtk.get("savings_pct", 0.0),
    }


def _freshness(act: dict, ins: dict) -> dict:
    """Récence des signaux (jours) — confiance dans la fraîcheur du dashboard."""
    git = act.get("git", {})
    releases = act.get("releases", [])
    reports = ins.get("bench", {}).get("reports", [])
    out: dict = {
        "days_since_commit": _days_since(git.get("last_commit", {}).get("date", "")),
        "generated_at": ins.get("generated_at", ""),
    }
    if releases:
        out["days_since_release"] = _days_since(releases[0].get("date", ""))
    if reports:
        out["days_since_bench"] = _days_since(reports[-1])
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate web/ data layer from the project")
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--out-dir", type=Path, default=None, help="where to write the JSON (default: <root>/web/data)")
    ap.add_argument("--with-tests", action="store_true", help="run pytest --collect-only for the exact test count")
    args = ap.parse_args(argv)
    root = args.root.resolve()

    out_dir = (args.out_dir.resolve() if args.out_dir else root / "web" / "data")
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = build_meta(root, args.with_tests)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[OK] web/data/meta.json — v{meta['version']} · {meta['counts']}")

    board = build_taskboard(root)
    if board is not None:
        (out_dir / "taskboard.json").write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tag = "demo" if board["is_demo"] else "live"
        print(f"[OK] web/data/taskboard.json — {tag} · {len(board['states'])} états · {len(board['tasks'])} tâches · {board['source']}")

    obs = build_observatory(root)
    if obs is not None:
        (out_dir / "observatory.json").write_text(json.dumps(obs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tag = "demo" if obs.get("is_demo") else "live"
        print(f"[OK] web/data/observatory.json — {tag} · {len(obs.get('traces', []))} traces · {len(obs.get('agents', []))} agents · {len(obs.get('relationships', []))} relations")

    act = build_activity(root)
    (out_dir / "activity.json").write_text(json.dumps(act, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    g = act.get("git", {})
    print(f"[OK] web/data/activity.json — {g.get('commits_total', 0)} commits · {g.get('commits_7d', 0)} cette semaine · {len(act.get('pulls', []))} PRs · {g.get('contributor_count', 0)} contributeurs")

    ins = build_insights(root)
    ins["efficiency"] = _efficiency(meta, act, ins)
    ins["freshness"] = _freshness(act, ins)
    (out_dir / "insights.json").write_text(json.dumps(ins, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    code = ins.get("code", {})
    print(f"[OK] web/data/insights.json — {len(ins.get('bench', {}).get('latest', []))} agents bench · {ins.get('routing', {}).get('samples', 0)} routings · {code.get('loc', 0)} LOC · {code.get('tags_total', 0)} tags")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
