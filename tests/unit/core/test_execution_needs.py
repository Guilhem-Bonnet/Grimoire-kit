"""Résolution des besoins d'exécution (issue #205, lot 2).

Deux sources, jamais une troisième : ``needs.commands`` déclaré dans
``project-context.yaml`` l'emporte toujours sur la détection par marqueur de
projet ; un besoin ni déclaré ni détecté reste ``unresolved`` — jamais une
commande inventée à partir de l'id du besoin.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.execution_needs import (
    EXECUTION_NEED_IDS,
    KNOWN_MARKERS,
    resolve_execution_needs,
    resolve_need,
)


def test_execution_need_ids_are_fixed_and_small() -> None:
    assert set(EXECUTION_NEED_IDS) == {
        "test-runner",
        "lint",
        "typecheck",
        "build",
        "migration-tool",
        "format",
    }


def test_known_markers_are_the_four_documented_manifests() -> None:
    assert set(KNOWN_MARKERS) == {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}


def test_no_marker_no_declaration_is_unresolved(tmp_path: Path) -> None:
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.resolved is False
    assert resolved.command is None
    assert resolved.source == "unresolved"


def test_pyproject_marker_detects_python_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    resolved = resolve_execution_needs(tmp_path)
    assert resolved["test-runner"].command == "pytest -q"
    assert resolved["test-runner"].source == "detected"
    assert resolved["test-runner"].evidence == "pyproject.toml"
    assert resolved["lint"].command == "ruff check ."
    assert resolved["typecheck"].command == "mypy ."
    # Aucune commande de build/migration-tool/format n'est un défaut consensuel pour Python.
    assert resolved["build"].source == "unresolved"
    assert resolved["migration-tool"].source == "unresolved"
    assert resolved["format"].source == "unresolved"


def test_package_json_marker_detects_node_defaults(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    resolved = resolve_execution_needs(tmp_path)
    assert resolved["test-runner"].command == "npm test"
    assert resolved["lint"].command == "npm run lint"
    assert resolved["build"].command == "npm run build"


def test_cargo_toml_marker_detects_rust_defaults(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n", encoding="utf-8")
    resolved = resolve_execution_needs(tmp_path)
    assert resolved["test-runner"].command == "cargo test"
    assert resolved["lint"].command == "cargo clippy"
    assert resolved["build"].command == "cargo build"


def test_go_mod_marker_detects_only_test_runner(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    resolved = resolve_execution_needs(tmp_path)
    assert resolved["test-runner"].command == "go test ./..."
    assert resolved["lint"].source == "unresolved"
    assert resolved["build"].source == "unresolved"


def test_declared_command_overrides_detected_marker(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\nneeds:\n  commands:\n    test-runner: tox -e py312\n",
        encoding="utf-8",
    )
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "tox -e py312"
    assert resolved.source == "declared"
    assert resolved.evidence == "project-context.yaml"


def test_declared_need_without_any_marker_still_resolves(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\nneeds:\n  commands:\n    migration-tool: alembic upgrade head\n",
        encoding="utf-8",
    )
    resolved = resolve_need("migration-tool", tmp_path)
    assert resolved.command == "alembic upgrade head"
    assert resolved.source == "declared"


def test_invalid_project_context_falls_back_to_detection_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "project-context.yaml").write_text("not: [valid, project, context", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "pytest -q"
    assert resolved.source == "detected"


def test_need_id_outside_catalog_is_unresolved(tmp_path: Path) -> None:
    resolved = resolve_need("not-a-real-need", tmp_path)
    assert resolved.resolved is False
    assert resolved.source == "unresolved"
