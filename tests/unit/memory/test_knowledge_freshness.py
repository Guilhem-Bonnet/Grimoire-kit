"""EKP-E1 — chaque résultat de mémoire dit d'où il vient et s'il est encore vrai.

Cadrage : ``docs/cadrage/portail-connaissance.md`` §5.1 (fiche de fait), §5.2
(fraîcheur calculée à la lecture), §5.3 (inconnu n'est pas vrai), D2, D5, D11.
Les tests passent par les surfaces publiques (gestionnaire, CLI, outil MCP),
pour échouer sur ``origin/main`` par une assertion et non par un import.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.memory.projections import sync_docs_projection

pytestmark = pytest.mark.covers("cli:memory supersede", "cli:memory search")

runner = CliRunner()

QUERY = "portail"


def _manager(root: Path, *, companion: bool = False) -> MemoryManager:
    primary = LocalMemoryBackend(root / "primary.json")
    lexical = LocalMemoryBackend(root / "lexical.json") if companion else None
    return MemoryManager(primary, lexical_companion=lexical, project_root=root, project_name="demo")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _synced_doc(root: Path, body: str = "Le portail rend des extraits sourcés.\n") -> MemoryManager:
    _write(root / "docs" / "portail.md", "# Portail\n\n" + body)
    mgr = _manager(root)
    sync_docs_projection(mgr, project_root=root, paths=[Path("docs")])
    return mgr


def _by_id(results: list[Any], entry_id: str) -> Any:
    return next(r for r in results if r.id == entry_id)


class TestFreshnessOfFiles:
    def test_synced_doc_is_up_to_date(self, tmp_path: Path) -> None:
        mgr = _synced_doc(tmp_path)
        entry = _by_id(mgr.hybrid_search(QUERY), "docs:docs/portail.md")
        assert entry.freshness == "a_jour"
        assert entry.source == "docs/portail.md"
        assert entry.provenance["source"]["empreinte"]
        assert entry.provenance["schema"] == "grimoire.knowledge.fact/v1"

    def test_doc_modified_after_sync_is_outdated(self, tmp_path: Path) -> None:
        mgr = _synced_doc(tmp_path)
        _write(tmp_path / "docs" / "portail.md", "# Portail\n\nLe portail a changé de sens.\n")
        entry = _by_id(mgr.hybrid_search(QUERY), "docs:docs/portail.md")
        assert entry.freshness == "perime"
        assert entry.provenance["date_changement"]

    def test_resync_after_change_is_up_to_date_again(self, tmp_path: Path) -> None:
        mgr = _synced_doc(tmp_path)
        _write(tmp_path / "docs" / "portail.md", "# Portail\n\nLe portail a changé de sens.\n")
        sync_docs_projection(mgr, project_root=tmp_path, paths=[Path("docs")])
        assert _by_id(mgr.hybrid_search(QUERY), "docs:docs/portail.md").freshness == "a_jour"

    def test_change_beyond_the_truncation_still_resyncs(self, tmp_path: Path) -> None:
        """Le texte projeté est tronqué à 8 000 caractères : un changement plus loin
        ne change pas ``content_hash``, mais l'empreinte de la source, si."""
        body = "portail " + "x" * 9000 + "\n"
        mgr = _synced_doc(tmp_path, body)
        _write(tmp_path / "docs" / "portail.md", "# Portail\n\nportail " + "x" * 9000 + " fin modifiée\n")
        entry = _by_id(mgr.hybrid_search(QUERY), "docs:docs/portail.md")
        assert entry.freshness == "perime"
        stats = sync_docs_projection(mgr, project_root=tmp_path, paths=[Path("docs")])
        assert stats["upserted"] == 1
        assert _by_id(mgr.hybrid_search(QUERY), "docs:docs/portail.md").freshness == "a_jour"

    def test_unchanged_resync_writes_nothing(self, tmp_path: Path) -> None:
        mgr = _synced_doc(tmp_path)
        assert sync_docs_projection(mgr, project_root=tmp_path, paths=[Path("docs")])["upserted"] == 0

    def test_deleted_source_is_gone_and_not_served_by_default(self, tmp_path: Path) -> None:
        mgr = _synced_doc(tmp_path)
        (tmp_path / "docs" / "portail.md").unlink()
        assert mgr.hybrid_search(QUERY) == []
        assert mgr.hidden_stale == 1
        shown = mgr.hybrid_search(QUERY, include_stale=True)
        assert [e.freshness for e in shown] == ["source_disparue"]

    def test_unreadable_source_is_never_up_to_date(self, tmp_path: Path) -> None:
        """Un répertoire à la place du fichier : la relecture échoue, l'état n'est pas ``à jour``."""
        mgr = _synced_doc(tmp_path)
        (tmp_path / "docs" / "portail.md").unlink()
        (tmp_path / "docs" / "portail.md").mkdir()
        entry = _by_id(mgr.hybrid_search(QUERY), "docs:docs/portail.md")
        assert entry.freshness == "non_verifie"
        assert entry.provenance["fraicheur_raison"]

    def test_source_too_large_to_reread_is_never_up_to_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _synced_doc(tmp_path)
        monkeypatch.setattr("grimoire.memory.knowledge.MAX_SOURCE_BYTES", 8)
        entry = _by_id(mgr.hybrid_search(QUERY), "docs:docs/portail.md")
        assert entry.freshness == "non_verifie"

    def test_source_outside_the_project_is_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.md"
        _write(outside, "portail secret\n")
        root = tmp_path / "proj"
        root.mkdir()
        mgr = _manager(root)
        mgr.upsert("esc", "portail hors projet", metadata={"file_path": "../outside.md"})
        entry = _by_id(mgr.hybrid_search(QUERY), "esc")
        assert entry.freshness == "non_verifie"

    def test_entry_indexed_before_provenance_tracking_is_not_up_to_date(self, tmp_path: Path) -> None:
        """Pas d'empreinte d'indexation : on ne peut pas dire que la source n'a pas changé."""
        _write(tmp_path / "docs" / "a.md", "portail\n")
        mgr = _manager(tmp_path)
        mgr.backend.upsert("old", "portail ancien", metadata={"file_path": "docs/a.md", "project_name": "demo", "source_kind": "projection"})
        assert _by_id(mgr.hybrid_search(QUERY), "old").freshness == "non_verifie"


