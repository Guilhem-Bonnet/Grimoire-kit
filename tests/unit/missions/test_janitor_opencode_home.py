"""Le janitor purge les ``HOME`` jetables des workers opencode (suite de #1310, épique #1281 L4).

Un ``HOME`` isolé (``tmp_path``) porte le cache du kit : le vrai ``HOME`` n'est jamais touché. Le processus du worker
est simulé par un ensemble de pids vivants injecté dans :class:`OpencodeWorkerHost`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, make_repo

from grimoire.missions.janitor import ACTION_EVENT, Janitor
from grimoire.missions.launcher import LAUNCHED_EVENT, WORKER_RESUMED_EVENT
from grimoire.missions.opencode_homes import ORPHAN_AGE_S, purge_task_homes, sweep_orphan_homes
from grimoire.missions.orchestrator_policy import OrchestratorPolicy
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.worker_guard import new_opencode_home, opencode_home
from grimoire.missions.worker_host import OpencodeWorkerHost


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    return home


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


class Workers:
    """Les pids vivants ; ``stoppable`` faux : l'arrêt est refusé."""

    def __init__(self) -> None:
        self.live: set[int] = set()
        self.stoppable = True

    def host(self) -> OpencodeWorkerHost:
        def kill(pid: int) -> bool:
            if not self.stoppable:
                return False
            self.live.discard(pid)
            return True

        return OpencodeWorkerHost(alive=lambda pid: pid in self.live, kill=kill)


