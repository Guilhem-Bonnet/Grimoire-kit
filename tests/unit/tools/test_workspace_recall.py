"""Reçu de rappel lisible et rejouable (P27), prouvé sans navigateur.

- une lecture rattachée à une tâche garde sa requête (caviardée) ; hors tâche, non ;
- ``recall_receipts`` rend les reçus d'une tâche, chaque entrée servie décrite
  telle qu'elle est aujourd'hui (titre, portée, état, fraîcheur) ; un journal
  absent ou sans lecture de la tâche se dit, jamais par un chiffre inventé ;
- ``recall_replay`` relance la même lecture et rend le différentiel (apparue,
  remplacée, déclassée, disparue) ; une requête libre se rejoue pareil ;
- rien de tout cela n'écrit un reçu : le cockpit lit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.config import GrimoireConfig
from grimoire.memory.manager import MemoryManager
from grimoire.memory.reads_log import READS_RELPATH, record_read
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.recall import DEFAULT_LEDGER_RELPATH, build_task_recall
from grimoire.tools import workspace_api
from grimoire.tools import workspace_recall as wr
from grimoire.tools.workspace_routes import PREFIX, workspace_get

PIN_OLD = "Épinglage du Qdrant partagé en 1.17.1."
PIN_NEW = "Qdrant partagé épinglé en 1.19.1, une mineure à la fois."
SNAPSHOTS = "Piège Qdrant : snapshots hors volume, perdus à la recréation du conteneur."


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for key in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)


def _project(root: Path, name: str = "Forge") -> MemoryManager:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(f"project:\n  name: {name}\nmemory:\n  backend: local\n", encoding="utf-8")
    return MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)


def _lines(root: Path) -> list[dict[str, Any]]:
    path = root / READS_RELPATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _task(root: Path, title: str) -> str:
    ledger = MissionLedger(root / DEFAULT_LEDGER_RELPATH)
    mission = ledger.create_mission(title="Poste", origin="test")
    return ledger.create_task(mission.id, title, acceptance=("version montée",)).id


@pytest.fixture
def forge(tmp_path: Path) -> tuple[Path, MemoryManager, dict[str, str]]:
    root = tmp_path / "forge"
    mgr = _project(root)
    ids = {
        "old": mgr.remember("decisions", "agent", PIN_OLD).id,
        "new": mgr.remember("decisions", "agent", PIN_NEW).id,
        "snap": mgr.remember("failures", "agent", SNAPSHOTS).id,
    }
    return root, mgr, ids


# ── Le reçu garde la requête d'une lecture de tâche ─────────────────────────


class TestQueryKept:
    def test_a_task_bound_read_keeps_its_query_redacted(self, tmp_path: Path) -> None:
        record_read(tmp_path, mode="search", surface="mcp", served=[], query="qdrant  token=ghp_" + "a" * 36,
                    task_ref="T-1")
        (row,) = _lines(tmp_path)
        assert row["query"].startswith("qdrant token=")
        assert "ghp_" + "a" * 36 not in row["query"]
        assert row["query_chars"] == len("qdrant  token=ghp_" + "a" * 36)

    def test_a_read_outside_any_task_keeps_only_its_length(self, tmp_path: Path) -> None:
        record_read(tmp_path, mode="search", surface="mcp", served=[], query="donnée personnelle")
        (row,) = _lines(tmp_path)
        assert "query" not in row
        assert row["query_chars"] == len("donnée personnelle")

    def test_the_query_is_bounded(self, tmp_path: Path) -> None:
        record_read(tmp_path, mode="search", surface="mcp", served=[], query="q" * 1000, task_ref="T-1")
        (row,) = _lines(tmp_path)
        assert len(row["query"]) == 300


# ── Les reçus d'une tâche ───────────────────────────────────────────────────


class TestReceipts:
    def test_no_journal_is_said_not_zeroed(self, tmp_path: Path) -> None:
        root = tmp_path / "vide"
        _project(root)
        payload = wr.recall_receipts(root, task_id="T-1")
        assert payload["logPresent"] is False
        assert payload["receipts"] == []
        assert payload["reason"] is None

    def test_a_journal_without_this_task_has_no_receipt(self, forge: tuple[Path, MemoryManager, dict[str, str]]) -> None:
        root, mgr, _ids = forge
        mgr.search("qdrant")  # hors tâche
        payload = wr.recall_receipts(root, task_id="T-absente")
        assert payload["logPresent"] is True
        assert payload["receipts"] == []
        assert payload["total"] == 0

    def test_task_recall_receipt_is_described_as_it_is_today(
        self, forge: tuple[Path, MemoryManager, dict[str, str]],
    ) -> None:
        root, mgr, ids = forge
        task_id = _task(root, "Mettre à jour le Qdrant partagé du poste")
        build_task_recall(root, task_id, memory_manager=mgr)
        before = len(_lines(root))

        payload = wr.recall_receipts(root, task_id=task_id)

        assert len(_lines(root)) == before, "lire les reçus n'écrit aucun reçu"
        (receipt,) = payload["receipts"]
        assert receipt["mode"] == "task_recall"
        assert receipt["taskRef"] == task_id
        assert receipt["taskTitle"] == "Mettre à jour le Qdrant partagé du poste"
        assert receipt["query"].startswith("Mettre à jour le Qdrant")
        assert receipt["ts"]
        assert receipt["replayable"] is True
        served = {s["id"]: s for s in receipt["served"]}
        assert set(served) <= set(ids.values()) and served
        first = receipt["served"][0]
        assert first["rank"] == 1 and isinstance(first["score"], float)
        assert first["scope"] == "project"
        assert first["state"] == "live"
        assert first["freshness"] and first["freshnessLabel"]
        assert first["title"]

    def test_state_follows_the_memory_of_today(self, forge: tuple[Path, MemoryManager, dict[str, str]],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
        root, mgr, ids = forge
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "T-9")
        mgr.search("Qdrant partagé", limit=5)
        monkeypatch.delenv("GRIMOIRE_TASK_ID")
        mgr.supersede(ids["old"], ids["new"])
        mgr.retract(ids["snap"], reason="obsolète")

        (receipt,) = wr.recall_receipts(root, task_id="T-9")["receipts"]
        states = {s["id"]: s["state"] for s in receipt["served"]}
        assert states.get(ids["old"]) == "superseded"
        assert states.get(ids["snap"]) == "retracted"
        replaced = next(s for s in receipt["served"] if s["id"] == ids["old"])
        assert replaced["replacedBy"]["id"] == ids["new"]

    def test_missing_other_project_and_old_lines(self, forge: tuple[Path, MemoryManager, dict[str, str]]) -> None:
        root, mgr, _ids = forge
        other = mgr.store("Note d'un autre projet sur Qdrant", metadata={"project_name": "Autre"})
        line = {"ts": "2026-10-01T10:00:00+00:00", "mode": "search", "surface": "mcp", "task_ref": "T-2",
                "query_chars": 6, "served": [{"id": other.id, "rank": 1, "score": 0.5},
                                              {"id": "disparu", "rank": 2, "score": 0.1}]}
        path = root / READS_RELPATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("pas du json\n" + json.dumps(line) + "\n")

        (receipt,) = wr.recall_receipts(root, task_id="T-2")["receipts"]
        hidden, missing = receipt["served"]
        assert hidden["scope"] == "other" and hidden["title"] is None and hidden["hidden"] is True
        assert missing["state"] == "missing" and missing["found"] is False
        assert receipt["replayable"] is False
        assert "requête non conservée" in receipt["notReplayable"]

    def test_project_listing_newest_first_and_bench_hidden(self, forge: tuple[Path, MemoryManager, dict[str, str]],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
        root, mgr, _ids = forge
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "T-1")
        mgr.search("Qdrant")
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "T-2")
        mgr.search("snapshots")
        mgr.surface = "bench"
        mgr.search("banc")
        payload = wr.recall_receipts(root, limit=1)
        assert payload["total"] == 2
        assert [r["taskRef"] for r in payload["receipts"]] == ["T-2"]

    def test_unknown_slug_names_its_reason(self, tmp_path: Path) -> None:
        payload = wr.recall_receipts(tmp_path, slug="absent")
        assert payload["reason"] == "projet absent du registre de la machine"

    def test_unreadable_memory_keeps_the_receipt_and_says_why(self, tmp_path: Path) -> None:
        root = tmp_path / "sans-config"
        record_read(root, mode="search", surface="mcp", served=[], query="q", task_ref="T-1")
        payload = wr.recall_receipts(root, task_id="T-1")
        assert payload["memoryReason"].startswith("projet non initialisé")
        (receipt,) = payload["receipts"]
        assert receipt["replayable"] is False


# ── Le rejeu ────────────────────────────────────────────────────────────────


class TestDiff:
    def test_every_kind(self) -> None:
        states = {"r": "superseded", "x": "retracted", "v": "live"}
        diff = wr.recall_diff(
            [("a", 1), ("r", 2), ("b", 3), ("v", 4), ("x", 5), ("c", 6)],
            [("b", 1), ("a", 2), ("n", 3), ("c", 6)],
            lambda i: states.get(i, "missing"),
        )
        kinds = {r["id"]: r["kind"] for r in diff["rows"]}
        assert kinds == {"n": "appeared", "r": "replaced", "x": "replaced", "a": "demoted", "v": "vanished",
                         "b": "promoted", "c": "same"}
        assert diff["counts"]["replaced"] == 2
        assert diff["changed"] == 6
        assert diff["rows"][0]["kind"] == "appeared"


class TestReplay:
    def test_replaying_a_receipt_shows_what_changed_and_writes_nothing(
        self, forge: tuple[Path, MemoryManager, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root, mgr, ids = forge
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "T-3")
        mgr.search("Qdrant partagé", limit=5)
        monkeypatch.delenv("GRIMOIRE_TASK_ID")
        mgr.supersede(ids["old"], ids["new"])
        fresh = mgr.remember("decisions", "agent", "Qdrant partagé : sauvegarde des snapshots avant montée.")
        (receipt,) = wr.recall_receipts(root, task_id="T-3")["receipts"]
        written = len(_lines(root))

        payload = wr.recall_replay(root, receipt=receipt["key"], task_id="T-3")

        assert len(_lines(root)) == written, "rejouer depuis le cockpit n'écrit aucun reçu"
        assert payload["reason"] is None
        assert payload["mode"] == "search"
        assert payload["query"] == "Qdrant partagé"
        kinds = {r["id"]: r["kind"] for r in payload["diff"]["rows"]}
        assert kinds[ids["old"]] == "replaced"
        assert kinds[fresh.id] == "appeared"
        assert payload["diff"]["rows"][0]["entry"]["title"]
        assert all(r["scope"] == "project" for r in payload["results"])

    @pytest.mark.parametrize("mode", ["hybrid", "taxonomy", "typed"])
    def test_each_mode_replays_with_its_own_reader(
        self, tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "modes"
        mgr = _project(root)
        entry = mgr.remember("decisions", "agent", PIN_NEW)
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "T-4")
        if mode == "hybrid":
            # Sans index lexical compagnon, `hybrid_search` retombe sur `search` : la ligne est posée telle
            # qu'un projet Qdrant + lexical l'écrirait, le rejeu suit le même repli.
            record_read(root, mode="hybrid", surface="mcp", served=mgr.search("Qdrant"), query="Qdrant",
                        task_ref="T-4", lanes={"lexical": [(entry.id, 1.0)]})
        elif mode == "taxonomy":
            mgr.search_taxonomy("Qdrant")
        else:
            mgr.recall_typed("Qdrant", type_="decisions")
        monkeypatch.delenv("GRIMOIRE_TASK_ID")
        receipt = next(r for r in wr.recall_receipts(root, task_id="T-4")["receipts"] if r["mode"] == mode)
        payload = wr.recall_replay(root, receipt=receipt["key"])
        assert payload["mode"] == mode
        assert payload["reason"] is None
        assert payload["diff"]["changed"] == 0

    def test_task_recall_replays(self, forge: tuple[Path, MemoryManager, dict[str, str]]) -> None:
        root, mgr, _ids = forge
        task_id = _task(root, "Mettre à jour le Qdrant partagé du poste")
        build_task_recall(root, task_id, memory_manager=mgr)
        (receipt,) = wr.recall_receipts(root, task_id=task_id)["receipts"]
        payload = wr.recall_replay(root, receipt=receipt["key"], task_id=task_id)
        assert payload["mode"] == "task_recall"
        assert payload["diff"]["changed"] == 0
        assert payload["results"]

    def test_free_query_first_pass_then_compared(self, forge: tuple[Path, MemoryManager, dict[str, str]]) -> None:
        root, mgr, ids = forge
        written = len(_lines(root))
        first = wr.recall_replay(root, query="  Qdrant   partagé ")
        assert first["query"] == "Qdrant partagé"
        assert first["mode"] == "free"
        assert first["diff"] is None
        assert first["results"]
        base = ",".join(r["id"] for r in first["results"])
        mgr.supersede(ids["old"], ids["new"])
        again = wr.recall_replay(root, query="Qdrant partagé", base=base)
        kinds = {r["id"]: r["kind"] for r in again["diff"]["rows"]}
        if ids["old"] in base.split(","):
            assert kinds[ids["old"]] == "replaced"
        assert len(_lines(root)) == written

    def test_refusals_are_named(self, forge: tuple[Path, MemoryManager, dict[str, str]],
                                monkeypatch: pytest.MonkeyPatch) -> None:
        root, mgr, _ids = forge
        assert wr.recall_replay(root, query="   ")["reason"] == "requête vide : rien à rejouer"
        assert wr.recall_replay(root, receipt="0" * 16)["reason"].startswith("reçu introuvable")
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "T-5")
        mgr.search("Qdrant")
        monkeypatch.delenv("GRIMOIRE_TASK_ID")
        (receipt,) = wr.recall_receipts(root, task_id="T-5")["receipts"]
        assert wr.recall_replay(root, receipt=receipt["key"], task_id="T-6")["reason"] == (
            "ce reçu n'appartient pas à cette tâche")
        mgr.search("sans tâche")
        bare = wr.recall_receipts(root)["receipts"][0]
        refused = wr.recall_replay(root, receipt=bare["key"])
        assert refused["reason"].startswith("requête non conservée")
        assert refused["receipt"]["key"] == bare["key"]
        assert wr.recall_replay(tmp_unknown := root / "nulle-part", query="q")["reason"].startswith(
            "projet non initialisé"), tmp_unknown

    def test_a_memory_failure_during_replay_is_a_reason(
        self, forge: tuple[Path, MemoryManager, dict[str, str]], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root, _mgr, _ids = forge

        def boom(*_a: Any, **_k: Any) -> Any:
            raise OSError("disque absent")

        monkeypatch.setattr("grimoire.memory.taxonomy.run_memory_search", boom)
        assert wr.recall_replay(root, query="Qdrant")["reason"] == "rejeu impossible : disque absent"


# ── Les routes et l'inspecteur en lecture seule ─────────────────────────────


class TestRoutes:
    def test_get_routes(self, forge: tuple[Path, MemoryManager, dict[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
        root, mgr, _ids = forge
        monkeypatch.setenv("GRIMOIRE_TASK_ID", "T-7")
        mgr.search("Qdrant")
        monkeypatch.delenv("GRIMOIRE_TASK_ID")
        by_task = workspace_get(root, f"{PREFIX}tasks/T-7/receipts", {})
        assert by_task["schemaVersion"] == wr.RECEIPTS_SCHEMA_VERSION and by_task["taskId"] == "T-7"
        listing = workspace_get(root, f"{PREFIX}memory/recall", {"task": ["T-7"]})
        key = listing["receipts"][0]["key"]
        replay = workspace_get(root, f"{PREFIX}memory/recall/replay", {"receipt": [key], "task": ["T-7"]})
        assert replay["schemaVersion"] == wr.REPLAY_SCHEMA_VERSION and replay["diff"] is not None
        free = workspace_get(root, f"{PREFIX}memory/recall/replay", {"q": ["Qdrant"], "limit": ["3"]})
        assert len(free["results"]) <= 3

    def test_opening_the_inspector_recall_writes_no_receipt(self, forge: tuple[Path, MemoryManager, dict[str, str]]) -> None:
        root, _mgr, _ids = forge
        task_id = _task(root, "Mettre à jour le Qdrant partagé du poste")
        workspace_api.task_recall_view(root, task_id)
        assert _lines(root) == [], "l'inspecteur du cockpit lit le rappel sans écrire de reçu"
