"""P13 — le reçu de rappel : chaque lecture mémoire servie aux agents laisse une ligne.

``_grimoire-output/memory-reads.jsonl`` : identifiants servis (rang, score, par
voie en lecture hybride), requête hachée, filtres, provenance. Jamais de contenu.
Borné par rotation, ignoré par git, écrit sans réseau, et son échec n'échoue
jamais la lecture.
"""

from __future__ import annotations

import json
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.memory import reads_log
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.memory.reads_log import READS_RELPATH, read_counts, record_read
from grimoire.memory.supersession import bench_supersession
from grimoire.missions.recall import build_task_recall

REPO = Path(__file__).resolve().parents[3]
QUERY = "décision backend vectoriel secret-dans-la-requête"
TEXT_A = "Décision : le backend vectoriel retenu est Qdrant, confidentiel-A."
TEXT_B = "Décision : le backend vectoriel de secours est Weaviate, confidentiel-B."


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def mgr(tmp_path: Path) -> MemoryManager:
    manager = MemoryManager(
        LocalMemoryBackend(tmp_path / "primary.json"),
        lexical_companion=LocalMemoryBackend(tmp_path / "lexical.json"),
        project_name="demo", project_root=tmp_path,
    )
    manager.store(TEXT_A, user_id="u")
    manager.store(TEXT_B, user_id="u")
    return manager


