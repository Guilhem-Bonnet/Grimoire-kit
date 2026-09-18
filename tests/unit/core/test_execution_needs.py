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


# ── Détection étendue de test-runner (issue #582 lot G3) ────────────────────
#
# Le banc à trois bras (`_scratch/bench-f/analyse-tours-kit-gov.md`, 21 runs)
# a mesuré que 15/21 tâches (tous les exercices Python d'Exercism) ne
# portaient aucun des quatre marqueurs historiques : `need.resolved` restait
# structurellement faux, et `gate run-tests` ne pouvait qu'échouer tôt. Ces
# tests couvrent chaque marqueur de repli, un par un, plus la priorité
# (déclaration > quatre marqueurs historiques > repli) et le cas d'ambiguïté.


def test_pytest_ini_without_pyproject_detects_pytest(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "python -m pytest -q"
    assert resolved.source == "detected"
    assert resolved.evidence == "pytest.ini"


def test_setup_cfg_with_pytest_section_detects_pytest(tmp_path: Path) -> None:
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\ntestpaths = tests\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "python -m pytest -q"
    assert resolved.evidence == "setup.cfg[tool:pytest]"


def test_setup_cfg_without_pytest_section_stays_unresolved(tmp_path: Path) -> None:
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = x\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.resolved is False


def test_tox_ini_detects_pytest(tmp_path: Path) -> None:
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py312\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "python -m pytest -q"
    assert resolved.evidence == "tox.ini"


def test_tests_directory_detects_pytest(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "python -m pytest -q"
    assert resolved.evidence == "tests/"


def test_a_root_level_test_file_detects_pytest(tmp_path: Path) -> None:
    """Issue #582 lot G3 (3) : le cas réel du banc — un exercice Exercism Python

    n'a ni `pyproject.toml` ni `tests/`, seulement un `*_test.py` à la racine.
    """
    (tmp_path / "zipper_test.py").write_text("def test_zipper(): pass\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "python -m pytest -q"
    assert resolved.evidence == "test_*.py|*_test.py"


def test_a_root_level_test_underscore_prefixed_file_detects_pytest(tmp_path: Path) -> None:
    (tmp_path / "test_zipper.py").write_text("def test_zipper(): pass\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "python -m pytest -q"


def test_pom_xml_detects_maven(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "mvn -q test"
    assert resolved.evidence == "pom.xml"


def test_build_gradle_without_wrapper_uses_bare_gradle(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "gradle test"


def test_build_gradle_kts_with_wrapper_uses_the_wrapper(tmp_path: Path) -> None:
    (tmp_path / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "./gradlew test"


def test_cmake_lists_detects_ctest(tmp_path: Path) -> None:
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "ctest --test-dir build"


def test_makefile_with_a_test_target_detects_make_test(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("build:\n\techo hi\ntest: build\n\tpytest\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "make test"


def test_makefile_without_a_test_target_stays_unresolved(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("build:\n\techo hi\n# mentions test but no target\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.resolved is False


def test_mix_exs_detects_mix_test(tmp_path: Path) -> None:
    (tmp_path / "mix.exs").write_text("defmodule Mix.Project do\nend\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "mix test"


def test_gemfile_with_spec_dir_detects_rspec(tmp_path: Path) -> None:
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    (tmp_path / "spec").mkdir()
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "bundle exec rspec"


def test_gemfile_without_spec_dir_stays_unresolved(tmp_path: Path) -> None:
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.resolved is False


def test_csproj_detects_dotnet_test(tmp_path: Path) -> None:
    (tmp_path / "app.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "dotnet test"


def test_sln_detects_dotnet_test(tmp_path: Path) -> None:
    (tmp_path / "solution.sln").write_text("Microsoft Visual Studio Solution File\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "dotnet test"


def test_package_swift_detects_swift_test(tmp_path: Path) -> None:
    (tmp_path / "Package.swift").write_text("// swift-tools-version:5.9\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "swift test"


def test_the_four_historic_markers_still_win_over_the_fallback(tmp_path: Path) -> None:
    """pyproject.toml (marqueur historique) doit gagner sur pom.xml (repli), même présent."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "pytest -q"
    assert resolved.evidence == "pyproject.toml"


def test_declared_command_still_wins_over_a_fallback_marker(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\nneeds:\n  commands:\n    test-runner: tox -e py312\n",
        encoding="utf-8",
    )
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "tox -e py312"
    assert resolved.source == "declared"


def test_an_ambiguous_project_picks_the_first_marker_in_documented_order_never_inventing(
    tmp_path: Path,
) -> None:
    """Deux langages (Maven et Ruby) : le premier de l'ordre documenté l'emporte, pas une devinette."""
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    (tmp_path / "spec").mkdir()
    resolved = resolve_need("test-runner", tmp_path)
    assert resolved.command == "mvn -q test"
    assert resolved.evidence == "pom.xml"


def test_other_needs_are_not_affected_by_the_test_runner_fallback(tmp_path: Path) -> None:
    """Le repli est scopé à `test-runner` : `pom.xml` ne doit pas inventer un `lint`/`build` Java."""
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    resolved = resolve_execution_needs(tmp_path)
    assert resolved["lint"].source == "unresolved"
    assert resolved["build"].source == "unresolved"
