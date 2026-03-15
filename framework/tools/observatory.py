#!/usr/bin/env python3
"""
observatory.py — Grimoire Observatory: Interactive Visual Dashboard.
================================================================

Génère un dashboard HTML interactif *autoporté* (single-file, zero CDN)
à partir des données Grimoire : traces, event-log, agent-graph, shared-state.

Vues :
  1. **Timeline**    — Flux chronologique des échanges inter-agents (swimlanes)
  2. **DAG**         — Graphe de tâches parallèles/séquentielles (HPE status)
  3. **Agent Graph** — Réseau relationnel des agents (ARG BM-57)
  4. **Trace Log**   — Tableau filtrable de toutes les entrées Grimoire_TRACE
  5. **Metrics**     — KPIs : trust scores, throughput, parallélisme

Modes :
  generate  — Génère le fichier HTML dans _grimoire-output/
  serve     — Génère + lance un serveur local avec auto-reload
  export    — Exporte les données parsées en JSON

Usage :
  python3 observatory.py --project-root . generate
  python3 observatory.py --project-root . serve --port 8420
  python3 observatory.py --project-root . export > data.json

Stdlib only — aucune dépendance externe.

Sources de données :
  - _grimoire-output/Grimoire_TRACE.md          (BM-28)
  - _grimoire-output/.event-log.jsonl        (BM-59 ELSS)
  - _grimoire-output/.agent-graph.yaml       (BM-57 ARG)
  - _grimoire-output/.shared-state.yaml      (BM-59 ELSS)
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

OBSERVATORY_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

OUTPUT_DIR = "_grimoire-output"
TRACE_FILE = "Grimoire_TRACE.md"
EVENT_LOG_FILE = ".event-log.jsonl"
AGENT_GRAPH_FILE = ".agent-graph.yaml"
SHARED_STATE_FILE = ".shared-state.yaml"
OBSERVATORY_HTML = "observatory.html"


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class TraceEntry:
    """Parsed entry from Grimoire_TRACE.md."""
    timestamp: str = ""
    agent: str = ""
    event_type: str = ""
    payload: str = ""
    session: str = ""


@dataclass
class EventEntry:
    """Parsed entry from .event-log.jsonl."""
    id: str = ""
    ts: str = ""
    agent: str = ""
    type: str = ""
    payload: dict = field(default_factory=dict)
    trace_id: str = ""
    seq: int = 0
    correlation_id: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class AgentNode:
    """Agent node from .agent-graph.yaml."""
    id: str = ""
    persona: str = ""
    capabilities: list[str] = field(default_factory=list)
    emergent_capabilities: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class AgentRelationship:
    """Edge in the agent graph."""
    from_agent: str = ""
    to_agent: str = ""
    type: str = ""
    strength: float = 0.0
    interactions: int = 0
    avg_trust: float = 0.0


@dataclass
class ObservatoryData:
    """All parsed data for the observatory."""
    traces: list[TraceEntry] = field(default_factory=list)
    events: list[EventEntry] = field(default_factory=list)
    agents: list[AgentNode] = field(default_factory=list)
    relationships: list[AgentRelationship] = field(default_factory=list)
    shared_state: dict = field(default_factory=dict)
    sessions: list[str] = field(default_factory=list)
    agent_ids: list[str] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)


# ── Parsers ──────────────────────────────────────────────────────────────────

# Regex for Grimoire_TRACE.md line format:
# [2026-02-27T14:32:01Z] [dev/Amelia]       [ACTION:implement]   story: US-042 ...
_TRACE_RE = re.compile(
    r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)",
)
_SESSION_RE = re.compile(r"^## Session\s+(\S+)")


def parse_trace(path: Path) -> tuple[list[TraceEntry], list[str]]:
    """Parse Grimoire_TRACE.md into structured entries."""
    entries: list[TraceEntry] = []
    sessions: list[str] = []
    current_session = "unknown"

    if not path.exists():
        return entries, sessions

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or (line.startswith("#") and not line.startswith("## Session")):
            sm = _SESSION_RE.match(line)
            if sm:
                current_session = sm.group(1)
                if current_session not in sessions:
                    sessions.append(current_session)
            continue

        sm = _SESSION_RE.match(line)
        if sm:
            current_session = sm.group(1)
            if current_session not in sessions:
                sessions.append(current_session)
            continue

        m = _TRACE_RE.match(line)
        if m:
            entries.append(TraceEntry(
                timestamp=m.group(1).strip(),
                agent=m.group(2).strip(),
                event_type=m.group(3).strip(),
                payload=m.group(4).strip(),
                session=current_session,
            ))

    return entries, sessions


def parse_event_log(path: Path) -> list[EventEntry]:
    """Parse .event-log.jsonl into structured events."""
    events: list[EventEntry] = []
    if not path.exists():
        return events

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(EventEntry(
            id=raw.get("id", ""),
            ts=raw.get("ts", ""),
            agent=raw.get("agent", ""),
            type=raw.get("type", ""),
            payload=raw.get("payload", {}),
            trace_id=raw.get("trace_id", ""),
            seq=raw.get("seq", 0),
            correlation_id=raw.get("correlation_id", ""),
            tags=raw.get("tags", []),
        ))

    return events


def _parse_yaml_simple(text: str) -> dict:
    """Minimal YAML parser for the subset used in Grimoire (no external deps).

    Handles:
      - key: "value" / key: value / key: 123 / key: 0.5
      - key: [...] (inline lists)
      - Nested indentation (2-space)
      - Comments (#)
      - Sequences (- item)

    NOT a full YAML parser — covers the agent-graph.yaml and shared-state.yaml
    schemas specifically.
    """
    result: dict = {}
    # Stack of (indent, container, parent_key, parent_container)
    # parent_key/parent_container allow converting dict→list when first "- " found
    stack: list[tuple[int, dict | list, str | None, dict | list | None]] = [
        (-1, result, None, None),
    ]

    for raw_line in text.splitlines():
        stripped = raw_line.split("#")[0].rstrip()  # remove comments
        if not stripped:
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # Pop stack to find parent at correct indent level
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        _, current, _, _ = stack[-1]

        # Sequence item: "- value" or "- key: value"
        if stripped.lstrip().startswith("- "):
            item_text = stripped.lstrip()[2:].strip()

            # Determine target list
            if isinstance(current, list):
                target_list = current
            elif isinstance(current, dict):
                # Find the key in parent that points to current (empty dict → convert to list)
                # Search stack for the entry that pushed current
                p_key = stack[-1][2]
                p_container = stack[-1][3]
                if p_key is not None and isinstance(p_container, dict):
                    # Convert the empty-dict placeholder to a list
                    if isinstance(p_container[p_key], dict) and not p_container[p_key]:
                        new_list: list = []
                        p_container[p_key] = new_list
                        # Replace current in stack
                        stack[-1] = (stack[-1][0], new_list, p_key, p_container)
                        target_list = new_list
                    elif isinstance(p_container[p_key], list):
                        target_list = p_container[p_key]
                    else:
                        # Last key's value might be the list
                        last_key = list(current.keys())[-1] if current else None
                        if last_key is not None and isinstance(current[last_key], list):
                            target_list = current[last_key]
                        else:
                            target_list = []
                            if last_key is not None:
                                current[last_key] = target_list
                else:
                    # Fallback: find last key whose value is a list
                    last_key = list(current.keys())[-1] if current else None
                    if last_key is not None and isinstance(current[last_key], list):
                        target_list = current[last_key]
                    else:
                        target_list = []
                        if last_key is not None:
                            current[last_key] = target_list
            else:
                continue

            # "- key: value" pattern
            if ":" in item_text and not item_text.startswith("{") and not item_text.startswith("["):
                kv_parts = item_text.split(":", 1)
                k = kv_parts[0].strip()
                v = _yaml_value(kv_parts[1].strip())
                item_dict: dict = {k: v}
                target_list.append(item_dict)
                # Push at content indent (dash_indent + 2) but use dash_indent + 1
                # so sub-keys at dash_indent+2 won't pop this entry (pop uses >=).
                content_indent = indent + 1
                stack.append((content_indent, item_dict, None, None))
            else:
                target_list.append(_yaml_value(item_text))
            continue

        # Key: value
        if ":" in stripped:
            key_part, val_part = stripped.split(":", 1)
            key = key_part.strip()
            val_str = val_part.strip()

            if not val_str:
                # Block mapping or sequence will follow
                new_container: dict | list = {}
                if isinstance(current, dict):
                    current[key] = new_container
                stack.append((indent, new_container, key, current if isinstance(current, dict) else None))
            elif val_str.startswith("[") and val_str.endswith("]"):
                # Inline list
                inner = val_str[1:-1]
                items = [_yaml_value(i.strip()) for i in inner.split(",") if i.strip()]
                if isinstance(current, dict):
                    current[key] = items
            else:
                if isinstance(current, dict):
                    current[key] = _yaml_value(val_str)

    return result


def _yaml_value(s: str):
    """Convert a YAML scalar string to a Python value."""
    if not s:
        return ""
    # Quoted string
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # Boolean
    if s.lower() in ("true", "yes"):
        return True
    if s.lower() in ("false", "no"):
        return False
    if s.lower() in ("null", "~"):
        return None
    # Number
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        pass
    return s


def parse_agent_graph(path: Path) -> tuple[list[AgentNode], list[AgentRelationship]]:
    """Parse .agent-graph.yaml."""
    agents: list[AgentNode] = []
    rels: list[AgentRelationship] = []

    if not path.exists():
        return agents, rels

    data = _parse_yaml_simple(path.read_text(encoding="utf-8"))
    ag = data.get("agent_graph", data)

    # Agents
    agents_raw = ag.get("agents", {})
    if isinstance(agents_raw, dict):
        for aid, info in agents_raw.items():
            if not isinstance(info, dict):
                continue
            agents.append(AgentNode(
                id=str(aid),
                persona=str(info.get("persona", "")),
                capabilities=info.get("static_capabilities", []),
                emergent_capabilities=info.get("emergent_capabilities", []),
                metrics=info.get("metrics", {}),
            ))

    # Relationships
    rels_raw = ag.get("relationships", [])
    if isinstance(rels_raw, list):
        for r in rels_raw:
            if not isinstance(r, dict):
                continue
            rels.append(AgentRelationship(
                from_agent=str(r.get("from", "")),
                to_agent=str(r.get("to", "")),
                type=str(r.get("type", "")),
                strength=float(r.get("strength", 0)),
                interactions=int(r.get("interactions", 0)),
                avg_trust=float(r.get("avg_outcome_trust", 0)),
            ))

    return agents, rels


def parse_shared_state(path: Path) -> dict:
    """Parse .shared-state.yaml."""
    if not path.exists():
        return {}
    return _parse_yaml_simple(path.read_text(encoding="utf-8"))


# ── Aggregate ────────────────────────────────────────────────────────────────


def _find_output_dir(project_root: Path) -> Path:
    """Find the output directory — supports both Grimoire and BMAD layouts.

    Prefers directories that actually contain trace data.
    """
    candidates = [
        project_root / OUTPUT_DIR,          # _grimoire-output/
        project_root / "_bmad-output",      # BMAD custom layout
    ]
    # First pass: find one with trace data
    for d in candidates:
        if d.is_dir():
            for name in (TRACE_FILE, "BMAD_TRACE.md"):
                if (d / name).exists():
                    return d
    # Second pass: just find an existing directory
    for d in candidates:
        if d.is_dir():
            return d
    # Default to standard
    return project_root / OUTPUT_DIR


def _find_trace(out_dir: Path) -> Path:
    """Find trace file — supports multiple naming conventions."""
    candidates = [
        out_dir / TRACE_FILE,               # Grimoire_TRACE.md
        out_dir / "BMAD_TRACE.md",          # BMAD custom naming
    ]
    for f in candidates:
        if f.exists():
            return f
    return out_dir / TRACE_FILE


def load_all(project_root: Path, output_dir: Path | None = None) -> ObservatoryData:
    """Load all Grimoire data sources."""
    out_dir = output_dir or _find_output_dir(project_root)

    traces, sessions = parse_trace(_find_trace(out_dir))
    events = parse_event_log(out_dir / EVENT_LOG_FILE)
    agents, rels = parse_agent_graph(out_dir / AGENT_GRAPH_FILE)
    shared = parse_shared_state(out_dir / SHARED_STATE_FILE)

    # Collect unique agent IDs and event types
    agent_ids = sorted({t.agent for t in traces} | {e.agent for e in events} | {a.id for a in agents})
    event_types = sorted({t.event_type for t in traces} | {e.type for e in events})

    return ObservatoryData(
        traces=traces,
        events=events,
        agents=agents,
        relationships=rels,
        shared_state=shared,
        sessions=sessions,
        agent_ids=agent_ids,
        event_types=event_types,
    )


def data_to_json(data: ObservatoryData) -> str:
    """Serialize observatory data to JSON for embedding in HTML."""
    return json.dumps({
        "traces": [asdict(t) for t in data.traces],
        "events": [asdict(e) for e in data.events],
        "agents": [asdict(a) for a in data.agents],
        "relationships": [asdict(r) for r in data.relationships],
        "shared_state": data.shared_state,
        "sessions": data.sessions,
        "agent_ids": data.agent_ids,
        "event_types": data.event_types,
    }, ensure_ascii=False, indent=None)


# ── HTML Template ────────────────────────────────────────────────────────────


def generate_html(data: ObservatoryData, *, auto_refresh: bool = False) -> str:
    """Generate the self-contained observatory HTML."""
    json_data = data_to_json(data)
    html = _HTML_TEMPLATE.replace("__Grimoire_DATA__", json_data)
    # auto_refresh is handled by JS HEAD-check (preserves tab/scroll state)
    # No meta refresh tag — it would cause full reloads losing all state
    return html.replace("__AUTO_REFRESH__", "")


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grimoire Observatory</title>
__AUTO_REFRESH__
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
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
header h1{font-size:1.2rem;font-weight:600;display:flex;align-items:center;gap:8px}
.header-stats{display:flex;gap:14px;margin-left:auto;font-size:.82rem;color:var(--fg2)}
.header-stats .n{color:var(--accent);font-weight:600}
.live-badge{background:#238636;color:#fff;font-size:.7rem;padding:2px 8px;border-radius:10px;animation:livePulse 2s infinite}
@keyframes livePulse{0%,100%{opacity:1}50%{opacity:.5}}
.kbd{display:inline-block;font-size:.65rem;padding:1px 5px;border:1px solid var(--border);border-radius:3px;color:var(--fg2);font-family:var(--mono);margin-left:4px;vertical-align:middle}

/* ── Tabs ───────────────────────────────── */
.tabs{display:flex;background:var(--bg2);border-bottom:1px solid var(--border);padding:0 24px;overflow-x:auto}
.tab{padding:10px 18px;cursor:pointer;color:var(--fg2);font-size:.85rem;border-bottom:2px solid transparent;transition:all .12s;white-space:nowrap;user-select:none}
.tab:hover{color:var(--fg);background:var(--bg3)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}

/* ── Main ───────────────────────────────── */
main{flex:1;overflow-y:auto}
.view{display:none;padding:20px 24px}
.view.active{display:block}

/* ── Filters ────────────────────────────── */
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;padding:10px 12px;background:var(--bg2);border-radius:6px;border:1px solid var(--border)}
.filters label{font-size:.78rem;color:var(--fg2);display:flex;flex-direction:column;gap:3px}
.filters select,.filters input{background:var(--bg3);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:5px 8px;font-size:.82rem;font-family:var(--mono)}
.filters select:focus,.filters input:focus{outline:none;border-color:var(--accent)}

/* ── Timeline ───────────────────────────── */
.tl-entry{display:grid;grid-template-columns:120px 140px 1fr;gap:10px;padding:6px 10px;border-left:3px solid var(--border);margin-left:0;cursor:pointer;transition:background .1s}
.tl-entry:hover{background:var(--bg2)}
.tl-entry .ts{font-family:var(--mono);font-size:.75rem;color:var(--fg2);white-space:nowrap}
.tl-entry .agent{font-weight:600;font-size:.82rem}
.tl-entry .ev-type{font-family:var(--mono);font-size:.75rem;padding:1px 7px;border-radius:10px;display:inline-block}
.tl-entry .payload{color:var(--fg2);font-size:.82rem;word-break:break-word}

.ag-dev{color:var(--green);border-left-color:var(--green)}.ag-dev .ev-type{background:rgba(63,185,80,.12);color:var(--green)}
.ag-qa{color:var(--purple);border-left-color:var(--purple)}.ag-qa .ev-type{background:rgba(188,140,255,.12);color:var(--purple)}
.ag-architect{color:var(--cyan);border-left-color:var(--cyan)}.ag-architect .ev-type{background:rgba(57,210,192,.12);color:var(--cyan)}
.ag-pm{color:var(--orange);border-left-color:var(--orange)}.ag-pm .ev-type{background:rgba(240,136,62,.12);color:var(--orange)}
.ag-analyst{color:var(--yellow);border-left-color:var(--yellow)}.ag-analyst .ev-type{background:rgba(210,153,34,.12);color:var(--yellow)}
.ag-sm{color:var(--pink);border-left-color:var(--pink)}.ag-sm .ev-type{background:rgba(247,120,186,.12);color:var(--pink)}
.ag-orchestr{color:var(--accent);border-left-color:var(--accent)}.ag-orchestr .ev-type{background:rgba(88,166,255,.12);color:var(--accent)}
.ag-techwr{color:#7ee787;border-left-color:#7ee787}.ag-techwr .ev-type{background:rgba(126,231,135,.12);color:#7ee787}
.ag-default{color:var(--fg);border-left-color:var(--fg2)}.ag-default .ev-type{background:var(--bg3);color:var(--fg2)}

/* ── Swimlane ───────────────────────────── */
.sl-wrapper{position:relative;overflow:auto;background:var(--bg)}
.sl-header{display:flex;position:sticky;top:0;z-index:10;background:var(--bg2);border-bottom:1px solid var(--border)}
.sl-col-hdr{text-align:center;font-size:.78rem;font-weight:600;padding:8px 0;border-right:1px solid var(--border);min-width:160px;flex-shrink:0}
.sl-col-hdr .persona{font-size:.68rem;color:var(--fg2);font-weight:400}
.sl-body{position:relative;min-height:400px}
.sl-lane{position:absolute;top:0;bottom:0;border-right:1px solid rgba(48,54,61,.5);min-width:160px}
.sl-event{position:absolute;display:flex;align-items:center;gap:6px;padding:3px 8px;border-radius:4px;font-size:.75rem;cursor:pointer;z-index:2;transition:transform .1s,box-shadow .1s;white-space:nowrap;max-width:150px;overflow:hidden;text-overflow:ellipsis}
.sl-event:hover{transform:scale(1.08);box-shadow:0 2px 8px rgba(0,0,0,.5);z-index:5;max-width:none}
.sl-event .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sl-event .sl-label{overflow:hidden;text-overflow:ellipsis}
.sl-time-label{position:absolute;left:4px;font-family:var(--mono);font-size:.68rem;color:var(--fg2);width:54px;text-align:right}
.sl-svg{position:absolute;top:0;left:0;pointer-events:none;z-index:1}
.sl-svg line{stroke-width:1.5;opacity:.6}
.sl-svg .arrow-head{fill-opacity:.6}
.sl-parallel-band{position:absolute;left:60px;right:0;background:rgba(88,166,255,.03);border-top:1px dashed rgba(88,166,255,.15);border-bottom:1px dashed rgba(88,166,255,.15);z-index:0}
.sl-parallel-label{position:absolute;right:8px;font-size:.62rem;color:var(--accent);opacity:.6}

/* ── DAG Gantt ──────────────────────────── */
.gantt-wrapper{overflow-x:auto;background:var(--bg)}
.gantt-header{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.gantt-header h3{font-size:.95rem;font-weight:600}
.gantt-legend{display:flex;gap:12px;font-size:.75rem;color:var(--fg2);margin-left:auto}
.gantt-legend .gl-item{display:flex;align-items:center;gap:4px}
.gantt-legend .gl-dot{width:10px;height:3px;border-radius:2px}
.gantt-chart{position:relative;min-height:200px}
.gantt-row{display:flex;align-items:center;height:40px;border-bottom:1px solid var(--border)}
.gantt-label{width:180px;flex-shrink:0;padding:0 12px;font-size:.8rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gantt-label .gl-agent{font-size:.7rem;color:var(--fg2)}
.gantt-track{flex:1;position:relative;height:100%}
.gantt-bar{position:absolute;height:22px;top:9px;border-radius:4px;display:flex;align-items:center;padding:0 8px;font-size:.7rem;font-family:var(--mono);cursor:pointer;transition:transform .1s}
.gantt-bar:hover{transform:scaleY(1.3)}
.gantt-bar.done{background:rgba(63,185,80,.25);border:1px solid var(--green);color:var(--green)}
.gantt-bar.running{background:rgba(210,153,34,.2);border:1px solid var(--yellow);color:var(--yellow);animation:barPulse 2s infinite}
.gantt-bar.waiting{background:var(--bg3);border:1px solid var(--border);color:var(--fg2)}
.gantt-bar.failed{background:rgba(248,81,73,.2);border:1px solid var(--red);color:var(--red)}
@keyframes barPulse{0%,100%{opacity:1}50%{opacity:.6}}
.gantt-dep-svg{position:absolute;top:0;left:180px;right:0;pointer-events:none}
.gantt-dep-svg line{stroke:var(--fg2);stroke-width:1;stroke-dasharray:4 3;opacity:.4;vector-effect:non-scaling-stroke}
.gantt-time-axis{display:flex;margin-left:180px;border-top:1px solid var(--border);padding-top:4px}
.gantt-time-tick{font-size:.65rem;color:var(--fg2);font-family:var(--mono)}

/* ── Agent Network ──────────────────────── */
.graph-section{display:grid;grid-template-columns:1fr 300px;gap:16px}
.graph-canvas{height:480px;background:var(--bg2);border-radius:8px;border:1px solid var(--border);overflow:hidden}
.graph-canvas canvas{width:100%;height:100%}
.graph-sidebar{display:flex;flex-direction:column;gap:12px;overflow-y:auto;max-height:480px}
.agent-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px;cursor:pointer;transition:border-color .15s}
.agent-card:hover,.agent-card.selected{border-color:var(--accent)}
.agent-card .ac-header{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.agent-card .ac-avatar{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.9rem;font-weight:700;border:2px solid}
.agent-card .ac-name{font-weight:600;font-size:.85rem}
.agent-card .ac-persona{font-size:.75rem;color:var(--fg2)}
.agent-card .ac-stats{display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:.72rem}
.agent-card .ac-stat{display:flex;justify-content:space-between}
.agent-card .ac-stat .label{color:var(--fg2)}
.agent-card .ac-caps{display:flex;flex-wrap:wrap;gap:3px;margin-top:6px}
.agent-card .ac-cap{font-size:.65rem;padding:1px 6px;border-radius:8px;background:var(--bg3);color:var(--fg2)}
.graph-legend{display:flex;gap:14px;margin-top:8px;font-size:.75rem;color:var(--fg2)}
.graph-legend .item{display:flex;align-items:center;gap:4px}
.graph-legend .dot{width:10px;height:10px;border-radius:50%}

/* ── Trace Table ────────────────────────── */
.trace-table{width:100%;border-collapse:collapse;font-size:.82rem}
.trace-table th{text-align:left;padding:7px 10px;background:var(--bg2);color:var(--fg2);font-size:.75rem;text-transform:uppercase;letter-spacing:.5px;position:sticky;top:0;border-bottom:1px solid var(--border)}
.trace-table td{padding:5px 10px;border-bottom:1px solid var(--border)}
.trace-table tr{cursor:pointer;transition:background .1s}
.trace-table tr:hover td{background:var(--bg2)}
.mono{font-family:var(--mono);font-size:.78rem}

/* ── Metrics ────────────────────────────── */
.metrics-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin-bottom:20px}
.metric-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px}
.metric-card .mc-label{font-size:.72rem;color:var(--fg2);text-transform:uppercase;letter-spacing:.4px}
.metric-card .mc-value{font-size:1.8rem;font-weight:700;margin:2px 0;color:var(--accent)}
.metric-card .mc-detail{font-size:.75rem;color:var(--fg2)}
.metric-card.good .mc-value{color:var(--green)}
.metric-card.warn .mc-value{color:var(--yellow)}
.metric-card.bad .mc-value{color:var(--red)}
.metrics-section{margin-top:20px}
.metrics-section h3{font-size:.9rem;margin-bottom:10px;color:var(--fg2)}
.bar-chart{display:flex;flex-direction:column;gap:4px}
.bar-row{display:flex;align-items:center;gap:8px;font-size:.78rem}
.bar-row .bar-label{width:160px;text-align:right;color:var(--fg2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-row .bar-track{flex:1;height:18px;background:var(--bg3);border-radius:3px;overflow:hidden}
.bar-row .bar-fill{height:100%;border-radius:3px;display:flex;align-items:center;padding:0 6px;font-size:.68rem;color:#fff;font-family:var(--mono);min-width:24px}
.bar-row .bar-count{width:40px;font-family:var(--mono);color:var(--fg2);font-size:.75rem}

/* ── Detail Drawer ──────────────────────── */
.drawer-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:200}
.drawer-overlay.open{display:block}
.drawer{position:fixed;right:0;top:0;bottom:0;width:440px;max-width:90vw;background:var(--bg2);border-left:1px solid var(--border);z-index:201;transform:translateX(100%);transition:transform .2s ease;overflow-y:auto;padding:20px}
.drawer.open{transform:translateX(0)}
.drawer-close{position:absolute;top:12px;right:12px;background:none;border:none;color:var(--fg2);font-size:1.4rem;cursor:pointer;padding:4px 8px;border-radius:4px}
.drawer-close:hover{background:var(--bg3);color:var(--fg)}
.drawer h2{font-size:1rem;margin-bottom:16px;padding-right:32px}
.drawer .d-section{margin-bottom:14px}
.drawer .d-label{font-size:.72rem;color:var(--fg2);text-transform:uppercase;margin-bottom:4px}
.drawer .d-value{font-size:.85rem;word-break:break-word}
.drawer .d-value pre{background:var(--bg);padding:8px;border-radius:4px;overflow-x:auto;font-family:var(--mono);font-size:.78rem;white-space:pre-wrap}
.drawer .d-tag{display:inline-block;font-size:.7rem;padding:1px 6px;border-radius:8px;background:var(--bg3);color:var(--fg2);margin:2px}
.drawer .d-related{margin-top:12px}
.drawer .d-related-item{padding:6px 8px;border-left:2px solid var(--border);margin-bottom:4px;font-size:.8rem;cursor:pointer}
.drawer .d-related-item:hover{background:var(--bg3)}

/* ── Empty State ────────────────────────── */
.empty-state{text-align:center;padding:60px 24px;color:var(--fg2)}
.empty-state .icon{font-size:2.5rem;margin-bottom:12px}
.empty-state h2{color:var(--fg);margin-bottom:6px;font-size:1rem}
.empty-state p{max-width:440px;margin:0 auto;line-height:1.5;font-size:.85rem}
.empty-state code{background:var(--bg3);padding:1px 5px;border-radius:3px;font-family:var(--mono);font-size:.8rem}

::-webkit-scrollbar{width:7px;height:7px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--bg3);border-radius:4px}

/* ── Overview ───────────────────────────── */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(195px,1fr));gap:14px;margin-bottom:20px}
.ov-card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;transition:border-color .15s}
.ov-card:hover{border-color:var(--accent)}
.ov-card .ov-icon{font-size:1.5rem;margin-bottom:4px}
.ov-card .ov-label{font-size:.72rem;color:var(--fg2);text-transform:uppercase;letter-spacing:.3px}
.ov-card .ov-value{font-size:2rem;font-weight:700;color:var(--accent);margin:2px 0}
.ov-card .ov-sub{font-size:.75rem;color:var(--fg2)}
.ov-card.good .ov-value{color:var(--green)}
.ov-card.warn .ov-value{color:var(--yellow)}
.ov-card.bad .ov-value{color:var(--red)}
.ov-section{margin-top:22px}
.ov-section h3{font-size:.92rem;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.ov-two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.ov-two-col{grid-template-columns:1fr}}
.trust-gauge{position:relative;width:130px;height:130px;margin:0 auto}
.trust-gauge svg{transform:rotate(-90deg)}
.trust-gauge .gauge-text{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:700;font-size:1.5rem}
.trust-gauge .gauge-label{font-size:.68rem;color:var(--fg2);font-weight:400}
.sparkbar{display:flex;align-items:flex-end;gap:2px;height:48px;padding:4px 0}
.sparkbar .bar{flex:1;background:var(--accent);border-radius:2px 2px 0 0;min-width:4px;transition:height .2s;opacity:.7}
.sparkbar .bar:hover{opacity:1}
.ov-decisions{display:flex;flex-direction:column;gap:6px;max-height:280px;overflow-y:auto}
.ov-decision{padding:8px 10px;background:var(--bg3);border-radius:6px;border-left:3px solid var(--orange);font-size:.82rem;cursor:pointer;transition:background .1s}
.ov-decision:hover{background:var(--bg2)}
.ov-decision .d-agent{font-weight:600;font-size:.78rem}
.ov-decision .d-time{font-size:.68rem;color:var(--fg2);font-family:var(--mono)}
.ov-alerts{display:flex;flex-direction:column;gap:6px}
.ov-alert{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;font-size:.82rem}
.ov-alert.hup{background:rgba(210,153,34,.1);border-left:3px solid var(--yellow)}
.ov-alert.error{background:rgba(248,81,73,.1);border-left:3px solid var(--red)}
.ov-alert.info{background:rgba(88,166,255,.1);border-left:3px solid var(--accent)}
.ov-workload{display:flex;flex-direction:column;gap:6px}
.ov-work-row{display:flex;align-items:center;gap:8px;font-size:.78rem}
.ov-work-row .wl-name{width:100px;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ov-work-row .wl-bar{flex:1;height:14px;background:var(--bg3);border-radius:3px;overflow:hidden}
.ov-work-row .wl-fill{height:100%;border-radius:3px;display:flex;align-items:center;padding:0 5px;font-size:.64rem;color:#fff;font-family:var(--mono)}
.ov-work-row .wl-score{width:32px;font-family:var(--mono);font-size:.72rem;color:var(--fg2)}
.ov-sessions{display:flex;flex-direction:column;gap:8px}
.ov-sess{display:flex;align-items:center;gap:10px;padding:8px 10px;background:var(--bg2);border-radius:6px;border:1px solid var(--border);cursor:pointer;transition:border-color .15s}
.ov-sess:hover{border-color:var(--accent)}
.ov-sess .s-id{font-weight:600;font-size:.85rem;color:var(--accent);min-width:100px}
.ov-sess .s-stats{font-size:.75rem;color:var(--fg2);display:flex;gap:12px}
.ov-sess .s-bar{flex:1;height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;display:flex}
.ov-sess .s-bar .s-seg{height:100%}
.obs-tooltip{position:fixed;z-index:300;pointer-events:none;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:.78rem;max-width:340px;box-shadow:0 4px 16px rgba(0,0,0,.4);opacity:0;transition:opacity .12s}
.obs-tooltip.visible{opacity:1}
.obs-tooltip .tt-agent{font-weight:600;margin-bottom:3px}
.obs-tooltip .tt-type{font-family:var(--mono);font-size:.72rem;margin-bottom:3px}
.obs-tooltip .tt-payload{color:var(--fg2);font-size:.74rem;word-break:break-word}
.global-search{position:relative}
.global-search input{background:var(--bg3);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:5px 10px 5px 28px;font-size:.82rem;width:180px;transition:width .2s,border-color .15s;font-family:var(--font)}
.global-search input:focus{width:260px;border-color:var(--accent);outline:none}
.global-search .search-icon{position:absolute;left:8px;top:50%;transform:translateY(-50%);color:var(--fg2);font-size:.82rem;pointer-events:none}
.global-search .search-results{position:absolute;top:100%;left:0;right:-60px;max-height:360px;overflow-y:auto;background:var(--bg2);border:1px solid var(--border);border-radius:0 0 6px 6px;display:none;z-index:150}
.global-search .search-results.open{display:block}
.global-search .sr-item{padding:6px 10px;cursor:pointer;font-size:.78rem;border-bottom:1px solid var(--border);transition:background .1s}
.global-search .sr-item:hover{background:var(--bg3)}
.global-search .sr-item .sr-agent{font-weight:600}
.global-search .sr-item .sr-type{font-family:var(--mono);font-size:.7rem;color:var(--fg2)}
.btn-export{background:var(--bg3);color:var(--fg2);border:1px solid var(--border);border-radius:6px;padding:4px 10px;font-size:.78rem;cursor:pointer;transition:all .15s;font-family:var(--font)}
.btn-export:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.handoff-chain{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px;margin-top:16px}
.handoff-chain h4{font-size:.85rem;margin-bottom:10px;color:var(--fg2)}
.hc-flow{display:flex;align-items:center;gap:0;flex-wrap:wrap}
.hc-node{padding:4px 10px;border-radius:14px;font-size:.78rem;font-weight:600;border:2px solid}
.hc-arrow{color:var(--fg2);padding:0 4px;font-size:.85rem}
</style>
</head>
<body>

<header>
  <h1>🔭 Grimoire Observatory</h1>
  <div class="global-search">
    <span class="search-icon">&#128269;</span>
    <input id="global-q" type="text" placeholder="Recherche globale…" autocomplete="off">
    <div class="search-results" id="global-results"></div>
  </div>
  <div class="header-stats">
    <span>Traces: <span class="n" id="stat-traces">0</span></span>
    <span>Events: <span class="n" id="stat-events">0</span></span>
    <span>Agents: <span class="n" id="stat-agents">0</span></span>
    <span>Sessions: <span class="n" id="stat-sessions">0</span></span>
  </div>
  <button class="btn-export" id="btn-export" title="Exporter JSON">&#128229; Export</button>
  <span class="live-badge" id="live-badge" style="display:none">LIVE</span>
</header>

<div class="tabs" id="tabs">
  <div class="tab active" data-view="overview">Overview <span class="kbd">0</span></div>
  <div class="tab" data-view="timeline">Timeline <span class="kbd">1</span></div>
  <div class="tab" data-view="swimlane">Swimlane <span class="kbd">2</span></div>
  <div class="tab" data-view="dag">DAG <span class="kbd">3</span></div>
  <div class="tab" data-view="graph">Network <span class="kbd">4</span></div>
  <div class="tab" data-view="tracelog">Log <span class="kbd">5</span></div>
  <div class="tab" data-view="metrics">Metrics <span class="kbd">6</span></div>
</div>

<main>
  <!-- Overview -->
  <div class="view active" id="view-overview">
    <div class="ov-grid" id="ov-grid"></div>
    <div class="ov-two-col">
      <div>
        <div class="ov-section"><h3>&#128200; Activit&eacute; par session</h3><div id="ov-activity"></div></div>
        <div class="ov-section"><h3>&#128101; Charge agents</h3><div id="ov-workload" class="ov-workload"></div></div>
      </div>
      <div>
        <div class="ov-section"><h3>&#9889; D&eacute;cisions r&eacute;centes</h3><div id="ov-decisions" class="ov-decisions"></div></div>
        <div class="ov-section"><h3>&#9888;&#65039; Alertes</h3><div id="ov-alerts" class="ov-alerts"></div></div>
      </div>
    </div>
    <div class="ov-section"><h3>&#128279; Cha&icirc;nes de Handoff</h3><div id="ov-handoffs"></div></div>
    <div class="ov-section"><h3>&#128218; Sessions</h3><div id="ov-sessions" class="ov-sessions"></div></div>
  </div>

  <!-- Timeline -->
  <div class="view" id="view-timeline">
    <div class="filters">
      <label>Agent <select id="f-tl-agent"><option value="">Tous</option></select></label>
      <label>Type <select id="f-tl-type"><option value="">Tous</option></select></label>
      <label>Session <select id="f-tl-session"><option value="">Toutes</option></select></label>
      <label>Recherche <input id="f-tl-q" type="text" placeholder="mot-clé…"></label>
    </div>
    <div id="timeline-ct"></div>
  </div>

  <!-- Swimlane -->
  <div class="view" id="view-swimlane">
    <div class="filters">
      <label>Session <select id="f-sl-session"><option value="">Toutes</option></select></label>
    </div>
    <div id="swimlane-ct" class="sl-wrapper"></div>
  </div>

  <!-- DAG -->
  <div class="view" id="view-dag">
    <div id="dag-ct"></div>
  </div>

  <!-- Agent Network -->
  <div class="view" id="view-graph">
    <div class="graph-section">
      <div>
        <div class="graph-canvas" id="graph-box"><canvas id="graph-cv"></canvas></div>
        <div class="graph-legend" id="graph-legend"></div>
      </div>
      <div class="graph-sidebar" id="graph-sidebar"></div>
    </div>
  </div>

  <!-- Trace Log -->
  <div class="view" id="view-tracelog">
    <div class="filters">
      <label>Agent <select id="f-log-agent"><option value="">Tous</option></select></label>
      <label>Type <select id="f-log-type"><option value="">Tous</option></select></label>
      <label>Recherche <input id="f-log-q" type="text" placeholder="mot-clé…"></label>
    </div>
    <div style="max-height:72vh;overflow-y:auto">
      <table class="trace-table"><thead><tr><th>Timestamp</th><th>Agent</th><th>Type</th><th>Payload</th><th>Session</th></tr></thead><tbody id="trace-tbody"></tbody></table>
    </div>
  </div>

  <!-- Metrics -->
  <div class="view" id="view-metrics">
    <div class="metrics-grid" id="metrics-grid"></div>
    <div class="metrics-section" id="metrics-charts"></div>
  </div>
</main>

<!-- Detail Drawer -->
<div class="drawer-overlay" id="drawer-overlay"></div>
<div class="drawer" id="drawer">
  <button class="drawer-close" id="drawer-close">&times;</button>
  <div id="drawer-content"></div>
</div>
<div class="obs-tooltip" id="obs-tooltip"></div>

<script>
const DATA = __Grimoire_DATA__;
const $ = (s,e) => (e||document).querySelector(s);
const $$ = (s,e) => [...(e||document).querySelectorAll(s)];
const esc = s => { const d=document.createElement('div');d.textContent=s;return d.innerHTML; };

// ── Global item store for event delegation (no inline onclick) ──
const ITEMS = [];
function storeItem(item) { const idx = ITEMS.length; ITEMS.push(item); return idx; }
document.addEventListener('click', e => {
  const el = e.target.closest('[data-item-idx]');
  if (el) showItemDetail(ITEMS[parseInt(el.dataset.itemIdx)]);
});

// ── Agent Colors ────────────────────────────────────────────
const AG_COLORS = {dev:'#3fb950',qa:'#bc8cff',architect:'#39d2c0',pm:'#f0883e',analyst:'#d29922',sm:'#f778ba',orchestr:'#58a6ff',techwr:'#7ee787',hpe:'#58a6ff',default:'#8b949e'};

function agentKey(agent) {
  const a = (agent||'').toLowerCase();
  if (a.includes('dev') || a.includes('amelia')) return 'dev';
  if (a.includes('qa') || a.includes('quinn')) return 'qa';
  if (a.includes('architect') || a.includes('winston')) return 'architect';
  if (a.includes('pm') || a.includes('john')) return 'pm';
  if (a.includes('analyst') || a.includes('mary')) return 'analyst';
  if (a.includes('sm') || a.includes('bob')) return 'sm';
  if (a.includes('tech') || a.includes('paige')) return 'techwr';
  if (a.includes('orchestr') || a.includes('sog')) return 'orchestr';
  if (a === 'hpe') return 'hpe';
  return 'default';
}
function agentClass(agent) { return 'ag-' + agentKey(agent); }
function agentColor(agent) { return AG_COLORS[agentKey(agent)] || AG_COLORS.default; }

// ── Stats ───────────────────────────────────────────────────
$('#stat-traces').textContent = DATA.traces.length;
$('#stat-events').textContent = DATA.events.length;
$('#stat-agents').textContent = DATA.agent_ids.length;
$('#stat-sessions').textContent = DATA.sessions.length;

// ── Tabs + Keyboard ─────────────────────────────────────────
const TAB_VIEWS = ['overview','timeline','swimlane','dag','graph','tracelog','metrics'];
function switchTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.view===name));
  $$('.view').forEach(v => v.classList.toggle('active', v.id==='view-'+name));
  if (name==='overview') renderOverview();
  if (name==='swimlane') renderSwimlane();
  if (name==='dag') renderDAG();
  if (name==='graph') renderGraph();
  if (name==='metrics') renderMetrics();
}
$$('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.view)));
document.addEventListener('keydown', e => {
  if (e.target.tagName==='INPUT' || e.target.tagName==='SELECT') return;
  const n = parseInt(e.key);
  if (n >= 0 && n <= 6) { e.preventDefault(); switchTab(TAB_VIEWS[n]); }
  if (e.key === 'Escape') closeDrawer();
});

// ── Filters ─────────────────────────────────────────────────
function popSelect(sel, items) { items.forEach(i => { const o=document.createElement('option');o.value=i;o.textContent=i;sel.appendChild(o); }); }
popSelect($('#f-tl-agent'), DATA.agent_ids);
popSelect($('#f-tl-type'), DATA.event_types);
popSelect($('#f-tl-session'), DATA.sessions);
popSelect($('#f-sl-session'), DATA.sessions);
popSelect($('#f-log-agent'), DATA.agent_ids);
popSelect($('#f-log-type'), DATA.event_types);

// ── Drawer ──────────────────────────────────────────────────
function openDrawer(html) {
  $('#drawer-content').innerHTML = html;
  $('#drawer-overlay').classList.add('open');
  $('#drawer').classList.add('open');
}
function closeDrawer() {
  $('#drawer-overlay').classList.remove('open');
  $('#drawer').classList.remove('open');
}
$('#drawer-overlay').addEventListener('click', closeDrawer);
$('#drawer-close').addEventListener('click', closeDrawer);

function showItemDetail(item) {
  let html = `<h2 style="color:${agentColor(item.agent)}">${esc(item.agent)}</h2>`;
  html += `<div class="d-section"><div class="d-label">Timestamp</div><div class="d-value mono">${esc(item.ts)}</div></div>`;
  html += `<div class="d-section"><div class="d-label">Type</div><div class="d-value"><span class="${agentClass(item.agent)}" style="padding:2px 8px;border-radius:8px;font-family:var(--mono)">${esc(item.type)}</span></div></div>`;
  html += `<div class="d-section"><div class="d-label">Payload</div><div class="d-value"><pre>${esc(typeof item.payload === 'object' ? JSON.stringify(item.payload, null, 2) : item.payload)}</pre></div></div>`;
  if (item.session) html += `<div class="d-section"><div class="d-label">Session</div><div class="d-value mono">${esc(item.session)}</div></div>`;
  if (item.tags && item.tags.length) html += `<div class="d-section"><div class="d-label">Tags</div><div class="d-value">${item.tags.map(t => `<span class="d-tag">${esc(t)}</span>`).join(' ')}</div></div>`;

  // Related events
  const related = DATA.traces.filter(t => t.agent === item.agent && t.timestamp !== item.ts).slice(0, 5);
  if (related.length) {
    html += `<div class="d-related"><div class="d-label">Autres actions de ${esc(item.agent)}</div>`;
    related.forEach(r => {
      html += `<div class="d-related-item" style="border-left-color:${agentColor(r.agent)}">${esc(r.event_type)} — ${esc(r.payload.substring(0,60))}</div>`;
    });
    html += `</div>`;
  }
  openDrawer(html);
}

// ── Merge items ─────────────────────────────────────────────
function getAllItems(sessionFilter) {
  let items = [];
  DATA.traces.forEach(t => items.push({ts:t.timestamp, agent:t.agent, type:t.event_type, payload:t.payload, session:t.session, src:'trace'}));
  DATA.events.forEach(e => items.push({ts:e.ts, agent:e.agent, type:e.type, payload:JSON.stringify(e.payload), session:e.trace_id, src:'event', tags:e.tags, correlation_id:e.correlation_id}));
  if (sessionFilter) items = items.filter(i => (i.session||'').includes(sessionFilter));
  // Deduplicate by timestamp+agent+type (trace and event may overlap)
  const seen = new Set();
  items = items.filter(i => { const k = i.ts+'|'+i.agent+'|'+i.type; if (seen.has(k)) return false; seen.add(k); return true; });
  items.sort((a,b) => a.ts.localeCompare(b.ts));
  return items;
}

// ══════════════════════════════════════════════════════════════
// TIMELINE
// ══════════════════════════════════════════════════════════════
function renderTimeline() {
  const ag = $('#f-tl-agent').value, tp = $('#f-tl-type').value, ss = $('#f-tl-session').value, q = ($('#f-tl-q').value||'').toLowerCase();
  let items = getAllItems(ss);
  if (ag) items = items.filter(i => i.agent.includes(ag));
  if (tp) items = items.filter(i => i.type.includes(tp));
  if (q) items = items.filter(i => `${i.agent} ${i.type} ${i.payload}`.toLowerCase().includes(q));

  const ct = $('#timeline-ct');
  if (!items.length) { ct.innerHTML = `<div class="empty-state"><div class="icon">📭</div><h2>Aucune trace</h2><p>Lancez un workflow pour voir l'activité.</p></div>`; return; }
  const shown = items.slice(0, 500);
  ct.innerHTML = shown.map(i => {
    const time = i.ts.replace(/.*T/,'').replace('Z','');
    const idx = storeItem(i);
    return `<div class="tl-entry ${agentClass(i.agent)}" data-item-idx="${idx}"><span class="ts">${esc(time)}</span><span class="agent">${esc(i.agent)}<br><span class="ev-type">${esc(i.type)}</span></span><span class="payload">${esc(i.payload)}</span></div>`;
  }).join('') + (items.length > 500 ? `<div style="text-align:center;padding:14px;color:var(--fg2)">… et ${items.length-500} entrées supplémentaires</div>` : '');
}
['#f-tl-agent','#f-tl-type','#f-tl-session'].forEach(s => $(s).addEventListener('change', renderTimeline));
$('#f-tl-q').addEventListener('input', renderTimeline);
renderTimeline();

// ══════════════════════════════════════════════════════════════
// SWIMLANE — The key visualization
// ══════════════════════════════════════════════════════════════
function renderSwimlane() {
  const ct = $('#swimlane-ct');
  const sessionF = $('#f-sl-session').value;
  const items = getAllItems(sessionF);

  if (!items.length) { ct.innerHTML = `<div class="empty-state"><div class="icon">🏊</div><h2>Aucune donnée pour le swimlane</h2><p>Les traces apparaîtront ici comme un diagramme de séquence.</p></div>`; return; }

  // Determine agent columns — orchestrator/HPE first, then by frequency
  const agentFreq = {};
  items.forEach(i => { agentFreq[i.agent] = (agentFreq[i.agent]||0) + 1; });
  const agents = Object.keys(agentFreq).sort((a,b) => {
    const aP = agentKey(a)==='orchestr' || agentKey(a)==='hpe' ? 0 : 1;
    const bP = agentKey(b)==='orchestr' || agentKey(b)==='hpe' ? 0 : 1;
    if (aP !== bP) return aP - bP;
    return agentFreq[b] - agentFreq[a];
  });

  const COL_W = 160, PAD_L = 64, ROW_H = 36, PAD_T = 44;
  const totalW = PAD_L + agents.length * COL_W + 20;
  const totalH = PAD_T + items.length * ROW_H + 40;

  // Build agent column index
  const agentCol = {};
  agents.forEach((a, i) => { agentCol[a] = i; });

  // Header
  let headerHtml = `<div class="sl-header" style="width:${totalW}px;padding-left:${PAD_L}px">`;
  agents.forEach(a => {
    const persona = (DATA.agents.find(ag => ag.id === a.split('/')[0]) || {}).persona || '';
    headerHtml += `<div class="sl-col-hdr" style="width:${COL_W}px;color:${agentColor(a)}">${esc(a)}${persona ? `<div class="persona">${esc(persona)}</div>` : ''}</div>`;
  });
  headerHtml += '</div>';

  // Body
  let bodyHtml = '';
  let svgLines = '';
  const positions = []; // {x, y, item, idx}

  // Detect parallel groups (events with very close timestamps)
  const parallelGroups = [];
  let currentGroup = [0];
  for (let i = 1; i < items.length; i++) {
    const prevT = new Date(items[i-1].ts).getTime();
    const curT = new Date(items[i].ts).getTime();
    if (curT - prevT <= 2000) { // within 2 seconds = parallel
      currentGroup.push(i);
    } else {
      if (currentGroup.length > 1) parallelGroups.push([...currentGroup]);
      currentGroup = [i];
    }
  }
  if (currentGroup.length > 1) parallelGroups.push([...currentGroup]);

  // Draw parallel bands
  parallelGroups.forEach(group => {
    const y1 = PAD_T + group[0] * ROW_H;
    const y2 = PAD_T + (group[group.length-1]+1) * ROW_H;
    bodyHtml += `<div class="sl-parallel-band" style="top:${y1}px;height:${y2-y1}px"><span class="sl-parallel-label" style="top:2px">⚡ parallel</span></div>`;
  });

  // Lane backgrounds
  agents.forEach((a, ci) => {
    bodyHtml += `<div class="sl-lane" style="left:${PAD_L + ci*COL_W}px;width:${COL_W}px;height:${totalH}px;opacity:.3"></div>`;
  });

  // Place events
  items.forEach((item, idx) => {
    const ci = agentCol[item.agent];
    if (ci === undefined) return;
    const x = PAD_L + ci * COL_W + COL_W/2;
    const y = PAD_T + idx * ROW_H + ROW_H/2;
    positions.push({x, y, item, ci, idx});

    const time = item.ts.replace(/.*T/,'').replace('Z','');
    const color = agentColor(item.agent);
    const shortType = item.type.length > 18 ? item.type.substring(0,18)+'…' : item.type;

    // Time label (left margin)
    if (idx === 0 || items[idx-1].ts.substring(11,19) !== item.ts.substring(11,19)) {
      bodyHtml += `<div class="sl-time-label" style="top:${y-7}px">${esc(time)}</div>`;
    }

    // Event node
    bodyHtml += `<div class="sl-event" style="left:${x-70}px;top:${y-12}px;border:1px solid ${color}20;background:${color}10" data-idx="${idx}" data-item-idx="${storeItem(item)}"><span class="dot" style="background:${color}"></span><span class="sl-label">${esc(shortType)}</span></div>`;
  });

  // Draw connection arrows between agents
  // Detect dispatch patterns: SOG:routed → agents, HPE:dispatch → agent
  for (let i = 0; i < positions.length; i++) {
    const p = positions[i];
    const item = p.item;

    // SOG:routed — arrows to dispatched agents
    if (item.type === 'SOG:routed' || item.type === 'SOG:aggregated') {
      const agMatch = item.payload.match(/agents=\[([^\]]+)\]/);
      if (agMatch) {
        agMatch[1].split(',').forEach(target => {
          const tgt = target.trim();
          const tgtPos = positions.find((pp, j) => j > i && pp.item.agent.toLowerCase().includes(tgt.toLowerCase()));
          if (tgtPos) svgLines += svgArrow(p.x, p.y, tgtPos.x, tgtPos.y, agentColor(item.agent));
        });
      }
    }

    // HPE:dispatch — arrow to specific agent or parallel wave
    if (item.type === 'HPE:dispatch') {
      const agMatch = item.payload.match(/agent=(\S+)/);
      const waveMatch = item.payload.match(/parallel_wave[^=]*=\[([^\]]+)\]/);
      if (agMatch) {
        const tgt = positions.find((pp, j) => j > i && j <= i+3 && pp.ci !== p.ci);
        if (tgt) svgLines += svgArrow(p.x, p.y, tgt.x, tgt.y, agentColor(item.agent));
      }
      if (waveMatch) {
        // Arrow to next 2-3 events (the parallel dispatches)
        for (let k = i+1; k < Math.min(i+5, positions.length); k++) {
          if (positions[k].ci !== p.ci) {
            svgLines += svgArrow(p.x, p.y, positions[k].x, positions[k].y, agentColor(item.agent));
          }
        }
      }
    }

    // HPE:complete — arrow back to next HPE event
    if (item.type === 'HPE:complete') {
      const nextHpe = positions.find((pp, j) => j > i && (pp.item.type.startsWith('HPE:') || pp.item.agent.includes('orchestr')));
      if (nextHpe && nextHpe.ci !== p.ci) {
        svgLines += svgArrow(p.x, p.y, nextHpe.x, nextHpe.y, agentColor(item.agent), true);
      }
    }

    // CVTL:requested — arrow from requester to next event
    if (item.type.includes('CVTL:requested')) {
      const validator = positions.find((pp, j) => j > i && j <= i+2 && pp.item.type.includes('CVTL:'));
      if (validator) svgLines += svgArrow(p.x, p.y, validator.x, validator.y, '#bc8cff');
    }

    // QEC — arrow back to orchestrator
    if (item.type.includes('QEC:received')) {
      const resolver = positions.find((pp, j) => j > i && j <= i+2 && pp.item.type.includes('QEC:'));
      if (resolver) svgLines += svgArrow(p.x, p.y, resolver.x, resolver.y, '#d29922');
    }

    // ACTIVATED — arrow from previous orchestrator/HPE dispatch
    if (item.type === 'ACTIVATED') {
      const dispatch = positions.slice(Math.max(0,i-3), i).reverse().find(pp => pp.item.type.includes('dispatch') || pp.item.type.includes('routed'));
      if (dispatch && dispatch.ci !== p.ci) svgLines += svgArrow(dispatch.x, dispatch.y, p.x, p.y, agentColor(dispatch.item.agent));
    }

    // HANDOFF→agent — arrow from sender to receiver
    if (item.type.startsWith('HANDOFF')) {
      const target = item.type.match(/HANDOFF\u2192(.+)/);
      if (target) {
        const tgtName = target[1].trim();
        const tgtPos = positions.find((pp, j) => j > i && j <= i+4 && pp.item.agent.includes(tgtName));
        if (tgtPos) svgLines += svgArrow(p.x, p.y, tgtPos.x, tgtPos.y, '#f778ba');
      }
    }
  }

  const svgHtml = `<svg class="sl-svg" width="${totalW}" height="${totalH}">${svgLines}</svg>`;

  ct.innerHTML = headerHtml + `<div class="sl-body" style="width:${totalW}px;height:${totalH}px;position:relative">${bodyHtml}${svgHtml}</div>`;
}

function svgArrow(x1, y1, x2, y2, color, dashed) {
  const id = 'ah' + Math.random().toString(36).substring(2,6);
  const dx = x2-x1, dy = y2-y1;
  const len = Math.sqrt(dx*dx+dy*dy);
  if (len < 5) return '';
  // Control point for curve
  const mx = (x1+x2)/2, my = (y1+y2)/2;
  const cx = mx + (y2-y1)*0.15, cy = my - (x2-x1)*0.15;
  const dashAttr = dashed ? ' stroke-dasharray="4 3"' : '';
  return `<defs><marker id="${id}" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto"><polygon points="0 0, 6 2, 0 4" class="arrow-head" fill="${color}"/></marker></defs><path d="M${x1},${y1} Q${cx},${cy} ${x2},${y2}" stroke="${color}" fill="none" stroke-width="1.5" opacity=".5" marker-end="url(#${id})"${dashAttr}/>`;
}

$('#f-sl-session').addEventListener('change', renderSwimlane);

// ══════════════════════════════════════════════════════════════
// DAG — Gantt Style
// ══════════════════════════════════════════════════════════════
function renderDAG() {
  const ct = $('#dag-ct');

  // Build task map from events
  const tasks = new Map();
  DATA.events.forEach(e => {
    if (e.type === 'task_started') {
      const id = e.payload.task_id || e.id;
      tasks.set(id, {id, agent:e.agent, desc:e.payload.description||id, status:'running', start:e.ts, end:null, deps:e.payload.depends_on||[], trust:null});
    }
    if (e.type === 'task_completed') {
      const id = e.payload.task_id || e.correlation_id || e.id;
      if (tasks.has(id)) { const t=tasks.get(id); t.status='done'; t.end=e.ts; t.trust=e.payload.trust_score||null; }
      else tasks.set(id, {id, agent:e.agent, desc:id, status:'done', start:e.ts, end:e.ts, deps:[], trust:e.payload.trust_score||null});
    }
    if (e.type === 'task_failed') {
      const id = e.payload.task_id || e.id;
      if (tasks.has(id)) tasks.get(id).status = 'failed';
    }
  });

  // Also from HPE traces
  DATA.traces.forEach(t => {
    if (t.event_type === 'HPE:complete') {
      const m = t.payload.match(/task=(\S+)/);
      if (m && !tasks.has(m[1])) tasks.set(m[1], {id:m[1],agent:'',desc:m[1],status:'done',start:t.timestamp,end:t.timestamp,deps:[],trust:null});
      if (m && tasks.has(m[1])) { const tk=tasks.get(m[1]); if (!tk.end) tk.end=t.timestamp; tk.status='done'; }
      const trustM = t.payload.match(/trust=(\d+)/);
      if (trustM && m && tasks.has(m[1])) tasks.get(m[1]).trust = parseInt(trustM[1]);
    }
  });

  if (!tasks.size) { ct.innerHTML = `<div class="empty-state"><div class="icon">🔀</div><h2>Aucun DAG détecté</h2><p>Le moteur HPE n'a pas encore planifié de tâches.<br>Événements attendus : <code>task_started</code> / <code>task_completed</code></p></div>`; return; }

  // Sort by start time, then topologically
  const taskList = [...tasks.values()].sort((a,b) => (a.start||'').localeCompare(b.start||''));

  // Time range
  const allTimes = taskList.flatMap(t => [t.start, t.end].filter(Boolean)).map(t => new Date(t).getTime());
  const minT = Math.min(...allTimes), maxT = Math.max(...allTimes);
  const range = maxT - minT || 1;

  let html = `<div class="gantt-header"><h3>📊 Task DAG — ${taskList.length} tâches</h3><div class="gantt-legend"><span class="gl-item"><span class="gl-dot" style="background:var(--green)"></span>Done</span><span class="gl-item"><span class="gl-dot" style="background:var(--yellow)"></span>Running</span><span class="gl-item"><span class="gl-dot" style="background:var(--border)"></span>Waiting</span><span class="gl-item"><span class="gl-dot" style="background:var(--red)"></span>Failed</span></div></div>`;
  html += `<div class="gantt-chart" style="position:relative">`;

  // SVG for dependency lines — use percentage-based viewBox for alignment with flex tracks
  let depLines = '';
  const taskY = {};
  taskList.forEach((t, i) => { taskY[t.id] = i * 40 + 20; });

  // Dependency lines (percentage X positions mapped to 0-100 viewBox)
  taskList.forEach(t => {
    t.deps.forEach(depId => {
      if (taskY[depId] !== undefined && taskY[t.id] !== undefined) {
        const startX = tasks.has(depId) && tasks.get(depId).end
          ? ((new Date(tasks.get(depId).end).getTime() - minT) / range) * 100
          : 0;
        const endX = t.start ? ((new Date(t.start).getTime() - minT) / range) * 100 : 0;
        depLines += `<line x1="${startX}" y1="${taskY[depId]}" x2="${endX}" y2="${taskY[t.id]}"/>`;
      }
    });
  });
  const svgH = taskList.length * 40;
  const depSvg = `<svg class="gantt-dep-svg" viewBox="0 0 100 ${svgH}" preserveAspectRatio="none" style="width:100%;height:${svgH}px">${depLines}</svg>`;

  // Task rows
  taskList.forEach((t, i) => {
    const startPct = t.start ? ((new Date(t.start).getTime() - minT) / range) * 100 : 0;
    const endPct = t.end ? ((new Date(t.end).getTime() - minT) / range) * 100 : startPct + 5;
    const widthPct = Math.max(endPct - startPct, 3);
    const trustBadge = t.trust ? ` 🛡️${t.trust}` : '';
    const color = agentColor(t.agent);
    html += `<div class="gantt-row"><div class="gantt-label" style="color:${color}">${esc(t.id)}<div class="gl-agent">${esc(t.agent)}</div></div><div class="gantt-track"><div class="gantt-bar ${t.status}" style="left:${startPct}%;width:${widthPct}%" data-item-idx="${storeItem({ts:t.start,agent:t.agent,type:"task:"+t.id,payload:"Status: "+t.status+(t.trust?" | Trust: "+t.trust:""),session:""})}">${esc(t.desc.substring(0,20))}${trustBadge}</div></div></div>`;
  });

  // Time axis — flex-based, no fixed width
  const steps = 5;
  html += `<div class="gantt-time-axis">`;
  for (let s = 0; s <= steps; s++) {
    const t = new Date(minT + (range * s / steps));
    const time = t.toISOString().substring(11,19);
    html += `<div class="gantt-time-tick" style="flex:1;text-align:${s===0?'left':s===steps?'right':'center'}">${time}</div>`;
  }
  html += '</div>';

  html += depSvg + '</div>';
  ct.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
// AGENT NETWORK (Canvas + Cards)
// ══════════════════════════════════════════════════════════════
function renderGraph() {
  const cv = $('#graph-cv');
  const box = $('#graph-box');
  const W = box.clientWidth, H = box.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  cv.style.width = W+'px'; cv.style.height = H+'px';
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);

  // Merge agents from graph + traces
  const allAgents = new Map();
  DATA.agents.forEach(a => allAgents.set(a.id, a));
  DATA.relationships.forEach(r => {
    if (!allAgents.has(r.from_agent)) allAgents.set(r.from_agent, {id:r.from_agent,persona:'',capabilities:[],metrics:{}});
    if (!allAgents.has(r.to_agent)) allAgents.set(r.to_agent, {id:r.to_agent,persona:'',capabilities:[],metrics:{}});
  });
  DATA.agent_ids.forEach(id => { if (!allAgents.has(id)) allAgents.set(id, {id,persona:'',capabilities:[],metrics:{}}); });

  const agentList = [...allAgents.values()];

  if (!agentList.length) {
    ctx.fillStyle='#8b949e'; ctx.font='14px system-ui'; ctx.textAlign='center';
    ctx.fillText('Aucun agent d\u00e9tect\u00e9', W/2, H/2);
    return;
  }

  // Force-directed layout with drag support
  const nodes = agentList.map((a, i) => {
    const angle = (2*Math.PI*i)/agentList.length - Math.PI/2;
    const r = Math.min(W,H)/2 - 80;
    return {id:a.id, data:a, x:W/2+r*Math.cos(angle), y:H/2+r*Math.sin(angle), vx:0, vy:0, fx:null, fy:null};
  });
  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  const edges = DATA.relationships.map(r => ({source:nodeMap.get(r.from_agent), target:nodeMap.get(r.to_agent), rel:r})).filter(e => e.source && e.target);

  const typeColors = {collaboration:'#3fb950',validation:'#bc8cff',delegation:'#39d2c0',challenge:'#f0883e'};

  // Force simulation parameters
  const REPULSION = 4000, ATTRACTION = 0.008, DAMPING = 0.85, CENTER_PULL = 0.01;
  let simRunning = true, simSteps = 0;

  function simulate() {
    // Coulomb repulsion between all nodes
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i+1; j < nodes.length; j++) {
        let dx = nodes[j].x - nodes[i].x, dy = nodes[j].y - nodes[i].y;
        const dist = Math.sqrt(dx*dx + dy*dy) || 1;
        const force = REPULSION / (dist * dist);
        const fx = (dx/dist)*force, fy = (dy/dist)*force;
        nodes[i].vx -= fx; nodes[i].vy -= fy;
        nodes[j].vx += fx; nodes[j].vy += fy;
      }
    }
    // Hooke attraction along edges
    edges.forEach(e => {
      const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y;
      const dist = Math.sqrt(dx*dx + dy*dy) || 1;
      const ideal = 120 + (1 - e.rel.strength) * 80;
      const force = (dist - ideal) * ATTRACTION * (0.5 + e.rel.strength);
      const fx = (dx/dist)*force, fy = (dy/dist)*force;
      e.source.vx += fx; e.source.vy += fy;
      e.target.vx -= fx; e.target.vy -= fy;
    });
    // Center pull
    nodes.forEach(n => {
      n.vx += (W/2 - n.x) * CENTER_PULL;
      n.vy += (H/2 - n.y) * CENTER_PULL;
    });
    // Apply velocity
    nodes.forEach(n => {
      if (n.fx !== null) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; return; }
      n.vx *= DAMPING; n.vy *= DAMPING;
      n.x += n.vx; n.y += n.vy;
      n.x = Math.max(40, Math.min(W-40, n.x));
      n.y = Math.max(40, Math.min(H-40, n.y));
    });
  }

  let highlightNode = null;

  function draw() {
    ctx.clearRect(0, 0, W, H);

    // Draw edges
    edges.forEach(e => {
      const from = e.source, to = e.target;
      const isHighlighted = highlightNode && (from.id === highlightNode || to.id === highlightNode);
      ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y);
      ctx.strokeStyle = typeColors[e.rel.type] || '#30363d';
      ctx.globalAlpha = isHighlighted ? 0.9 : Math.max(.12, e.rel.strength * 0.5);
      ctx.lineWidth = isHighlighted ? 2 + e.rel.strength*4 : 1 + e.rel.strength*2;
      ctx.stroke(); ctx.globalAlpha = 1;

      // Edge label
      if (isHighlighted || edges.length < 12) {
        const mx = (from.x+to.x)/2, my = (from.y+to.y)/2;
        ctx.fillStyle = isHighlighted ? '#c9d1d9' : '#8b949e';
        ctx.font = isHighlighted ? 'bold 10px system-ui' : '9px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText(e.rel.type + ' (' + e.rel.interactions + ')', mx, my - 4);
        if (e.rel.avg_trust) ctx.fillText('trust:' + e.rel.avg_trust, mx, my + 9);
      }

      // Draw arrow
      const angle = Math.atan2(to.y-from.y, to.x-from.x);
      const arrX = to.x - 30*Math.cos(angle), arrY = to.y - 30*Math.sin(angle);
      ctx.beginPath();
      ctx.moveTo(arrX + 8*Math.cos(angle), arrY + 8*Math.sin(angle));
      ctx.lineTo(arrX - 5*Math.cos(angle-0.5), arrY - 5*Math.sin(angle-0.5));
      ctx.lineTo(arrX - 5*Math.cos(angle+0.5), arrY - 5*Math.sin(angle+0.5));
      ctx.closePath();
      ctx.fillStyle = typeColors[e.rel.type] || '#30363d';
      ctx.globalAlpha = isHighlighted ? 0.8 : 0.4;
      ctx.fill();
      ctx.globalAlpha = 1;
    });

    // Draw nodes
    nodes.forEach(n => {
      const a = n.data;
      const color = agentColor(a.id);
      const isHL = highlightNode === a.id;
      const connected = highlightNode && edges.some(e => (e.source.id===a.id||e.target.id===a.id) && (e.source.id===highlightNode||e.target.id===highlightNode));
      const dim = highlightNode && !isHL && !connected;

      // Glow effect for highlighted node
      if (isHL) {
        ctx.beginPath(); ctx.arc(n.x, n.y, 38, 0, 2*Math.PI);
        const glow = ctx.createRadialGradient(n.x, n.y, 26, n.x, n.y, 38);
        glow.addColorStop(0, color + '40'); glow.addColorStop(1, color + '00');
        ctx.fillStyle = glow; ctx.fill();
      }

      // Node circle
      ctx.globalAlpha = dim ? 0.25 : 1;
      ctx.beginPath(); ctx.arc(n.x, n.y, isHL ? 30 : 26, 0, 2*Math.PI);
      ctx.fillStyle = '#161b22'; ctx.fill();
      ctx.strokeStyle = color; ctx.lineWidth = isHL ? 4 : 2.5; ctx.stroke();

      // Label
      ctx.fillStyle = color; ctx.font = 'bold 11px system-ui'; ctx.textAlign = 'center';
      ctx.fillText(a.id, n.x, n.y - 36);
      if (a.persona) { ctx.fillStyle = dim ? '#555' : '#8b949e'; ctx.font = '10px system-ui'; ctx.fillText(a.persona, n.x, n.y - 24); }

      // Initials
      ctx.fillStyle = color; ctx.font = 'bold 14px system-ui';
      ctx.fillText((a.persona||a.id).substring(0,2).toUpperCase(), n.x, n.y + 5);

      // Trust badge
      if (a.metrics && a.metrics.avg_trust_score) {
        const ts = a.metrics.avg_trust_score;
        ctx.fillStyle = ts>=90?'#3fb950':ts>=70?'#d29922':'#f85149';
        ctx.font = 'bold 10px system-ui'; ctx.fillText('\u2b50'+ts, n.x, n.y + 46);
      }
      ctx.globalAlpha = 1;
    });
  }

  // Animation loop
  function tick() {
    if (!simRunning) return;
    simulate();
    draw();
    simSteps++;
    if (simSteps < 200) requestAnimationFrame(tick);
    else { simRunning = false; draw(); }
  }
  tick();

  // Drag support
  let dragNode = null;
  cv.addEventListener('mousedown', e => {
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    dragNode = nodes.find(n => Math.hypot(n.x-mx, n.y-my) < 30);
    if (dragNode) { dragNode.fx = mx; dragNode.fy = my; cv.style.cursor = 'grabbing'; }
  });
  cv.addEventListener('mousemove', e => {
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (dragNode) {
      dragNode.fx = mx; dragNode.fy = my;
      dragNode.x = mx; dragNode.y = my;
      draw();
    } else {
      const hover = nodes.find(n => Math.hypot(n.x-mx, n.y-my) < 30);
      const newHL = hover ? hover.id : null;
      if (newHL !== highlightNode) { highlightNode = newHL; draw(); }
      cv.style.cursor = hover ? 'grab' : 'default';
    }
  });
  cv.addEventListener('mouseup', () => {
    if (dragNode) { dragNode.fx = null; dragNode.fy = null; dragNode = null; cv.style.cursor = 'default'; if (!simRunning) { simRunning = true; simSteps = 150; tick(); } }
  });
  cv.addEventListener('mouseleave', () => {
    if (dragNode) { dragNode.fx = null; dragNode.fy = null; dragNode = null; }
    if (highlightNode) { highlightNode = null; draw(); }
    cv.style.cursor = 'default';
  });

  // Legend
  $('#graph-legend').innerHTML = Object.entries(typeColors).map(([k,v]) => `<span class="item"><span class="dot" style="background:${v}"></span>${k}</span>`).join('') + ' <span class="item" style="margin-left:12px;color:var(--fg2)">&#128073; Drag nodes \u2014 hover to highlight</span>';

  // Sidebar: Agent cards
  const sidebar = $('#graph-sidebar');
  sidebar.innerHTML = agentList.map(a => {
    const color = agentColor(a.id);
    const m = a.metrics || {};
    const initials = (a.persona||a.id).substring(0,2).toUpperCase();
    let capsHtml = (a.capabilities||[]).map(c => `<span class="ac-cap">${esc(c)}</span>`).join('');
    // Emergent capabilities
    (a.emergent_capabilities||[]).forEach(ec => {
      if (ec.skill) capsHtml += `<span class="ac-cap" style="border:1px solid ${color};color:${color}">✨ ${esc(ec.skill)}</span>`;
    });
    return `<div class="agent-card">
      <div class="ac-header"><div class="ac-avatar" style="border-color:${color};color:${color}">${initials}</div><div><div class="ac-name" style="color:${color}">${esc(a.id)}</div><div class="ac-persona">${esc(a.persona||'')}</div></div></div>
      <div class="ac-stats">
        ${m.tasks_completed?`<div class="ac-stat"><span class="label">Tasks</span><span>${m.tasks_completed}</span></div>`:''}
        ${m.avg_trust_score?`<div class="ac-stat"><span class="label">Trust</span><span style="color:${m.avg_trust_score>=90?'var(--green)':m.avg_trust_score>=70?'var(--yellow)':'var(--red)'}">${m.avg_trust_score}</span></div>`:''}
        ${m.cross_validations_passed?`<div class="ac-stat"><span class="label">CVTL ✓</span><span>${m.cross_validations_passed}</span></div>`:''}
        ${m.hup_red_count!==undefined?`<div class="ac-stat"><span class="label">HUP 🔴</span><span>${m.hup_red_count}</span></div>`:''}
      </div>
      ${capsHtml?`<div class="ac-caps">${capsHtml}</div>`:''}
    </div>`;
  }).join('');
}

// Re-render graph on window resize
let _graphResizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(_graphResizeTimer);
  _graphResizeTimer = setTimeout(() => {
    if ($('#view-graph').classList.contains('active')) renderGraph();
  }, 200);
});

// ══════════════════════════════════════════════════════════════
// TRACE LOG TABLE
// ══════════════════════════════════════════════════════════════
function renderTraceLog() {
  const ag=$('#f-log-agent').value, tp=$('#f-log-type').value, q=($('#f-log-q').value||'').toLowerCase();
  let items = [...DATA.traces];
  if (ag) items = items.filter(i => i.agent.includes(ag));
  if (tp) items = items.filter(i => i.event_type.includes(tp));
  if (q) items = items.filter(i => `${i.agent} ${i.event_type} ${i.payload}`.toLowerCase().includes(q));
  const tbody = $('#trace-tbody');
  if (!items.length) { tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--fg2)">Aucune entrée</td></tr>`; return; }
  tbody.innerHTML = items.slice(0,1000).map(t => {
    const color = agentColor(t.agent);
    const idx = storeItem({ts:t.timestamp,agent:t.agent,type:t.event_type,payload:t.payload,session:t.session});
    return `<tr data-item-idx="${idx}"><td class="mono">${esc(t.timestamp.replace(/.*T/,'').replace('Z',''))}</td><td style="color:${color}">${esc(t.agent)}</td><td class="mono">${esc(t.event_type)}</td><td>${esc(t.payload)}</td><td class="mono">${esc(t.session)}</td></tr>`;
  }).join('');
}
['#f-log-agent','#f-log-type'].forEach(s => $(s).addEventListener('change', renderTraceLog));
$('#f-log-q').addEventListener('input', renderTraceLog);
renderTraceLog();

// ══════════════════════════════════════════════════════════════
// METRICS
// ══════════════════════════════════════════════════════════════
function renderMetrics() {
  const grid = $('#metrics-grid');
  const chartsDiv = $('#metrics-charts');
  const cards = [];

  cards.push({label:'Traces totales',value:DATA.traces.length,cls:DATA.traces.length>0?'good':'',detail:'Grimoire_TRACE.md'});
  cards.push({label:'Événements',value:DATA.events.length,cls:DATA.events.length>0?'good':'',detail:'.event-log.jsonl'});
  cards.push({label:'Agents actifs',value:DATA.agent_ids.length,cls:DATA.agent_ids.length>3?'good':'warn',detail:'Uniques dans traces + events'});
  cards.push({label:'Sessions',value:DATA.sessions.length,cls:'',detail:'Branches de session'});

  const handoffs = DATA.traces.filter(t => t.event_type.includes('HANDOFF'));
  cards.push({label:'Handoffs',value:handoffs.length,cls:'',detail:'Transferts inter-agents'});

  const decisions = DATA.traces.filter(t => t.event_type === 'DECISION');
  cards.push({label:'Décisions',value:decisions.length,cls:decisions.length>0?'good':'',detail:'Décisions techniques'});

  const hup = DATA.traces.filter(t => t.event_type.startsWith('HUP:'));
  const hupRed = hup.filter(t => t.event_type.includes('escalation'));
  cards.push({label:'HUP Checks',value:hup.length,cls:hupRed.length===0?'good':'warn',detail:`${hupRed.length} escalations ROUGE`});

  const trusts = DATA.agents.filter(a => a.metrics&&a.metrics.avg_trust_score).map(a => a.metrics.avg_trust_score);
  const avgTrust = trusts.length ? Math.round(trusts.reduce((a,b)=>a+b,0)/trusts.length) : 0;
  cards.push({label:'Trust Moyen',value:avgTrust||'—',cls:avgTrust>=85?'good':avgTrust>=70?'warn':avgTrust>0?'bad':'',detail:trusts.length?`Sur ${trusts.length} agents`:'Pas de données ARG'});

  const hpeEvents = DATA.traces.filter(t => t.event_type.startsWith('HPE:'));
  cards.push({label:'HPE Events',value:hpeEvents.length,cls:hpeEvents.length>0?'good':'',detail:'Parallélisme HPE'});

  const pce = DATA.traces.filter(t => t.event_type.startsWith('PCE:'));
  cards.push({label:'Débats PCE',value:pce.length,cls:'',detail:'Productive Conflict Engine'});

  const cvtl = DATA.traces.filter(t => t.event_type.startsWith('CVTL:'));
  cards.push({label:'Cross-Validations',value:cvtl.length,cls:cvtl.length>0?'good':'',detail:'CVTL checks'});

  const agentActivity = {};
  DATA.traces.forEach(t => { agentActivity[t.agent]=(agentActivity[t.agent]||0)+1; });
  const mostActive = Object.entries(agentActivity).sort((a,b)=>b[1]-a[1])[0];
  cards.push({label:'Agent + actif',value:mostActive?mostActive[0]:'—',cls:'',detail:mostActive?`${mostActive[1]} actions`:''});

  grid.innerHTML = cards.map(c => `<div class="metric-card ${c.cls}"><div class="mc-label">${esc(c.label)}</div><div class="mc-value">${esc(String(c.value))}</div><div class="mc-detail">${esc(c.detail)}</div></div>`).join('');

  // Charts section
  let chartsHtml = '';

  // Event type distribution bar chart
  const typeCounts = {};
  DATA.traces.forEach(t => { typeCounts[t.event_type]=(typeCounts[t.event_type]||0)+1; });
  const sortedTypes = Object.entries(typeCounts).sort((a,b)=>b[1]-a[1]);
  const maxCount = sortedTypes[0]?sortedTypes[0][1]:1;

  chartsHtml += `<h3>📊 Distribution des types d'événements</h3><div class="bar-chart">`;
  sortedTypes.slice(0,15).forEach(([type,count]) => {
    const pct = (count/maxCount)*100;
    const color = type.startsWith('HPE:') ? 'var(--accent)' : type.startsWith('HUP:') ? 'var(--yellow)' : type.startsWith('CVTL:') ? 'var(--purple)' : type.includes('ACTION') ? 'var(--green)' : type === 'DECISION' ? 'var(--orange)' : type === 'ACTIVATED' ? 'var(--cyan)' : 'var(--fg2)';
    chartsHtml += `<div class="bar-row"><span class="bar-label">${esc(type)}</span><span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:${color}">${count}</span></span></div>`;
  });
  chartsHtml += '</div>';

  // Agent activity bar chart
  const sortedAgents = Object.entries(agentActivity).sort((a,b)=>b[1]-a[1]);
  const maxAg = sortedAgents[0]?sortedAgents[0][1]:1;
  chartsHtml += `<h3 style="margin-top:24px">👥 Activité par agent</h3><div class="bar-chart">`;
  sortedAgents.forEach(([agent,count]) => {
    const pct = (count/maxAg)*100;
    chartsHtml += `<div class="bar-row"><span class="bar-label" style="color:${agentColor(agent)}">${esc(agent)}</span><span class="bar-track"><span class="bar-fill" style="width:${pct}%;background:${agentColor(agent)}">${count}</span></span></div>`;
  });
  chartsHtml += '</div>';

  chartsDiv.innerHTML = chartsHtml;
}

// ══════════════════════════════════════════════════════════════
// OVERVIEW — High-level Dashboard
// ══════════════════════════════════════════════════════════════
function renderOverview() {
  const grid = $('#ov-grid');
  const cards = [];

  // KPI Cards
  cards.push({icon:'&#128209;',label:'Traces',value:DATA.traces.length,cls:DATA.traces.length>0?'good':'',sub:'BMAD_TRACE.md'});
  cards.push({icon:'&#9889;',label:'\u00c9v\u00e9nements',value:DATA.events.length,cls:DATA.events.length>0?'good':'',sub:'.event-log.jsonl'});
  cards.push({icon:'&#129302;',label:'Agents',value:DATA.agents.length||DATA.agent_ids.length,cls:DATA.agent_ids.length>3?'good':'warn',sub:DATA.agent_ids.length+' uniques'});
  cards.push({icon:'&#128218;',label:'Sessions',value:DATA.sessions.length,cls:'',sub:'Contextes de travail'});

  const decisions = DATA.traces.filter(t => t.event_type === 'DECISION');
  cards.push({icon:'&#128161;',label:'D\u00e9cisions',value:decisions.length,cls:decisions.length>0?'good':'',sub:'Choix techniques'});

  const hup = DATA.traces.filter(t => t.event_type.startsWith('HUP:'));
  const hupEsc = hup.filter(t => t.event_type.includes('escalation'));
  cards.push({icon:'&#128737;',label:'HUP Checks',value:hup.length,cls:hupEsc.length===0?'good':'warn',sub:hupEsc.length+' escalations'});

  const trusts = DATA.agents.filter(a => a.metrics && a.metrics.avg_trust_score).map(a => a.metrics.avg_trust_score);
  const avgTrust = trusts.length ? Math.round(trusts.reduce((a,b)=>a+b,0)/trusts.length) : 0;
  cards.push({icon:'&#128170;',label:'Trust Moyen',value:avgTrust||'\u2014',cls:avgTrust>=85?'good':avgTrust>=70?'warn':avgTrust>0?'bad':'',sub:trusts.length?trusts.length+' agents':'Pas de donn\u00e9es ARG'});

  const cvtl = DATA.traces.filter(t => t.event_type.startsWith('CVTL:'));
  cards.push({icon:'&#9989;',label:'Cross-Valid.',value:cvtl.length,cls:cvtl.length>0?'good':'',sub:'CVTL checks'});

  grid.innerHTML = cards.map(c => `<div class="ov-card ${c.cls}"><div class="ov-icon">${c.icon}</div><div class="ov-label">${esc(c.label)}</div><div class="ov-value">${esc(String(c.value))}</div><div class="ov-sub">${esc(c.sub)}</div></div>`).join('');

  // Activity sparkbar per session
  const actDiv = $('#ov-activity');
  if (DATA.sessions.length) {
    let html = '';
    DATA.sessions.forEach(sid => {
      const evts = DATA.traces.filter(t => t.session === sid);
      // Build time slots (group by minute)
      const slots = {};
      evts.forEach(e => { const k = (e.timestamp||'').substring(0,16); slots[k] = (slots[k]||0)+1; });
      const slotKeys = Object.keys(slots).sort();
      const maxSlot = Math.max(...Object.values(slots), 1);
      const bars = slotKeys.map(k => `<div class="bar" style="height:${(slots[k]/maxSlot)*100}%" title="${k}: ${slots[k]} events"></div>`).join('');
      html += `<div style="margin-bottom:12px"><div style="font-size:.78rem;font-weight:600;color:var(--accent);margin-bottom:4px">${esc(sid)} <span style="color:var(--fg2);font-weight:400">(${evts.length} traces)</span></div><div class="sparkbar">${bars||'<span style="color:var(--fg2);font-size:.75rem">Pas de donn\u00e9es temporelles</span>'}</div></div>`;
    });
    actDiv.innerHTML = html;
  } else {
    actDiv.innerHTML = '<div style="color:var(--fg2);font-size:.82rem">Aucune session d\u00e9tect\u00e9e</div>';
  }

  // Agent workload
  const wlDiv = $('#ov-workload');
  const agActivity = {};
  DATA.traces.forEach(t => { const k = t.agent.split('/')[0]; agActivity[k] = (agActivity[k]||0)+1; });
  const sortedAg = Object.entries(agActivity).sort((a,b)=>b[1]-a[1]);
  const maxAg = sortedAg[0]?sortedAg[0][1]:1;
  if (sortedAg.length) {
    wlDiv.innerHTML = sortedAg.map(([ag,cnt]) => {
      const pct = (cnt/maxAg)*100;
      const color = agentColor(ag);
      const trust = (DATA.agents.find(a=>a.id===ag)||{}).metrics;
      const ts = trust && trust.avg_trust_score ? trust.avg_trust_score : '';
      return `<div class="ov-work-row"><span class="wl-name" style="color:${color}">${esc(ag)}</span><span class="wl-bar"><span class="wl-fill" style="width:${pct}%;background:${color}">${cnt}</span></span><span class="wl-score">${ts?'\u2b50'+ts:''}</span></div>`;
    }).join('');
  } else {
    wlDiv.innerHTML = '<div style="color:var(--fg2);font-size:.82rem">Aucune activit\u00e9 agent</div>';
  }

  // Recent decisions
  const decDiv = $('#ov-decisions');
  if (decisions.length) {
    decDiv.innerHTML = decisions.slice(-10).reverse().map(d => {
      const time = (d.timestamp||'').replace(/.*T/,'').replace('Z','');
      const idx = storeItem({ts:d.timestamp,agent:d.agent,type:d.event_type,payload:d.payload,session:d.session});
      return `<div class="ov-decision" data-item-idx="${idx}"><span class="d-agent" style="color:${agentColor(d.agent)}">${esc(d.agent)}</span> <span class="d-time">${esc(time)}</span><div style="margin-top:3px">${esc(d.payload.substring(0,120))}</div></div>`;
    }).join('');
  } else {
    decDiv.innerHTML = '<div style="color:var(--fg2);font-size:.82rem">Aucune d\u00e9cision enregistr\u00e9e</div>';
  }

  // Alerts
  const alertDiv = $('#ov-alerts');
  const alerts = [];
  hupEsc.forEach(h => alerts.push({cls:'hup',icon:'&#9888;&#65039;',text:`HUP Escalation: ${h.payload.substring(0,80)}`,ts:h.timestamp}));
  DATA.traces.filter(t => t.event_type.includes('PCE:')).forEach(p => alerts.push({cls:'info',icon:'&#128172;',text:`D\u00e9bat: ${p.payload.substring(0,80)}`,ts:p.timestamp}));
  DATA.traces.filter(t => t.payload.toLowerCase().includes('fail') || t.payload.toLowerCase().includes('error')).slice(-5).forEach(f => alerts.push({cls:'error',icon:'&#10060;',text:`${f.agent}: ${f.payload.substring(0,80)}`,ts:f.timestamp}));

  if (alerts.length) {
    alertDiv.innerHTML = alerts.slice(-8).reverse().map(a => `<div class="ov-alert ${a.cls}">${a.icon} ${esc(a.text)}</div>`).join('');
  } else {
    alertDiv.innerHTML = '<div class="ov-alert info">&#9989; Aucune alerte \u2014 tout est nominal</div>';
  }

  // Handoff chains
  const hoDiv = $('#ov-handoffs');
  const handoffs = DATA.traces.filter(t => t.event_type.startsWith('HANDOFF'));
  if (handoffs.length) {
    // Build chains
    const chains = [];
    let chain = [];
    handoffs.forEach(h => {
      const m = h.event_type.match(/HANDOFF\u2192(.+)/);
      if (m) {
        if (!chain.length) chain.push(h.agent);
        chain.push(m[1]);
      } else {
        if (chain.length > 1) chains.push([...chain]);
        chain = [h.agent];
      }
    });
    if (chain.length > 1) chains.push(chain);

    if (chains.length) {
      hoDiv.innerHTML = chains.map(ch => {
        const nodes = ch.map(a => `<span class="hc-node" style="border-color:${agentColor(a)};color:${agentColor(a)}">${esc(a)}</span>`);
        return `<div class="handoff-chain"><div class="hc-flow">${nodes.join('<span class="hc-arrow">\u2192</span>')}</div></div>`;
      }).join('');
    } else {
      hoDiv.innerHTML = '<div style="color:var(--fg2);font-size:.82rem">Pas de cha\u00eenes d\u00e9tect\u00e9es</div>';
    }
  } else {
    hoDiv.innerHTML = '<div style="color:var(--fg2);font-size:.82rem">Aucun handoff inter-agents</div>';
  }

  // Sessions list
  const sessDiv = $('#ov-sessions');
  if (DATA.sessions.length) {
    sessDiv.innerHTML = DATA.sessions.map(sid => {
      const sTraces = DATA.traces.filter(t => t.session === sid);
      const agents = [...new Set(sTraces.map(t => t.agent.split('/')[0]))];
      const types = {};
      sTraces.forEach(t => { const k = agentKey(t.agent); types[k] = (types[k]||0)+1; });
      const total = sTraces.length || 1;
      const segs = Object.entries(types).map(([k,v]) => `<span class="s-seg" style="width:${(v/total)*100}%;background:${AG_COLORS[k]||AG_COLORS.default}"></span>`).join('');
      return `<div class="ov-sess" onclick="switchTab('swimlane');$('#f-sl-session').value='${esc(sid)}';renderSwimlane()"><span class="s-id">${esc(sid)}</span><span class="s-stats"><span>${sTraces.length} traces</span><span>${agents.length} agents</span></span><span class="s-bar">${segs}</span></div>`;
    }).join('');
  } else {
    sessDiv.innerHTML = '<div style="color:var(--fg2);font-size:.82rem">Aucune session</div>';
  }
}

// ══════════════════════════════════════════════════════════════
// TOOLTIP SYSTEM
// ══════════════════════════════════════════════════════════════
(function() {
  const tt = $('#obs-tooltip');
  let ttTimer = null;
  document.addEventListener('mouseover', e => {
    const el = e.target.closest('[data-item-idx]');
    if (!el) return;
    const item = ITEMS[parseInt(el.dataset.itemIdx)];
    if (!item) return;
    clearTimeout(ttTimer);
    ttTimer = setTimeout(() => {
      const payload = typeof item.payload === 'object' ? JSON.stringify(item.payload) : (item.payload||'');
      tt.innerHTML = `<div class="tt-agent" style="color:${agentColor(item.agent)}">${esc(item.agent)}</div><div class="tt-type">${esc(item.type)}</div><div class="tt-payload">${esc(payload.substring(0,180))}${payload.length>180?'\u2026':''}</div>`;
      const rect = el.getBoundingClientRect();
      let top = rect.bottom + 6, left = rect.left;
      if (top + 120 > window.innerHeight) top = rect.top - 80;
      if (left + 340 > window.innerWidth) left = window.innerWidth - 350;
      tt.style.top = top + 'px';
      tt.style.left = Math.max(8, left) + 'px';
      tt.classList.add('visible');
    }, 300);
  });
  document.addEventListener('mouseout', e => {
    const el = e.target.closest('[data-item-idx]');
    if (el) { clearTimeout(ttTimer); tt.classList.remove('visible'); }
  });
  document.addEventListener('click', () => { tt.classList.remove('visible'); });
})();

// ══════════════════════════════════════════════════════════════
// GLOBAL SEARCH
// ══════════════════════════════════════════════════════════════
(function() {
  const input = $('#global-q');
  const results = $('#global-results');
  let debounce = null;
  input.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      const q = input.value.toLowerCase().trim();
      if (q.length < 2) { results.classList.remove('open'); return; }
      const items = getAllItems('');
      const matches = items.filter(i => `${i.agent} ${i.type} ${i.payload}`.toLowerCase().includes(q)).slice(0, 20);
      if (!matches.length) { results.innerHTML = '<div class="sr-item" style="color:var(--fg2)">Aucun r\u00e9sultat</div>'; results.classList.add('open'); return; }
      results.innerHTML = matches.map(m => {
        const idx = storeItem(m);
        return `<div class="sr-item" data-item-idx="${idx}"><span class="sr-agent" style="color:${agentColor(m.agent)}">${esc(m.agent)}</span> <span class="sr-type">${esc(m.type)}</span><div style="color:var(--fg2);font-size:.72rem;margin-top:2px">${esc((m.payload||'').substring(0,100))}</div></div>`;
      }).join('');
      results.classList.add('open');
    }, 200);
  });
  input.addEventListener('blur', () => { setTimeout(() => results.classList.remove('open'), 200); });
  input.addEventListener('focus', () => { if (input.value.length >= 2) input.dispatchEvent(new Event('input')); });
})();

// ══════════════════════════════════════════════════════════════
// EXPORT BUTTON
// ══════════════════════════════════════════════════════════════
$('#btn-export').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(DATA, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'grimoire-observatory-' + new Date().toISOString().substring(0,10) + '.json';
  a.click();
  URL.revokeObjectURL(a.href);
});

// ── Auto-refresh (serve mode) ───────────────────────────────
(function() {
  let lastMod = null;
  function checkRefresh() {
    fetch(window.location.pathname, { method:'HEAD', cache:'no-cache' })
      .then(r => {
        const mod = r.headers.get('last-modified') || r.headers.get('etag');
        if (lastMod === null) { lastMod = mod; $('#live-badge').style.display = ''; return; }
        if (mod && mod !== lastMod) { lastMod = mod; window.location.reload(); }
      }).catch(() => {});
  }
  // Only activate if served via HTTP (not file://)
  if (window.location.protocol.startsWith('http')) {
    setInterval(checkRefresh, 2500);
    checkRefresh();
  }
})();

// ── Initial renders ─────────────────────────────────────────
// Overview is the default tab
renderOverview();
</script>
</body>
</html>

"""


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate observatory HTML."""
    root = Path(args.project_root).resolve()
    out_dir = _find_output_dir(root)
    data = load_all(root, out_dir)
    html = generate_html(data)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OBSERVATORY_HTML
    out_path.write_text(html, encoding="utf-8")
    print(f"✅ Observatory generated: {out_path}")
    print(f"   📊 {len(data.traces)} traces | {len(data.events)} events | {len(data.agents)} agents")
    print(f"   🌐 Open in browser: file://{out_path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Generate + serve with auto-reload."""
    root = Path(args.project_root).resolve()
    out_dir = _find_output_dir(root)
    port = args.port

    # Initial generate
    data = load_all(root, out_dir)
    html = generate_html(data, auto_refresh=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OBSERVATORY_HTML
    out_path.write_text(html, encoding="utf-8")

    # Watch source files and regenerate on change
    trace_path = _find_trace(out_dir)
    watch_files = [
        trace_path,
        out_dir / EVENT_LOG_FILE,
        out_dir / AGENT_GRAPH_FILE,
        out_dir / SHARED_STATE_FILE,
    ]
    last_mtimes = {str(f): f.stat().st_mtime if f.exists() else 0 for f in watch_files}

    def watcher():
        nonlocal last_mtimes
        while True:
            time.sleep(2)
            changed = False
            for f in watch_files:
                mt = f.stat().st_mtime if f.exists() else 0
                if mt != last_mtimes.get(str(f), 0):
                    changed = True
                    last_mtimes[str(f)] = mt
            if changed:
                try:
                    new_data = load_all(root, out_dir)
                    new_html = generate_html(new_data, auto_refresh=True)
                    out_path.write_text(new_html, encoding="utf-8")
                    print(f"🔄 Regenerated ({len(new_data.traces)} traces, {len(new_data.events)} events)")
                except Exception as e:
                    print(f"⚠️ Regen error: {e}")

    t = threading.Thread(target=watcher, daemon=True)
    t.start()

    # Serve
    os.chdir(str(out_dir))

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass  # silent

    server = http.server.HTTPServer(("", port), QuietHandler)
    print(f"🔭 Grimoire Observatory serving at http://localhost:{port}/{OBSERVATORY_HTML}")
    print(f"   Auto-reload: watching {len(watch_files)} files every 2s")
    print("   Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Observatory stopped.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export parsed data as JSON."""
    root = Path(args.project_root).resolve()
    out_dir = _find_output_dir(root)
    data = load_all(root, out_dir)
    print(data_to_json(data))
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="observatory",
        description="Grimoire Observatory — Interactive Visual Dashboard",
    )
    parser.add_argument("--project-root", default=".", help="Project root path")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("generate", help="Generate observatory HTML")

    sp_serve = sub.add_parser("serve", help="Generate + serve with auto-reload")
    sp_serve.add_argument("--port", type=int, default=8420, help="HTTP port (default: 8420)")

    sub.add_parser("export", help="Export parsed data as JSON")

    args = parser.parse_args(argv)

    if args.command == "generate":
        return cmd_generate(args)
    elif args.command == "serve":
        return cmd_serve(args)
    elif args.command == "export":
        return cmd_export(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
