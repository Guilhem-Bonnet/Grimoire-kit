"""P47 lot 1 : le préflight d'assignation — plan en lecture seule, rien n'est lancé.

``launch_preflight`` ne rend que ce que le tick réel lirait : le plan à blanc de
l'admission, la règle de réservation du claim_guard, les plafonds du lanceur et
la classe de vérifiabilité. Ce qui n'est pas mesurable (politique illisible,
quota illisible, réservations non croisables) sort ``inconnu`` — jamais deviné.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.admission import QUOTA_CONSUMED_EVENT, HostReading, RouteRef
from grimoire.missions.launcher import LAUNCH_FAILED_EVENT, LAUNCHED_EVENT
from grimoire.missions.schemas import DependencyKind, MissionTask, TaskDependency, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import WORKTREE_CREATED_EVENT
from grimoire.tools.workspace_launch_preflight import launch_preflight

ACC = "le préflight se lit"
STANDARD = Path("_grimoire/standard")

READING = HostReading(load_per_cpu=0.42, mem_available_mb=2048)


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def service(projet: Path) -> TaskService:
    return TaskService(projet)


@pytest.fixture
def mission(service: TaskService) -> str:
    return service.ledger.create_mission(title="Préflight", origin="test").id


def _capacity(projet: Path, text: str) -> None:
    (projet / STANDARD / "capacity.yaml").write_text(text, encoding="utf-8")


def _policy(projet: Path, text: str) -> None:
    (projet / STANDARD / "orchestration-policy.yaml").write_text(text, encoding="utf-8")


def _pret(service: TaskService, mission: str, titre: str, **kw: object) -> MissionTask:
    kw.setdefault("owner", "amelia")
    task = service.ledger.create_task(mission, titre, acceptance=(ACC,), **kw)  # type: ignore[arg-type]
    service.ledger.transition_task(task.id, TaskState.READY)
    return task


def _plan(projet: Path, task_id: str, **kw: object) -> dict:
    kw.setdefault("probe", lambda: READING)
    return launch_preflight(projet, task_id, **kw)  # type: ignore[arg-type]


def _verif(plan: dict, vid: str) -> dict:
    found = [v for v in plan["verifications"] if v["id"] == vid]
    assert found, f"vérification {vid} absente de {[v['id'] for v in plan['verifications']]}"
    return found[0]


# ── Payload complet ───────────────────────────────────────────────────────────


def test_le_payload_complet_d_une_tache_admissible_est_pret(
    projet: Path, service: TaskService, mission: str
) -> None:
    task = _pret(service, mission, "libre d'entrer")
    plan = _plan(projet, task.id)
    assert (plan["task_id"], plan["titre"], plan["statut"]) == (task.id, "libre d'entrer", "ready")
    # Le bloc modèle : classe recalculée (V2 : critère ambigu), palier et justification jamais vides.
    assert plan["modele"]["classe"] == "V2"
    assert plan["modele"]["palier"] == "session"
    assert plan["modele"]["recommande"] is None  # aucun modèle (politique par défaut, pas de route)
    assert plan["modele"]["justification"]
    assert "aucun" in plan["modele"]["source"]
    # Worktree proposé : la convention du lanceur, branche task/<id>.
    assert plan["worktree"]["branche"] == f"task/{task.id}"
    assert plan["worktree"]["existant"] is False
    # Les cinq vérifications, dans l'ordre de la maquette.
    assert [v["id"] for v in plan["verifications"]] == [
        "conflit_reservation", "plafond_sessions", "admission", "dependances", "quota",
    ]
    assert _verif(plan, "conflit_reservation")["verdict"] == "ok"
    assert _verif(plan, "admission") == {
        "id": "admission", "libelle": "Admission", "verdict": "ok", "detail": "serait admise maintenant (Admit)",
    }
    assert _verif(plan, "dependances")["verdict"] == "ok"
    assert _verif(plan, "quota")["verdict"] == "ok"
    assert "illimité" in _verif(plan, "quota")["detail"]
    # Politique par défaut : worker désactivé — un warn, qui ne bloque pas le préflight.
    plafond = _verif(plan, "plafond_sessions")
    assert plafond["verdict"] == "warn"
    assert "rôle worker désactivé" in plafond["detail"]
    assert "0/1 session(s) actives" in plafond["detail"]
    # La commande équivalente : celle du lanceur, sans modèle ni permission-mode (politique par défaut).
    assert plan["commande"]["argv"][:4] == ["claude", "--bg", "--name", f"grimoire-{task.id}"]
    assert "--model" not in plan["commande"]["argv"]
    assert "--permission-mode" not in plan["commande"]["argv"]
    assert plan["commande"]["texte"].startswith(f"claude --bg --name grimoire-{task.id}")
    assert plan["commande"]["raison"] == ""
    assert plan["pret"] is True
    assert plan["raison"] == "toutes les vérifications passent"


# ── Tâche inexistante, ledger absent ──────────────────────────────────────────


def test_une_tache_inconnue_leve_l_erreur_mission(projet: Path, service: TaskService, mission: str) -> None:
    _pret(service, mission, "une autre")
    with pytest.raises(GrimoireMissionError, match="inconnue"):
        _plan(projet, "GAO-fantome-001")


def test_sans_ledger_le_prefligh_refuse_plutot_que_deviner(projet: Path) -> None:
    with pytest.raises(GrimoireMissionError, match="aucun ledger"):
        _plan(projet, "GAO-fantome-001")


# ── Conflit de réservation ────────────────────────────────────────────────────


def test_un_fichier_deja_reserve_est_un_verdict_bloquant_et_pret_tombe(
    projet: Path, service: TaskService, mission: str
) -> None:
    tenue = _pret(service, mission, "tient le fichier")
    assert service.claim(tenue.id, "worker", files=("src/x.py",)).task.status is TaskState.CLAIMED
    cible = _pret(service, mission, "veut le même fichier")
    plan = _plan(projet, cible.id, files=("src/x.py", "src/libre.py"))
    conflit = _verif(plan, "conflit_reservation")
    assert conflit["verdict"] == "bloquant"
    assert "src/x.py" in conflit["detail"] and tenue.id in conflit["detail"]
    assert plan["pret"] is False
    assert plan["raison"].startswith("Conflit de réservation :")


def test_des_fichiers_declares_et_libres_sont_ok(projet: Path, service: TaskService, mission: str) -> None:
    cible = _pret(service, mission, "fichiers libres")
    plan = _plan(projet, cible.id, files=("src/libre.py",))
    assert _verif(plan, "conflit_reservation") == {
        "id": "conflit_reservation", "libelle": "Conflit de réservation", "verdict": "ok",
        "detail": "1 fichier(s) exclusif(s) libres",
    }


def test_sans_fichier_declare_face_a_des_reservations_le_verdict_est_inconnu(
    projet: Path, service: TaskService, mission: str
) -> None:
    tenue = _pret(service, mission, "tient des fichiers")
    service.claim(tenue.id, "worker", files=("src/x.py",))
    cible = _pret(service, mission, "ne déclare rien")
    plan = _plan(projet, cible.id)
    conflit = _verif(plan, "conflit_reservation")
    assert conflit["verdict"] == "inconnu"
    assert tenue.id in conflit["detail"] and "non évaluable" in conflit["detail"]
    assert plan["pret"] is False


def test_une_tache_tenue_sans_fichier_ne_rend_pas_le_conflit_inconnu(
    projet: Path, service: TaskService, mission: str
) -> None:
    tenue = _pret(service, mission, "tenue sans fichier")
    service.claim(tenue.id, "worker")
    cible = _pret(service, mission, "ne déclare rien non plus")
    conflit = _verif(_plan(projet, cible.id), "conflit_reservation")
    assert conflit["verdict"] == "ok"
    assert "rien à croiser" in conflit["detail"]


def test_les_fichiers_du_claim_courant_sont_la_source_par_defaut(
    projet: Path, service: TaskService, mission: str
) -> None:
    # Une tâche déjà réclamée avec fichiers : son claim est la source, et elle n'entre plus en file.
    tenue = _pret(service, mission, "déjà réclamée")
    service.claim(tenue.id, "worker", files=("src/y.py",))
    _capacity(projet, "wip:\n  limit_global: 1\n")  # après le claim : la garde l'aurait sinon refusé
    # D'autres tâches peuplent les listes du plan (différée, exclue) sans concerner la cible.
    _pret(service, mission, "différée par le wip")
    autre = service.ledger.create_task(mission, "encore ouverte", acceptance=(ACC,))
    _pret(service, mission, "exclue pour dépendance",
          dependencies=(TaskDependency(kind=DependencyKind.BLOCKS, target=autre.id),))
    plan = _plan(projet, tenue.id)
    assert _verif(plan, "conflit_reservation")["detail"] == "1 fichier(s) exclusif(s) libres"
    admission = _verif(plan, "admission")
    assert admission["verdict"] == "bloquant"
    assert "non candidate" in admission["detail"] and "claimed" in admission["detail"]


# ── Plafond de sessions ───────────────────────────────────────────────────────


def test_une_politique_illisible_rend_le_plafond_et_la_commande_inconnus(
    projet: Path, service: TaskService, mission: str
) -> None:
    _policy(projet, "orchestrator:\n  roles:\n    worker:\n        enabled: true\n")  # permission_mode manquant
    task = _pret(service, mission, "sous politique illisible")
    plan = _plan(projet, task.id)
    plafond = _verif(plan, "plafond_sessions")
    assert plafond["verdict"] == "inconnu"
    assert "orchestration-policy.yaml" in plafond["detail"]
    assert plan["commande"]["argv"] is None and plan["commande"]["texte"] is None
    assert "illisible" in plan["commande"]["raison"]
    # Le modèle retombe sur la seule source restante : la classe de vérifiabilité.
    assert "classe de vérifiabilité" in plan["modele"]["source"]
    assert plan["modele"]["palier"] == "session"
    assert plan["pret"] is False


def test_le_wip_du_worker_atteint_est_bloquant(projet: Path, service: TaskService, mission: str) -> None:
    _policy(projet, "orchestrator:\n  roles:\n    worker:\n        enabled: true\n        permission_mode: acceptEdits\n")
    vivante = _pret(service, mission, "session vivante")
    service.claim(vivante.id, "worker")
    service.ledger.append_event(LAUNCHED_EVENT, vivante.id, "task", "worker", {"session": "abcd1234"})
    # Un lancement orphelin (tâche inconnue du ledger) ne compte pas une session.
    service.ledger.append_event(LAUNCHED_EVENT, "GAO-fantome-001", "task", "worker", {"session": "ffff0000"})
    cible = _pret(service, mission, "voudrait un créneau")
    plafond = _verif(_plan(projet, cible.id), "plafond_sessions")
    assert plafond["verdict"] == "bloquant"
    assert "WIP du worker atteint" in plafond["detail"]
    assert "1/1 session(s) actives" in plafond["detail"]


def test_le_plafond_de_lancements_du_jour_est_bloquant(projet: Path, service: TaskService, mission: str) -> None:
    _policy(
        projet,
        "orchestrator:\n  roles:\n    worker:\n        enabled: true\n        permission_mode: acceptEdits\n        sessions_per_day: 1\n",
    )
    restee = _pret(service, mission, "restée ready")  # lancée puis rendue : ne compte plus au WIP
    service.ledger.append_event(LAUNCH_FAILED_EVENT, restee.id, "task", "worker", {"detail": "essai du jour"})
    cible = _pret(service, mission, "voudrait lancer")
    plafond = _verif(_plan(projet, cible.id), "plafond_sessions")
    assert plafond["verdict"] == "bloquant"
    assert "plafond de lancements du jour atteint" in plafond["detail"]
    assert "1/1 lancement(s) aujourd'hui" in plafond["detail"]


def test_un_worker_actif_sous_les_plafonds_est_ok_et_demain_le_compte_du_jour_retombe(
    projet: Path, service: TaskService, mission: str
) -> None:
    _policy(projet, "orchestrator:\n  roles:\n    worker:\n        enabled: true\n        permission_mode: acceptEdits\n        wip: 2\n")
    service.ledger.append_event(LAUNCH_FAILED_EVENT, "GAO-hier-001", "task", "worker", {"detail": "essai d'hier"})
    cible = _pret(service, mission, "sous les plafonds")
    demain = datetime.now(UTC) + timedelta(days=1)
    plafond = _verif(_plan(projet, cible.id, now=demain), "plafond_sessions")
    assert plafond["verdict"] == "ok"
    assert plafond["detail"] == "0/2 session(s) actives · 0/5 lancement(s) aujourd'hui"


# ── Admission ─────────────────────────────────────────────────────────────────


def test_un_defer_de_wip_global_est_bloquant_avec_sa_raison(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "wip:\n  limit_global: 1\n")
    tenue = _pret(service, mission, "déjà tenue")
    service.claim(tenue.id, "worker")
    cible = _pret(service, mission, "différée")
    plan = _plan(projet, cible.id)
    admission = _verif(plan, "admission")
    assert admission["verdict"] == "bloquant"
    assert "Defer(wip)" in admission["detail"] and "WIP global atteint (1/1)" in admission["detail"]
    assert plan["pret"] is False


def test_un_defer_quota_porte_le_retry_et_le_quota_est_bloquant(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 100\n")
    service.ledger.append_event(QUOTA_CONSUMED_EVENT, "x", "task", "test", {"tokens": 150})
    cible = _pret(service, mission, "au-dessus du quota")
    plan = _plan(projet, cible.id)
    admission = _verif(plan, "admission")
    assert admission["verdict"] == "bloquant"
    assert "Defer(quota)" in admission["detail"] and "retenter vers" in admission["detail"]
    quota = _verif(plan, "quota")
    assert quota["verdict"] == "bloquant"
    assert ">= 150/100 tokens" in quota["detail"]


def test_un_quota_illisible_rend_admission_et_quota_inconnus(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 0\n")
    cible = _pret(service, mission, "sous quota illisible")
    plan = _plan(projet, cible.id)
    assert _verif(plan, "admission")["verdict"] == "inconnu"
    quota = _verif(plan, "quota")
    assert quota["verdict"] == "inconnu" and "tokens_per_day" in quota["detail"]
    assert plan["pret"] is False


def test_un_quota_fini_sous_le_plafond_est_ok_et_une_conso_invisible_inconnue(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 1000\n")
    service.ledger.append_event(QUOTA_CONSUMED_EVENT, "x", "task", "test", {"tokens": 200})
    cible = _pret(service, mission, "sous le plafond")
    assert _verif(_plan(projet, cible.id), "quota") == {
        "id": "quota", "libelle": "Quota de tokens du jour", "verdict": "ok",
        "detail": "mode fini — >= 200/1000 tokens consommés",
    }

def test_une_consommation_invisible_sous_quota_fini_est_inconnue(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 1000\n")
    cible = _pret(service, mission, "consommation invisible")
    # Un dispatch du jour sans aucun chiffre : la consommation est inconnue, jamais estimée.
    service.ledger.append_event("task.dispatched", "x", "task", "test", {})
    quota = _verif(_plan(projet, cible.id), "quota")
    assert quota["verdict"] == "inconnu" and "consommation du jour inconnue" in quota["detail"]


# ── Dépendances ───────────────────────────────────────────────────────────────


def test_une_dependance_blocks_ouverte_bloque_dependances_et_admission(
    projet: Path, service: TaskService, mission: str
) -> None:
    bloqueuse = service.ledger.create_task(mission, "encore ouverte", acceptance=(ACC,))
    cible = _pret(
        service, mission, "dépendante",
        dependencies=(TaskDependency(kind=DependencyKind.BLOCKS, target=bloqueuse.id),),
    )
    plan = _plan(projet, cible.id)
    deps = _verif(plan, "dependances")
    assert deps["verdict"] == "bloquant"
    assert bloqueuse.id in deps["detail"]
    admission = _verif(plan, "admission")
    assert admission["verdict"] == "bloquant"
    assert "exclue de la file" in admission["detail"]
    assert plan["pret"] is False
    assert plan["raison"].startswith("Admission :") or plan["raison"].startswith("Dépendances")


# ── Modèle et commande ────────────────────────────────────────────────────────


def test_le_modele_de_la_politique_prime_et_compose_la_commande(
    projet: Path, service: TaskService, mission: str
) -> None:
    _policy(
        projet,
        "orchestrator:\n  roles:\n    worker:\n        enabled: true\n        model: opus\n        permission_mode: acceptEdits\n",
    )
    task = _pret(service, mission, "modèle imposé")
    plan = _plan(projet, task.id)
    assert plan["modele"]["recommande"] == "opus"
    assert plan["modele"]["source"] == "worker.model de la politique d'orchestration"
    argv = plan["commande"]["argv"]
    assert argv[4:8] == ["--model", "opus", "--permission-mode", "acceptEdits"]
    assert f"grimoire task attach {task.id}" in argv[-1]  # le même prompt que le lanceur
    assert "--model opus" in plan["commande"]["texte"]


def test_la_route_anthropic_du_routeur_donne_son_modele(
    projet: Path, service: TaskService, mission: str
) -> None:
    _policy(projet, "orchestrator:\n  roles:\n    worker:\n        enabled: true\n        permission_mode: acceptEdits\n")
    task = _pret(service, mission, "routée")
    plan = _plan(projet, task.id, route_of=lambda _t: RouteRef("route", "claude", "claude-haiku-4-5"))
    assert plan["modele"]["recommande"] == "claude-haiku-4-5"
    assert plan["modele"]["source"] == "route du routeur (K9)"
    assert "--model claude-haiku-4-5" in plan["commande"]["texte"]


def test_une_route_hors_anthropic_retombe_sur_le_palier_puis_sur_rien(
    projet: Path, service: TaskService, mission: str
) -> None:
    _policy(
        projet,
        "orchestrator:\n  roles:\n    worker:\n        enabled: true\n        permission_mode: acceptEdits\n        tier: strong\n",
    )
    task = _pret(service, mission, "routée ailleurs")
    plan = _plan(projet, task.id, route_of=lambda _t: RouteRef("route", "ollama", "qwen3"))
    assert plan["modele"]["recommande"] == "opus"  # _TIER_ALIAS du lanceur : strong → opus
    assert plan["modele"]["source"] == "palier strong de la politique d'orchestration"
    assert plan["modele"]["palier"] == "strong"


# ── Worktree ──────────────────────────────────────────────────────────────────


def test_un_worktree_note_au_ledger_est_rendu_tel_quel(projet: Path, service: TaskService, mission: str) -> None:
    task = _pret(service, mission, "déjà outillée")
    service.ledger.append_event(
        WORKTREE_CREATED_EVENT, task.id, "task", "worker",
        {"path": "/depot/.git/grimoire/worktrees/" + task.id, "branch": f"task/{task.id}",
         "base_sha": "deadbeef", "into": "main"},
    )
    wt = _plan(projet, task.id)["worktree"]
    assert wt == {
        "chemin": f"/depot/.git/grimoire/worktrees/{task.id}", "branche": f"task/{task.id}",
        "existant": True, "detail": "",
    }


def test_dans_un_depot_git_le_chemin_propose_suit_la_convention_du_lanceur(
    projet: Path, service: TaskService, mission: str
) -> None:
    subprocess.run(["git", "init", "--quiet", str(projet)], check=True)
    task = _pret(service, mission, "proposée")
    wt = _plan(projet, task.id)["worktree"]
    assert wt["existant"] is False and wt["detail"] == ""
    assert wt["chemin"] == str(projet / ".git" / "grimoire" / "worktrees" / task.id)


def test_hors_depot_git_le_chemin_est_inconnu_jamais_devine(
    projet: Path, service: TaskService, mission: str
) -> None:
    task = _pret(service, mission, "hors dépôt")
    wt = _plan(projet, task.id)["worktree"]
    assert wt["chemin"] is None
    assert "illisible" in wt["detail"]


def test_un_git_absent_rend_le_chemin_inconnu(
    projet: Path, service: TaskService, mission: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _pret(service, mission, "sans binaire git")

    def _boom(*_a: object, **_k: object) -> object:
        raise OSError("git introuvable")

    import grimoire.tools.workspace_launch_preflight as module

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=_boom, TimeoutExpired=subprocess.TimeoutExpired))
    wt = _plan(projet, task.id)["worktree"]
    assert wt["chemin"] is None
    assert "OSError" in wt["detail"]


# ── Route HTTP ────────────────────────────────────────────────────────────────


def test_la_route_launch_plan_est_un_get_pur_vers_le_prefligh(
    projet: Path, service: TaskService, mission: str
) -> None:
    from grimoire.tools.workspace_routes import workspace_get

    task = _pret(service, mission, "par la route")
    payload = workspace_get(projet, f"/api/workspace/tasks/{task.id}/launch-plan", {})
    assert payload["task_id"] == task.id
    assert payload["pret"] is True
    # Une tâche inconnue devient un 404 côté transport (FileNotFoundError), jamais un 500.
    with pytest.raises(FileNotFoundError):
        workspace_get(projet, "/api/workspace/tasks/GAO-fantome-001/launch-plan", {})
