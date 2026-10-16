"""Garde de coût face au nu (B-00) : données figées, aucun appel modèle."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"


def _load(name: str, filename: str) -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cg = _load("cost_gate", "cost_gate.py")
ta = _load("three_arms_cost_gate_test", "three_arms.py")

SUBSET = ["go/a", "python/b"]


def _rows(nu: float, gov: float, *, k: int = 3, **overrides: Any) -> list[dict[str, Any]]:
    rows = []
    for task in SUBSET:
        for arm, cost in (("nu", nu), ("kit-gov", gov)):
            for i in range(k):
                row: dict[str, Any] = {
                    "task_id": task,
                    "arm": arm,
                    "run_index": i,
                    "success": True,
                    "total_cost_usd": cost,
                    "model_usage": {"claude-opus-5-5": {}},
                }
                row.update(overrides.get(arm, {}))
                rows.append(row)
    return rows


# ── Un coût inconnu n'est pas zéro ──────────────────────────────────────────


@pytest.mark.parametrize(
    "result", [None, {}, {"total_cost_usd": None}, {"total_cost_usd": "abc"}, {"total_cost_usd": True}]
)
def test_cost_from_result_unknown_is_not_a_measure(result: dict[str, Any] | None) -> None:
    assert cg.cost_from_result(result) == (0.0, False)


def test_cost_from_result_explicit_zero_is_a_known_cost() -> None:
    assert cg.cost_from_result({"total_cost_usd": 0}) == (0.0, True)
    assert cg.cost_from_result({"total_cost_usd": 0.42}) == (0.42, True)


def test_legacy_row_without_flag_reads_zero_as_unknown() -> None:
    assert cg.row_cost_known({"total_cost_usd": 0.12})
    assert not cg.row_cost_known({"total_cost_usd": 0.0})
    assert not cg.row_cost_known({"total_cost_usd": None})
    assert cg.row_cost_known({"total_cost_usd": 0.0, "cost_known": True})
    assert not cg.row_cost_known({"total_cost_usd": 0.3, "cost_known": False})


def test_run_record_carries_cost_known_and_report_flags_partial_cost() -> None:
    """Le harnais écrit ``cost_known`` ; le rapport dit que les coûts sont des bornes basses."""
    outcome = ta.RunOutcome()
    outcome.total_cost_usd, outcome.cost_known = cg.cost_from_result(None)
    assert outcome.cost_known is False

    def rec(arm: str, cost: float, known: bool) -> Any:
        return ta.RunRecord("python/a", "python", arm, 0, True, cost, 1, 1, 1, 1.0, "completed", cost_known=known)

    records = [rec("nu", 0.1, True), rec("kit-gov", 0.2, True), rec("kit-gov", 0.0, False)]
    report = ta.build_report(records, total_tasks=1, expected_cost=None, seed=1)
    assert report["per_arm"]["nu"]["cost_known_ratio"] == 1.0
    assert report["per_arm"]["kit-gov"]["cost_known_runs"] == 1
    assert report["per_arm"]["kit-gov"]["cost_known_ratio"] == 0.5
    markdown = ta.render_report_markdown(report)
    assert "cost_known_ratio 0.50" in markdown
    assert "bornes basses" in markdown
    assert "Bras `nu` : coût connu" not in markdown


# ── Verdict ─────────────────────────────────────────────────────────────────


def test_pass_within_tolerance_reports_ratio_gap_and_verdict() -> None:
    result = cg.evaluate_cost_gate(
        _rows(0.10, 0.12), subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0}, expected_runs_per_arm=6
    )
    assert result["verdict"] == "pass"
    assert result["ratio_pct"] == pytest.approx(120.0)
    assert result["gap_points"] == pytest.approx(2.0)
    assert result["saturated"] is True
    md = cg.render_markdown(result)
    assert "120.0 %" in md and "+2.0 points" in md and "pass" in md and "mesure le coût, pas le succès" in md


def test_exceeding_tolerance_blocks() -> None:
    result = cg.evaluate_cost_gate(_rows(0.10, 0.126), subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0})
    assert result["verdict"] == "exceeded"
    assert result["gap_points"] == pytest.approx(8.0)
    assert cg.EXIT_CODES[result["verdict"]] == 1


def test_gap_exactly_at_tolerance_passes_and_improvement_passes() -> None:
    assert (
        cg.evaluate_cost_gate(_rows(0.10, 0.123), subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0})[
            "verdict"
        ]
        == "pass"
    )
    assert (
        cg.evaluate_cost_gate(_rows(0.10, 0.11), subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0})[
            "verdict"
        ]
        == "pass"
    )


def test_unknown_cost_makes_the_ratio_unevaluated_not_a_pass() -> None:
    """Un coût manquant compté pour 0 ferait baisser le coût du kit et passer la garde."""
    rows = _rows(0.10, 0.20, **{"kit-gov": {}})
    for row in rows:
        if row["arm"] == "kit-gov" and row["run_index"] == 0:
            row["total_cost_usd"] = 0.0
            row["cost_known"] = False
    result = cg.evaluate_cost_gate(rows, subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 190.0})
    assert result["verdict"] == "unevaluated"
    assert result["ratio_pct"] is None
    assert result["arms"]["kit-gov"]["cost_known_ratio"] == pytest.approx(4 / 6)
    assert any("coût inconnu n'est pas zéro" in r for r in result["reasons"])
    assert cg.EXIT_CODES["unevaluated"] == 2


def test_missing_previous_barrier_is_unevaluated_but_ratio_is_published() -> None:
    result = cg.evaluate_cost_gate(_rows(0.10, 0.12), subset=SUBSET)
    assert result["verdict"] == "unevaluated"
    assert result["ratio_pct"] == pytest.approx(120.0)


def test_incomplete_subset_is_unevaluated() -> None:
    rows = [r for r in _rows(0.10, 0.12) if not (r["arm"] == "kit-gov" and r["run_index"] == 2)]
    result = cg.evaluate_cost_gate(
        rows, subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0}, expected_runs_per_arm=6
    )
    assert result["verdict"] == "unevaluated"
    assert any("4 runs valides au lieu de 6" in r for r in result["reasons"])
    gone = cg.evaluate_cost_gate(
        [r for r in _rows(0.1, 0.12) if r["task_id"] != "go/a"],
        subset=SUBSET,
        previous={"verdict": "pass", "ratio_pct": 1.0},
    )
    assert gone["verdict"] == "unevaluated"


def test_rows_outside_subset_and_invalid_runs_are_ignored() -> None:
    rows = [
        *_rows(0.10, 0.12),
        {"task_id": "rust/z", "arm": "kit-gov", "success": True, "total_cost_usd": 9.0},
        {"task_id": "go/a", "arm": "kit-gov", "success": True, "total_cost_usd": 9.0, "transcript_check": "invalid"},
    ]
    result = cg.evaluate_cost_gate(rows, subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0})
    assert result["verdict"] == "pass"
    assert result["arms"]["kit-gov"]["n_invalid"] == 1


def test_unsaturated_subset_is_flagged() -> None:
    rows = _rows(0.10, 0.12)
    rows[-1]["success"] = False
    result = cg.evaluate_cost_gate(rows, subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0})
    assert result["saturated"] is False
    assert any("non saturé" in w for w in result["warnings"])


def test_model_change_is_warned_not_hidden() -> None:
    result = cg.evaluate_cost_gate(
        _rows(0.10, 0.12), subset=SUBSET, previous={"verdict": "pass", "ratio_pct": 118.0, "models": ["claude-opus-5"]}
    )
    assert result["verdict"] == "pass"
    assert any("modèles différents" in w for w in result["warnings"])


def test_waiver_needs_author_reason_and_date_and_is_traced() -> None:
    kwargs: dict[str, Any] = {"subset": SUBSET, "previous": {"verdict": "pass", "ratio_pct": 100.0}}
    waived = cg.evaluate_cost_gate(
        _rows(0.10, 0.20), waiver={"by": "Guilhem", "reason": "W2-09 assumé", "date": "2026-10-20"}, **kwargs
    )
    assert waived["verdict"] == "waived"
    assert waived["waiver"]["verdict_before_waiver"] == "exceeded"
    assert "Dérogation de Guilhem" in cg.render_markdown(waived)
    refused = cg.evaluate_cost_gate(_rows(0.10, 0.20), waiver={"by": "Guilhem"}, **kwargs)
    assert refused["verdict"] == "exceeded"
    assert any("dérogation refusée" in r for r in refused["reasons"])
    passing = cg.evaluate_cost_gate(
        _rows(0.10, 0.11),
        waiver={"by": "G", "reason": "r", "date": "d"},
        subset=SUBSET,
        previous={"verdict": "pass", "ratio_pct": 108.0},
    )
    assert passing["verdict"] == "pass" and passing["waiver"] is None


# ── Le coût des dispatchs compte (B00-S1-01) ────────────────────────────────

PREV = {"verdict": "pass", "ratio_pct": 118.0}


def _dispatch(total: int, usd: float | None, status: str | None = "exact") -> dict[str, Any]:
    overall: dict[str, Any] = {"total": total, "total_cost_usd": usd}
    if status is not None:
        overall["cost_status"] = status
    return {"overall": overall}


def test_dispatch_cost_is_added_to_the_session_cost_and_can_exceed() -> None:
    """Le panel k=3 tourne en sous-processus : son coût n'est pas dans le ``total_cost_usd`` de la session."""
    rows = _rows(0.10, 0.12, **{"kit-gov": {"dispatch_stats": _dispatch(3, 0.30)}})
    result = cg.evaluate_cost_gate(rows, subset=SUBSET, previous=PREV, expected_runs_per_arm=6)
    assert result["verdict"] == "exceeded"
    assert result["ratio_pct"] == pytest.approx(420.0)
    assert result["arms"]["kit-gov"]["dispatch_cost_usd"] == pytest.approx(6 * 0.30)
    assert result["arms"]["kit-gov"]["median_cost_usd_resolved"] == pytest.approx(0.42)


