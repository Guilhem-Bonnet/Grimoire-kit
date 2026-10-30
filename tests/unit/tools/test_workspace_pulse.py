"""UI-3 du cockpit : le pouls (actualisation sans rechargement), le compteur de
décisions, la vivacité, la durée et le coût de chaque carte.

Constat sur la base : aucune route ne dit « quelque chose a changé » (le navigateur
n'avait que le rechargement), aucune ne compte les décisions en attente hors de la
vue qui les liste, et une carte de tâche ne portait ni vivacité, ni durée, ni coût.
Chaque test dit ce qu'il empêche.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions.decisions import pending_decisions
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_api as wa
from grimoire.tools import workspace_portfolio as wp
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, workspace_get

PULSE = f"{PREFIX}pulse"
LEDGER = Path("_grimoire-runtime-output/ledger")
RUNS = Path("_grimoire-output/.runs")


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    monkeypatch.delenv("GRIMOIRE_PULSE_SECONDS", raising=False)
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)


def _running(service: TaskService, title: str) -> str:
    tid = service.add(title, ("critère",), actor="t", owner="guilhem", ready=True).task.id
    service.claim(tid, "agent-a", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "agent-a")
    return tid


def _ask(service: TaskService, tid: str, text: str) -> str:
    service.ask(tid, text, "agent-a", kind="merge", options=("fusionner", "attendre"))
    task = service.require(tid)
    assert task.waiting is not None and task.waiting.question is not None
    return task.waiting.question.id


def _with_two_decisions(root: Path) -> tuple[TaskService, list[tuple[str, str]]]:
    service = TaskService(root)
    pairs = []
    for index in range(2):
        tid = _running(service, f"Décision numéro {index} à trancher")
        pairs.append((tid, _ask(service, tid, f"Fusionner la PR #{index} ?")))
    return service, pairs


def _journal(root: Path, session_id: str, *, task_id: str, started: datetime, updated: datetime) -> Path:
    path = root / RUNS / f"session-{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": 1, "session_id": session_id, "started_at": started.isoformat(),
            "updated_at": updated.isoformat(), "task_id": task_id, "rules": {},
        }),
        encoding="utf-8",
    )
    return path


def _pulse(root: Path, **query: str) -> dict[str, Any]:
    payload = workspace_get(root, PULSE, {k: [v] for k, v in query.items()})
    assert isinstance(payload, dict), "la route du pouls n'existe pas sur la base"
    return payload


# ── Le pouls ────────────────────────────────────────────────────────────────


def test_la_route_du_pouls_est_declaree_et_dit_son_intervalle(tmp_path: Path) -> None:
    """Sans route, le navigateur ne peut que recharger la page : rien ne se met à jour seul."""
    assert PULSE in GET_ROUTES
    payload = _pulse(tmp_path)

    assert payload["changed"] is True
    assert isinstance(payload["fingerprint"], str) and payload["fingerprint"]
    assert payload["interval_seconds"] == 5, "l'intervalle déclaré par défaut"
    assert payload["decisions"]["pending"] == 0, "zéro décision est un 0 mesuré, pas une clé absente"


@pytest.mark.parametrize(("raw", "expected"), [("2", 2), ("0", 1), ("9999", 300), ("abc", 5), ("", 5)])
def test_l_intervalle_se_declare_et_se_borne(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
    """Un intervalle à 0 s ferait de chaque onglet un marteau ; une valeur illisible ne se devine pas."""
    monkeypatch.setenv("GRIMOIRE_PULSE_SECONDS", raw)

    assert _pulse(tmp_path)["interval_seconds"] == expected


def test_le_compteur_de_decisions_est_exact(tmp_path: Path) -> None:
    """Le compteur est la même lecture que `grimoire status` (`pending_decisions`), pas une seconde définition."""
    service, pairs = _with_two_decisions(tmp_path)

    payload = _pulse(tmp_path)

    assert payload["decisions"]["pending"] == len(pending_decisions(service.ledger)) == 2
    assert payload["decisions"]["oldest_at"], "la plus ancienne est datée"
    service.answer(pairs[0][0], "fusionner", "guilhem", origin=ORIGIN_HUMAN, question_id=pairs[0][1])
    assert _pulse(tmp_path)["decisions"]["pending"] == 1, "une décision tranchée sort du compte"


def test_sans_changement_le_pouls_ne_relit_rien_et_le_dit(tmp_path: Path) -> None:
    """« Sans charge si rien ne change » : la même empreinte rend `changed: false` sans le détail."""
    _with_two_decisions(tmp_path)
    first = _pulse(tmp_path)

    again = _pulse(tmp_path, since=first["fingerprint"])

    assert again["changed"] is False
    assert again["fingerprint"] == first["fingerprint"]
    assert "decisions" not in again, "le détail n'est relu que quand quelque chose a bougé"


def test_un_changement_de_ledger_change_l_empreinte(tmp_path: Path) -> None:
    """Défaut empêché : un pouls figé qui annonce « rien de nouveau » après une écriture du ledger."""
    service, pairs = _with_two_decisions(tmp_path)
    first = _pulse(tmp_path)

    service.answer(pairs[1][0], "attendre", "guilhem", origin=ORIGIN_HUMAN, question_id=pairs[1][1])
    after = _pulse(tmp_path, since=first["fingerprint"])

    assert after["changed"] is True
    assert after["fingerprint"] != first["fingerprint"]
    assert after["decisions"]["pending"] == 1


def test_un_battement_de_session_change_l_empreinte(tmp_path: Path) -> None:
    """Le dernier signe de vie vient du journal de session, pas du ledger : il doit faire bouger le pouls."""
    now = datetime.now(UTC)
    path = _journal(tmp_path, "S1", task_id="T", started=now - timedelta(minutes=10), updated=now - timedelta(minutes=5))
    first = wp_fingerprint(tmp_path, now)
    before = path.stat()

    path.write_text(path.read_text(encoding="utf-8").replace(
        (now - timedelta(minutes=5)).isoformat(), (now - timedelta(seconds=3)).isoformat()), encoding="utf-8")
    # Même taille et même mtime qu'avant, comme sous l'horloge grossière de Windows : seul le contenu change.
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size

    assert wp_fingerprint(tmp_path, now) != first


def test_une_session_qui_sort_de_la_fenetre_de_vie_change_l_empreinte_sans_ecriture(tmp_path: Path) -> None:
    """La vivacité se calcule avec l'horloge : `vivant` devient `inactif` sans qu'aucun fichier ne bouge.

    Sans ce terme, l'interface afficherait « vivante » indéfiniment sur une session muette.
    """
    now = datetime.now(UTC)
    _journal(tmp_path, "S1", task_id="T", started=now - timedelta(minutes=10), updated=now)
    # mtime réel = maintenant : la session est dans la fenêtre à `now`, hors fenêtre deux heures plus tard.
    inside = wp_fingerprint(tmp_path, now)
    later = wp_fingerprint(tmp_path, now + timedelta(hours=2))

    assert inside != later


def wp_fingerprint(root: Path, now: datetime) -> str:
    from grimoire.tools import workspace_pulse

    return str(workspace_pulse.pulse_view(root, now=now)["fingerprint"])


def test_la_portee_flotte_compte_tout_le_registre_et_nomme_l_illisible(tmp_path: Path) -> None:
    """Le compteur du cockpit est global ; un projet qu'on ne peut pas lire est dit, jamais compté pour zéro."""
    a, b = tmp_path / "a", tmp_path / "b"
    _with_two_decisions(a)
    service_b = TaskService(b)
    _ask(service_b, _running(service_b, "Une décision côté B"), "Publier ?")
    reg.save_registry([
        {"slug": "a", "name": "Alpha", "path": str(a)},
        {"slug": "b", "name": "Beta", "path": str(b)},
        {"slug": "c", "name": "Gamma", "path": str(tmp_path / "absent")},
    ])

    payload = _pulse(tmp_path, scope="portfolio")

    assert payload["decisions"]["pending"] == 3
    assert {p["slug"]: p["pending"] for p in payload["decisions"]["projects"]} == {"a": 2, "b": 1}
    assert payload["decisions"]["unreadable"] == ["c"]
    assert _pulse(tmp_path)["decisions"]["pending"] == 0, "sans `scope`, le projet servi seul"


