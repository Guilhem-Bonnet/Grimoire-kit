"""Tests for grimoire.tools.ext_manager — gestionnaire d'extensions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.tools.ext_manager import (
    ExtensionError,
    install_blueprint_from_registry,
    install_extension,
    install_from_registry,
    list_installed,
    load_manifest,
    main,
    publish_blueprint,
    publish_extension,
    remove_extension,
    validate_manifest,
)


def make_manifest(**overrides: object) -> dict:
    manifest: dict = {
        "manifestVersion": 1,
        "id": "demo-ext",
        "name": "Demo Extension",
        "version": "0.1.0",
        "description": "Extension de test.",
        "license": "MIT",
        "authors": [{"name": "Test"}],
        "compat": {"kit": ">=3.11", "manifest": 1},
        "provides": {"agents": ["artifacts/demo.agent.md"]},
        "patterns": {"implements": ["ORC-01"]},
        "permissions": {
            "filesystem": "artifacts",
            "network": False,
            "hooks": [],
            "memory": "none",
        },
        "install": {
            "steps": [
                {
                    "kind": "copy",
                    "from": "artifacts/demo.agent.md",
                    "to": ".github/agents/demo.agent.md",
                }
            ]
        },
    }
    manifest.update(overrides)
    return manifest


@pytest.fixture
def ext_dir(tmp_path: Path) -> Path:
    ext = tmp_path / "demo-ext"
    (ext / "artifacts").mkdir(parents=True)
    (ext / "artifacts" / "demo.agent.md").write_text("# Demo agent\n", encoding="utf-8")
    (ext / "extension.json").write_text(
        json.dumps(make_manifest()), encoding="utf-8"
    )
    return ext


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


class TestLoadManifest:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        with pytest.raises(ExtensionError, match="introuvable"):
            load_manifest(tmp_path)

    def test_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "extension.json").write_text("{", encoding="utf-8")
        with pytest.raises(ExtensionError, match="JSON invalide"):
            load_manifest(tmp_path)

    def test_loads_valid(self, ext_dir: Path) -> None:
        assert load_manifest(ext_dir)["id"] == "demo-ext"


class TestValidateManifest:
    def test_valid(self, ext_dir: Path) -> None:
        assert validate_manifest(load_manifest(ext_dir), ext_dir) == []

    def test_missing_required_key(self, ext_dir: Path) -> None:
        manifest = make_manifest()
        del manifest["patterns"]
        errors = validate_manifest(manifest, ext_dir)
        assert any("patterns" in e for e in errors)

    def test_empty_implements(self, ext_dir: Path) -> None:
        manifest = make_manifest(patterns={"implements": []})
        errors = validate_manifest(manifest, ext_dir)
        assert any("implements vide" in e for e in errors)

    def test_kind_valid(self, ext_dir: Path) -> None:
        for kind in ("flow-adapter", "mcp-toolbox", "observability", "capability"):
            manifest = make_manifest(kind=kind)
            assert validate_manifest(manifest, ext_dir) == []

    def test_kind_invalid(self, ext_dir: Path) -> None:
        manifest = make_manifest(kind="plugin")
        errors = validate_manifest(manifest, ext_dir)
        assert any("kind invalide" in e for e in errors)

    def test_kind_optional(self, ext_dir: Path) -> None:
        # kind est optionnel (manifestVersion 1) : son absence reste valide.
        manifest = make_manifest()
        assert "kind" not in manifest
        assert validate_manifest(manifest, ext_dir) == []

    def test_bad_pattern_id(self, ext_dir: Path) -> None:
        manifest = make_manifest(patterns={"implements": ["XXX-99"]})
        errors = validate_manifest(manifest, ext_dir)
        assert any("pattern id invalide" in e for e in errors)

    def test_path_traversal_rejected(self, ext_dir: Path) -> None:
        manifest = make_manifest(
            install={
                "steps": [
                    {"kind": "copy", "from": "../evil", "to": "/etc/passwd"}
                ]
            }
        )
        errors = validate_manifest(manifest, ext_dir)
        assert any("from non sûr" in e for e in errors)
        assert any("to non sûr" in e for e in errors)

    def test_missing_source_file(self, ext_dir: Path) -> None:
        manifest = make_manifest(provides={"agents": ["artifacts/absent.md"]})
        errors = validate_manifest(manifest, ext_dir)
        assert any("fichier absent" in e for e in errors)

    def test_bad_permissions(self, ext_dir: Path) -> None:
        manifest = make_manifest(
            permissions={
                "filesystem": "everything",
                "network": "yes",
                "hooks": [],
                "memory": "none",
            }
        )
        errors = validate_manifest(manifest, ext_dir)
        assert any("filesystem invalide" in e for e in errors)
        assert any("network" in e for e in errors)


class TestInstall:
    def test_copies_and_records(self, ext_dir: Path, project_root: Path) -> None:
        result = install_extension(ext_dir, project_root)
        assert result.extension_id == "demo-ext"
        assert (project_root / ".github" / "agents" / "demo.agent.md").is_file()
        state = list_installed(project_root)
        assert state["demo-ext"]["version"] == "0.1.0"
        assert state["demo-ext"]["files"] == [".github/agents/demo.agent.md"]

    def test_refuses_double_install(self, ext_dir: Path, project_root: Path) -> None:
        install_extension(ext_dir, project_root)
        with pytest.raises(ExtensionError, match="déjà installée"):
            install_extension(ext_dir, project_root)

    def test_force_reinstalls(self, ext_dir: Path, project_root: Path) -> None:
        install_extension(ext_dir, project_root)
        result = install_extension(ext_dir, project_root, force=True)
        assert result.copied == (".github/agents/demo.agent.md",)

    def test_invalid_manifest_blocks_install(
        self, ext_dir: Path, project_root: Path
    ) -> None:
        (ext_dir / "extension.json").write_text(
            json.dumps(make_manifest(patterns={"implements": []})), encoding="utf-8"
        )
        with pytest.raises(ExtensionError, match="manifeste invalide"):
            install_extension(ext_dir, project_root)

    def test_skip_scripts(self, ext_dir: Path, project_root: Path) -> None:
        (ext_dir / "hook.sh").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        manifest = make_manifest()
        manifest["install"]["steps"].append({"kind": "script", "path": "hook.sh"})
        (ext_dir / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = install_extension(ext_dir, project_root, skip_scripts=True)
        assert result.skipped == ("script:hook.sh",)

    def test_failing_script_raises(self, ext_dir: Path, project_root: Path) -> None:
        (ext_dir / "hook.sh").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        manifest = make_manifest()
        manifest["install"]["steps"].append({"kind": "script", "path": "hook.sh"})
        (ext_dir / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ExtensionError, match="a échoué"):
            install_extension(ext_dir, project_root)


class TestRegistry:
    def test_publish_creates_archive_and_index(
        self, ext_dir: Path, tmp_path: Path
    ) -> None:
        registry = tmp_path / "registry"
        release = publish_extension(ext_dir, registry)
        assert release["version"] == "0.1.0"
        assert (registry / "dist" / "demo-ext-0.1.0.tar.gz").is_file()
        index = json.loads((registry / "registry.json").read_text(encoding="utf-8"))
        assert index["extensions"]["demo-ext"]["latest"] == "0.1.0"
        assert release["checksum"].startswith("sha256:")

    def test_publish_is_deterministic(self, ext_dir: Path, tmp_path: Path) -> None:
        registry = tmp_path / "registry"
        first = publish_extension(ext_dir, registry)
        second = publish_extension(ext_dir, registry)
        assert first["checksum"] == second["checksum"]
        index = json.loads((registry / "registry.json").read_text(encoding="utf-8"))
        assert len(index["extensions"]["demo-ext"]["versions"]) == 1

    def test_install_from_registry(
        self, ext_dir: Path, project_root: Path, tmp_path: Path
    ) -> None:
        registry = tmp_path / "registry"
        publish_extension(ext_dir, registry)
        result = install_from_registry("demo-ext", registry, project_root)
        assert result.extension_id == "demo-ext"
        assert (project_root / ".github" / "agents" / "demo.agent.md").is_file()
        state = list_installed(project_root)
        assert state["demo-ext"]["source"].startswith("registry:")
        assert state["demo-ext"]["checksum"].startswith("sha256:")
        remove_extension("demo-ext", project_root)
        assert list_installed(project_root) == {}

    def test_tampered_archive_rejected(
        self, ext_dir: Path, project_root: Path, tmp_path: Path
    ) -> None:
        registry = tmp_path / "registry"
        publish_extension(ext_dir, registry)
        archive = registry / "dist" / "demo-ext-0.1.0.tar.gz"
        archive.write_bytes(archive.read_bytes() + b"tamper")
        with pytest.raises(ExtensionError, match="checksum invalide"):
            install_from_registry("demo-ext", registry, project_root)

    def test_unknown_extension_in_registry(
        self, ext_dir: Path, project_root: Path, tmp_path: Path
    ) -> None:
        registry = tmp_path / "registry"
        publish_extension(ext_dir, registry)
        with pytest.raises(ExtensionError, match="absente du registry"):
            install_from_registry("ghost", registry, project_root)


class TestRemove:
    def test_removes_files_and_state(self, ext_dir: Path, project_root: Path) -> None:
        install_extension(ext_dir, project_root)
        remove_extension("demo-ext", project_root)
        assert not (project_root / ".github" / "agents" / "demo.agent.md").exists()
        assert list_installed(project_root) == {}

    def test_unknown_extension(self, project_root: Path) -> None:
        with pytest.raises(ExtensionError, match="non installée"):
            remove_extension("ghost", project_root)


class TestMainCLI:
    def test_add_list_verify_remove_cycle(
        self, ext_dir: Path, project_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = ["--project-root", str(project_root)]
        assert main([*root, "add", str(ext_dir)]) == 0
        assert "Installé : demo-ext v0.1.0" in capsys.readouterr().out
        assert main([*root, "list"]) == 0
        assert "demo-ext v0.1.0" in capsys.readouterr().out
        assert main([*root, "verify", "demo-ext"]) == 0
        assert "pas de script de vérification" in capsys.readouterr().out
        assert main([*root, "remove", "demo-ext"]) == 0
        assert main([*root, "list"]) == 0
        assert "Aucune extension installée." in capsys.readouterr().out

    def test_publish_then_add_from_registry(
        self, ext_dir: Path, project_root: Path, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        registry = tmp_path / "registry"
        root = ["--project-root", str(project_root)]
        assert main([*root, "publish", str(ext_dir), "--registry", str(registry)]) == 0
        assert "Publié : Demo Extension 0.1.0" in capsys.readouterr().out
        assert main([*root, "add", "demo-ext", "--registry", str(registry)]) == 0
        assert (project_root / ".github" / "agents" / "demo.agent.md").is_file()

    def test_error_returns_1(
        self, project_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--project-root", str(project_root), "remove", "ghost"]) == 1
        assert "Erreur" in capsys.readouterr().err


def make_blueprint(tmp_path: Path) -> Path:
    bp = {
        "blueprintVersion": 1,
        "id": "demo-flow",
        "name": "Demo Flow",
        "catalogRef": {"version": "1.0.0"},
        "extensions": [{"id": "demo-ext", "version": ">=0.1.0"}],
        "nodes": [
            {"id": "a", "kind": "pattern", "ref": "ORC-01", "label": "Orch",
             "pins": [{"id": "out", "direction": "out", "contract": "task-envelope"}]}
        ],
        "edges": [],
    }
    path = tmp_path / "demo-flow.blueprint.json"
    path.write_text(json.dumps(bp), encoding="utf-8")
    return path


class TestBlueprintRegistry:
    def test_publish_and_install(
        self, tmp_path: Path, project_root: Path
    ) -> None:
        registry = tmp_path / "registry"
        entry = publish_blueprint(make_blueprint(tmp_path), registry)
        assert entry["summary"]["nodes"] == 1
        assert entry["checksum"].startswith("sha256:")
        index = json.loads((registry / "registry.json").read_text(encoding="utf-8"))
        assert "demo-flow" in index["blueprints"]

        result = install_blueprint_from_registry("demo-flow", registry, project_root)
        assert result["missingExtensions"] == ["demo-ext"]
        assert (project_root / "_grimoire" / "blueprints" / "demo-flow.blueprint.json").is_file()

    def test_install_refuses_overwrite_without_force(
        self, tmp_path: Path, project_root: Path
    ) -> None:
        registry = tmp_path / "registry"
        publish_blueprint(make_blueprint(tmp_path), registry)
        install_blueprint_from_registry("demo-flow", registry, project_root)
        with pytest.raises(ExtensionError, match="déjà présent"):
            install_blueprint_from_registry("demo-flow", registry, project_root)
        install_blueprint_from_registry("demo-flow", registry, project_root, force=True)

    def test_tampered_blueprint_rejected(
        self, tmp_path: Path, project_root: Path
    ) -> None:
        registry = tmp_path / "registry"
        publish_blueprint(make_blueprint(tmp_path), registry)
        target = registry / "blueprints" / "demo-flow.blueprint.json"
        target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with pytest.raises(ExtensionError, match="checksum invalide"):
            install_blueprint_from_registry("demo-flow", registry, project_root)

    def test_invalid_blueprint_refused_at_publish(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.blueprint.json"
        path.write_text(json.dumps({"blueprintVersion": 1, "id": "X"}), encoding="utf-8")
        with pytest.raises(ExtensionError, match="blueprint invalide"):
            publish_blueprint(path, tmp_path / "registry")

    def test_cli_publish_and_add_blueprint(
        self, tmp_path: Path, project_root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        registry = tmp_path / "registry"
        bp = make_blueprint(tmp_path)
        root = ["--project-root", str(project_root)]
        assert main([*root, "publish", str(bp), "--registry", str(registry)]) == 0
        assert "Blueprint publié : Demo Flow" in capsys.readouterr().out
        assert main([*root, "add-blueprint", "demo-flow", "--registry", str(registry)]) == 0
        out = capsys.readouterr().out
        assert "Blueprint installé : demo-flow" in out
        assert "demo-ext" in out
