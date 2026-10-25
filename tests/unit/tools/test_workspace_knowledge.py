"""Espace Savoir (P24) : entrées paginées, fiche d'une entrée, recherche de flotte par rang.

Trois promesses tenues côté serveur, chacune prouvée ici sans navigateur :

- ``memory/entries`` lit le store par tranches et rend un curseur : jamais un
  ``get_all`` complet ; il sépare souvenirs et projections ;
- ``memory/entry`` rend la provenance, le remplaçant (et ce que l'entrée
  remplace) et les nœuds liés ;
- dans un emplacement partagé, les entrées des AUTRES projets ne sont que
  comptées, jamais rendues ; la recherche de flotte classe par rang fusionné
  (RRF), plus par score brut.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_knowledge as wk
from grimoire.tools import workspace_memory as wm
from grimoire.tools.workspace_routes import PREFIX, workspace_get


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)


def _project(root: Path, name: str, *, backend: str = "local") -> MemoryManager:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\nmemory:\n  backend: {backend}\n", encoding="utf-8",
    )
    return MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)


def _shared_projects(tmp_path: Path, names: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Projets qui déclarent la même collection Qdrant : un seul store, servi ici par un fichier local."""
    shared = tmp_path / "store-partage.json"
    roots = []
    for name in names:
        root = tmp_path / name.lower()
        root.mkdir(parents=True, exist_ok=True)
        (root / "project-context.yaml").write_text(
            f"project:\n  name: {name}\nmemory:\n  backend: qdrant-server\n"
            "  qdrant_url: http://qdrant.lan:6333/\n  collection_prefix: flotte\n",
            encoding="utf-8",
        )
        roots.append(root)

    def _from_config(cls: type, cfg: Any, *, project_root: Path | None = None) -> MemoryManager:
        return MemoryManager(LocalMemoryBackend(shared), project_name=cfg.project.name, project_root=project_root)

    monkeypatch.setattr(MemoryManager, "from_config", classmethod(_from_config))
    reg.save_registry([{"slug": r.name, "name": n, "path": str(r)} for r, n in zip(roots, names, strict=True)])
    return roots


