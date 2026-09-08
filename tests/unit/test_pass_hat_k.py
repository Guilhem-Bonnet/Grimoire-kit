"""Tests for evals/pass_hat_k.py (B13) — the raw PassHatK/pass_hat_k function.

Ported from tests/unit/test_evals.py::TestPassHatK when the surrounding
EvalCase/EvalHarness system (grimoire.evals) was retired by the flows/evals
consolidation — pass_hat_k itself is still consumed by evals/aggregate.py.
tests/unit/test_evals_aggregate.py separately covers `_pass_hat_k(rows)`,
aggregate.py's own wrapper over this function; this file tests the function
directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "evals"))

from pass_hat_k import pass_hat_k  # noqa: E402


def test_task_passing_all_k_reps_counts_as_passed() -> None:
    result = pass_hat_k({"t1": [True, True, True]}, k=3)
    assert result.tasks_evaluated == 1
    assert result.tasks_passed == 1
    assert result.value == 1.0
    assert result.per_task == {"t1": True}


def test_a_single_failure_fails_the_whole_task() -> None:
    result = pass_hat_k({"t1": [True, True, False]}, k=3)
    assert result.tasks_passed == 0
    assert result.tasks_evaluated == 1
    assert result.per_task == {"t1": False}


def test_unexecuted_rep_is_neither_success_nor_failure() -> None:
    """A rep that never ran (None) must not be silently coerced into a
    pass or a fail — the task falls short of k and is excluded instead.
    """
    result = pass_hat_k({"t1": [True, True, None]}, k=3)
    assert result.tasks_evaluated == 0
    assert result.tasks_passed == 0
    assert result.tasks_insufficient == ("t1",)
    assert result.per_task == {"t1": None}


def test_more_than_k_executed_reps_still_counts() -> None:
    result = pass_hat_k({"t1": [True, True, True, True]}, k=3)
    assert result.tasks_evaluated == 1
    assert result.tasks_passed == 1


def test_mixed_suite_rate() -> None:
    result = pass_hat_k(
        {
            "t1": [True, True, True],
            "t2": [True, False, True],
            "t3": [True, None, True],  # insufficient — excluded
        },
        k=3,
    )
    assert result.tasks_evaluated == 2
    assert result.tasks_passed == 1
    assert result.value == 0.5
    assert result.tasks_insufficient == ("t3",)


def test_empty_suite_has_no_rate() -> None:
    result = pass_hat_k({}, k=5)
    assert result.tasks_evaluated == 0
    assert result.value is None


def test_rejects_k_below_one() -> None:
    with pytest.raises(ValueError, match="k doit"):
        pass_hat_k({"t1": [True]}, k=0)


def test_to_dict_is_json_ready() -> None:
    result = pass_hat_k({"t1": [True]}, k=1)
    d = result.to_dict()
    assert d["pass_hat_k"] == 1.0
    assert d["tasks_evaluated"] == 1
    json.dumps(d)  # doit être sérialisable tel quel
