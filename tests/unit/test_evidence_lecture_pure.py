"""Le panneau Preuves en lecture pure et sous la seconde (I9, GAO-i9-rendre-l-001).

Avant : une ouverture du panneau appelait ``check_evidence_gates`` par tâche,
chaque appel ajoutait un ``gate.checked`` au journal (et rendait un journal
périmé « frais »), et chaque appel relisait le YAML (30 s pour 42 tâches).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from grimoire.core import agentic_standard as std
from grimoire.core.agentic_standard import (
    check_evidence_gates,
    setup_standard_profile,
    verify_standard_profile,
)
from grimoire.core.standard_checks.runtime_journal_check import (
    CLAIM_EVENT,
    JOURNAL_PATH,
    LEDGER_EVENTS_PATH,
    STALE_GRACE_S,
)
from grimoire.tools import workspace_api as wa

BOARD = Path("_grimoire/standard/task-board.yaml")


def _project(tmp_path: Path, statuses: list[str]) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    path = tmp_path / BOARD
    board = yaml.safe_load(path.read_text(encoding="utf-8"))
    board["tasks"] = [
        {"task_id": f"T-{i:03d}", "title": f"Tâche {i}", "status": status}
        for i, status in enumerate(statuses)
    ]
    path.write_text(yaml.safe_dump(board, allow_unicode=True), encoding="utf-8")
    return tmp_path


def _stat(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def test_evidence_view_n_ecrit_pas_dans_le_journal(tmp_path: Path) -> None:
    root = _project(tmp_path, ["archived"] * 150 + ["accepted"] * 47 + ["in_progress"] * 3)
    journal = root / JOURNAL_PATH
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("", encoding="utf-8")
    before = _stat(journal)

    wa.evidence_view(root)
    wa.evidence_view(root, include_all=True)

    assert _stat(journal) == before

    check_evidence_gates(root, task_id="T-199", target_state="in_progress")
    assert _stat(journal)[0] > before[0], "le défaut continue de consigner"


def test_au_plus_quatre_lectures_yaml_par_requete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[Path] = []
    real = std._yaml

    class _Counting:
        def __init__(self) -> None:
            self._inner = real()

        def load(self, path):
            reads.append(Path(path))
            return self._inner.load(path)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(std, "_yaml", _Counting)
    root = _project(tmp_path, ["in_progress"] * 60 + ["archived"] * 140)
    reads.clear()

    payload = wa.evidence_view(root, include_all=True, budget=600)

    assert payload["count"] == 200
    assert len(reads) <= 4, [p.name for p in reads]


def test_parite_des_gates_des_taches_actives_et_decompte_terminal(tmp_path: Path) -> None:
    statuses = ["proposed", "ready", "in_progress", "review", "blocked", "accepted", "archived", "archived"]
    root = _project(tmp_path, statuses)

    payload = wa.evidence_view(root)

    assert payload["terminal"] == {"accepted": 1, "archived": 2}
    assert payload["truncated"] is False
    assert [t["status"] for t in payload["tasks"]] == statuses[:5]
    for entry in payload["tasks"]:
        gate = check_evidence_gates(root, task_id=entry["task_id"], target_state=entry["status"], record=False)
        assert entry["gates"]["ok"] == gate.ok
        assert entry["gates"]["missing"] == list(gate.missing)
        assert [c["id"] for c in entry["gates"]["checks"]] == [c.id for c in gate.checks]

    full = wa.evidence_view(root, include_all=True, budget=600)
    assert full["count"] == len(statuses)


def test_all_rend_les_283_entrees(tmp_path: Path) -> None:
    root = _project(tmp_path, ["archived"] * 200 + ["accepted"] * 80 + ["in_progress"] * 3)
    assert wa.evidence_view(root, include_all=True, budget=600)["count"] == 283
    assert wa.evidence_view(root)["count"] == 3


def test_une_borne_depassee_le_dit_au_lieu_de_rendre_tout_vert(tmp_path: Path) -> None:
    root = _project(tmp_path, ["in_progress"] * 20 + ["archived"] * 5)
    payload = wa.evidence_view(root, budget=-1.0)
    assert payload["truncated"] is True
    assert payload["terminal"] == {"archived": 5}


def test_journal_stale_reste_leve_apres_evidence_view(tmp_path: Path) -> None:
    root = _project(tmp_path, ["in_progress"] * 5)
    now = datetime.now(UTC)
    journal = root / JOURNAL_PATH
    journal.parent.mkdir(parents=True, exist_ok=True)
    old = now - timedelta(seconds=STALE_GRACE_S + 60)
    journal.write_text(json.dumps({
        "schema": "grimoire-agentic-standard-runtime-event/v1", "event_type": "context.built",
        "task_id": "T-000", "profile": "governed", "timestamp": old.isoformat(), "details": {},
    }) + "\n", encoding="utf-8")
    ledger = root / LEDGER_EVENTS_PATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"event_type": CLAIM_EVENT, "entity_id": "GAO-1", "created_at": now.isoformat()}) + "\n", encoding="utf-8")

    wa.evidence_view(root)

    assert "journal.stale" in [c.id for c in verify_standard_profile(root).checks]


@pytest.mark.perf
def test_283_taches_servies_sous_1_5_s(tmp_path: Path) -> None:
    """``pytest -m perf tests/unit/test_evidence_lecture_pure.py`` ; avant : > 30 s."""
    root = _project(tmp_path, ["archived"] * 200 + ["accepted"] * 80 + ["in_progress"] * 3)
    start = time.perf_counter()
    payload = wa.evidence_view(root)
    elapsed = time.perf_counter() - start
    assert payload["truncated"] is False
    assert elapsed < 1.5, f"{elapsed:.2f} s"