class TestEntriesPage:
    def test_pages_are_read_by_slices_and_never_as_a_complete_listing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        a = tmp_path / "projet-a"
        mgr = _project(a, "Alpha")
        for i in range(5):
            mgr.remember("decisions", "agent", f"décision numéro {i} sur le cache")
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])
        calls: list[tuple[int, int | None]] = []
        real = LocalMemoryBackend.get_all

        def spy(self: LocalMemoryBackend, *, user_id: str = "", offset: int = 0, limit: int | None = None) -> Any:
            calls.append((offset, limit))
            return real(self, user_id=user_id, offset=offset, limit=limit)

        monkeypatch.setattr(LocalMemoryBackend, "get_all", spy)

        first = wk.memory_entries(a, limit=2)
        second = wk.memory_entries(a, limit=2, offset=first["nextOffset"])
        last = wk.memory_entries(a, limit=2, offset=second["nextOffset"])

        assert [len(p["entries"]) for p in (first, second, last)] == [2, 2, 1]
        assert first["nextOffset"] == 2 and second["nextOffset"] == 4
        assert last["nextOffset"] is None and last["exhausted"] is True
        ids = [e["id"] for p in (first, second, last) for e in p["entries"]]
        assert len(set(ids)) == 5, "aucune entrée sautée ni répétée d'une page à l'autre"
        assert calls and all(limit is not None for _, limit in calls), "jamais get_all(limit=None)"

    def test_scan_budget_returns_a_cursor_instead_of_reading_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        a = tmp_path / "projet-a"
        mgr = _project(a, "Alpha")
        for i in range(6):
            mgr.store(f"fait libre {i}")
        monkeypatch.setattr(wk, "SCAN_BATCH", 2)
        monkeypatch.setattr(wk, "MAX_SCAN", 4)

        page = wk.memory_entries(a, types="decision")

        assert page["entries"] == []
        assert page["scanned"] == 4
        assert page["nextOffset"] == 4 and page["exhausted"] is False
        assert page["counts"]["type"] == {"untyped": 4}
        assert page["hidden"]["filtered"] == 4

    def test_souvenir_and_projection_are_separated(self, tmp_path: Path) -> None:
        from grimoire.memory.projections import sync_docs_projection

        a = tmp_path / "projet-a"
        mgr = _project(a, "Alpha")
        (a / "docs").mkdir()
        (a / "docs" / "guide.md").write_text("# Guide\n\nle guide du portail\n", encoding="utf-8")
        sync_docs_projection(mgr, project_root=a, paths=[Path("docs")])
        mgr.remember("failures", "agent", "Le portail tombe sans cache. Redémarrer le service.")

        everything = wk.memory_entries(a)
        souvenirs = wk.memory_entries(a, kind="souvenir")
        projections = wk.memory_entries(a, kind="projection")

        assert everything["counts"]["kind"]["souvenir"] == 1
        assert everything["counts"]["kind"]["projection"] >= 1
        assert {e["kind"] for e in souvenirs["entries"]} == {"souvenir"}
        assert {e["kind"] for e in projections["entries"]} == {"projection"}
        assert {e["type"] for e in projections["entries"]} == {"docs"}
        incident = souvenirs["entries"][0]
        assert incident["type"] == "incident"
        assert incident["title"] == "Le portail tombe sans cache."
        assert incident["excerpt"] == "Redémarrer le service."
        assert incident["provenance"]["source"]["kind"]
        assert incident["projectName"] == "Alpha"

    def test_type_date_and_superseded_filters(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        mgr = _project(a, "Alpha")
        old = mgr.remember("decisions", "agent", "Qdrant épinglé en 1.17.1.")
        new = mgr.remember("decisions", "agent", "Qdrant épinglé en 1.19.1, une mineure à la fois.")
        mgr.supersede(old.id, new.id, reason="montée de version")
        mgr.store("un fait sans type")

        default = wk.memory_entries(a, types="decision")
        assert [e["id"] for e in default["entries"]] == [new.id]
        assert default["hidden"]["superseded"] == 1
        assert default["entries"][0]["supersession"]["replaces"] == [{"id": old.id, "title": "Qdrant épinglé en 1.17.1."}]

        with_old = wk.memory_entries(a, types="decision", include_superseded=True)
        states = {e["id"]: e["supersession"] for e in with_old["entries"]}
        assert states[old.id]["state"] == "superseded"
        assert states[old.id]["replacedBy"] == {"id": new.id, "title": new.text, "found": True}

        assert wk.memory_entries(a, since="2999-01-01")["entries"] == []
        assert len(wk.memory_entries(a, since="2000-01-01")["entries"]) == 2

    def test_retracted_entries_are_counted_not_listed(self, tmp_path: Path) -> None:
        from grimoire.memory.supersession import RETRACTED_KEY

        a = tmp_path / "projet-a"
        mgr = _project(a, "Alpha")
        gone = mgr.store("retiré")
        mgr.backend.update(gone.id, metadata={**gone.metadata, RETRACTED_KEY: {"at": "2026-10-01"}})

        page = wk.memory_entries(a)

        assert page["entries"] == []
        assert page["hidden"]["retracted"] == 1

    def test_unreadable_targets_name_their_reason(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        a.mkdir()
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "perdu", "name": "Perdu", "path": str(tmp_path / "n-existe-pas")},
        ])

        assert "non initialisé" in wk.memory_entries(a)["reason"]
        assert "dossier introuvable" in wk.memory_entries(a, slug="perdu")["reason"]
        assert "absent du registre" in wk.memory_entries(a, slug="inconnu")["reason"]
        (a / "project-context.yaml").write_text("project: [\n", encoding="utf-8")
        assert "illisible" in wk.memory_entries(a)["reason"]

    def test_failed_store_read_is_a_reason_never_an_empty_page(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        a = tmp_path / "projet-a"
        _project(a, "Alpha").store("une entrée")

        def broken(self: LocalMemoryBackend, **_: Any) -> Any:
            raise OSError("disque débranché")

        monkeypatch.setattr(LocalMemoryBackend, "get_all", broken)
        page = wk.memory_entries(a)

        assert page["entries"] == []
        assert "disque débranché" in page["reason"]
        assert page["exhausted"] is False

    def test_bad_numbers_fall_back_to_bounds(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _project(a, "Alpha").store("une entrée")

        page = wk.memory_entries(a, offset="moins un", limit="9999")

        assert page["offset"] == 0
        assert page["limit"] == wk.MAX_PAGE


class TestSharedLocation:
    def test_other_projects_entries_are_only_counted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        alpha, beta = _shared_projects(tmp_path, ("Alpha", "Beta"), monkeypatch)
        # Un manager par écriture : le fichier partagé est relu à chaque ouverture.
        mine = wk.open_readonly(alpha)[1].store("secret alpha : le cache redis")
        mgr_beta = wk.open_readonly(beta)[1]
        mgr_beta.store("secret beta : le cache redis")
        mgr_beta.store("secret beta : la file kafka")
        orphan = wk.open_readonly(alpha)[1].backend.store("entrée sans projet du cache", metadata={"source_kind": "memory"})

        page = wk.memory_entries(alpha)

        assert [e["id"] for e in page["entries"]] == [mine.id]
        assert page["hidden"]["otherProjects"] == [{"name": "Beta", "count": 2}]
        assert page["hidden"]["unattributed"] == 1
        assert page["sharedWith"] == ["Beta"]
        assert "secret beta" not in repr(page)

        refused = wk.memory_entry(alpha, orphan.id)
        assert refused["found"] is False and refused["hidden"] is True and refused["entry"] is None

        found = wm.memory_search(alpha, "cache", None)
        assert [r["id"] for r in found["results"]] == [mine.id]
        assert found["projects"][0]["hiddenOther"] == [{"name": "Beta", "count": 1}]
        assert found["projects"][0]["hiddenUnattributed"] == 1
        assert "secret beta" not in repr(found)

    def test_ownership_without_neighbours_shows_unattributed_entries(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        mgr = _project(a, "Alpha")
        loose = mgr.backend.store("ancienne entrée sans projet", metadata={"source_kind": "memory"})
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        page = wk.memory_entries(a)

        assert [e["id"] for e in page["entries"]] == [loose.id]
        assert page["sharedWith"] == []


class TestEntryDetail:
    def test_provenance_replacement_chain_and_linked_nodes(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        mgr = _project(a, "Alpha")
        first = mgr.store("Snapshots dans le volume.", task_ref="GAO-qdrant-001", actor="guilhem", session_id="s-42")
        second = mgr.store("Snapshots hors volume.", tags=("qdrant",))
        third = mgr.store("Snapshots sauvegardés avant chaque montée.")
        mgr.supersede(first.id, second.id)
        mgr.supersede(second.id, third.id)
        reg.save_registry([{"slug": "projet-a", "name": "Alpha", "path": str(a)}])

        detail = wk.memory_entry(a, first.id, slug="projet-a")

        assert detail["found"] is True
        entry = detail["entry"]
        assert entry["supersession"]["state"] == "superseded"
        assert [link["id"] for link in entry["chain"]] == [second.id, third.id]
        assert entry["actor"] == "guilhem" and entry["sessionId"] == "s-42"
        assert entry["taskRef"] == "GAO-qdrant-001"
        kinds = {(link["kind"], link["id"]) for link in entry["links"]}
        assert ("tâche", "GAO-qdrant-001") in kinds
        assert ("remplaçant", second.id) in kinds
        assert [row["op"] for row in entry["journal"]] == ["store", "supersede"]
        assert entry["provenance"]["cycle"] == "remplacé"

        middle = wk.memory_entry(a, second.id)["entry"]
        assert middle["supersession"]["replaces"] == [{"id": first.id, "title": "Snapshots dans le volume."}]
        assert ("tag", "qdrant") in {(link["kind"], link["id"]) for link in middle["links"]}

    def test_missing_ids_and_unknown_entries_say_why(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _project(a, "Alpha")

        assert wk.memory_entry(a, "")["reason"] == "identifiant d'entrée requis"
        assert "introuvable" in wk.memory_entry(a, "pas-un-id")["reason"]
        assert "absent du registre" in wk.memory_entry(a, "x", slug="fantome")["reason"]
        (a / "project-context.yaml").write_text("project: [\n", encoding="utf-8")
        assert "illisible" in wk.memory_entry(a, "x")["reason"]


class TestFleetRanking:
    def test_fusion_ranks_by_position_not_by_raw_score(self) -> None:
        cosine = {"slug": "a", "results": [
            {"id": "a1", "projectSlug": "a", "score": 0.91}, {"id": "a2", "projectSlug": "a", "score": 0.90},
        ]}
        bm25 = {"slug": "b", "results": [
            {"id": "b1", "projectSlug": "b", "score": 12.0}, {"id": "b2", "projectSlug": "b", "score": 11.0},
            {"id": "b3", "projectSlug": "b", "score": 10.0},
        ]}

        fused = wm.fuse_by_rank([bm25, cosine])

        # Par score brut, les trois BM25 passeraient devant les deux cosinus.
        assert [r["id"] for r in fused] == ["a1", "b1", "a2", "b2", "b3"]
        assert fused[0]["fleetScore"] == pytest.approx(1 / (wm.RRF_K + 1))
        assert fused[0]["score"] == 0.91, "le score brut reste porté, nommé par scoreKind"

    def test_search_interleaves_projects_with_incomparable_scores(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        b = tmp_path / "projet-b"
        lexical = _project(a, "Alpha", backend="lexical")
        for text in ("portail rapide", "portail lent", "portail moyen"):
            lexical.store(text)
        _project(b, "Beta").store("portail unique de beta")
        reg.save_registry([
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
        ])

        found = wm.memory_search(a, "portail", "all")

        assert found["ranking"] == "rrf"
        slugs = [r["projectSlug"] for r in found["results"]]
        assert slugs[:2] == ["projet-a", "projet-b"], "le premier de chaque projet avant le deuxième d'Alpha"
        assert [r["projectRank"] for r in found["results"]][:2] == [1, 1]
        assert {r["scoreKind"] for r in found["results"]} == {"bm25", "backend"}

    def test_served_project_absent_from_registry_is_still_searched(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        _project(a, "Alpha").store("le portail de l'atelier")

        found = wm.memory_search(a, "portail", None)

        assert found["count"] == 1
        assert found["results"][0]["projectName"] == "Alpha"


class TestRoutes:
    def test_entries_and_entry_are_get_routes(self, tmp_path: Path) -> None:
        a = tmp_path / "projet-a"
        kept = _project(a, "Alpha").remember("decisions", "agent", "Une décision servie par la route.")

        page = workspace_get(a, f"{PREFIX}memory/entries", {"limit": ["5"], "kind": ["souvenir"]})
        detail = workspace_get(a, f"{PREFIX}memory/entry", {"id": [kept.id]})

        assert page["schemaVersion"] == wk.ENTRIES_SCHEMA_VERSION
        assert [e["id"] for e in page["entries"]] == [kept.id]
        assert detail["schemaVersion"] == wk.ENTRY_SCHEMA_VERSION
        assert detail["entry"]["type"] == "decision"


class TestTagGraphRemoved:
    """P24 : le graphe par tags partagés reliait des entrées par coïncidence d'étiquette ; il est retiré."""

    ROOT = Path(__file__).resolve().parents[3]

    def test_generator_no_longer_builds_a_tag_graph(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("gsd_p24", self.ROOT / "scripts" / "gen-site-data.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert not hasattr(module, "_memory_graph")
        source = (self.ROOT / "scripts" / "gen-site-data.py").read_text(encoding="utf-8")
        body = source.split("def build_memory(")[1].split("\ndef ")[0]
        assert '"graph"' not in body

    def test_pages_and_snapshots_carry_no_tag_graph(self) -> None:
        import json

        page = (self.ROOT / "web" / "memory.html").read_text(encoding="utf-8")
        assert 'id="graph"' not in page and "renderGraph" not in page
        assert "arêtes = tags partagés" not in page
        cockpit = (self.ROOT / "web" / "workspace" / "spaces" / "memoire.js").read_text(encoding="utf-8")
        assert "tags partagés" not in cockpit.replace("Aucun graphe par tags partagés", "")
        snapshots = [self.ROOT / "web" / "data" / "memory.json", *sorted((self.ROOT / "web" / "data" / "projects").glob("*/memory.json"))]
        assert snapshots
        for snapshot in snapshots:
            assert "graph" not in json.loads(snapshot.read_text(encoding="utf-8")), snapshot