class TestFreeTextIsNeverUpToDate:
    def test_free_text_entry_is_unverified(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.store("Décision orale : le portail reste local.")
        entry = mgr.hybrid_search(QUERY)[0]
        assert entry.freshness == "non_verifie"
        assert entry.source == "saisie libre"
        assert entry.provenance["source"]["kind"] == "saisie libre"

    def test_old_free_text_becomes_a_hypothesis_and_is_served_last(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        old = (datetime.now(UTC) - timedelta(days=400)).isoformat(timespec="seconds")
        mgr.upsert("vieux", "portail : vieille idée", metadata={"indexed_at": old})
        mgr.upsert("neuf", "portail : idée récente")
        results = mgr.hybrid_search(QUERY, include_superseded=True)
        assert _by_id(results, "vieux").freshness == "hypothese"
        assert _by_id(results, "neuf").freshness == "non_verifie"
        assert [e.id for e in results][-1] == "vieux"

    def test_confirmation_keeps_a_fact_unverified_not_current(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.upsert("confirme", "portail confirmé hier", metadata={"confirmed_at": datetime.now(UTC).isoformat()})
        assert _by_id(mgr.hybrid_search(QUERY), "confirme").freshness == "non_verifie"

    def test_remember_without_source_is_free_text_with_source_is_not(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.remember("decisions", "agent-x", "portail : décision A")
        mgr.remember("decisions", "agent-x", "portail : décision B", source="task:T-42")
        by_text = {e.text: e for e in mgr.hybrid_search(QUERY)}
        assert by_text["portail : décision A"].source == "saisie libre"
        assert by_text["portail : décision B"].source == "task:T-42"
        assert by_text["portail : décision B"].provenance["source"]["kind"] == "tâche"
        assert by_text["portail : décision B"].freshness == "non_verifie"


class TestEveryResultHasASource:
    def test_no_result_without_a_source(self, tmp_path: Path) -> None:
        mgr = _synced_doc(tmp_path)
        mgr.store("portail : saisie libre")
        mgr.remember("decisions", "a", "portail : typé", source="session:s1")
        # Entrée écrite sans passer par le manager : aucune métadonnée du tout.
        mgr.backend.upsert("brut", "portail brut sans métadonnées")
        results = mgr.hybrid_search(QUERY, include_superseded=True, limit=10)
        assert len(results) == 4
        assert all(r.source for r in results)
        assert _by_id(results, "brut").source == "inconnue"
        assert _by_id(results, "brut").freshness == "non_verifie"

    def test_plain_search_and_taxonomy_search_carry_the_same_fields(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.store("portail : un fait")
        assert mgr.search(QUERY)[0].source == "saisie libre"
        assert mgr.search_taxonomy(QUERY)[0].source == "saisie libre"

    def test_fused_search_carries_the_fields(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path, companion=True)
        mgr.store("portail : un fait")
        results = mgr.hybrid_search(QUERY)
        assert results and results[0].source == "saisie libre"

    def test_legacy_json_export_keys_are_kept(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.store("portail : un fait")
        payload = mgr.hybrid_search(QUERY)[0].to_dict()
        for key in ("id", "text", "user_id", "tags", "metadata", "created_at", "updated_at",
                    "score", "source", "provenance", "freshness", "task_ref"):
            assert key in payload
        assert payload["freshness"] != "current"


class TestSupersede:
    def _two(self, root: Path) -> MemoryManager:
        mgr = _manager(root)
        mgr.upsert("old", "portail : décision ancienne, Weaviate")
        mgr.upsert("new", "portail : décision nouvelle, Qdrant")
        return mgr

    def test_cli_supersede_hides_then_include_superseded_shows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = self._two(tmp_path)
        monkeypatch.setattr("grimoire.cli.cmd_memory._load_manager", lambda *a, **k: mgr)
        done = runner.invoke(app, ["-o", "json", "memory", "supersede", "old", "new"])
        assert done.exit_code == 0, done.output
        assert json.loads(done.output)["superseded"] is True

        hidden = runner.invoke(app, ["-o", "json", "memory", "search", QUERY, "--no-hybrid"])
        assert [r["id"] for r in json.loads(hidden.output)] == ["new"]

        shown = runner.invoke(app, ["-o", "json", "memory", "search", QUERY, "--no-hybrid", "--include-superseded"])
        rows = {r["id"]: r for r in json.loads(shown.output)}
        assert set(rows) == {"old", "new"}
        assert rows["old"]["provenance"]["cycle"] == "remplacé"
        assert rows["old"]["provenance"]["remplace_par"] == "new"

    def test_cli_supersede_refuses_an_unknown_replacement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = self._two(tmp_path)
        monkeypatch.setattr("grimoire.cli.cmd_memory._load_manager", lambda *a, **k: mgr)
        refused = runner.invoke(app, ["-o", "json", "memory", "supersede", "old", "ghost"])
        assert refused.exit_code == 1
        assert json.loads(refused.output)["superseded"] is False
        assert {r.id for r in mgr.hybrid_search(QUERY)} == {"old", "new"}


class TestCliSearch:
    def test_json_search_exposes_source_freshness_and_dates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _synced_doc(tmp_path)
        mgr.store("portail : saisie")
        monkeypatch.setattr("grimoire.cli.cmd_memory._load_manager", lambda *a, **k: mgr)
        out = runner.invoke(app, ["-o", "json", "memory", "search", QUERY, "--no-hybrid"])
        assert out.exit_code == 0, out.output
        rows = {r["id"]: r for r in json.loads(out.output)}
        doc = rows["docs:docs/portail.md"]
        assert doc["freshness"] == "a_jour"
        assert doc["source"] == "docs/portail.md"
        assert doc["provenance"]["date_indexation"]
        assert doc["provenance"]["date_source"]
        free = next(r for r in rows.values() if r["text"] == "portail : saisie")
        assert free["freshness"] == "non_verifie"

    def test_text_search_prints_label_and_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr = _synced_doc(tmp_path)
        monkeypatch.setattr("grimoire.cli.cmd_memory._load_manager", lambda *a, **k: mgr)
        out = runner.invoke(app, ["memory", "search", QUERY, "--no-hybrid"], terminal_width=200)
        assert "à jour" in out.output
        assert "docs/portail.md" in out.output

    def test_stale_hidden_count_is_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mgr = _synced_doc(tmp_path)
        (tmp_path / "docs" / "portail.md").unlink()
        monkeypatch.setattr("grimoire.cli.cmd_memory._load_manager", lambda *a, **k: mgr)
        out = runner.invoke(app, ["memory", "search", QUERY, "--no-hybrid"], terminal_width=200)
        assert "source disparue" in out.output
        shown = runner.invoke(app, ["-o", "json", "memory", "search", QUERY, "--no-hybrid", "--include-stale"])
        assert json.loads(shown.output)[0]["freshness"] == "source_disparue"


class TestProfileInService:
    def test_standard_profile_does_not_promise_unconditional_fusion(self) -> None:
        from grimoire.memory.profiles import PROFILES

        summary = PROFILES["standard"].summary
        assert "quand" in summary or "seulement" in summary

    def test_lexical_only_manager_reports_no_fusion(self, tmp_path: Path) -> None:
        from grimoire.memory.profiles import retrieval_in_service

        report = retrieval_in_service(_manager(tmp_path).prefers_hybrid, "standard")
        assert report["in_service"] == "lexical"
        assert report["honored"] is False
        assert "aucune fusion" in str(report["label"])

    def test_fused_manager_reports_fusion(self, tmp_path: Path) -> None:
        from grimoire.memory.profiles import retrieval_in_service

        report = retrieval_in_service(_manager(tmp_path, companion=True).prefers_hybrid, "standard")
        assert report["in_service"] == "hybrid"
        assert report["honored"] is True


class TestMcp:
    @pytest.fixture()
    def project(self, tmp_path: Path) -> Path:
        pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: test-mcp\n  type: webapp\n  stack:\n    - python\n"
            "user:\n  name: Guilhem\n  language: Français\n  skill_level: expert\n"
            "memory:\n  backend: local\nagents:\n  archetype: minimal\n",
            encoding="utf-8",
        )
        (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
        return tmp_path

    @staticmethod
    def _body(result: Any) -> Any:
        if isinstance(result, str):
            return json.loads(result)
        return json.loads("".join(getattr(block, "text", "") for block in result.content))

    def test_search_results_carry_source_and_freshness(self, project: Path) -> None:
        from grimoire.mcp.server import grimoire_memory_search, grimoire_memory_store

        grimoire_memory_store("portail : une décision orale", project_path=str(project))
        grimoire_memory_store("portail : une décision tracée", project_path=str(project), source="task:T-7")
        data = self._body(grimoire_memory_search(QUERY, project_path=str(project)))
        by_text = {r["text"]: r for r in data["results"]}
        assert by_text["portail : une décision orale"]["source"] == "saisie libre"
        assert by_text["portail : une décision orale"]["freshness"] == "non_verifie"
        assert by_text["portail : une décision tracée"]["source"] == "task:T-7"
        assert data["retrieval_in_service"]["in_service"] == "lexical"
        assert data["hidden_source_disparue"] == 0

    def test_supersede_tool_hides_then_include_superseded_shows(self, project: Path) -> None:
        from grimoire.mcp.server import grimoire_memory_search, grimoire_memory_store, grimoire_memory_supersede

        old = self._body(grimoire_memory_store("portail : décision ancienne", project_path=str(project)))["id"]
        new = self._body(grimoire_memory_store("portail : décision nouvelle", project_path=str(project)))["id"]
        done = self._body(grimoire_memory_supersede(old, new, project_path=str(project)))
        assert done["superseded"] is True
        live = self._body(grimoire_memory_search(QUERY, project_path=str(project)))
        assert [r["id"] for r in live["results"]] == [new]
        every = self._body(grimoire_memory_search(QUERY, project_path=str(project), include_superseded=True))
        assert {r["id"] for r in every["results"]} == {old, new}

    def test_supersede_tool_refuses_an_unknown_replacement(self, project: Path) -> None:
        from grimoire.mcp.server import grimoire_memory_store, grimoire_memory_supersede

        old = self._body(grimoire_memory_store("portail : décision", project_path=str(project)))["id"]
        body = self._body(grimoire_memory_supersede(old, "ghost", project_path=str(project)))
        assert "error" in body