def test_dispatch_stats_in_error_make_the_row_unknown_not_a_pass() -> None:
    rows = _rows(0.10, 0.12, **{"kit-gov": {"dispatch_stats": {"error": "boom"}}})
    result = cg.evaluate_cost_gate(rows, subset=SUBSET, previous=PREV, expected_runs_per_arm=6)
    assert result["verdict"] == "unevaluated"
    assert result["ratio_pct"] is None
    assert result["arms"]["kit-gov"]["dispatch_cost_unknown_runs"] == 6
    assert any("dispatch" in r and "coût inconnu n'est pas zéro" in r for r in result["reasons"])


@pytest.mark.parametrize(
    "stats",
    [
        _dispatch(3, None, "unknown"),
        _dispatch(3, 0.2, "lower_bound"),
        _dispatch(3, 0.0, None),  # ledger d'avant W1-01 : un 0.0 ne distingue pas gratuit d'inconnu
        _dispatch(3, None, None),
        {"overall": "illisible"},
    ],
)
def test_dispatches_without_an_exact_cost_are_unknown(stats: dict[str, Any]) -> None:
    rows = _rows(0.10, 0.12, **{"kit-gov": {"dispatch_stats": stats}})
    assert cg.evaluate_cost_gate(rows, subset=SUBSET, previous=PREV)["verdict"] == "unevaluated"


