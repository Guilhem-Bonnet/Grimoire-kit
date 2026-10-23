"""Tests du garde scripts/check-up-prove-budget.py (B-03).

Le budget de ``grimoire up --prove`` n'est un budget que si dépasser fait
échouer, que si un seuil introuvable fait échouer lui aussi, et que le job de
release l'exécute réellement.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-up-prove-budget.py"

GOOD: dict[str, Any] = {"hooks_fired": True, "first_gate": "green", "time_to_first_proof_s": 3.2}


def _run(tmp_path: Path, report: object, doc_text: str | None, *, raw: str | None = None) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "prove.json"
    report_path.write_text(raw if raw is not None else json.dumps(report), encoding="utf-8")
    doc = tmp_path / "onboarding.md"
    if doc_text is not None:
        doc.write_text(doc_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--report", str(report_path), "--doc", str(doc)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )


BUDGET_DOC = "# J1\n\n<!-- up-prove-budget-s: 60 -->\n"


def test_a_proof_within_budget_passes(tmp_path: Path) -> None:
    result = _run(tmp_path, GOOD, BUDGET_DOC)
    assert result.returncode == 0, result.stderr
    assert "3.2" in result.stdout


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"time_to_first_proof_s": 60.5}, "dépasse le budget"),
        ({"hooks_fired": False}, "hooks_fired"),
        ({"first_gate": "red"}, "first_gate"),
        ({"first_gate": "unevaluated"}, "first_gate"),
        ({"time_to_first_proof_s": None}, "time_to_first_proof_s"),
        ({"time_to_first_proof_s": True}, "time_to_first_proof_s"),
    ],
)
def test_each_broken_field_fails(tmp_path: Path, override: dict[str, Any], expected: str) -> None:
    result = _run(tmp_path, {**GOOD, **override}, BUDGET_DOC)
    assert result.returncode == 1, result.stdout
    assert expected in result.stderr


def test_a_missing_field_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, {"first_gate": "green"}, BUDGET_DOC)
    assert result.returncode == 1
    assert "hooks_fired" in result.stderr


def test_a_budget_that_cannot_be_found_fails_instead_of_passing(tmp_path: Path) -> None:
    """Un seuil introuvable ne serait jamais dépassé : c'est un échec, pas un laissez-passer."""
    no_marker = _run(tmp_path, GOOD, "# J1\n\nsans budget\n")
    assert no_marker.returncode == 2
    assert "aucun budget déclaré" in no_marker.stderr
    (tmp_path / "other").mkdir()
    no_doc = _run(tmp_path / "other", GOOD, None)
    assert no_doc.returncode == 2
    assert "illisible" in no_doc.stderr


def test_an_unreadable_report_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, None, BUDGET_DOC, raw="{pas du json")
    assert result.returncode == 1
    assert "illisible" in result.stderr


def test_a_report_that_is_not_an_object_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, ["hooks_fired"], BUDGET_DOC).returncode == 1


def test_the_measured_duration_reaches_the_job_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert _run(tmp_path, GOOD, BUDGET_DOC).returncode == 0
    assert "3.2" in summary.read_text(encoding="utf-8")


def test_onboarding_declares_a_budget_and_the_release_job_enforces_it() -> None:
    """Brancher : le seuil est dans le document, et le job de release lance le garde dessus."""
    sys.path.insert(0, str(SCRIPT_PATH.parent))
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("check_up_prove_budget", SCRIPT_PATH)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPT_PATH.parent))
    doc = (REPO_ROOT / "docs" / "tutoriels" / "onboarding.md").read_text(encoding="utf-8")
    assert module.declared_budget(doc) is not None
    publish = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "grimoire -o json up" in publish
    assert "--prove" in publish
    assert "scripts/check-up-prove-budget.py" in publish
