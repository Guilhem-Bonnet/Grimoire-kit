"""Tests for grimoire/evals/ — EvalHarness, schemas, and pre-built fixtures."""

from __future__ import annotations

import json

from grimoire.evals import EvalCase, EvalHarness, EvalOutcome, EvalReport, EvalResult, EvalScore
from grimoire.evals.fixtures import make_intake_suite, make_mission_lifecycle_suite, make_policy_suite

# ── Schema roundtrip ──────────────────────────────────────────────────────────

class TestEvalSchemas:
    def test_eval_score_roundtrip(self) -> None:
        s = EvalScore(value=0.85, label="good", explanation="meets criteria")
        assert EvalScore.from_dict(s.to_dict()) == s

    def test_eval_result_roundtrip(self) -> None:
        r = EvalResult(
            case_id="test.case",
            outcome=EvalOutcome.PASS,
            details="all checks passed",
            latency_ms=12.5,
            score=EvalScore(value=1.0, label="perfect"),
        )
        assert EvalResult.from_dict(r.to_dict()) == r

    def test_eval_result_roundtrip_no_score(self) -> None:
        r = EvalResult(case_id="test.fail", outcome=EvalOutcome.FAIL, error="assertion failed")
        assert EvalResult.from_dict(r.to_dict()) == r

    def test_eval_report_counts(self) -> None:
        results = (
            EvalResult(case_id="a", outcome=EvalOutcome.PASS),
            EvalResult(case_id="b", outcome=EvalOutcome.FAIL),
            EvalResult(case_id="c", outcome=EvalOutcome.ERROR),
            EvalResult(case_id="d", outcome=EvalOutcome.SKIP),
        )
        report = EvalReport(results=results, generated_at="2026-01-01T00:00:00+00:00", suite_id="test")
        assert report.pass_count == 1
        assert report.fail_count == 1
        assert report.error_count == 1
        assert report.skip_count == 1

    def test_eval_report_pass_rate(self) -> None:
        results = (
            EvalResult(case_id="a", outcome=EvalOutcome.PASS),
            EvalResult(case_id="b", outcome=EvalOutcome.PASS),
            EvalResult(case_id="c", outcome=EvalOutcome.FAIL),
            EvalResult(case_id="d", outcome=EvalOutcome.SKIP),
        )
        report = EvalReport(results=results, generated_at="2026-01-01T00:00:00+00:00")
        # SKIP excluded from denominator → 2 pass / 3 total = 0.666
        assert abs(report.pass_rate - 2 / 3) < 1e-6

    def test_eval_report_pass_rate_all_skip(self) -> None:
        results = (EvalResult(case_id="a", outcome=EvalOutcome.SKIP),)
        report = EvalReport(results=results, generated_at="2026-01-01T00:00:00+00:00")
        assert report.pass_rate == 1.0

    def test_eval_report_mean_score(self) -> None:
        results = (
            EvalResult(case_id="a", outcome=EvalOutcome.PASS, score=EvalScore(value=0.8)),
            EvalResult(case_id="b", outcome=EvalOutcome.PASS, score=EvalScore(value=1.0)),
            EvalResult(case_id="c", outcome=EvalOutcome.FAIL),
        )
        report = EvalReport(results=results, generated_at="2026-01-01T00:00:00+00:00")
        assert report.mean_score is not None
        assert abs(report.mean_score - 0.9) < 1e-6

    def test_eval_report_mean_score_none_when_no_scores(self) -> None:
        report = EvalReport(results=(EvalResult(case_id="a", outcome=EvalOutcome.FAIL),), generated_at="2026-01-01")
        assert report.mean_score is None

    def test_eval_report_to_dict(self) -> None:
        report = EvalReport(
            results=(EvalResult(case_id="a", outcome=EvalOutcome.PASS),),
            generated_at="2026-01-01T00:00:00+00:00",
            suite_id="suite-01",
        )
        d = report.to_dict()
        assert d["pass_count"] == 1
        assert d["suite_id"] == "suite-01"
        assert "results" in d

    def test_eval_report_to_jsonl(self, tmp_path) -> None:
        results = (
            EvalResult(case_id="a", outcome=EvalOutcome.PASS),
            EvalResult(case_id="b", outcome=EvalOutcome.FAIL),
        )
        report = EvalReport(results=results, generated_at="2026-01-01T00:00:00+00:00")
        out = tmp_path / "results.jsonl"
        count = report.to_jsonl(out)
        assert count == 2
        lines = [json.loads(line) for line in out.read_text().splitlines()]
        assert lines[0]["case_id"] == "a"
        assert lines[1]["outcome"] == "fail"


# ── EvalHarness ───────────────────────────────────────────────────────────────

