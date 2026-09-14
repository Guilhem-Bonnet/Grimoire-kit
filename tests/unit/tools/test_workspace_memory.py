"""Agrégation mémoire multi-projets (#172, dernier volet de « Cockpit — du
générateur statique au portefeuille actif », Refs #468).

`memory_link_status()` (`grimoire.tools.memory_link`) reste mono-projet ;
`grimoire.tools.workspace_memory` l'appelle une fois par projet du registre et
agrège côté serveur — ce que la Flotte de Piloter faisait jusqu'ici en
boucle côté navigateur (`Promise.allSettled`). Un projet illisible doit
apparaître avec sa raison, jamais être compté comme un store vide en
silence (leçon #264).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_memory as wm


def _init_project(root: Path, name: str, *, backend: str = "local") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: {backend}\n", encoding="utf-8",
    )


def _store(root: Path, text: str) -> None:
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    cfg = GrimoireConfig.from_yaml(root / "project-context.yaml")
    mgr = MemoryManager.from_config(cfg, project_root=root)
    mgr.store(text)


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


class TestMemoryOverview:
    def test_two_readable_projects_and_one_unreadable(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        _store(a, "une décision importante sur le cache")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
            {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
        ])

        overview = wm.memory_overview(a, "all")

        by_slug = {p["slug"]: p for p in overview["projects"]}
        assert by_slug["projet-a"]["entries"] == 1
        assert by_slug["projet-a"]["state"] == "ok"
        assert by_slug["projet-a"]["lastWrite"]
        assert by_slug["projet-a"]["lexicalIndex"] == "absent"
        assert by_slug["projet-b"]["entries"] == 0
        assert by_slug["projet-b"]["reason"] is None
        assert by_slug["projet-c"]["state"] == "unreadable"
        assert by_slug["projet-c"]["reason"]
        assert overview["summary"]["count"] == 3
        assert overview["summary"]["readable"] == 2
        assert overview["summary"]["totalEntries"] == 1

    def test_default_scope_is_this_project_only(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
        ])

        overview = wm.memory_overview(a, None)

        assert [p["slug"] for p in overview["projects"]] == ["projet-a"]

    def test_explicit_slug_list_ignores_the_rest_of_the_registry(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
        ])

        overview = wm.memory_overview(a, "projet-b")

        assert [p["slug"] for p in overview["projects"]] == ["projet-b"]

    def test_uninitialized_project_names_its_own_reason(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        a.mkdir()
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        overview = wm.memory_overview(a, "all")

        row = overview["projects"][0]
        assert row["state"] == "uninitialized"
        assert "non initialisé" in row["reason"]

    def test_no_project_matches_yields_empty_list_not_an_error(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _init_project(a, "Alpha")
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        overview = wm.memory_overview(a, "projet-jamais-enregistre")

        assert overview["projects"] == []
        assert overview["summary"]["count"] == 0


class TestMemorySearch:
    def test_cross_project_search_labels_each_result(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        _store(a, "le cache redis a un ticket ouvert")
        _store(b, "rien a voir ici")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
        ])

        result = wm.memory_search(a, "redis", "all")

        assert result["count"] == 1
        assert result["results"][0]["projectSlug"] == "projet-a"
        assert result["results"][0]["projectName"] == "Alpha"

    def test_empty_query_returns_no_results_without_calling_any_backend(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _init_project(a, "Alpha")
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        result = wm.memory_search(a, "   ", "all")

        assert result["count"] == 0
        assert result["results"] == []
        assert result["projects"] == []

    def test_order_is_stable_across_registry_order(self, tmp_path: Path) -> None:
        """Deux projets à égalité de score : le tri par slug rend l'ordre
        indépendant de l'ordre du registre, pas d'un hasard de dict."""
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        _init_project(a, "Alpha")
        _init_project(b, "Beta")
        _store(a, "budget projet")
        _store(b, "budget projet")
        reg.save_registry([
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
        ])

        result = wm.memory_search(a, "budget", "all")

        assert [r["projectSlug"] for r in result["results"]] == ["projet-a", "projet-b"]

    def test_unreadable_project_is_named_not_silently_skipped(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _init_project(a, "Alpha")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
        ])

        result = wm.memory_search(a, "budget", "all")

        by_slug = {p["slug"]: p for p in result["projects"]}
        assert by_slug["projet-c"]["reason"]
        assert by_slug["projet-c"]["count"] == 0
