"""P79 / GAO-perf-cockpit-002 : les lectures lourdes (tâches, attention, sessions, fiche) se calculent une fois
par empreinte — rechargement du direct *et* ouverture de page.

Constat du 2026-10-27 : huit workers faisaient bouger l'empreinte plusieurs fois par seconde, chaque onglet relisait
``tasks``, ``attention`` et ``sessions``, et le serveur recalculait tout à chaque fois (``attention`` 7,6 s, une
lecture lourde voisine 67 s au lieu de 0,35 s). Constat du 2026-11-07 : ce cache ne servait que les requêtes marquées
``pulse_reload=1`` ; l'ouverture de page et la navigation, elles, payaient le calcul complet. Chaque test dit ce
qu'il empêche.
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
from grimoire.tools.workspace_read_cache import cacheable_read
from grimoire.tools.workspace_routes import LIVE_ROUTES, PREFIX, workspace_get

TASKS = f"{PREFIX}tasks"
ATTENTION = f"{PREFIX}attention"
SESSIONS = f"{PREFIX}sessions"
SHEET = f"{PREFIX}sheet"
LIVE = {"pulse_reload": ["1"]}


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


def _write_trace(root: Path, line: str = '{"kind": "trace"}\n') -> None:
    """Écrit hors cockpit, comme le tick ou un hook : le TraceLedger. Rien ne passe par le serveur."""
    traces = root / "_grimoire-output" / "traces" / "traces.jsonl"
    traces.parent.mkdir(parents=True, exist_ok=True)
    with traces.open("a", encoding="utf-8") as fh:
        fh.write(line)


def test_les_lectures_du_direct_sont_gardees() -> None:
    assert {TASKS, ATTENTION, SESSIONS, SHEET} <= LIVE_ROUTES


def test_les_quatre_vues_lourdes_passent_par_le_cache_sans_marqueur() -> None:
    """L'ouverture de page n'envoie pas ``pulse_reload`` : elle doit pourtant être servie par le cache."""
    for path in (TASKS, ATTENTION, SESSIONS, SHEET):
        assert cacheable_read(path, {}, live_routes=LIVE_ROUTES), path
        assert cacheable_read(path, dict(LIVE), live_routes=LIVE_ROUTES), path


def test_le_portefeuille_et_une_sonde_demandee_ne_sont_jamais_gardes() -> None:
    assert not cacheable_read(TASKS, {"scope": ["portfolio"]}, live_routes=LIVE_ROUTES)
    assert not cacheable_read(ATTENTION, {"probe": ["1"]}, live_routes=LIVE_ROUTES)
    assert cacheable_read(ATTENTION, {"probe": ["0"]}, live_routes=LIVE_ROUTES)


def test_deux_get_sans_pulse_reload_font_un_seul_calcul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché : une ouverture de page et une navigation paient deux fois le même calcul."""
    TaskService(tmp_path).add("Une tâche à lister", ("critère",), actor="t", owner="guilhem", ready=True)
    calls = _counted(monkeypatch)
    first = workspace_get(tmp_path, TASKS, {})
    second = workspace_get(tmp_path, TASKS, {})
    assert len(calls) == 1 and second == first
    # Une page plus ancienne qui porte encore le marqueur du direct partage la même entrée, même empreinte.
    assert workspace_get(tmp_path, TASKS, dict(LIVE)) == first
    assert len(calls) == 1


def test_une_ecriture_hors_cockpit_dans_traces_fait_recalculer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut empêché : une trace écrite par le tick ou un hook reste invisible jusqu'à MAX_AGE_SECONDS."""
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, {})
    _write_trace(tmp_path)
    workspace_get(tmp_path, TASKS, {})
    assert len(calls) == 2


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


def test_un_projet_nomme_dans_l_url_est_garde_pour_sa_propre_racine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?project=`` est résolu par l'hôte avant la route : la lecture porte sur cette racine, son empreinte la couvre."""
    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, {"project": ["served"]})
    workspace_get(tmp_path, TASKS, {"project": ["served"]})
    assert len(calls) == 1


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


def test_une_ecriture_du_cockpit_vide_le_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché (e2e P75, agents) : une écriture du cockpit qui ne bouge pas l'empreinte (taux CAD, skill retiré)
    resservie périmée jusqu'à MAX_AGE_SECONDS. do_POST et do_PUT vident le cache une fois l'écriture faite."""
    from grimoire.cli import cmd_cockpit

    calls = _counted(monkeypatch)
    workspace_get(tmp_path, TASKS, {})
    cmd_cockpit._forget_live_reads()
    workspace_get(tmp_path, TASKS, {})
    assert len(calls) == 2


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


def test_une_lecture_du_portefeuille_n_est_jamais_gardee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché (e2e du portefeuille rouge en CI) : une lecture d'agrégat, gardée avec l'empreinte du seul projet
    servi, restait figée quand un autre projet écrivait."""
    calls = _counted(monkeypatch)
    for _ in range(2):
        workspace_get(tmp_path, TASKS, {"scope": ["portfolio"], **LIVE})
    assert len(calls) == 2


def test_le_marqueur_du_direct_ne_filtre_jamais_le_portefeuille(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché (CI de main, 2026-10-29) : le marqueur s'appelait `live`, le filtre « Sessions vivantes » de la
    liste du portefeuille ; un rechargement du direct ne gardait que les tâches tenues par une session vivante."""
    from grimoire.tools import workspace_portfolio

    seen: list[bool] = []

    def spy(root: Path, **kwargs: Any) -> dict[str, Any]:
        seen.append(bool(kwargs.get("live")))
        return {"tasks": [], "count": 0}

    monkeypatch.setattr(workspace_portfolio, "portfolio_tasks", spy)
    route = f"{PREFIX}portfolio/tasks"
    workspace_get(tmp_path, route, dict(LIVE))  # rechargement du direct : aucun filtre
    workspace_get(tmp_path, route, {"live": ["1"], **LIVE})  # filtre « Sessions vivantes » demandé
    assert seen == [False, True]
