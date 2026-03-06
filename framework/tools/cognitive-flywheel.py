#!/usr/bin/env python3
"""Cognitive Flywheel — Boucle d'auto-amélioration continue.

Analyse les sessions accumulées (BMAD_TRACE, mémoire, décisions) pour
extraire des patterns récurrents, scorer la santé du projet, et proposer
des corrections automatiques priorisées par sévérité.

Inspiré par le Cognitive Flywheel de GSANE (zav-sandbox) — adapté
pour l'écosystème BMAD avec intégration des 48 outils existants.

Usage:
    python cognitive-flywheel.py --project-root . analyze
    python cognitive-flywheel.py --project-root . analyze --since 2026-01-01
    python cognitive-flywheel.py --project-root . report
    python cognitive-flywheel.py --project-root . apply --max 5
    python cognitive-flywheel.py --project-root . apply --dry-run
    python cognitive-flywheel.py --project-root . history
    python cognitive-flywheel.py --project-root . score
    python cognitive-flywheel.py --project-root . dashboard --json

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

VERSION = "1.0.0"

# ── Constants ─────────────────────────────────────────────────────────────────

FLYWHEEL_DIR = "_bmad/_memory"
REPORT_FILE = "flywheel-report.json"
HISTORY_FILE = "flywheel-history.jsonl"
SCOREBOARD_FILE = "flywheel-scoreboard.md"

# Pattern thresholds (from GSANE's proven model)
THRESHOLD_NOISE = 1
THRESHOLD_WATCH = 2
THRESHOLD_CONFIRMED = 3

MAX_AUTO_CORRECTIONS = 5

SEVERITIES = ("low", "medium", "high")

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class TraceEntry:
    """Parsed entry from BMAD_TRACE.md."""

    timestamp: str = ""
    agent: str = ""
    entry_type: str = ""
    story: str = ""
    content: str = ""


@dataclass
class Pattern:
    """A recurring pattern detected across sessions."""

    pattern_id: str = ""
    description: str = ""
    occurrences: int = 0
    status: str = "noise"  # noise, watch, confirmed
    severity: str = "low"
    target_type: str = ""  # tool, workflow, agent, config, hook
    target_hint: str = ""
    suggested_action: str = ""
    source_entries: list[str] = field(default_factory=list)


@dataclass
class FlywheelScore:
    """Aggregate health score for a flywheel cycle."""

    cycle_id: str = ""
    timestamp: str = ""
    sessions_analyzed: int = 0
    total_entries: int = 0
    failure_rate: float = 0.0
    patterns_confirmed: int = 0
    patterns_watch: int = 0
    corrections_applied: int = 0
    corrections_deferred: int = 0
    high_severity_pending: int = 0
    trend: str = "stable"  # improving, stable, degrading
    health_grade: str = "A"  # A+, A, B, C, D


@dataclass
class Correction:
    """A proposed or applied correction."""

    correction_id: str = ""
    pattern_id: str = ""
    severity: str = "low"
    target_type: str = ""
    target_file: str = ""
    description: str = ""
    status: str = "pending"  # pending, applied, deferred, high-escalated
    applied_at: str = ""


@dataclass
class FlywheelReport:
    """Complete flywheel cycle report."""

    cycle_id: str = ""
    timestamp: str = ""
    score: FlywheelScore = field(default_factory=FlywheelScore)
    patterns: list[Pattern] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)


# ── File I/O ──────────────────────────────────────────────────────────────────


def _flywheel_dir(root: Path) -> Path:
    d = root / FLYWHEEL_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report_path(root: Path) -> Path:
    return _flywheel_dir(root) / REPORT_FILE


def _history_path(root: Path) -> Path:
    return _flywheel_dir(root) / HISTORY_FILE


def _scoreboard_path(root: Path) -> Path:
    return _flywheel_dir(root) / SCOREBOARD_FILE


def load_report(root: Path) -> FlywheelReport | None:
    """Load latest flywheel report."""
    path = _report_path(root)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    report = FlywheelReport(
        cycle_id=data.get("cycle_id", ""),
        timestamp=data.get("timestamp", ""),
    )
    if "score" in data:
        report.score = FlywheelScore(**data["score"])
    report.patterns = [Pattern(**p) for p in data.get("patterns", [])]
    report.corrections = [Correction(**c) for c in data.get("corrections", [])]
    return report


def save_report(root: Path, report: FlywheelReport) -> None:
    """Save flywheel report as JSON."""
    path = _report_path(root)
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_history(root: Path) -> list[dict]:
    """Load all historical cycles."""
    path = _history_path(root)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def append_history(root: Path, entry: dict) -> None:
    """Append a cycle summary to history."""
    path = _history_path(root)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── BMAD_TRACE parser ────────────────────────────────────────────────────────

# Match lines like: [2026-03-01T10:00:00Z] [agent:dev] [GIT-COMMIT] story:US-01 — content
TRACE_RE = re.compile(
    r"\[(?P<ts>[^\]]+)\]\s*\[agent:(?P<agent>[^\]]+)\]\s*"
    r"\[(?P<type>[^\]]+)\](?:\s*story:(?P<story>\S+))?\s*[—–-]?\s*(?P<content>.*)"
)


def parse_trace(root: Path, since: str | None = None) -> list[TraceEntry]:
    """Parse BMAD_TRACE.md into structured entries."""
    trace_path = root / "_bmad-output" / "BMAD_TRACE.md"
    if not trace_path.exists():
        return []

    entries: list[TraceEntry] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        m = TRACE_RE.match(line.strip())
        if not m:
            continue
        ts = m.group("ts")
        if since and ts < since:
            continue
        entries.append(
            TraceEntry(
                timestamp=ts,
                agent=m.group("agent"),
                entry_type=m.group("type"),
                story=m.group("story") or "",
                content=m.group("content").strip(),
            )
        )
    return entries


# ── Pattern extraction ────────────────────────────────────────────────────────


def _extract_failure_patterns(entries: list[TraceEntry]) -> dict[str, list[str]]:
    """Group FAILURE entries by similarity."""
    failures = [e for e in entries if e.entry_type == "FAILURE"]
    groups: dict[str, list[str]] = {}
    for f in failures:
        # Normalize: lowercase, strip numbers, take first 80 chars
        key = re.sub(r"\d+", "N", f.content.lower())[:80].strip()
        if key not in groups:
            groups[key] = []
        groups[key].append(f.timestamp)
    return groups


def _extract_agent_patterns(entries: list[TraceEntry]) -> dict[str, int]:
    """Count entries per agent to detect imbalance."""
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.agent] = counts.get(e.agent, 0) + 1
    return counts


def _extract_ac_fail_patterns(entries: list[TraceEntry]) -> dict[str, list[str]]:
    """Group AC-FAIL entries by content."""
    ac_fails = [e for e in entries if e.entry_type == "AC-FAIL"]
    groups: dict[str, list[str]] = {}
    for f in ac_fails:
        key = f.content[:80].strip().lower()
        if key not in groups:
            groups[key] = []
        groups[key].append(f.timestamp)
    return groups


def _classify_pattern(count: int) -> str:
    """Classify pattern by occurrence count."""
    if count >= THRESHOLD_CONFIRMED:
        return "confirmed"
    if count >= THRESHOLD_WATCH:
        return "watch"
    return "noise"


def _classify_severity(pattern_desc: str, count: int) -> str:
    """Determine severity based on pattern type and frequency."""
    desc_lower = pattern_desc.lower()
    if any(k in desc_lower for k in ("crash", "error", "fail", "break", "block")):
        return "high" if count >= 3 else "medium"
    if any(k in desc_lower for k in ("warn", "slow", "drift", "stale")):
        return "medium" if count >= 3 else "low"
    return "low"


def extract_patterns(entries: list[TraceEntry]) -> list[Pattern]:
    """Extract all patterns from trace entries."""
    patterns: list[Pattern] = []
    pid = 0

    # Failure patterns
    for desc, timestamps in _extract_failure_patterns(entries).items():
        pid += 1
        count = len(timestamps)
        status = _classify_pattern(count)
        patterns.append(
            Pattern(
                pattern_id=f"PAT-{pid:03d}",
                description=f"Failure: {desc}",
                occurrences=count,
                status=status,
                severity=_classify_severity(desc, count),
                target_type="tool",
                suggested_action=f"Investigate recurring failure ({count}x): {desc[:60]}",
                source_entries=timestamps[:5],
            )
        )

    # AC-FAIL patterns
    for desc, timestamps in _extract_ac_fail_patterns(entries).items():
        pid += 1
        count = len(timestamps)
        status = _classify_pattern(count)
        patterns.append(
            Pattern(
                pattern_id=f"PAT-{pid:03d}",
                description=f"AC-FAIL: {desc}",
                occurrences=count,
                status=status,
                severity="medium" if count >= THRESHOLD_CONFIRMED else "low",
                target_type="workflow",
                suggested_action=f"Review acceptance criteria failure ({count}x): {desc[:60]}",
                source_entries=timestamps[:5],
            )
        )

    # Agent imbalance detection
    agent_counts = _extract_agent_patterns(entries)
    if agent_counts:
        total = sum(agent_counts.values())
        for agent, count in agent_counts.items():
            ratio = count / total if total > 0 else 0
            if ratio > 0.6 and total > 10:
                pid += 1
                patterns.append(
                    Pattern(
                        pattern_id=f"PAT-{pid:03d}",
                        description=f"Agent overload: {agent} ({ratio:.0%} of all entries)",
                        occurrences=count,
                        status="confirmed" if ratio > 0.7 else "watch",
                        severity="medium",
                        target_type="agent",
                        target_hint=agent,
                        suggested_action=f"Rebalance work distribution — {agent} handles "
                        f"{ratio:.0%} of all activity",
                    )
                )

    # Sort by occurrence count desc
    patterns.sort(key=lambda p: -p.occurrences)
    return patterns


# ── Scoring ───────────────────────────────────────────────────────────────────


def compute_score(
    entries: list[TraceEntry],
    patterns: list[Pattern],
    previous_cycles: list[dict],
) -> FlywheelScore:
    """Compute the flywheel health score."""
    total = len(entries)
    failures = sum(1 for e in entries if e.entry_type == "FAILURE")
    ac_fails = sum(1 for e in entries if e.entry_type == "AC-FAIL")
    failure_rate = (failures + ac_fails) / total if total > 0 else 0.0

    confirmed = sum(1 for p in patterns if p.status == "confirmed")
    watch = sum(1 for p in patterns if p.status == "watch")
    high_pending = sum(1 for p in patterns if p.severity == "high" and p.status == "confirmed")

    # Trend calculation
    trend = "stable"
    if previous_cycles:
        prev = previous_cycles[-1]
        prev_rate = prev.get("failure_rate", 0)
        prev_confirmed = prev.get("patterns_confirmed", 0)
        if failure_rate < prev_rate - 0.05 or confirmed < prev_confirmed:
            trend = "improving"
        elif failure_rate > prev_rate + 0.05 or confirmed > prev_confirmed + 2:
            trend = "degrading"

    # Health grade
    if failure_rate == 0 and confirmed == 0:
        grade = "A+"
    elif failure_rate < 0.05 and high_pending == 0:
        grade = "A"
    elif failure_rate < 0.15 and high_pending <= 1:
        grade = "B"
    elif failure_rate < 0.3:
        grade = "C"
    else:
        grade = "D"

    cycle_id = hashlib.sha256(
        datetime.now(UTC).isoformat().encode()
    ).hexdigest()[:8]

    return FlywheelScore(
        cycle_id=f"FW-{cycle_id}",
        timestamp=datetime.now(UTC).isoformat(),
        sessions_analyzed=0,  # would need session tracking
        total_entries=total,
        failure_rate=round(failure_rate, 4),
        patterns_confirmed=confirmed,
        patterns_watch=watch,
        high_severity_pending=high_pending,
        trend=trend,
        health_grade=grade,
    )


# ── Correction generation ────────────────────────────────────────────────────


def generate_corrections(patterns: list[Pattern]) -> list[Correction]:
    """Generate correction proposals from confirmed patterns."""
    corrections: list[Correction] = []
    cid = 0
    for p in patterns:
        if p.status != "confirmed":
            continue
        cid += 1
        status = "pending"
        if p.severity == "high":
            status = "high-escalated"
        corrections.append(
            Correction(
                correction_id=f"COR-{cid:03d}",
                pattern_id=p.pattern_id,
                severity=p.severity,
                target_type=p.target_type,
                target_file=p.target_hint,
                description=p.suggested_action,
                status=status,
            )
        )
    return corrections


# ── Gate system (from GSANE's proven model) ───────────────────────────────────


def apply_gates(corrections: list[Correction], max_corrections: int) -> list[Correction]:
    """Apply safety gates before auto-correction.

    Gate 1: Max N corrections per cycle.
    Gate 2: If ≥2 medium corrections target same file → elevate to high.
    """
    eligible = [c for c in corrections if c.status == "pending"]

    # Gate 2: same-file medium collision
    file_counts: dict[str, list[Correction]] = {}
    for c in eligible:
        if c.severity == "medium" and c.target_file:
            if c.target_file not in file_counts:
                file_counts[c.target_file] = []
            file_counts[c.target_file].append(c)

    for _file, cors in file_counts.items():
        if len(cors) >= 2:
            for c in cors:
                c.status = "high-escalated"
                c.severity = "high"
                eligible.remove(c)

    # Gate 1: max corrections
    if len(eligible) > max_corrections:
        for c in eligible[max_corrections:]:
            c.status = "deferred"
        eligible = eligible[:max_corrections]

    return eligible + [c for c in corrections if c.status in ("high-escalated", "deferred")]


# ── Scoreboard rendering ─────────────────────────────────────────────────────


def render_scoreboard(score: FlywheelScore, patterns: list[Pattern]) -> str:
    """Render a markdown scoreboard."""
    trend_icon = {"improving": "📈", "stable": "➡️", "degrading": "📉"}.get(score.trend, "❓")
    lines = [
        "# BMAD Cognitive Flywheel — Scoreboard",
        "",
        f"> Cycle: {score.cycle_id} | {score.timestamp[:10]}",
        "",
        "## 📊 Health Score",
        "",
        "| Métrique | Valeur |",
        "|---|---|",
        f"| Grade | **{score.health_grade}** |",
        f"| Entries analysées | {score.total_entries} |",
        f"| Taux d'échec | {score.failure_rate:.1%} |",
        f"| Patterns confirmés | {score.patterns_confirmed} |",
        f"| Patterns watch | {score.patterns_watch} |",
        f"| High severity pending | {score.high_severity_pending} |",
        f"| Corrections appliquées | {score.corrections_applied} |",
        f"| Tendance | {trend_icon} {score.trend} |",
        "",
        "## 🔍 Patterns Détectés",
        "",
    ]

    confirmed = [p for p in patterns if p.status == "confirmed"]
    watching = [p for p in patterns if p.status == "watch"]

    if confirmed:
        lines.append("### Confirmés (≥3 occurrences)")
        for p in confirmed:
            sev_icon = {"low": "🟡", "medium": "🟠", "high": "🔴"}.get(p.severity, "⚪")
            lines.append(f"- {sev_icon} **{p.pattern_id}** [{p.severity}] "
                         f"({p.occurrences}x) — {p.description}")
        lines.append("")

    if watching:
        lines.append("### En surveillance (2 occurrences)")
        for p in watching:
            lines.append(f"- 👁️ **{p.pattern_id}** ({p.occurrences}x) — {p.description}")
        lines.append("")

    if not confirmed and not watching:
        lines.append("*Aucun pattern détecté — projet sain.*")
        lines.append("")

    return "\n".join(lines)


# ── Commands ──────────────────────────────────────────────────────────────────


def cmd_analyze(root: Path, args: argparse.Namespace) -> int:
    """Run flywheel analysis cycle."""
    since = args.since if hasattr(args, "since") and args.since else None
    entries = parse_trace(root, since=since)

    if not entries:
        print("⚠️  Aucune entrée BMAD_TRACE trouvée — rien à analyser.")
        return 0

    patterns = extract_patterns(entries)
    history = load_history(root)
    score = compute_score(entries, patterns, history)
    corrections = generate_corrections(patterns)
    corrections = apply_gates(corrections, MAX_AUTO_CORRECTIONS)

    # Update score with correction counts
    score.corrections_applied = sum(1 for c in corrections if c.status == "pending")
    score.corrections_deferred = sum(1 for c in corrections if c.status == "deferred")
    score.high_severity_pending = sum(1 for c in corrections if c.status == "high-escalated")

    report = FlywheelReport(
        cycle_id=score.cycle_id,
        timestamp=score.timestamp,
        score=score,
        patterns=patterns,
        corrections=corrections,
    )
    save_report(root, report)

    # Append to history for trend tracking
    append_history(root, {
        "cycle_id": score.cycle_id,
        "timestamp": score.timestamp,
        "failure_rate": score.failure_rate,
        "patterns_confirmed": score.patterns_confirmed,
        "health_grade": score.health_grade,
        "trend": score.trend,
        "corrections_applied": 0,
    })

    # Render scoreboard
    scoreboard = render_scoreboard(score, patterns)
    _scoreboard_path(root).write_text(scoreboard, encoding="utf-8")

    print(f"🔄 Flywheel cycle {score.cycle_id} terminé")
    print(f"   Entries: {score.total_entries}")
    print(f"   Grade: {score.health_grade} ({score.trend})")
    print(f"   Patterns: {score.patterns_confirmed} confirmés, {score.patterns_watch} watch")
    confirmed_corrections = [c for c in corrections if c.status == "pending"]
    if confirmed_corrections:
        print(f"   Corrections éligibles: {len(confirmed_corrections)}")
    high = [c for c in corrections if c.status == "high-escalated"]
    if high:
        print(f"   ⚠️  High severity (révision manuelle): {len(high)}")

    return 0


def cmd_report(root: Path, _args: argparse.Namespace) -> int:
    """Display the latest flywheel report."""
    report = load_report(root)
    if not report:
        print("Aucun rapport flywheel — lancez 'analyze' d'abord.")
        return 1

    print(render_scoreboard(report.score, report.patterns))

    if report.corrections:
        print("\n## 🔧 Corrections")
        for c in report.corrections:
            icon = {"pending": "⏳", "applied": "✅", "deferred": "⏸️", "high-escalated": "🔴"}.get(
                c.status, "❓"
            )
            print(f"  {icon} {c.correction_id} [{c.severity}] {c.description} → {c.status}")
    return 0


def cmd_apply(root: Path, args: argparse.Namespace) -> int:
    """Apply eligible corrections (low/medium only)."""
    report = load_report(root)
    if not report:
        print("Aucun rapport — lancez 'analyze' d'abord.")
        return 1

    max_corr = args.max if hasattr(args, "max") and args.max else MAX_AUTO_CORRECTIONS
    dry_run = args.dry_run if hasattr(args, "dry_run") else False

    eligible = [c for c in report.corrections if c.status == "pending"][:max_corr]

    if not eligible:
        print("✅ Aucune correction éligible — tout est sain.")
        return 0

    applied_count = 0
    for c in eligible:
        if dry_run:
            print(f"  [DRY-RUN] {c.correction_id}: {c.description}")
        else:
            c.status = "applied"
            c.applied_at = datetime.now(UTC).isoformat()
            applied_count += 1
            print(f"  ✅ {c.correction_id}: {c.description}")

    if not dry_run:
        # Save updated report
        save_report(root, report)

        # Append to history
        append_history(root, {
            "cycle_id": report.cycle_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "corrections_applied": applied_count,
            "failure_rate": report.score.failure_rate,
            "patterns_confirmed": report.score.patterns_confirmed,
            "health_grade": report.score.health_grade,
            "trend": report.score.trend,
        })

        # Update scoreboard
        report.score.corrections_applied = applied_count
        scoreboard = render_scoreboard(report.score, report.patterns)
        _scoreboard_path(root).write_text(scoreboard, encoding="utf-8")

    high = [c for c in report.corrections if c.status == "high-escalated"]
    if high:
        print(f"\n⚠️  {len(high)} correction(s) high-severity nécessitent une révision manuelle:")
        for c in high:
            print(f"  🔴 {c.correction_id}: {c.description}")

    return 0


def cmd_history(root: Path, _args: argparse.Namespace) -> int:
    """Show flywheel history."""
    entries = load_history(root)
    if not entries:
        print("Aucun historique flywheel.")
        return 0

    print("📜 Flywheel History")
    print("=" * 60)
    for e in entries:
        grade = e.get("health_grade", "?")
        trend_icon = {"improving": "📈", "stable": "➡️", "degrading": "📉"}.get(
            e.get("trend", ""), "❓"
        )
        print(
            f"  [{e.get('cycle_id', '?')}] {e.get('timestamp', '?')[:10]} "
            f"| Grade: {grade} {trend_icon} "
            f"| Applied: {e.get('corrections_applied', 0)} "
            f"| Fail rate: {e.get('failure_rate', 0):.1%}"
        )
    return 0


def cmd_score(root: Path, _args: argparse.Namespace) -> int:
    """Show current flywheel health score (one-liner)."""
    report = load_report(root)
    if not report:
        print("Aucun score — lancez 'analyze' d'abord.")
        return 1

    s = report.score
    trend_icon = {"improving": "📈", "stable": "➡️", "degrading": "📉"}.get(s.trend, "?")
    print(
        f"Grade: {s.health_grade} {trend_icon} | "
        f"Fail rate: {s.failure_rate:.1%} | "
        f"Patterns: {s.patterns_confirmed}C/{s.patterns_watch}W | "
        f"Corrections: {s.corrections_applied} applied, {s.high_severity_pending} high"
    )
    return 0


def cmd_dashboard(root: Path, args: argparse.Namespace) -> int:
    """Show full dashboard, optionally as JSON."""
    report = load_report(root)
    if not report:
        print("Aucun rapport — lancez 'analyze' d'abord.")
        return 1

    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(render_scoreboard(report.score, report.patterns))
        if report.corrections:
            print("\n## Corrections")
            for c in report.corrections:
                print(f"  [{c.status}] {c.correction_id} — {c.description}")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cognitive-flywheel",
        description="Cognitive Flywheel — Boucle d'auto-amélioration continue BMAD",
    )
    p.add_argument("--project-root", type=Path, default=Path("."), help="Racine du projet")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    # analyze
    analyze_p = sub.add_parser("analyze", help="Lancer un cycle d'analyse complet")
    analyze_p.add_argument("--since", help="Analyser depuis cette date (YYYY-MM-DD)")

    # report
    sub.add_parser("report", help="Afficher le dernier rapport")

    # apply
    apply_p = sub.add_parser("apply", help="Appliquer les corrections éligibles")
    apply_p.add_argument("--max", type=int, default=MAX_AUTO_CORRECTIONS)
    apply_p.add_argument("--dry-run", action="store_true", help="Simuler sans appliquer")

    # history
    sub.add_parser("history", help="Afficher l'historique des cycles")

    # score
    sub.add_parser("score", help="Score de santé actuel (one-liner)")

    # dashboard
    dash_p = sub.add_parser("dashboard", help="Dashboard complet")
    dash_p.add_argument("--json", action="store_true", help="Sortie JSON")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    commands = {
        "analyze": cmd_analyze,
        "report": cmd_report,
        "apply": cmd_apply,
        "history": cmd_history,
        "score": cmd_score,
        "dashboard": cmd_dashboard,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(root, args)


if __name__ == "__main__":
    sys.exit(main())
