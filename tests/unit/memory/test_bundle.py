"""Tests for grimoire.memory.bundle — portable embedding-model bundles.

No network, no real model: a fake model directory stands in for the weights.
The offline guarantee is tested for real (sockets blocked), not mocked away.
"""

from __future__ import annotations

import json
import socket
import tarfile
from pathlib import Path

import pytest

from grimoire.memory import bundle as mod
from grimoire.memory.bundle import (
    BUNDLE_SCHEMA,
    BundleError,
    BundleManifest,
    OfflineViolationError,
    default_install_root,
    detect_dim,
    export_bundle,
    install_bundle,
    no_network,
    slugify_model,
    verify_bundle,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_model(tmp_path: Path) -> Path:
    """A directory shaped like a sentence-transformers model."""
    root = tmp_path / "src-model"
    (root / "1_Pooling").mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"hidden_size": 768}))
    (root / "1_Pooling" / "config.json").write_text(json.dumps({"word_embedding_dimension": 384}))
    (root / "model.safetensors").write_bytes(b"weights" * 100)
    (root / "tokenizer.json").write_text('{"vocab": []}')
    # Download metadata that must not travel with the bundle.
    (root / ".cache").mkdir()
    (root / ".cache" / "junk.lock").write_text("x")
    return root


@pytest.fixture
def bundle_archive(tmp_path: Path, fake_model: Path) -> Path:
    out = tmp_path / "bundle.tar.gz"
    export_bundle(str(fake_model), out, model_name="acme/test-model")
    return out


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_slugify_model_makes_one_safe_segment() -> None:
    assert slugify_model("sentence-transformers/all-MiniLM-L6-v2") == "sentence-transformers_all-MiniLM-L6-v2"
    assert "/" not in slugify_model("a/b/c")
    assert slugify_model("///") == "model"


def test_detect_dim_prefers_pooling_config(fake_model: Path) -> None:
    assert detect_dim(fake_model) == 384


def test_detect_dim_falls_back_to_hidden_size(tmp_path: Path) -> None:
    root = tmp_path / "m"
    root.mkdir()
    (root / "config.json").write_text(json.dumps({"hidden_size": 1024}))
    assert detect_dim(root) == 1024


def test_detect_dim_returns_none_when_undeclared(tmp_path: Path) -> None:
    root = tmp_path / "m"
    root.mkdir()
    assert detect_dim(root) is None


def test_default_install_root_honours_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GRIMOIRE_EMBEDDING_CACHE", str(tmp_path / "custom"))
    assert default_install_root() == tmp_path / "custom"


def test_default_install_root_honours_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GRIMOIRE_EMBEDDING_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_install_root() == tmp_path / "xdg" / "grimoire" / "embeddings"


# ── Export ────────────────────────────────────────────────────────────────────


def test_export_writes_archive_and_manifest(tmp_path: Path, fake_model: Path) -> None:
    out = tmp_path / "b.tar.gz"
    manifest = export_bundle(str(fake_model), out, model_name="acme/test-model")

    assert out.is_file()
    assert manifest.model == "acme/test-model"
    assert manifest.dim == 384
    assert manifest.schema == BUNDLE_SCHEMA
    assert manifest.created_at

    names = {f.path for f in manifest.files}
    assert "model/config.json" in names
    assert "model/1_Pooling/config.json" in names
    # .cache/ metadata is not shipped
    assert not any(".cache" in n for n in names)


def test_export_records_real_digests(tmp_path: Path, fake_model: Path) -> None:
    out = tmp_path / "b.tar.gz"
    manifest = export_bundle(str(fake_model), out)
    entry = next(f for f in manifest.files if f.path == "model/tokenizer.json")
    assert entry.sha256 == mod._sha256(fake_model / "tokenizer.json")
    assert entry.size == (fake_model / "tokenizer.json").stat().st_size


def test_export_defaults_manifest_name_to_model(tmp_path: Path, fake_model: Path) -> None:
    manifest = export_bundle(str(fake_model), tmp_path / "b.tar.gz")
    assert manifest.model == str(fake_model)


def test_export_rejects_empty_model_dir(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BundleError, match="nothing to bundle"):
        export_bundle(str(empty), tmp_path / "b.tar.gz")


def test_export_without_hub_gives_actionable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_hub(model: str, dest: Path) -> None:
        raise BundleError(
            f"'{model}' is not a local directory and huggingface-hub is not installed.\n"
            "  → install it on the connected machine: pip install huggingface-hub"
        )

    monkeypatch.setattr(mod, "_snapshot_from_hub", _no_hub)
    with pytest.raises(BundleError, match="huggingface-hub is not installed"):
        export_bundle("acme/not-local", tmp_path / "b.tar.gz")


# ── Install ───────────────────────────────────────────────────────────────────


def test_install_extracts_and_verifies(tmp_path: Path, bundle_archive: Path) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")

    assert installed.model_dir.is_dir()
    assert (installed.model_dir / "config.json").is_file()
    assert installed.manifest.model == "acme/test-model"
    assert installed.model_dir.parent.name == "acme_test-model"


