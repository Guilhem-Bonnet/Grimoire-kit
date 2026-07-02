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

Positionnement produit :
  - UI gamifiée utilitaire : outil visuel intuitif pour custom/debug/comprendre/
    modifier/ajuster les agents
  - Non-objectif : expérience de jeu autonome orientée "fun"

Modes :
  generate  — Génère le fichier HTML dans _grimoire-output/
  serve     — Génère + lance un serveur local avec auto-reload
  export    — Exporte les données parsées en JSON

LIVE (état actuel) :
  - ancrage timeline côté client via bouton LIVE
  - rafraîchissement quasi-live en mode serve (polling HTTP HEAD + reload)

Usage :
  python3 observatory.py --project-root . generate
  python3 observatory.py --project-root . serve --host 127.0.0.1 --port 8420
  python3 observatory.py --project-root . serve --port 8420 --commit-required
  python3 observatory.py --project-root . serve --port 8420 --read-only
  python3 observatory.py --project-root . export > data.json

Stdlib only — aucune dépendance externe.

Sources de données :
  - _grimoire-output/Grimoire_TRACE.md          (BM-28)
  - _grimoire-output/.event-log.jsonl        (BM-59 Event Log)
  - _grimoire-output/.agent-graph.yaml       (BM-57 ARG)
  - _grimoire-output/.shared-state.yaml      (BM-59 Event Log)
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ── Version ──────────────────────────────────────────────────────────────────

OBSERVATORY_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

