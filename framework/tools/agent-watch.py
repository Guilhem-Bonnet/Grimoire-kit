#!/usr/bin/env python3
"""
agent-watch.py — Drift Detection & Monitoring for Grimoire Agents.
=================================================================

Surveille la dérive comportementale d'un agent par rapport à son baseline.
Détecte les changements de persona, d'utilisation d'outils, de qualité
d'output et de pattern d'exécution.

Modes :
  snapshot  — Capture le baseline actuel d'un agent
  check     — Compare l'état courant au baseline
  drift     — Rapport de dérive détaillé
  history   — Historique des checks de drift

Usage :
  python3 agent-watch.py --project-root . snapshot --agent blender-expert
  python3 agent-watch.py --project-root . check --agent blender-expert
  python3 agent-watch.py --project-root . drift --agent blender-expert
  python3 agent-watch.py --project-root . history --last 10

Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.agent_watch")

AGENT_WATCH_VERSION = "1.0.0"

WATCH_DIR = "_grimoire-output/.agent-watch"
BASELINES_DIR = "baselines"
CHECKS_DIR = "checks"
HISTORY_FILE = "watch-history.jsonl"

# Drift thresholds
DRIFT_THRESHOLD_LOW = 0.10    # < 10% change = stable
DRIFT_THRESHOLD_MED = 0.25    # 10-25% = minor drift
DRIFT_THRESHOLD_HIGH = 0.50   # 25-50% = significant drift
# > 50% = critical drift


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class AgentFingerprint:
    """Empreinte d'un agent à un instant donné."""

    agent_name: str = ""
    agent_file: str = ""
    timestamp: str = ""
    file_hash: str = ""
    # Structure
    has_persona: bool = False
    has_menu: bool = False
    has_rules: bool = False
    has_activation: bool = False
    has_vision_loop: bool = False
    # Counts
    num_capabilities: int = 0
    num_mcp_servers: int = 0
    num_handlers: int = 0
    num_rules: int = 0
    num_menu_items: int = 0
    # Content signatures
    capabilities: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    handlers: list[str] = field(default_factory=list)
    persona_hash: str = ""
    rules_hash: str = ""
    # Size
    total_lines: int = 0
    total_chars: int = 0


@dataclass
class DriftVector:
    """Vecteur de dérive entre deux fingerprints."""

    dimension: str = ""         # structure | capabilities | mcp | persona | rules | size
    baseline_value: str = ""
    current_value: str = ""
    drift_score: float = 0.0   # 0 = identical, 1 = completely different
    severity: str = "none"     # none | low | medium | high | critical
    detail: str = ""


@dataclass
class DriftReport:
    """Rapport complet de dérive d'un agent."""

    agent_name: str = ""
    agent_file: str = ""
    timestamp: str = ""
    baseline_timestamp: str = ""
    overall_drift: float = 0.0   # 0-1 aggregated drift score
    severity: str = "stable"     # stable | low | medium | high | critical
    vectors: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    recommendation: str = ""


# ── Fingerprinting ───────────────────────────────────────────────────────────


def _hash_block(content: str, tag_open: str, tag_close: str) -> str:
    """Hash le contenu d'un bloc balisé."""
    pattern = re.compile(re.escape(tag_open) + r'(.*?)' + re.escape(tag_close), re.DOTALL)
    m = pattern.search(content)
    if m:
        return hashlib.sha256(m.group(1).strip().encode()).hexdigest()[:16]
    return ""


