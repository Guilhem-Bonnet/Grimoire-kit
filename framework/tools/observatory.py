#!/usr/bin/env python3
"""
observatory.py — BMAD Observatory: Interactive Visual Dashboard.
================================================================

Génère un dashboard HTML interactif *autoporté* (single-file, zero CDN)
à partir des données BMAD : traces, event-log, agent-graph, shared-state.

Vues :
  1. **Timeline**    — Flux chronologique des échanges inter-agents (swimlanes)
  2. **DAG**         — Graphe de tâches parallèles/séquentielles (HPE status)
  3. **Agent Graph** — Réseau relationnel des agents (ARG BM-57)
  4. **Trace Log**   — Tableau filtrable de toutes les entrées BMAD_TRACE
  5. **Metrics**     — KPIs : trust scores, throughput, parallélisme

Modes :
  generate  — Génère le fichier HTML dans _bmad-output/
  serve     — Génère + lance un serveur local avec auto-reload
  export    — Exporte les données parsées en JSON

Usage :
  python3 observatory.py --project-root . generate
  python3 observatory.py --project-root . serve --port 8420
  python3 observatory.py --project-root . export > data.json

Stdlib only — aucune dépendance externe.

Sources de données :
  - _bmad-output/BMAD_TRACE.md          (BM-28)
  - _bmad-output/.event-log.jsonl        (BM-59 ELSS)
  - _bmad-output/.agent-graph.yaml       (BM-57 ARG)
  - _bmad-output/.shared-state.yaml      (BM-59 ELSS)
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

OUTPUT_DIR = "_bmad-output"
TRACE_FILE = "BMAD_TRACE.md"
EVENT_LOG_FILE = ".event-log.jsonl"
AGENT_GRAPH_FILE = ".agent-graph.yaml"
SHARED_STATE_FILE = ".shared-state.yaml"
OBSERVATORY_HTML = "observatory.html"


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class TraceEntry:
    """Parsed entry from BMAD_TRACE.md."""
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

# Regex for BMAD_TRACE.md line format:
# [2026-02-27T14:32:01Z] [dev/Amelia]       [ACTION:implement]   story: US-042 ...
_TRACE_RE = re.compile(
    r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)",
)
_SESSION_RE = re.compile(r"^## Session\s+(\S+)")


def parse_trace(path: Path) -> tuple[list[TraceEntry], list[str]]:
    """Parse BMAD_TRACE.md into structured entries."""
    entries: list[TraceEntry] = []
    sessions: list[str] = []
    current_session = "unknown"

    if not path.exists():
        return entries, sessions

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") and not line.startswith("## Session"):
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
    """Minimal YAML parser for the subset used in BMAD (no external deps).

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


def load_all(project_root: Path) -> ObservatoryData:
    """Load all BMAD data sources."""
    out_dir = project_root / OUTPUT_DIR

    traces, sessions = parse_trace(out_dir / TRACE_FILE)
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
    html = _HTML_TEMPLATE.replace("__BMAD_DATA__", json_data)
    # auto_refresh is handled by JS HEAD-check (preserves tab/scroll state)
    # No meta refresh tag — it would cause full reloads losing all state
    return html.replace("__AUTO_REFRESH__", "")


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BMAD Observatory</title>
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
</style>
</head>
<body>

<header>
  <h1>🔭 BMAD Observatory</h1>
  <div class="header-stats">
    <span>Traces: <span class="n" id="stat-traces">0</span></span>
    <span>Events: <span class="n" id="stat-events">0</span></span>
    <span>Agents: <span class="n" id="stat-agents">0</span></span>
    <span>Sessions: <span class="n" id="stat-sessions">0</span></span>
  </div>
  <span class="live-badge" id="live-badge" style="display:none">LIVE</span>
</header>

<div class="tabs" id="tabs">
  <div class="tab" data-view="timeline">Timeline <span class="kbd">1</span></div>
  <div class="tab active" data-view="swimlane">Swimlane <span class="kbd">2</span></div>
  <div class="tab" data-view="dag">DAG <span class="kbd">3</span></div>
  <div class="tab" data-view="graph">Network <span class="kbd">4</span></div>
  <div class="tab" data-view="tracelog">Log <span class="kbd">5</span></div>
  <div class="tab" data-view="metrics">Metrics <span class="kbd">6</span></div>
</div>

<main>
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
  <div class="view active" id="view-swimlane">
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