OUTPUT_DIR = "_grimoire-output"
RUNTIME_OUTPUT_DIR = "_grimoire-runtime-output"
TRACE_FILE = "Grimoire_TRACE.md"
TRACE_FILE_CANDIDATES = (TRACE_FILE, "GRIMOIRE_TRACE.md", "BMAD_TRACE.md")
EVENT_LOG_FILE = ".event-log.jsonl"
AGENT_GRAPH_FILE = ".agent-graph.yaml"
SHARED_STATE_FILE = ".shared-state.yaml"
OBSERVATORY_HTML = "observatory.html"
AGENT_CONFIG_FILE = ".agent-config-overrides.json"
AGENT_CONFIG_BACKUP_DIR = ".agent-config-backups"
AGENT_CONFIG_MAX_BACKUPS = 20
AGENT_CONFIG_SCHEMA_VERSION = 1
AGENT_CONFIG_AUDIT_FILE = ".agent-config-audit.jsonl"


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
    name: str = ""
    persona: str = ""
    description: str = ""
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
    agent_config: dict[str, Any] = field(default_factory=dict)


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
            static_caps = info.get("static_capabilities", [])
            if not isinstance(static_caps, list):
                static_caps = []
            emergent_caps = info.get("emergent_capabilities", [])
            if not isinstance(emergent_caps, list):
                emergent_caps = []
            agents.append(AgentNode(
                id=str(aid),
                name=str(info.get("name", aid)),
                persona=str(info.get("persona", "")),
                description=str(info.get("description", "")),
                capabilities=[str(cap) for cap in static_caps if str(cap).strip()],
                emergent_capabilities=emergent_caps,
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


def _utc_now_iso() -> str:
  """Return UTC timestamp in ISO format used across Observatory artifacts."""
  return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_int(value: Any, default: int = 0) -> int:
  """Best-effort integer conversion with fallback."""
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def _agent_base_id(agent_id: str) -> str:
  """Normalize an agent identifier to its canonical base id."""
  return str(agent_id or "").strip().split("/")[0].strip()


def _agent_config_path(out_dir: Path) -> Path:
  """Return agent config override file path."""
  return out_dir / AGENT_CONFIG_FILE


def _agent_config_backup_path(out_dir: Path) -> Path:
  """Return agent config backup directory path."""
  return out_dir / AGENT_CONFIG_BACKUP_DIR


def _agent_config_audit_path(out_dir: Path) -> Path:
  """Return append-only audit trail path for config API actions."""
  return out_dir / AGENT_CONFIG_AUDIT_FILE


def _append_agent_config_audit(out_dir: Path, entry: dict[str, Any]) -> None:
  """Append one JSONL audit record to the agent config audit trail."""
  path = _agent_config_audit_path(out_dir)
  path.parent.mkdir(parents=True, exist_ok=True)
  row = {"ts": _utc_now_iso(), **entry}
  with path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _empty_agent_config() -> dict[str, Any]:
  """Create an empty normalized config payload."""
  return {
    "schema_version": AGENT_CONFIG_SCHEMA_VERSION,
    "version": 0,
    "updated_at": "",
    "updated_by": "",
    "agents": {},
  }


def _sanitize_skills(skills: Any) -> list[str]:
  """Normalize skills as a unique ordered list of non-empty strings."""
  if isinstance(skills, str):
    raw = [s.strip() for s in skills.split(",")]
  elif isinstance(skills, list):
    raw = [str(s).strip() for s in skills]
  else:
    raw = []
  seen: set[str] = set()
  clean: list[str] = []
  for skill in raw:
    if not skill:
      continue
    if skill in seen:
      continue
    seen.add(skill)
    clean.append(skill)
  return clean


def _normalize_agent_entry(agent_id: str, raw: Any, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
  """Normalize one editable agent profile entry."""
  base_id = _agent_base_id(agent_id)
  base = fallback or {}
  src = raw if isinstance(raw, dict) else {}

  name = str(src.get("name", base.get("name", base_id))).strip() or base_id
  persona = str(src.get("persona", base.get("persona", ""))).strip()
  description = str(src.get("description", base.get("description", ""))).strip()
  skills = _sanitize_skills(src.get("skills", base.get("skills", [])))

  return {
    "name": name,
    "persona": persona,
    "description": description,
    "skills": skills,
    "version": max(0, _safe_int(src.get("version", base.get("version", 0)), 0)),
    "updated_at": str(src.get("updated_at", base.get("updated_at", ""))).strip(),
    "updated_by": str(src.get("updated_by", base.get("updated_by", ""))).strip(),
    "commit_message": str(src.get("commit_message", base.get("commit_message", ""))).strip(),
  }


def _normalize_agent_config(raw: Any) -> dict[str, Any]:
  """Normalize full config payload from disk/user input."""
  cfg = _empty_agent_config()
  if not isinstance(raw, dict):
    return cfg

  cfg["schema_version"] = max(
    1,
    _safe_int(raw.get("schema_version", AGENT_CONFIG_SCHEMA_VERSION), AGENT_CONFIG_SCHEMA_VERSION),
  )
  cfg["version"] = max(0, _safe_int(raw.get("version", 0), 0))
  cfg["updated_at"] = str(raw.get("updated_at", "")).strip()
  cfg["updated_by"] = str(raw.get("updated_by", "")).strip()

  agents_raw = raw.get("agents", {})
  agents_clean: dict[str, Any] = {}
  if isinstance(agents_raw, dict):
    for aid, entry in agents_raw.items():
      base_id = _agent_base_id(str(aid))
      if not base_id:
        continue
      agents_clean[base_id] = _normalize_agent_entry(base_id, entry)
  cfg["agents"] = agents_clean
  return cfg


def load_agent_config(out_dir: Path) -> dict[str, Any]:
  """Load and normalize persisted editable agent config."""
  path = _agent_config_path(out_dir)
  if not path.exists():
    return _empty_agent_config()

  try:
    raw = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return _empty_agent_config()

  return _normalize_agent_config(raw)


def _write_agent_config(out_dir: Path, config: dict[str, Any]) -> None:
  """Write normalized config payload atomically."""
  path = _agent_config_path(out_dir)
  path.parent.mkdir(parents=True, exist_ok=True)
  temp_path = path.with_suffix(path.suffix + ".tmp")
  temp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
  temp_path.replace(path)


def list_agent_config_backups(out_dir: Path) -> list[dict[str, Any]]:
  """Return available backups ordered from newest to oldest."""
  backup_dir = _agent_config_backup_path(out_dir)
  if not backup_dir.exists():
    return []

  backups: list[dict[str, Any]] = []
  for item in sorted(backup_dir.glob("*.json"), reverse=True):
    stat = item.stat()
    backups.append(
      {
        "name": item.name,
        "size": stat.st_size,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
      }
    )
  return backups


def _prune_agent_config_backups(out_dir: Path, *, max_backups: int = AGENT_CONFIG_MAX_BACKUPS) -> None:
  """Keep only latest backups up to max_backups."""
  if max_backups <= 0:
    return
  backup_dir = _agent_config_backup_path(out_dir)
  if not backup_dir.exists():
    return
  all_files = sorted(backup_dir.glob("*.json"), reverse=True)
  for old_file in all_files[max_backups:]:
    old_file.unlink(missing_ok=True)


def _create_agent_config_backup(out_dir: Path, *, reason: str) -> str | None:
  """Create a backup copy of current config before mutating it."""
  config_path = _agent_config_path(out_dir)
  if not config_path.exists():
    return None

  cfg = load_agent_config(out_dir)
  backup_dir = _agent_config_backup_path(out_dir)
  backup_dir.mkdir(parents=True, exist_ok=True)

  stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
  safe_reason = re.sub(r"[^a-z0-9_-]+", "-", reason.lower()).strip("-") or "update"
  name = f"{stamp}-v{_safe_int(cfg.get('version', 0), 0):04d}-{safe_reason}.json"
  target = backup_dir / name
  shutil.copy2(config_path, target)
  _prune_agent_config_backups(out_dir)
  return name


def _agent_defaults_for(agent_id: str, data: ObservatoryData | None = None) -> dict[str, Any]:
  """Build baseline editable fields from loaded observatory data."""
  base_id = _agent_base_id(agent_id)
  if data:
    for agent in data.agents:
      if _agent_base_id(agent.id) != base_id:
        continue
      name = (agent.name or agent.id or base_id).strip() if isinstance(agent.name, str) else base_id
      return {
        "name": name or base_id,
        "persona": str(agent.persona or "").strip(),
        "description": str(agent.description or "").strip(),
        "skills": [str(skill).strip() for skill in agent.capabilities if str(skill).strip()],
      }
  return {
    "name": base_id,
    "persona": "",
    "description": "",
    "skills": [],
  }


def compute_agent_config_diff(current: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
  """Compute a field-level diff for editable agent profile fields."""
  fields = ("name", "persona", "description", "skills")
  diff: list[dict[str, Any]] = []
  for field_name in fields:
    before = current.get(field_name)
    after = candidate.get(field_name)
    if before == after:
      continue
    diff.append({"field": field_name, "before": before, "after": after})
  return diff


def apply_agent_config_update(
  out_dir: Path,
  agent_id: str,
  candidate: dict[str, Any] | None,
  *,
  data: ObservatoryData | None = None,
  updated_by: str = "observatory-ui",
  commit_required: bool = False,
  commit_message: str = "",
) -> dict[str, Any]:
  """Apply one agent config update with versioning and backup semantics."""
  base_id = _agent_base_id(agent_id)
  if not base_id:
    raise ValueError("agent_id is required")

  cfg = load_agent_config(out_dir)
  baseline = _agent_defaults_for(base_id, data)
  current = _normalize_agent_entry(base_id, cfg.get("agents", {}).get(base_id, {}), fallback=baseline)
  next_entry = _normalize_agent_entry(base_id, candidate or {}, fallback=current)
  diff = compute_agent_config_diff(current, next_entry)

  if not diff:
    return {
      "changed": False,
      "agent_id": base_id,
      "diff": [],
      "config": cfg,
      "agent": current,
      "backup": None,
    }

  clean_commit_message = str(commit_message or "").strip()
  if commit_required and not clean_commit_message:
    raise ValueError("commit_message is required in commit-required mode")

  backup_name = _create_agent_config_backup(out_dir, reason="apply")

  now = _utc_now_iso()
  next_entry["version"] = _safe_int(current.get("version", 0), 0) + 1
  next_entry["updated_at"] = now
  next_entry["updated_by"] = updated_by
  next_entry["commit_message"] = clean_commit_message

  cfg["schema_version"] = AGENT_CONFIG_SCHEMA_VERSION
  cfg["version"] = _safe_int(cfg.get("version", 0), 0) + 1
  cfg["updated_at"] = now
  cfg["updated_by"] = updated_by
  agents_cfg = cfg.get("agents", {})
  if not isinstance(agents_cfg, dict):
    agents_cfg = {}
  agents_cfg[base_id] = next_entry
  cfg["agents"] = agents_cfg

  _write_agent_config(out_dir, cfg)

  return {
    "changed": True,
    "agent_id": base_id,
    "diff": diff,
    "config": cfg,
    "agent": next_entry,
    "backup": backup_name,
  }


def rollback_agent_config(
  out_dir: Path,
  *,
  backup_name: str = "",
  updated_by: str = "observatory-ui",
) -> dict[str, Any]:
  """Restore config from latest or named backup and bump global version."""
  backups = list_agent_config_backups(out_dir)
  if not backups:
    raise FileNotFoundError("No agent config backup available")

  target_name = backup_name.strip() if backup_name else backups[0]["name"]
  target_path = _agent_config_backup_path(out_dir) / target_name
  if not target_path.exists():
    raise FileNotFoundError(f"Backup not found: {target_name}")

  previous = load_agent_config(out_dir)
  rollback_backup = _create_agent_config_backup(out_dir, reason="rollback")
  restored_raw = json.loads(target_path.read_text(encoding="utf-8"))
  restored = _normalize_agent_config(restored_raw)

  restored["version"] = max(
    _safe_int(previous.get("version", 0), 0) + 1,
    _safe_int(restored.get("version", 0), 0) + 1,
  )
  restored["updated_at"] = _utc_now_iso()
  restored["updated_by"] = updated_by
  _write_agent_config(out_dir, restored)

  return {
    "restored": target_name,
    "rollback_backup": rollback_backup,
    "config": restored,
  }


def _apply_agent_config_overrides(agents: list[AgentNode], config: dict[str, Any]) -> list[AgentNode]:
  """Apply editable profile overrides to parsed agent graph nodes."""
  for agent in agents:
    if not agent.name:
      agent.name = agent.id

  overrides = config.get("agents", {}) if isinstance(config, dict) else {}
  if not isinstance(overrides, dict) or not overrides:
    return agents

  index: dict[str, AgentNode] = {}
  for agent in agents:
    base_id = _agent_base_id(agent.id)
    if base_id and base_id not in index:
      index[base_id] = agent

  for aid, raw_entry in overrides.items():
    base_id = _agent_base_id(str(aid))
    if not base_id:
      continue
    fallback = _agent_defaults_for(base_id)
    entry = _normalize_agent_entry(base_id, raw_entry, fallback=fallback)
    node = index.get(base_id)
    if node is None:
      node = AgentNode(
        id=base_id,
        name=entry["name"],
        persona=entry["persona"],
        description=entry["description"],
        capabilities=entry["skills"],
      )
      agents.append(node)
      index[base_id] = node
      continue

    node.name = entry["name"] or node.name or node.id
    node.persona = entry["persona"]
    node.description = entry["description"]
    node.capabilities = entry["skills"]

  return agents


# ── Aggregate ────────────────────────────────────────────────────────────────


def _find_output_dir(project_root: Path) -> Path:
  """Find the output directory across current and runtime layouts.

  Prefers directories that actually contain trace data.
  """
  candidates = [
    project_root / OUTPUT_DIR,
    project_root / RUNTIME_OUTPUT_DIR,
  ]
  for directory in candidates:
    if directory.is_dir():
      for name in TRACE_FILE_CANDIDATES:
        if (directory / name).exists():
          return directory

  for directory in candidates:
    if directory.is_dir():
      return directory

  return project_root / OUTPUT_DIR


def _find_trace(out_dir: Path) -> Path:
  """Find trace file — supports multiple naming conventions."""
  candidates = [out_dir / name for name in TRACE_FILE_CANDIDATES]
  for trace_file in candidates:
    if trace_file.exists():
      return trace_file
  return out_dir / TRACE_FILE


def load_all(project_root: Path, output_dir: Path | None = None) -> ObservatoryData:
    """Load all Grimoire data sources."""
    out_dir = output_dir or _find_output_dir(project_root)

    traces, sessions = parse_trace(_find_trace(out_dir))
    events = parse_event_log(out_dir / EVENT_LOG_FILE)
    agents, rels = parse_agent_graph(out_dir / AGENT_GRAPH_FILE)
    agent_config = load_agent_config(out_dir)
    agents = _apply_agent_config_overrides(agents, agent_config)
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
        agent_config=agent_config,
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
        "agent_config": data.agent_config,
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
.gantt-bar.blocked{background:rgba(240,143,62,.2);border:1px solid #f0883e;color:#f0883e}
.gantt-bar.paused{background:rgba(88,166,255,.2);border:1px solid var(--accent);color:var(--accent)}
.gantt-bar.retrying{background:rgba(210,153,34,.2);border:1px solid var(--yellow);color:var(--yellow);animation:barPulse 1.2s infinite}
.gantt-bar.cancelled{background:rgba(139,148,158,.22);border:1px solid var(--fg2);color:var(--fg2);text-decoration:line-through}
.gantt-bar.skipped{background:rgba(188,140,255,.2);border:1px solid var(--purple);color:var(--purple)}
.gantt-bar.timeout{background:rgba(248,81,73,.28);border:1px solid var(--red);color:var(--red)}
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
.agent-card .ac-actions{display:flex;justify-content:flex-end;margin-top:8px}
.agent-card .ac-btn{background:var(--bg3);border:1px solid var(--border);color:var(--fg2);font-size:.72rem;border-radius:4px;padding:3px 8px;cursor:pointer}
.agent-card .ac-btn:hover{border-color:var(--accent);color:var(--accent)}
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

/* ── Office View (Pixel Art Game) ───────── */
.office-wrap{position:relative;width:100%;height:calc(100vh - 200px);min-height:400px;background:#1a1a2e;border-radius:8px;overflow:hidden;cursor:grab}
.office-wrap:active{cursor:grabbing}
.office-wrap canvas{display:block;image-rendering:pixelated;image-rendering:crisp-edges}
.office-hud{position:absolute;top:10px;left:10px;display:flex;gap:8px;z-index:10}
.office-hud .hud-btn{background:rgba(22,27,34,.85);border:1px solid var(--border);color:var(--fg2);border-radius:6px;padding:4px 10px;font-size:.75rem;cursor:pointer;backdrop-filter:blur(4px)}
.office-hud .hud-btn:hover{color:var(--fg);border-color:var(--accent)}
.office-hud .hud-btn.active{color:var(--accent);border-color:var(--accent)}
.office-minimap{position:absolute;bottom:10px;right:10px;border:1px solid var(--border);border-radius:4px;background:rgba(13,17,23,.9);z-index:10;cursor:crosshair}
.office-info{position:absolute;top:10px;right:10px;background:rgba(22,27,34,.9);border:1px solid var(--border);border-radius:8px;padding:10px 14px;z-index:10;min-width:200px;font-size:.78rem;backdrop-filter:blur(4px)}
.office-info .oi-name{font-weight:700;font-size:.9rem;margin-bottom:4px}
.office-info .oi-state{display:flex;align-items:center;gap:6px;margin:3px 0}
.office-info .oi-trust{height:4px;background:var(--bg3);border-radius:2px;margin:4px 0;overflow:hidden}
.office-info .oi-trust-fill{height:100%;border-radius:2px;transition:width .3s}
.office-info .oi-tools{display:flex;flex-wrap:wrap;gap:3px;margin-top:6px}
.office-info .oi-tool{font-size:.65rem;padding:1px 5px;border-radius:8px;background:var(--bg3);color:var(--fg2);cursor:pointer;border:1px solid transparent}
.office-info .oi-tool:hover{border-color:var(--accent)}
.office-info .oi-tool.active{background:rgba(88,166,255,.15);color:var(--accent);border-color:var(--accent)}
.office-info .oi-actions{display:flex;gap:6px;margin-top:8px}
.office-info .oi-btn{background:var(--bg3);border:1px solid var(--border);color:var(--fg2);font-size:.72rem;border-radius:4px;padding:3px 8px;cursor:pointer}
.office-info .oi-btn:hover{border-color:var(--accent);color:var(--accent)}

/* ── Agent Config Drawer ─────────────────── */
.cfg-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.cfg-meta{font-size:.72rem;color:var(--fg2)}
.cfg-form{display:flex;flex-direction:column;gap:10px}
.cfg-group{display:flex;flex-direction:column;gap:4px}
.cfg-group label{font-size:.72rem;color:var(--fg2);text-transform:uppercase;letter-spacing:.4px}
.cfg-input,.cfg-textarea{background:var(--bg);border:1px solid var(--border);color:var(--fg);border-radius:6px;padding:8px;font-size:.82rem;font-family:var(--mono)}
.cfg-textarea{min-height:82px;resize:vertical;font-family:var(--font)}
.cfg-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:4px}
.cfg-btn{background:var(--bg3);border:1px solid var(--border);color:var(--fg);padding:6px 10px;border-radius:6px;font-size:.78rem;cursor:pointer}
.cfg-btn:hover{border-color:var(--accent);color:var(--accent)}
.cfg-btn.primary{background:rgba(88,166,255,.15);border-color:var(--accent);color:var(--accent)}
.cfg-btn.warn{background:rgba(248,81,73,.12);border-color:rgba(248,81,73,.35);color:#ff938e}
.cfg-status{font-size:.76rem;color:var(--fg2);margin-top:2px;word-break:break-word}
.cfg-status.ok{color:var(--green)}
.cfg-status.warn{color:var(--yellow)}
.cfg-status.err{color:var(--red)}
.cfg-diff{margin-top:8px;padding:8px;border-radius:6px;background:var(--bg);border:1px solid var(--border);max-height:180px;overflow:auto;font-size:.76rem;font-family:var(--mono);white-space:pre-wrap}

/* ── Timeline Bar (Global) ──────────────── */
.timeline-bar{position:fixed;bottom:0;left:0;right:0;background:var(--bg2);border-top:1px solid var(--border);z-index:100;padding:6px 16px;display:flex;align-items:center;gap:10px;height:52px}
.tbar-controls{display:flex;align-items:center;gap:4px}
.tbar-btn{background:none;border:none;color:var(--fg2);cursor:pointer;font-size:1rem;padding:2px 4px;border-radius:4px;line-height:1}
.tbar-btn:hover{color:var(--fg);background:var(--bg3)}
.tbar-btn.active{color:var(--accent)}
.tbar-scrub{flex:1;position:relative;height:28px;cursor:pointer}
.tbar-track{position:absolute;top:12px;left:0;right:0;height:4px;background:var(--bg3);border-radius:2px;overflow:hidden}
.tbar-heat{position:absolute;top:0;left:0;right:0;height:100%;opacity:.6}
.tbar-progress{position:absolute;top:0;left:0;height:100%;background:var(--accent);border-radius:2px;transition:width 50ms linear}
.tbar-thumb{position:absolute;top:8px;width:12px;height:12px;border-radius:50%;background:var(--accent);border:2px solid var(--bg2);transform:translateX(-50%);cursor:grab;z-index:2;transition:left 50ms linear}
.tbar-thumb:hover{transform:translateX(-50%) scale(1.3)}
.tbar-markers{position:absolute;top:0;left:0;right:0;height:10px}
.tbar-marker{position:absolute;top:0;width:2px;height:8px;border-radius:1px;opacity:.5}
.tbar-time{font-family:var(--mono);font-size:.72rem;color:var(--fg2);min-width:70px;text-align:center}
.tbar-speed{background:var(--bg3);border:1px solid var(--border);color:var(--fg2);border-radius:4px;padding:2px 6px;font-size:.72rem;font-family:var(--mono);cursor:pointer;min-width:36px;text-align:center}
.tbar-speed:hover{border-color:var(--accent);color:var(--fg)}
.tbar-session{background:var(--bg3);border:1px solid var(--border);color:var(--fg2);border-radius:4px;padding:2px 8px;font-size:.72rem;font-family:var(--mono)}
.tbar-live{background:#238636;color:#fff;font-size:.65rem;padding:2px 8px;border-radius:10px;cursor:pointer;border:none;animation:livePulse 2s infinite}
body{padding-bottom:56px}
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
  <div class="tab" data-view="office">&#127918; Office <span class="kbd">O</span></div>
  <div class="tab active" data-view="overview">Overview <span class="kbd">0</span></div>
  <div class="tab" data-view="timeline">Timeline <span class="kbd">1</span></div>
  <div class="tab" data-view="swimlane">Swimlane <span class="kbd">2</span></div>
  <div class="tab" data-view="dag">DAG <span class="kbd">3</span></div>
  <div class="tab" data-view="graph">Network <span class="kbd">4</span></div>
  <div class="tab" data-view="tracelog">Log <span class="kbd">5</span></div>
  <div class="tab" data-view="metrics">Metrics <span class="kbd">6</span></div>
</div>

<main>
  <!-- Office (Pixel Art Game) -->
  <div class="view" id="view-office">
    <div class="office-wrap" id="office-wrap">
      <canvas id="office-cv"></canvas>
      <div class="office-hud">
        <button class="hud-btn" id="hud-grid" title="Toggle grille (G)">&#9638; Grid</button>
        <button class="hud-btn" id="hud-names" title="Toggle noms (N)">&#128100; Names</button>
        <button class="hud-btn" id="hud-trust" title="Toggle trust bars (T)">&#128737; Trust</button>
        <button class="hud-btn active" id="hud-bubbles" title="Toggle bulles (B)">&#128172; Bulles</button>
        <button class="hud-btn" id="hud-reset" title="Recentrer (R)">&#127919; Reset</button>
      </div>
      <canvas class="office-minimap" id="office-minimap" width="160" height="120"></canvas>
      <div class="office-info" id="office-info" style="display:none"></div>
    </div>
  </div>

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

<!-- Timeline Bar (Global) -->
<div class="timeline-bar" id="timeline-bar">
  <div class="tbar-controls">
    <button class="tbar-btn" id="tbar-start" title="D&eacute;but">&#9198;</button>
    <button class="tbar-btn" id="tbar-prev" title="Pr&eacute;c&eacute;dent">&#9664;</button>
    <button class="tbar-btn" id="tbar-play" title="Play/Pause (Space)">&#9654;</button>
    <button class="tbar-btn" id="tbar-next" title="Suivant">&#9654;</button>
    <button class="tbar-btn" id="tbar-end" title="Fin">&#9197;</button>
  </div>
  <span class="tbar-time" id="tbar-current">--:--:--</span>
  <div class="tbar-scrub" id="tbar-scrub">
    <div class="tbar-track">
      <canvas class="tbar-heat" id="tbar-heat-cv"></canvas>
      <div class="tbar-progress" id="tbar-progress"></div>
    </div>
    <div class="tbar-markers" id="tbar-markers"></div>
    <div class="tbar-thumb" id="tbar-thumb"></div>
  </div>
  <span class="tbar-time" id="tbar-total">--:--:--</span>
  <button class="tbar-speed" id="tbar-speed" title="Vitesse">1x</button>
  <select class="tbar-session" id="tbar-session"><option value="">All</option></select>
  <button class="tbar-live" id="tbar-live">LIVE</button>
</div>

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

function normalizeTaskStatus(raw, fallback='waiting') {
  const s = (raw || '').toString().trim().toLowerCase();
  if (!s) return fallback;
  if (['done','success','succeeded','complete','completed','approved','validated','ok','pass','passed'].includes(s)) return 'done';
  if (['running','in_progress','active','resumed','processing'].includes(s)) return 'running';
  if (['waiting','pending','queued','ready','on_hold'].includes(s)) return 'waiting';
  if (['blocked','stalled'].includes(s)) return 'blocked';
  if (['paused','suspended'].includes(s)) return 'paused';
  if (['retrying','retried','retry'].includes(s)) return 'retrying';
  if (['failed','error','errored','invalidated','rejected'].includes(s)) return 'failed';
  if (['timeout','timed_out'].includes(s)) return 'timeout';
  if (['cancelled','canceled','abandoned'].includes(s)) return 'cancelled';
  if (['skipped','skip'].includes(s)) return 'skipped';
  return fallback;
}

function inferTaskStatusFromEvent(e) {
  const payload = e.payload || {};
  const explicit = normalizeTaskStatus(payload.status || payload.state || payload.task_status || '', '');
  if (explicit) return explicit;

  const t = (e.type || '').toLowerCase();
  if (t.includes('fail') || t.includes('error')) return 'failed';
  if (t.includes('timeout') || t.includes('timed_out')) return 'timeout';
  if (t.includes('cancel')) return 'cancelled';
  if (t.includes('skip')) return 'skipped';
  if (t.includes('block')) return 'blocked';
  if (t.includes('pause')) return 'paused';
  if (t.includes('retry')) return 'retrying';
  if (t.includes('complete') || t.includes('done') || t.includes('success')) return 'done';
  if (t.includes('start') || t.includes('run') || t.includes('resume')) return 'running';
  if (t.includes('pending') || t.includes('queued') || t.includes('wait') || t.includes('ready')) return 'waiting';
  return 'waiting';
}

function agentStateFromItem(item) {
  const typeL = (item.type || '').toLowerCase();
  const payloadL = (item.payload || '').toLowerCase();
  const text = `${typeL} ${payloadL}`;

  if (/(error|fail|timeout|timed_out|invalidat|reject|hup)/.test(text)) return 'error';
  if (/(cancel|skip|abort|blocked|block|pending|queued|ready|pause|paused|wait|qec|question)/.test(text)) return 'waiting';
  if (/(complete|done|success|approved|validated|aggregation|checkpoint)/.test(text)) return 'celebrating';
  if (/(handoff|dispatch|route|routing|debate|decision|vote|cross_validation|trust_scored|graph_update|sync)/.test(text)) return 'speaking';
  if (/(read|search|review|check|audit|analy|inspect|validate|test)/.test(text)) return 'reading';
  if (/(implement|edit|write|action|task_started|artifact_created|create|fix|refactor|build|code)/.test(text)) return 'typing';
  if (/(activated|idle)/.test(text)) return 'idle';
  return 'typing';
}

// ── Stats ───────────────────────────────────────────────────
$('#stat-traces').textContent = DATA.traces.length;
$('#stat-events').textContent = DATA.events.length;
$('#stat-agents').textContent = DATA.agent_ids.length;
$('#stat-sessions').textContent = DATA.sessions.length;

// ── Tabs + Keyboard ─────────────────────────────────────────
const TAB_VIEWS = ['office','overview','timeline','swimlane','dag','graph','tracelog','metrics'];
function switchTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.view===name));
  $$('.view').forEach(v => v.classList.toggle('active', v.id==='view-'+name));
  if (name==='office') initOffice();
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
  if (n >= 0 && n <= 6) { e.preventDefault(); switchTab(TAB_VIEWS[n+1]); }
  if (e.key.toLowerCase() === 'o') { e.preventDefault(); switchTab('office'); }
  if (e.key === 'Escape') closeDrawer();
  if (e.key === ' ' && !e.target.closest('.filters')) { e.preventDefault(); tbarTogglePlay(); }
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

// ── Agent Config Editor ───────────────────────────────────
const IS_HTTP = window.location.protocol.startsWith('http');
const AGENT_CONFIG = {
  commitRequired: false,
  readOnly: false,
  config: (DATA.agent_config && typeof DATA.agent_config === 'object') ? DATA.agent_config : {agents:{}},
  backups: [],
};

function canonicalAgentId(agentId) {
  return (agentId || '').toString().split('/')[0].trim();
}

function normalizeSkillsInput(raw) {
  const items = Array.isArray(raw) ? raw : (raw || '').toString().split(',');
  const out = [];
  const seen = new Set();
  items.forEach(item => {
    const skill = (item || '').toString().trim();
    if (!skill || seen.has(skill)) return;
    seen.add(skill);
    out.push(skill);
  });
  return out;
}

function getCurrentEditableProfile(agentId) {
  const baseId = canonicalAgentId(agentId);
  const base = DATA.agents.find(a => canonicalAgentId(a.id) === baseId || a.id === baseId) || {id:baseId,name:baseId,persona:'',description:'',capabilities:[]};
  const overrides = (AGENT_CONFIG.config && AGENT_CONFIG.config.agents) ? AGENT_CONFIG.config.agents : {};
  const entry = overrides[baseId] || {};
  const skills = Array.isArray(entry.skills) ? entry.skills : (Array.isArray(base.capabilities) ? base.capabilities : []);
  return {
    name: (entry.name || base.name || base.id || baseId || '').toString().trim() || baseId,
    persona: entry.persona !== undefined ? (entry.persona || '').toString() : (base.persona || '').toString(),
    description: entry.description !== undefined ? (entry.description || '').toString() : (base.description || '').toString(),
    skills: normalizeSkillsInput(skills),
    version: Number(entry.version || 0),
  };
}

function computeLocalConfigDiff(current, candidate) {
  const fields = ['name', 'persona', 'description', 'skills'];
  const diff = [];
  fields.forEach(field => {
    const before = current[field];
    const after = candidate[field];
    const same = Array.isArray(before) || Array.isArray(after)
      ? JSON.stringify(before || []) === JSON.stringify(after || [])
      : before === after;
    if (!same) diff.push({field, before, after});
  });
  return diff;
}

function diffToText(diff) {
  if (!diff.length) return 'Aucun changement détecté.';
  const fmt = v => {
    if (Array.isArray(v)) return `[${v.join(', ')}]`;
    if (v === null || v === undefined || v === '') return '∅';
    return String(v);
  };
  return diff.map(d => `- ${d.field}\n  before: ${fmt(d.before)}\n  after : ${fmt(d.after)}`).join('\n\n');
}

function setCfgStatus(text, kind) {
  const el = $('#cfg-status');
  if (!el) return;
  el.classList.remove('ok', 'warn', 'err');
  if (kind) el.classList.add(kind);
  el.textContent = text || '';
}

function readCfgFormCandidate(agentId) {
  return {
    name: ($('#cfg-name').value || canonicalAgentId(agentId)).trim(),
    persona: ($('#cfg-persona').value || '').trim(),
    description: ($('#cfg-description').value || '').trim(),
    skills: normalizeSkillsInput($('#cfg-skills').value || ''),
  };
}

async function refreshAgentConfigMeta() {
  if (!IS_HTTP) return AGENT_CONFIG;
  const response = await fetch('/api/agent-config', {cache:'no-cache'});
  if (!response.ok) {
    throw new Error(`API config unavailable (${response.status})`);
  }
  const payload = await response.json();
  AGENT_CONFIG.commitRequired = !!payload.commit_required;
  AGENT_CONFIG.readOnly = !!payload.read_only;
  AGENT_CONFIG.config = payload.config || {agents:{}};
  AGENT_CONFIG.backups = Array.isArray(payload.backups) ? payload.backups : [];
  DATA.agent_config = AGENT_CONFIG.config;
  return payload;
}

async function requestConfigDiff(agentId, candidate) {
  if (!IS_HTTP) {
    const current = getCurrentEditableProfile(agentId);
    return {diff: computeLocalConfigDiff(current, candidate), current};
  }
  const response = await fetch('/api/agent-config/diff', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({agent_id: canonicalAgentId(agentId), candidate}),
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Diff request failed (${response.status})`);
  }
  return payload;
}

async function requestConfigApply(agentId, candidate, commitMessage) {
  const response = await fetch('/api/agent-config/apply', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      agent_id: canonicalAgentId(agentId),
      candidate,
      commit_message: commitMessage || '',
    }),
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Apply failed (${response.status})`);
  }
  return payload;
}

async function requestRollbackLatest() {
  const response = await fetch('/api/agent-config/rollback', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({}),
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Rollback failed (${response.status})`);
  }
  return payload;
}

async function openAgentConfigDrawer(agentRef) {
  const agentId = canonicalAgentId(agentRef);
  if (!agentId) return;

  try {
    await refreshAgentConfigMeta();
  } catch (err) {
    console.warn('Agent config metadata unavailable:', err);
  }

  const current = getCurrentEditableProfile(agentId);
  const modeText = AGENT_CONFIG.readOnly
    ? 'read-only'
    : (AGENT_CONFIG.commitRequired ? 'commit-required' : 'safe-edit');
  const backupText = AGENT_CONFIG.backups.length ? `${AGENT_CONFIG.backups.length} backups` : 'aucun backup';
  const canMutate = IS_HTTP && !AGENT_CONFIG.readOnly;

  const html = `
    <div class="cfg-head">
      <h2>⚙ Agent Config — ${esc(agentId)}</h2>
      <div class="cfg-meta">Mode: ${esc(modeText)} · Version: v${esc(String(current.version || 0))}</div>
    </div>
    <div class="cfg-form">
      <div class="cfg-group">
        <label>Nom</label>
        <input id="cfg-name" class="cfg-input" type="text" value="${esc(current.name || agentId)}" maxlength="80">
      </div>
      <div class="cfg-group">
        <label>Persona</label>
        <input id="cfg-persona" class="cfg-input" type="text" value="${esc(current.persona || '')}" maxlength="120">
      </div>
      <div class="cfg-group">
        <label>Description</label>
        <textarea id="cfg-description" class="cfg-textarea" maxlength="500">${esc(current.description || '')}</textarea>
      </div>
      <div class="cfg-group">
        <label>Skills (comma-separated)</label>
        <input id="cfg-skills" class="cfg-input" type="text" value="${esc((current.skills || []).join(', '))}" placeholder="tdd, implementation, debug">
      </div>
      <div class="cfg-group">
        <label>Commit Message ${AGENT_CONFIG.commitRequired ? '(requis)' : '(optionnel)'}</label>
        <input id="cfg-commit" class="cfg-input" type="text" value="" maxlength="180" placeholder="why this change">
      </div>
      <div class="cfg-actions">
        <button class="cfg-btn" id="cfg-btn-diff">Diff</button>
        <button class="cfg-btn primary" id="cfg-btn-apply" ${canMutate ? '' : 'disabled'}>Apply</button>
        <button class="cfg-btn warn" id="cfg-btn-rollback" ${canMutate ? '' : 'disabled'}>Rollback dernier</button>
      </div>
      <div class="cfg-status${canMutate ? '' : ' warn'}" id="cfg-status">${canMutate ? `État: prêt · ${esc(backupText)}` : (AGENT_CONFIG.readOnly ? 'Mode lecture seule: apply/rollback désactivés.' : 'Mode statique: API indisponible, édition désactivée.')}</div>
      <pre class="cfg-diff" id="cfg-diff">Diff en attente...</pre>
    </div>
  `;

  openDrawer(html);

  $('#cfg-btn-diff').addEventListener('click', async () => {
    const candidate = readCfgFormCandidate(agentId);
    setCfgStatus('Calcul du diff…', 'warn');
    try {
      const result = await requestConfigDiff(agentId, candidate);
      const diff = result.diff || [];
      $('#cfg-diff').textContent = diffToText(diff);
      setCfgStatus(diff.length ? `Diff prêt: ${diff.length} changement(s)` : 'Aucun changement détecté', diff.length ? 'ok' : 'warn');
    } catch (err) {
      $('#cfg-diff').textContent = String(err.message || err);
      setCfgStatus(String(err.message || err), 'err');
    }
  });

  $('#cfg-btn-apply').addEventListener('click', async () => {
    if (!IS_HTTP) {
      setCfgStatus('Mode statique: apply indisponible.', 'warn');
      return;
    }
    if (AGENT_CONFIG.readOnly) {
      setCfgStatus('Mode lecture seule: apply désactivé.', 'warn');
      return;
    }

    const candidate = readCfgFormCandidate(agentId);
    const commitMessage = ($('#cfg-commit').value || '').trim();
    if (AGENT_CONFIG.commitRequired && !commitMessage) {
      setCfgStatus('Commit message requis en mode commit-required.', 'err');
      return;
    }

    setCfgStatus('Apply en cours…', 'warn');
    try {
      const result = await requestConfigApply(agentId, candidate, commitMessage);
      $('#cfg-diff').textContent = diffToText(result.diff || []);
      const backupMsg = result.backup ? ` · backup ${result.backup}` : '';
      setCfgStatus(`Apply réussi (v${(result.agent && result.agent.version) || '?'}${backupMsg})`, 'ok');
      setTimeout(() => window.location.reload(), 450);
    } catch (err) {
      setCfgStatus(String(err.message || err), 'err');
    }
  });

  $('#cfg-btn-rollback').addEventListener('click', async () => {
    if (!IS_HTTP) {
      setCfgStatus('Mode statique: rollback indisponible.', 'warn');
      return;
    }
    if (AGENT_CONFIG.readOnly) {
      setCfgStatus('Mode lecture seule: rollback désactivé.', 'warn');
      return;
    }
    if (!confirm('Rollback vers le backup le plus récent ?')) return;
    setCfgStatus('Rollback en cours…', 'warn');
    try {
      const result = await requestRollbackLatest();
      setCfgStatus(`Rollback appliqué: ${result.restored || 'backup inconnu'}`, 'ok');
      setTimeout(() => window.location.reload(), 450);
    } catch (err) {
      setCfgStatus(String(err.message || err), 'err');
    }
  });
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
  const terminalStatuses = new Set(['done','failed','timeout','cancelled','skipped']);

  DATA.events.forEach(e => {
    const payload = e.payload || {};
    const eventType = (e.type || '').toLowerCase();
    const isTaskEvent = eventType.startsWith('task_') || Boolean(payload.task_id);
    if (!isTaskEvent) return;

    const id = payload.task_id || e.correlation_id || e.id;
    const status = inferTaskStatusFromEvent(e);

    if (!tasks.has(id)) {
      tasks.set(id, {
        id,
        agent: e.agent,
        desc: payload.description || id,
        status,
        start: e.ts,
        end: null,
        deps: Array.isArray(payload.depends_on) ? payload.depends_on : [],
        trust: typeof payload.trust_score === 'number' ? payload.trust_score : null,
      });
    }

    const t = tasks.get(id);
    t.agent = t.agent || e.agent;
    if (payload.description) t.desc = payload.description;
    if (Array.isArray(payload.depends_on)) t.deps = payload.depends_on;
    t.status = status;
    if (typeof payload.trust_score === 'number') t.trust = payload.trust_score;
    if (!t.start) t.start = e.ts;
    if (terminalStatuses.has(status)) t.end = e.ts;
    if (status === 'running' || status === 'retrying') t.end = null;
  });

  // Also from HPE traces
  DATA.traces.forEach(t => {
    if (t.event_type === 'HPE:dispatch') {
      const m = t.payload.match(/task=([^\s|]+)/);
      if (m) {
        const id = m[1];
        if (!tasks.has(id)) {
          tasks.set(id, {id, agent:'', desc:id, status:'running', start:t.timestamp, end:null, deps:[], trust:null});
        } else {
          const tk = tasks.get(id);
          tk.status = 'running';
          if (!tk.start) tk.start = t.timestamp;
          tk.end = null;
        }
      }
    }
    if (t.event_type === 'HPE:ready') {
      const m = t.payload.match(/task=([^\s|]+)/);
      if (m && !tasks.has(m[1])) {
        tasks.set(m[1], {id:m[1], agent:'', desc:m[1], status:'waiting', start:t.timestamp, end:null, deps:[], trust:null});
      }
    }
    if (t.event_type === 'HPE:complete') {
      const m = t.payload.match(/task=([^\s|]+)/);
      const s = t.payload.match(/status=([^\s|]+)/);
      if (!m) return;

      const id = m[1];
      const normalized = normalizeTaskStatus(s ? s[1] : 'done', 'done');
      if (!tasks.has(id)) {
        tasks.set(id, {id, agent:'', desc:id, status:normalized, start:t.timestamp, end:t.timestamp, deps:[], trust:null});
      }
      if (tasks.has(id)) {
        const tk = tasks.get(id);
        if (!tk.start) tk.start = t.timestamp;
        tk.status = normalized;
        if (terminalStatuses.has(normalized)) tk.end = t.timestamp;
      }
      const trustM = t.payload.match(/trust=(\d+)/);
      if (trustM && tasks.has(id)) tasks.get(id).trust = parseInt(trustM[1]);
    }
  });

  if (!tasks.size) { ct.innerHTML = `<div class="empty-state"><div class="icon">🔀</div><h2>Aucun DAG détecté</h2><p>Le moteur HPE n'a pas encore planifié de tâches.<br>Événements attendus : <code>task_started</code> / <code>task_completed</code></p></div>`; return; }

  // Sort by start time, then topologically
  const taskList = [...tasks.values()].sort((a,b) => (a.start||'').localeCompare(b.start||''));

  // Time range
  const allTimes = taskList.flatMap(t => [t.start, t.end].filter(Boolean)).map(t => new Date(t).getTime());
  const minT = Math.min(...allTimes), maxT = Math.max(...allTimes);
  const range = maxT - minT || 1;

  let html = `<div class="gantt-header"><h3>📊 Task DAG — ${taskList.length} tâches</h3><div class="gantt-legend"><span class="gl-item"><span class="gl-dot" style="background:var(--green)"></span>Done</span><span class="gl-item"><span class="gl-dot" style="background:var(--yellow)"></span>Running/Retry</span><span class="gl-item"><span class="gl-dot" style="background:var(--border)"></span>Waiting/Pending</span><span class="gl-item"><span class="gl-dot" style="background:#f0883e"></span>Blocked/Paused</span><span class="gl-item"><span class="gl-dot" style="background:var(--fg2)"></span>Cancelled/Skipped</span><span class="gl-item"><span class="gl-dot" style="background:var(--red)"></span>Failed/Timeout</span></div></div>`;
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
    const displayName = a.name || a.id;
    let capsHtml = (a.capabilities||[]).map(c => `<span class="ac-cap">${esc(c)}</span>`).join('');
    // Emergent capabilities
    (a.emergent_capabilities||[]).forEach(ec => {
      if (ec.skill) capsHtml += `<span class="ac-cap" style="border:1px solid ${color};color:${color}">✨ ${esc(ec.skill)}</span>`;
    });
    return `<div class="agent-card">
      <div class="ac-header"><div class="ac-avatar" style="border-color:${color};color:${color}">${initials}</div><div><div class="ac-name" style="color:${color}">${esc(displayName)}</div><div class="ac-persona">${esc(a.persona||'')}</div></div></div>
      <div class="ac-stats">
        ${m.tasks_completed?`<div class="ac-stat"><span class="label">Tasks</span><span>${m.tasks_completed}</span></div>`:''}
        ${m.avg_trust_score?`<div class="ac-stat"><span class="label">Trust</span><span style="color:${m.avg_trust_score>=90?'var(--green)':m.avg_trust_score>=70?'var(--yellow)':'var(--red)'}">${m.avg_trust_score}</span></div>`:''}
        ${m.cross_validations_passed?`<div class="ac-stat"><span class="label">CVTL ✓</span><span>${m.cross_validations_passed}</span></div>`:''}
        ${m.hup_red_count!==undefined?`<div class="ac-stat"><span class="label">HUP 🔴</span><span>${m.hup_red_count}</span></div>`:''}
      </div>
      ${capsHtml?`<div class="ac-caps">${capsHtml}</div>`:''}
      <div class="ac-actions"><button class="ac-btn ac-config-btn" data-agent-id="${esc(a.id)}">⚙ Config</button></div>
    </div>`;
  }).join('');

  $$('.ac-config-btn', sidebar).forEach(btn => {
    btn.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      openAgentConfigDrawer(btn.dataset.agentId || '');
    });
  });
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
  cards.push({icon:'&#128209;',label:'Traces',value:DATA.traces.length,cls:DATA.traces.length>0?'good':'',sub:'GRIMOIRE_TRACE.md'});
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

// ══════════════════════════════════════════════════════════════
// PIXEL OFFICE — Game Engine
// ══════════════════════════════════════════════════════════════

// ── Sprite Factory ──────────────────────────────────────────
const TILE = 16;
const AGENT_THEME = {
  dev:{pri:'#3fb950',sec:'#2ea043',skin:'#f5d6b8',hair:'#5c3317',hat:null,item:'laptop'},
  qa:{pri:'#bc8cff',sec:'#a371f7',skin:'#d4a373',hair:'#1a1a1a',hat:null,item:'magnify'},
  architect:{pri:'#39d2c0',sec:'#2db7a8',skin:'#f5d6b8',hair:'#8b6914',hat:'hardhat',item:'blueprint'},
  pm:{pri:'#f0883e',sec:'#d68030',skin:'#c68642',hair:'#1a1a1a',hat:null,item:'clipboard'},
  analyst:{pri:'#d29922',sec:'#bf8700',skin:'#f5d6b8',hair:'#a0522d',hat:'glasses',item:'chart'},
  sm:{pri:'#f778ba',sec:'#db61a2',skin:'#e8beac',hair:'#daa520',hat:null,item:'postit'},
  techwr:{pri:'#7ee787',sec:'#56d364',skin:'#f5d6b8',hair:'#2f1b14',hat:null,item:'pen'},
  orchestr:{pri:'#58a6ff',sec:'#388bfd',skin:'#f5d6b8',hair:'#4a4a4a',hat:'wizard',item:'wand'},
  tea:{pri:'#f85149',sec:'#da3633',skin:'#d4a373',hair:'#1a1a1a',hat:'badge',item:'terminal'},
  default:{pri:'#8b949e',sec:'#6e7681',skin:'#f5d6b8',hair:'#4a4a4a',hat:null,item:null}
};

const _spriteCache = new Map();
function getSprite(key, w, h, drawFn) {
  if (_spriteCache.has(key)) return _spriteCache.get(key);
  const c = document.createElement('canvas'); c.width=w; c.height=h;
  const x = c.getContext('2d'); x.imageSmoothingEnabled=false;
  drawFn(x, w, h);
  _spriteCache.set(key, c);
  return c;
}

function drawPixel(ctx, x, y, color, s) { s=s||1; ctx.fillStyle=color; ctx.fillRect(x*s,y*s,s,s); }

const OFFICE_EXTERNAL_ASSET_FILES = {
  floor: ['floors/floor_parquet_warm_v01.png', 'floors/floor_tiles_mono_v01.png'],
  wall: ['walls/wall_brick_dark_v01.png', 'walls/wall_seed_01_v01.png'],
  desk: ['furniture/furniture_desk_front_seed_v01.png', 'furniture/furniture_desk_side_seed_v01.png'],
  plant: ['furniture/furniture_plant_seed_v01.png', 'furniture/terrarium_v01.png'],
  whiteboard: ['furniture/furniture_whiteboard_seed_v01.png', 'furniture/blueprint_wall_v01.png'],
  coffee: ['furniture/furniture_coffee_seed_v01.png', 'furniture/water_cooler_v01.png'],
  characters: [
    'characters/character_seed_01_v01.png',
    'characters/character_seed_02_v01.png',
    'characters/character_seed_03_v01.png',
    'characters/character_seed_04_v01.png',
    'characters/character_seed_05_v01.png',
    'characters/character_seed_06_v01.png',
  ]
};

const officeExternalAssets = {
  ready: false,
  loadStarted: false,
  loadedCount: 0,
  totalCount: 0,
  floor: null,
  wall: null,
  desk: null,
  plant: null,
  whiteboard: null,
  coffee: null,
  characters: [],
};

function buildAssetPrefixCandidates() {
  const prefixes = new Set([
    '/_grimoire-output/assets/',
    '../_grimoire-output/assets/',
  ]);

  const pathname = window.location.pathname || '';
  if (pathname.includes('/_grimoire-output/')) {
    prefixes.add('assets/');
    prefixes.add('./assets/');
  }

  const marker = '/_grimoire-runtime-output/';
  const markerIdx = pathname.indexOf(marker);
  if (markerIdx >= 0) {
    const rootPrefix = pathname.substring(0, markerIdx);
    prefixes.add(`${rootPrefix}/_grimoire-output/assets/`);
  }

  const outputMarker = '/_grimoire-output/';
  const outputMarkerIdx = pathname.indexOf(outputMarker);
  if (outputMarkerIdx >= 0) {
    const rootPrefix = pathname.substring(0, outputMarkerIdx);
    prefixes.add(`${rootPrefix}/_grimoire-output/assets/`);
  }

  return [...prefixes];
}

function loadImageFromCandidates(relativePath, prefixes) {
  return new Promise((resolve) => {
    let idx = 0;
    const tryNext = () => {
      if (idx >= prefixes.length) {
        resolve(null);
        return;
      }
      const src = `${prefixes[idx]}${relativePath}`;
      idx += 1;
      const img = new Image();
      img.decoding = 'async';
      img.onload = () => resolve(img);
      img.onerror = tryNext;
      img.src = src;
    };
    tryNext();
  });
}

async function loadExternalOfficeAssets() {
  if (officeExternalAssets.loadStarted) return;
  officeExternalAssets.loadStarted = true;

  const prefixes = buildAssetPrefixCandidates();
  const slots = [
    ['floor', OFFICE_EXTERNAL_ASSET_FILES.floor],
    ['wall', OFFICE_EXTERNAL_ASSET_FILES.wall],
    ['desk', OFFICE_EXTERNAL_ASSET_FILES.desk],
    ['plant', OFFICE_EXTERNAL_ASSET_FILES.plant],
    ['whiteboard', OFFICE_EXTERNAL_ASSET_FILES.whiteboard],
    ['coffee', OFFICE_EXTERNAL_ASSET_FILES.coffee],
  ];

  for (const [slotName, candidates] of slots) {
    officeExternalAssets.totalCount += 1;
    let found = null;
    for (const relativePath of candidates) {
      found = await loadImageFromCandidates(relativePath, prefixes);
      if (found) break;
    }
    officeExternalAssets[slotName] = found;
    if (found) officeExternalAssets.loadedCount += 1;
  }

  for (const relativePath of OFFICE_EXTERNAL_ASSET_FILES.characters) {
    officeExternalAssets.totalCount += 1;
    const image = await loadImageFromCandidates(relativePath, prefixes);
    if (image) {
      officeExternalAssets.characters.push(image);
      officeExternalAssets.loadedCount += 1;
    }
  }

  officeExternalAssets.ready = officeExternalAssets.loadedCount > 0;
}

function agentSpriteFromExternalAssets(agentId) {
  if (!officeExternalAssets.characters.length) return null;
  const id = (agentId || '').split('/')[0];
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = ((hash * 31) + id.charCodeAt(i)) >>> 0;
  }
  return officeExternalAssets.characters[hash % officeExternalAssets.characters.length];
}

function makeCharSprite(theme, state, frame, dir, scale) {
  const S = scale || 2;
  const key = `char_${theme.pri}_${state}_${frame}_${dir}_${S}`;
  return getSprite(key, 16*S, 16*S, (ctx) => {
    const p = (x,y,c) => drawPixel(ctx,x,y,c,S);
    const {pri,sec,skin,hair} = theme;
    // Head
    p(6,0,hair);p(7,0,hair);p(8,0,hair);p(9,0,hair);
    p(5,1,hair);p(6,1,skin);p(7,1,skin);p(8,1,skin);p(9,1,skin);p(10,1,hair);
    p(5,2,skin);p(6,2,skin);p(7,2,'#1a1a1a');p(8,2,skin);p(9,2,'#1a1a1a');p(10,2,skin);
    p(6,3,skin);p(7,3,skin);p(8,3,'#c9736b');p(9,3,skin);
    // Body
    const bodyY = 4;
    for(let bx=5;bx<=10;bx++) p(bx,bodyY,pri);
    for(let bx=5;bx<=10;bx++) p(bx,bodyY+1,pri);
    for(let bx=5;bx<=10;bx++) p(bx,bodyY+2,sec);
    for(let bx=6;bx<=9;bx++) p(bx,bodyY+3,sec);
    // Arms + state animation
    const af = frame % 3;
    if(state==='typing') {
      p(4,bodyY+1+af%2,skin);p(11,bodyY+1+(af+1)%2,skin);
      p(3,bodyY+2+af%2,skin);p(12,bodyY+2+(af+1)%2,skin);
    } else if(state==='speaking') {
      p(4,bodyY,skin);p(11,bodyY,skin);
      p(3,bodyY+((frame%2)?-1:0),skin);p(12,bodyY+((frame%2)?-1:0),skin);
    } else if(state==='reading') {
      p(4,bodyY+1,skin);p(11,bodyY+1,skin);
      p(4,bodyY+2,pri);p(11,bodyY+2,pri);
    } else {
      p(4,bodyY+1,skin);p(11,bodyY+1,skin);
    }
    // Legs
    const legY = bodyY+4;
    if(state==='walking') {
      const lf = frame%4;
      if(lf<2){p(6,legY,'#4a4a4a');p(6,legY+1,'#4a4a4a');p(6,legY+2,'#333');p(9,legY,'#4a4a4a');p(9,legY+1+lf,'#4a4a4a');}
      else{p(6,legY,'#4a4a4a');p(6,legY+1+(lf-2),'#4a4a4a');p(9,legY,'#4a4a4a');p(9,legY+1,'#4a4a4a');p(9,legY+2,'#333');}
    } else {
      p(6,legY,'#4a4a4a');p(6,legY+1,'#4a4a4a');p(6,legY+2,'#333');
      p(9,legY,'#4a4a4a');p(9,legY+1,'#4a4a4a');p(9,legY+2,'#333');
    }
    // Hat
    if(theme.hat==='wizard'){p(6,-1+1,'#58a6ff');p(7,-1+1,'#58a6ff');p(8,-1+1,'#58a6ff');p(9,-1+1,'#58a6ff');p(7,0,'#388bfd');p(8,0,'#d29922');}
    if(theme.hat==='glasses'){p(6,2,'#ffd700');p(7,2,'#ffd700');p(8,2,skin);p(9,2,'#ffd700');p(10,2,'#ffd700');}
    // Idle bob
    if(state==='idle'&&frame%2===1){/* slight shift handled by caller */}
  });
}

function makeFloorTile(variant) {
  return getSprite('floor_'+variant, TILE, TILE, (ctx) => {
    ctx.fillStyle = variant==='carpet'?'#1e2a3a':'#2a1f14';
    ctx.fillRect(0,0,TILE,TILE);
    ctx.fillStyle = variant==='carpet'?'#1a2636':'#251b10';
    for(let i=0;i<3;i++){const x=Math.floor(Math.sin(i*47)*6)+7;const y=Math.floor(Math.cos(i*31)*6)+7;ctx.fillRect(x,y,2,2);}
    ctx.strokeStyle = variant==='carpet'?'#16202e':'#1e170c';
    ctx.strokeRect(0,0,TILE,TILE);
  });
}

function makeDeskSprite(agentColor) {
  return getSprite('desk_'+agentColor, 32, 20, (ctx) => {
    // Desk surface
    ctx.fillStyle='#5c4033';ctx.fillRect(0,4,32,12);
    ctx.fillStyle='#6b4c3b';ctx.fillRect(1,5,30,10);
    // Monitor
    ctx.fillStyle='#2a2a3e';ctx.fillRect(10,0,12,8);
    ctx.fillStyle=agentColor;ctx.globalAlpha=0.3;ctx.fillRect(11,1,10,6);ctx.globalAlpha=1;
    // Stand
    ctx.fillStyle='#4a4a4a';ctx.fillRect(14,8,4,2);
    // Color accent strip
    ctx.fillStyle=agentColor;ctx.fillRect(0,15,32,1);
  });
}

function makeWallTile(orientation) {
  return getSprite('wall_'+orientation, TILE, TILE, (ctx) => {
    ctx.fillStyle='#2d333b';ctx.fillRect(0,0,TILE,TILE);
    ctx.fillStyle='#373e47';
    if(orientation==='h'){ctx.fillRect(0,6,TILE,4);}
    else if(orientation==='v'){ctx.fillRect(6,0,4,TILE);}
    ctx.fillStyle='#444c56';
    if(orientation==='h'){ctx.fillRect(0,7,TILE,2);}
    else if(orientation==='v'){ctx.fillRect(7,0,2,TILE);}
  });
}

function makePlant() {
  return getSprite('plant', 16, 20, (ctx) => {
    // Pot
    ctx.fillStyle='#8b4513';ctx.fillRect(4,14,8,6);ctx.fillRect(3,14,10,2);
    // Leaves
    ctx.fillStyle='#2d6a2d';
    ctx.fillRect(6,4,4,10);ctx.fillRect(3,6,10,6);
    ctx.fillStyle='#3a8a3a';
    ctx.fillRect(5,5,6,8);ctx.fillRect(4,7,8,4);
    ctx.fillStyle='#4caf50';
    ctx.fillRect(6,6,4,6);
  });
}

function makeWhiteboard() {
  return getSprite('whiteboard', 48, 32, (ctx) => {
    // Frame
    ctx.fillStyle='#6e5c4f';ctx.fillRect(0,0,48,32);
    // Surface
    ctx.fillStyle='#e8e0d4';ctx.fillRect(2,2,44,24);
    // Content lines
    ctx.fillStyle='#555';
    for(let i=0;i<4;i++) ctx.fillRect(6,6+i*5,36,1);
    // Color dots
    ctx.fillStyle='#3fb950';ctx.fillRect(6,24,4,3);
    ctx.fillStyle='#f85149';ctx.fillRect(12,24,4,3);
    ctx.fillStyle='#58a6ff';ctx.fillRect(18,24,4,3);
    // Legs
    ctx.fillStyle='#4a4a4a';ctx.fillRect(4,26,2,6);ctx.fillRect(42,26,2,6);
  });
}

function makeCoffeeMachine() {
  return getSprite('coffee', 16, 20, (ctx) => {
    ctx.fillStyle='#4a4a4a';ctx.fillRect(2,4,12,14);
    ctx.fillStyle='#666';ctx.fillRect(3,5,10,12);
    ctx.fillStyle='#f85149';ctx.fillRect(5,7,2,2); // button
    ctx.fillStyle='#3fb950';ctx.fillRect(9,7,2,2);
    ctx.fillStyle='#8b4513';ctx.fillRect(5,14,6,3); // cup
  });
}

// ── Office Layout ───────────────────────────────────────────
const GRID_W = 42, GRID_H = 30;
const ZONES = {
  vision:    {x:1,y:1,w:12,h:10,floor:'carpet',label:'Team Vision'},
  architect: {x:15,y:1,w:12,h:10,floor:'carpet',label:'Architecture'},
  central:   {x:14,y:12,w:14,h:7,floor:'carpet',label:'SOG Central'},
  dev:       {x:1,y:14,w:12,h:14,floor:'wood',label:'Team Dev'},
  ops:       {x:29,y:1,w:12,h:10,floor:'carpet',label:'Ops'},
  commons:   {x:29,y:14,w:12,h:14,floor:'wood',label:'Commons'}
};

function buildOfficeLayout(agentIds) {
  const grid = Array.from({length:GRID_H}, () => Array(GRID_W).fill(0)); // 0=floor,1=wall,2=furniture
  const furniture = []; // {x,y,type,sprite,agentId}
  const agentDesks = {}; // agentId -> {x,y,zone}

  // Place walls
  for(let x=0;x<GRID_W;x++){grid[0][x]=1;grid[GRID_H-1][x]=1;}
  for(let y=0;y<GRID_H;y++){grid[y][0]=1;grid[y][GRID_W-1]=1;}

  // Zone dividers (partial walls)
  for(let x=13;x<=14;x++) for(let y=0;y<11;y++) grid[y][x]=1;
  for(let x=28;x<=29;x++) for(let y=0;y<11;y++) grid[y][x]=1;
  for(let y=11;y<=12;y++) for(let x=0;x<14;x++) grid[y][x]=1;
  // Doors (gaps in walls)
  grid[5][13]=0;grid[5][14]=0; grid[5][28]=0;grid[5][29]=0;
  grid[11][6]=0;grid[11][7]=0; grid[12][6]=0;grid[12][7]=0;

  // Assign agents to zones
  const zoneAssign = {
    analyst:'vision', pm:'vision', ux:'vision',
    architect:'architect', techwr:'architect',
    orchestr:'central', sog:'central', hpe:'central', master:'central',
    dev:'dev', qa:'dev', tea:'dev',
    sm:'ops'
  };

  const zoneAgents = {};
  agentIds.forEach(id => {
    const k = agentKey(id);
    const zone = zoneAssign[k] || 'commons';
    if(!zoneAgents[zone]) zoneAgents[zone]=[];
    zoneAgents[zone].push(id);
  });

  // Place desks in each zone
  Object.entries(zoneAgents).forEach(([zoneName, agents]) => {
    const z = ZONES[zoneName] || ZONES.commons;
    agents.forEach((aid, i) => {
      const col = i % 3, row = Math.floor(i/3);
      const dx = z.x + 2 + col*4;
      const dy = z.y + 2 + row*4;
      if(dx < z.x+z.w-2 && dy < z.y+z.h-2) {
        grid[dy][dx]=2; grid[dy][dx+1]=2;
        furniture.push({x:dx,y:dy,type:'desk',agentId:aid});
        agentDesks[aid] = {x:dx+1, y:dy+1, zone:zoneName};
      }
    });
  });

  // Decorations
  const decoSpots = [{x:13,y:15,type:'plant'},{x:28,y:15,type:'plant'},{x:20,y:3,type:'whiteboard'},{x:35,y:18,type:'coffee'},{x:2,y:12,type:'plant'},{x:39,y:12,type:'plant'}];
  decoSpots.forEach(d => {
    if(d.x<GRID_W && d.y<GRID_H) {
      furniture.push(d);
      grid[d.y][d.x]=2;
    }
  });

  return {grid, furniture, agentDesks};
}

// ── A* Pathfinder ───────────────────────────────────────────
function findPath(grid, sx, sy, ex, ey) {
  if(sx===ex && sy===ey) return [{x:sx,y:sy}];
  const W=grid[0].length, H=grid.length;
  const key = (x,y)=>y*W+x;
  const open = [{x:sx,y:sy,g:0,f:0}];
  const closed = new Set();
  const parent = new Map();
  const gScore = new Map(); gScore.set(key(sx,sy),0);

  while(open.length) {
    open.sort((a,b)=>a.f-b.f);
    const cur = open.shift();
    const ck = key(cur.x,cur.y);
    if(cur.x===ex && cur.y===ey) {
      const path = [];
      let k=ck;
      while(k!==undefined){const y=Math.floor(k/W),x=k%W;path.unshift({x,y});k=parent.get(k);}
      return path;
    }
    closed.add(ck);
    for(const [dx,dy] of [[0,-1],[0,1],[-1,0],[1,0],[1,1],[1,-1],[-1,1],[-1,-1]]) {
      const nx=cur.x+dx,ny=cur.y+dy;
      if(nx<0||nx>=W||ny<0||ny>=H) continue;
      if(grid[ny][nx]===1) continue; // wall
      const nk=key(nx,ny);
      if(closed.has(nk)) continue;
      const ng = cur.g + (dx!==0&&dy!==0?1.41:1);
      if(!gScore.has(nk)||ng<gScore.get(nk)) {
        gScore.set(nk,ng);
        parent.set(nk,ck);
        const h=Math.abs(nx-ex)+Math.abs(ny-ey);
        open.push({x:nx,y:ny,g:ng,f:ng+h});
      }
    }
  }
  return [{x:sx,y:sy},{x:ex,y:ey}]; // fallback straight line
}

// ── Agent Character ─────────────────────────────────────────
class Agent {
  constructor(id, deskX, deskY, zone) {
    this.id = id;
    this.key = agentKey(id);
    this.theme = AGENT_THEME[this.key] || AGENT_THEME.default;
    this.deskX = deskX; this.deskY = deskY;
    this.x = deskX; this.y = deskY;
    this.targetX = deskX; this.targetY = deskY;
    this.zone = zone;
    this.state = 'idle'; // idle, walking, typing, reading, speaking, waiting, error, celebrating
    this.frame = 0;
    this.frameTimer = 0;
    this.path = [];
    this.pathIdx = 0;
    this.selected = false;
    this.bubble = null; // {text, timer}
    this.trust = 85 + Math.floor(Math.random()*15);
    this.lastAction = '';
    this.lastActionTime = '';
    this.capabilities = [];
    this.tools = [];
    this.dir = 0; // 0=down
    this.particles = [];
  }
  setState(st, payload) {
    if(this.state===st) return;
    this.state = st;
    this.frame = 0;
    this.frameTimer = 0;
    if(payload) { this.lastAction=payload; this.lastActionTime=new Date().toISOString(); }
  }
  walkTo(tx, ty, grid) {
    if(Math.abs(this.x-tx)<0.5 && Math.abs(this.y-ty)<0.5) return;
    this.path = findPath(grid, Math.round(this.x), Math.round(this.y), Math.round(tx), Math.round(ty));
    this.pathIdx = 0;
    this.targetX = tx; this.targetY = ty;
    this.setState('walking');
  }
  showBubble(text, duration) { this.bubble = {text, timer:duration||120}; }
  update(dt) {
    this.frameTimer += dt;
    if(this.frameTimer > 150) { this.frame++; this.frameTimer=0; }
    // Walk along path
    if(this.state==='walking' && this.path.length>1) {
      const target = this.path[Math.min(this.pathIdx+1, this.path.length-1)];
      const dx = target.x - this.x, dy = target.y - this.y;
      const dist = Math.sqrt(dx*dx+dy*dy);
      if(dist < 0.15) {
        this.pathIdx++;
        if(this.pathIdx >= this.path.length-1) { this.x=this.targetX;this.y=this.targetY; this.setState('idle'); }
      } else {
        const speed = 0.06;
        this.x += (dx/dist)*speed*dt*0.06;
        this.y += (dy/dist)*speed*dt*0.06;
      }
    }
    // Bubble decay
    if(this.bubble) { this.bubble.timer--; if(this.bubble.timer<=0) this.bubble=null; }
    // Particles decay
    this.particles = this.particles.filter(p => { p.life-=dt; p.x+=p.vx; p.y+=p.vy; p.vy+=0.01; return p.life>0; });
  }
  addParticles(type) {
    const colors = type==='success'?['#3fb950','#56d364','#aff5b4']:type==='error'?['#f85149','#ff7b72','#da3633']:['#58a6ff','#79c0ff','#d2a8ff'];
    for(let i=0;i<8;i++) {
      this.particles.push({x:this.x*TILE*2+16,y:this.y*TILE*2-4,vx:(Math.random()-.5)*2,vy:-Math.random()*2-1,life:500+Math.random()*500,color:colors[i%colors.length],size:2+Math.random()*2});
    }
  }
}

// ── Timeline Engine ─────────────────────────────────────────
class TimelineEngine {
  constructor(items) {
    this.items = items; // sorted by ts
    this.currentIdx = 0;
    this.mode = 'paused'; // paused, playing, live
    this.speed = 1;
    this.listeners = {};
    this.playTimer = null;
    this.startTs = items.length ? new Date(items[0].ts).getTime() : 0;
    this.endTs = items.length ? new Date(items[items.length-1].ts).getTime() : 0;
    this.currentTs = this.startTs;
  }
  on(evt, fn) { if(!this.listeners[evt]) this.listeners[evt]=[]; this.listeners[evt].push(fn); }
  emit(evt, data) { (this.listeners[evt]||[]).forEach(fn=>fn(data)); }
  get progress() { const range=this.endTs-this.startTs; return range?((this.currentTs-this.startTs)/range):0; }
  get currentItem() { return this.items[this.currentIdx]; }
  seekTo(pct) {
    pct = Math.max(0,Math.min(1,pct));
    this.currentTs = this.startTs + (this.endTs-this.startTs)*pct;
    // Find closest event
    let best=0, bestDist=Infinity;
    this.items.forEach((it,i)=>{const d=Math.abs(new Date(it.ts).getTime()-this.currentTs);if(d<bestDist){bestDist=d;best=i;}});
    this.currentIdx = best;
    this.emit('seek', {idx:this.currentIdx, ts:this.currentTs, item:this.items[this.currentIdx]});
    this.emit('timeUpdate', this.currentTs);
  }
  seekToIdx(idx) {
    idx = Math.max(0,Math.min(this.items.length-1, idx));
    this.currentIdx = idx;
    if(this.items[idx]) this.currentTs = new Date(this.items[idx].ts).getTime();
    this.emit('seek', {idx, ts:this.currentTs, item:this.items[idx]});
    this.emit('timeUpdate', this.currentTs);
  }
  play() {
    if(this.mode==='playing') return;
    this.mode = 'playing';
    this.emit('modeChange', 'playing');
    const step = () => {
      if(this.mode!=='playing') return;
      if(this.currentIdx < this.items.length-1) {
        this.currentIdx++;
        this.currentTs = new Date(this.items[this.currentIdx].ts).getTime();
        this.emit('event', this.items[this.currentIdx]);
        this.emit('timeUpdate', this.currentTs);
        // Calculate delay to next event (compressed by speed)
        const nextTs = this.currentIdx < this.items.length-1 ? new Date(this.items[this.currentIdx+1].ts).getTime() : this.currentTs;
        const delay = Math.max(80, Math.min(2000, (nextTs - this.currentTs) / this.speed));
        this.playTimer = setTimeout(step, delay);
      } else {
        this.pause();
      }
    };
    step();
  }
  pause() { this.mode='paused'; clearTimeout(this.playTimer); this.emit('modeChange','paused'); }
  togglePlay() { this.mode==='playing' ? this.pause() : this.play(); }
  setSpeed(s) { this.speed=s; }
  next() { this.pause(); if(this.currentIdx<this.items.length-1) this.seekToIdx(this.currentIdx+1); }
  prev() { this.pause(); if(this.currentIdx>0) this.seekToIdx(this.currentIdx-1); }
  goStart() { this.pause(); this.seekToIdx(0); }
  goEnd() { this.pause(); this.seekToIdx(this.items.length-1); }
  goLive() { this.mode='live'; this.seekToIdx(this.items.length-1); this.emit('modeChange','live'); }
}

// ── Office Renderer ─────────────────────────────────────────
let officeInited = false;
let officeAgents = [];
let officeLayout = null;
let timeline = null;
let camera = {x:0, y:0, zoom:2, targetZoom:2};
let selectedAgent = null;
let officeHudState = {grid:false, names:true, trust:true, bubbles:true};
let officeAnimId = null;
let lastFrameTime = 0;

function initOffice() {
  if(officeInited) { renderOfficeFrame(performance.now()); return; }
  officeInited = true;

  // Prefer governed assets when available, while keeping procedural fallback deterministic.
  loadExternalOfficeAssets();

  // Build layout from data
  officeLayout = buildOfficeLayout(DATA.agent_ids);

  // Create agent characters
  officeAgents = [];
  DATA.agent_ids.forEach(id => {
    const desk = officeLayout.agentDesks[id];
    if(desk) {
      const a = new Agent(id, desk.x, desk.y, desk.zone);
      // Load capabilities from agent graph data
      const agData = DATA.agents.find(ag=>ag.id===id.split('/')[0] || ag.id===id);
      if(agData) {
        a.capabilities = agData.capabilities || [];
        a.trust = (agData.metrics && agData.metrics.trust_score) || a.trust;
      }
      officeAgents.push(a);
    } else {
      // Fallback: place in commons
      const z = ZONES.commons;
      const i = officeAgents.length;
      const a = new Agent(id, z.x+2+(i%3)*4, z.y+2+Math.floor(i/3)*4, 'commons');
      officeAgents.push(a);
    }
  });

  // Init timeline engine
  const allItems = getAllItems('');
  timeline = new TimelineEngine(allItems);
  initTimelineBar();

  // Timeline events drive agent states
  timeline.on('event', (item) => {
    const agent = officeAgents.find(a => a.id === item.agent);
    if(!agent) return;
    const state = agentStateFromItem(item);
    const typeL = (item.type||'').toLowerCase();
    const payloadL = (item.payload||'').toLowerCase();

    agent.setState(state, item.payload);

    if(state === 'typing') {
      agent.walkTo(agent.deskX, agent.deskY, officeLayout.grid);
    }
    if(state === 'speaking') {
      agent.showBubble(item.type.substring(0,20), 200);
      // Walk toward target agent when payload references another agent id/persona
      const target = officeAgents.find(a => payloadL.includes(a.id.split('/')[0].toLowerCase()) && a.id !== agent.id);
      if(target) agent.walkTo(target.x, target.y, officeLayout.grid);
    }
    if(state === 'celebrating') {
      agent.addParticles('success');
    }
    if(state === 'error') {
      agent.addParticles('error');
      agent.showBubble('!', 150);
    }
    if(state === 'waiting') {
      agent.showBubble('?', 200);
    }
    if(typeL.includes('activated')) {
      agent.showBubble(item.type.substring(0,15), 100);
    }
  });

  // Seeked → set all agents to state at that point
  timeline.on('seek', ({idx}) => {
    // Reset all to idle then replay recent events per agent
    officeAgents.forEach(a => { a.setState('idle',''); a.x=a.deskX; a.y=a.deskY; });
    // Find last event per agent before current index
    const lastPerAgent = {};
    for(let i=0; i<=idx && i<timeline.items.length; i++) {
      const it = timeline.items[i];
      lastPerAgent[it.agent] = it;
    }
    Object.values(lastPerAgent).forEach(item => {
      const agent = officeAgents.find(a=>a.id===item.agent);
      if(agent) timeline.emit('event', item);
    });
  });

  // Setup canvas
  const wrap = $('#office-wrap');
  const cv = $('#office-cv');
  const resize = () => {
    cv.width = wrap.clientWidth; cv.height = wrap.clientHeight;
  };
  resize();
  new ResizeObserver(resize).observe(wrap);

  // Camera controls — pan
  let dragging=false, dragX=0, dragY=0;
  wrap.addEventListener('pointerdown', e => {
    if(e.target.tagName==='BUTTON'||e.target.tagName==='CANVAS'&&e.target.id==='office-minimap') return;
    dragging=true; dragX=e.clientX; dragY=e.clientY; wrap.setPointerCapture(e.pointerId);
  });
  wrap.addEventListener('pointermove', e => {
    if(!dragging) return;
    camera.x += (e.clientX-dragX); camera.y += (e.clientY-dragY);
    dragX=e.clientX; dragY=e.clientY;
  });
  wrap.addEventListener('pointerup', () => { dragging=false; });
  // Zoom
  wrap.addEventListener('wheel', e => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.15 : 0.15;
    camera.targetZoom = Math.max(0.8, Math.min(4, camera.targetZoom + delta));
  }, {passive:false});

  // Click agent detection
  cv.addEventListener('click', e => {
    const rect = cv.getBoundingClientRect();
    const mx = (e.clientX - rect.left - camera.x) / camera.zoom;
    const my = (e.clientY - rect.top - camera.y) / camera.zoom;
    let found = null;
    officeAgents.forEach(a => {
      const ax = a.x*TILE*2, ay = a.y*TILE*2-8;
      if(mx>=ax && mx<=ax+32 && my>=ay && my<=ay+40) found = a;
    });
    if(found) {
      selectedAgent = found===selectedAgent ? null : found;
      officeAgents.forEach(a => a.selected = (a===selectedAgent));
      updateOfficeInfo();
    } else {
      selectedAgent = null;
      officeAgents.forEach(a => a.selected=false);
      $('#office-info').style.display='none';
    }
  });

  // HUD buttons
  const toggleHud = (btn, key) => { officeHudState[key]=!officeHudState[key]; btn.classList.toggle('active',officeHudState[key]); };
  $('#hud-grid').addEventListener('click', () => toggleHud($('#hud-grid'),'grid'));
  $('#hud-names').addEventListener('click', () => toggleHud($('#hud-names'),'names'));
  $('#hud-trust').addEventListener('click', () => toggleHud($('#hud-trust'),'trust'));
  $('#hud-bubbles').addEventListener('click', () => toggleHud($('#hud-bubbles'),'bubbles'));
  $('#hud-reset').addEventListener('click', () => { camera.x=0;camera.y=0;camera.targetZoom=2; selectedAgent=null; officeAgents.forEach(a=>a.selected=false); $('#office-info').style.display='none'; });

  // Center camera initially
  camera.x = -GRID_W*TILE + wrap.clientWidth/2;
  camera.y = -GRID_H*TILE/2 + wrap.clientHeight/2;

  // Start game loop
  if(!officeAnimId) officeLoop(performance.now());
}

