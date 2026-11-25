"""Lectures du cockpit gardées par empreinte de fichier (#1243, mesuré sur un poste réel le 2026-11-25).

Défauts empêchés : ~2 000 journaux de session de ~40 Ko reparsés à chaque requête, la chaîne de hash du journal
d'autorité (~9 400 entrées) revérifiée par projet, le cache des événements couverts trop petit pour cinq projets.
Défaut à ne pas rouvrir : servir une valeur périmée après une écriture.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import file_memo
from grimoire.missions import autopilot_view, ledger_snapshot, mandate
from grimoire.missions.autopilot_view import ViewJournal
from grimoire.missions.mandate import AuthorityJournal, JournalError
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_portfolio as portfolio


@pytest.fixture(autouse=True)
def _vide() -> None:
    file_memo.clear()
    autopilot_view._view_verified.clear()
    ledger_snapshot.clear_covered_cache()


def _age(path: Path, seconds: float = 3600) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _legacy_index(project_root: Path) -> dict[str, dict[str, Any]]:
    """Le ``sessions_index`` d'avant : un ``json.loads`` par fichier, rien de gardé."""
    index: dict[str, dict[str, Any]] = {}
    runs = project_root / portfolio.RUNS_RELPATH
    for path in sorted(runs.glob("session-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        sid = str(data.get("session_id") or path.stem.removeprefix("session-"))
        index[sid] = {
            "session_id": sid,
            "updated_at": str(data.get("updated_at") or data.get("started_at") or ""),
            "started_at": str(data.get("started_at") or ""),
            "ended_at": str(data.get("ended_at") or ""),
            "ended": bool(data.get("ended_at")),
            "task_id": str(data.get("task_id") or ""),
            "host": str(data.get("host") or data.get("host_id") or ""),
            "journal": path.relative_to(project_root).as_posix(),
        }
    return index


def _sessions(root: Path) -> Path:
    runs = root / portfolio.RUNS_RELPATH
    runs.mkdir(parents=True)
    now = datetime.now(UTC).isoformat()
    (runs / "session-a.json").write_text(json.dumps({"session_id": "a", "task_id": "T-1", "started_at": now}))
    (runs / "session-b.json").write_text(json.dumps({"task_id": "T-2", "host_id": "h", "ended_at": now}))
    (runs / "session-liste.json").write_text("[1, 2]")
    (runs / "session-casse.json").write_text("{pas du json")
    for path in runs.glob("session-*.json"):
        _age(path)
    return runs


class TestSessionJournals:
    def test_meme_index_que_l_ancienne_lecture(self, tmp_path: Path) -> None:
        _sessions(tmp_path)
        first = portfolio.sessions_index(tmp_path)
        second = portfolio.sessions_index(tmp_path)  # servi depuis la mémoire
        assert first == second == _legacy_index(tmp_path)
        assert set(first) == {"a", "b"}

    def test_un_fichier_ancien_n_est_parse_qu_une_fois(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _sessions(tmp_path)
        calls: list[int] = []
        original = portfolio._journal_fields
        monkeypatch.setattr(portfolio, "_journal_fields", lambda data: (calls.append(1), original(data))[1])
        portfolio.sessions_index(tmp_path)
        before = len(calls)
        portfolio.sessions_index(tmp_path)
        assert before > 0
        assert len(calls) == before + 1  # seul le fichier cassé est relu : une erreur n'est jamais gardée

    def test_une_reecriture_est_vue(self, tmp_path: Path) -> None:
        runs = _sessions(tmp_path)
        assert portfolio.sessions_index(tmp_path)["a"]["task_id"] == "T-1"
        path = runs / "session-a.json"
        path.write_text(json.dumps({"session_id": "a", "task_id": "T-9", "started_at": "x"}))
        _age(path)
        assert portfolio.sessions_index(tmp_path)["a"]["task_id"] == "T-9"

    def test_une_reecriture_a_taille_et_date_egales_tout_juste_ecrite_est_vue(self, tmp_path: Path) -> None:
        runs = _sessions(tmp_path)
        path = runs / "session-a.json"
        path.write_text(json.dumps({"session_id": "a", "task_id": "T-1", "started_at": "x"}))  # récent : non gardé
        assert portfolio.sessions_index(tmp_path)["a"]["task_id"] == "T-1"
        info = path.stat()
        path.write_text(json.dumps({"session_id": "a", "task_id": "T-2", "started_at": "x"}))
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        assert portfolio.sessions_index(tmp_path)["a"]["task_id"] == "T-2"

    def test_une_nouvelle_session_est_vue(self, tmp_path: Path) -> None:
        runs = _sessions(tmp_path)
        assert "c" not in portfolio.sessions_index(tmp_path)
        (runs / "session-c.json").write_text(json.dumps({"session_id": "c"}))
        assert "c" in portfolio.sessions_index(tmp_path)

    def test_journal_vivant_egal_a_l_ancienne_lecture_et_suit_les_ecritures(self, tmp_path: Path) -> None:
        runs = tmp_path / portfolio.RUNS_RELPATH
        runs.mkdir(parents=True)
        now = datetime.now(UTC)
        path = runs / "session-x.json"
        path.write_text(json.dumps({"updated_at": (now - timedelta(minutes=1)).isoformat()}))
        _age(path)
        assert wa._live_journal(tmp_path, now) is True
        assert wa._live_journal(tmp_path, now) is True
        path.write_text(json.dumps({"updated_at": (now - timedelta(minutes=1)).isoformat(), "ended_at": "fin"}))
        _age(path)
        assert wa._live_journal(tmp_path, now) is False


def _journal(tmp_path: Path, count: int = 4) -> AuthorityJournal:
    journal = AuthorityJournal(tmp_path / "authority.jsonl")
    for i in range(count):
        journal.append({"type": "t", "n": i})
    _age(journal.path)
    return journal


class TestViewJournal:
    def test_meme_lecture_que_le_journal_verifie(self, tmp_path: Path) -> None:
        base = _journal(tmp_path)
        view = ViewJournal(base.path)
        assert view.read() == base.read()
        assert view.read() == base.read()  # chaud

    def test_chaud_ne_reverifie_pas_la_chaine(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        base = _journal(tmp_path)
        view = ViewJournal(base.path)
        view.read()
        links: list[int] = []
        original = mandate._link
        monkeypatch.setattr(mandate, "_link", lambda *a: (links.append(1), original(*a))[1])
        view.read()
        assert links == []
        base.read()  # le chemin de décision, lui, vérifie toujours
        assert links

    def test_une_entree_ajoutee_est_vue(self, tmp_path: Path) -> None:
        base = _journal(tmp_path)
        view = ViewJournal(base.path)
        assert len(view.read()) == 4
        base.append({"type": "t", "n": 4})
        assert len(view.read()) == 5

    def test_un_journal_reecrit_a_taille_egale_est_revu(self, tmp_path: Path) -> None:
        base = _journal(tmp_path)
        view = ViewJournal(base.path)
        view.read()
        data = base.path.read_bytes()
        base.path.write_bytes(
            data.replace(b'"n": 1', b'"n": 7', 1) if b'"n": 1' in data else data.replace(b'"n":1', b'"n":7', 1)
        )
        _age(base.path)
        with pytest.raises(JournalError):  # chaîne rompue : la vérification complète refuse
            view.read()

    def test_une_tete_retiree_ou_en_avance_est_refusee_meme_a_chaud(self, tmp_path: Path) -> None:
        base = _journal(tmp_path)
        view = ViewJournal(base.path)
        view.read()
        base.head_path.unlink()
        with pytest.raises(JournalError):
            view.read()

    def test_un_fichier_recent_n_est_pas_garde(self, tmp_path: Path) -> None:
        base = _journal(tmp_path)
        os.utime(base.path, None)  # tout juste écrit
        view = ViewJournal(base.path)
        view.read()
        assert not autopilot_view._view_verified


class TestCoveredCacheHoldsAFleet:
    def test_six_ledgers_ne_se_chassent_pas(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.service import TaskService

        roots = []
        for n in range(6):
            root = tmp_path / f"p{n}"
            root.mkdir()
            service = TaskService(root)
            for i in range(3):
                service.add(f"T{i}", ("ok",), actor="t", owner="g", ready=True)
            monkeypatch.setattr(ledger_snapshot, "SNAPSHOT_AFTER_MS", -1.0)
            MissionLedger(service.ledger._root).list_tasks()
            roots.append(service.ledger._root)
        monkeypatch.setattr(ledger_snapshot, "SNAPSHOT_AFTER_MS", 1e9)
        for root in roots:
            MissionLedger(root).list_events()
        parsed: list[int] = []
        original = ledger_snapshot.parse_jsonl
        monkeypatch.setattr(
            ledger_snapshot, "parse_jsonl", lambda data, *a: (parsed.append(len(data)), original(data, *a))[1]
        )
        for root in roots:
            MissionLedger(root).list_events()
        assert [n for n in parsed if n > 0] == []
