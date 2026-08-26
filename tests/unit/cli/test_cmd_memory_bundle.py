"""Tests for the ``grimoire memory bundle`` CLI sub-commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.memory import bundle as mod
from grimoire.memory.bundle import export_bundle, install_bundle

runner = CliRunner()


@pytest.fixture
def fake_model(tmp_path: Path) -> Path:
    root = tmp_path / "src-model"
    (root / "1_Pooling").mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"hidden_size": 384}))
    (root / "1_Pooling" / "config.json").write_text(json.dumps({"word_embedding_dimension": 384}))
    (root / "tokenizer.json").write_text('{"vocab": []}')
    return root


@pytest.fixture
def archive(tmp_path: Path, fake_model: Path) -> Path:
    out = tmp_path / "bundle.tar.gz"
    export_bundle(str(fake_model), out, model_name="acme/test-model")
    return out


@pytest.fixture(autouse=True)
def _no_real_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch a real embedding engine from CLI tests."""
    monkeypatch.setattr(mod, "_embed_offline", lambda _d, _m="": ("fastembed", 384))


# ── export ────────────────────────────────────────────────────────────────────


def test_export_writes_archive(tmp_path: Path, fake_model: Path) -> None:
    out = tmp_path / "out.tar.gz"
    result = runner.invoke(app, ["memory", "bundle", "export", "-m", str(fake_model), "-o", str(out)])

    assert result.exit_code == 0, result.output
    assert out.is_file()


def test_export_json_reports_manifest(tmp_path: Path, fake_model: Path) -> None:
    out = tmp_path / "out.tar.gz"
    result = runner.invoke(
        app,
        ["-o", "json", "memory", "bundle", "export", "-m", str(fake_model), "-o", str(out), "--name", "acme/m"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["model"] == "acme/m"
    assert payload["dim"] == 384
    assert payload["archive"] == str(out.resolve())
    assert payload["archive_size"] > 0


def test_export_reports_failure_with_exit_1(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["memory", "bundle", "export", "-m", str(empty), "-o", str(tmp_path / "b.tar.gz")])

    assert result.exit_code == 1


# ── install ───────────────────────────────────────────────────────────────────


def test_install_extracts_bundle(tmp_path: Path, archive: Path) -> None:
    dest = tmp_path / "cache"
    result = runner.invoke(app, ["memory", "bundle", "install", str(archive), "--dest", str(dest)])

    assert result.exit_code == 0, result.output
    assert (dest / "acme_test-model" / "model" / "config.json").is_file()


def test_install_json_reports_target(tmp_path: Path, archive: Path) -> None:
    dest = tmp_path / "cache"
    result = runner.invoke(app, ["-o", "json", "memory", "bundle", "install", str(archive), "--dest", str(dest)])

    payload = json.loads(result.output)
    assert payload["installed"] is True
    assert payload["model"] == "acme/test-model"
    assert payload["configured"] == ""
    assert payload["files"] == 3


def test_install_twice_fails_without_force(tmp_path: Path, archive: Path) -> None:
    dest = tmp_path / "cache"
    runner.invoke(app, ["memory", "bundle", "install", str(archive), "--dest", str(dest)])
    result = runner.invoke(app, ["memory", "bundle", "install", str(archive), "--dest", str(dest)])

    assert result.exit_code == 1


def test_install_configure_updates_project_config(
    tmp_path: Path, archive: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    config = project / "project-context.yaml"
    config.write_text("project:\n  name: Demo\nmemory:\n  backend: auto   # keep\n")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        ["memory", "bundle", "install", str(archive), "--dest", str(tmp_path / "cache"), "--configure"],
    )

    assert result.exit_code == 0, result.output
    text = config.read_text()
    assert "embedding_model:" in text
    assert "# keep" in text


def test_install_missing_archive_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["memory", "bundle", "install", str(tmp_path / "nope.tar.gz")])
    assert result.exit_code != 0


# ── verify ────────────────────────────────────────────────────────────────────


def test_verify_ok_exits_0(tmp_path: Path, archive: Path) -> None:
    installed = install_bundle(archive, dest_root=tmp_path / "cache")
    result = runner.invoke(app, ["memory", "bundle", "verify", str(installed.model_dir)])

    assert result.exit_code == 0, result.output


def test_verify_corrupted_exits_1(tmp_path: Path, archive: Path) -> None:
    installed = install_bundle(archive, dest_root=tmp_path / "cache")
    (installed.model_dir / "tokenizer.json").write_text("tampered")
    result = runner.invoke(app, ["memory", "bundle", "verify", str(installed.model_dir)])

    assert result.exit_code == 1


def test_verify_json_reports_details(tmp_path: Path, archive: Path) -> None:
    installed = install_bundle(archive, dest_root=tmp_path / "cache")
    result = runner.invoke(app, ["-o", "json", "memory", "bundle", "verify", str(installed.model_dir)])

    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["embedded"] is True
    assert payload["embed_dim"] == 384


def test_verify_no_embed_skips_engine(tmp_path: Path, archive: Path) -> None:
    installed = install_bundle(archive, dest_root=tmp_path / "cache")
    result = runner.invoke(
        app, ["-o", "json", "memory", "bundle", "verify", str(installed.model_dir), "--no-embed"],
    )

    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["embedded"] is False


def test_verify_unknown_path_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["memory", "bundle", "verify", str(tmp_path / "absent")])
    assert result.exit_code == 1


# ── where ─────────────────────────────────────────────────────────────────────


def test_where_reports_install_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_EMBEDDING_CACHE", str(tmp_path / "cache"))
    result = runner.invoke(app, ["-o", "json", "memory", "bundle", "where"])

    payload = json.loads(result.output)
    assert payload["install_root"] == str(tmp_path / "cache")
    assert payload["exists"] is False


def test_bundle_is_registered_in_memory_help() -> None:
    result = runner.invoke(app, ["memory", "--help"])
    assert "bundle" in result.output
