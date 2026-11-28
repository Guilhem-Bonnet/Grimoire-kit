"""#1243 — la chauffe du cockpit construit l'index des traces par tâche que lit la vue « attention ».

Défaut empêché : le premier affichage reparsait tout ``traces.jsonl`` (≈ 2 s sur 70 000 traces) parce que la chauffe
ne remplissait que le journal complet, pas l'index par tâche. Critère de coût : les traces parsées (``stats``), jamais
une durée. La chauffe ne laisse rien sur le disque et aucun fil derrière elle.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from grimoire.cli import cockpit_http, cockpit_warm
from grimoire.tools import workspace_work
from grimoire.tools.workspace_pulse import TRACES_RELPATH
from grimoire.traces.ledger import TraceLedger, TraceOutcome
from grimoire.traces.task_index import TaskTraceIndex, index_for

if TYPE_CHECKING:
    from pathlib import Path

JOIN_S = 10.0


def _record(ledger: TraceLedger, task_id: str) -> None:
    ledger.record(
        run_id="RUN-a",
        workflow_instance_id="WFI-1",
        mission_id="MIS-1",
        task_id=task_id,
        recipe_id="recipe.test",
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-01-01T00:00:00+00:00",
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    ledger = TraceLedger((tmp_path / TRACES_RELPATH).parent)
    for n in range(6):
        _record(ledger, f"GAO-{n % 3}")
    return tmp_path


@pytest.fixture(autouse=True)
def _only_the_journals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les étapes de fiche et de Flotte remplacées : seuls les journaux comptent ici."""
    from grimoire.tools import workspace_api

    monkeypatch.setattr(workspace_api, "sheet_view", lambda root: None)
    monkeypatch.setattr(workspace_api, "tasks_view", lambda root: None)
    monkeypatch.setattr(cockpit_warm, "warm_fleet", lambda root, stop=None: 0)


def _warm(project: Path, stop: threading.Event | None = None) -> None:
    thread = threading.Thread(target=cockpit_http.warm_sheet, args=(project, stop), name="warm-under-test")
    thread.start()
    thread.join(JOIN_S)
    assert not thread.is_alive()


def _ids(grouped: dict[str, list[object]]) -> dict[str, list[str]]:
    return {task: [t.id for t in traces] for task, traces in grouped.items()}  # type: ignore[attr-defined]


def test_la_chauffe_remplit_l_index_que_lit_la_vue(project: Path) -> None:
    _warm(project)
    index = index_for(project / TRACES_RELPATH)
    assert index.stats() == {"full_loads": 1, "incremental_loads": 0, "parsed_records": 6}

    grouped = workspace_work._traces_by_task(project)  # ce que la vue « attention » appelle

    assert index.stats() == {"full_loads": 1, "incremental_loads": 0, "parsed_records": 6}  # rien de reparsé
    assert sorted(grouped) == ["GAO-0", "GAO-1", "GAO-2"]


def test_meme_reponse_que_l_index_froid(project: Path) -> None:
    cold = TaskTraceIndex(project / TRACES_RELPATH).traces_by_task()
    _warm(project)
    assert _ids(workspace_work._traces_by_task(project)) == _ids(cold)


def test_journal_absent_la_chauffe_ne_leve_pas_et_la_reponse_reste_vide(tmp_path: Path) -> None:
    _warm(tmp_path)
    assert workspace_work._traces_by_task(tmp_path) == {}


def test_journal_corrompu_meme_reponse_que_le_calcul_complet(project: Path) -> None:
    path = project / TRACES_RELPATH
    with open(path, "ab") as fh:
        fh.write(b'{"id": "tronque", "run_id"\n\x00\xff pas du json\n[1, 2]\n')
    cold = TaskTraceIndex(path).traces_by_task()
    _warm(project)
    assert _ids(workspace_work._traces_by_task(project)) == _ids(cold)
    assert sum(len(v) for v in cold.values()) == 6


def test_une_ligne_ajoutee_apres_la_chauffe_ne_parse_que_l_ajout(project: Path) -> None:
    _warm(project)
    _record(TraceLedger((project / TRACES_RELPATH).parent), "GAO-9")
    grouped = workspace_work._traces_by_task(project)
    assert len(grouped["GAO-9"]) == 1
    assert index_for(project / TRACES_RELPATH).stats() == {"full_loads": 1, "incremental_loads": 1, "parsed_records": 7}


def test_journal_tronque_ou_remplace_apres_la_chauffe(project: Path) -> None:
    path = project / TRACES_RELPATH
    _warm(project)
    original = path.read_bytes()
    first_line = original.split(b"\n", 1)[0] + b"\n"

    path.write_bytes(first_line)  # tronqué
    assert sum(len(v) for v in workspace_work._traces_by_task(project).values()) == 1

    replacement = path.with_name("autre.jsonl")  # remplacé : autre inode, autre contenu
    replacement.write_bytes(original + original)
    replacement.replace(path)
    assert sum(len(v) for v in workspace_work._traces_by_task(project).values()) == 12


def test_aucun_fil_ne_reste_apres_l_arret(project: Path) -> None:
    stop = cockpit_http.start_warm(project)
    for thread in threading.enumerate():
        if thread.name == "cockpit-warm":
            thread.join(JOIN_S)
    stop.set()
    assert [t for t in threading.enumerate() if t.name == "cockpit-warm"] == []


def test_arret_leve_avant_la_chauffe_rien_n_est_construit(project: Path) -> None:
    stop = threading.Event()
    stop.set()
    _warm(project, stop)
    assert index_for(project / TRACES_RELPATH).stats()["parsed_records"] == 0


def test_la_chauffe_n_ecrit_rien_sur_le_disque(project: Path) -> None:
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    _warm(project)
    assert sorted(p.relative_to(project).as_posix() for p in project.rglob("*")) == before
