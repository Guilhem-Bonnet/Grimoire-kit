#!/usr/bin/env python3
"""Génère des projets de DÉMO factices pour la vitrine multi-projets (GitHub Pages).

La vitrine publique est mono-projet par nature (un seul vrai dépôt). Pour *montrer*
le cockpit multi-projets (sélecteur + portefeuille), on dérive 2-3 projets démo du
projet primaire réel : copie des JSON + overrides d'en-tête, tout marqué is_demo.

NB : ceci est réservé à la VITRINE. En local, `serve-site.sh --registry` génère les
VRAIS projets. Les features de pilotage restent bloquées sur la vitrine (env public).

Usage : python scripts/gen-demo-projects.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "data"
BASE = DATA / "projects" / "grimoire-kit"
FILES = ["meta", "taskboard", "observatory", "activity", "insights", "memory"]

# Projets démo (factices) — overrides d'en-tête sur la base réelle.
SPECS = [
    {"slug": "atlas-ops", "name": "Atlas Ops", "version": "1.8.0", "ci": "failure",
     "af": 71, "level": "RÉSILIENT", "contradictions": 3, "coverage": 58.0,
     "commits": 872, "commits7": 23, "cost": 1.24, "backend": "redis"},
    {"slug": "sentinel-sec", "name": "Sentinel Sec", "version": "0.9.3", "ci": "success",
     "af": 68, "level": "RÉSILIENT", "contradictions": 7, "coverage": 63.0,
     "commits": 503, "commits7": 12, "cost": 0.61, "backend": "weaviate"},
    {"slug": "ledger-data", "name": "Ledger Data", "version": "3.1.2", "ci": "success",
     "af": 88, "level": "ROBUSTE", "contradictions": 0, "coverage": 91.0,
     "commits": 2041, "commits7": 31, "cost": 2.07, "backend": "neo4j"},
]


def _write(pdir: Path, name: str, data: dict) -> None:
    (pdir / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _apply(base: dict, spec: dict) -> dict:
    pdir = DATA / "projects" / spec["slug"]
    pdir.mkdir(parents=True, exist_ok=True)
    d = {f: copy.deepcopy(base[f]) for f in FILES}
    for f in FILES:
        if isinstance(d[f], dict):
            d[f]["is_demo"] = True

    d["meta"]["version"] = spec["version"]

    obs = d["observatory"]
    m = obs.setdefault("metrics", {})
    cur_cost = m.get("total_cost_usd") or 0.27
    scale = spec["cost"] / cur_cost if cur_cost else 1.0
    for k in ("total_cost_usd", "avg_cost_per_trace"):
        if m.get(k):
            m[k] = round(m[k] * scale, 6)
    if isinstance(m.get("cost_by_model"), dict):
        m["cost_by_model"] = {kk: round(vv * scale, 6) for kk, vv in m["cost_by_model"].items()}

    d["memory"].setdefault("backend", {})["active"] = spec["backend"]
    gov = d["insights"].setdefault("governance", {}).setdefault("antifragile", {})
    gov["score"] = spec["af"]
    gov["level"] = spec["level"]
    d["insights"].setdefault("memory", {})["contradictions"] = spec["contradictions"]
    d["memory"].setdefault("counts_by_type", {})["contradictions"] = spec["contradictions"]

    act = d["activity"]
    tr = act.setdefault("tracking", {})
    tr["ci_status"] = spec["ci"]
    tr.setdefault("coverage", {})["percent"] = spec["coverage"]
    g = act.setdefault("git", {})
    g["commits_total"] = spec["commits"]
    g["commits_7d"] = spec["commits7"]

    for f in FILES:
        _write(pdir, f, d[f])

    return {
        "slug": spec["slug"], "name": spec["name"], "path": "(démo)", "version": spec["version"],
        "is_demo": True, "commits_total": spec["commits"], "commits_7d": spec["commits7"],
        "total_cost_usd": spec["cost"], "traces": len(obs.get("traces", [])),
        "agents": len(obs.get("agent_ids", [])), "ci_status": spec["ci"],
        "antifragile": spec["af"], "antifragile_level": spec["level"],
        "memory_backend": spec["backend"], "memory_entries": d["memory"].get("store", {}).get("total_entries", 0),
        "contradictions": spec["contradictions"], "coverage": spec["coverage"],
        "generated_at": d["meta"].get("generated_at", ""),
    }


def main() -> int:
    if not BASE.is_dir():
        print("[!] data/projects/grimoire-kit absent — lance d'abord gen-site-data.py")
        return 1
    base = {f: json.loads((BASE / f"{f}.json").read_text(encoding="utf-8")) for f in FILES}
    index = json.loads((DATA / "projects.json").read_text(encoding="utf-8"))
    primary_head = index["projects"][0]  # grimoire-kit (réel)
    heads = [primary_head] + [_apply(base, s) for s in SPECS]
    index["projects"] = heads
    index["multi"] = True
    index["env"] = "vitrine"  # signal : features de pilotage bloquées sur la vitrine publique
    (DATA / "projects.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[OK] {len(heads)} projets (1 réel + {len(SPECS)} démo) · vitrine multi-projets")
    for h in heads:
        print(f"  {h['slug']:14} v{h['version']:8} CI {h['ci_status']:9} AF {h['antifragile']} · {h['memory_backend']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