@pytest.mark.parametrize(
    "stats", [None, {"overall": None}, _dispatch(0, 0.0), _dispatch(0, 0.0, None), _dispatch(3, 0.0, "exact")]
)
def test_no_dispatch_or_a_priced_zero_is_a_known_cost_and_keeps_the_ratio(stats: dict[str, Any] | None) -> None:
    """Le lot L (``overall.total == 0`` sur 60/60) reste comparable : sa base de 118,4 % n'inclut aucun dispatch."""
    rows = _rows(0.10, 0.12, **{"kit-gov": {"dispatch_stats": stats}})
    result = cg.evaluate_cost_gate(rows, subset=SUBSET, previous=PREV)
    assert result["verdict"] == "pass" and result["ratio_pct"] == pytest.approx(120.0)


# ── Une référence non validée n'en est pas une (B00-S1-02) ──────────────────


def test_an_exceeded_barrier_is_refused_as_reference() -> None:
    """Sinon, rejouer avec le rapport du dépassement lui-même le ferait passer à l'écart 0."""
    exceeded = cg.evaluate_cost_gate(_rows(0.10, 0.15), subset=SUBSET, previous=PREV)
    assert exceeded["verdict"] == "exceeded"
    again = cg.evaluate_cost_gate(_rows(0.10, 0.15), subset=SUBSET, previous=exceeded)
    assert again["verdict"] == "unevaluated"
    assert again["previous_ratio_pct"] is None and again["gap_points"] is None
    assert any("référence refusée" in r and "exceeded" in r for r in again["reasons"])
    assert cg.EXIT_CODES[again["verdict"]] == 2


def test_an_unevaluated_barrier_is_refused_as_reference() -> None:
    first = cg.evaluate_cost_gate(_rows(0.10, 0.30), subset=SUBSET)
    assert first["verdict"] == "unevaluated" and first["ratio_pct"] == pytest.approx(300.0)
    again = cg.evaluate_cost_gate(_rows(0.10, 0.30), subset=SUBSET, previous=first)
    assert again["verdict"] == "unevaluated"
    assert any("référence refusée" in r and "unevaluated" in r for r in again["reasons"])


