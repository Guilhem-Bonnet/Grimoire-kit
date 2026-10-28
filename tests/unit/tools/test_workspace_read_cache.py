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
LIVE = {"live": ["1"]}


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
    first = workspace_get(tmp_path, TASKS, LIVE)
    second = workspace_get(tmp_path, TASKS, LIVE)
    assert len(calls) == 1 and second == first


def test_une_ecriture_au_ledger_fait_recalculer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché : un cache qui resservirait la liste d'avant une écriture ; la page ne redemanderait pas."""
    service = TaskService(tmp_path)
    tid = service.add("Une tâche notée", ("critère",), actor="t", owner="guilhem", ready=True).task.id
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, LIVE)
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "agent-a")
    after = workspace_get(tmp_path, TASKS, LIVE)
    assert len(calls) == 2
    assert any(t.get("id") == tid and t.get("status") == "running" for t in after["tasks"])


def test_les_parametres_et_le_projet_font_partie_de_la_cle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "autre"
    other.mkdir()
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, LIVE)
    workspace_get(tmp_path, TASKS, {"status": ["ready"], **LIVE})
    workspace_get(other, TASKS, LIVE)
    assert len(calls) == 3


def test_une_reponse_trop_vieille_est_recalculee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché : la vivacité d'une session (horloge) figée tant que rien n'est écrit."""
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, LIVE)
    later = time.monotonic() + workspace_read_cache.MAX_AGE_SECONDS + 1
    monkeypatch.setattr(workspace_read_cache.time, "monotonic", lambda: later)
    workspace_get(tmp_path, TASKS, LIVE)
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


def test_une_ecriture_du_cockpit_vide_le_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché (e2e P75, agents) : une écriture du cockpit qui ne bouge pas l'empreinte (taux CAD, skill retiré)
    resservie périmée jusqu'à MAX_AGE_SECONDS. do_POST et do_PUT vident le cache une fois l'écriture faite."""
    from grimoire.cli import cmd_cockpit

    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, LIVE)
    cmd_cockpit._forget_live_reads()
    workspace_get(tmp_path, TASKS, LIVE)
    assert len(calls) == 2


def test_sans_live_la_lecture_est_toujours_recalculee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché (e2e agent_freshness) : un rechargement de page resservi depuis le cache alors qu'un fichier hors
    empreinte (définition d'agent, traces) a changé. Seul un rechargement du direct (``live=1``) est gardé."""
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, LIVE)
    workspace_get(tmp_path, TASKS, {})
    workspace_get(tmp_path, TASKS, {})
    assert len(calls) == 3


def test_un_calcul_en_vol_pendant_une_ecriture_n_est_pas_garde(tmp_path: Path) -> None:
    """Défaut empêché (e2e portefeuille, CI du 2026-10-28) : une lecture commencée avant une écriture du cockpit,
    finie après le vidage, remettait l'état d'avant au cache ; plus rien ne relisait, la page restait figée."""
    calls: list[int] = []

    def compute_during_write() -> str:
        calls.append(1)
        workspace_read_cache.clear()  # l'écriture du cockpit arrive pendant le calcul
        return "avant l'écriture"

    assert workspace_read_cache.cached_read("r", tmp_path, {}, compute_during_write, lambda: "même") == "avant l'écriture"
    assert workspace_read_cache.cached_read("r", tmp_path, {}, lambda: "après", lambda: "même") == "après"


def test_la_fiche_d_un_autre_projet_suit_l_empreinte_du_portefeuille(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché : la fiche d'un projet regardé en portefeuille, gardée avec l'empreinte du seul projet servi,
    ne bougeait pas quand l'autre projet écrivait."""
    from grimoire.tools import workspace_pulse

    seen: list[bool] = []
    real = workspace_pulse.fingerprint_of

    def spy(root: Path, *, portfolio: bool = False, now: Any = None) -> str:
        seen.append(portfolio)
        return real(root, portfolio=portfolio)

    monkeypatch.setattr(workspace_pulse, "fingerprint_of", spy)
    workspace_get(tmp_path, f"{PREFIX}sheet", {"project": ["beta"], **LIVE})
    workspace_get(tmp_path, f"{PREFIX}sheet", LIVE)
    assert seen == [True, False]
