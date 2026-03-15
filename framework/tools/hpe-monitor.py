#!/usr/bin/env python3
"""
hpe-monitor.py — HPE Execution Monitor & Dashboard.
====================================================

Génère un dashboard HTML interactif *autoporté* (single-file, zero CDN)
pour visualiser l'état des plans HPE, les vagues d'exécution, les tâches
et leurs dépendances.

Vues :
  1. **Plan Overview** — Résumé des plans actifs (progression, états)
  2. **DAG View**      — Graphe des tâches avec code couleur par état
  3. **Wave Timeline** — Chronologie des vagues d'exécution
  4. **Trace Log**     — Tableau filtrable des traces d'exécution
  5. **Backend Stats** — Statistiques MCP / Sequential / Dry-run

Sources de données :
  - _grimoire-output/.hpe/plans/*.json       (HPE plans)
  - _grimoire-output/.hpe/checkpoints/*.json (Checkpoints)
  - _grimoire-output/.hpe/traces/*.json      (Execution traces)
  - _grimoire-output/.hpe/hpe-history.jsonl  (Event history)

Modes :
  generate  — Génère le fichier HTML dans _grimoire-output/
  serve     — Génère + lance un serveur local avec auto-reload
  status    — Affiche un résumé texte des plans
  export    — Exporte les données parsées en JSON

Usage :
  python3 hpe-monitor.py --project-root . generate
  python3 hpe-monitor.py --project-root . serve --port 8421
  python3 hpe-monitor.py --project-root . status
  python3 hpe-monitor.py --project-root . export > hpe-data.json

Stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.hpe_monitor")

HPE_MONITOR_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

HPE_DIR = "_grimoire-output/.hpe"
PLANS_DIR = "plans"
CHECKPOINTS_DIR = "checkpoints"
TRACES_DIR = "traces"
HISTORY_FILE = "hpe-history.jsonl"
OUTPUT_FILE = "hpe-dashboard.html"

# État → couleur CSS (reprend le palette Observatory)
STATE_COLORS = {
    "pending": "#8b949e",
    "ready": "#58a6ff",
    "running": "#d29922",
    "done": "#3fb950",
    "failed": "#f85149",
    "cancelled": "#8b949e",
    "skipped": "#bc8cff",
    "completed": "#3fb950",
    "paused": "#f0883e",
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class MonitorData:
    """Données collectées pour le dashboard."""

    plans: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    backends: dict[str, int] = field(default_factory=dict)
    generated_at: str = ""


# ── Data Loaders ─────────────────────────────────────────────────────────────


def _hpe_dir(project_root: Path) -> Path:
    return project_root / HPE_DIR


def load_plans(project_root: Path) -> list[dict[str, Any]]:
    """Charge tous les plans HPE."""
    plans_dir = _hpe_dir(project_root) / PLANS_DIR
    if not plans_dir.is_dir():
        return []
    plans = []
    for f in sorted(plans_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            plans.append(data)
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("Cannot load plan %s: %s", f.name, e)
    return plans


def load_checkpoints(project_root: Path) -> list[dict[str, Any]]:
    """Charge tous les checkpoints."""
    cp_dir = _hpe_dir(project_root) / CHECKPOINTS_DIR
    if not cp_dir.is_dir():
        return []
    checkpoints = []
    for f in sorted(cp_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            checkpoints.append(data)
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("Cannot load checkpoint %s: %s", f.name, e)
    return checkpoints


def load_traces(project_root: Path) -> list[dict[str, Any]]:
    """Charge toutes les traces d'exécution."""
    traces_dir = _hpe_dir(project_root) / TRACES_DIR
    if not traces_dir.is_dir():
        return []
    traces = []
    for f in sorted(traces_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            traces.append(data)
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("Cannot load trace %s: %s", f.name, e)
    return traces


def load_history(project_root: Path) -> list[dict[str, Any]]:
    """Charge l'historique des événements."""
    history_file = _hpe_dir(project_root) / HISTORY_FILE
    if not history_file.exists():
        return []
    events = []
    for line in history_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            events.append(json.loads(line))
    return events


def compute_backend_stats(traces: list[dict[str, Any]]) -> dict[str, int]:
    """Calcule les stats par backend."""
    stats: dict[str, int] = {}
    for t in traces:
        backend = t.get("backend", "unknown")
        stats[backend] = stats.get(backend, 0) + 1
    return stats


def load_all(project_root: Path) -> MonitorData:
    """Charge toutes les données HPE."""
    plans = load_plans(project_root)
    checkpoints = load_checkpoints(project_root)
    traces = load_traces(project_root)
    history = load_history(project_root)
    backends = compute_backend_stats(traces)

    return MonitorData(
        plans=plans,
        checkpoints=checkpoints,
        traces=traces,
        history=history,
        backends=backends,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


# ── Plan Analysis ────────────────────────────────────────────────────────────


def plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    """Résumé compact d'un plan."""
    tasks = plan.get("tasks", [])
    by_status: dict[str, int] = {}
    for t in tasks:
        st = t.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1

    total = len(tasks)
    done = by_status.get("done", 0) + by_status.get("skipped", 0)
    progress = round(done / total * 100, 1) if total else 0

    return {
        "plan_id": plan.get("plan_id", "?"),
        "description": plan.get("description", ""),
        "state": plan.get("state", "pending"),
        "total_tasks": total,
        "by_status": by_status,
        "progress_pct": progress,
        "waves_completed": plan.get("waves_completed", 0),
        "created_at": plan.get("created_at", ""),
        "updated_at": plan.get("updated_at", ""),
    }


def build_dag_edges(plan: dict[str, Any]) -> list[dict[str, str]]:
    """Extrait les arêtes du DAG pour le rendu visuel."""
    edges = []
    for t in plan.get("tasks", []):
        task_id = t.get("id", "")
        for dep in t.get("depends_on", []):
            edges.append({"from": dep, "to": task_id})
    return edges


def topological_layers(plan: dict[str, Any]) -> list[list[str]]:
    """Calcule les couches topologiques (waves) pour l'affichage."""
    tasks = {t["id"]: t for t in plan.get("tasks", []) if "id" in t}
    if not tasks:
        return []

    in_degree: dict[str, int] = dict.fromkeys(tasks, 0)
    children: dict[str, list[str]] = {tid: [] for tid in tasks}

    for t in tasks.values():
        for dep in t.get("depends_on", []):
            if dep in in_degree:
                in_degree[t["id"]] = in_degree.get(t["id"], 0) + 1
                children.setdefault(dep, []).append(t["id"])

    layers: list[list[str]] = []
    remaining = dict(in_degree)
    while remaining:
        layer = [tid for tid, deg in remaining.items() if deg == 0]
        if not layer:
            break
        layers.append(sorted(layer))
        for tid in layer:
            del remaining[tid]
            for child in children.get(tid, []):
                if child in remaining:
                    remaining[child] -= 1

    return layers


# ── Text Status ──────────────────────────────────────────────────────────────


def format_status_text(data: MonitorData) -> str:
    """Formate un résumé texte des plans HPE."""
    lines: list[str] = []
    lines.append(f"═══ HPE Monitor — {data.generated_at} ═══")
    lines.append("")

    if not data.plans:
        lines.append("Aucun plan HPE trouvé.")
        lines.append(f"  Répertoire scanné: {HPE_DIR}/plans/")
        return "\n".join(lines)

    for plan in data.plans:
        s = plan_summary(plan)
        state_icon = {
            "pending": "⏳", "running": "🔄", "completed": "✅",
            "failed": "❌", "paused": "⏸️",
        }.get(s["state"], "❔")

        lines.append(f"{state_icon} Plan: {s['plan_id']}")
        if s["description"]:
            lines.append(f"  {s['description']}")
        lines.append(f"  État: {s['state']}  |  Progrès: {s['progress_pct']}%  |  "
                      f"Waves: {s['waves_completed']}")
        lines.append(f"  Tâches: {s['total_tasks']}  →  "
                      + "  ".join(f"{k}:{v}" for k, v in sorted(s["by_status"].items())))

        # DAG layers
        layers = topological_layers(plan)
        if layers:
            lines.append("  Waves :")
            for i, layer in enumerate(layers, 1):
                task_details = []
                for tid in layer:
                    task = next((t for t in plan.get("tasks", []) if t.get("id") == tid), None)
                    if task:
                        st = task.get("status", "?")
                        agent = task.get("agent", "?")
                        icon = {"done": "✅", "failed": "❌", "running": "🔄",
                                "pending": "⏳", "ready": "🔵", "skipped": "⏭️",
                                "cancelled": "🚫"}.get(st, "❔")
                        task_details.append(f"    {icon} {tid} ({agent}) [{st}]")
                lines.append(f"    Wave {i}: [{', '.join(layer)}]")
                lines.extend(task_details)

        lines.append("")

    # Traces summary
    if data.traces:
        lines.append(f"📊 Traces: {len(data.traces)} exécutions")
        for backend, count in sorted(data.backends.items()):
            lines.append(f"  {backend}: {count}")
        lines.append("")

    # Checkpoints
    if data.checkpoints:
        lines.append(f"💾 Checkpoints: {len(data.checkpoints)}")
        for cp in data.checkpoints[-3:]:
            lines.append(f"  {cp.get('checkpoint_id', '?')} "
                          f"(plan: {cp.get('plan_id', '?')}, "
                          f"trigger: {cp.get('trigger_task', '-')})")
        lines.append("")

    return "\n".join(lines)


# ── JSON Export ──────────────────────────────────────────────────────────────


def data_to_json(data: MonitorData) -> str:
    """Sérialise les données pour export ou embedding HTML."""
    payload = {
        "plans": data.plans,
        "plan_summaries": [plan_summary(p) for p in data.plans],
        "checkpoints": data.checkpoints,
        "traces": data.traces,
        "history": data.history,
        "backends": data.backends,
        "generated_at": data.generated_at,
        "dag_layers": {
            p.get("plan_id", "?"): topological_layers(p)
            for p in data.plans
        },
        "dag_edges": {
            p.get("plan_id", "?"): build_dag_edges(p)
            for p in data.plans
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=None)


# ── HTML Generator ───────────────────────────────────────────────────────────


def generate_html(data: MonitorData) -> str:
    """Génère le dashboard HTML autoporté."""
    json_data = data_to_json(data)
    return _HTML_TEMPLATE.replace("__HPE_DATA__", json_data)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HPE Monitor — Grimoire</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;
  --fg:#c9d1d9;--fg2:#8b949e;--accent:#58a6ff;--green:#3fb950;
  --yellow:#d29922;--red:#f85149;--purple:#bc8cff;--orange:#f0883e;
  --cyan:#39d2c0;--pink:#f778ba;
  --font:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --mono:'SF Mono',Consolas,'Liberation Mono',Menlo,monospace;
}
html{font-size:14px;background:var(--bg);color:var(--fg)}
body{font-family:var(--font);min-height:100vh;display:flex;flex-direction:column}
a{color:var(--accent);text-decoration:none}

/* ── Header ─────────────────────────────── */
.hdr{background:var(--bg2);border-bottom:1px solid var(--border);padding:12px 24px;
  display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.hdr h1{font-size:1.2rem;font-weight:600}
.hdr .gen{color:var(--fg2);font-size:.85rem;margin-left:auto}

/* ── Tabs ───────────────────────────────── */
.tabs{display:flex;gap:0;background:var(--bg2);border-bottom:1px solid var(--border);
  padding:0 24px;position:sticky;top:44px;z-index:99}
.tab{padding:10px 18px;cursor:pointer;color:var(--fg2);border-bottom:2px solid transparent;
  font-size:.9rem;transition:.2s}
.tab:hover{color:var(--fg)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}

/* ── Content ────────────────────────────── */
.content{flex:1;padding:24px;max-width:1400px;margin:0 auto;width:100%}
.view{display:none}
.view.active{display:block}

/* ── Cards ──────────────────────────────── */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:16px;margin-bottom:16px}
.card h3{font-size:1rem;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}

/* ── Progress bar ───────────────────────── */
.progress-bar{height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;margin:8px 0}
.progress-fill{height:100%;border-radius:4px;transition:width .3s}

/* ── State badges ───────────────────────── */
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75rem;
  font-weight:600;text-transform:uppercase;letter-spacing:.03em}
.badge-pending{background:#8b949e22;color:#8b949e}
.badge-ready{background:#58a6ff22;color:#58a6ff}
.badge-running{background:#d2992222;color:#d29922}
.badge-done,.badge-completed{background:#3fb95022;color:#3fb950}
.badge-failed{background:#f8514922;color:#f85149}
.badge-cancelled{background:#8b949e22;color:#8b949e}
.badge-skipped{background:#bc8cff22;color:#bc8cff}
.badge-paused{background:#f0883e22;color:#f0883e}

/* ── DAG ────────────────────────────────── */
.dag-container{position:relative;overflow-x:auto;padding:20px 0}
.wave-col{display:inline-flex;flex-direction:column;gap:10px;padding:0 24px;
  vertical-align:top;min-width:140px;border-left:2px solid var(--border)}
.wave-col:first-child{border-left:none}
.wave-label{color:var(--fg2);font-size:.8rem;font-weight:600;margin-bottom:8px;
  text-align:center;text-transform:uppercase}
.task-node{background:var(--bg3);border:2px solid var(--border);border-radius:8px;
  padding:10px 14px;min-width:120px;cursor:default;transition:.2s;position:relative}
.task-node:hover{border-color:var(--accent);transform:translateY(-1px)}
.task-node .tid{font-family:var(--mono);font-weight:600;font-size:.9rem}
.task-node .agent{color:var(--fg2);font-size:.8rem}
.task-node .desc{color:var(--fg2);font-size:.75rem;margin-top:4px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}
.task-node[data-status="done"]{border-color:var(--green)}
.task-node[data-status="failed"]{border-color:var(--red)}
.task-node[data-status="running"]{border-color:var(--yellow);
  box-shadow:0 0 8px rgba(210,153,34,.3)}
.task-node[data-status="ready"]{border-color:var(--accent)}
.task-node[data-status="skipped"]{border-color:var(--purple);opacity:.7}
.task-node[data-status="cancelled"]{opacity:.5}

/* ── SVG edges ──────────────────────────── */
.dag-svg{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:1}
.dag-svg line{stroke:var(--border);stroke-width:2}
.dag-nodes{position:relative;z-index:2;display:flex;align-items:flex-start}

/* ── Timeline ───────────────────────────── */
.timeline{position:relative;padding-left:24px;border-left:2px solid var(--border)}
.timeline-event{position:relative;padding:8px 0 16px 16px}
.timeline-event::before{content:'';position:absolute;left:-29px;top:12px;
  width:10px;height:10px;border-radius:50%;border:2px solid var(--border);background:var(--bg)}
.timeline-event.ev-plan_start::before{background:var(--accent)}
.timeline-event.ev-plan_end::before{background:var(--green)}
.timeline-event.ev-checkpoint::before{background:var(--yellow)}
.timeline-event.ev-wave_complete::before{background:var(--cyan)}
.timeline-meta{color:var(--fg2);font-size:.8rem}
.timeline-body{font-size:.9rem;margin-top:2px}

/* ── Table ──────────────────────────────── */
.tbl{width:100%;border-collapse:collapse;font-size:.85rem}
.tbl th,.tbl td{padding:8px 12px;border-bottom:1px solid var(--border);text-align:left}
.tbl th{color:var(--fg2);font-weight:600;text-transform:uppercase;font-size:.75rem;
  letter-spacing:.04em;position:sticky;top:0;background:var(--bg2)}
.tbl tbody tr:hover{background:var(--bg3)}

/* ── Stats ──────────────────────────────── */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.stat{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
  padding:16px;text-align:center}
.stat .val{font-size:2rem;font-weight:700;line-height:1}
.stat .lbl{color:var(--fg2);font-size:.8rem;margin-top:4px}

/* ── Empty state ────────────────────────── */
.empty{text-align:center;color:var(--fg2);padding:48px;font-size:1.1rem}
.empty code{color:var(--accent);font-family:var(--mono)}

/* ── Popover ────────────────────────────── */
.popover{display:none;position:absolute;background:var(--bg2);border:1px solid var(--border);
  border-radius:8px;padding:12px;z-index:200;min-width:240px;max-width:380px;
  box-shadow:0 4px 16px rgba(0,0,0,.4);font-size:.85rem}
.popover.show{display:block}
.popover h4{font-size:.9rem;margin-bottom:8px}
.popover .kv{display:flex;gap:8px;margin:4px 0}
.popover .kv .k{color:var(--fg2);min-width:70px}

/* ── Responsive ─────────────────────────── */
@media(max-width:768px){
  .card-grid{grid-template-columns:1fr}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  .hdr{padding:8px 12px}
  .content{padding:12px}
}
</style>
</head>
<body>

<div class="hdr">
  <h1>⚡ HPE Monitor</h1>
  <span style="color:var(--fg2)">Hybrid Parallelism Engine</span>
  <span class="gen" id="gen-time"></span>
</div>

<div class="tabs" id="tabs">
  <div class="tab active" data-view="overview">Overview</div>
  <div class="tab" data-view="dag">DAG</div>
  <div class="tab" data-view="timeline">Timeline</div>
  <div class="tab" data-view="traces">Traces</div>
  <div class="tab" data-view="backends">Backends</div>
</div>

<div class="content">
  <!-- OVERVIEW -->
  <div class="view active" id="v-overview"></div>

  <!-- DAG -->
  <div class="view" id="v-dag"></div>

  <!-- TIMELINE -->
  <div class="view" id="v-timeline"></div>

  <!-- TRACES -->
  <div class="view" id="v-traces"></div>

  <!-- BACKENDS -->
  <div class="view" id="v-backends"></div>
</div>

<div class="popover" id="popover"></div>

<script>
// ──────────────────────────────────────────────────────────────────────────
//  Data injected by hpe-monitor.py
// ──────────────────────────────────────────────────────────────────────────
const D = __HPE_DATA__;

const stateColors = {
  pending:'#8b949e', ready:'#58a6ff', running:'#d29922',
  done:'#3fb950', completed:'#3fb950', failed:'#f85149',
  cancelled:'#8b949e', skipped:'#bc8cff', paused:'#f0883e'
};

// ── Tab switching ───────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.view').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('v-' + t.dataset.view).classList.add('active');
  });
});

// ── Helpers ─────────────────────────────────────────────────
function badge(state) {
  return `<span class="badge badge-${state}">${state}</span>`;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function pct(val, total) {
  return total ? Math.round(val / total * 1000) / 10 : 0;
}

// ── Generated time ──────────────────────────────────────────
document.getElementById('gen-time').textContent = D.generated_at ?
  `Généré : ${D.generated_at}` : '';

// ══════════════════════════════════════════════════════════════
//  Overview
// ══════════════════════════════════════════════════════════════
function renderOverview() {
  const el = document.getElementById('v-overview');
  if (!D.plans.length) {
    el.innerHTML = `<div class="empty">
      <p>Aucun plan HPE trouvé.</p>
      <p style="margin-top:12px">Lancez un plan via&nbsp;:
      <code>python3 hpe-runner.py --project-root . run …</code></p>
    </div>`;
    return;
  }

  // Global stats
  const totalTasks = D.plans.reduce((a, p) => a + (p.tasks || []).length, 0);
  const doneT = D.plans.reduce((a, p) =>
    a + (p.tasks || []).filter(t => t.status === 'done' || t.status === 'skipped').length, 0);
  const failedT = D.plans.reduce((a, p) =>
    a + (p.tasks || []).filter(t => t.status === 'failed').length, 0);

  let html = `<div class="stat-grid" style="margin-bottom:24px">
    <div class="stat"><div class="val">${D.plans.length}</div><div class="lbl">Plans</div></div>
    <div class="stat"><div class="val">${totalTasks}</div><div class="lbl">Tâches totales</div></div>
    <div class="stat"><div class="val" style="color:var(--green)">${doneT}</div>
      <div class="lbl">Terminées</div></div>
    <div class="stat"><div class="val" style="color:var(--red)">${failedT}</div>
      <div class="lbl">Échouées</div></div>
    <div class="stat"><div class="val">${D.traces.length}</div><div class="lbl">Traces</div></div>
    <div class="stat"><div class="val">${D.checkpoints.length}</div>
      <div class="lbl">Checkpoints</div></div>
  </div>`;

  html += '<div class="card-grid">';
  for (const sum of D.plan_summaries) {
    const prog = sum.progress_pct;
    const progColor = prog >= 100 ? 'var(--green)' : prog > 0 ? 'var(--yellow)' : 'var(--fg2)';
    html += `<div class="card">
      <h3>${badge(sum.state)} <code style="font-size:.85rem">${esc(sum.plan_id)}</code></h3>
      ${sum.description ? `<p style="color:var(--fg2);margin-bottom:8px">${esc(sum.description)}</p>` : ''}
      <div class="progress-bar"><div class="progress-fill"
        style="width:${prog}%;background:${progColor}"></div></div>
      <div style="display:flex;justify-content:space-between;font-size:.8rem;color:var(--fg2)">
        <span>${prog}% complété</span>
        <span>Waves: ${sum.waves_completed}</span>
      </div>
      <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">
        ${Object.entries(sum.by_status).map(([k, v]) =>
          `<span style="font-size:.8rem;color:${stateColors[k] || '#8b949e'}">${k}: ${v}</span>`
        ).join(' · ')}
      </div>
      ${sum.created_at ? `<div style="margin-top:8px;font-size:.75rem;color:var(--fg2)">
        Créé: ${sum.created_at}</div>` : ''}
    </div>`;
  }
  html += '</div>';
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
//  DAG View
// ══════════════════════════════════════════════════════════════
function renderDAG() {
  const el = document.getElementById('v-dag');
  if (!D.plans.length) {
    el.innerHTML = '<div class="empty">Aucun plan à visualiser.</div>';
    return;
  }

  let html = '';
  for (const plan of D.plans) {
    const planId = plan.plan_id || '?';
    const layers = D.dag_layers[planId] || [];
    const tasks = plan.tasks || [];
    const taskMap = {};
    tasks.forEach(t => { taskMap[t.id] = t; });

    html += `<div class="card">
      <h3>${badge(plan.state || 'pending')} DAG — <code>${esc(planId)}</code></h3>`;

    if (!layers.length) {
      html += '<p style="color:var(--fg2)">Aucune couche topologique.</p>';
    } else {
      html += '<div class="dag-container"><div class="dag-nodes">';
      for (let w = 0; w < layers.length; w++) {
        html += `<div class="wave-col">
          <div class="wave-label">Wave ${w + 1}</div>`;
        for (const tid of layers[w]) {
          const task = taskMap[tid] || {};
          const st = task.status || 'pending';
          const agent = task.agent || '?';
          const desc = (task.task || '').substring(0, 50);
          const deps = (task.depends_on || []).join(', ');
          html += `<div class="task-node" data-status="${st}" data-tid="${esc(tid)}"
            title="${esc(tid)} (${agent}) [${st}]${deps ? '\nDeps: ' + deps : ''}">
            <div style="display:flex;align-items:center;gap:6px">
              <span style="width:8px;height:8px;border-radius:50%;
                background:${stateColors[st] || '#8b949e'}"></span>
              <span class="tid">${esc(tid)}</span>
            </div>
            <div class="agent">🤖 ${esc(agent)}</div>
            ${desc ? `<div class="desc">${esc(desc)}</div>` : ''}
          </div>`;
        }
        html += '</div>';
      }
      html += '</div></div>';

      // Dependency arrows (text-only legend)
      const edges = D.dag_edges[planId] || [];
      if (edges.length) {
        html += `<details style="margin-top:12px">
          <summary style="color:var(--fg2);cursor:pointer;font-size:.85rem">
            📐 Dépendances (${edges.length})</summary>
          <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:8px">
            ${edges.map(e =>
              `<span style="font-size:.8rem;font-family:var(--mono);
                color:var(--fg2)">${esc(e.from)} → ${esc(e.to)}</span>`
            ).join('')}
          </div>
        </details>`;
      }
    }
    html += '</div>';
  }
  el.innerHTML = html;

  // Task node click → popover
  el.querySelectorAll('.task-node').forEach(node => {
    node.addEventListener('click', e => {
      const tid = node.dataset.tid;
      const plan = D.plans.find(p => (p.tasks || []).some(t => t.id === tid));
      if (!plan) return;
      const task = plan.tasks.find(t => t.id === tid);
      if (!task) return;
      showPopover(e, task);
    });
  });
}

function showPopover(event, task) {
  const pop = document.getElementById('popover');
  const st = task.status || 'pending';
  let html = `<h4>${badge(st)} ${esc(task.id)}</h4>`;
  html += `<div class="kv"><span class="k">Agent</span><span>${esc(task.agent)}</span></div>`;
  html += `<div class="kv"><span class="k">Tâche</span><span>${esc(task.task)}</span></div>`;
  if (task.depends_on && task.depends_on.length) {
    html += `<div class="kv"><span class="k">Dépend</span>
      <span>${task.depends_on.map(esc).join(', ')}</span></div>`;
  }
  if (task.output_key) {
    html += `<div class="kv"><span class="k">Output</span>
      <span><code>${esc(task.output_key)}</code></span></div>`;
  }
  if (task.error) {
    html += `<div class="kv"><span class="k" style="color:var(--red)">Erreur</span>
      <span style="color:var(--red)">${esc(task.error)}</span></div>`;
  }
  if (task.started_at) {
    html += `<div class="kv"><span class="k">Début</span><span>${task.started_at}</span></div>`;
  }
  if (task.completed_at) {
    html += `<div class="kv"><span class="k">Fin</span><span>${task.completed_at}</span></div>`;
  }
  if (task.result && Object.keys(task.result).length) {
    html += `<details style="margin-top:8px"><summary style="cursor:pointer;color:var(--fg2)">
      Résultat</summary><pre style="margin-top:4px;font-size:.75rem;
      overflow:auto;max-height:200px">${esc(JSON.stringify(task.result, null, 2))}</pre></details>`;
  }
  pop.innerHTML = html;
  pop.classList.add('show');
  const rect = event.target.closest('.task-node').getBoundingClientRect();
  pop.style.top = (rect.bottom + window.scrollY + 4) + 'px';
  pop.style.left = Math.min(rect.left, window.innerWidth - 400) + 'px';
}
document.addEventListener('click', e => {
  if (!e.target.closest('.task-node') && !e.target.closest('.popover')) {
    document.getElementById('popover').classList.remove('show');
  }
});

// ══════════════════════════════════════════════════════════════
//  Timeline
// ══════════════════════════════════════════════════════════════
function renderTimeline() {
  const el = document.getElementById('v-timeline');
  if (!D.history.length) {
    el.innerHTML = '<div class="empty">Aucun événement HPE enregistré.</div>';
    return;
  }

  let html = '<div class="card"><h3>📅 Historique des événements</h3><div class="timeline">';
  for (const ev of D.history) {
    const type = ev.event || 'unknown';
    const planId = ev.plan_id || '';
    let body = `<strong>${esc(type)}</strong>`;
    if (planId) body += ` — <code>${esc(planId)}</code>`;
    if (ev.state) body += ` → ${badge(ev.state)}`;
    if (ev.waves) body += ` (${ev.waves} waves)`;
    if (ev.task_id) body += ` task: <code>${esc(ev.task_id)}</code>`;
    const ts = ev.timestamp || ev.ts || '';

    html += `<div class="timeline-event ev-${type}">
      <div class="timeline-meta">${esc(ts)}</div>
      <div class="timeline-body">${body}</div>
    </div>`;
  }
  html += '</div></div>';
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
//  Traces
// ══════════════════════════════════════════════════════════════
function renderTraces() {
  const el = document.getElementById('v-traces');
  if (!D.traces.length) {
    el.innerHTML = '<div class="empty">Aucune trace d\'exécution.</div>';
    return;
  }

  let html = `<div class="card"><h3>📋 Traces d'exécution (${D.traces.length})</h3>
    <div style="overflow-x:auto"><table class="tbl"><thead><tr>
      <th>Trace ID</th><th>Task</th><th>Agent</th><th>Backend</th>
      <th>Status</th><th>Durée</th><th>Tokens</th><th>Timestamp</th>
    </tr></thead><tbody>`;

  for (const t of D.traces) {
    const dur = t.duration_ms ? `${t.duration_ms}ms` : '-';
    const tokens = t.tokens_used || '-';
    html += `<tr>
      <td style="font-family:var(--mono);font-size:.8rem">${esc(t.trace_id)}</td>
      <td><code>${esc(t.task_id)}</code></td>
      <td>${esc(t.agent)}</td>
      <td>${badge(t.backend || 'unknown')}</td>
      <td>${badge(t.status || 'unknown')}</td>
      <td>${dur}</td>
      <td>${tokens}</td>
      <td style="color:var(--fg2);font-size:.8rem">${esc(t.timestamp)}</td>
    </tr>`;
  }
  html += '</tbody></table></div></div>';
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
//  Backends
// ══════════════════════════════════════════════════════════════
function renderBackends() {
  const el = document.getElementById('v-backends');
  const entries = Object.entries(D.backends);
  if (!entries.length) {
    el.innerHTML = '<div class="empty">Aucune donnée backend.</div>';
    return;
  }

  const total = entries.reduce((a, [_, v]) => a + v, 0);
  const backendColors = {
    mcp: 'var(--accent)', sequential: 'var(--yellow)',
    'dry-run': 'var(--purple)', 'message-bus': 'var(--cyan)',
  };

  let html = '<div class="stat-grid" style="margin-bottom:24px">';
  for (const [name, count] of entries) {
    const color = backendColors[name] || 'var(--fg)';
    html += `<div class="stat">
      <div class="val" style="color:${color}">${count}</div>
      <div class="lbl">${esc(name)}</div>
      <div style="color:var(--fg2);font-size:.75rem;margin-top:4px">
        ${pct(count, total)}%</div>
    </div>`;
  }
  html += '</div>';

  // Backend distribution bar
  html += '<div class="card"><h3>🔧 Distribution des backends</h3>';
  html += '<div style="display:flex;height:32px;border-radius:6px;overflow:hidden;margin-top:12px">';
  for (const [name, count] of entries) {
    const color = backendColors[name] || 'var(--fg2)';
    const w = pct(count, total);
    html += `<div style="width:${w}%;background:${color};display:flex;
      align-items:center;justify-content:center;font-size:.75rem;font-weight:600;
      color:#0d1117;min-width:${w > 5 ? 0 : 40}px"
      title="${name}: ${count} (${w}%)">${w > 8 ? name : ''}</div>`;
  }
  html += '</div></div>';

  // Trace durations per backend
  const durations = {};
  for (const t of D.traces) {
    const b = t.backend || 'unknown';
    if (!durations[b]) durations[b] = [];
    if (t.duration_ms) durations[b].push(t.duration_ms);
  }
  if (Object.keys(durations).length) {
    html += '<div class="card"><h3>⏱️ Durées par backend</h3><table class="tbl"><thead><tr>';
    html += '<th>Backend</th><th>Min</th><th>Moy</th><th>Max</th><th>Exécutions</th>';
    html += '</tr></thead><tbody>';
    for (const [b, durs] of Object.entries(durations)) {
      if (!durs.length) continue;
      const min = Math.min(...durs);
      const max = Math.max(...durs);
      const avg = Math.round(durs.reduce((a, v) => a + v, 0) / durs.length);
      html += `<tr><td>${esc(b)}</td><td>${min}ms</td><td>${avg}ms</td>
        <td>${max}ms</td><td>${durs.length}</td></tr>`;
    }
    html += '</tbody></table></div>';
  }

  el.innerHTML = html;
}

// ── Render all ──────────────────────────────────────────────
renderOverview();
renderDAG();
renderTimeline();
renderTraces();
renderBackends();

// ── Auto-refresh (if file served) ───────────────────────────
let lastMod = '';
setInterval(async () => {
  try {
    const r = await fetch(location.href, { method: 'HEAD' });
    const mod = r.headers.get('last-modified') || '';
    if (lastMod && mod !== lastMod) location.reload();
    lastMod = mod;
  } catch(_) {}
}, 3000);
</script>
</body>
</html>"""


# ── CLI ──────────────────────────────────────────────────────────────────────


def cmd_generate(args: argparse.Namespace) -> int:
    """Génère le dashboard HTML."""
    root = Path(args.project_root).resolve()
    data = load_all(root)
    html = generate_html(data)

    out_dir = root / "_grimoire-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / OUTPUT_FILE
    out_file.write_text(html, encoding="utf-8")

    if args.json:
        print(data_to_json(data))
    else:
        print(f"Dashboard généré : {out_file}")
        print(format_status_text(data))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Génère + lance un serveur local."""
    root = Path(args.project_root).resolve()
    port = args.port

    # Generate initial
    data = load_all(root)
    out_dir = root / "_grimoire-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / OUTPUT_FILE

    html = generate_html(data)
    out_file.write_text(html, encoding="utf-8")

    # Background refresh thread
    def refresh_loop():
        while True:
            time.sleep(5)
            try:
                new_data = load_all(root)
                new_html = generate_html(new_data)
                out_file.write_text(new_html, encoding="utf-8")
            except Exception as e:
                _log.warning("Refresh error: %s", e)

    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()

    # Serve
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out_dir), **kw)
        def log_message(self, fmt, *a):
            _log.info(fmt, *a)

    print(f"HPE Monitor : http://localhost:{port}/{OUTPUT_FILE}")
    print(format_status_text(data))
    print("Auto-refresh toutes les 5s. Ctrl+C pour arrêter.")

    server = http.server.HTTPServer(("", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Affiche un résumé texte."""
    root = Path(args.project_root).resolve()
    data = load_all(root)

    if args.json:
        print(data_to_json(data))
    else:
        print(format_status_text(data))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Exporte en JSON."""
    root = Path(args.project_root).resolve()
    data = load_all(root)
    print(json.dumps(json.loads(data_to_json(data)), indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        prog="hpe-monitor",
        description="HPE Monitor — Dashboard de visualisation des plans HPE",
    )
    parser.add_argument("--version", action="version",
                        version=f"hpe-monitor {HPE_MONITOR_VERSION}")
    parser.add_argument("--project-root", default=".",
                        help="Racine du projet Grimoire")
    parser.add_argument("--json", action="store_true",
                        help="Sortie JSON")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("generate", help="Génère le dashboard HTML")
    sp_serve = sub.add_parser("serve", help="Génère + serveur local")
    sp_serve.add_argument("--port", type=int, default=8421)
    sub.add_parser("status", help="Résumé texte des plans")
    sub.add_parser("export", help="Export JSON")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    dispatch = {
        "generate": cmd_generate,
        "serve": cmd_serve,
        "status": cmd_status,
        "export": cmd_export,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
