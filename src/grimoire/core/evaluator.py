"""Multi-dimensional agent output evaluator.

Inspired by Anthropic plugins' validation patterns and gstack's
quality pipeline.  Evaluates sub-agent outputs across multiple
quality dimensions and records scores for trend analysis.

Usage::

    from grimoire.core.evaluator import Evaluator, EvalCriteria

    ev = Evaluator(project_root=Path("."))
    result = ev.evaluate(
        agent="dev",
        output="def login(): ...",
        criteria=EvalCriteria(check_tests=True),
        task="implement login endpoint",
    )
    print(result.score, result.grade)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DimensionScore", "EvalCriteria", "EvalResult", "Evaluator"]

EVALUATOR_VERSION = "1.0.0"

_EVAL_DIR = "_grimoire/_memory/telemetry"
_EVAL_FILE = "evaluations.jsonl"
_MAX_ENTRIES = 500


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DimensionScore:
    """Score for a single quality dimension."""

    dimension: str  # correctness, completeness, safety, style, relevance
    score: float  # 0.0–1.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "score": round(self.score, 3), "reason": self.reason}


@dataclass(frozen=True, slots=True)
class EvalCriteria:
    """Controls which checks are applied."""

    check_completeness: bool = True
    check_safety: bool = True
    check_style: bool = True
    check_relevance: bool = True
    check_tests: bool = False
    min_output_length: int = 10
    max_output_length: int = 50000


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Complete evaluation result."""

    agent: str
    task: str
    dimensions: tuple[DimensionScore, ...]
    score: float  # aggregate 0.0–1.0
    grade: str  # A, B, C, D, F
    passed: bool
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task": self.task,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "score": round(self.score, 3),
            "grade": self.grade,
            "passed": self.passed,
            "timestamp": self.timestamp,
        }


# ── Grading ──────────────────────────────────────────────────────────────────

_GRADE_THRESHOLDS = [
    (0.9, "A"),
    (0.8, "B"),
    (0.7, "C"),
    (0.6, "D"),
    (0.0, "F"),
]


def _grade(score: float) -> str:
    for threshold, letter in _GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


# ── Safety patterns ──────────────────────────────────────────────────────────

_UNSAFE_PATTERNS = (
    r"rm\s+-rf\s+/",
    r"DROP\s+TABLE",
    r"eval\s*\(",
    r"exec\s*\(",
    r"__import__\s*\(",
    r"subprocess\.call\([^)]*shell\s*=\s*True",
    r"os\.system\s*\(",
    r"password\s*=\s*['\"][^'\"]+['\"]",
    r"secret\s*=\s*['\"][^'\"]+['\"]",
    r"api_key\s*=\s*['\"][^'\"]+['\"]",
)


# ── Core implementation ──────────────────────────────────────────────────────


