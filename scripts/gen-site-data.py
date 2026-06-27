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
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

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
    {"span_id": "a4", "parent_span_id": "a1", "trace_id": "a1", "tool": "llm", "operation": "generate", "agent": "dev", "duration_ms": 3100, "tokens": 4200, "retries": 0, "status": "ok"},
    {"span_id": "a5", "parent_span_id": "a1", "trace_id": "a1", "tool": "verify", "operation": "check", "agent": "qa", "duration_ms": 260, "tokens": 180, "retries": 0, "status": "ok"},
    # trace « standard-bridge » — gate de vérification
    {"span_id": "b1", "parent_span_id": "", "trace_id": "b1", "tool": "verify", "operation": "gate", "agent": "qa", "duration_ms": 1500, "tokens": 600, "retries": 0, "status": "ok"},
    {"span_id": "b2", "parent_span_id": "b1", "trace_id": "b1", "tool": "capability", "operation": "scan", "agent": "qa", "duration_ms": 240, "tokens": 150, "retries": 0, "status": "ok"},
    {"span_id": "b3", "parent_span_id": "b1", "trace_id": "b1", "tool": "llm", "operation": "review", "agent": "qa", "duration_ms": 1100, "tokens": 2800, "retries": 1, "status": "ok"},
    # trace « memory-runtime » — migration mémoire (avec un échec gated)
    {"span_id": "c1", "parent_span_id": "", "trace_id": "c1", "tool": "migrate", "operation": "run", "agent": "dev", "duration_ms": 5200, "tokens": 900, "retries": 0, "status": "ok"},
    {"span_id": "c2", "parent_span_id": "c1", "trace_id": "c1", "tool": "weaviate", "operation": "write", "agent": "dev", "duration_ms": 1800, "tokens": 300, "retries": 0, "status": "ok"},
    {"span_id": "c3", "parent_span_id": "c1", "trace_id": "c1", "tool": "llm", "operation": "embed", "agent": "mnemo", "duration_ms": 2600, "tokens": 5200, "retries": 0, "status": "ok"},
    {"span_id": "c4", "parent_span_id": "c1", "trace_id": "c1", "tool": "verify", "operation": "failclosed", "agent": "qa", "duration_ms": 140, "tokens": 120, "retries": 0, "status": "error"},
]


def _est_cost(tokens: int) -> float:
    return round(max(0, int(tokens or 0)) / 1000.0 * _COST_PER_1K, 6)


def _norm_span(e: dict) -> dict:
    """Normalise une entrée (synapse export ou démo) au schéma de la page."""
    return {
        "span_id": e.get("span_id", ""),
        "parent_span_id": e.get("parent_span_id", ""),
        "trace_id": e.get("trace_id", "") or e.get("span_id", ""),
        "tool": e.get("tool", ""),
        "operation": e.get("operation", ""),
        "agent": e.get("agent", ""),
        "duration_ms": e.get("duration_ms", 0) or 0,
        "tokens": e.get("tokens", e.get("tokens_estimated", 0)) or 0,
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
    return {
        "total_tokens": tokens,
        "total_cost_usd": cost,
        "total_duration_ms": round(sum(durs), 1),
        "span_count": n_spans,
        "trace_count": n_traces,
        "p50_latency_ms": _percentile(durs, 50),
        "p95_latency_ms": _percentile(durs, 95),
        "throughput_per_min": tput,
        "error_rate": round(errors / n_spans, 3) if n_spans else 0.0,
        "avg_trust": round(sum(trusts) / len(trusts), 3) if trusts else 0.0,
        # moyennes
        "avg_cost_per_trace": round(cost / n_traces, 6) if n_traces else 0.0,
        "avg_tokens_per_span": round(tokens / n_spans) if n_spans else 0,
        "avg_duration_ms": round(sum(durs) / n_spans, 1) if n_spans else 0.0,
        "avg_spans_per_trace": round(n_spans / n_traces, 1) if n_traces else 0.0,
        "retry_rate": round(retries / n_spans, 3) if n_spans else 0.0,
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
            sp["cost_usd"] = _est_cost(sp["tokens"])
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
    return out


# ── Project activity (git + GitHub, best-effort, données 100% réelles) ───────

def _run(args: list[str], root: Path, timeout: int = 25) -> str:
    try:
        r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
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


def build_activity(root: Path) -> dict:
    """Signaux projet 100% réels (git + GitHub) pour la page observability."""
    gh = _github(root)
    return {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "git": _git_activity(root),
        "pulls": gh["pulls"],
        "pulls_open": gh.get("pulls_open", 0),
        "repo": gh["repo"],
        "releases": _git_releases(root),
    }


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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