function officeLoop(now) {
  const dt = now - lastFrameTime;
  lastFrameTime = now;
  // Smooth zoom
  camera.zoom += (camera.targetZoom - camera.zoom) * 0.12;
  // Update agents
  officeAgents.forEach(a => a.update(Math.min(dt,100)));
  // Render if Office tab active
  if($('#view-office').classList.contains('active')) renderOfficeFrame(now);
  // Render minimap
  renderMinimap();
  // Update timeline bar progress
  updateTimelineBarProgress();
  officeAnimId = requestAnimationFrame(officeLoop);
}

function renderOfficeFrame(now) {
  const cv = $('#office-cv');
  if(!cv || !cv.getContext) return;
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.save();
  ctx.translate(camera.x, camera.y);
  ctx.scale(camera.zoom, camera.zoom);

  const S = 2; // Pixel scale for tiles
  const TS = TILE * S;

  // Floor tiles
  for(let y=0; y<GRID_H; y++) {
    for(let x=0; x<GRID_W; x++) {
      const cell = officeLayout.grid[y][x];
      if(cell===1) {
        const wallAsset = officeExternalAssets.wall;
        if (wallAsset) {
          ctx.drawImage(wallAsset, x*TS, y*TS, TS, TS);
        } else {
          ctx.drawImage(makeWallTile(y===0||y===GRID_H-1?'h':'v'), x*TS, y*TS, TS, TS);
        }
      } else {
        // Determine zone for floor variant
        let variant = 'wood';
        Object.values(ZONES).forEach(z => {
          if(x>=z.x && x<z.x+z.w && y>=z.y && y<z.y+z.h) variant=z.floor;
        });
        const floorAsset = officeExternalAssets.floor;
        if (floorAsset) {
          ctx.drawImage(floorAsset, x*TS, y*TS, TS, TS);
        } else {
          ctx.drawImage(makeFloorTile(variant), x*TS, y*TS, TS, TS);
        }
      }
    }
  }

  // Grid overlay
  if(officeHudState.grid) {
    ctx.strokeStyle = 'rgba(88,166,255,0.08)';
    ctx.lineWidth = 0.5;
    for(let y=0; y<=GRID_H; y++) { ctx.beginPath(); ctx.moveTo(0,y*TS); ctx.lineTo(GRID_W*TS,y*TS); ctx.stroke(); }
    for(let x=0; x<=GRID_W; x++) { ctx.beginPath(); ctx.moveTo(x*TS,0); ctx.lineTo(x*TS,GRID_H*TS); ctx.stroke(); }
  }

  // Zone labels
  ctx.font = '10px system-ui';
  ctx.globalAlpha = 0.3;
  Object.values(ZONES).forEach(z => {
    ctx.fillStyle = '#58a6ff';
    ctx.fillText(z.label, (z.x+1)*TS, (z.y+z.h-0.5)*TS);
  });
  ctx.globalAlpha = 1;

  // Furniture
  officeLayout.furniture.forEach(f => {
    if(f.type==='desk') {
      const aColor = f.agentId ? agentColor(f.agentId) : '#6e7681';
      if (officeExternalAssets.desk) {
        ctx.drawImage(officeExternalAssets.desk, f.x*TS-4, f.y*TS-4, 64, 40);
      } else {
        ctx.drawImage(makeDeskSprite(aColor), f.x*TS-4, f.y*TS-4, 64, 40);
      }
    }
    if(f.type==='plant') {
      if (officeExternalAssets.plant) {
        ctx.drawImage(officeExternalAssets.plant, f.x*TS, f.y*TS-8, 32, 40);
      } else {
        ctx.drawImage(makePlant(), f.x*TS, f.y*TS-8, 32, 40);
      }
    }
    if(f.type==='whiteboard') {
      if (officeExternalAssets.whiteboard) {
        ctx.drawImage(officeExternalAssets.whiteboard, f.x*TS, f.y*TS, 96, 64);
      } else {
        ctx.drawImage(makeWhiteboard(), f.x*TS, f.y*TS, 96, 64);
      }
    }
    if(f.type==='coffee') {
      if (officeExternalAssets.coffee) {
        ctx.drawImage(officeExternalAssets.coffee, f.x*TS, f.y*TS-8, 32, 40);
      } else {
        ctx.drawImage(makeCoffeeMachine(), f.x*TS, f.y*TS-8, 32, 40);
      }
    }
  });

  // Agents (sorted by Y for depth)
  const sorted = [...officeAgents].sort((a,b)=>a.y-b.y);
  sorted.forEach(a => {
    const px = a.x * TS;
    const py = a.y * TS - 8 + (a.state==='idle' && a.frame%2 ? -1 : 0);
    const sprite = makeCharSprite(a.theme, a.state, a.frame, a.dir, S);
    const externalSprite = agentSpriteFromExternalAssets(a.id);
    if (externalSprite) {
      const frameSize = 16;
      const sw = Math.min(frameSize, externalSprite.naturalWidth || frameSize);
      const sh = Math.min(frameSize, externalSprite.naturalHeight || frameSize);
      ctx.drawImage(externalSprite, 0, 0, sw, sh, px, py, 32, 32);
    } else {
      ctx.drawImage(sprite, px, py);
    }

    // Selection ring
    if(a.selected) {
      ctx.strokeStyle = '#58a6ff';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.ellipse(px+16, py+34, 18, 6, 0, 0, Math.PI*2);
      ctx.stroke();
    }

    // Trust bar
    if(officeHudState.trust) {
      const tw = 24, th = 3;
      const tx = px+4, ty = py-6;
      ctx.fillStyle = 'rgba(0,0,0,0.5)';
      ctx.fillRect(tx, ty, tw, th);
      const trustPct = a.trust/100;
      ctx.fillStyle = trustPct > 0.7 ? '#3fb950' : trustPct > 0.4 ? '#d29922' : '#f85149';
      ctx.fillRect(tx, ty, tw*trustPct, th);
    }

    // Name label
    if(officeHudState.names) {
      ctx.font = '8px system-ui';
      ctx.fillStyle = agentColor(a.id);
      ctx.textAlign = 'center';
      ctx.fillText(a.id.split('/')[0], px+16, py-8);
      ctx.textAlign = 'left';
    }

    // State indicator
    const stateEmoji = {idle:'',typing:'⌨',reading:'📖',speaking:'💬',waiting:'⏳',error:'❌',celebrating:'🎉',walking:'🚶'};
    if(stateEmoji[a.state]) {
      ctx.font = '10px system-ui';
      ctx.fillText(stateEmoji[a.state], px+28, py+8);
    }

    // Speech bubble
    if(officeHudState.bubbles && a.bubble) {
      const bx = px+16, by = py-20;
      const text = a.bubble.text;
      ctx.font = '7px system-ui';
      const tm = ctx.measureText(text);
      const bw = Math.min(tm.width+8, 80);
      ctx.fillStyle = 'rgba(22,27,34,0.92)';
      ctx.beginPath();
      ctx.roundRect(bx-bw/2, by-12, bw, 14, 4);
      ctx.fill();
      ctx.strokeStyle = agentColor(a.id);
      ctx.lineWidth = 0.5;
      ctx.stroke();
      ctx.fillStyle = '#c9d1d9';
      ctx.textAlign = 'center';
      ctx.fillText(text.substring(0,16), bx, by-2);
      ctx.textAlign = 'left';
    }

    // Particles
    a.particles.forEach(p => {
      ctx.globalAlpha = Math.max(0, p.life/500);
      ctx.fillStyle = p.color;
      ctx.fillRect(p.x, p.y, p.size, p.size);
    });
    ctx.globalAlpha = 1;
  });

  ctx.restore();

  if (officeExternalAssets.loadStarted) {
    ctx.fillStyle = 'rgba(13,17,23,0.7)';
    ctx.fillRect(10, 10, 150, 18);
    ctx.fillStyle = '#c9d1d9';
    ctx.font = '11px monospace';
    const mode = officeExternalAssets.ready ? 'assets' : 'assets-loading';
    ctx.fillText(`${mode}: ${officeExternalAssets.loadedCount}/${officeExternalAssets.totalCount}`, 16, 23);
  }
}