def test_install_uses_default_root_when_dest_omitted(
    tmp_path: Path, bundle_archive: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRIMOIRE_EMBEDDING_CACHE", str(tmp_path / "cache"))
    installed = install_bundle(bundle_archive)
    assert (tmp_path / "cache") in installed.model_dir.parents


def test_install_refuses_to_overwrite_without_force(tmp_path: Path, bundle_archive: Path) -> None:
    dest = tmp_path / "cache"
    install_bundle(bundle_archive, dest_root=dest)
    with pytest.raises(BundleError, match="--force"):
        install_bundle(bundle_archive, dest_root=dest)


def test_install_replaces_with_force(tmp_path: Path, bundle_archive: Path) -> None:
    dest = tmp_path / "cache"
    first = install_bundle(bundle_archive, dest_root=dest)
    (first.model_dir / "stale.txt").write_text("leftover")
    second = install_bundle(bundle_archive, dest_root=dest, force=True)
    assert not (second.model_dir / "stale.txt").exists()


def test_install_rejects_missing_archive(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="Bundle not found"):
        install_bundle(tmp_path / "nope.tar.gz", dest_root=tmp_path / "cache")


def test_install_fails_closed_on_tampered_file(tmp_path: Path, fake_model: Path) -> None:
    """A file swapped after export must abort the install, not warn."""
    staged = tmp_path / "grimoire-embedding-bundle"
    model_dir = staged / "model"
    model_dir.mkdir(parents=True)
    (model_dir / "weights.bin").write_bytes(b"honest")
    manifest = BundleManifest(
        model="acme/tampered",
        dim=384,
        files=(mod.BundleFile(path="model/weights.bin", sha256="0" * 64, size=6),),
        created_at="2026-01-01T00:00:00",
    )
    (staged / "manifest.json").write_text(json.dumps(manifest.to_dict()))

    archive = tmp_path / "tampered.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staged, arcname="grimoire-embedding-bundle")

    with pytest.raises(BundleError, match="wrong digest"):
        install_bundle(archive, dest_root=tmp_path / "cache")
    assert not (tmp_path / "cache" / "acme_tampered").exists()


def test_install_fails_closed_on_missing_declared_file(tmp_path: Path) -> None:
    staged = tmp_path / "grimoire-embedding-bundle"
    (staged / "model").mkdir(parents=True)
    manifest = BundleManifest(
        model="acme/incomplete",
        dim=None,
        files=(mod.BundleFile(path="model/absent.bin", sha256="a" * 64, size=1),),
    )
    (staged / "manifest.json").write_text(json.dumps(manifest.to_dict()))
    archive = tmp_path / "incomplete.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staged, arcname="grimoire-embedding-bundle")

    with pytest.raises(BundleError, match="absent"):
        install_bundle(archive, dest_root=tmp_path / "cache")


def test_install_rejects_traversal_path_in_manifest(tmp_path: Path) -> None:
    staged = tmp_path / "grimoire-embedding-bundle"
    (staged / "model").mkdir(parents=True)
    manifest = BundleManifest(
        model="acme/evil",
        dim=None,
        files=(mod.BundleFile(path="../../etc/passwd", sha256="b" * 64, size=1),),
    )
    (staged / "manifest.json").write_text(json.dumps(manifest.to_dict()))
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staged, arcname="grimoire-embedding-bundle")

    with pytest.raises(BundleError, match="unsafe path"):
        install_bundle(archive, dest_root=tmp_path / "cache")


def test_install_rejects_archive_without_expected_root(tmp_path: Path) -> None:
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "file.txt").write_text("x")
    archive = tmp_path / "stray.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stray, arcname="somewhere-else")

    with pytest.raises(BundleError, match="does not contain"):
        install_bundle(archive, dest_root=tmp_path / "cache")


def test_install_rejects_unknown_schema(tmp_path: Path) -> None:
    staged = tmp_path / "grimoire-embedding-bundle"
    (staged / "model").mkdir(parents=True)
    (staged / "manifest.json").write_text(json.dumps({"schema": "something/9", "model": "x", "files": []}))
    archive = tmp_path / "future.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staged, arcname="grimoire-embedding-bundle")

    with pytest.raises(BundleError, match="Unsupported bundle schema"):
        install_bundle(archive, dest_root=tmp_path / "cache")


# ── Offline guarantee ─────────────────────────────────────────────────────────


def test_no_network_blocks_outbound_sockets() -> None:
    with no_network(), pytest.raises(OfflineViolationError):
        socket.create_connection(("example.invalid", 80), timeout=0.1)


def test_no_network_restores_the_socket_api() -> None:
    original = socket.socket.connect
    with no_network():
        pass
    assert socket.socket.connect is original


def test_no_network_restores_even_on_error() -> None:
    original = socket.create_connection
    with pytest.raises(ValueError, match="boom"), no_network():
        raise ValueError("boom")
    assert socket.create_connection is original


