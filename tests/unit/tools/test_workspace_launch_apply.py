"""P47 lot 3 : l'apply d'assignation — l'empreinte du plan engage le lancement (ADR-008).

``launch_apply`` refuse sans empreinte, refuse une empreinte périmée (l'état a
changé depuis le plan montré), lance par le ``WorkerLauncher`` du tick — toutes
ses gardes comprises — contre un faux ``claude``, est idempotent par tâche, et
journalise ``apply_started``/``apply_effect`` en ajout seul, fail-closed à
l'entrée : pas de trace, pas d'effet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Les doubles des lots 2 à 4 de #876 (faux ``claude``, dépôt git avec remote nue)
# vivent à côté des tests du lanceur ; ce test lance par le même lanceur.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "missions"))
from lot2_doubles import FakeHost, make_repo

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.launcher import LAUNCHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools.workspace_launch_apply import launch_apply
from grimoire.tools.workspace_launch_preflight import launch_preflight, plan_fingerprint

ACC = "le lancement se constate"
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
JOURNAL = Path("_grimoire-runtime-output/cockpit/launch-apply.jsonl")


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 2\n"
        "      sessions_per_day: 5\n      permission_mode: acceptEdits\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


def _ready(service: TaskService, title: str) -> str:
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(
        title="Apply P47", origin="test"
    )
    task = service.ledger.create_task(mission.id, title, acceptance=(ACC,), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _journal_events(projet: Path) -> list[dict]:
    path = projet / JOURNAL
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── L'empreinte engage l'apply ────────────────────────────────────────────────


def test_sans_empreinte_le_lancement_est_refuse_et_rien_n_est_lance(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    tid = _ready(service, "sans empreinte")
    verdict = launch_apply(projet, tid, {}, exec_=host)
    assert verdict["lance"] is False
    assert "empreinte du plan requise" in verdict["raison"]
    assert not host.calls
    assert not _journal_events(projet)


def test_une_empreinte_perimee_est_refusee_avec_l_empreinte_a_jour(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    tid = _ready(service, "empreinte périmée")
    stale = launch_preflight(projet, tid)["empreinte"]
    # L'état change entre le plan montré et le geste : la tâche est bloquée.
    service.ledger.transition_task(
        tid, TaskState.BLOCKED, actor_id="amelia", reason="le fournisseur ne répond plus"
    )
    verdict = launch_apply(projet, tid, {"empreinte": stale}, exec_=host)
    assert verdict["lance"] is False
    assert "l'état a changé depuis le plan montré" in verdict["raison"]
    fresh = verdict["empreinte"]
    assert fresh and fresh != stale
    assert fresh == launch_preflight(projet, tid)["empreinte"]
    assert not host.calls
    assert not _journal_events(projet)


def test_le_detail_d_une_verification_ne_change_pas_l_empreinte_mais_son_verdict_oui() -> None:
    payload = {
        "task_id": "GAO-x", "statut": "ready", "pret": True,
        "verifications": [{"id": "quota", "verdict": "ok", "detail": ">= 100/1000 tokens"}],
        "modele": {"recommande": None}, "worktree": {"branche": "task/GAO-x", "existant": False},
        "commande": {"texte": "claude --bg"},
    }
    base = plan_fingerprint(payload)
    # Un compteur bouge sans changer la décision : même empreinte.
    payload["verifications"][0]["detail"] = ">= 900/1000 tokens"
    assert plan_fingerprint(payload) == base
    # Le verdict bascule : l'empreinte change.
    payload["verifications"][0]["verdict"] = "bloquant"
    assert plan_fingerprint(payload) != base


def test_un_plan_pas_pret_est_refuse_meme_avec_la_bonne_empreinte(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    mission = service.ledger.create_mission(title="Apply P47", origin="test").id
    proposed = service.ledger.create_task(mission, "encore proposée", acceptance=(ACC,), owner="amelia").id
    empreinte = launch_preflight(projet, proposed)["empreinte"]
    verdict = launch_apply(projet, proposed, {"empreinte": empreinte}, exec_=host)
    assert verdict["lance"] is False
    assert verdict["raison"].startswith("plan pas prêt : ")
    assert not host.calls


# ── Le lancement réel, par le lanceur du tick ─────────────────────────────────


def test_le_lancement_passe_par_le_lanceur_du_tick_et_se_journalise(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    tid = _ready(service, "lancée depuis le cockpit")
    empreinte = launch_preflight(projet, tid)["empreinte"]
    verdict = launch_apply(projet, tid, {"empreinte": empreinte}, exec_=host)
    assert verdict["lance"] is True, verdict
    assert (verdict["statut"], verdict["raison"]) == ("launched", "")
    assert verdict["session"]
    # Le lanceur du tick a lancé : un seul `claude --bg`, l'événement au ledger.
    assert len(host.of("claude", "--bg")) == 1
    # Relire depuis le disque : le `service` de la fixture a son propre cache.
    events = [e for e in TaskService(projet).ledger.list_events() if e.event_type == LAUNCHED_EVENT]
    assert [e.entity_id for e in events] == [tid]
    # Le journal de l'apply : apply_started puis apply_effect, en ajout seul.
    journal = _journal_events(projet)
    assert [e["event"] for e in journal] == ["apply_started", "apply_effect"]
    assert journal[0]["empreinte"] == empreinte
    assert (journal[1]["status"], journal[1]["task_id"]) == ("launched", tid)
    assert all(e["ts"] for e in journal)


def test_relancer_une_tache_deja_lancee_rend_sa_session_sans_rien_relancer(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    tid = _ready(service, "idempotente")
    empreinte = launch_preflight(projet, tid)["empreinte"]
    first = launch_apply(projet, tid, {"empreinte": empreinte}, exec_=host)
    assert first["lance"] is True
    # Second geste, même une empreinte périmée : la session existante, rien de neuf.
    second = launch_apply(projet, tid, {"empreinte": empreinte}, exec_=host)
    assert second["lance"] is False
    assert second["deja_lancee"] is True
    assert second["session"] == first["session"]
    assert len(host.of("claude", "--bg")) == 1
    assert [e["event"] for e in _journal_events(projet)] == ["apply_started", "apply_effect"]


def test_un_role_worker_desactive_rend_skipped_avec_sa_raison(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    (projet / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: false\n", encoding="utf-8"
    )
    tid = _ready(service, "rôle désactivé")
    empreinte = launch_preflight(projet, tid)["empreinte"]
    verdict = launch_apply(projet, tid, {"empreinte": empreinte}, exec_=host)
    assert (verdict["lance"], verdict["statut"]) == (False, "skipped")
    assert "rôle worker désactivé" in verdict["raison"]
    assert not host.of("claude", "--bg")
    # Le geste est tracé quand même : started, puis l'effet « skipped ».
    assert [e["event"] for e in _journal_events(projet)] == ["apply_started", "apply_effect"]


def test_un_journal_non_inscriptible_refuse_avant_tout_effet(
    projet: Path, service: TaskService, host: FakeHost
) -> None:
    tid = _ready(service, "fail-closed")
    empreinte = launch_preflight(projet, tid)["empreinte"]
    # Le chemin du journal est un dossier : toute écriture échoue.
    (projet / JOURNAL).mkdir(parents=True)
    verdict = launch_apply(projet, tid, {"empreinte": empreinte}, exec_=host)
    assert verdict["lance"] is False
    assert "journal de l'apply non inscriptible" in verdict["raison"]
    assert not host.calls
    assert not [e for e in TaskService(projet).ledger.list_events() if e.event_type == LAUNCHED_EVENT]


def test_une_tache_inconnue_leve_l_erreur_mission(projet: Path, service: TaskService, host: FakeHost) -> None:
    _ready(service, "pour que le ledger existe")
    with pytest.raises(GrimoireMissionError):
        launch_apply(projet, "GAO-inconnue-000", {"empreinte": "x"}, exec_=host)
