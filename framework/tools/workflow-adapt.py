#!/usr/bin/env python3
"""
workflow-adapt.py — Plasticité synaptique Grimoire.
================================================

Analyse les traces d'exécution (Grimoire_TRACE) pour détecter les patterns
d'utilisation réels et proposer des adaptations automatiques des workflows :

  - Détection des étapes systématiquement sautées (dead code workflow)
  - Identification des chemins fréquents (desire paths)
  - Mesure de l'effort réel vs planifié
  - Overlay d'adaptations proposées (JIT, moindre action)
  - Apprentissage continu : chaque exécution affine le modèle

Principe : "Le système le plus efficace est celui qui s'adapte à l'usage réel,
pas celui qui force un usage théorique."

Usage :
  python3 workflow-adapt.py --project-root . analyze
  python3 workflow-adapt.py --project-root . overlay
  python3 workflow-adapt.py --project-root . prune
  python3 workflow-adapt.py --project-root . jit
  python3 workflow-adapt.py --project-root . history

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.workflow_adapt")

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"
TRACE_DIR_DEFAULT = ".grimoire-trace"
ADAPT_HISTORY = "workflow-adapt-history.json"

ADAPTATION_TYPES = {
    "PRUNE": "Étape inutilisée — candidate à la suppression",
    "SHORTCUT": "Raccourci détecté — formaliser le chemin court",
    "JIT": "Étape différable — exécuter juste-à-temps",
    "MERGE": "Étapes souvent consécutives — fusionner",
    "SPLIT": "Étape trop longue — découper",
    "REORDER": "Ordre sous-optimal — réorganiser",
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class TraceEntry:
    """Une entrée de trace d'exécution."""
    workflow: str = ""
    step: str = ""
    status: str = ""          # completed, skipped, failed
    duration_s: float = 0.0
    timestamp: str = ""

@dataclass
class WorkflowStats:
    """Statistiques d'utilisation d'un workflow."""
    name: str = ""
    executions: int = 0
    avg_duration_s: float = 0.0
    step_usage: dict[str, int] = field(default_factory=dict)       # step → count completed
    step_skipped: dict[str, int] = field(default_factory=dict)     # step → count skipped
    step_failed: dict[str, int] = field(default_factory=dict)      # step → count failed
    step_durations: dict[str, list[float]] = field(default_factory=dict)

@dataclass
class Adaptation:
    """Proposition d'adaptation."""
    workflow: str
    step: str
    kind: str           # PRUNE, SHORTCUT, JIT, MERGE, SPLIT, REORDER
    reason: str
    confidence: float   # 0.0-1.0
    evidence: str = ""

@dataclass
class AdaptReport:
    workflows_analyzed: int = 0
    total_traces: int = 0
    adaptations: list[Adaptation] = field(default_factory=list)
    workflow_stats: list[WorkflowStats] = field(default_factory=list)


# ── Trace Parser ─────────────────────────────────────────────────────────────

def _find_trace_files(project_root: Path) -> list[Path]:
    """Cherche les fichiers de trace (.grimoire-trace/, ou patterns connus)."""
    traces = []
    trace_dir = project_root / TRACE_DIR_DEFAULT
    if trace_dir.exists():
        traces.extend(trace_dir.rglob("*.json"))
        traces.extend(trace_dir.rglob("*.log"))

    # Aussi chercher dans _grimoire/_memory
    mem_dir = project_root / "_grimoire" / "_memory"
    if mem_dir.exists():
        for f in mem_dir.rglob("*trace*"):
            traces.append(f)

    return sorted(traces)