function renderMinimap() {
  const mc = $('#office-minimap');
  if(!mc) return;
  const mctx = mc.getContext('2d');
  mctx.imageSmoothingEnabled = false;
  const S = 160/GRID_W;
  mctx.fillStyle = '#0d1117';
  mctx.fillRect(0,0,160,120);
  // Zones
  Object.values(ZONES).forEach(z => {
    mctx.fillStyle = z.floor==='carpet'?'#1e2a3a':'#2a1f14';
    mctx.fillRect(z.x*S, z.y*(120/GRID_H), z.w*S, z.h*(120/GRID_H));
  });
  // Agents
  officeAgents.forEach(a => {
    mctx.fillStyle = agentColor(a.id);
    mctx.fillRect(a.x*S-1, a.y*(120/GRID_H)-1, 3, 3);
  });
  // Camera viewport
  const cv = $('#office-cv');
  if(cv) {
    const vx = -camera.x/camera.zoom * S / (TILE*2);
    const vy = -camera.y/camera.zoom * (120/GRID_H) / (TILE*2);
    const vw = cv.width/camera.zoom * S / (TILE*2);
    const vh = cv.height/camera.zoom * (120/GRID_H) / (TILE*2);
    mctx.strokeStyle = '#58a6ff';
    mctx.lineWidth = 1;
    mctx.strokeRect(vx, vy, vw, vh);
  }
}