def fingerprint_agent(agent_path: Path) -> AgentFingerprint:
    """Capture l'empreinte complète d'un agent."""
    fp = AgentFingerprint(
        agent_name=agent_path.stem,
        agent_file=str(agent_path),
        timestamp=datetime.now().isoformat(),
    )

    if not agent_path.exists():
        return fp

    content = agent_path.read_text(encoding="utf-8")
    fp.file_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
    fp.total_lines = content.count("\n") + 1
    fp.total_chars = len(content)

    # Structure
    fp.has_persona = "<persona>" in content or "<persona " in content
    fp.has_menu = "<menu>" in content or "<menu " in content
    fp.has_rules = "<rules>" in content or "<r>" in content
    fp.has_activation = "<activation" in content
    fp.has_vision_loop = bool(
        re.search(r'vision.?loop|vision.?judge|visual.?eval', content, re.IGNORECASE)
    )

    # Capabilities
    for m in re.finditer(r'<cap\s+id="([^"]+)"', content):
        fp.capabilities.append(m.group(1))
    fp.num_capabilities = len(fp.capabilities)

    # MCP servers
    for m in re.finditer(r'<server\s+name="([^"]+)"', content):
        fp.mcp_servers.append(m.group(1))
    fp.num_mcp_servers = len(fp.mcp_servers)

    # Handlers
    for m in re.finditer(r'<handler\s+id="([^"]+)"', content):
        fp.handlers.append(m.group(1))
    fp.num_handlers = len(fp.handlers)

    # Rules count
    fp.num_rules = len(re.findall(r'<r[^u>]', content))

    # Menu items
    fp.num_menu_items = len(re.findall(r'^\s*\d+\.\s+\*\*', content, re.MULTILINE))

    # Content hashes for drift detection
    fp.persona_hash = _hash_block(content, "<persona>", "</persona>")
    if not fp.persona_hash:
        fp.persona_hash = _hash_block(content, '<persona ', "</persona>")
    fp.rules_hash = _hash_block(content, "<rules>", "</rules>")
    if not fp.rules_hash:
        fp.rules_hash = _hash_block(content, "<r>", "</r>")

    return fp


# ── Baseline Management ─────────────────────────────────────────────────────


