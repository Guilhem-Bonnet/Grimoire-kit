"""Eval harness schemas — EvalCase, EvalResult, EvalReport."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "EvalCase",
    "EvalCategory",
    "EvalOutcome",
    "EvalReport",
    "EvalResult",
    "EvalScore",
    "PassHatK",
    "pass_hat_k",
]


class EvalCategory(StrEnum):
    """capability (mesure une compétence, taux bas attendu) vs regression
    (sourcée d'un échec réel passé, taux proche de 100 % attendu) — voir
    Anthropic `[blog]` cité dans `framework/agentic-industry-reference.md`
    §6.1. Default is ``capability``: a task earns ``regression`` only once
    it is actually sourced from a real past failure — inventing the label
    would misrepresent what the task measures.
    """

    CAPABILITY = "capability"
    REGRESSION = "regression"


class EvalOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - evaluation outcome, not a password.
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EvalScore:
    value: float  # 0.0–1.0
    label: str = ""
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label, "explanation": self.explanation}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalScore:
        return cls(
            value=float(d.get("value", 0.0)),
            label=str(d.get("label", "")),
            explanation=str(d.get("explanation", "")),
        )


@dataclass(frozen=True, slots=True)
class EvalResult:
    case_id: str
    outcome: EvalOutcome
    details: str = ""
    error: str = ""
    latency_ms: float = 0.0
    score: EvalScore | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "outcome": self.outcome.value,
            "details": self.details,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "score": self.score.to_dict() if self.score else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalResult:
        score = EvalScore.from_dict(d["score"]) if d.get("score") else None
        return cls(
            case_id=d["case_id"],
            outcome=EvalOutcome(d["outcome"]),
            details=str(d.get("details", "")),
            error=str(d.get("error", "")),
            latency_ms=float(d.get("latency_ms", 0.0)),
            score=score,
        )


@dataclass(frozen=True, slots=True)
class EvalReport:
    results: tuple[EvalResult, ...]
    generated_at: str
    suite_id: str = ""

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == EvalOutcome.PASS)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == EvalOutcome.FAIL)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == EvalOutcome.ERROR)

    @property
    def skip_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == EvalOutcome.SKIP)

    @property
    def pass_rate(self) -> float:
        total = len(self.results) - self.skip_count
        if total == 0:
            return 1.0
        return self.pass_count / total

    @property
    def mean_score(self) -> float | None:
        scores = [r.score.value for r in self.results if r.score is not None]
        return sum(scores) / len(scores) if scores else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "generated_at": self.generated_at,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "error_count": self.error_count,
            "skip_count": self.skip_count,
            "pass_rate": round(self.pass_rate, 4),
            "mean_score": round(self.mean_score, 4) if self.mean_score is not None else None,
            "results": [r.to_dict() for r in self.results],
        }

    def to_jsonl(self, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with dest.open("w", encoding="utf-8") as f:
            for r in self.results:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
                count += 1
        return count


@dataclass
class EvalCase:
    case_id: str
    name: str
    fn: Callable[[], EvalResult]
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    category: EvalCategory = EvalCategory.CAPABILITY

    def run(self) -> EvalResult:
        t0 = time.perf_counter()
        try:
            result = self.fn()
            if result.latency_ms == 0.0:
                latency = (time.perf_counter() - t0) * 1000
                result = EvalResult(
                    case_id=result.case_id,
                    outcome=result.outcome,
                    details=result.details,
                    error=result.error,
                    latency_ms=latency,
                    score=result.score,
                )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            result = EvalResult(
                case_id=self.case_id,
                outcome=EvalOutcome.ERROR,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=latency,
            )
        return result


@dataclass(frozen=True, slots=True)
class PassHatK:
    """pass^k over a task suite's repeated runs (τ-bench, τ²-bench convention:
    consistency across repetitions, not just best-of-k like pass@k).
    """

    k: int
    tasks_evaluated: int
    tasks_passed: int
    #: Task ids with fewer than ``k`` executed repetitions — excluded from
    #: both numerator and denominator, reported so a thin sample is visible
    #: rather than silently folded into a rate.
    tasks_insufficient: tuple[str, ...]
    #: Per-task verdict: True (passed all k), False (failed at least one),
    #: None (insufficient data — see `tasks_insufficient`).
    per_task: dict[str, bool | None]

    @property
    def value(self) -> float | None:
        return round(self.tasks_passed / self.tasks_evaluated, 4) if self.tasks_evaluated else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "tasks_evaluated": self.tasks_evaluated,
            "tasks_passed": self.tasks_passed,
            "tasks_insufficient": list(self.tasks_insufficient),
            "pass_hat_k": self.value,
            "per_task": dict(self.per_task),
        }


def pass_hat_k(results_by_task: dict[str, list[bool | None]], k: int) -> PassHatK:
    """pass^k: the fraction of tasks that succeeded on every one of their
    executed repetitions, among tasks that reached ``k`` executed reps.

    ``results_by_task`` maps a task id to one entry per repetition that was
    at least attempted: ``True``/``False`` once judged, or ``None`` when the
    repetition ran but has no verdict yet (unjudged). A repetition that
    never ran at all (budget stop, infra incident) simply has no entry —
    absence, not ``None``. Either way, "un run non exécuté n'est ni succès
    ni échec" (audit B13): it contributes to neither the pass count nor the
    fail count, and a task short of ``k`` *executed and judged* reps cannot
    be scored at all — it lands in ``tasks_insufficient`` instead of being
    silently counted as a pass or a fail.

    This is τ-bench/τ²-bench's pass^k (consistency across repeated runs of
    the *same* task), not pass@k (best-of-k, a different metric the audit
    explicitly distinguishes it from).
    """
    if k < 1:
        msg = f"k doit être ≥ 1, reçu {k}"
        raise ValueError(msg)
    passed = 0
    evaluated = 0
    insufficient: list[str] = []
    per_task: dict[str, bool | None] = {}
    for task_id, outcomes in results_by_task.items():
        judged = [o for o in outcomes if o is not None]
        if len(judged) < k:
            insufficient.append(task_id)
            per_task[task_id] = None
            continue
        evaluated += 1
        ok = all(judged)
        per_task[task_id] = ok
        if ok:
            passed += 1
    return PassHatK(
        k=k,
        tasks_evaluated=evaluated,
        tasks_passed=passed,
        tasks_insufficient=tuple(sorted(insufficient)),
        per_task=per_task,
    )