function updateOfficeInfo() {
  const panel = $('#office-info');
  if(!selectedAgent) { panel.style.display='none'; return; }
  const a = selectedAgent;
  const baseId = canonicalAgentId(a.id);
  const profile = DATA.agents.find(ag => canonicalAgentId(ag.id) === baseId || ag.id === baseId) || {};
  const displayName = profile.name || a.id;
  const stateLabel = {idle:'💤 En attente',typing:'⌨️ Typing',reading:'📖 Reading',speaking:'💬 Speaking',waiting:'⏳ Waiting',error:'❌ Erreur',celebrating:'🎉 Done!',walking:'🚶 En déplacement'};
  const trustColor = a.trust>70?'#3fb950':a.trust>40?'#d29922':'#f85149';
  const caps = a.capabilities.length ? a.capabilities : ['agent','trace'];
  const toolsHtml = caps.map(c => `<span class="oi-tool active">${esc(c)}</span>`).join('');
  panel.style.display='block';
  panel.innerHTML = `
    <div class="oi-name" style="color:${agentColor(a.id)}">${esc(displayName)}</div>
    <div class="oi-state">${stateLabel[a.state]||a.state}</div>
    <div style="font-size:.68rem;color:var(--fg2)">Trust: ${a.trust}%</div>
    <div class="oi-trust"><div class="oi-trust-fill" style="width:${a.trust}%;background:${trustColor}"></div></div>
    <div style="font-size:.68rem;color:var(--fg2);margin-top:4px">Zone: ${a.zone}</div>
    ${a.lastAction?`<div style="font-size:.68rem;color:var(--fg2);margin-top:4px;word-break:break-word">Last: ${esc(a.lastAction.substring(0,80))}</div>`:''}
    <div class="oi-tools" style="margin-top:8px">${toolsHtml}</div>
    <div class="oi-actions"><button class="oi-btn" id="oi-config-btn">⚙ Config</button></div>
  `;

  const configBtn = $('#oi-config-btn');
  if (configBtn) {
    configBtn.addEventListener('click', () => openAgentConfigDrawer(a.id));
  }
}