def _rows(root: Path) -> list[dict[str, Any]]:
    path = root / READS_RELPATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestWhatIsWritten:
    def test_hybrid_search_writes_ids_ranks_scores_and_lanes(self, mgr: MemoryManager, tmp_path: Path) -> None:
        mgr.surface = "mcp"
        served = mgr.hybrid_search("backend vectoriel", user_id="u", limit=5)
        (row,) = _rows(tmp_path)
        assert row["mode"] == "hybrid"
        assert row["surface"] == "mcp"
        assert row["filters"]["limit"] == 5
        assert row["filters"]["user_id"] == "u"
        assert [s["id"] for s in row["served"]] == [e.id for e in served]
        assert [s["rank"] for s in row["served"]] == [1, 2]
        assert all("score" in s for s in row["served"])
        # Rang et score avant fusion, dans chaque classement qui contenait le souvenir.
        assert set(row["served"][0]["lanes"]) == {"vector", "lexical"}
        assert row["served"][0]["lanes"]["vector"]["rank"] >= 1
        assert datetime.fromisoformat(row["ts"]).tzinfo is not None

    def test_search_taxonomy_and_typed_each_write_a_row(self, mgr: MemoryManager, tmp_path: Path) -> None:
        plain = MemoryManager(LocalMemoryBackend(tmp_path / "p2.json"), project_name="demo", project_root=tmp_path)
        plain.store(TEXT_A, user_id="u")
        plain.search("backend vectoriel")
        plain.search_taxonomy("backend vectoriel", wing="")
        plain.remember("decisions", "agent", TEXT_B)
        plain.recall_typed("backend vectoriel", type_="decisions", agent="agent")
        assert [r["mode"] for r in _rows(tmp_path)] == ["search", "taxonomy", "typed"]

    def test_a_search_that_serves_nothing_still_leaves_a_receipt(self, mgr: MemoryManager, tmp_path: Path) -> None:
        mgr.search("zzzqqq")
        (row,) = _rows(tmp_path)
        assert row["served"] == []

    def test_provenance_is_taken_from_the_session(
        self, mgr: MemoryManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GRIMOIRE_ACTOR", "alice")
        monkeypatch.setenv("GRIMOIRE_SESSION_ID", "11111111-0000-4000-8000-000000000001")
        mgr.search("backend vectoriel")
        (row,) = _rows(tmp_path)
        assert row["actor"] == "alice"
        assert row["session_id"] == "11111111-0000-4000-8000-000000000001"

    def test_no_content_and_no_query_text(self, mgr: MemoryManager, tmp_path: Path) -> None:
        mgr.hybrid_search(QUERY, limit=5)
        mgr.search(QUERY, limit=5)
        raw = (tmp_path / READS_RELPATH).read_text(encoding="utf-8")
        assert "confidentiel" not in raw
        assert "Qdrant" not in raw
        assert "secret-dans-la-requête" not in raw
        for row in _rows(tmp_path):
            assert row["query_sha"] == reads_log.query_fingerprint(QUERY)
            assert row["query_chars"] == len(QUERY)
            assert set(row) <= {
                "ts", "mode", "surface", "actor", "session_id", "task_ref", "query_sha", "query_chars",
                "filters", "served", "served_truncated", "hidden_stale",
            }
            assert all(set(s) <= {"id", "rank", "score", "lanes"} for s in row["served"])

    def test_filters_record_what_was_hidden_by_default(self, mgr: MemoryManager, tmp_path: Path) -> None:
        mgr.search("backend", include_retracted=True)
        (row,) = _rows(tmp_path)
        assert row["filters"] == {"limit": 5, "include_retracted": True}

    def test_a_manager_without_project_writes_no_journal(self, tmp_path: Path) -> None:
        bare = MemoryManager.from_backend(LocalMemoryBackend(tmp_path / "m.json"))
        bare.store(TEXT_A)
        bare.search("backend")
        assert not (tmp_path / READS_RELPATH).exists()


class TestTaskRecall:
    def test_build_task_recall_writes_one_receipt_with_the_task(self, tmp_path: Path) -> None:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.recall import DEFAULT_LEDGER_RELPATH

        memoire = MemoryManager(
            LocalMemoryBackend(tmp_path / "memoire.json"), project_name="demo", project_root=tmp_path,
        )
        memoire.remember("failures", "agent", "Le webhook de paiement échoue : certificat TLS expiré.")
        memoire.remember("decisions", "agent", "Le webhook de paiement passe par la passerelle interne.")
        ledger = MissionLedger(tmp_path / DEFAULT_LEDGER_RELPATH)
        mission = ledger.create_mission(title="Travaux", origin="test")
        task = ledger.create_task(
            mission.id, "Réparer le webhook de paiement", acceptance=("Le webhook répond 200 en CI",),
        )

        recall = build_task_recall(tmp_path, task.id, memory_manager=memoire)

        rows = [r for r in _rows(tmp_path) if r["mode"] == "task_recall"]
        assert len(rows) == 1
        assert rows[0]["task_ref"] == task.id
        assert recall.memory_hits
        assert {s["id"] for s in rows[0]["served"]} == {h.id for h in recall.memory_hits}
        # Un seul reçu pour tout le rappel : pas un « typed » par type de mémoire interrogé.
        assert not [r for r in _rows(tmp_path) if r["mode"] == "typed"]
        assert "certificat TLS" not in (tmp_path / READS_RELPATH).read_text(encoding="utf-8")


class TestBounds:
    def test_gitignored(self) -> None:
        for name in ("memory-reads.jsonl",):
            out = subprocess.run(
                ["git", "check-ignore", "-q", f"_grimoire-output/{name}"], cwd=REPO, check=False,
            )
            assert out.returncode == 0, f"{name} doit être ignoré par git"

    def test_rotation_keeps_the_file_under_the_cap_and_the_newest_lines(
        self, mgr: MemoryManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(reads_log, "MAX_BYTES", 2_000)
        for i in range(60):
            mgr.search(f"backend vectoriel {i}")
        path = tmp_path / READS_RELPATH
        assert path.stat().st_size < 2_000 + 1_500  # une ligne de plus que le plafond au plus
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert 1 < len(rows) < 60
        # Ce qui reste est la fin du journal, en ordre, en JSON complet.
        last = reads_log.query_fingerprint("backend vectoriel 59")
        assert rows[-1]["query_sha"] == last
        assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)

    def test_served_rows_are_capped(self, tmp_path: Path) -> None:
        from grimoire.memory.backends.base import MemoryEntry

        many = [MemoryEntry(id=f"e{i}", text="t", score=1.0) for i in range(reads_log.MAX_SERVED + 10)]
        record_read(tmp_path, mode="search", surface="api", served=many, query="q")
        (row,) = _rows(tmp_path)
        assert len(row["served"]) == reads_log.MAX_SERVED
        assert row["served_truncated"] == reads_log.MAX_SERVED + 10

    def test_no_network_in_the_write_path(
        self, mgr: MemoryManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def blocked(*_a: Any, **_k: Any) -> None:
            raise AssertionError("appel réseau dans le chemin d'écriture du journal des lectures")

        monkeypatch.setattr(socket, "socket", blocked)
        monkeypatch.setattr(socket, "create_connection", blocked)
        monkeypatch.setattr(socket, "getaddrinfo", blocked)
        mgr.hybrid_search("backend vectoriel")
        mgr.search("backend vectoriel")
        assert len(_rows(tmp_path)) == 2

    def test_a_journal_failure_never_fails_the_read(
        self, mgr: MemoryManager, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Le chemin du journal est occupé par un répertoire : l'ouverture en écriture échoue.
        (tmp_path / "_grimoire-output" / "memory-reads.jsonl").mkdir(parents=True)
        with caplog.at_level("WARNING"):
            served = mgr.hybrid_search("backend vectoriel")
        assert len(served) == 2
        assert any("journal des lectures" in r.message for r in caplog.records)

    def test_an_unwritable_root_does_not_raise(self, tmp_path: Path) -> None:
        blocker = tmp_path / "fichier"
        blocker.write_text("x", encoding="utf-8")
        record_read(blocker, mode="search", surface="api", served=[], query="q")  # ne lève pas


class TestWhoIsNotCounted:
    def test_bench_replays_are_not_reads_served_to_agents(self, mgr: MemoryManager, tmp_path: Path) -> None:
        bench_supersession(mgr, ["backend vectoriel"], limit=3)
        assert _rows(tmp_path) == []
        assert mgr.journal_reads is True

    def test_cockpit_reads_write_nothing(self, tmp_path: Path) -> None:
        from grimoire.tools.workspace_memory import _search_project

        project = tmp_path / "p"
        (project / "_grimoire" / "_memory").mkdir(parents=True)
        (project / "project-context.yaml").write_text(
            "project:\n  name: demo\n  type: library\n  stack: [python]\nmemory:\n  backend: local\n",
            encoding="utf-8",
        )
        found = _search_project({"slug": "p", "name": "p", "path": str(project)}, "backend", 5)
        assert found["reason"] is None  # la recherche a bien eu lieu
        assert not (project / READS_RELPATH).exists()


class TestRecallCount:
    def test_counts_per_memory_over_the_window(self, tmp_path: Path) -> None:
        from grimoire.memory.backends.base import MemoryEntry

        e1, e2 = MemoryEntry(id="e1", text=""), MemoryEntry(id="e2", text="")
        record_read(tmp_path, mode="search", surface="mcp", served=[e1, e2], query="a")
        record_read(tmp_path, mode="search", surface="mcp", served=[e1], query="b")
        record_read(tmp_path, mode="search", surface="mcp", served=[], query="c")
        record_read(tmp_path, mode="search", surface="bench", served=[e2], query="d")
        counts = read_counts(tmp_path, days=7)
        assert counts.per_memory == {"e1": 2, "e2": 1}
        assert (counts.reads, counts.reads_with_results) == (3, 2)
        assert counts.to_dict()["recall_count_7d"] == {"e1": 2, "e2": 1}

    def test_old_and_corrupt_lines_do_not_count(self, tmp_path: Path) -> None:
        old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        path = tmp_path / READS_RELPATH
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"ts": old, "mode": "search", "surface": "mcp", "served": [{"id": "x"}]}) + "\n"
            + "pas du json\n" + json.dumps({"mode": "search"}) + "\n" + json.dumps([1, 2]) + "\n",
            encoding="utf-8",
        )
        counts = read_counts(tmp_path, days=7)
        assert counts.per_memory == {}
        assert counts.reads == 0

    def test_empty_when_no_journal(self, tmp_path: Path) -> None:
        assert read_counts(tmp_path).per_memory == {}

    def test_real_reads_feed_the_count(self, mgr: MemoryManager, tmp_path: Path) -> None:
        served = mgr.hybrid_search("backend vectoriel")
        mgr.search("backend vectoriel")
        counts = read_counts(tmp_path)
        assert counts.per_memory == {e.id: 2 for e in served}