def test_verify_reports_engine_reaching_the_network(
    tmp_path: Path, bundle_archive: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engine that silently falls back to a download must fail verification."""
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")

    def _sneaky(model_dir: Path) -> tuple[str, int]:
        with no_network():
            socket.create_connection(("huggingface.co", 443), timeout=0.1)
        return "unreachable", 0

    monkeypatch.setattr(mod, "_embed_offline", _sneaky)
    report = verify_bundle(installed.model_dir)

    assert not report.ok
    assert not report.embedded
    assert any("tried to reach the network" in e for e in report.errors)


def test_offline_env_sets_and_restores_hub_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    with mod._offline_env():
        import os

        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    import os

    assert "HF_HUB_OFFLINE" not in os.environ
    assert os.environ["TRANSFORMERS_OFFLINE"] == "0"


# ── Verify ────────────────────────────────────────────────────────────────────


def test_verify_ok_on_intact_install(tmp_path: Path, bundle_archive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")
    monkeypatch.setattr(mod, "_embed_offline", lambda _d: ("sentence-transformers", 384))
    report = verify_bundle(installed.model_dir)

    assert report.ok
    assert report.embedded
    assert report.embed_dim == 384
    assert report.files_checked == 4


def test_verify_accepts_the_bundle_root_too(tmp_path: Path, bundle_archive: Path) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")
    report = verify_bundle(installed.model_dir.parent, embed=False)
    assert report.ok


def test_verify_detects_corruption_after_install(tmp_path: Path, bundle_archive: Path) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")
    (installed.model_dir / "tokenizer.json").write_text('{"vocab": ["tampered"]}')
    report = verify_bundle(installed.model_dir, embed=False)

    assert not report.ok
    assert "model/tokenizer.json" in report.mismatched


def test_verify_detects_deletion_after_install(tmp_path: Path, bundle_archive: Path) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")
    (installed.model_dir / "model.safetensors").unlink()
    report = verify_bundle(installed.model_dir, embed=False)

    assert not report.ok
    assert "model/model.safetensors" in report.missing


def test_verify_skips_embed_when_digests_already_broken(
    tmp_path: Path, bundle_archive: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")
    (installed.model_dir / "tokenizer.json").write_text("tampered")

    called = False

    def _tracker(model_dir: Path) -> tuple[str, int]:
        nonlocal called
        called = True
        return "x", 1

    monkeypatch.setattr(mod, "_embed_offline", _tracker)
    verify_bundle(installed.model_dir)
    assert not called


def test_verify_flags_dimension_mismatch(tmp_path: Path, bundle_archive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")
    monkeypatch.setattr(mod, "_embed_offline", lambda _d: ("sentence-transformers", 768))
    report = verify_bundle(installed.model_dir)

    assert not report.ok
    assert any("dimension mismatch" in e for e in report.errors)


def test_verify_reports_absent_engine_without_crashing(
    tmp_path: Path, bundle_archive: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")

    def _no_engine(model_dir: Path) -> tuple[str, int]:
        raise BundleError("No embedding engine installed — cannot prove the model loads.")

    monkeypatch.setattr(mod, "_embed_offline", _no_engine)
    report = verify_bundle(installed.model_dir)

    assert not report.ok
    assert not report.embedded
    assert any("No embedding engine" in e for e in report.errors)


def test_verify_to_dict_is_json_serialisable(tmp_path: Path, bundle_archive: Path) -> None:
    installed = install_bundle(bundle_archive, dest_root=tmp_path / "cache")
    payload = json.loads(json.dumps(verify_bundle(installed.model_dir, embed=False).to_dict()))
    assert payload["ok"] is True
    assert payload["model"] == "acme/test-model"


# ── Project wiring ────────────────────────────────────────────────────────────


def test_configure_project_preserves_comments(tmp_path: Path) -> None:
    config = tmp_path / "project-context.yaml"
    config.write_text(
        "# Grimoire project context\n"
        "project:\n"
        '  name: "Demo"          # inline comment\n'
        "memory:\n"
        "  backend: auto         # keep me\n",
    )
    mod.configure_project(config, tmp_path / "models" / "acme" / "model")
    text = config.read_text()

    assert "# Grimoire project context" in text
    assert "# keep me" in text
    assert "# inline comment" in text
    assert "embedding_model:" in text
    assert str(tmp_path / "models" / "acme" / "model") in text


def test_configure_project_creates_memory_section(tmp_path: Path) -> None:
    config = tmp_path / "project-context.yaml"
    config.write_text("project:\n  name: Demo\n")
    mod.configure_project(config, tmp_path / "m")
    assert "embedding_model:" in config.read_text()


def test_configure_project_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="No project config"):
        mod.configure_project(tmp_path / "absent.yaml", tmp_path / "m")


def test_configure_project_rejects_empty_file(tmp_path: Path) -> None:
    config = tmp_path / "project-context.yaml"
    config.write_text("")
    with pytest.raises(BundleError, match="empty"):
        mod.configure_project(config, tmp_path / "m")