def test_le_pouls_du_portefeuille_porte_une_empreinte_par_projet(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / LEDGER).mkdir(parents=True)
    reg.save_registry([
        {"slug": "a", "name": "Alpha", "path": str(a)},
        {"slug": "b", "name": "Beta", "path": str(b)},
    ])

    before = _pulse(tmp_path, scope="portfolio")
    assert set(before["projects"]) == {"a", "b"}
    assert before["fingerprint"], "le champ historique reste"

    (b / LEDGER / "events.jsonl").write_text('{"x": 1}\n')
    after = _pulse(tmp_path, scope="portfolio")

    assert after["projects"]["a"] == before["projects"]["a"]
    assert after["projects"]["b"] != before["projects"]["b"]
    assert after["fingerprint"] != before["fingerprint"]


# ── Coût, durée, vivacité par carte ─────────────────────────────────────────


def _dispatch(root: Path, task_id: str, cost_usd: float | None) -> None:
    MissionLedger(root / LEDGER).append_event(
        "task.dispatched", task_id, "task", "agent-a",
        {"task_id": task_id, "cost_usd": cost_usd, "verdict": "green"},
    )


def _card(root: Path, task_id: str) -> dict[str, Any]:
    return next(t for t in wa.tasks_view(root)["tasks"] if t["id"] == task_id)