def test_a_report_without_verdict_is_refused_as_reference() -> None:
    result = cg.evaluate_cost_gate(_rows(0.10, 0.12), subset=SUBSET, previous={"ratio_pct": 118.0})
    assert result["verdict"] == "unevaluated"
    assert any("référence refusée" in r for r in result["reasons"])


def test_pass_baseline_and_waived_barriers_are_valid_references() -> None:
    waived = cg.evaluate_cost_gate(
        _rows(0.10, 0.20),
        subset=SUBSET,
        previous=PREV,
        waiver={"by": "Guilhem", "reason": "W2-09 assumé", "date": "2026-10-20"},
    )
    assert waived["verdict"] == "waived"
    base = json.loads((ROOT / "docs" / "bench" / "cost-gate-baseline.json").read_text(encoding="utf-8"))
    nxt_pass = cg.evaluate_cost_gate(_rows(0.10, 0.12), subset=SUBSET, previous=base)
    assert nxt_pass["verdict"] == "pass"
    # Une dérogation entérine le ratio dérogé : la barrière suivante s'y compare, et le rapport le dit.
    after = cg.evaluate_cost_gate(_rows(0.10, 0.20), subset=SUBSET, previous=waived)
    assert after["verdict"] == "pass" and after["previous_ratio_pct"] == pytest.approx(200.0)
    assert any("dérogée par Guilhem" in w for w in after["warnings"])
    worse = cg.evaluate_cost_gate(_rows(0.10, 0.22), subset=SUBSET, previous=waived)
    assert worse["verdict"] == "exceeded"


# ── Sous-lot pré-déclaré, base, CLI ─────────────────────────────────────────


def test_declared_subset_follows_its_rule_and_the_baseline_matches_its_scale() -> None:
    doc = json.loads((ROOT / "docs" / "bench" / "cost-gate-subset.json").read_text(encoding="utf-8"))
    tasks = cg.load_subset()
    assert tasks == doc["tasks"]
    langs = [t.split("/")[0] for t in tasks]
    assert sorted(langs) == ["go", "javascript", "python", "rust"]
    assert doc["k"] == 3 and doc["arms"] == ["nu", "kit-gov"]
    base = json.loads((ROOT / "docs" / "bench" / "cost-gate-baseline.json").read_text(encoding="utf-8"))
    nu, gov = base["median_cost_usd_resolved"]["nu"], base["median_cost_usd_resolved"]["kit-gov"]
    assert base["ratio_pct"] == pytest.approx(100 * gov / nu)


def test_load_subset_refuses_an_empty_declaration(tmp_path: Path) -> None:
    bad = tmp_path / "s.json"
    bad.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="liste non vide"):
        cg.load_subset(bad)


def test_cli_writes_report_and_exit_code(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text("\n".join(json.dumps(r) for r in _rows(0.10, 0.20)) + "\n", encoding="utf-8")
    subset = tmp_path / "subset.json"
    subset.write_text(json.dumps({"k": 3, "tasks": SUBSET}), encoding="utf-8")
    previous = tmp_path / "prev.json"
    previous.write_text(json.dumps({"verdict": "pass", "ratio_pct": 118.0}), encoding="utf-8")
    out = tmp_path / "out"
    code = cg.main(
        ["--results", str(results), "--subset", str(subset), "--previous", str(previous), "--out-dir", str(out)]
    )
    assert code == 1
    saved = json.loads((out / "cost-gate.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == "exceeded" and saved["ratio_pct"] == pytest.approx(200.0)
    assert "exceeded" in (out / "cost-gate.md").read_text(encoding="utf-8")


def test_filter_tasks_keeps_declared_tasks_and_refuses_a_missing_one(tmp_path: Path) -> None:
    from types import SimpleNamespace

    tasks = [SimpleNamespace(task_id=t) for t in ("go/a", "python/b", "rust/c")]
    subset = tmp_path / "s.json"
    subset.write_text(json.dumps({"tasks": SUBSET}), encoding="utf-8")
    assert [t.task_id for t in cg.filter_tasks(tasks, subset)] == SUBSET
    assert len(cg.filter_tasks(tasks, None)) == 3
    subset.write_text(json.dumps({"tasks": ["go/a", "go/zzz"]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="go/zzz"):
        cg.filter_tasks(tasks, subset)
