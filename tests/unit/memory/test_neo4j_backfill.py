"""P15b — garde-fous du rattrapage Neo4j (racine de code propre au projet, aperçu sans écriture)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.memory.neo4j_backfill import BackfillRefusalError, backfill_project, resolve_code_prefixes


class TestResolveCodePrefixes:
    def test_subdirectory_becomes_a_relative_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        assert resolve_code_prefixes(tmp_path, ["src", " src/pkg/ "]) == ["src/", "src/pkg/"]

    @pytest.mark.parametrize("raw", [".", "./", ""])
    def test_project_root_is_refused(self, tmp_path: Path, raw: str) -> None:
        with pytest.raises(BackfillRefusalError):
            resolve_code_prefixes(tmp_path, [raw] if raw else [raw, " "])

    def test_outside_or_missing_directory_is_refused(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        project.mkdir()
        (tmp_path / "other").mkdir()
        with pytest.raises(BackfillRefusalError, match="hors du projet"):
            resolve_code_prefixes(project, ["../other"])
        with pytest.raises(BackfillRefusalError, match="introuvable"):
            resolve_code_prefixes(project, ["src"])


class _Graph:
    project = "p"
    key_prefix = "p::"

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[str] = []

    def _read(self, statement: str, **_params: Any) -> list[dict[str, Any]]:
        self.reads.append(statement)
        return [{"count": 0}] if "count(" in statement else []

    def _execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        self.writes.append(statement)


def test_preview_issues_reads_only(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    graph = _Graph()
    report = backfill_project(graph, collections=["c"], project_root=tmp_path, code_prefixes=["src/"], apply=False)  # type: ignore[arg-type]
    assert graph.writes == [] and graph.reads
    assert report["conflicts"] == [] and report["conflicts_total"] == 0


def test_backfill_needs_a_project_key(tmp_path: Path) -> None:
    graph = _Graph()
    graph.project = ""
    with pytest.raises(ValueError, match="project key"):
        backfill_project(graph, collections=[], project_root=tmp_path, code_prefixes=[])  # type: ignore[arg-type]