// ── Timeline Bar UI ─────────────────────────────────────────
function initTimelineBar() {
  if(!timeline) return;

  // Populate session selector
  const sel = $('#tbar-session');
  DATA.sessions.forEach(s => { const o=document.createElement('option');o.value=s;o.textContent=s;sel.appendChild(o); });

  // Time display
  const fmt = ts => { if(!ts) return '--:--:--'; const d=new Date(ts); return d.toISOString().substring(11,19); };
  $('#tbar-total').textContent = fmt(timeline.endTs);
  $('#tbar-current').textContent = fmt(timeline.startTs);

  // Draw heatmap on track
  const heatCv = $('#tbar-heat-cv');
  const scrub = $('#tbar-scrub');
  heatCv.width = scrub.clientWidth;
  heatCv.height = 4;
  const hctx = heatCv.getContext('2d');
  if(timeline.items.length > 1) {
    const bins = Math.min(200, scrub.clientWidth);
    const counts = new Array(bins).fill(0);
    timeline.items.forEach(it => {
      const pct = (new Date(it.ts).getTime() - timeline.startTs) / (timeline.endTs - timeline.startTs);
      const bin = Math.floor(pct * (bins-1));
      counts[Math.max(0,Math.min(bins-1,bin))]++;
    });
    const max = Math.max(...counts, 1);
    for(let i=0; i<bins; i++) {
      const intensity = counts[i]/max;
      hctx.fillStyle = `rgba(88,166,255,${intensity*0.8})`;
      hctx.fillRect(i*(scrub.clientWidth/bins), 0, scrub.clientWidth/bins+1, 4);
    }
  }

  // Event markers on track
  const markers = $('#tbar-markers');
  timeline.items.forEach(it => {
    if(!it.ts) return;
    const pct = timeline.items.length>1 ? ((new Date(it.ts).getTime()-timeline.startTs)/(timeline.endTs-timeline.startTs))*100 : 50;
    const mk = document.createElement('div');
    mk.className = 'tbar-marker';
    mk.style.left = pct+'%';
    mk.style.background = agentColor(it.agent);
    markers.appendChild(mk);
  });

  // Controls
  $('#tbar-play').addEventListener('click', () => tbarTogglePlay());
  $('#tbar-prev').addEventListener('click', () => timeline.prev());
  $('#tbar-next').addEventListener('click', () => timeline.next());
  $('#tbar-start').addEventListener('click', () => timeline.goStart());
  $('#tbar-end').addEventListener('click', () => timeline.goEnd());
  $('#tbar-live').addEventListener('click', () => timeline.goLive());

  // Speed cycling
  const speeds = [0.5, 1, 2, 4, 8];
  let speedIdx = 1;
  $('#tbar-speed').addEventListener('click', () => {
    speedIdx = (speedIdx+1) % speeds.length;
    timeline.setSpeed(speeds[speedIdx]);
    $('#tbar-speed').textContent = speeds[speedIdx]+'x';
  });

  // Scrub drag
  let scrubbing = false;
  const scrubSeek = (e) => {
    const rect = scrub.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    timeline.seekTo(pct);
  };
  scrub.addEventListener('pointerdown', e => { scrubbing=true; scrub.setPointerCapture(e.pointerId); scrubSeek(e); });
  scrub.addEventListener('pointermove', e => { if(scrubbing) scrubSeek(e); });
  scrub.addEventListener('pointerup', () => { scrubbing=false; });

  // Timeline events update display
  timeline.on('timeUpdate', ts => {
    $('#tbar-current').textContent = fmt(ts);
  });
  timeline.on('modeChange', mode => {
    $('#tbar-play').innerHTML = mode==='playing' ? '&#9208;' : '&#9654;';
    $('#tbar-play').classList.toggle('active', mode==='playing');
    $('#tbar-live').style.display = mode==='live'?'inline-block':'none';
  });
}