def launched(
    projet: Path, workers: Workers, pid: int = 4242, *, state: TaskState | None = TaskState.CANCELLED
) -> tuple[str, Path]:
    """Une tâche dont le worker opencode *pid* tourne dans un ``HOME`` du kit neuf, puis passée à *state*."""
    service = TaskService(projet)
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(
        title="L4", origin="test"
    )
    task = service.ledger.create_task(mission.id, "travail", acceptance=("ok",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    home = new_opencode_home(projet, task.id)
    (home / "work").mkdir(parents=True)
    (home / ".local" / "share" / "opencode").mkdir(parents=True)
    (home / ".local" / "share" / "opencode" / "sessions.db").write_text("x", encoding="utf-8")
    service.ledger.append_event(
        LAUNCHED_EVENT,
        task.id,
        "task",
        "worker",
        {"session": "ses_1", "session_id": "ses_1", "host": "opencode", "pid": str(pid), "kit_home": str(home)},
    )
    workers.live.add(pid)
    if state is not None:
        service.ledger.transition_task(task.id, state, actor_id="test")
    return task.id, home


def janitor(projet: Path, workers: Workers) -> Janitor:
    return Janitor(TaskService(projet), OrchestratorPolicy(), exec_=FakeHost(), opencode=workers.host())


def actions(outcomes, action: str) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return [(o.status, o.detail) for o in outcomes if o.action == action]


def age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    for current in (path, *path.iterdir()):
        os.utime(current, (old, old), follow_symlinks=False)


def test_le_janitor_purge_le_home_apres_arret_du_worker(projet: Path) -> None:
    workers = Workers()
    tid, home = launched(projet, workers)
    assert home.is_relative_to(opencode_home(projet)) and home.is_dir()
    outcomes = janitor(projet, workers).sweep()
    assert actions(outcomes, "session")[0][0] == "done"
    assert actions(outcomes, "opencode_home") == [("done", f"HOME opencode retiré : {home.name}")]
    assert not home.exists() and opencode_home(projet).is_dir()
    journal = [e.payload for e in TaskService(projet).ledger.events_for(tid) if e.event_type == ACTION_EVENT]
    assert {"action": "opencode_home", "status": "done", "detail": f"HOME opencode retiré : {home.name}"} in journal
    assert janitor(projet, workers).sweep() == ()  # swept : rien n'est refait


def test_le_janitor_garde_le_home_d_un_worker_vivant_puis_le_purge_a_sa_fin(projet: Path) -> None:
    workers = Workers()
    workers.stoppable = False
    _tid, home = launched(projet, workers)
    outcomes = janitor(projet, workers).sweep()
    assert actions(outcomes, "session")[0][0] == "failed"
    status, detail = actions(outcomes, "opencode_home")[0]
    assert status == "failed" and "encore vivant" in detail and home.is_dir()
    workers.live.clear()  # le processus finit de lui-même : le tick suivant rejoue et purge
    assert actions(janitor(projet, workers).sweep(), "opencode_home")[0][0] == "done"
    assert not home.exists()


def test_le_janitor_purge_aussi_le_home_d_une_tentative_precedente(projet: Path) -> None:
    workers = Workers()
    tid, home = launched(projet, workers, state=None)
    earlier = new_opencode_home(projet, tid)
    earlier.mkdir()
    service = TaskService(projet)
    service.ledger.append_event(
        LAUNCHED_EVENT, tid, "task", "worker", {"host": "opencode", "pid": "1", "kit_home": str(earlier)}
    )
    service.ledger.append_event(
        WORKER_RESUMED_EVENT, tid, "task", "worker", {"host": "opencode", "pid": "4243", "kit_home": str(home)}
    )
    workers.live = {4243}
    service.ledger.transition_task(tid, TaskState.CANCELLED, actor_id="test")
    assert purge_task_homes(TaskService(projet), tid, workers.host().alive)[0] == "failed"  # le pid de reprise vit
    assert home.is_dir() and not earlier.exists()  # la tentative morte est déjà partie
    workers.live.clear()
    assert purge_task_homes(TaskService(projet), tid, workers.host().alive)[0] == "done"
    assert not home.exists()


def test_le_janitor_refuse_un_lien_symbolique_sortant_de_la_racine(projet: Path, tmp_path: Path) -> None:
    workers = Workers()
    tid, home = launched(projet, workers, state=None)
    outside = tmp_path / "precieux"
    outside.mkdir()
    (outside / "garde.txt").write_text("à garder", encoding="utf-8")
    import shutil

    shutil.rmtree(home)
    home.symlink_to(outside, target_is_directory=True)
    workers.live.clear()
    TaskService(projet).ledger.transition_task(tid, TaskState.CANCELLED, actor_id="test")
    status, detail = actions(janitor(projet, workers).sweep(), "opencode_home")[0]
    assert status == "skipped" and "lien symbolique" in detail
    assert home.is_symlink() and (outside / "garde.txt").read_text(encoding="utf-8") == "à garder"


def test_le_janitor_ne_touche_pas_un_home_hors_de_la_racine(projet: Path, tmp_path: Path) -> None:
    workers = Workers()
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="L4", origin="test")
    task = service.ledger.create_task(mission.id, "travail", acceptance=("ok",), owner="amelia")
    stranger = tmp_path / "ailleurs" / f"{task.id}-0123456789abcdef"
    stranger.mkdir(parents=True)
    escaped = opencode_home(projet) / ".." / f"{task.id}-0123456789abcdef"  # nom valide, mais ``..`` sort de la racine
    service.ledger.append_event(
        LAUNCHED_EVENT, task.id, "task", "worker", {"host": "opencode", "pid": "9", "kit_home": str(stranger)}
    )
    service.ledger.append_event(
        LAUNCHED_EVENT, task.id, "task", "worker", {"host": "opencode", "pid": "9", "kit_home": str(escaped)}
    )
    status, detail = purge_task_homes(TaskService(projet), task.id, workers.host().alive)
    assert status == "skipped" and detail.count("hors de la racine") == 2
    assert stranger.is_dir()


def test_le_janitor_balaie_les_homes_orphelins_de_plus_de_sept_jours(projet: Path, tmp_path: Path) -> None:
    workers = Workers()
    root = opencode_home(projet)
    orphan = new_opencode_home(projet, "GAO-x-001")
    recent = new_opencode_home(projet, "GAO-x-002")
    for path in (orphan, recent):
        (path / "work").mkdir(parents=True)
    in_review, review_home = launched(projet, workers, state=None)  # en attente de reprise : son HOME reste
    workers.live.clear()
    alive_tid, alive_home = launched(projet, workers, pid=777)  # tâche terminale, worker encore vivant
    outside = tmp_path / "precieux"
    outside.mkdir()
    link = root / "GAO-x-003-0123456789abcdef"
    link.symlink_to(outside, target_is_directory=True)
    stray = root / "notes.txt"
    stray.write_text("x", encoding="utf-8")
    for path in (orphan, review_home, alive_home):
        age(path, ORPHAN_AGE_S + 60)
    age(recent, ORPHAN_AGE_S - 3600)
    removed = sweep_orphan_homes(TaskService(projet), [in_review], workers.host().alive)
    assert removed == [orphan.name]
    assert not orphan.exists()
    assert recent.is_dir() and review_home.is_dir() and alive_home.is_dir() and link.is_symlink() and stray.is_file()
    assert outside.is_dir() and alive_tid


def test_le_janitor_rapporte_les_orphelins_retires_dans_son_passage(projet: Path) -> None:
    workers = Workers()
    orphan = new_opencode_home(projet, "GAO-x-001")
    (orphan / "work").mkdir(parents=True)
    age(orphan, ORPHAN_AGE_S + 60)
    outcomes = janitor(projet, workers).sweep()
    assert actions(outcomes, "opencode_orphans") == [("done", f"HOME opencode orphelin retiré : {orphan.name}")]
    assert not orphan.exists()


def test_le_janitor_sans_racine_opencode_ne_balaie_rien(projet: Path) -> None:
    assert sweep_orphan_homes(TaskService(projet), [], Workers().host().alive) == []


def _create_task(projet: Path) -> str:
    service = TaskService(projet)
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(
        title="L4", origin="test"
    )
    return service.ledger.create_task(mission.id, "travail", acceptance=("ok",), owner="amelia").id


def _note(projet: Path, tid: str, home: Path, pid: str | None, event: str = LAUNCHED_EVENT) -> None:
    payload = {"host": "opencode", "kit_home": str(home)}
    if pid is not None:
        payload["pid"] = pid
    TaskService(projet).ledger.append_event(event, tid, "task", "worker", payload)


def test_le_home_reste_si_une_autre_tache_y_note_un_worker_vivant(projet: Path) -> None:
    workers = Workers()
    tid, home = launched(projet, workers, pid=4242)
    workers.live = {9999}  # le worker de la tâche purgée est mort…
    other = _create_task(projet)
    _note(projet, other, home, "9999")  # …mais une autre tâche a noté un worker vivant dans le même HOME
    status, detail = purge_task_homes(TaskService(projet), tid, workers.host().alive)
    assert status == "failed" and "pid 9999" in detail and home.is_dir()
    workers.live.clear()
    assert purge_task_homes(TaskService(projet), tid, workers.host().alive)[0] == "done"
    assert not home.exists()


def test_une_entree_sans_pid_n_efface_pas_le_pid_vivant(projet: Path) -> None:
    workers = Workers()
    tid, home = launched(projet, workers, pid=4242)  # 4242 vit
    _note(projet, tid, home, None, WORKER_RESUMED_EVENT)  # une entrée plus récente sans pid
    status, detail = purge_task_homes(TaskService(projet), tid, workers.host().alive)
    assert status == "failed" and "pid 4242" in detail and home.is_dir()


def test_un_home_note_sans_pid_est_garde(projet: Path) -> None:
    workers = Workers()
    tid = _create_task(projet)
    home = new_opencode_home(projet, tid)
    (home / "work").mkdir(parents=True)
    _note(projet, tid, home, "")
    TaskService(projet).ledger.transition_task(tid, TaskState.CANCELLED, actor_id="test")
    status, detail = purge_task_homes(TaskService(projet), tid, workers.host().alive)
    assert status == "skipped" and "pid inconnu" in detail and home.is_dir()
    age(home, ORPHAN_AGE_S + 60)
    assert sweep_orphan_homes(TaskService(projet), [], workers.host().alive) == []  # l'orphelin aussi le garde
    assert home.is_dir()


def test_les_pids_de_tous_les_lancements_d_un_home_sont_gardes(projet: Path) -> None:
    workers = Workers()
    tid, home = launched(projet, workers, pid=4242)
    _note(projet, tid, home, "4243", WORKER_RESUMED_EVENT)  # la reprise note un autre pid, 4242 vit toujours
    status, detail = purge_task_homes(TaskService(projet), tid, workers.host().alive)
    assert status == "failed" and "pid 4242" in detail and home.is_dir()


def test_l_orphelin_reste_si_une_autre_tache_y_note_un_worker_vivant(projet: Path) -> None:
    workers = Workers()
    tid, home = launched(projet, workers, pid=4242)
    workers.live = {9999}
    _note(projet, _create_task(projet), home, "9999")
    age(home, ORPHAN_AGE_S + 60)
    assert sweep_orphan_homes(TaskService(projet), [], workers.host().alive) == []
    assert home.is_dir() and tid


def test_un_echange_par_un_lien_apres_la_verification_est_refuse(
    projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.missions import opencode_homes

    if not opencode_homes._fd_removal_supported():
        pytest.skip("suppression par descripteur indisponible sur cette plateforme")
    root = opencode_home(projet)
    root.mkdir(parents=True)
    outside = tmp_path / "precieux"
    outside.mkdir()
    (outside / "garde.txt").write_text("à garder", encoding="utf-8")
    swapped = root / "GAO-x-001-0123456789abcdef"
    swapped.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        opencode_homes, "_refusal", lambda _root, _home: ""
    )  # la vérification est passée avant l'échange
    assert opencode_homes.purge_home(root, swapped) == (False, "lien symbolique")
    assert swapped.is_symlink() and (outside / "garde.txt").read_text(encoding="utf-8") == "à garder"


def test_une_panne_du_balayage_des_orphelins_ne_fait_pas_tomber_le_tick(
    projet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.missions import opencode_homes

    def boom(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(opencode_homes, "sweep_orphan_homes", boom)
    outcomes = janitor(projet, Workers()).sweep()
    assert actions(outcomes, "opencode_orphans") == [("failed", "RuntimeError")]
