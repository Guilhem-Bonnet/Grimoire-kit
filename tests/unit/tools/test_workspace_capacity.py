"""P71 : la vue « Capacité » du cockpit — WIP, réservoirs, quota, file d'admission.

``capacity_view`` ne rend que ce que :mod:`grimoire.missions.admission` dérive du
ledger réel : aucun compteur à côté, aucune écriture. Ce qui n'est pas mesurable
(sonde muette, section de ``capacity.yaml`` illisible, consommation inconnue)
sort ``inconnu`` — jamais une valeur devinée.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.missions.admission import QUOTA_CONSUMED_EVENT, HostReading
from grimoire.missions.schemas import MissionTask, TaskState
from grimoire.missions.service import TaskService
from grimoire.tools.workspace_capacity import capacity_view

ACC = "la capacité se lit"
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
    return service.ledger.create_mission(title="Capacité", origin="test").id


def _capacity(projet: Path, text: str) -> None:
    (projet / STANDARD / "capacity.yaml").write_text(text, encoding="utf-8")


def _pret(service: TaskService, mission: str, titre: str, **kw: object) -> MissionTask:
    kw.setdefault("owner", "amelia")
    task = service.ledger.create_task(mission, titre, acceptance=(ACC,), **kw)  # type: ignore[arg-type]
    service.ledger.transition_task(task.id, TaskState.READY)
    return task


def _tenue(service: TaskService, mission: str, titre: str) -> MissionTask:
    task = _pret(service, mission, titre)
    assert service.claim(task.id, "worker").task.status is TaskState.CLAIMED
    return task


def _reservoir(view: dict, rid: str) -> dict:
    found = [r for r in view["reservoirs"] if r["id"] == rid]
    assert found, f"réservoir {rid} absent de {[r['id'] for r in view['reservoirs']]}"
    return found[0]


# ── Sans ledger ────────────────────────────────────────────────────────────────


def test_sans_ledger_la_vue_reste_lisible_et_la_file_vide(projet: Path) -> None:
    view = capacity_view(projet, probe=lambda: READING)
    assert view["ledger"] is False
    assert view["file"] == []
    assert view["wip"]["actifs"] == 0
    assert _reservoir(view, "ci.runners")["capacite"] == 3  # D7 : défaut sans fichier


# ── Réservoirs ────────────────────────────────────────────────────────────────


def test_un_pool_host_non_declare_n_est_pas_borne_mais_la_lecture_reelle_s_affiche(
    projet: Path, service: TaskService, mission: str
) -> None:
    _pret(service, mission, "une tâche")
    view = capacity_view(projet, probe=lambda: READING)
    host = _reservoir(view, "host")
    assert host["etat"] == "non_declare"
    assert host["capacite"] is None
    assert "0.42/CPU" in host["detail"] and "2048 Mo" in host["detail"]
    assert "aucune limite déclarée" in host["detail"]


def test_une_sonde_muette_sur_un_pool_declare_vaut_inconnu_jamais_un_chiffre(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "pools:\n  host:\n    max_load_per_cpu: 2\n")
    _pret(service, mission, "gâtée par la sonde")
    view = capacity_view(projet, probe=lambda: None)
    host = _reservoir(view, "host")
    assert host["etat"] == "inconnu"
    assert host["utilise"] is None and host["capacite"] is None
    assert "sonde muette" in host["detail"]
    # L'admission échoue fermée : la tâche est différée, raison « inconnue », rien d'inventé.
    (ligne,) = view["file"]
    assert ligne["verdict"] == "defer"
    assert ligne["raison"] == "unknown"
    assert ligne["raison_libelle"] == "inconnue"


def test_une_section_illisible_rend_son_reservoir_inconnu_avec_le_motif(projet: Path) -> None:
    _capacity(projet, "pools:\n  ci:\n    runners: -3\n")
    view = capacity_view(projet, probe=lambda: READING)
    ci = _reservoir(view, "ci.runners")
    assert ci["etat"] == "inconnu"
    assert "pools.ci.runners" in ci["detail"]


def test_les_sessions_llm_declarees_comptent_les_claims_du_fournisseur(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "pools:\n  llm:\n    claude:\n      sessions: 2\n")
    view = capacity_view(projet, probe=lambda: READING)
    llm = _reservoir(view, "llm.claude")
    assert (llm["utilise"], llm["capacite"], llm["etat"]) == (0, 2, "ok")


# ── WIP et file ───────────────────────────────────────────────────────────────


def test_le_wip_compte_les_taches_tenues_et_la_file_porte_la_raison_du_defer(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "wip:\n  limit_global: 1\n")
    _tenue(service, mission, "déjà tenue")
    premiere = _pret(service, mission, "première prête", priority="high")
    seconde = _pret(service, mission, "seconde prête", priority="low")
    view = capacity_view(projet, probe=lambda: READING)
    assert view["wip"] == {
        "actifs": 1, "limite": 1, "par_role": None, "etat": "plein", "detail": "1/1 créneau(x) tenus",
    }
    assert [ligne["task_id"] for ligne in view["file"]] == [premiere.id, seconde.id]
    assert [ligne["rang"] for ligne in view["file"]] == [1, 2]
    for ligne in view["file"]:
        assert ligne["verdict"] == "defer"
        assert ligne["raison"] == "wip"
        assert ligne["raison_libelle"] == "WIP atteint"
        assert "WIP global atteint (1/1)" in ligne["detail"]
    assert view["file"][0]["titre"] == "première prête"
    assert view["file"][0]["priorite"] == "high"


def test_une_tache_admise_porte_le_verdict_admit_sans_raison(
    projet: Path, service: TaskService, mission: str
) -> None:
    _pret(service, mission, "libre d'entrer")
    view = capacity_view(projet, probe=lambda: READING)
    (ligne,) = view["file"]
    assert ligne["verdict"] == "admit"
    assert ligne["raison"] is None and ligne["raison_libelle"] is None
    assert ligne["retry_at"] is None


# ── Quota ─────────────────────────────────────────────────────────────────────


def test_le_quota_illimite_est_le_defaut_et_ne_defere_jamais(
    projet: Path, service: TaskService, mission: str
) -> None:
    _pret(service, mission, "sous quota illimité")
    view = capacity_view(projet, probe=lambda: READING)
    assert view["quota"]["mode"] == "illimite"
    assert view["quota"]["plafond"] is None
    assert view["file"][0]["verdict"] == "admit"


def test_le_quota_fini_montre_la_consommation_du_jour(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 1000\n")
    _pret(service, mission, "sous quota fini")
    service.ledger.append_event(QUOTA_CONSUMED_EVENT, "x", "task", "test", {"tokens": 200})
    view = capacity_view(projet, probe=lambda: READING)
    assert view["quota"]["mode"] == "fini"
    assert (view["quota"]["consomme"], view["quota"]["plafond"]) == (200, 1000)
    assert view["quota"]["statut"] == "lower_bound"
    assert view["file"][0]["verdict"] == "admit"


def test_le_quota_atteint_defere_avec_sa_raison_et_un_retry_a_minuit(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 100\n")
    _pret(service, mission, "au-dessus du quota")
    service.ledger.append_event(QUOTA_CONSUMED_EVENT, "x", "task", "test", {"tokens": 150})
    view = capacity_view(projet, probe=lambda: READING)
    (ligne,) = view["file"]
    assert ligne["verdict"] == "defer"
    assert ligne["raison"] == "quota"
    assert ligne["raison_libelle"] == "quota du jour atteint"
    assert ligne["retry_at"] is not None
    assert datetime.fromisoformat(ligne["retry_at"]) > datetime.now(UTC)


def test_une_consommation_inconnue_sous_quota_fini_s_affiche_inconnue(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 1000\n")
    _pret(service, mission, "consommation invisible")
    # Un dispatch du jour sans aucun chiffre de tokens : on ne sait pas.
    service.ledger.append_event("task.dispatched", "x", "task", "test", {})
    view = capacity_view(projet, probe=lambda: READING)
    assert view["quota"]["consomme"] is None
    assert view["quota"]["statut"] == "unknown"
    assert "inconnue" in view["quota"]["detail"]
