"""B-02 : recalcul d'un rapport depuis ses données brutes, hash des données, MDE et n requis.

Données synthétiques ou figées : aucun appel ``claude``, aucune dépense.
"""

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
FIXTURE = ROOT / "tests" / "fixtures" / "bench" / "lot-l-nu-baseline.json"


def _load(name: str, filename: str) -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ta = _load("three_arms_recompute_test", "three_arms.py")
import power_calc as pc  # noqa: E402 - `_load` a placé scripts/bench sur sys.path
import recompute as rc  # noqa: E402


def _row(
    task: str, arm: str, index: int, success: bool, *, cost: float = 0.5, cost_known: bool | None = True,
    model: str | None = "claude-a",
) -> dict[str, Any]:  # fmt: skip
    record = ta.RunRecord(
        task, task.split("/")[0], arm, index, success, cost, 10, 10, 3, 5.0, "completed",
        cost_known=cost_known, model_id=model,
    )  # fmt: skip
    return record.to_dict()


def _rows() -> list[dict[str, Any]]:
    """4 tâches x (nu, kit-gov) x k=3 : le nu varie sur 2 tâches, kit-gov sur 1."""
    rows: list[dict[str, Any]] = []
    plan = {
        "python/a": {"nu": [True, True, True], "kit-gov": [True, True, True]},
        "python/b": {"nu": [True, False, True], "kit-gov": [True, True, True]},
        "python/c": {"nu": [True, True, True], "kit-gov": [True, False, True]},
        "python/d": {"nu": [False, False, True], "kit-gov": [False, True, True]},
    }
    for task, arms in plan.items():
        for arm, outcomes in arms.items():
            rows += [_row(task, arm, i, ok) for i, ok in enumerate(outcomes)]
    return rows


