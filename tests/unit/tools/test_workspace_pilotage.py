"""Pilotage de l'orchestrateur depuis le cockpit : état lisible, pause, reprise, disjoncteur.

Les gestes sont joués contre un vrai serveur du cockpit (garde d'écriture, résolution du projet, clé de contrôle),
l'état contre les fonctions du module. Ce qu'ils doivent prouver : sans clé valide, rien n'est écrit ; une dictée,
un projet non nommé ou contredit sont refusés ; une pause d'autrui s'affiche et ne s'écrase qu'à la demande ;
aucun outil MCP ni commande de la Console n'expose ces gestes.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.missions import mandate as md
from grimoire.missions.breaker import read_breaker, record_failure, trip
from grimoire.missions.launcher import LAUNCH_FAILED_EVENT, LAUNCHED_EVENT
from grimoire.missions.mandate import AuthorityJournal
from grimoire.missions.pause import (
    DRAIN,
    HOLD,
    SCOPE_GLOBAL,
    global_pause_path,
    project_pause_path,
    read_flag,
    write_pause,
)
from grimoire.missions.service import TaskService
from grimoire.missions.timer import _unit_name, unit_dir
from grimoire.tools import project_registry, workspace_pilotage
from grimoire.tools.workspace_routes import GET_ROUTES, POST_ROUTES, PREFIX

from .. import _cockpit_http

PAUSE = f"{PREFIX}pilotage/pause"
RESUME = f"{PREFIX}pilotage/resume"
MACHINE_PAUSE = f"{PREFIX}pilotage/machine/pause"
MACHINE_RESUME = f"{PREFIX}pilotage/machine/resume"
RESET = f"{PREFIX}pilotage/breaker/reset"
TRIP = f"{PREFIX}pilotage/breaker/trip"
STATE = f"{PREFIX}pilotage"
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La clé, le journal d'autorité, la pause machine et le minuteur vivent sous HOME : un HOME par test."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    monkeypatch.setenv("GRIMOIRE_CONTROL_HOME", str(home / ".grimoire" / "control"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    workspace_pilotage.clear_cache()


def _key() -> str:
    return md.ensure_control_key().read_text(encoding="utf-8").strip()


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root.resolve()


@pytest.fixture
def duo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Path, Path]]:
    """Un cockpit qui sert deux projets enrôlés : ``alpha`` (ouverture) et ``beta``."""
    alpha, beta = _project(tmp_path, "alpha"), _project(tmp_path, "beta")
    project_registry.save_registry(
        [
            {"name": "Alpha", "path": str(alpha), "slug": "alpha"},
            {"name": "Beta", "path": str(beta), "slug": "beta"},
        ]
    )
    project_registry.set_selected_slug("alpha")
    monkeypatch.setattr(cmd_cockpit, "_OPENING_SLUG", "alpha")
    monkeypatch.setattr(cmd_cockpit, "_OPENING_PATH", alpha)
    cmd_cockpit._API_CACHE.clear()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], alpha, beta
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


def _post(port: int, route: str, slug: str, body: dict[str, Any] | None = None, *, key: str | None = None) -> tuple[int, Any]:
    payload = {"project": slug, "control_key": _key() if key is None else key, **(body or {})}
    return _cockpit_http.post(port, f"{route}?project={slug}", payload)


def _state(port: int, slug: str) -> dict[str, Any]:
    status, body = _cockpit_http.get(port, f"{STATE}?project={slug}")
    assert status == 200, body
    return body  # type: ignore[no-any-return]


def _journal_gestures() -> list[str]:
    return [e["gesture"] for e in AuthorityJournal().read() if e.get("type") == "pilotage"]


# ── Lecture ────────────────────────────────────────────────────────────────────


def test_un_projet_sans_rien_est_actif_et_le_dit(duo: tuple[int, Path, Path]) -> None:
    port, _alpha, _beta = duo

    state = _state(port, "alpha")

    assert state["state"] == "actif" and state["pause"]["active"] is False
    assert state["breaker"]["open"] is False and state["breaker"]["threshold"] == 3
    assert state["timer"]["installed"] is False and state["timer"]["last_tick"] is None
    assert state["roles"]["roles"]["worker"]["enabled"] is False


def test_la_lecture_est_une_route_en_lecture_seule_et_les_gestes_sont_des_ecritures() -> None:
    assert STATE in GET_ROUTES and STATE not in POST_ROUTES
    for route in (PAUSE, RESUME, MACHINE_PAUSE, MACHINE_RESUME, RESET, TRIP):
        assert route in POST_ROUTES and route not in GET_ROUTES


def test_le_minuteur_pose_donne_sa_periode_sans_sous_processus(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    unit_dir().mkdir(parents=True)
    (unit_dir() / f"{_unit_name(alpha)}.timer").write_text("[Timer]\nOnUnitActiveSec=15min\n", encoding="utf-8")

    timer = _state(port, "alpha")["timer"]

    assert timer["installed"] is True and timer["interval_minutes"] == 15
    assert "ne consigne pas" in timer["note"]


def test_un_minuteur_sans_periode_lisible_reste_pose(tmp_path: Path) -> None:
    root = _project(tmp_path, "alpha")
    unit_dir().mkdir(parents=True)
    (unit_dir() / f"{_unit_name(root)}.timer").write_text("rien de lisible\n", encoding="utf-8")

    timer = workspace_pilotage.pilotage_state(root)["timer"]

    assert timer["installed"] is True and timer["interval_minutes"] is None


def test_les_roles_disent_leur_plafond_et_la_consommation_du_jour(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    (alpha / POLICY).parent.mkdir(parents=True)
    (alpha / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      permission_mode: acceptEdits\n"
        "      wip: 2\n      sessions_per_day: 4\n",
        encoding="utf-8",
    )
    ledger = TaskService(alpha).ledger
    ledger.append_event(LAUNCHED_EVENT, "T-1", "task", "tick", {})
    ledger.append_event(LAUNCH_FAILED_EVENT, "T-2", "task", "tick", {})
    ledger.append_event("task.autre", "T-3", "task", "tick", {})

    worker = _state(port, "alpha")["roles"]["roles"]["worker"]

    assert worker == {"enabled": True, "wip": 2, "sessions_per_day": 4, "sessions_today": 2}


def test_une_politique_illisible_se_dit_au_lieu_de_deviner_les_roles(tmp_path: Path) -> None:
    root = _project(tmp_path, "alpha")
    (root / POLICY).parent.mkdir(parents=True)
    (root / POLICY).write_text("orchestrator: [pas une table]\n", encoding="utf-8")

    roles = workspace_pilotage.pilotage_state(root)["roles"]

    assert roles["error"] and roles["roles"] == {}


def test_un_ledger_illisible_donne_une_consommation_inconnue_et_non_un_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, "alpha")
    (root / POLICY).parent.mkdir(parents=True)
    (root / POLICY).write_text("orchestrator:\n  roles:\n    worker:\n      enabled: false\n", encoding="utf-8")

    def boom(*_a: Any, **_k: Any) -> bool:
        raise OSError("ledger")

    monkeypatch.setattr(TaskService, "has_ledger", property(boom))

    worker = workspace_pilotage.pilotage_state(root)["roles"]["roles"]["worker"]

    assert worker["sessions_today"] is None


def test_le_compte_du_jour_est_garde_dix_secondes_et_borne(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, "alpha")
    ledger = TaskService(root).ledger
    ledger.append_event(LAUNCHED_EVENT, "T-1", "task", "tick", {})
    assert workspace_pilotage._launched_today(root, workspace_pilotage.datetime.now(workspace_pilotage.UTC)) == 1
    ledger.append_event(LAUNCHED_EVENT, "T-2", "task", "tick", {})

    now = workspace_pilotage.datetime.now(workspace_pilotage.UTC)
    assert workspace_pilotage._launched_today(root, now) == 1, "gardé : pas de relecture du ledger à chaque pouls"
    monkeypatch.setattr(workspace_pilotage, "_COUNT_MAX_ENTRIES", 1)
    workspace_pilotage._launched_today(_project(tmp_path, "beta"), now)
    assert len(workspace_pilotage._COUNTS) == 1, "le cache ne grossit pas sans borne"
    workspace_pilotage.clear_cache()
    assert workspace_pilotage._launched_today(root, now) == 2


# ── Pause et reprise d'un projet ──────────────────────────────────────────────


def test_mettre_en_pause_puis_reprendre_un_projet(duo: tuple[int, Path, Path]) -> None:
    port, alpha, beta = duo

    status, body = _post(port, PAUSE, "alpha", {"mode": DRAIN, "reason": "je relis la PR"})

    assert status == 200, body
    flag = read_flag(project_pause_path(alpha), "project")
    assert flag is not None and (flag.mode, flag.actor, flag.origin, flag.reason) == (DRAIN, "cockpit", "human", "je relis la PR")
    assert read_flag(project_pause_path(beta), "project") is None, "la pause ne touche que le projet nommé"
    state = body["pilotage"]
    assert state["state"] == "pause" and "le cockpit" in state["sentence"] and "je relis la PR" in state["sentence"]
    assert body["journaled"] is True

    status, body = _post(port, RESUME, "alpha")

    assert status == 200, body
    assert read_flag(project_pause_path(alpha), "project") is None
    assert body["pilotage"]["state"] == "actif" and "le cockpit" in body["notice"]
    assert _journal_gestures() == ["pause", "resume"]


def test_arreter_net_pose_un_hold(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo

    status, body = _post(port, PAUSE, "alpha", {"mode": HOLD})

    assert status == 200, body
    assert read_flag(project_pause_path(alpha), "project").mode == HOLD  # type: ignore[union-attr]
    assert body["pilotage"]["state"] == "arret" and body["pilotage"]["sentence"].startswith("Arrêté net")


def test_un_mode_inconnu_est_refuse_et_rien_n_est_ecrit(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo

    status, body = _post(port, PAUSE, "alpha", {"mode": "tout"})

    assert status == 400 and "mode" in body["error"]
    assert not project_pause_path(alpha).exists()


@pytest.mark.parametrize("route", [PAUSE, RESUME, MACHINE_PAUSE, MACHINE_RESUME, RESET, TRIP])
@pytest.mark.parametrize("key", ["", "pas-la-cle"])
def test_sans_cle_de_controle_valide_rien_n_est_ecrit(duo: tuple[int, Path, Path], route: str, key: str) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=DRAIN, actor="guilhem")  # de quoi reprendre ; et un disjoncteur à refermer
    trip(alpha, "une raison")
    before = (project_pause_path(alpha).read_bytes(), read_breaker(alpha).to_dict())

    status, body = _post(port, route, "alpha", {"mode": HOLD, "confirm": True, "reason": "x", "replace": True}, key=key)

    assert status == 403 and "clé de contrôle" in body["error"]
    assert (project_pause_path(alpha).read_bytes(), read_breaker(alpha).to_dict()) == before
    assert not global_pause_path() or not global_pause_path().exists()  # type: ignore[union-attr]
    assert _journal_gestures() == []


@pytest.mark.parametrize("route", [PAUSE, RESUME, MACHINE_PAUSE, MACHINE_RESUME, RESET, TRIP])
def test_une_dictee_ne_pose_ni_ne_leve_rien(duo: tuple[int, Path, Path], route: str) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=DRAIN, actor="guilhem")
    trip(alpha, "une raison")
    before = (project_pause_path(alpha).read_bytes(), read_breaker(alpha).to_dict())

    status, body = _post(port, route, "alpha", {"mode": HOLD, "confirm": True, "reason": "x", "dictation": True})

    assert status == 400 and "dicte" in body["error"]
    assert (project_pause_path(alpha).read_bytes(), read_breaker(alpha).to_dict()) == before
    assert _journal_gestures() == []


@pytest.mark.parametrize("route", [PAUSE, RESUME, MACHINE_PAUSE, MACHINE_RESUME, RESET, TRIP])
def test_le_projet_cible_est_obligatoire_enrole_et_confirme_par_le_corps(duo: tuple[int, Path, Path], route: str) -> None:
    port, alpha, beta = duo
    body = {"mode": DRAIN, "confirm": True, "reason": "x", "control_key": _key()}

    assert _cockpit_http.post(port, route, body)[0] == 400, "sans ?project=, projet non nommé"
    assert _cockpit_http.post(port, f"{route}?project=inconnu", body)[0] == 404
    assert _cockpit_http.post(port, f"{route}?project=alpha", {**body, "project": "beta"})[0] == 400
    assert not project_pause_path(alpha).exists() and not project_pause_path(beta).exists()
    assert not global_pause_path().exists()  # type: ignore[union-attr]


def test_une_pause_en_place_n_est_pas_ecrasee_sans_geste_explicite(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=DRAIN, actor="guilhem", reason="je tiens la main")
    before = project_pause_path(alpha).read_bytes()

    status, body = _post(port, PAUSE, "alpha", {"mode": HOLD})

    assert status == 400 and "guilhem" in body["error"] and "je tiens la main" in body["error"]
    assert project_pause_path(alpha).read_bytes() == before

    status, body = _post(port, PAUSE, "alpha", {"mode": HOLD, "replace": True})

    assert status == 200, body
    assert read_flag(project_pause_path(alpha), "project").mode == HOLD  # type: ignore[union-attr]


def test_la_pause_d_une_regle_d_usage_est_affichee_et_la_reprise_la_leve_en_le_disant(
    duo: tuple[int, Path, Path],
) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=DRAIN, actor="usage-rule:day:gros", reason="consommation du jour au seuil")

    pause = _state(port, "alpha")["pause"]["project"]

    assert pause["author"] == "la règle d'usage day:gros" and pause["foreign"] is True and pause["usage_rule"] is True
    assert pause["reason"] == "consommation du jour au seuil" and pause["since"]
    status, body = _post(port, RESUME, "alpha")

    assert status == 200, body
    assert "règle d'usage day:gros" in body["notice"] and "reposer" in body["notice"]
    assert not project_pause_path(alpha).exists()
    assert body["lifted"]["actor"] == "usage-rule:day:gros"


def test_les_autres_auteurs_se_nomment(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=DRAIN, actor="session-7", origin="agent")
    assert _state(port, "alpha")["pause"]["project"]["author"] == "une session d'agent (session-7)"
    write_pause(alpha, mode=DRAIN, actor="", origin="agent")
    assert _state(port, "alpha")["pause"]["project"]["author"] == "une session d'agent"
    write_pause(alpha, mode=DRAIN, actor="guilhem", origin="human")
    assert _state(port, "alpha")["pause"]["project"]["author"] == "guilhem (ligne de commande)"
    project_pause_path(alpha).write_text("{pas du json", encoding="utf-8")
    state = _state(port, "alpha")
    assert "illisible" in state["pause"]["project"]["author"] and state["state"] == "pause"


def test_reprendre_sans_pause_ne_leve_rien(duo: tuple[int, Path, Path]) -> None:
    port, _alpha, _beta = duo

    status, body = _post(port, RESUME, "alpha")

    assert status == 200 and body["lifted"] is None and body["notice"] == "Aucune pause à lever."


# ── Pause de la machine ───────────────────────────────────────────────────────


def test_pauser_toute_la_machine_exige_la_confirmation_puis_s_affiche_partout(duo: tuple[int, Path, Path]) -> None:
    port, alpha, beta = duo

    status, body = _post(port, MACHINE_PAUSE, "alpha", {"mode": DRAIN})
    assert status == 400 and "confirm" in body["error"]
    assert not global_pause_path().exists()  # type: ignore[union-attr]

    status, body = _post(port, MACHINE_PAUSE, "alpha", {"mode": DRAIN, "confirm": True, "reason": "coupure réseau"})

    assert status == 200, body
    flag = read_flag(global_pause_path(), SCOPE_GLOBAL)  # type: ignore[arg-type]
    assert flag is not None and flag.actor == "cockpit" and flag.reason == "coupure réseau"
    for slug, root in (("alpha", alpha), ("beta", beta)):
        state = _state(port, slug)
        assert state["pause"]["machine"]["author"] == "le cockpit" and state["state"] == "pause"
        assert "toute la machine" in state["sentence"] and not project_pause_path(root).exists()
    status, rows = _cockpit_http.get(port, f"{STATE}?project=alpha&scope=portfolio")
    assert status == 200
    assert rows["machine"]["mode"] == DRAIN and {r["slug"] for r in rows["projects"]} == {"alpha", "beta"}
    assert all("roles" not in r for r in rows["projects"]), "la Flotte ne lit pas le ledger de chaque projet"

    status, body = _post(port, MACHINE_RESUME, "beta")

    assert status == 200 and not global_pause_path().exists()  # type: ignore[union-attr]
    assert _journal_gestures() == ["pause", "resume"]


def test_la_pause_de_la_machine_d_autrui_n_est_pas_ecrasee(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=DRAIN, scope=SCOPE_GLOBAL, actor="guilhem", reason="maintenance")

    status, body = _post(port, MACHINE_PAUSE, "alpha", {"mode": HOLD, "confirm": True})

    assert status == 400 and "maintenance" in body["error"]
    assert read_flag(global_pause_path(), SCOPE_GLOBAL).mode == DRAIN  # type: ignore[union-attr,arg-type]


def test_la_flotte_saute_un_projet_dont_le_dossier_a_disparu(duo: tuple[int, Path, Path], tmp_path: Path) -> None:
    port, _alpha, _beta = duo
    project_registry.save_registry(
        [
            {"name": "Alpha", "path": str(tmp_path / "alpha"), "slug": "alpha"},
            {"name": "Parti", "path": str(tmp_path / "parti"), "slug": "parti"},
        ]
    )

    status, rows = _cockpit_http.get(port, f"{STATE}?project=alpha&scope=portfolio")

    assert status == 200 and [r["slug"] for r in rows["projects"]] == ["alpha"]


# ── Disjoncteur ───────────────────────────────────────────────────────────────


def test_le_disjoncteur_s_ouvre_a_la_main_s_affiche_avec_son_motif_et_se_referme(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo

    assert _post(port, TRIP, "alpha", {})[0] == 400, "le motif d'ouverture est obligatoire"
    status, body = _post(port, TRIP, "alpha", {"reason": "je veux voir"})
    assert status == 200, body
    assert read_breaker(alpha).open is True and read_breaker(alpha).source == "manual"
    state = body["pilotage"]
    assert state["state"] == "disjoncteur" and state["breaker"]["reason"] == "je veux voir"
    assert "Disjoncteur ouvert : je veux voir" in state["sentence"]
    assert _post(port, TRIP, "alpha", {"reason": "encore"})[0] == 400, "déjà ouvert"

    status, body = _post(port, RESET, "alpha")

    assert status == 200 and body["was"] == "je veux voir", body
    assert read_breaker(alpha).open is False and body["pilotage"]["state"] == "actif"
    assert _post(port, RESET, "alpha")[0] == 400, "déjà fermé"
    assert _journal_gestures() == ["breaker_trip", "breaker_reset"]


def test_un_disjoncteur_ouvert_par_comptage_donne_ses_echecs_et_son_seuil(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    for n in range(3):
        record_failure(alpha, 3, f"échec {n}")

    breaker = _state(port, "alpha")["breaker"]

    assert breaker["open"] is True and breaker["failures"] == 3 and breaker["threshold"] == 3
    assert breaker["source"] == "count" and "échec 2" in breaker["reason"] and breaker["history"]


def test_pause_et_disjoncteur_se_disent_ensemble(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=HOLD, actor="guilhem")
    trip(alpha, "raison")

    state = _state(port, "alpha")

    assert state["state"] == "arret" and "Disjoncteur ouvert : raison" in state["sentence"] and " · " in state["sentence"]


def test_un_disjoncteur_ouvert_sans_pause_donne_l_etat_disjoncteur(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    trip(alpha, "raison")

    assert _state(port, "alpha")["state"] == "disjoncteur"


# ── Journal d'autorité ────────────────────────────────────────────────────────


def test_un_journal_illisible_n_empeche_pas_d_arreter_mais_empeche_de_rendre_l_autonomie(
    duo: tuple[int, Path, Path],
) -> None:
    port, alpha, _beta = duo
    journal = AuthorityJournal().path
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("pas une ligne de journal\n", encoding="utf-8")

    status, body = _post(port, PAUSE, "alpha", {"mode": HOLD})
    assert status == 200 and body["journaled"] is False, "arrêter reste toujours possible"
    status, body = _post(port, TRIP, "alpha", {"reason": "x"})
    assert status == 200 and body["journaled"] is False

    status, body = _post(port, RESUME, "alpha")
    assert status == 400 and "journal d'autorité illisible" in body["error"]
    assert project_pause_path(alpha).exists(), "la reprise refusée n'a rien levé"
    status, body = _post(port, RESET, "alpha")
    assert status == 400 and read_breaker(alpha).open is True


def test_l_evenement_gouverne_nomme_le_geste_et_l_humain(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    _post(port, PAUSE, "alpha", {"mode": DRAIN, "reason": "motif"})

    [entry] = [e for e in AuthorityJournal().read() if e.get("type") == "pilotage"]

    assert (entry["gesture"], entry["by"], entry["via"], entry["scope"], entry["mode"], entry["reason"]) == (
        "pause", "human", "cockpit", "project", DRAIN, "motif",
    )
    assert entry["project"] == str(alpha)


def _break_journal() -> None:
    journal = AuthorityJournal().path
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("pas une ligne de journal\n", encoding="utf-8")


def test_sans_journal_lisible_aucun_remplacement_n_affaiblit_un_arret(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=HOLD, actor="guilhem", reason="arret humain", origin="human")
    write_pause(alpha, mode=HOLD, scope=SCOPE_GLOBAL, actor="guilhem", origin="human")
    _break_journal()
    before = (project_pause_path(alpha).read_bytes(), global_pause_path().read_bytes())  # type: ignore[union-attr]

    for mode in (DRAIN, HOLD):  # affaiblir, et remplacer à l'identique : aucune trace possible, donc refusé
        status, body = _post(port, PAUSE, "alpha", {"mode": mode, "replace": True})
        assert status == 400 and "journal d'autorité illisible" in body["error"], body
        status, body = _post(port, MACHINE_PAUSE, "beta", {"mode": mode, "confirm": True, "replace": True})
        assert status == 400 and "journal d'autorité illisible" in body["error"], body

    assert (project_pause_path(alpha).read_bytes(), global_pause_path().read_bytes()) == before  # type: ignore[union-attr]


def test_sans_journal_lisible_poser_ou_durcir_ou_ouvrir_passent(duo: tuple[int, Path, Path]) -> None:
    port, alpha, beta = duo
    _break_journal()

    status, body = _post(port, PAUSE, "alpha", {"mode": DRAIN})
    assert status == 200 and body["journaled"] is False
    status, body = _post(port, PAUSE, "alpha", {"mode": HOLD, "replace": True})
    assert status == 200, "durcir (drain vers hold) arrête davantage"
    assert read_flag(project_pause_path(alpha), "project").mode == HOLD  # type: ignore[union-attr]
    assert _post(port, TRIP, "beta", {"reason": "r"})[0] == 200
    assert read_breaker(beta).open is True


def test_le_remplacement_trace_l_auteur_d_avant_et_n_herite_d_aucune_session(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=HOLD, actor="session-x", reason="motif de l'agent", origin="agent", session="S-secrete")

    status, body = _post(port, PAUSE, "alpha", {"mode": DRAIN, "replace": True, "reason": "je reprends la main"})

    assert status == 200, body
    flag = read_flag(project_pause_path(alpha), "project")
    assert flag is not None and (flag.origin, flag.actor, flag.session) == ("human", "cockpit", "")
    [event] = [e for e in AuthorityJournal().read() if e.get("type") == "pilotage"]
    replaced = event["replaced"]
    assert (replaced["origin"], replaced["mode"], replaced["reason"]) == ("agent", HOLD, "motif de l'agent")
    assert "session-x" in replaced["author"] and replaced["had_session"] is True
    assert "S-secrete" not in json.dumps(event)

    status, body = _post(port, RESUME, "alpha")
    lifted = [e for e in AuthorityJournal().read() if e.get("type") == "pilotage"][-1]["lifted"]
    assert status == 200 and (lifted["origin"], lifted["mode"], lifted["reason"]) == ("human", DRAIN, "je reprends la main")


def test_les_refus_de_sortie_de_blocage_citent_la_bonne_commande_de_la_ligne_de_commande(
    duo: tuple[int, Path, Path],
) -> None:
    port, alpha, _beta = duo
    write_pause(alpha, mode=DRAIN, actor="guilhem")
    write_pause(alpha, mode=DRAIN, scope=SCOPE_GLOBAL, actor="guilhem")
    trip(alpha, "r")
    _break_journal()

    assert "`grimoire orchestrate resume`" in _post(port, RESUME, "alpha")[1]["error"]
    assert "`grimoire orchestrate resume --global`" in _post(port, MACHINE_RESUME, "alpha")[1]["error"]
    assert "`grimoire orchestrate breaker reset`" in _post(port, RESET, "alpha")[1]["error"]


def test_le_motif_ecrit_par_un_agent_est_nettoye_borne_et_sa_session_ne_sort_pas(duo: tuple[int, Path, Path]) -> None:
    port, alpha, _beta = duo
    hostile = "<img src=x onerror=alert(1)>\n\x1b[31m rouge ‮​" + "A" * 5000
    write_pause(alpha, mode=DRAIN, actor="agent\nzz", reason=hostile, origin="agent", session="SESSION-SECRETE")
    trip(alpha, hostile)

    state = _state(port, "alpha")
    flag = state["pause"]["project"]

    assert "\n" not in flag["reason"] and "\x1b" not in flag["reason"] and "‮" not in flag["reason"]
    assert len(flag["reason"]) <= workspace_pilotage._REASON_MAX and flag["reason"].endswith("…")
    assert flag["actor"] == "agent zz" and "session" not in flag and flag["has_session"] is True
    assert len(state["breaker"]["reason"]) <= workspace_pilotage._REASON_MAX
    assert "SESSION-SECRETE" not in json.dumps(state) and "\n" not in state["sentence"]
    status, body = _post(port, PAUSE, "alpha", {"mode": HOLD})
    assert status == 400 and "\x1b" not in body["error"] and len(body["error"]) < 600


def test_un_geste_de_machine_se_journalise_en_portee_machine_avec_son_projet_d_ancrage(
    duo: tuple[int, Path, Path],
) -> None:
    port, _alpha, beta = duo

    _post(port, MACHINE_PAUSE, "beta", {"mode": DRAIN, "confirm": True})
    _post(port, MACHINE_RESUME, "beta")

    events = [e for e in AuthorityJournal().read() if e.get("type") == "pilotage"]
    assert [(e["scope"], e["project"], e["via_project"]) for e in events] == [("machine", "", str(beta))] * 2


# ── Ni agent ni console ───────────────────────────────────────────────────────

_FORBIDDEN_WORDS = re.compile(r"pause|resume|breaker|pilotage|orchestrate|drain|hold", re.IGNORECASE)
_FORBIDDEN_CALLS = re.compile(r"write_pause|clear_pause|missions\.breaker|missions\.pause|workspace_pilotage|cmd_orchestrate|orchestrate_app|grimoire orchestrate\b")


def test_aucun_outil_mcp_ne_pose_ni_ne_leve_une_pause_ni_ne_touche_au_disjoncteur() -> None:
    from grimoire.mcp import server

    names = [tool.name for tool in asyncio.run(server.mcp.list_tools())]

    assert names, "le serveur MCP expose des outils"
    assert [n for n in names if _FORBIDDEN_WORDS.search(n)] == []
    mcp_dir = Path(server.__file__).parent
    offenders = [p.name for p in mcp_dir.glob("*.py") if _FORBIDDEN_CALLS.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], f"ces modules MCP touchent la pause ou le disjoncteur : {offenders}"


def test_aucune_commande_de_la_console_du_cockpit_ne_pose_ni_ne_leve_une_pause() -> None:
    from grimoire.tools import workspace_exec

    assert [k for k in workspace_exec.ALLOWED if _FORBIDDEN_WORDS.search(k)] == []
    assert [c["command"] for c in workspace_exec.catalogue() if _FORBIDDEN_WORDS.search(c["command"])] == []


def test_une_pause_ou_un_disjoncteur_pose_hors_du_cockpit_change_l_empreinte_du_pouls(tmp_path: Path) -> None:
    from grimoire.tools.workspace_pulse import fingerprint_of

    root = _project(tmp_path, "alpha")
    seen = [fingerprint_of(root)]
    write_pause(root, mode=DRAIN, actor="cli")
    seen.append(fingerprint_of(root))
    trip(root, "raison")
    seen.append(fingerprint_of(root))
    write_pause(root, mode=DRAIN, scope=SCOPE_GLOBAL, actor="cli")
    seen.append(fingerprint_of(root))

    assert len(set(seen)) == 4, "chaque changement doit atteindre le bloc Pilotage au pouls suivant"


def test_le_journal_de_la_garde_note_le_projet_de_chaque_geste(duo: tuple[int, Path, Path]) -> None:
    from grimoire.tools.workspace_write_guard import cockpit_journal_path

    port, _alpha, _beta = duo
    _post(port, PAUSE, "beta", {"mode": DRAIN})

    lines = [json.loads(line) for line in cockpit_journal_path().read_text(encoding="utf-8").splitlines()]
    assert any(e.get("route") == PAUSE and e.get("project") == "beta" and e["verdict"] == "acceptée" for e in lines)
