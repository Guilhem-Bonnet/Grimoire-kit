"""Tests for ``check_embedding_model`` — the offline-readiness probe.

The check must never touch the network and never download: it reads what the
project declares and looks on disk. A closed site has to learn about a missing
model before the first search, not during it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.cli.cmd_up import check_embedding_model, run_env_checks


def _project(tmp_path: Path, memory_block: str) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        "project:\n"
        '  name: "Demo"\n'
        '  description: "d"\n'
        '  type: "infrastructure"\n'
        f"memory:\n{memory_block}",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never look at the real user cache from a test."""
    monkeypatch.setenv("GRIMOIRE_EMBEDDING_CACHE", str(tmp_path / "empty-cache"))


def test_lexical_project_needs_no_model(tmp_path: Path) -> None:
    root = _project(tmp_path, '  backend: "auto"\n  vector_database: false\n  retrieval_mode: "lexical"\n')
    check = check_embedding_model(root)

    assert check.level == "ok"
    assert "no embedding model needed" in check.detail


def test_declared_model_path_that_exists_is_ok(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.onnx").write_bytes(b"x")
    root = _project(tmp_path, f'  backend: "qdrant-local"\n  embedding_model_path: "{model}"\n')

    check = check_embedding_model(root)

    assert check.level == "ok"
    assert str(model) in check.detail


def test_declared_model_path_that_is_missing_is_flagged(tmp_path: Path) -> None:
    root = _project(tmp_path, f'  backend: "qdrant-local"\n  embedding_model_path: "{tmp_path / "absent"}"\n')
    check = check_embedding_model(root)

    assert check.level == "warn"
    assert "bundle install" in (check.remedy or "")


def test_declared_model_path_that_is_empty_is_flagged(tmp_path: Path) -> None:
    """An empty directory is not a model — a half-finished copy must not pass."""
    empty = tmp_path / "hollow"
    empty.mkdir()
    root = _project(tmp_path, f'  backend: "qdrant-local"\n  embedding_model_path: "{empty}"\n')

    assert check_embedding_model(root).level == "warn"


def test_offline_without_a_model_is_flagged(tmp_path: Path) -> None:
    root = _project(tmp_path, '  backend: "qdrant-local"\n  embedding_offline: true\n')
    check = check_embedding_model(root)

    assert check.level == "warn"
    assert "no local model is declared" in check.detail


def test_installed_bundle_not_wired_in_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    (cache / "acme_model" / "model").mkdir(parents=True)
    monkeypatch.setenv("GRIMOIRE_EMBEDDING_CACHE", str(cache))
    root = _project(tmp_path, '  backend: "qdrant-local"\n')

    check = check_embedding_model(root)

    assert check.level == "info"
    assert "acme_model" in check.detail


def test_plain_vector_project_warns_about_the_download(tmp_path: Path) -> None:
    root = _project(tmp_path, '  backend: "qdrant-local"\n')
    check = check_embedding_model(root)

    assert check.level == "info"
    assert "will download" in check.detail
    assert check.passed is True


def test_no_project_config_is_not_a_failure(tmp_path: Path) -> None:
    check = check_embedding_model(tmp_path)
    assert check.passed is True


def test_check_is_wired_into_run_env_checks(tmp_path: Path) -> None:
    root = _project(tmp_path, '  backend: "auto"\n  vector_database: false\n  retrieval_mode: "lexical"\n')
    ids = {c.name for c in run_env_checks(root)}
    assert "env_embedding_model" in ids