def _write_results(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _campaign(tmp_path: Path, rows: list[dict[str, Any]] | None = None) -> tuple[Path, Path]:
    """results.jsonl + report.json écrits par le vrai ``write_report`` (hash cité)."""
    results = _write_results(tmp_path / "results.jsonl", rows or _rows())
    records = [ta.RunRecord(**row) for row in json.loads("[" + ",".join(results.read_text().splitlines()) + "]")]
    report_dir = tmp_path / "report"
    ta.write_report(records, report_dir, total_tasks=4, expected_cost=None, seed=551, results_path=results)
    return results, report_dir / "report.json"


def _verdict(results: Path, report: Path, **kwargs: Any) -> dict[str, Any]:
    return rc.recompute_report(results, report, ta=ta, **kwargs)


def _edit_report(report: Path, edit: Any) -> None:
    data = json.loads(report.read_text(encoding="utf-8"))
    edit(data)
    report.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── 1. Un rapport intact se recalcule, un rapport falsifié est détecté ─────


def test_untouched_report_recomputes_clean(tmp_path: Path) -> None:
    results, report = _campaign(tmp_path)
    verdict = _verdict(results, report)
    assert verdict["ok"] is True, verdict["mismatches"]
    assert verdict["mismatches"] == []
    assert verdict["hash"]["status"] == "ok"


@pytest.mark.parametrize(
    ("path", "tamper"),
    [
        ("per_arm.kit-gov.success_rate", lambda d: d["per_arm"]["kit-gov"].__setitem__("success_rate", 1.0)),
        ("per_arm.kit-gov.pass_hat_k_rate", lambda d: d["per_arm"]["kit-gov"].__setitem__("pass_hat_k_rate", 1.0)),
        ("per_arm.kit-gov.total_cost_usd", lambda d: d["per_arm"]["kit-gov"].__setitem__("total_cost_usd", 0.01)),
        ("per_arm.nu.n_runs", lambda d: d["per_arm"]["nu"].__setitem__("n_runs", 99)),
        ("by_task.python/d.kit-gov.pass_hat_k", lambda d: d["by_task"]["python/d"]["kit-gov"].__setitem__("pass_hat_k", 1)),
        ("suite_saturation.verdict", lambda d: d["suite_saturation"].__setitem__("verdict", "SATURÉ")),
    ],
)
def test_falsified_report_is_detected(tmp_path: Path, path: str, tamper: Any) -> None:
    results, report = _campaign(tmp_path)
    _edit_report(report, tamper)
    verdict = _verdict(results, report)
    assert verdict["ok"] is False
    assert path in [m["path"] for m in verdict["mismatches"]]
    # Les données n'ont pas bougé : c'est bien le rapport qui est en cause.
    assert verdict["hash"]["status"] == "ok"


def test_tampered_results_are_detected_by_the_cited_hash(tmp_path: Path) -> None:
    results, report = _campaign(tmp_path)
    rows = _rows()
    rows[-1]["success"] = not rows[-1]["success"]
    _write_results(results, rows)
    verdict = _verdict(results, report)
    assert verdict["ok"] is False
    assert verdict["hash"]["status"] == "mismatch"


def test_consistently_edited_results_and_report_still_fail_on_the_cited_hash(tmp_path: Path) -> None:
    """Quelqu'un refait tout le rapport sur des données retouchées : seul le hash cité le trahit."""
    results, report = _campaign(tmp_path)
    cited = json.loads(report.read_text(encoding="utf-8"))["data"]
    rows = [{**row, "success": True} for row in _rows()]
    _write_results(results, rows)
    records = [ta.RunRecord(**row) for row in rows]
    forged_dir = tmp_path / "forged"
    ta.write_report(records, forged_dir, total_tasks=4, expected_cost=None, seed=551)  # sans nouveau sceau
    forged = json.loads((forged_dir / "report.json").read_text(encoding="utf-8"))
    forged["data"] = cited  # il garde le hash d'origine
    report.write_text(json.dumps(forged), encoding="utf-8")
    verdict = _verdict(results, report)
    assert verdict["mismatches"] == []
    assert verdict["hash"]["status"] == "mismatch"
    assert verdict["ok"] is False


def test_report_without_cited_hash_recomputes_but_is_reported_unsealed(tmp_path: Path) -> None:
    results, report = _campaign(tmp_path)
    _edit_report(report, lambda d: d.pop("data"))
    verdict = _verdict(results, report)
    assert verdict["hash"]["status"] == "non scellé"
    assert verdict["ok"] is True
    assert "NON SCELLÉ" in "\n".join(rc.render_verdict_lines(verdict)).upper()
    assert _verdict(results, report, require_seal=True)["ok"] is False


def test_duplicate_rows_are_refused(tmp_path: Path) -> None:
    rows = _rows()
    results, report = _campaign(tmp_path, [*rows, rows[0]])
    verdict = _verdict(results, report)
    assert verdict["ok"] is False
    assert verdict["duplicate_keys"] == ["python/a/nu/0"]


def test_mixed_models_give_an_error_verdict_not_a_crash(tmp_path: Path) -> None:
    results, report = _campaign(tmp_path)
    rows = _rows()
    rows[0]["model_id"] = "claude-b"
    _write_results(results, rows)
    verdict = _verdict(results, report)
    assert verdict["ok"] is False
    assert "modèles différents" in verdict["error"]


def test_report_without_seed_is_unreadable_not_recomputed_with_a_guess(tmp_path: Path) -> None:
    results, report = _campaign(tmp_path)
    _edit_report(report, lambda d: d.pop("seed"))
    verdict = _verdict(results, report)
    assert verdict["ok"] is False
    assert "seed" in verdict["error"]


# ── 2. Le hash des données est cité, cost_known_ratio est publié ───────────


def test_write_report_cites_the_sha256_of_results_in_json_and_markdown(tmp_path: Path) -> None:
    import hashlib

    results, report = _campaign(tmp_path)
    expected = hashlib.sha256(results.read_bytes()).hexdigest()
    data = json.loads(report.read_text(encoding="utf-8"))["data"]
    assert data == {"results_file": "results.jsonl", "results_rows": len(_rows()), "results_sha256": expected}
    assert expected in (report.parent / "report.md").read_text(encoding="utf-8")


def test_write_report_without_results_path_cites_nothing(tmp_path: Path) -> None:
    records = [ta.RunRecord(**row) for row in _rows()]
    ta.write_report(records, tmp_path / "r", total_tasks=4, expected_cost=None, seed=551)
    assert "data" not in json.loads((tmp_path / "r" / "report.json").read_text(encoding="utf-8"))


def test_report_only_seals_the_report_and_recompute_accepts_it(tmp_path: Path) -> None:
    """Chemin réel ``--report-only`` : le rapport écrit par le banc se recalcule et cite le hash."""
    state = tmp_path / "ws" / "state"
    state.mkdir(parents=True)
    results = _write_results(state / "results.jsonl", _rows())
    out = tmp_path / "out"
    assert ta.main(["--report-only", "--workspace", str(tmp_path / "ws"), "--report-dir", str(out)]) == 0
    verdict = _verdict(results, out / "report.json")
    assert verdict["hash"]["status"] == "ok", verdict
    assert verdict["ok"] is True, verdict["mismatches"]


def test_verdict_publishes_cost_known_ratio_per_arm(tmp_path: Path) -> None:
    rows = _rows()
    for row in rows:
        if row["arm"] == "kit-gov" and row["run_index"] == 0:
            row["cost_known"] = False
            row["total_cost_usd"] = 0.0
    results, report = _campaign(tmp_path, rows)
    verdict = _verdict(results, report)
    assert verdict["cost_known_ratio"]["nu"] == 1.0
    assert verdict["cost_known_ratio"]["kit-gov"] == pytest.approx(8 / 12)
    assert "cost_known_ratio" in "\n".join(rc.render_verdict_lines(verdict))


# ── 3. MDE et n requis ─────────────────────────────────────────────────────


def test_n_required_for_a_10_point_mde_in_the_worst_case() -> None:
    # (1,95996 + 0,84162)^2 * 0,5 / 0,10^2 = 392,4 : 393 tâches par bras, très au-delà d'un banc de 20.
    assert pc.n_required(0.10) == 393


def test_mde_for_n_3_and_n_30_in_the_worst_case() -> None:
    assert pc.minimum_detectable_effect(30) == pytest.approx(0.3617, abs=1e-3)
    assert pc.minimum_detectable_effect(3) == pytest.approx(1.1437, abs=1e-3)


def test_mde_above_100_points_is_marked_undetectable() -> None:
    report = pc.power_report(tasks_run=20)
    assert report["worst_case"]["by_n"]["3"]["detectable"] is False
    assert report["worst_case"]["by_n"]["30"]["detectable"] is True
    assert {"3", "20", "30"} <= set(report["worst_case"]["by_n"])


@pytest.mark.parametrize("n", [6, 20, 30, 100, 393])
def test_n_required_inverts_the_mde(n: int) -> None:
    assert pc.n_required(pc.minimum_detectable_effect(n)) == n


def test_one_sided_needs_fewer_tasks_than_two_sided() -> None:
    assert pc.n_required(0.10, sides=1) < pc.n_required(0.10, sides=2)


def test_observed_variance_uses_the_observed_rates() -> None:
    var, reason = pc.observed_variance_sum(0.6, 0.8)
    assert reason is None
    assert var == pytest.approx(0.6 * 0.4 + 0.8 * 0.2)


@pytest.mark.parametrize("rates", [(1.0, 0.5), (0.5, 0.0), (None, 0.5)])
def test_degenerate_observed_variance_is_unavailable_not_zero(rates: tuple[float | None, float | None]) -> None:
    var, reason = pc.observed_variance_sum(*rates)
    assert var is None
    assert reason
    report = pc.power_report(tasks_run=20, rate_a=rates[0], rate_b=rates[1])
    assert report["observed"] is None
    assert report["n_reached_basis"] == "worst_case"


def test_saturated_lot_l_has_no_observed_variance() -> None:
    """Lot L : 20 tâches sur 20 à 1.0 pour le nu. Sa variance observée est nulle : pas de MDE inventé."""
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))["nu_successes_by_task"]
    rate = sum(1 for runs in frozen.values() if all(runs)) / len(frozen)
    assert rate == 1.0
    report = pc.power_report(tasks_run=len(frozen), rate_a=rate, rate_b=0.9)
    assert report["observed"] is None
    assert report["n_reached"] is False
    assert report["worst_case"]["n_required"] > len(frozen)