def save_baseline(fp: AgentFingerprint, project_root: Path) -> Path:
    """Sauvegarde un baseline pour un agent."""
    baselines_dir = project_root / WATCH_DIR / BASELINES_DIR
    baselines_dir.mkdir(parents=True, exist_ok=True)
    baseline_file = baselines_dir / f"{fp.agent_name}-baseline.json"
    baseline_file.write_text(
        json.dumps(asdict(fp), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return baseline_file


def load_baseline(agent_name: str, project_root: Path) -> AgentFingerprint | None:
    """Charge le baseline d'un agent."""
    baseline_file = project_root / WATCH_DIR / BASELINES_DIR / f"{agent_name}-baseline.json"
    if not baseline_file.exists():
        return None
    try:
        data = json.loads(baseline_file.read_text(encoding="utf-8"))
        fp = AgentFingerprint()
        for k, v in data.items():
            if hasattr(fp, k):
                setattr(fp, k, v)
        return fp
    except (json.JSONDecodeError, OSError):
        return None


# ── Drift Detection ─────────────────────────────────────────────────────────


def _set_drift(added: set, removed: set, unchanged: set) -> float:
    """Calcule le score de dérive entre deux ensembles."""
    total = len(added) + len(removed) + len(unchanged)
    if total == 0:
        return 0.0
    changes = len(added) + len(removed)
    return changes / total


def _severity(score: float) -> str:
    """Classifie la sévérité d'un score de dérive."""
    if score < DRIFT_THRESHOLD_LOW:
        return "none"
    if score < DRIFT_THRESHOLD_MED:
        return "low"
    if score < DRIFT_THRESHOLD_HIGH:
        return "medium"
    return "high" if score < 1.0 else "critical"


def compute_drift(baseline: AgentFingerprint, current: AgentFingerprint) -> DriftReport:
    """Calcule la dérive entre un baseline et l'état courant."""
    report = DriftReport(
        agent_name=current.agent_name,
        agent_file=current.agent_file,
        timestamp=current.timestamp,
        baseline_timestamp=baseline.timestamp,
    )
    vectors: list[DriftVector] = []

    # 1. File hash — global change indicator
    file_changed = baseline.file_hash != current.file_hash
    vectors.append(DriftVector(
        dimension="file_hash",
        baseline_value=baseline.file_hash,
        current_value=current.file_hash,
        drift_score=1.0 if file_changed else 0.0,
        severity="low" if file_changed else "none",
        detail="File content changed" if file_changed else "Identical",
    ))

    if not file_changed:
        report.overall_drift = 0.0
        report.severity = "stable"
        report.vectors = [asdict(v) for v in vectors]
        report.recommendation = "Agent identique au baseline — aucune action requise."
        return report

    # 2. Structure drift
    struct_fields = ["has_persona", "has_menu", "has_rules", "has_activation", "has_vision_loop"]
    struct_changes = sum(
        1 for f in struct_fields
        if getattr(baseline, f) != getattr(current, f)
    )
    struct_score = struct_changes / len(struct_fields) if struct_fields else 0.0
    changed_fields = [
        f for f in struct_fields
        if getattr(baseline, f) != getattr(current, f)
    ]
    vectors.append(DriftVector(
        dimension="structure",
        baseline_value=str({f: getattr(baseline, f) for f in struct_fields}),
        current_value=str({f: getattr(current, f) for f in struct_fields}),
        drift_score=struct_score,
        severity=_severity(struct_score),
        detail=f"Changed: {', '.join(changed_fields)}" if changed_fields else "Stable",
    ))

    # 3. Capabilities drift
    base_caps = set(baseline.capabilities)
    curr_caps = set(current.capabilities)
    added_caps = curr_caps - base_caps
    removed_caps = base_caps - curr_caps
    cap_score = _set_drift(added_caps, removed_caps, base_caps & curr_caps)
    vectors.append(DriftVector(
        dimension="capabilities",
        baseline_value=str(sorted(base_caps)),
        current_value=str(sorted(curr_caps)),
        drift_score=cap_score,
        severity=_severity(cap_score),
        detail=f"+{list(added_caps)}, -{list(removed_caps)}" if added_caps or removed_caps else "Stable",
    ))

    if removed_caps:
        report.alerts.append(f"⚠️ Capabilities supprimées: {', '.join(removed_caps)}")

    # 4. MCP servers drift
    base_mcp = set(baseline.mcp_servers)
    curr_mcp = set(current.mcp_servers)
    added_mcp = curr_mcp - base_mcp
    removed_mcp = base_mcp - curr_mcp
    mcp_score = _set_drift(added_mcp, removed_mcp, base_mcp & curr_mcp)
    vectors.append(DriftVector(
        dimension="mcp_servers",
        baseline_value=str(sorted(base_mcp)),
        current_value=str(sorted(curr_mcp)),
        drift_score=mcp_score,
        severity=_severity(mcp_score),
        detail=f"+{list(added_mcp)}, -{list(removed_mcp)}" if added_mcp or removed_mcp else "Stable",
    ))

    if removed_mcp:
        report.alerts.append(f"🔴 Serveurs MCP retirés: {', '.join(removed_mcp)}")

    # 5. Persona drift
    persona_changed = baseline.persona_hash != current.persona_hash and baseline.persona_hash
    persona_score = 1.0 if persona_changed else 0.0
    vectors.append(DriftVector(
        dimension="persona",
        baseline_value=baseline.persona_hash,
        current_value=current.persona_hash,
        drift_score=persona_score,
        severity="high" if persona_changed else "none",
        detail="Persona block content changed" if persona_changed else "Stable",
    ))

    if persona_changed:
        report.alerts.append("🔴 Persona modifiée — vérifier la cohérence d'identité")

    # 6. Rules drift
    rules_changed = baseline.rules_hash != current.rules_hash and baseline.rules_hash
    rules_score = 1.0 if rules_changed else 0.0
    vectors.append(DriftVector(
        dimension="rules",
        baseline_value=baseline.rules_hash,
        current_value=current.rules_hash,
        drift_score=rules_score,
        severity="medium" if rules_changed else "none",
        detail="Rules block content changed" if rules_changed else "Stable",
    ))

    # 7. Size drift (proportional change)
    if baseline.total_lines > 0:
        size_ratio = abs(current.total_lines - baseline.total_lines) / baseline.total_lines
    else:
        size_ratio = 1.0 if current.total_lines > 0 else 0.0
    size_score = min(size_ratio, 1.0)
    vectors.append(DriftVector(
        dimension="size",
        baseline_value=f"{baseline.total_lines} lines / {baseline.total_chars} chars",
        current_value=f"{current.total_lines} lines / {current.total_chars} chars",
        drift_score=size_score,
        severity=_severity(size_score),
        detail=f"Δ {current.total_lines - baseline.total_lines:+d} lines, "
               f"Δ {current.total_chars - baseline.total_chars:+d} chars",
    ))

    if size_score > DRIFT_THRESHOLD_HIGH:
        report.alerts.append(f"⚠️ Changement de taille important: {size_score:.0%}")

    # 8. Handlers drift
    base_handlers = set(baseline.handlers)
    curr_handlers = set(current.handlers)
    added_h = curr_handlers - base_handlers
    removed_h = base_handlers - curr_handlers
    handler_score = _set_drift(added_h, removed_h, base_handlers & curr_handlers)
    vectors.append(DriftVector(
        dimension="handlers",
        baseline_value=str(sorted(base_handlers)),
        current_value=str(sorted(curr_handlers)),
        drift_score=handler_score,
        severity=_severity(handler_score),
        detail=f"+{list(added_h)}, -{list(removed_h)}" if added_h or removed_h else "Stable",
    ))

    # Aggregate
    weights = {
        "file_hash": 0.0,      # Not used in aggregate (it's meta)
        "structure": 0.15,
        "capabilities": 0.20,
        "mcp_servers": 0.15,
        "persona": 0.25,       # Persona drift is most important
        "rules": 0.10,
        "size": 0.05,
        "handlers": 0.10,
    }
    weighted_sum = sum(
        v.drift_score * weights.get(v.dimension, 0.0)
        for v in vectors
    )
    total_weight = sum(
        weights.get(v.dimension, 0.0) for v in vectors
    )
    report.overall_drift = round(weighted_sum / total_weight, 3) if total_weight > 0 else 0.0
    report.severity = _severity(report.overall_drift)
    report.vectors = [asdict(v) for v in vectors]

    # Recommendation
    if report.severity == "stable" or report.severity == "none":
        report.recommendation = "Agent stable — pas d'action requise."
    elif report.severity == "low":
        report.recommendation = "Dérive mineure — surveiller lors du prochain check."
    elif report.severity == "medium":
        report.recommendation = "Dérive notable — relancer les tests comportementaux (agent-test.py run)."
    elif report.severity == "high":
        report.recommendation = ("Dérive significative — review humain recommandé. "
                                 "Envisager un re-baseline ou un rollback.")
    else:
        report.recommendation = ("⚠️ Dérive critique — agent potentiellement cassé. "
                                 "Rollback vers le baseline recommandé.")

    return report


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_agent_watch_snapshot(
    agent_file: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: capture un baseline snapshot pour un agent.

    Args:
        agent_file: Chemin vers le fichier agent (.md).
        project_root: Racine du projet.

    Returns:
        AgentFingerprint sérialisé.
    """
    root = Path(project_root).resolve()
    agent_path = root / agent_file if not Path(agent_file).is_absolute() else Path(agent_file)
    fp = fingerprint_agent(agent_path)
    save_baseline(fp, root)
    return asdict(fp)


def mcp_agent_watch_check(
    agent_file: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: vérifie la dérive d'un agent par rapport à son baseline.

    Args:
        agent_file: Chemin vers le fichier agent (.md).
        project_root: Racine du projet.

    Returns:
        DriftReport sérialisé.
    """
    root = Path(project_root).resolve()
    agent_path = root / agent_file if not Path(agent_file).is_absolute() else Path(agent_file)
    agent_name = agent_path.stem
    baseline = load_baseline(agent_name, root)
    if baseline is None:
        return {"error": f"No baseline found for '{agent_name}'. Run snapshot first."}
    current = fingerprint_agent(agent_path)
    report = compute_drift(baseline, current)

    # Save check
    checks_dir = root / WATCH_DIR / CHECKS_DIR
    checks_dir.mkdir(parents=True, exist_ok=True)
    check_file = checks_dir / f"{agent_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    check_file.write_text(
        json.dumps(asdict(report), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Append history
    history_path = root / WATCH_DIR / HISTORY_FILE
    with history_path.open("a", encoding="utf-8") as f:
        entry = {
            "agent": agent_name,
            "drift": report.overall_drift,
            "severity": report.severity,
            "alerts": len(report.alerts),
            "timestamp": report.timestamp,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return asdict(report)


def mcp_agent_watch_history(
    last: int = 10,
    project_root: str = ".",
) -> list[dict[str, Any]]:
    """MCP tool: historique des checks de dérive.

    Args:
        last: Nombre de résultats récents.
        project_root: Racine du projet.

    Returns:
        Liste des entrées d'historique.
    """
    root = Path(project_root).resolve()
    history_path = root / WATCH_DIR / HISTORY_FILE
    if not history_path.exists():
        return []
    lines = history_path.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    for line in lines[-last:]:
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


# ── CLI Commands ─────────────────────────────────────────────────────────────


def cmd_snapshot(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    agent_path = root / args.agent
    fp = fingerprint_agent(agent_path)
    baseline_file = save_baseline(fp, root)

    if args.json:
        print(json.dumps(asdict(fp), indent=2, ensure_ascii=False))
    else:
        print(f"\n  📸 Baseline captured: {fp.agent_name}")
        print(f"  File: {fp.agent_file}")
        print(f"  Hash: {fp.file_hash}")
        print(f"  Structure: persona={fp.has_persona}, menu={fp.has_menu}, "
              f"rules={fp.has_rules}, activation={fp.has_activation}")
        print(f"  Capabilities: {fp.num_capabilities} | MCP: {fp.num_mcp_servers} | "
              f"Handlers: {fp.num_handlers}")
        print(f"  Size: {fp.total_lines} lines / {fp.total_chars} chars")
        print(f"  Saved: {baseline_file}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    agent_path = root / args.agent
    agent_name = agent_path.stem

    baseline = load_baseline(agent_name, root)
    if baseline is None:
        print(f"  ❌ No baseline for '{agent_name}'. Run: agent-watch snapshot --agent {args.agent}")
        return 1

    current = fingerprint_agent(agent_path)
    report = compute_drift(baseline, current)

    # Save
    checks_dir = root / WATCH_DIR / CHECKS_DIR
    checks_dir.mkdir(parents=True, exist_ok=True)
    check_file = checks_dir / f"{agent_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    check_file.write_text(
        json.dumps(asdict(report), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if args.json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        sev_icons = {"stable": "🟢", "none": "🟢", "low": "🔵", "medium": "🟡", "high": "🟠", "critical": "🔴"}
        icon = sev_icons.get(report.severity, "⚪")
        print(f"\n  {icon} Drift Check: {report.agent_name} — {report.severity.upper()} ({report.overall_drift:.1%})")
        print(f"  Baseline: {report.baseline_timestamp[:16]} → Now: {report.timestamp[:16]}\n")

        for v in report.vectors:
            if v["dimension"] == "file_hash":
                continue
            vi = sev_icons.get(v["severity"], "⚪")
            print(f"  {vi} {v['dimension']:15s} {v['drift_score']:.0%} — {v['detail']}")

        if report.alerts:
            print("\n  🚨 Alertes:")
            for a in report.alerts:
                print(f"    {a}")

        print(f"\n  💡 {report.recommendation}")
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    """Rapport de dérive détaillé — identique à check avec plus de détails."""
    return cmd_check(args)


def cmd_history(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    entries = mcp_agent_watch_history(args.last, str(root))

    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        if not entries:
            print("  Aucun historique de monitoring.")
            return 0
        print(f"\n  📊 Derniers {len(entries)} checks:\n")
        for e in reversed(entries):
            sev = e.get("severity", "?")
            icon = {"stable": "🟢", "none": "🟢", "low": "🔵", "medium": "🟡",
                    "high": "🟠", "critical": "🔴"}.get(sev, "⚪")
            alerts = f" ({e.get('alerts', 0)} alertes)" if e.get("alerts", 0) > 0 else ""
            print(f"  {icon} {e.get('agent', '?'):20s} {sev:10s} "
                  f"drift={e.get('drift', 0):.1%}{alerts} [{e.get('timestamp', '')[:16]}]")
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-watch",
        description="Drift Detection & Monitoring for Grimoire Agents",
    )
    p.add_argument("--project-root", default=".", help="Project root directory")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    sub = p.add_subparsers(dest="command")

    # snapshot
    s = sub.add_parser("snapshot", help="Capture baseline for an agent")
    s.add_argument("--agent", required=True, help="Path to agent file (relative to project root)")
    s.set_defaults(func=cmd_snapshot)

    # check
    c = sub.add_parser("check", help="Check drift against baseline")
    c.add_argument("--agent", required=True, help="Path to agent file (relative to project root)")
    c.set_defaults(func=cmd_check)

    # drift
    d = sub.add_parser("drift", help="Detailed drift report")
    d.add_argument("--agent", required=True, help="Path to agent file (relative to project root)")
    d.set_defaults(func=cmd_drift)

    # history
    h = sub.add_parser("history", help="Show drift check history")
    h.add_argument("--last", type=int, default=10, help="Number of recent entries")
    h.set_defaults(func=cmd_history)

    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
