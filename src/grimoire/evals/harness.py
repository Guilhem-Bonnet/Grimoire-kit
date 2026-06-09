"""EvalHarness — run EvalCases, collect EvalReports, diff against baselines."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.evals.schemas import EvalCase, EvalOutcome, EvalReport, EvalResult

__all__ = ["EvalHarness"]


class EvalHarness:
    """Registry and runner for evaluation cases.

    Usage::

        harness = EvalHarness(suite_id="policy-regression-v1")
        harness.register(my_case)
        report = harness.run()
        report.to_jsonl(Path("evals/results/latest.jsonl"))
    """

    def __init__(self, suite_id: str = "default") -> None:
        self._suite_id = suite_id
        self._cases: dict[str, EvalCase] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, case: EvalCase) -> None:
        self._cases[case.case_id] = case

    def register_many(self, cases: list[EvalCase]) -> None:
        for case in cases:
            self.register(case)

    # ── Execution ─────────────────────────────────────────────────────────────

    def run(
        self,
        tags: tuple[str, ...] | None = None,
        case_ids: tuple[str, ...] | None = None,
    ) -> EvalReport:
        """Run all (or filtered) cases and return an EvalReport."""
        candidates = list(self._cases.values())
        if tags:
            candidates = [c for c in candidates if any(t in c.tags for t in tags)]
        if case_ids:
            candidates = [c for c in candidates if c.case_id in case_ids]

        results = tuple(c.run() for c in candidates)
        return EvalReport(
            results=results,
            generated_at=datetime.now(tz=UTC).isoformat(),
            suite_id=self._suite_id,
        )

    def run_case(self, case_id: str) -> EvalResult:
        case = self._cases.get(case_id)
        if case is None:
            return EvalResult(
                case_id=case_id,
                outcome=EvalOutcome.ERROR,
                error=f"Case '{case_id}' not registered in harness '{self._suite_id}'",
            )
        return case.run()

    # ── Baseline diff ─────────────────────────────────────────────────────────

    def diff(self, report: EvalReport, baseline_path: Path) -> dict[str, Any]:
        """Compare report against a JSONL baseline, return regressions + improvements."""
        if not baseline_path.exists():
            return {"error": f"Baseline not found: {baseline_path}", "regressions": [], "improvements": []}

        baseline: dict[str, str] = {}
        with baseline_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    baseline[rec["case_id"]] = rec["outcome"]
                except (json.JSONDecodeError, KeyError):
                    continue

        regressions: list[dict[str, str]] = []
        improvements: list[dict[str, str]] = []

        for result in report.results:
            prev = baseline.get(result.case_id)
            if prev is None:
                continue
            if prev == EvalOutcome.PASS and result.outcome != EvalOutcome.PASS:
                regressions.append({
                    "case_id": result.case_id,
                    "was": prev,
                    "now": result.outcome.value,
                    "error": result.error,
                })
            elif prev != EvalOutcome.PASS and result.outcome == EvalOutcome.PASS:
                improvements.append({"case_id": result.case_id, "was": prev, "now": result.outcome.value})

        return {
            "suite_id": self._suite_id,
            "regressions": regressions,
            "improvements": improvements,
            "regression_count": len(regressions),
            "improvement_count": len(improvements),
        }

    # ── Introspection ─────────────────────────────────────────────────────────

    def list_cases(self, tags: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        cases = list(self._cases.values())
        if tags:
            cases = [c for c in cases if any(t in c.tags for t in tags)]
        return [{"case_id": c.case_id, "name": c.name, "tags": list(c.tags), "description": c.description} for c in cases]

    def __len__(self) -> int:
        return len(self._cases)
