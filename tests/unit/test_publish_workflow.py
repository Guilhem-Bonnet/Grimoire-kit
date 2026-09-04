"""Le workflow de publication ne masque aucun job et ne promet rien qu'il ne fait pas.

L'étage TestPyPI a échoué à chaque tag pendant des mois sans bloquer quoi que
ce soit : `continue-on-error: true` en faisait une pré-vérification de façade
(#195). Le job est retiré ; ce test empêche qu'un autre prenne la même forme.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish.yml"


@pytest.fixture(scope="module")
def jobs() -> dict[str, dict]:
    if not _WORKFLOW.is_file():
        pytest.skip("dépôt source uniquement")
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_no_job_is_allowed_to_fail_silently(jobs: dict[str, dict]) -> None:
    masked = [name for name, job in jobs.items() if job.get("continue-on-error")]
    assert masked == [], f"jobs en continue-on-error : {masked} — un job qui peut échouer sans bloquer ne vérifie rien"


def test_publishing_waits_for_the_build_and_the_smoke_test(jobs: dict[str, dict]) -> None:
    needs = jobs["publish-pypi"]["needs"]
    assert {"build", "test"} <= set(needs if isinstance(needs, list) else [needs])


def test_no_stage_targets_an_index_the_project_is_not_registered_on(jobs: dict[str, dict]) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "test.pypi.org" not in text
    assert "publish-testpypi" not in jobs
