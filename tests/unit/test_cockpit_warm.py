"""#1243 — la chauffe du cockpit paie le parcours du journal des traces avant la première requête.

Critère de coût : le nombre de lignes parsées (``TraceRecord.from_dict``), jamais une durée.
Tout fil de chauffe est joint avec un délai court.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from grimoire.cli import cockpit_http, cockpit_warm
from grimoire.tools.workspace_pulse import TRACES_RELPATH
from grimoire.traces import journal_cache
from grimoire.traces.journal_cache import load_trace_journal
from grimoire.traces.ledger import TraceLedger, TraceOutcome
from grimoire.traces.schemas import TraceRecord
from grimoire.traces.task_index import traces_by_task

if TYPE_CHECKING:
    from pathlib import Path

JOIN_S = 5.0
LINES = 7


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    journal_cache.clear()


def _run_warm(project: Path, stop: threading.Event | None = None) -> None:
    """La chauffe dans son propre fil (elle baisse la priorité de son fil : jamais celle du test)."""
    thread = threading.Thread(target=cockpit_http.warm_sheet, args=(project, stop))
    thread.start()
    thread.join(JOIN_S)
    assert not thread.is_alive()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    ledger = TraceLedger((tmp_path / TRACES_RELPATH).parent)
    for _ in range(LINES):
        ledger.record(
            run_id="RUN-a",
            workflow_instance_id="WFI-1",
            mission_id="MIS-1",
            task_id="GAO-1",
            recipe_id="recipe.test",
            outcome=TraceOutcome.SUCCESS,
            started_at="2026-01-01T00:00:00+00:00",
        )
    return tmp_path


@pytest.fixture
def parsed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = TraceRecord.from_dict.__func__  # type: ignore[attr-defined]

    def counting(cls: type[TraceRecord], d: dict, **kw: object) -> TraceRecord:
        calls.append(str(d.get("id")))
        return original(cls, d, **kw)

    monkeypatch.setattr(TraceRecord, "from_dict", classmethod(counting))
    return calls


@pytest.fixture
def no_sheet(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Les étapes de fiche remplacées par un témoin : seul le journal compte ici."""
    from grimoire.tools import workspace_api

    seen: list[str] = []
    monkeypatch.setattr(workspace_api, "sheet_view", lambda root: seen.append("sheet"))
    monkeypatch.setattr(workspace_api, "tasks_view", lambda root: seen.append("tasks"))
    monkeypatch.setattr(cockpit_warm, "warm_fleet", lambda root, stop=None: seen.append("fleet"))
    return seen


def test_warm_reads_the_journal_once_for_n_requests(project: Path, parsed: list[str], no_sheet: list[str]) -> None:
    stop = cockpit_http.start_warm(project)
    for thread in threading.enumerate():
        if thread.name == "cockpit-warm":
            thread.join(JOIN_S)
    assert no_sheet == ["sheet", "tasks", "fleet"]
    assert len(parsed) == 2 * LINES  # un parcours complet pour l'index par tâche, un pour le journal complet
    for _ in range(5):  # cinq requêtes ensuite : aucune ligne de plus
        assert len(load_trace_journal(project / TRACES_RELPATH)) == LINES
        assert len(traces_by_task(project / TRACES_RELPATH)["GAO-1"]) == LINES
    assert len(parsed) == 2 * LINES
    assert not stop.is_set()


def test_requests_during_warm_share_one_pass(project: Path, parsed: list[str], no_sheet: list[str]) -> None:
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(len(load_trace_journal(project / TRACES_RELPATH))))
        for _ in range(4)
    ]
    warm = threading.Thread(target=cockpit_http.warm_sheet, args=(project,))
    for t in (warm, *threads):
        t.start()
    for t in (warm, *threads):
        t.join(JOIN_S)
    assert results == [LINES] * 4
    assert len(parsed) == 2 * LINES  # le verrou : un seul parcours par journal pour la chauffe et les quatre requêtes


def test_stop_cancels_the_rest(project: Path, parsed: list[str], no_sheet: list[str]) -> None:
    stop = threading.Event()
    stop.set()
    _run_warm(project, stop)
    assert parsed == []
    assert no_sheet == []