function tbarTogglePlay() { if(timeline) timeline.togglePlay(); }

function updateTimelineBarProgress() {
  if(!timeline || !timeline.items.length) return;
  const pct = timeline.progress * 100;
  const prog = $('#tbar-progress');
  const thumb = $('#tbar-thumb');
  if(prog) prog.style.width = pct+'%';
  if(thumb) thumb.style.left = pct+'%';
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
    host = str(getattr(args, "host", "127.0.0.1")).strip() or "127.0.0.1"
    commit_required = bool(getattr(args, "commit_required", False))
    read_only = bool(getattr(args, "read_only", False))

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OBSERVATORY_HTML
    regen_lock = threading.Lock()
    config_mutation_lock = threading.Lock()
    audit_lock = threading.Lock()

    def write_audit(action: str, **fields: Any) -> None:
        payload = {"action": action, **fields}
        with audit_lock:
            _append_agent_config_audit(out_dir, payload)

    def regenerate(reason: str) -> ObservatoryData:
        with regen_lock:
            new_data = load_all(root, out_dir)
            new_html = generate_html(new_data, auto_refresh=True)
            out_path.write_text(new_html, encoding="utf-8")
        if reason in {"watch", "api-apply", "api-rollback"}:
            print(
                f"🔄 Regenerated ({len(new_data.traces)} traces, {len(new_data.events)} events) [{reason}]"
            )
        return new_data

    regenerate("initial")

    # Watch source files and regenerate on change
    trace_path = _find_trace(out_dir)
    watch_files = [
        trace_path,
        out_dir / EVENT_LOG_FILE,
        out_dir / AGENT_GRAPH_FILE,
        out_dir / SHARED_STATE_FILE,
        _agent_config_path(out_dir),
    ]
    last_mtimes = {str(f): f.stat().st_mtime if f.exists() else 0 for f in watch_files}

    def watcher() -> None:
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
                    regenerate("watch")
                except Exception as e:
                    print(f"⚠️ Regen error: {e}")

    t = threading.Thread(target=watcher, daemon=True)
    t.start()

    # Serve
    os.chdir(str(out_dir))

    class ObservatoryHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *a) -> None:
            pass  # silent

        def _client_ip(self) -> str:
            if not self.client_address:
                return ""
            return str(self.client_address[0])

        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _audit(self, action: str, **fields: Any) -> None:
            write_audit(action, client=self._client_ip(), **fields)

        def _read_json_body(self) -> dict[str, Any]:
            length = _safe_int(self.headers.get("Content-Length", "0"), 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            if not raw.strip():
                return {}
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid JSON payload") from exc
            if not isinstance(obj, dict):
                raise ValueError("JSON payload must be an object")
            return obj

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/agent-config":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "commit_required": commit_required,
                        "read_only": read_only,
                        "config": load_agent_config(out_dir),
                        "backups": list_agent_config_backups(out_dir),
                    },
                )
                return
            if path.startswith("/api/"):
                self._audit(
                    "request",
                    method="GET",
                    endpoint=path,
                    status="not-found",
                    error="Unknown endpoint",
                )
                self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {path}"})
                return
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                return super().do_POST()

            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._audit(
                    "request",
                    method="POST",
                    endpoint=path,
                    status="error",
                    error=str(exc),
                )
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            if path == "/api/agent-config/diff":
                agent_id = _agent_base_id(str(payload.get("agent_id", "")))
                candidate_payload = payload.get("candidate", payload.get("patch", {}))
                if not agent_id:
                    self._audit(
                        "diff",
                        method="POST",
                        endpoint=path,
                        status="error",
                        error="agent_id is required",
                    )
                    self._send_json(400, {"ok": False, "error": "agent_id is required"})
                    return

                current_data = load_all(root, out_dir)
                cfg = load_agent_config(out_dir)
                baseline = _agent_defaults_for(agent_id, current_data)
                current_entry = _normalize_agent_entry(
                    agent_id,
                    cfg.get("agents", {}).get(agent_id, {}),
                    fallback=baseline,
                )
                candidate_entry = _normalize_agent_entry(
                    agent_id,
                    candidate_payload,
                    fallback=current_entry,
                )
                diff = compute_agent_config_diff(current_entry, candidate_entry)
                current_version = _safe_int(current_entry.get("version", 0), 0)
                next_version = current_version + (1 if diff else 0)
                self._audit(
                    "diff",
                    method="POST",
                    endpoint=path,
                    status="ok",
                    agent_id=agent_id,
                    before_version=current_version,
                    after_version=next_version,
                    diff_fields=[str(item.get("field", "")) for item in diff],
                )
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "agent_id": agent_id,
                        "current": current_entry,
                        "candidate": candidate_entry,
                        "diff": diff,
                        "current_version": current_version,
                        "next_version": next_version,
                    },
                )
                return

            if path == "/api/agent-config/apply":
                agent_id = _agent_base_id(str(payload.get("agent_id", "")))
                candidate_payload = payload.get("candidate", payload.get("patch", {}))
                commit_message = str(payload.get("commit_message") or payload.get("message") or "")

                if read_only:
                    self._audit(
                        "apply",
                        method="POST",
                        endpoint=path,
                        status="forbidden",
                        agent_id=agent_id,
                        commit_message=commit_message,
                        error="read_only mode",
                    )
                    self._send_json(403, {"ok": False, "error": "Agent config is read-only"})
                    return

                if not agent_id:
                    self._audit(
                        "apply",
                        method="POST",
                        endpoint=path,
                        status="error",
                        error="agent_id is required",
                        commit_message=commit_message,
                    )
                    self._send_json(400, {"ok": False, "error": "agent_id is required"})
                    return

                with config_mutation_lock:
                    before_cfg = load_agent_config(out_dir)
                    before_version = _safe_int(before_cfg.get("version", 0), 0)
                    before_agent = before_cfg.get("agents", {}).get(agent_id, {})
                    if not isinstance(before_agent, dict):
                        before_agent = {}
                    before_agent_version = _safe_int(before_agent.get("version", 0), 0)

                    current_data = load_all(root, out_dir)
                    try:
                        result = apply_agent_config_update(
                            out_dir,
                            agent_id,
                            candidate_payload,
                            data=current_data,
                            updated_by="observatory-ui",
                            commit_required=commit_required,
                            commit_message=commit_message,
                        )
                    except ValueError as exc:
                        self._audit(
                            "apply",
                            method="POST",
                            endpoint=path,
                            status="error",
                            agent_id=agent_id,
                            before_version=before_version,
                            before_agent_version=before_agent_version,
                            commit_message=commit_message,
                            error=str(exc),
                        )
                        self._send_json(400, {"ok": False, "error": str(exc)})
                        return

                    changed = bool(result.get("changed"))
                    after_cfg = result.get("config", {})
                    if not isinstance(after_cfg, dict):
                        after_cfg = {}
                    after_version = _safe_int(after_cfg.get("version", before_version), before_version)
                    result_agent = result.get("agent", {})
                    if not isinstance(result_agent, dict):
                        result_agent = {}
                    after_agent_version = _safe_int(
                        result_agent.get("version", before_agent_version),
                        before_agent_version,
                    )

                if changed:
                    regenerate("api-apply")

                self._audit(
                    "apply",
                    method="POST",
                    endpoint=path,
                    status="ok" if changed else "unchanged",
                    agent_id=agent_id,
                    before_version=before_version,
                    after_version=after_version,
                    before_agent_version=before_agent_version,
                    after_agent_version=after_agent_version,
                    commit_message=commit_message,
                    backup=result.get("backup"),
                    diff_fields=[str(item.get("field", "")) for item in result.get("diff", [])],
                )

                self._send_json(
                    200,
                    {
                        "ok": True,
                      "changed": changed,
                        "agent_id": result.get("agent_id"),
                        "diff": result.get("diff", []),
                        "agent": result.get("agent", {}),
                        "backup": result.get("backup"),
                        "config": result.get("config", {}),
                        "backups": list_agent_config_backups(out_dir),
                        "commit_required": commit_required,
                      "read_only": read_only,
                    },
                )
                return

            if path == "/api/agent-config/rollback":
                requested_backup = str(payload.get("backup", "")).strip()

                if read_only:
                    self._audit(
                        "rollback",
                        method="POST",
                        endpoint=path,
                        status="forbidden",
                        requested_backup=requested_backup,
                        error="read_only mode",
                    )
                    self._send_json(403, {"ok": False, "error": "Agent config is read-only"})
                    return

                with config_mutation_lock:
                    before_cfg = load_agent_config(out_dir)
                    before_version = _safe_int(before_cfg.get("version", 0), 0)
                    try:
                        result = rollback_agent_config(
                            out_dir,
                            backup_name=requested_backup,
                            updated_by="observatory-ui",
                        )
                    except FileNotFoundError as exc:
                        self._audit(
                            "rollback",
                            method="POST",
                            endpoint=path,
                            status="not-found",
                            requested_backup=requested_backup,
                            before_version=before_version,
                            error=str(exc),
                        )
                        self._send_json(404, {"ok": False, "error": str(exc)})
                        return

                    after_cfg = result.get("config", {})
                    if not isinstance(after_cfg, dict):
                        after_cfg = {}
                    after_version = _safe_int(after_cfg.get("version", before_version), before_version)

                regenerate("api-rollback")
                self._audit(
                    "rollback",
                    method="POST",
                    endpoint=path,
                    status="ok",
                    requested_backup=requested_backup,
                    restored=result.get("restored", ""),
                    rollback_backup=result.get("rollback_backup"),
                    before_version=before_version,
                    after_version=after_version,
                )
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "restored": result.get("restored", ""),
                        "rollback_backup": result.get("rollback_backup"),
                        "config": result.get("config", {}),
                        "backups": list_agent_config_backups(out_dir),
                        "commit_required": commit_required,
                        "read_only": read_only,
                    },
                )
                return

            self._audit(
                "request",
                method="POST",
                endpoint=path,
                status="not-found",
                error="Unknown endpoint",
            )
            self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {path}"})

    server = http.server.ThreadingHTTPServer((host, port), ObservatoryHandler)
    server.daemon_threads = True
    bound_port = _safe_int(getattr(server, "server_port", port), port)
    display_host = host if host not in {"", "0.0.0.0", "::"} else "127.0.0.1"  # noqa: S104

    print(f"🔭 Grimoire Observatory serving at http://{display_host}:{bound_port}/{OBSERVATORY_HTML}")
    print(f"   Auto-reload: watching {len(watch_files)} files every 2s")
    print(f"   Agent config API: commit-required={'on' if commit_required else 'off'}")
    print(f"   Agent config API: read-only={'on' if read_only else 'off'}")
    print(f"   Agent config audit: {_agent_config_audit_path(out_dir)}")
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
    sp_serve.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1)")
    sp_serve.add_argument("--port", type=int, default=8420, help="HTTP port (default: 8420)")
    sp_serve.add_argument(
        "--commit-required",
        action="store_true",
        help="Require a commit message for config apply operations via API",
    )
    sp_serve.add_argument(
      "--read-only",
      action="store_true",
      help="Disable apply/rollback mutations and expose config API in read-only mode",
    )

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
