"""Workflow analyzer — telemetry-driven optimization insights.

Reads telemetry JSONL data to identify patterns, bottlenecks, and
optimization opportunities across skill and tool usage.

Usage::

    from grimoire.core.workflow_analyzer import WorkflowAnalyzer

    wa = WorkflowAnalyzer(project_root=Path("."))
    report = wa.analyze()
    print(report.to_markdown())
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AnalysisReport", "Recommendation", "SkillMetrics", "WorkflowAnalyzer"]

WORKFLOW_ANALYZER_VERSION = "1.0.0"

_TELEMETRY_FILE = "_grimoire/_memory/telemetry/skill-usage.jsonl"


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkillMetrics:
    """Usage metrics for a single skill."""

    skill: str
    invocations: int
    successes: int
    failures: int
    avg_duration_s: float
    last_used: str

    @property
    def success_rate(self) -> float:
        return self.successes / self.invocations if self.invocations else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "invocations": self.invocations,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 3),
            "avg_duration_s": round(self.avg_duration_s, 2),
            "last_used": self.last_used,
        }


@dataclass(frozen=True, slots=True)
class Recommendation:
    """An actionable optimization recommendation."""

    category: str  # bottleneck, underuse, failure_pattern, efficiency
    severity: str  # high, medium, low
    message: str
    skill: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "skill": self.skill,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Complete workflow analysis report."""

    total_events: int
    unique_skills: int
    unique_tools: int
    skill_metrics: tuple[SkillMetrics, ...]
    recommendations: tuple[Recommendation, ...]
    top_failures: tuple[tuple[str, int], ...]  # (tool/skill, count)
    timestamp: str

    def to_markdown(self) -> str:
        lines = [
            f"# Workflow Analysis — {self.timestamp}",
            "",
            f"**Events**: {self.total_events} | "
            f"**Skills**: {self.unique_skills} | "
            f"**Tools**: {self.unique_tools}",
            "",
        ]

        if self.skill_metrics:
            lines.append("## Skill Usage")
            lines.append("")
            lines.append("| Skill | Invocations | Success Rate | Avg Duration |")
            lines.append("|---|---|---|---|")
            for m in self.skill_metrics:
                lines.append(
                    f"| {m.skill} | {m.invocations} | "
                    f"{m.success_rate:.0%} | {m.avg_duration_s:.1f}s |"
                )
            lines.append("")

        if self.top_failures:
            lines.append("## Top Failures")
            lines.append("")
            for name, count in self.top_failures:
                lines.append(f"- **{name}**: {count} failures")
            lines.append("")

        if self.recommendations:
            lines.append("## Recommendations")
            lines.append("")
            for rec in self.recommendations:
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rec.severity, "⚪")
                lines.append(f"- {icon} **[{rec.category}]** {rec.message}")
                if rec.evidence:
                    lines.append(f"  _{rec.evidence}_")
            lines.append("")

        return "\n".join(lines)


# ── Core implementation ──────────────────────────────────────────────────────


class WorkflowAnalyzer:
    """Analyzes telemetry data for optimization insights.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._telemetry_file = self._root / _TELEMETRY_FILE

    def analyze(self) -> AnalysisReport:
        """Run full workflow analysis."""
        entries = self._load_entries()
        skill_metrics = self._compute_skill_metrics(entries)
        top_failures = self._find_top_failures(entries)
        recommendations = self._generate_recommendations(entries, skill_metrics, top_failures)

        unique_skills = {e.get("skill") for e in entries if e.get("skill")}
        unique_tools = {e.get("tool") for e in entries if e.get("tool")}

        return AnalysisReport(
            total_events=len(entries),
            unique_skills=len(unique_skills),
            unique_tools=len(unique_tools),
            skill_metrics=tuple(skill_metrics),
            recommendations=tuple(recommendations),
            top_failures=tuple(top_failures),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def _load_entries(self) -> list[dict[str, Any]]:
        if not self._telemetry_file.is_file():
            return []
        entries: list[dict[str, Any]] = []
        try:
            for line in self._telemetry_file.read_text(encoding="utf-8").strip().splitlines():
                entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            logger.debug("Failed to load telemetry")
        return entries

    def _compute_skill_metrics(self, entries: list[dict[str, Any]]) -> list[SkillMetrics]:
        skill_data: dict[str, dict[str, Any]] = {}

        for e in entries:
            skill = e.get("skill", "")
            if not skill or e.get("event_type") != "skill":
                continue
            if skill not in skill_data:
                skill_data[skill] = {
                    "invocations": 0,
                    "successes": 0,
                    "failures": 0,
                    "durations": [],
                    "last_used": "",
                }
            data = skill_data[skill]
            data["invocations"] += 1
            outcome = e.get("outcome", "")
            if outcome == "success":
                data["successes"] += 1
            elif outcome == "failure":
                data["failures"] += 1
            dur = e.get("duration_s", 0.0)
            if dur > 0:
                data["durations"].append(dur)
            ts = e.get("timestamp", "")
            if ts > data["last_used"]:
                data["last_used"] = ts

        metrics: list[SkillMetrics] = []
        for skill, data in sorted(skill_data.items(), key=lambda x: x[1]["invocations"], reverse=True):
            durations = data["durations"]
            avg_dur = sum(durations) / len(durations) if durations else 0.0
            metrics.append(SkillMetrics(
                skill=skill,
                invocations=data["invocations"],
                successes=data["successes"],
                failures=data["failures"],
                avg_duration_s=avg_dur,
                last_used=data["last_used"],
            ))
        return metrics

    def _find_top_failures(self, entries: list[dict[str, Any]], *, top_n: int = 5) -> list[tuple[str, int]]:
        failures: Counter[str] = Counter()
        for e in entries:
            if e.get("outcome") == "failure":
                name = e.get("skill") or e.get("tool") or "unknown"
                failures[name] += 1
        return failures.most_common(top_n)

    def _generate_recommendations(
        self,
        entries: list[dict[str, Any]],
        metrics: list[SkillMetrics],
        top_failures: list[tuple[str, int]],
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []

        # Bottleneck: skills with high avg duration
        for m in metrics:
            if m.avg_duration_s > 30.0 and m.invocations >= 3:
                recs.append(Recommendation(
                    category="bottleneck",
                    severity="high",
                    message=f"Skill '{m.skill}' averages {m.avg_duration_s:.1f}s — consider optimization",
                    skill=m.skill,
                    evidence=f"{m.invocations} invocations, avg {m.avg_duration_s:.1f}s",
                ))

        # Failure patterns: skills with >30% failure rate
        for m in metrics:
            if m.invocations >= 3 and m.success_rate < 0.7:
                recs.append(Recommendation(
                    category="failure_pattern",
                    severity="high",
                    message=f"Skill '{m.skill}' has {m.success_rate:.0%} success rate — investigate root cause",
                    skill=m.skill,
                    evidence=f"{m.failures}/{m.invocations} failures",
                ))

        # Underuse: skills invoked only once
        single_use = [m for m in metrics if m.invocations == 1]
        if len(single_use) > 3:
            names = ", ".join(m.skill for m in single_use[:5])
            recs.append(Recommendation(
                category="underuse",
                severity="low",
                message=f"{len(single_use)} skills used only once — consider promoting or removing",
                evidence=f"Examples: {names}",
            ))

        # Repeated tool failures
        for name, count in top_failures:
            if count >= 3:
                recs.append(Recommendation(
                    category="failure_pattern",
                    severity="medium",
                    message=f"'{name}' failed {count} times — may need a learning entry",
                    skill=name,
                    evidence=f"{count} recorded failures",
                ))

        return recs
