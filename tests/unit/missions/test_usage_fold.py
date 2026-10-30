"""GAO-i12-replier-001 : l'usage réel de chaque session est replié dans sa tâche, sans geste.

Un ``HOME`` jetable porte des journaux Claude Code synthétiques ; le pas du tick
(``fold_pending``) les retrouve, les replie une fois, et dit ce qu'il n'a pas pu lire.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.pause import CONTROL_HOME_ENV
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import WORKTREE_CREATED_EVENT
from grimoire.missions.usage_fold import fold_pending, locate_transcript, transcript_is_missing
from grimoire.traces.ledger import TraceLedger

ACC = "l'usage est replié"
HOST = "claude-code-cli"
SESSION = "41c89d23-0000-0000-0000-00000000000{n}"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONTROL_HOME_ENV, str(tmp_path / "control-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    from grimoire.missions import usage_fold
    from grimoire.tools import workspace_work as ww

    ww._TRANSCRIPT_CACHE.clear()
    usage_fold._missing_cache.clear()


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / "_grimoire/standard").mkdir(parents=True)
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


def _slug(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def _held(
    service: TaskService,
    titre: str,
    session: str,
    *,
    close: bool = False,
    journal: tuple[Path, str] | None = None,
    calls: int = 3,
    model: str = "claude-sonnet-5-5",
) -> str:
    """Une tâche tenue ; son journal (*journal* = ``(tmp_path, dossier)``) est écrit pendant le travail, avant la clôture."""
    mission = next(iter(service.ledger.list_missions()), None)
    mission_id = mission.id if mission else service.ledger.create_mission(title="Usage", origin="test").id
    task = service.ledger.create_task(mission_id, titre, acceptance=(ACC,), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    service.ledger.transition_task(task.id, TaskState.CLAIMED, actor_id="winston", claim=TaskClaim("winston", "local"))
    service.ledger.attach_session(task.id, session, session_host=HOST, actor_id="hook")
    service.ledger.transition_task(task.id, TaskState.RUNNING, actor_id="winston")
    if journal is not None:
        _journal(journal[0], journal[1], session, calls, model=model)
    if close:
        service.ledger.transition_task(task.id, TaskState.CANCELLED, actor_id="winston")
    return task.id


def _call(n: int, *, model: str = "claude-sonnet-5-5") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": datetime.now(UTC).isoformat(),
            "message": {
                "id": f"msg-{n}",
                "model": model,
                "content": [{"type": "text"}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


def _journal(tmp_path: Path, folder: str, session: str, calls: int = 3, *, model: str = "claude-sonnet-5-5") -> Path:
    path = tmp_path / "claude" / "projects" / folder / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_call(i, model=model) for i in range(calls)) + "\n", encoding="utf-8")
    return path


def _note_worktree(service: TaskService, task_id: str, path: Path) -> None:
    service.ledger._append_event(
        WORKTREE_CREATED_EVENT,
        task_id,
        "task",
        "test",
        {"path": str(path), "branch": f"task/{task_id}", "base_sha": "0" * 40, "into": "main"},
    )


def _traces(projet: Path, task_id: str) -> list[Any]:
    return TraceLedger(projet / "_grimoire-output/traces").list_traces(task_id=task_id)


def test_le_pas_replie_trois_appels_depuis_le_dossier_du_worktree_puis_rien(
    tmp_path: Path, projet: Path, service: TaskService
) -> None:
    session = SESSION.format(n=1)
    worktree = tmp_path / "wt" / "w1"
    worktree.mkdir(parents=True)
    tid = _held(service, "worktree", session, journal=(tmp_path, _slug(worktree)))
    _note_worktree(service, tid, worktree)

    first = fold_pending(service)
    assert [(o.task_id, o.status, o.new_calls) for o in first.outcomes] == [(tid, "folded", 3)]
    assert len(_traces(projet, tid)) == 3

    second = fold_pending(service)  # tâche vivante : relue, mais rien n'est réécrit
    assert second.new_calls == 0
    assert second.already_folded == 3
    assert len(_traces(projet, tid)) == 3


def test_une_tache_terminee_deja_repliee_n_est_plus_lue(tmp_path: Path, projet: Path, service: TaskService) -> None:
    session = SESSION.format(n=2)
    tid = _held(service, "close", session, close=True, journal=(tmp_path, "autre-projet"))

    assert fold_pending(service).new_calls == 3
    again = fold_pending(service)
    assert again.outcomes == []
    assert again.logs == 0
    assert len(_traces(projet, tid)) == 3


def test_un_journal_sous_un_autre_dossier_est_retrouve_par_la_session(tmp_path: Path) -> None:
    session = SESSION.format(n=3)
    path = _journal(tmp_path, "-ailleurs-quelconque", session)
    assert locate_transcript(session, worktree=tmp_path / "sans-journal") == path
    assert locate_transcript(SESSION.format(n=4)) is None


def test_un_identifiant_hors_motif_sur_est_refuse_sans_lecture(
    tmp_path: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert locate_transcript("../x") is None
    assert locate_transcript("../../etc/passwd") is None
    assert transcript_is_missing("../x") is False  # refusé, pas « introuvable »
    tid = _held(service, "traversée", SESSION.format(n=5))
    task = service.ledger._tasks[tid]
    object.__setattr__(task.claim, "session_id", "../x")
    opened: list[Any] = []
    real_open = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (opened.append(self), real_open(self, *a, **k))[1])
    report = fold_pending(service)
    assert [o.status for o in report.outcomes] == ["refused"]
    assert not [p for p in opened if p.suffix == ".jsonl"]


def test_un_journal_absent_est_dit_introuvable_pas_zero(tmp_path: Path, projet: Path, service: TaskService) -> None:
    tid = _held(service, "purgé", SESSION.format(n=6), close=True)
    report = fold_pending(service)
    assert [(o.task_id, o.status, o.detail) for o in report.outcomes] == [
        (tid, "missing", "journal de session introuvable")
    ]
    assert _traces(projet, tid) == []
    assert transcript_is_missing(SESSION.format(n=6)) is True


def test_au_plus_cinq_journaux_par_pas_puis_les_suivants(tmp_path: Path, projet: Path, service: TaskService) -> None:
    ids = []
    for i in range(7):
        session = f"41c89d23-1111-0000-0000-00000000000{i}"
        ids.append(_held(service, f"lot {i}", session, close=True, journal=(tmp_path, "p"), calls=1))

    first = fold_pending(service)
    assert first.logs == 5 and len(first.of("folded")) == 5 and len(first.of("deferred")) == 2
    second = fold_pending(service)
    assert second.logs == 2 and second.new_calls == 2
    assert sum(len(_traces(projet, t)) for t in ids) == 7
    assert fold_pending(service).outcomes == []


def test_un_journal_de_plus_de_50_mo_est_reporte_avec_un_diagnostic(
    tmp_path: Path, projet: Path, service: TaskService
) -> None:
    session = SESSION.format(n=7)
    tid = _held(service, "énorme", session, close=True, journal=(tmp_path, "p"))
    path = tmp_path / "claude/projects/p" / f"{session}.jsonl"
    with path.open("ab") as fh:
        fh.truncate(50 * 1024 * 1024 + 1)

    report = fold_pending(service)
    assert [(o.task_id, o.status) for o in report.outcomes] == [(tid, "deferred")]
    assert "reporté" in report.outcomes[0].detail and "50 Mo" in report.outcomes[0].detail
    assert report.logs == 0 and _traces(projet, tid) == []

    tick = run_tick(projet)
    assert any("reporté" in line for line in tick.attention)


def test_le_tick_replie_et_le_cockpit_rend_trois_appels(tmp_path: Path, projet: Path, service: TaskService) -> None:
    from grimoire.tools.workspace_routes import workspace_get

    session = SESSION.format(n=8)
    tid = _held(service, "cockpit", session, journal=(tmp_path, "p"))

    tick = run_tick(projet)
    assert [o.new_calls for o in tick.usage] == [3]
    assert tick.to_dict()["usage"][0]["task_id"] == tid

    payload = workspace_get(projet, "/api/workspace/tasks", {})
    card = next(c for c in payload["tasks"] if c["id"] == tid)
    usage = card["work"]["usage"]
    assert usage["usage_source"] == "claude-code-transcript"
    assert usage["calls"] == 3
    assert usage["cost_status"] != "unknown"


def test_un_modele_sans_tarif_garde_les_tokens_et_un_cout_inconnu(
    tmp_path: Path, projet: Path, service: TaskService
) -> None:
    from grimoire.tools.workspace_work import usage_of

    session = SESSION.format(n=9)
    tid = _held(service, "sans tarif", session, journal=(tmp_path, "p"), model="claude-modele-sans-tarif-9")
    fold_pending(service)

    usage = usage_of(projet, service.ledger._tasks[tid])
    assert usage["cost_status"] == "unknown"
    assert usage["total_tokens"] > 0
    assert usage["calls"] == 3


def test_backfill_dry_run_n_ecrit_rien_et_dit_le_nombre_de_journaux(
    tmp_path: Path, projet: Path, service: TaskService
) -> None:
    for i in range(2):
        session = f"41c89d23-2222-0000-0000-00000000000{i}"
        _held(service, f"clos {i}", session, close=True, journal=(tmp_path, "p"))
    _held(service, "vivante", SESSION.format(n=0))  # le backfill ne prend que les closes
    traces = projet / "_grimoire-output/traces/traces.jsonl"
    before = traces.stat().st_size if traces.exists() else 0

    result = runner.invoke(app, ["task", "usage", "--backfill", "--dry-run", "--project-root", str(projet)])
    assert result.exit_code == 0, result.output
    assert "2 journal" in result.output
    assert (traces.stat().st_size if traces.exists() else 0) == before

    done = runner.invoke(app, ["task", "usage", "--backfill", "--project-root", str(projet)])
    assert done.exit_code == 0, done.output
    assert traces.stat().st_size > before
