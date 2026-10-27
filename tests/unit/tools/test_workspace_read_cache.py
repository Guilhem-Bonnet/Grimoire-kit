"""P79 : ce que le flux en direct relance (tâches, attention, sessions, fiche) se calcule une fois par empreinte.

Constat du 2026-10-27 : huit workers faisaient bouger l'empreinte plusieurs fois par seconde, chaque onglet relisait
``tasks``, ``attention`` et ``sessions``, et le serveur recalculait tout à chaque fois (``attention`` 7,6 s, une
lecture lourde voisine 67 s au lieu de 0,35 s). Chaque test dit ce qu'il empêche.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_api, workspace_read_cache
from grimoire.tools.workspace_routes import LIVE_ROUTES, PREFIX, workspace_get

TASKS = f"{PREFIX}tasks"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    workspace_read_cache.clear()


def _counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = workspace_api.tasks_view

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(workspace_api, "tasks_view", spy)
    return calls


def test_les_lectures_du_direct_sont_gardees() -> None:
    assert {TASKS, f"{PREFIX}attention", f"{PREFIX}sessions", f"{PREFIX}sheet"} <= LIVE_ROUTES


def test_sans_ecriture_la_seconde_lecture_ne_recalcule_pas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché : deux onglets, ou deux pouls sans écriture au ledger, payent deux fois le même calcul."""
    TaskService(tmp_path).add("Une tâche à lister", ("critère",), actor="t", owner="guilhem", ready=True)
    calls = _counted(monkeypatch)
    first = workspace_get(tmp_path, TASKS, {})
    second = workspace_get(tmp_path, TASKS, {})
    assert len(calls) == 1 and second == first


def test_une_ecriture_au_ledger_fait_recalculer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché : un cache qui resservirait la liste d'avant une écriture ; la page ne redemanderait pas."""
    service = TaskService(tmp_path)
    tid = service.add("Une tâche notée", ("critère",), actor="t", owner="guilhem", ready=True).task.id
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, {})
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "agent-a")
    after = workspace_get(tmp_path, TASKS, {})
    assert len(calls) == 2
    assert any(t.get("id") == tid and t.get("status") == "running" for t in after["tasks"])


def test_les_parametres_et_le_projet_font_partie_de_la_cle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "autre"
    other.mkdir()
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, {})
    workspace_get(tmp_path, TASKS, {"status": ["ready"]})
    workspace_get(other, TASKS, {})
    assert len(calls) == 3


def test_une_reponse_trop_vieille_est_recalculee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché : la vivacité d'une session (horloge) figée tant que rien n'est écrit."""
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, {})
    later = time.monotonic() + workspace_read_cache.MAX_AGE_SECONDS + 1
    monkeypatch.setattr(workspace_read_cache.time, "monotonic", lambda: later)
    workspace_get(tmp_path, TASKS, {})
    assert len(calls) == 2


def test_une_erreur_n_est_pas_gardee(tmp_path: Path) -> None:
    attempts: list[int] = []

    def boom() -> Any:
        attempts.append(1)
        raise RuntimeError("panne")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            workspace_read_cache.cached_read("r", tmp_path, {}, boom, lambda: "même")
    assert len(attempts) == 2


def test_deux_appels_simultanes_attendent_le_meme_calcul(tmp_path: Path) -> None:
    """Défaut empêché : N onglets au même pouls lancent N calculs en parallèle et saturent le serveur."""
    started = threading.Event()
    release = threading.Event()
    computed: list[int] = []

    def slow() -> str:
        computed.append(1)
        started.set()
        release.wait(5)
        return "réponse"

    results: list[Any] = []
    threads = [
        threading.Thread(target=lambda: results.append(
            workspace_read_cache.cached_read("r", tmp_path, {}, slow, lambda: "même")))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    release.set()
    for thread in threads:
        thread.join(5)
    assert computed == [1] and results == ["réponse"] * 4
