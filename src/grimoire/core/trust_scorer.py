"""Sub-agent trust scoring based on historical telemetry and evaluations.

Backs the SOG Trust Scoring protocol declared in orchestrator-gateway.md.
Aggregates evaluation grades and telemetry outcomes to produce a
per-agent trust level that the SOG uses to decide whether to
cross-validate (CVTL) a sub-agent's output.

Usage::

    from grimoire.core.trust_scorer import TrustScorer

    scorer = TrustScorer(project_root=Path("."))
    trust = scorer.score("dev")
    if trust.level == "untrusted":
        # trigger CVTL cross-validation
        ...
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["TrustScore", "TrustScorer"]

TRUST_SCORER_VERSION = "1.0.0"

_EVAL_DIR = "_grimoire/_memory/telemetry"
_EVAL_FILE = "evaluations.jsonl"
_SKILL_USAGE_FILE = "skill-usage.jsonl"
_DEFAULT_MIN_EVENTS = 3


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TrustScore:
    """Trust assessment for a single agent."""

    agent: str
    score: float  # 0.0–1.0
    level: str  # trusted, cautious, untrusted
    eval_count: int
    success_rate: float
    avg_grade_score: float
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "score": round(self.score, 3),
            "level": self.level,
            "eval_count": self.eval_count,
            "success_rate": round(self.success_rate, 3),
            "avg_grade_score": round(self.avg_grade_score, 3),
            "evidence": list(self.evidence),
        }


# ── Score boundaries ─────────────────────────────────────────────────────────

_LEVEL_THRESHOLDS = [
    (0.75, "trusted"),
    (0.5, "cautious"),
    (0.0, "untrusted"),
]

_GRADE_VALUE = {"A": 1.0, "B": 0.8, "C": 0.65, "D": 0.5, "F": 0.2}


def _level_for(score: float) -> str:
    for threshold, level in _LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "untrusted"


# ── Core implementation ──────────────────────────────────────────────────────


class TrustScorer:
    """Compute trust scores from evaluation and telemetry history.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    min_events :
        Minimum data points required before scoring.  Below this,
        the agent gets a default ``cautious`` rating.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        min_events: int = _DEFAULT_MIN_EVENTS,
    ) -> None:
        self._root = project_root.resolve()
        self._min_events = min_events
        self._eval_path = self._root / _EVAL_DIR / _EVAL_FILE
        self._telem_path = self._root / _EVAL_DIR / _SKILL_USAGE_FILE

    def score(self, agent: str) -> TrustScore:
        """Compute trust score for *agent*."""
        evals = self._load_evaluations(agent)
        telem = self._load_telemetry(agent)
        evidence: list[str] = []

        total_events = len(evals) + len(telem)
        if total_events < self._min_events:
            evidence.append(f"Insufficient data ({total_events}/{self._min_events})")
            return TrustScore(
                agent=agent,
                score=0.5,
                level="cautious",
                eval_count=len(evals),
                success_rate=0.0,
                avg_grade_score=0.0,
                evidence=tuple(evidence),
            )

        # Evaluation-based scoring (60% weight)
        eval_score = 0.0
        avg_grade = 0.0
        if evals:
            grade_values = [_GRADE_VALUE.get(e.get("grade", "F"), 0.2) for e in evals]
            avg_grade = sum(grade_values) / len(grade_values)
            eval_score = avg_grade
            evidence.append(f"Eval avg grade: {avg_grade:.2f} ({len(evals)} evals)")

        # Telemetry-based scoring (40% weight)
        success_rate = 0.0
        telem_score = 0.5  # neutral default
        if telem:
            successes = sum(1 for t in telem if t.get("outcome") == "success")
            success_rate = successes / len(telem) if telem else 0
            telem_score = success_rate
            evidence.append(f"Telemetry success rate: {success_rate:.0%} ({len(telem)} events)")

        # Weighted blend
        if evals and telem:
            combined = eval_score * 0.6 + telem_score * 0.4
        elif evals:
            combined = eval_score
        else:
            combined = telem_score

        level = _level_for(combined)
        evidence.append(f"Level: {level}")

        return TrustScore(
            agent=agent,
            score=combined,
            level=level,
            eval_count=len(evals),
            success_rate=success_rate,
            avg_grade_score=avg_grade,
            evidence=tuple(evidence),
        )

    def scoreboard(self) -> dict[str, TrustScore]:
        """Compute trust scores for all known agents."""
        agents: set[str] = set()
        agents.update(self._collect_agents_from(self._eval_path, key="agent"))
        agents.update(self._collect_agents_from(self._telem_path, key="skill"))
        return {a: self.score(a) for a in sorted(agents)}

    # ── Internal ──────────────────────────────────────────────────────────

    def _load_evaluations(self, agent: str) -> list[dict[str, Any]]:
        return [
            e
            for e in self._load_jsonl(self._eval_path)
            if e.get("agent") == agent
        ]

    def _load_telemetry(self, agent: str) -> list[dict[str, Any]]:
        return [
            e
            for e in self._load_jsonl(self._telem_path)
            if e.get("skill") == agent or e.get("tool") == agent
        ]

    @staticmethod
    def _load_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            entries: list[dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    entries.append(json.loads(line))
            return entries
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _collect_agents_from(path: Path, *, key: str) -> set[str]:
        if not path.is_file():
            return set()
        agents: set[str] = set()
        try:
            for line in path.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    entry = json.loads(line)
                    val = entry.get(key, "")
                    if val:
                        agents.add(val)
        except (json.JSONDecodeError, OSError):
            pass
        return agents