def test_stop_between_steps_skips_the_sheet(
    project: Path, parsed: list[str], no_sheet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = threading.Event()
    # le journal est lu, puis l'arrêt tombe avant la fiche

    def read_then_stop(path: Path) -> list[TraceRecord]:
        out = load_trace_journal(path)
        stop.set()
        return out

    monkeypatch.setattr("grimoire.traces.journal_cache.load_trace_journal", read_then_stop)
    _run_warm(project, stop)
    assert len(parsed) == 2 * LINES  # l'index par tâche, puis le journal complet ; la fiche ne vient pas
    assert no_sheet == []


def test_no_root_starts_nothing() -> None:
    before = {t.ident for t in threading.enumerate()}
    stop = cockpit_http.start_warm(None)
    assert {t.ident for t in threading.enumerate()} == before
    stop.set()


def test_a_failure_costs_only_the_warm(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: Path) -> list[TraceRecord]:
        raise RuntimeError("journal illisible")

    monkeypatch.setattr("grimoire.traces.journal_cache.load_trace_journal", boom)
    _run_warm(project)  # ne lève pas


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="nice par fil : Linux")
def test_warm_thread_runs_at_lower_priority(
    project: Path, no_sheet: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    niceness: list[int] = []
    real = os.setpriority

    def record(which: int, who: int, value: int) -> None:
        niceness.append(value)
        real(which, who, value)

    monkeypatch.setattr(os, "setpriority", record)
    thread = threading.Thread(target=cockpit_http.warm_sheet, args=(project,))
    thread.start()
    thread.join(JOIN_S)
    assert niceness == [cockpit_http._WARM_NICE]
    assert os.getpriority(os.PRIO_PROCESS, 0) == 0  # le fil principal garde la sienne


def test_priority_refusal_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: int) -> None:
        raise PermissionError

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "setpriority", refuse, raising=False)
    cockpit_http._lower_priority()


class _FakeServer:
    """Un serveur dont ``serve_forever`` lève ce qu'on lui donne : l'arrêt du cockpit sans réseau."""

    def __init__(self, raises: BaseException, log: list[str]) -> None:
        self._raises = raises
        self._log = log

    def serve_forever(self) -> None:
        self._log.append("serve")
        raise self._raises

    def server_close(self) -> None:
        self._log.append("close")


def _patch_serve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raises: BaseException, log: list[str]
) -> list[Path | None]:
    from grimoire.cli import cmd_cockpit

    started: list[Path | None] = []

    class _LoggedEvent(threading.Event):
        def set(self) -> None:
            log.append("warm-stop")
            super().set()

    stop = _LoggedEvent()

    def fake_start(root: Path | None) -> threading.Event:
        started.append(root)
        return stop

    monkeypatch.setattr(cmd_cockpit, "_select_cwd_project", lambda root=None: None)
    monkeypatch.setattr(cmd_cockpit, "_serve_dir", lambda: tmp_path)
    monkeypatch.setattr(cmd_cockpit, "_sync_site", lambda serve_dir: None)
    monkeypatch.setattr(cmd_cockpit, "load_registry", list)
    monkeypatch.setattr(cmd_cockpit, "_unregistered_cwd_notice", lambda projects: None)
    monkeypatch.setattr(cmd_cockpit, "new_session_token", lambda: None)
    monkeypatch.setattr(cmd_cockpit, "ThreadingHTTPServer", lambda addr, handler: _FakeServer(raises, log))
    monkeypatch.setattr(cmd_cockpit, "_OPENING_PATH", tmp_path)
    monkeypatch.setattr(cmd_cockpit, "start_warm", fake_start)
    return started


@pytest.mark.parametrize("raises", [KeyboardInterrupt(), RuntimeError("boom")])
def test_serve_starts_the_warm_and_cancels_it_on_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raises: BaseException
) -> None:
    from grimoire.cli import cmd_cockpit

    log: list[str] = []
    started = _patch_serve(monkeypatch, tmp_path, raises, log)
    if isinstance(raises, KeyboardInterrupt):
        cmd_cockpit.serve(port=0, open_browser=False, do_refresh=False, with_tests=False, project_root=None)
    else:
        with pytest.raises(RuntimeError):
            cmd_cockpit.serve(port=0, open_browser=False, do_refresh=False, with_tests=False, project_root=None)
    assert started == [tmp_path]  # une seule chauffe, pour le projet servi
    assert log == ["serve", "warm-stop", "close"]  # annulée avant la fermeture, même sur erreur


def test_priority_untouched_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, ...]] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "setpriority", lambda *a: calls.append(a), raising=False)
    cockpit_http._lower_priority()
    assert calls == []  # hors Linux, le nice porterait sur tout le processus : on n'y touche pas