class Evaluator:
    """Multi-dimensional evaluator for agent outputs.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._eval_file = self._root / _EVAL_DIR / _EVAL_FILE

    def evaluate(
        self,
        agent: str,
        output: str,
        task: str = "",
        *,
        criteria: EvalCriteria | None = None,
    ) -> EvalResult:
        """Evaluate an agent output across quality dimensions.

        Parameters
        ----------
        agent :
            Agent identifier.
        output :
            The text output to evaluate.
        task :
            Task description (for relevance scoring).
        criteria :
            Evaluation criteria (defaults to standard).
        """
        crit = criteria or EvalCriteria()
        dims: list[DimensionScore] = []

        # Completeness
        if crit.check_completeness:
            dims.append(self._check_completeness(output, crit))

        # Safety
        if crit.check_safety:
            dims.append(self._check_safety(output))

        # Style
        if crit.check_style:
            dims.append(self._check_style(output))

        # Relevance
        if crit.check_relevance and task:
            dims.append(self._check_relevance(output, task))

        # Tests presence
        if crit.check_tests:
            dims.append(self._check_tests(output))

        # Aggregate
        avg = sum(d.score for d in dims) / len(dims) if dims else 0.0

        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result = EvalResult(
            agent=agent,
            task=task,
            dimensions=tuple(dims),
            score=avg,
            grade=_grade(avg),
            passed=avg >= 0.6,
            timestamp=ts,
        )

        self._record(result)
        self._bridge_to_telemetry(result)
        return result

    def recent(self, *, agent: str = "", limit: int = 10) -> list[dict[str, Any]]:
        """Load recent evaluations from the JSONL log."""
        if not self._eval_file.is_file():
            return []
        entries: list[dict[str, Any]] = []
        try:
            for line in self._eval_file.read_text(encoding="utf-8").strip().splitlines():
                entry = json.loads(line)
                if agent and entry.get("agent") != agent:
                    continue
                entries.append(entry)
        except (OSError, json.JSONDecodeError):
            return []
        return entries[-limit:]

    def agent_scores(self) -> dict[str, dict[str, Any]]:
        """Aggregate scores by agent."""
        entries = self.recent(limit=_MAX_ENTRIES)
        agents: dict[str, list[float]] = {}
        for e in entries:
            a = e.get("agent", "unknown")
            agents.setdefault(a, []).append(e.get("score", 0.0))
        return {
            a: {
                "count": len(scores),
                "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
                "grade": _grade(sum(scores) / len(scores)) if scores else "F",
            }
            for a, scores in agents.items()
        }

    # ── Dimension checkers ───────────────────────────────────────────────────

    @staticmethod
    def _check_completeness(output: str, criteria: EvalCriteria) -> DimensionScore:
        length = len(output)
        if length < criteria.min_output_length:
            return DimensionScore("completeness", 0.2, "Output too short")
        if length > criteria.max_output_length:
            return DimensionScore("completeness", 0.6, "Output excessively long")
        # Check for common completeness markers
        score = 0.7
        if any(m in output.lower() for m in ("todo", "fixme", "xxx", "placeholder")):
            score -= 0.2
            return DimensionScore("completeness", max(0.0, score), "Contains TODO/placeholder markers")
        return DimensionScore("completeness", min(1.0, score + 0.3), "Complete")

    @staticmethod
    def _check_safety(output: str) -> DimensionScore:
        for pattern in _UNSAFE_PATTERNS:
            if re.search(pattern, output, re.IGNORECASE):
                return DimensionScore("safety", 0.2, f"Unsafe pattern detected: {pattern[:30]}")
        return DimensionScore("safety", 1.0, "No unsafe patterns")

    @staticmethod
    def _check_style(output: str) -> DimensionScore:
        score = 1.0
        reasons: list[str] = []
        lines = output.split("\n")
        # Check for overly long lines
        long_lines = sum(1 for ln in lines if len(ln) > 120)
        if long_lines > len(lines) * 0.3:
            score -= 0.2
            reasons.append("Many lines >120 chars")
        # Check for consistent indentation
        indent_styles = set()
        for ln in lines:
            stripped = ln.lstrip()
            if stripped and ln != stripped:
                indent = ln[: len(ln) - len(stripped)]
                if "\t" in indent:
                    indent_styles.add("tab")
                else:
                    indent_styles.add("space")
        if len(indent_styles) > 1:
            score -= 0.3
            reasons.append("Mixed indentation")
        reason = "; ".join(reasons) if reasons else "Style OK"
        return DimensionScore("style", max(0.0, score), reason)

    @staticmethod
    def _check_relevance(output: str, task: str) -> DimensionScore:
        # Use [a-z0-9] instead of \w to split on underscores (e.g. sort_array → sort, array)
        task_words = set(re.findall(r"[a-z0-9]{3,}", task.lower()))
        output_words = set(re.findall(r"[a-z0-9]{3,}", output.lower()))
        if not task_words:
            return DimensionScore("relevance", 0.5, "No task keywords to match")
        overlap = len(task_words & output_words) / len(task_words)
        if overlap >= 0.5:
            return DimensionScore("relevance", min(1.0, 0.6 + overlap * 0.4), "Good keyword overlap")
        return DimensionScore("relevance", max(0.2, overlap), "Low keyword overlap with task")

    @staticmethod
    def _check_tests(output: str) -> DimensionScore:
        test_indicators = ("def test_", "class Test", "pytest", "unittest", "assert ")
        found = sum(1 for ind in test_indicators if ind in output)
        if found >= 3:
            return DimensionScore("tests", 1.0, "Tests present")
        if found >= 1:
            return DimensionScore("tests", 0.6, "Some test indicators")
        return DimensionScore("tests", 0.2, "No tests found")

    # ── Persistence ──────────────────────────────────────────────────────────

    def _record(self, result: EvalResult) -> None:
        """Append evaluation to JSONL log."""
        try:
            self._eval_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._eval_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

            # Prune
            lines = self._eval_file.read_text(encoding="utf-8").strip().splitlines()
            if len(lines) > _MAX_ENTRIES:
                kept = lines[-_MAX_ENTRIES:]
                self._eval_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except OSError:
            logger.debug("Failed to record evaluation")

    def _bridge_to_telemetry(self, result: EvalResult) -> None:
        """Also record the evaluation result in the Telemetry JSONL.

        This makes evaluations visible to WorkflowAnalyzer and TrustScorer
        via the unified telemetry pipeline.
        """
        try:
            from grimoire.core.telemetry import Telemetry

            telem = Telemetry(self._root)
            telem.record_tool(
                tool="evaluator",
                outcome="success" if result.passed else "failure",
                message=f"agent={result.agent} grade={result.grade} score={result.score:.2f}",
                skill=result.agent,
            )
        except Exception:
            logger.debug("Telemetry bridge failed for evaluation")
