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


# ── Branches d'erreur par section et pool hôte borné (couverture du diff P71) ─


def test_un_pool_host_illisible_rend_le_reservoir_inconnu_et_defere_unknown(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "pools:\n  host:\n    mauvaise_cle: 1\n")
    _pret(service, mission, "gâtée par la déclaration")
    view = capacity_view(projet, probe=lambda: READING)
    host = _reservoir(view, "host")
    assert host["etat"] == "inconnu"
    assert "pools.host" in host["detail"]
    (ligne,) = view["file"]
    assert (ligne["verdict"], ligne["raison"]) == ("defer", "unknown")


def test_un_pool_host_non_declare_et_sonde_muette_dit_les_deux(projet: Path) -> None:
    view = capacity_view(projet, probe=lambda: None)
    host = _reservoir(view, "host")
    assert host["etat"] == "non_declare"
    assert "pool non déclaré" in host["detail"] and "sonde muette" in host["detail"]


def test_le_pool_host_borne_rend_charge_et_memoire_sous_les_seuils(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "pools:\n  host:\n    max_load_per_cpu: 2\n    min_mem_available_mb: 1024\n")
    _pret(service, mission, "sous les seuils de l'hôte")
    view = capacity_view(projet, probe=lambda: READING)
    charge = _reservoir(view, "host.charge")
    assert (charge["utilise"], charge["capacite"], charge["etat"]) == (0.42, 2.0, "ok")
    assert "max 2" in charge["detail"]
    memoire = _reservoir(view, "host.memoire")
    assert (memoire["utilise"], memoire["etat"]) == (2048, "ok")
    assert "min 1024 Mo" in memoire["detail"]
    assert view["file"][0]["verdict"] == "admit"


def test_le_pool_host_sature_passe_plein_et_defere_hote_charge(
    projet: Path, service: TaskService, mission: str
) -> None:
    _capacity(projet, "pools:\n  host:\n    max_load_per_cpu: 2\n    min_mem_available_mb: 1024\n")
    _pret(service, mission, "au-dessus des seuils")
    view = capacity_view(projet, probe=lambda: HostReading(load_per_cpu=5.0, mem_available_mb=512))
    assert _reservoir(view, "host.charge")["etat"] == "plein"
    assert _reservoir(view, "host.memoire")["etat"] == "plein"
    (ligne,) = view["file"]
    assert (ligne["verdict"], ligne["raison"], ligne["raison_libelle"]) == ("defer", "host", "hôte chargé")


def test_une_seule_borne_host_laisse_l_autre_jauge_non_bornee(projet: Path) -> None:
    _capacity(projet, "pools:\n  host:\n    max_load_per_cpu: 2\n")
    memoire = _reservoir(capacity_view(projet, probe=lambda: READING), "host.memoire")
    assert memoire["capacite"] is None and "non bornée" in memoire["detail"]
    _capacity(projet, "pools:\n  host:\n    min_mem_available_mb: 1024\n")
    charge = _reservoir(capacity_view(projet, probe=lambda: READING), "host.charge")
    assert charge["capacite"] is None and "non bornée" in charge["detail"]


def test_un_pool_llm_illisible_rend_le_reservoir_inconnu(projet: Path) -> None:
    _capacity(projet, "pools:\n  llm:\n    claude:\n      sessions: 0\n")
    llm = _reservoir(capacity_view(projet, probe=lambda: READING), "llm")
    assert llm["etat"] == "inconnu"
    assert "pools.llm.claude.sessions" in llm["detail"]


def test_un_fournisseur_llm_sature_passe_plein(projet: Path, service: TaskService, mission: str) -> None:
    tenue = _tenue(service, mission, "tenue par claude")
    # La route enregistrée au ledger (K9) : c'est elle qui donne son fournisseur au créneau tenu.
    service.ledger.append_event(
        "task.routed", tenue.id, "task", "test",
        {"decision": {"outcome": "route", "provider": "claude", "model": "haiku"}},
    )
    _capacity(projet, "pools:\n  llm:\n    claude:\n      sessions: 1\n")
    llm = _reservoir(capacity_view(projet, probe=lambda: READING), "llm.claude")
    assert (llm["utilise"], llm["capacite"], llm["etat"]) == (1, 1, "plein")


def test_une_limite_wip_illisible_rend_le_wip_inconnu(projet: Path) -> None:
    _capacity(projet, "wip:\n  limit_global: 0\n")
    view = capacity_view(projet, probe=lambda: READING)
    assert view["wip"]["etat"] == "inconnu"
    assert view["wip"]["limite"] is None
    assert "limit_global" in view["wip"]["detail"]


def test_un_quota_illisible_rend_le_quota_inconnu(projet: Path, service: TaskService, mission: str) -> None:
    _capacity(projet, "quota:\n  tokens_per_day: 0\n")
    _pret(service, mission, "sous quota illisible")
    view = capacity_view(projet, probe=lambda: READING)
    assert view["quota"]["mode"] == "inconnu"
    assert "tokens_per_day" in view["quota"]["detail"]
    (ligne,) = view["file"]
    assert (ligne["verdict"], ligne["raison"]) == ("defer", "unknown")


def test_le_wip_par_role_est_expose_et_defere_la_tache_du_meme_role(
    projet: Path, service: TaskService, mission: str
) -> None:
    _tenue(service, mission, "tenue par amelia")
    _pret(service, mission, "aussi pour amelia")
    _capacity(projet, "wip:\n  limit_per_role: 1\n")  # après le claim : la garde de claim l'aurait sinon refusé
    view = capacity_view(projet, probe=lambda: READING)
    assert view["wip"]["par_role"] == 1
    assert view["wip"]["limite"] is None
    (ligne,) = view["file"]
    assert (ligne["verdict"], ligne["raison"]) == ("defer", "wip")
    assert "WIP du rôle amelia atteint (1/1)" in ligne["detail"]


def test_une_pause_se_voit_et_une_tache_differee_depuis_24h_entre_en_famine(
    projet: Path, service: TaskService, mission: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import timedelta

    from grimoire.missions.pause import write_pause

    # La pause de la machine du poste ne doit pas entrer dans ce test : on la détourne.
    monkeypatch.setenv("GRIMOIRE_CONTROL_HOME", str(tmp_path / "control-home"))
    task = _pret(service, mission, "gelée par la pause")
    write_pause(projet, mode="drain", actor="test", reason="gel de test")
    view = capacity_view(projet, now=datetime.now(UTC) + timedelta(hours=25), probe=lambda: READING)
    assert view["pause"] and "drain" in view["pause"][0]
    (ligne,) = view["file"]
    assert (ligne["verdict"], ligne["raison"], ligne["raison_libelle"]) == ("defer", "pause", "pause")
    # Différée depuis plus de 24 h (seuil famine par défaut) : la tâche est en famine.
    assert view["famine"] == [task.id]