def test_une_tache_jamais_dispatchee_n_a_pas_un_cout_de_zero(tmp_path: Path) -> None:
    """Le défaut que la fiche nomme : afficher 0 $ quand le kit n'a rien mesuré."""
    service = TaskService(tmp_path)
    tid = _running(service, "Tâche sans aucun dispatch")

    cost = _card(tmp_path, tid)["cost"]

    assert cost["status"] == "unknown"
    assert cost["usd"] is None
    assert cost["calls"] == 0


def test_le_cout_d_une_carte_est_exact_borne_basse_ou_inconnu(tmp_path: Path) -> None:
    """Trois états (W1-01) jusque dans la carte : un appel non chiffré rend le total minorant, jamais exact."""
    service = TaskService(tmp_path)
    exact = _running(service, "Carte dont tout est chiffré")
    partial = _running(service, "Carte dont un appel manque")
    silent = _running(service, "Carte dont aucun appel n'est chiffré")
    _dispatch(tmp_path, exact, 0.25)
    _dispatch(tmp_path, exact, 0.25)
    _dispatch(tmp_path, partial, 0.4)
    _dispatch(tmp_path, partial, None)
    _dispatch(tmp_path, silent, None)
    _dispatch(tmp_path, silent, None)

    assert _card(tmp_path, exact)["cost"] == {"usd": 0.5, "status": "exact", "unpriced_calls": 0, "calls": 2, "scope": "dispatch"}
    low = _card(tmp_path, partial)["cost"]
    assert (low["status"], low["usd"], low["unpriced_calls"]) == ("lower_bound", 0.4, 1)
    none = _card(tmp_path, silent)["cost"]
    assert (none["status"], none["usd"], none["unpriced_calls"]) == ("unknown", None, 2)


def test_la_carte_du_portefeuille_porte_le_meme_cout(tmp_path: Path) -> None:
    """Exécuter en portée projet et en portefeuille lisent la même arithmétique, pas deux copies."""
    service = TaskService(tmp_path)
    tid = _running(service, "Carte lue par les deux portées")
    _dispatch(tmp_path, tid, 0.1)
    _dispatch(tmp_path, tid, None)
    reg.save_registry([{"slug": "p", "name": "P", "path": str(tmp_path)}])

    card = next(t for t in wp.portfolio_tasks(tmp_path)["tasks"] if t["id"] == tid)

    assert card["cost"] == _card(tmp_path, tid)["cost"]
    assert card["cost"]["status"] == "lower_bound"


def test_la_carte_du_projet_porte_vivacite_dernier_signe_duree_et_cout_de_session(tmp_path: Path) -> None:
    """En portée projet la carte n'avait que l'identifiant de session : ni vivacité, ni signe de vie, ni durée."""
    service = TaskService(tmp_path)
    tid = _running(service, "Carte avec une session vivante")
    now = datetime.now(UTC)
    _journal(tmp_path, "S-vive", task_id=tid, started=now - timedelta(minutes=12), updated=now - timedelta(minutes=2))
    service.attach_session(tid, "S-vive", session_host="claude-code-cli", actor="agent-a")

    session = _card(tmp_path, tid)["session"]

    assert session["id"] == "S-vive"
    assert session["liveness"]["state"] == "vivant"
    assert session["updated_at"] == (now - timedelta(minutes=2)).isoformat(), "dernier signe de vie"
    assert session["duration_s"] == 600, "du premier au dernier signe de vie : 12 min - 2 min"
    assert session["cost"]["status"] == "unknown", "le coût de la session hôte n'est mesuré nulle part"
    assert session["cost"]["usd"] is None


def test_une_duree_qu_on_ne_peut_pas_calculer_n_est_pas_zero(tmp_path: Path) -> None:
    """Journal sans `started_at` lisible : la durée est inconnue (`None`), jamais `0`."""
    service = TaskService(tmp_path)
    tid = _running(service, "Carte au journal abîmé")
    path = _journal(tmp_path, "S-abimee", task_id=tid, started=datetime.now(UTC), updated=datetime.now(UTC))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["started_at"] = "pas une date"
    path.write_text(json.dumps(data), encoding="utf-8")
    service.attach_session(tid, "S-abimee", session_host="claude-code-cli", actor="agent-a")

    assert _card(tmp_path, tid)["session"]["duration_s"] is None
