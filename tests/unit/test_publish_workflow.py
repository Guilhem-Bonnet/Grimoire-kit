"""Le workflow de publication ne masque aucun job et ne promet rien qu'il ne fait pas.

L'étage TestPyPI a échoué à chaque tag pendant des mois sans bloquer quoi que
ce soit : `continue-on-error: true` en faisait une pré-vérification de façade
(#195). Le job est retiré ; ce test empêche qu'un autre prenne la même forme.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish.yml"


@pytest.fixture(scope="module")
def jobs() -> dict[str, dict]:
    if not _WORKFLOW.is_file():
        pytest.skip("dépôt source uniquement")
    return YAML(typ="safe").load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


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


# ── D-03 : attestation, sommes, SBOM de la wheel seule ────────────────────────


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def test_an_attest_job_gates_both_publications(jobs: dict[str, dict]) -> None:
    assert "attest" in jobs
    for name in ("publish-pypi", "github-release"):
        needs = jobs[name]["needs"]
        assert "attest" in (needs if isinstance(needs, list) else [needs]), f"{name} ne dépend pas de attest"
    assert "needs.attest.result == 'success'" in str(jobs["github-release"]["if"])


def test_the_attest_job_signs_every_published_file_and_fails_without_attestation(jobs: dict[str, dict]) -> None:
    job = jobs["attest"]
    assert {"id-token": "write", "attestations": "write"}.items() <= job["permissions"].items()
    attest = [s for s in _steps(job) if str(s.get("uses", "")).startswith("actions/attest-build-provenance@")]
    assert len(attest) == 1
    subjects = attest[0]["with"]["subject-path"]
    for pattern in ("dist/*", "sbom/sbom.cdx.json", "checksums/SHA256SUMS"):
        assert pattern in subjects
    verify = [s for s in _steps(job) if "gh attestation verify" in str(s.get("run", ""))]
    assert verify, "aucune étape ne vérifie que l'attestation existe"
    assert verify[0].get("continue-on-error") is not True
    order = _steps(job).index(attest[0]) < _steps(job).index(verify[0])
    assert order, "la vérification doit suivre l'attestation"


def test_the_sbom_describes_the_wheel_alone(jobs: dict[str, dict]) -> None:
    runs = [str(s.get("run", "")) for s in _steps(jobs["build"])]
    sbom = next(r for r in runs if "cyclonedx-py" in r)
    # un environnement neuf, jamais celui du runner (build, pip, cyclonedx-bom y figurent)
    assert ".wheel-env/bin/python" in sbom.split("cyclonedx-py", 1)[1]
    install = next(r for r in runs if "-m venv" in r)
    assert "--without-pip" in install and "dist/*.whl" in install


def test_sha256sums_are_built_checked_and_published(jobs: dict[str, dict]) -> None:
    runs = " ".join(str(s.get("run", "")) for s in _steps(jobs["build"]))
    assert "sha256sum --check" in runs
    uploads = [s["with"]["name"] for s in _steps(jobs["build"]) if "upload-artifact" in str(s.get("uses", ""))]
    assert "checksums" in uploads
    release = next(s for s in _steps(jobs["github-release"]) if "action-gh-release" in str(s.get("uses", "")))
    assert "checksums/SHA256SUMS" in release["with"]["files"]
