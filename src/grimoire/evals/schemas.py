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
    "EvalOutcome",
    "EvalReport",
    "EvalResult",
    "EvalScore",
]


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