class TestEvalHarness:
    def _make_pass_case(self, case_id: str = "test.pass", tags: tuple[str, ...] = ()) -> EvalCase:
        def fn() -> EvalResult:
            return EvalResult(case_id=case_id, outcome=EvalOutcome.PASS, details="ok")
        return EvalCase(case_id=case_id, name=f"Case {case_id}", fn=fn, tags=tags)

    def _make_fail_case(self, case_id: str = "test.fail") -> EvalCase:
        def fn() -> EvalResult:
            return EvalResult(case_id=case_id, outcome=EvalOutcome.FAIL, details="not ok")
        return EvalCase(case_id=case_id, name=f"Case {case_id}", fn=fn)

    def _make_error_case(self, case_id: str = "test.error") -> EvalCase:
        def fn() -> EvalResult:
            raise RuntimeError("unexpected error")
        return EvalCase(case_id=case_id, name=f"Case {case_id}", fn=fn)

    def test_register_and_len(self) -> None:
        h = EvalHarness()
        h.register(self._make_pass_case())
        assert len(h) == 1

    def test_run_empty(self) -> None:
        report = EvalHarness().run()
        assert report.results == ()
        assert report.pass_rate == 1.0

    def test_run_all(self) -> None:
        h = EvalHarness(suite_id="test-suite")
        h.register(self._make_pass_case("a"))
        h.register(self._make_fail_case("b"))
        report = h.run()
        assert report.pass_count == 1
        assert report.fail_count == 1
        assert report.suite_id == "test-suite"

    def test_run_error_case_does_not_raise(self) -> None:
        h = EvalHarness()
        h.register(self._make_error_case())
        report = h.run()
        assert report.error_count == 1
        assert "RuntimeError" in report.results[0].error

    def test_run_with_tag_filter(self) -> None:
        h = EvalHarness()
        h.register(self._make_pass_case("a", tags=("policy",)))
        h.register(self._make_pass_case("b", tags=("mission",)))
        report = h.run(tags=("policy",))
        assert len(report.results) == 1
        assert report.results[0].case_id == "a"

    def test_run_with_case_ids_filter(self) -> None:
        h = EvalHarness()
        h.register(self._make_pass_case("a"))
        h.register(self._make_pass_case("b"))
        report = h.run(case_ids=("b",))
        assert len(report.results) == 1
        assert report.results[0].case_id == "b"

    def test_run_case_single(self) -> None:
        h = EvalHarness()
        h.register(self._make_pass_case("x"))
        result = h.run_case("x")
        assert result.outcome == EvalOutcome.PASS

    def test_run_case_unknown_returns_error(self) -> None:
        h = EvalHarness()
        result = h.run_case("nonexistent")
        assert result.outcome == EvalOutcome.ERROR
        assert "not registered" in result.error

    def test_list_cases(self) -> None:
        h = EvalHarness()
        h.register(self._make_pass_case("a", tags=("policy",)))
        h.register(self._make_pass_case("b", tags=("mission",)))
        all_cases = h.list_cases()
        assert len(all_cases) == 2
        filtered = h.list_cases(tags=("policy",))
        assert len(filtered) == 1
        assert filtered[0]["case_id"] == "a"

    def test_diff_no_regressions(self, tmp_path) -> None:
        h = EvalHarness()
        h.register(self._make_pass_case("a"))
        report = h.run()
        baseline = tmp_path / "baseline.jsonl"
        report.to_jsonl(baseline)
        diff = h.diff(report, baseline)
        assert diff["regression_count"] == 0
        assert diff["improvement_count"] == 0

    def test_diff_detects_regression(self, tmp_path) -> None:
        h = EvalHarness(suite_id="s")
        h.register(self._make_pass_case("a"))
        baseline_report = h.run()
        baseline = tmp_path / "baseline.jsonl"
        baseline_report.to_jsonl(baseline)

        # Now the case fails
        h2 = EvalHarness(suite_id="s")
        h2.register(self._make_fail_case("a"))
        new_report = h2.run()
        diff = h.diff(new_report, baseline)
        assert diff["regression_count"] == 1
        assert diff["regressions"][0]["case_id"] == "a"

    def test_diff_missing_baseline(self, tmp_path) -> None:
        h = EvalHarness()
        report = h.run()
        diff = h.diff(report, tmp_path / "missing.jsonl")
        assert "error" in diff

    def test_latency_populated(self) -> None:
        h = EvalHarness()
        h.register(self._make_pass_case("a"))
        report = h.run()
        assert report.results[0].latency_ms >= 0.0

    def test_register_many(self) -> None:
        h = EvalHarness()
        h.register_many([self._make_pass_case("a"), self._make_pass_case("b")])
        assert len(h) == 2


# ── Pre-built fixtures ────────────────────────────────────────────────────────

class TestBuiltinFixtures:
    def test_policy_suite_all_pass(self) -> None:
        h = EvalHarness(suite_id="policy")
        h.register_many(make_policy_suite())
        report = h.run()
        assert report.fail_count == 0
        assert report.error_count == 0
        assert report.pass_count == 3

    def test_mission_lifecycle_suite_passes(self) -> None:
        h = EvalHarness(suite_id="mission")
        h.register_many(make_mission_lifecycle_suite())
        report = h.run()
        assert report.fail_count == 0
        assert report.pass_count == 1

    def test_intake_suite_all_pass(self) -> None:
        h = EvalHarness(suite_id="intake")
        h.register_many(make_intake_suite())
        report = h.run()
        assert report.fail_count == 0
        assert report.error_count == 0
        assert report.pass_count == 3

    def test_combined_suite_tag_filter(self) -> None:
        h = EvalHarness(suite_id="all")
        h.register_many(make_policy_suite())
        h.register_many(make_intake_suite())
        h.register_many(make_mission_lifecycle_suite())
        security_report = h.run(tags=("security",))
        # policy.block_destructive + intake.critical both have "security" tag
        assert len(security_report.results) == 2
