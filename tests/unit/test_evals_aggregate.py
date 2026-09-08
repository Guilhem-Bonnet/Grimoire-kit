"""Tests for evals/aggregate.py — pass^k, catégorie, coût par modèle (B13)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "evals" / "aggregate.py"


def _load_module():
    name = "evals_aggregate_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _row(task_id: str, *, witness: str = "web-app-todo", completed: bool | None = None, model_usage=None) -> dict:
    return {
        "witness": witness,
        "task_id": task_id,
        "judgment": {"completed": completed} if completed is not None else None,
        "external": {"tokens_cost": 1.0, "tests_green": True, "model_usage": model_usage},
        "run": {"num_turns": 10},
        "governance": {},
        "standard": {},
    }


def test_task_categories_reads_the_pinned_suite() -> None:
    mod = _load_module()
    cats = mod.task_categories("web-app-todo")
    assert cats["feat-due-dates"] == "capability"
    assert set(cats) == {t["id"] for t in mod.task_suite("web-app-todo")["tasks"]}


def test_pass_hat_k_all_reps_pass(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "task_categories", lambda w: {"feat-due-dates": "capability"})
    rows = [_row("feat-due-dates", completed=True) for _ in range(5)]  # repetitions_min = 5
    result = mod._pass_hat_k(rows)
    assert result["overall"]["pass_hat_k"] == 1.0
    assert result["overall"]["tasks_evaluated"] == 1
    assert result["capability"]["pass_hat_k"] == 1.0
    assert result["regression"] is None  # aucune tâche regression dans la suite pinnée


def test_pass_hat_k_one_failure_fails_the_task(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "task_categories", lambda w: {"feat-due-dates": "capability"})
    rows = [_row("feat-due-dates", completed=(i != 0)) for i in range(5)]
    result = mod._pass_hat_k(rows)
    assert result["overall"]["pass_hat_k"] == 0.0
    assert result["overall"]["tasks_passed"] == 0


def test_pass_hat_k_below_k_reps_is_insufficient_not_a_fail() -> None:
    mod = _load_module()
    rows = [_row("feat-due-dates", completed=True) for _ in range(3)]  # < repetitions_min (5)
    result = mod._pass_hat_k(rows)
    assert result["overall"]["tasks_evaluated"] == 0
    assert result["overall"]["tasks_insufficient"] == ["web-app-todo:feat-due-dates"]


def test_pass_hat_k_unjudged_rows_do_not_count_as_executed() -> None:
    mod = _load_module()
    rows = [_row("feat-due-dates", completed=True) for _ in range(4)] + [_row("feat-due-dates")]  # 5th unjudged
    result = mod._pass_hat_k(rows)
    assert result["overall"]["tasks_evaluated"] == 0
    assert result["overall"]["tasks_insufficient"]


def test_pass_hat_k_splits_capability_and_regression(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "task_categories", lambda w: {"cap-1": "capability", "reg-1": "regression"})
    rows = [_row("cap-1", completed=True) for _ in range(5)] + [_row("reg-1", completed=False) for _ in range(5)]
    result = mod._pass_hat_k(rows)
    assert result["capability"]["pass_hat_k"] == 1.0
    assert result["regression"]["pass_hat_k"] == 0.0
    assert result["overall"]["pass_hat_k"] == 0.5


def test_pass_hat_k_no_rows_returns_none() -> None:
    mod = _load_module()
    result = mod._pass_hat_k([])
    assert result == {"overall": None, "capability": None, "regression": None}


def test_cost_by_model_aggregates_when_exposed() -> None:
    mod = _load_module()
    rows = [
        _row("t1", model_usage={"claude-sonnet-4-6": {"costUSD": 1.5}}),
        _row("t2", model_usage={"claude-sonnet-4-6": {"costUSD": 0.5}, "claude-haiku-4-5": {"costUSD": 0.1}}),
    ]
    costs = mod._cost_by_model(rows)
    assert costs == {"claude-sonnet-4-6": 2.0, "claude-haiku-4-5": 0.1}


def test_cost_by_model_empty_when_cli_exposes_nothing() -> None:
    mod = _load_module()
    rows = [_row("t1", model_usage=None)]
    assert mod._cost_by_model(rows) == {}


def test_summarize_exposes_pass_hat_k_and_cost_by_model(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "task_categories", lambda w: {"feat-due-dates": "capability"})
    rows = [
        _row("feat-due-dates", completed=True, model_usage={"claude-sonnet-4-6": {"costUSD": 0.2}})
        for _ in range(5)
    ]
    summary = mod.summarize(rows)
    assert summary["pass_hat_k"] == 1.0
    assert summary["pass_hat_k_capability"] == 1.0
    assert summary["pass_hat_k_regression"] is None
    assert summary["cost_by_model"] == {"claude-sonnet-4-6": 1.0}