def _parse_trace_json(fpath: Path) -> list[TraceEntry]:
    """Parse un fichier de trace JSON."""
    entries = []
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for item in data:
                entries.append(TraceEntry(
                    workflow=item.get("workflow", "unknown"),
                    step=item.get("step", ""),
                    status=item.get("status", "completed"),
                    duration_s=float(item.get("duration_s", 0)),
                    timestamp=item.get("timestamp", ""),
                ))
        elif isinstance(data, dict) and "steps" in data:
            wf_name = data.get("workflow", fpath.stem)
            for step in data["steps"]:
                entries.append(TraceEntry(
                    workflow=wf_name,
                    step=step.get("name", ""),
                    status=step.get("status", "completed"),
                    duration_s=float(step.get("duration_s", 0)),
                    timestamp=step.get("timestamp", ""),
                ))
    except (json.JSONDecodeError, OSError, ValueError) as _exc:
        _log.debug("json.JSONDecodeError, OSError, ValueError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues
    return entries


def _parse_trace_log(fpath: Path) -> list[TraceEntry]:
    """Parse un fichier de trace log textuel."""
    entries = []
    pattern = re.compile(
        r"\[(?P<ts>[^\]]+)\]\s+(?P<wf>\S+)\s+→\s+(?P<step>\S+)\s+:\s+(?P<status>\w+)"
    )
    try:
        for line in fpath.read_text(encoding="utf-8").splitlines():
            m = pattern.search(line)
            if m:
                entries.append(TraceEntry(
                    workflow=m.group("wf"),
                    step=m.group("step"),
                    status=m.group("status").lower(),
                    timestamp=m.group("ts"),
                ))
    except (OSError, UnicodeDecodeError) as _exc:
        _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues
    return entries


def load_traces(project_root: Path) -> list[TraceEntry]:
    """Charge toutes les traces disponibles."""
    all_entries = []
    for fpath in _find_trace_files(project_root):
        if fpath.suffix == ".json":
            all_entries.extend(_parse_trace_json(fpath))
        else:
            all_entries.extend(_parse_trace_log(fpath))
    return all_entries


# ── Workflow Analysis ────────────────────────────────────────────────────────

def _synthesize_workflow_files(project_root: Path) -> list[WorkflowStats]:
    """Synthétise les workflows définis (même sans traces)."""
    stats = []
    workflow_dirs = [
        project_root / "framework" / "workflows",
        project_root / "_grimoire" / "bmm" / "workflows",
        project_root / "_grimoire" / "core" / "workflows",
    ]
    for wdir in workflow_dirs:
        if not wdir.exists():
            continue
        for fpath in wdir.rglob("*.yaml"):
            ws = WorkflowStats(name=fpath.stem)
            try:
                content = fpath.read_text(encoding="utf-8")
                # Extraire les steps définis
                step_pat = re.compile(r"^\s*-?\s*(?:name|step|id)\s*:\s*(.+)", re.MULTILINE)
                for m in step_pat.finditer(content):
                    step_name = m.group(1).strip().strip("'\"")
                    ws.step_usage[step_name] = 0
            except (OSError, UnicodeDecodeError) as _exc:
                _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
            stats.append(ws)
    return stats


def build_stats(traces: list[TraceEntry], project_root: Path) -> list[WorkflowStats]:
    """Construit les stats agrégées par workflow."""
    wf_map: dict[str, WorkflowStats] = {}

    for t in traces:
        if t.workflow not in wf_map:
            wf_map[t.workflow] = WorkflowStats(name=t.workflow)
        ws = wf_map[t.workflow]
        ws.executions += 1

        if t.status == "completed":
            ws.step_usage[t.step] = ws.step_usage.get(t.step, 0) + 1
        elif t.status == "skipped":
            ws.step_skipped[t.step] = ws.step_skipped.get(t.step, 0) + 1
        elif t.status == "failed":
            ws.step_failed[t.step] = ws.step_failed.get(t.step, 0) + 1

        if t.duration_s > 0:
            ws.step_durations.setdefault(t.step, []).append(t.duration_s)

    # Ajouter les workflows connus mais sans trace
    for defined_ws in _synthesize_workflow_files(project_root):
        if defined_ws.name not in wf_map:
            wf_map[defined_ws.name] = defined_ws

    return list(wf_map.values())


# ── Adaptation Analysis ─────────────────────────────────────────────────────

def detect_adaptations(stats: list[WorkflowStats]) -> list[Adaptation]:
    """Détecte les adaptations possibles."""
    adaptations = []

    for ws in stats:
        total_exec = ws.executions
        if total_exec == 0:
            continue

        # PRUNE — étapes skippées > 70% du temps
        all_steps = set(ws.step_usage) | set(ws.step_skipped) | set(ws.step_failed)
        for step in all_steps:
            used = ws.step_usage.get(step, 0)
            skipped = ws.step_skipped.get(step, 0)
            total = used + skipped
            if total > 3 and skipped / total > 0.7:
                adaptations.append(Adaptation(
                    workflow=ws.name, step=step, kind="PRUNE",
                    reason=f"Skippée {skipped}/{total} fois ({skipped/total:.0%})",
                    confidence=min(1.0, skipped / total),
                    evidence=f"Seuls {used} passages en {total} exécutions",
                ))

        # JIT — étapes avec durée longue + pas toujours nécessaires
        for step, durations in ws.step_durations.items():
            avg = sum(durations) / len(durations) if durations else 0
            skipped = ws.step_skipped.get(step, 0)
            if avg > 30 and skipped > 0:  # > 30s et parfois skippée
                adaptations.append(Adaptation(
                    workflow=ws.name, step=step, kind="JIT",
                    reason=f"Durée moyenne {avg:.0f}s et skippée {skipped}x — différer si nécessaire",
                    confidence=0.6,
                ))

        # MERGE — étapes très rapides consécutives
        sorted_steps = sorted(ws.step_durations.items(),
                              key=lambda x: sum(x[1]) / len(x[1]) if x[1] else 0)
        fast_steps = [s for s, d in sorted_steps if d and sum(d)/len(d) < 5]
        if len(fast_steps) >= 2:
            adaptations.append(Adaptation(
                workflow=ws.name, step=", ".join(fast_steps[:3]), kind="MERGE",
                reason=f"{len(fast_steps)} étapes < 5s — potentiel de fusion",
                confidence=0.4,
            ))

    return adaptations


def detect_from_workflow_structure(project_root: Path) -> list[Adaptation]:
    """Détecte des adaptations depuis la structure même des workflows (sans traces)."""
    adaptations = []
    workflow_dirs = [
        project_root / "framework" / "workflows",
        project_root / "_grimoire" / "bmm" / "workflows",
    ]
    for wdir in workflow_dirs:
        if not wdir.exists():
            continue
        for fpath in wdir.rglob("*.yaml"):
            try:
                content = fpath.read_text(encoding="utf-8")
                # Trop d'étapes
                step_count = content.lower().count("step:")
                if step_count > 10:
                    adaptations.append(Adaptation(
                        workflow=fpath.stem, step="(global)", kind="SPLIT",
                        reason=f"{step_count} étapes — workflow trop long",
                        confidence=0.5,
                    ))
            except (OSError, UnicodeDecodeError) as _exc:
                _log.debug("OSError, UnicodeDecodeError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
    return adaptations


# ── Report Builder ───────────────────────────────────────────────────────────

def build_adapt_report(project_root: Path) -> AdaptReport:
    traces = load_traces(project_root)
    stats = build_stats(traces, project_root)
    adaptations = detect_adaptations(stats)
    adaptations += detect_from_workflow_structure(project_root)

    return AdaptReport(
        workflows_analyzed=len(stats),
        total_traces=len(traces),
        adaptations=adaptations,
        workflow_stats=stats,
    )


# ── Persistence ──────────────────────────────────────────────────────────────

def save_history(project_root: Path, report: AdaptReport) -> Path:
    out = project_root / "_grimoire" / "_memory" / ADAPT_HISTORY
    out.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if out.exists():
        try:
            history = json.loads(out.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as _exc:
            _log.debug("json.JSONDecodeError, OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
    entry = {
        "timestamp": datetime.now().isoformat(),
        "workflows_analyzed": report.workflows_analyzed,
        "traces": report.total_traces,
        "adaptations": len(report.adaptations),
        "types": dict(Counter(a.kind for a in report.adaptations)),
    }
    history.append(entry)
    out.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ── Formatters ───────────────────────────────────────────────────────────────

def format_analysis(report: AdaptReport) -> str:
    lines = [
        "🧠 Plasticité synaptique — Analyse adaptative",
        f"   Workflows analysés : {report.workflows_analyzed}",
        f"   Traces chargées : {report.total_traces}",
        f"   Adaptations détectées : {len(report.adaptations)}",
        "",
    ]
    if report.adaptations:
        by_kind = defaultdict(list)
        for a in report.adaptations:
            by_kind[a.kind].append(a)
        for kind, items in sorted(by_kind.items()):
            lines.append(f"   {kind} — {ADAPTATION_TYPES.get(kind, '')}")
            for a in items:
                conf = "█" * int(a.confidence * 5)
                lines.append(f"      {conf} {a.workflow} → {a.step}")
                lines.append(f"           {a.reason}")
            lines.append("")
    else:
        lines.append("   Aucune adaptation identifiée (manque de traces ou workflows optimaux).")
    return "\n".join(lines)


def format_overlay(report: AdaptReport) -> str:
    """Génère un overlay YAML-like d'adaptations."""
    lines = ["# workflow-adaptations.yaml — Overlay généré automatiquement", ""]
    for ws in report.workflow_stats:
        relevant = [a for a in report.adaptations if a.workflow == ws.name]
        if not relevant:
            continue
        lines.append(f"{ws.name}:")
        for a in relevant:
            lines.append(f"  - step: {a.step}")
            lines.append(f"    action: {a.kind}")
            lines.append(f"    reason: \"{a.reason}\"")
            lines.append(f"    confidence: {a.confidence:.2f}")
        lines.append("")
    if not any(a for a in report.adaptations):
        lines.append("# Aucune adaptation — overlay vide")
    return "\n".join(lines)


def report_to_dict(report: AdaptReport) -> dict:
    return {
        "workflows_analyzed": report.workflows_analyzed,
        "total_traces": report.total_traces,
        "adaptations": [
            {"workflow": a.workflow, "step": a.step, "kind": a.kind,
             "reason": a.reason, "confidence": a.confidence}
            for a in report.adaptations
        ],
        "workflow_stats": [
            {"name": ws.name, "executions": ws.executions,
             "steps_defined": len(ws.step_usage)}
            for ws in report.workflow_stats
        ],
    }


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_analyze(args: argparse.Namespace) -> int:
    report = build_adapt_report(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(format_analysis(report))
    return 0


def cmd_overlay(args: argparse.Namespace) -> int:
    report = build_adapt_report(Path(args.project_root).resolve())
    print(format_overlay(report))
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    report = build_adapt_report(Path(args.project_root).resolve())
    prune = [a for a in report.adaptations if a.kind == "PRUNE"]
    if args.json:
        print(json.dumps([{"workflow": a.workflow, "step": a.step, "reason": a.reason}
                          for a in prune], indent=2, ensure_ascii=False))
    else:
        print(f"✂️ Candidats à l'élagage : {len(prune)}\n")
        for a in prune:
            print(f"   {a.workflow} → {a.step}")
            print(f"   ↳ {a.reason}")
    return 0


def cmd_jit(args: argparse.Namespace) -> int:
    report = build_adapt_report(Path(args.project_root).resolve())
    jit = [a for a in report.adaptations if a.kind == "JIT"]
    if args.json:
        print(json.dumps([{"workflow": a.workflow, "step": a.step, "reason": a.reason}
                          for a in jit], indent=2, ensure_ascii=False))
    else:
        print(f"⏱️ Candidats JIT (différer) : {len(jit)}\n")
        for a in jit:
            print(f"   {a.workflow} → {a.step}")
            print(f"   ↳ {a.reason}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    report = build_adapt_report(project_root)
    out = save_history(project_root, report)
    if args.json:
        data = json.loads(out.read_text(encoding="utf-8"))
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        data = json.loads(out.read_text(encoding="utf-8"))
        print(f"📜 Historique d'adaptations ({len(data)} entrées)\n")
        for entry in data[-10:]:
            print(f"   {entry['timestamp'][:19]} — {entry['adaptations']} adaptations, {entry['traces']} traces")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Plasticité synaptique — Workflows adaptatifs",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")
    subs.add_parser("analyze", help="Analyse des traces + adaptations").set_defaults(func=cmd_analyze)
    subs.add_parser("overlay", help="Overlay YAML d'adaptations").set_defaults(func=cmd_overlay)
    subs.add_parser("prune", help="Étapes à élaguer").set_defaults(func=cmd_prune)
    subs.add_parser("jit", help="Étapes à différer (JIT)").set_defaults(func=cmd_jit)
    subs.add_parser("history", help="Historique d'analyses").set_defaults(func=cmd_history)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
