"""W3-02 : la cellule Preuve d'un claim doit se résoudre (VER-2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.core.agentic_standard import check_evidence_gates, setup_standard_profile
from grimoire.core.standard_checks.claim_proof_resolve import resolve_proof_cell

LEDGER = "_grimoire-output/evidence/bootstrap/claim-ledger.md"
TEMPLATE_ROW = "| CL-001 |  | fait |  | hypothèse | faible | vérifier |"
SUMMARY_EMPTY = "| Affirmations bloquantes non prouvées |  |"


def _project(tmp_path: Path, profile: str, proof: str, status: str = "prouvé") -> Path:
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/real.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    ledger = tmp_path / LEDGER
    text = ledger.read_text(encoding="utf-8").replace(SUMMARY_EMPTY, "| Affirmations bloquantes non prouvées | 0 |")
    row = f"| CL-001 | a | fait | {proof} | {status} | haute | utiliser |"
    ledger.write_text(text.replace(TEMPLATE_ROW, row), encoding="utf-8")
    return tmp_path


def _claims(root: Path, state: str = "review") -> dict[str, str]:
    result = check_evidence_gates(root, task_id="bootstrap", target_state=state)
    return {c.id: c.severity for c in result.checks if c.id.startswith("claims.")}


def test_missing_file_is_an_error_in_governed(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "src/inexistant.py:12")
    assert _claims(root).get("claims.proof_unresolved") == "error"


def test_missing_file_is_a_warning_in_starter(tmp_path: Path) -> None:
    root = _project(tmp_path, "starter", "src/inexistant.py:12")
    assert _claims(root).get("claims.proof_unresolved") == "warning"


def test_existing_line_resolves(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "src/real.py:3")
    assert "claims.proof_unresolved" not in _claims(root)


def test_line_beyond_end_of_file_is_unresolved(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "src/real.py:99")
    assert _claims(root).get("claims.proof_unresolved") == "error"


def test_pinned_hash_must_match(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "src/real.py:1@sha256:00000000")
    assert _claims(root).get("claims.proof_unresolved") == "error"
    digest = hashlib.sha256((root / "src/real.py").read_bytes()).hexdigest()
    assert not any(not r.resolved for r in resolve_proof_cell(root, "bootstrap", f"src/real.py:1@sha256:{digest[:12]}"))


def test_path_escaping_the_root_is_unresolved(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "../outside.py:1")
    (tmp_path.parent / "outside.py").write_text("x\n", encoding="utf-8")
    assert _claims(root).get("claims.proof_unresolved") == "error"


def test_unknown_run_id_fails(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "test-run.json#abc123")
    assert _claims(root).get("claims.proof_unresolved") == "error"


def test_recorded_green_run_id_resolves_and_red_one_does_not(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "test-run.json#abc123")
    run = root / "_grimoire-output/evidence/bootstrap/test-run.json"
    run.write_text(json.dumps({"run_id": "abc123", "ok": True}), encoding="utf-8")
    assert "claims.proof_unresolved" not in _claims(root)
    run.write_text(json.dumps({"run_id": "abc123", "ok": False}), encoding="utf-8")
    assert _claims(root).get("claims.proof_unresolved") == "error"


def test_corrupt_test_run_is_an_error_not_a_crash(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "test-run.json#abc123")
    (root / "_grimoire-output/evidence/bootstrap/test-run.json").write_text("{not json", encoding="utf-8")
    assert _claims(root).get("claims.proof_unresolved") == "error"


def test_resolver_exception_is_an_unresolved_error(tmp_path: Path) -> None:
    def boom(_id: str) -> bool:
        raise RuntimeError("backend down")

    (res,) = resolve_proof_cell(tmp_path, "bootstrap", "mem:abc", memory_lookup=boom)
    assert not res.resolved and "backend down" in res.reason


def test_memory_reference_needs_a_lookup(tmp_path: Path) -> None:
    (res,) = resolve_proof_cell(tmp_path, "bootstrap", "mem:abc")
    assert not res.resolved
    (res,) = resolve_proof_cell(tmp_path, "bootstrap", "mem:abc", memory_lookup=lambda i: i == "abc")
    assert res.resolved


def test_only_archived_urls_resolve(tmp_path: Path) -> None:
    (live,) = resolve_proof_cell(tmp_path, "bootstrap", "https://example.com/page")
    (arch,) = resolve_proof_cell(tmp_path, "bootstrap", "https://web.archive.org/web/2026/https://example.com/page")
    assert not live.resolved and arch.resolved


def test_free_text_prouve_is_flagged_not_silent(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "relu le code")
    found = _claims(root)
    assert found.get("claims.proof_unstructured") == "warning"
    assert "claims.proof_unresolved" not in found


def test_resolution_is_a_warning_while_in_progress(tmp_path: Path) -> None:
    root = _project(tmp_path, "governed", "src/inexistant.py:12")
    assert _claims(root, "in_progress").get("claims.proof_unresolved") == "warning"


def test_cli_resolve_exit_code(tmp_path: Path) -> None:
    from grimoire.cli.app import app

    root = _project(tmp_path, "governed", "src/real.py:2")
    runner = CliRunner()
    ok = runner.invoke(app, ["standard", "claims", str(root), "--resolve"])
    assert ok.exit_code == 0, ok.output
    (root / "src/real.py").unlink()
    bad = runner.invoke(app, ["standard", "claims", str(root), "--resolve"])
    assert bad.exit_code == 1 and "UNRESOLVED" in bad.output


@pytest.mark.parametrize("cell", ["10:30", "prouvé", "v1"])
def test_non_reference_tokens_are_free_text(tmp_path: Path, cell: str) -> None:
    assert all(r.kind == "unstructured" for r in resolve_proof_cell(tmp_path, "bootstrap", cell))