<script>
const DATA = __BMAD_DATA__;
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
const TAB_VIEWS = ['timeline','swimlane','dag','graph','tracelog','metrics'];
function switchTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.view===name));
  $$('.view').forEach(v => v.classList.toggle('active', v.id==='view-'+name));
  if (name==='swimlane') renderSwimlane();
  if (name==='dag') renderDAG();
  if (name==='graph') renderGraph();
  if (name==='metrics') renderMetrics();
}
$$('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.view)));
document.addEventListener('keydown', e => {
  if (e.target.tagName==='INPUT' || e.target.tagName==='SELECT') return;
  const n = parseInt(e.key);
  if (n >= 1 && n <= 6) { e.preventDefault(); switchTab(TAB_VIEWS[n-1]); }
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
  // Add agents from traces not in graph
  DATA.agent_ids.forEach(id => { if (!allAgents.has(id)) allAgents.set(id, {id,persona:'',capabilities:[],metrics:{}}); });

  const agentList = [...allAgents.values()];

  if (!agentList.length) {
    ctx.fillStyle='#8b949e'; ctx.font='14px system-ui'; ctx.textAlign='center';
    ctx.fillText('Aucun agent détecté', W/2, H/2);
    return;
  }

  // Position in circle
  const cx=W/2, cy=H/2, radius=Math.min(W,H)/2-70;
  const positions = new Map();
  agentList.forEach((a,i) => {
    const angle = (2*Math.PI*i)/agentList.length - Math.PI/2;
    positions.set(a.id, {x:cx+radius*Math.cos(angle), y:cy+radius*Math.sin(angle)});
  });

  // Draw edges
  const typeColors = {collaboration:'#3fb950',validation:'#bc8cff',delegation:'#39d2c0',challenge:'#f0883e'};
  DATA.relationships.forEach(r => {
    const from=positions.get(r.from_agent), to=positions.get(r.to_agent);
    if (!from||!to) return;
    ctx.beginPath(); ctx.moveTo(from.x,from.y); ctx.lineTo(to.x,to.y);
    ctx.strokeStyle = typeColors[r.type]||'#30363d';
    ctx.globalAlpha = Math.max(.15, r.strength);
    ctx.lineWidth = 1 + r.strength*3;
    ctx.stroke(); ctx.globalAlpha=1;
    const mx=(from.x+to.x)/2, my=(from.y+to.y)/2;
    ctx.fillStyle='#8b949e'; ctx.font='9px system-ui'; ctx.textAlign='center';
    ctx.fillText(`${r.type} (${r.interactions})`, mx, my-3);
    if (r.avg_trust) ctx.fillText(`trust:${r.avg_trust}`, mx, my+9);
  });

  // Draw nodes
  agentList.forEach(a => {
    const pos=positions.get(a.id); if (!pos) return;
    const color = agentColor(a.id);
    ctx.beginPath(); ctx.arc(pos.x,pos.y,26,0,2*Math.PI);
    ctx.fillStyle='#161b22'; ctx.fill();
    ctx.strokeStyle=color; ctx.lineWidth=3; ctx.stroke();
    ctx.fillStyle=color; ctx.font='bold 11px system-ui'; ctx.textAlign='center';
    ctx.fillText(a.id, pos.x, pos.y-34);
    if (a.persona) { ctx.fillStyle='#8b949e'; ctx.font='10px system-ui'; ctx.fillText(a.persona, pos.x, pos.y-22); }
    ctx.fillStyle=color; ctx.font='bold 13px system-ui';
    ctx.fillText((a.persona||a.id).substring(0,2).toUpperCase(), pos.x, pos.y+4);
    if (a.metrics && a.metrics.avg_trust_score) {
      const ts=a.metrics.avg_trust_score;
      ctx.fillStyle = ts>=90?'#3fb950':ts>=70?'#d29922':'#f85149';
      ctx.font='10px system-ui'; ctx.fillText(`⭐${ts}`, pos.x, pos.y+44);
    }
  });

  // Legend
  $('#graph-legend').innerHTML = Object.entries(typeColors).map(([k,v]) => `<span class="item"><span class="dot" style="background:${v}"></span>${k}</span>`).join('');

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

  cards.push({label:'Traces totales',value:DATA.traces.length,cls:DATA.traces.length>0?'good':'',detail:'BMAD_TRACE.md'});
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
// Swimlane is the default tab — only render it once
renderSwimlane();
</script>
</body>
</html>

"""


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate observatory HTML."""
    root = Path(args.project_root).resolve()
    data = load_all(root)
    html = generate_html(data)

    out_dir = root / OUTPUT_DIR
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
    out_dir = root / OUTPUT_DIR
    port = args.port

    # Initial generate
    data = load_all(root)
    html = generate_html(data, auto_refresh=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OBSERVATORY_HTML
    out_path.write_text(html, encoding="utf-8")

    # Watch source files and regenerate on change
    watch_files = [
        out_dir / TRACE_FILE,
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
                    new_data = load_all(root)
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
    print(f"🔭 BMAD Observatory serving at http://localhost:{port}/{OBSERVATORY_HTML}")
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
    data = load_all(root)
    print(data_to_json(data))
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="observatory",
        description="BMAD Observatory — Interactive Visual Dashboard",
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