def test_n_reached_follows_the_required_n() -> None:
    assert pc.power_report(tasks_run=393)["n_reached"] is True
    assert pc.power_report(tasks_run=392)["n_reached"] is False


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_invalid_mde_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="mde"):
        pc.n_required(bad)


def test_invalid_arguments_are_refused() -> None:
    with pytest.raises(ValueError, match="n_tasks"):
        pc.minimum_detectable_effect(0)
    with pytest.raises(ValueError, match="alpha"):
        pc.minimum_detectable_effect(10, alpha=0.0)
    with pytest.raises(ValueError, match="sides"):
        pc.minimum_detectable_effect(10, sides=3)
    with pytest.raises(ValueError, match="variance nulle"):
        pc.minimum_detectable_effect(10, var_sum=0.0)


def test_verdict_carries_the_power_table_from_the_observed_variance(tmp_path: Path) -> None:
    results, report = _campaign(tmp_path)
    capacity = _verdict(results, report)["power"]
    # pass^3 sur les 4 tâches : nu 2/4 = 0,5 ; kit-gov 2/4 = 0,5 -> variance observée 0,5.
    assert capacity["observed"]["rates"] == [0.5, 0.5]
    assert capacity["n_reached"] is False
    assert capacity["tasks_run"] == 4


# ── 4. CLI ─────────────────────────────────────────────────────────────────


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results, report = _campaign(tmp_path)
    argv = ["--results", str(results), "--report", str(report)]
    assert rc.main(argv) == 0
    out = capsys.readouterr().out
    assert "VERDICT : OK" in out
    assert "n = 30" in out
    assert "n = 3" in out
    _edit_report(report, lambda d: d["per_arm"]["nu"].__setitem__("success_rate", 1.0))
    assert rc.main(argv) == 1
    assert "per_arm.nu.success_rate" in capsys.readouterr().out


def test_cli_missing_file_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rc.main(["--results", str(tmp_path / "x.jsonl"), "--report", str(tmp_path / "y.json")]) == 1
    assert "introuvable" in capsys.readouterr().err


def test_cli_truncated_results_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results, report = _campaign(tmp_path)
    results.write_text(results.read_text(encoding="utf-8") + '{"task_id": "x"', encoding="utf-8")
    assert rc.main(["--results", str(results), "--report", str(report)]) == 1
    assert "tronqué" in capsys.readouterr().err


def test_power_cli_prints_the_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert pc.main(["--mde", "0.10", "--tasks", "20"]) == 0
    out = capsys.readouterr().out
    assert "393" in out
    assert "n = 30" in out
