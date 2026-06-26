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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate web/ data layer from the project")
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--with-tests", action="store_true", help="run pytest --collect-only for the exact test count")
    args = ap.parse_args(argv)
    root = args.root.resolve()

    out_dir = root / "web" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = build_meta(root, args.with_tests)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[OK] web/data/meta.json — v{meta['version']} · {meta['counts']}")

    board = build_taskboard(root)
    if board is not None:
        (out_dir / "taskboard.json").write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tag = "demo" if board["is_demo"] else "live"
        print(f"[OK] web/data/taskboard.json — {tag} · {len(board['states'])} états · {len(board['tasks'])} tâches · {board['source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
