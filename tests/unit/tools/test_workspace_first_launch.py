"""Premier lancement (P45) : « Lancer » exécute les actions typées, vérifie, reprend.

Docker est un faux démon (``docker`` introuvable ou injoignable), la mémoire un
gestionnaire en mémoire vive : aucun test ne demande Docker ni service mémoire.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.tools import workspace_actions as wact
from grimoire.tools import workspace_first_launch as fl
from grimoire.tools import workspace_machine as wmach
from grimoire.tools.workspace_routes import GET_ROUTES, POST_ROUTES, PREFIX, workspace_get

LEXICAL = "  backend: local\n"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


def _project(root: Path, memory: str = LEXICAL) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(f"project:\n  name: Kit\nmemory:\n{memory}", encoding="utf-8")
    return root


def _profile(pid: str, label: str, *, possible: bool | None = True, start: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": pid,
        "label": label,
        "possible": possible,
        "reasons": ["demande Docker"] if possible is False else [],
        "start": start or [],
    }


GUIDE = {
    "profiles": [
        _profile("lexical", "Lexical"),
        _profile("standard", "Standard"),
        _profile("graphe", "Graphe", start=["Weaviate", "Neo4j"]),
        _profile("complet", "Complet", possible=False),
    ]
}


class FakeEntry:
    def __init__(self, entry_id: str, text: str) -> None:
        self.id, self.text = entry_id, text


class FakeManager:
    """Un backend à état ; ``blind`` : la recherche ne remonte rien, ``leaky`` : le retrait échoue."""

    def __init__(self, *, blind: bool = False, leaky: bool = False, broken: bool = False) -> None:
        self.entries: dict[str, FakeEntry] = {}
        self.blind, self.leaky, self.broken = blind, leaky, broken

    def store(self, text: str, **_kw: Any) -> FakeEntry:
        if self.broken:
            raise RuntimeError("service muet")
        entry = FakeEntry(f"m{len(self.entries) + 1}", text)
        self.entries[entry.id] = entry
        return entry

    def search(self, query: str, **_kw: Any) -> list[FakeEntry]:
        return [] if self.blind else [e for e in self.entries.values() if query in e.text]

    def recall(self, entry_id: str) -> FakeEntry | None:
        return self.entries.get(entry_id)

    def delete(self, entry_id: str, **_kw: Any) -> bool:
        if self.leaky:
            return False
        return self.entries.pop(entry_id, None) is not None


def _docker_muet(_argv: list[str]) -> tuple[int, str, str]:
    return 1, "", "Cannot connect to the Docker daemon"


def _env(manager: FakeManager | None = None, doctor: dict[str, Any] | None = None) -> fl.LaunchEnv:
    probes = wmach.MachineProbes(
        docker=_docker_muet,
        tcp=lambda *_a: "arrete",
        version=lambda *_a: (None, "muet"),
        which=lambda _n: None,
        module_installed=lambda _m: True,
        env={},
    )
    actions = wact.ActionEnv(
        run=lambda argv, cwd: _docker_muet(argv),
        probes=probes,
        sessions=lambda _r: [],
        memory_dir=lambda: Path("/nonexistent"),
        now=lambda: NOW,
    )
    view = doctor or {"ok": True, "code": 0, "timed_out": False, "lines": ["✓ tout va bien"]}
    mgr = manager or FakeManager()
    return fl.LaunchEnv(
        actions=actions,
        manager=lambda _root: mgr,
        doctor=lambda _root: view,
        guide=lambda _root: GUIDE,
        now=lambda: NOW,
    )


def _verb(root: Path, env: fl.LaunchEnv, verb: str, **extra: Any) -> dict[str, Any]:
    return fl.launch_verb(root, {"verb": verb, **extra}, env=env)


def _statuses(view: dict[str, Any]) -> dict[str, str]:
    return {s["id"]: s["status"] for s in view["steps"]}


def _run_to_end(root: Path, env: fl.LaunchEnv) -> dict[str, Any]:
    view = fl.launch_state(root)
    for _ in range(10):
        if view["finished"] or any(s["status"] == fl.FAILED for s in view["steps"]):
            break
        view = _verb(root, env, "next")
    return view


def test_lancer_applique_les_actions_typees_puis_verifie(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    env = _env()
    view = _verb(root, env, "start", profile="standard")
    assert _statuses(view) == {"compose": "pending", "verify.memory": "pending", "verify.doctor": "pending"}
    assert view["steps"][0]["action"] == "memory.compose" and view["current"] == "compose"
    view = _verb(root, env, "next")
    assert _statuses(view)["compose"] == "done" and view["current"] == "verify.memory"
    assert "memory.backend" in view["steps"][0]["detail"] or "écrit" in view["steps"][0]["detail"]
    view = _run_to_end(root, env)
    assert view["finished"] and set(_statuses(view).values()) == {"done"}
    # Le journal de P40 a vu passer plan, apply, effets : rien n'est passé à côté du catalogue.
    events = [json.loads(line)["event"] for line in wact.journal_path(root).read_text().splitlines()]
    assert events[:2] == ["plan", "apply_started"] and "applied" in events
    assert "backend: local" not in (root / "project-context.yaml").read_text()


def test_la_verification_fait_l_aller_retour_memoire(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    manager = FakeManager()
    findings = fl.memory_roundtrip(root, _env(manager))
    assert [(f["id"], f["state"]) for f in findings] == [("write", "ok"), ("find", "ok"), ("remove", "ok")]
    assert manager.entries == {}  # le souvenir de test est retiré


def test_un_ecart_memoire_s_affiche_avec_son_remede(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    leaky = FakeManager(leaky=True)
    findings = {f["id"]: f for f in fl.memory_roundtrip(root, _env(leaky))}
    assert findings["remove"]["state"] == "bad" and "grimoire memory delete m1" in findings["remove"]["remedy"]
    blind = FakeManager(blind=True)
    found = {f["id"]: f for f in fl.memory_roundtrip(root, _env(blind))}
    assert found["find"]["state"] == "warn" and "reindex" in found["find"]["remedy"]
    assert found["remove"]["state"] == "ok"
    broken = {f["id"]: f for f in fl.memory_roundtrip(root, _env(FakeManager(broken=True)))}
    assert broken["write"]["state"] == "bad" and broken["write"]["remedy"]
    assert "find" not in broken


def test_une_verification_en_ecart_arrete_le_lancement_et_garde_le_remede(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    env = _env(FakeManager(leaky=True))
    _verb(root, env, "start", profile="standard")
    view = _run_to_end(root, env)
    memory = next(s for s in view["steps"] if s["id"] == "verify.memory")
    assert (
        memory["status"] == "failed"
        and "Retirer" in memory["error"]["message"]
        and "memory delete" in memory["error"]["remedy"]
    )
    assert not view["finished"] and _statuses(view)["verify.doctor"] == "pending"
    with pytest.raises(ValueError, match="a échoué"):
        _verb(root, env, "next")
    with pytest.raises(ValueError, match="bloquante"):
        _verb(root, env, "skip", step="verify.memory")


def test_doctor_en_ecart_dit_la_reparation(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    bad = _env(doctor={"ok": False, "code": 1, "timed_out": False, "lines": ["✗ wrapper manquant", "✓ config"]})
    (finding,) = fl.doctor_findings(root, bad)
    assert finding["state"] == "bad" and "wrapper manquant" in finding["detail"] and "--fix" in finding["remedy"]


def test_echec_traduit_reessayer_et_sauter(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    env = _env()
    view = _verb(root, env, "start", profile="graphe")
    assert _statuses(view)["stack"] == "pending" and view["steps"][0]["skippable"] is True
    view = _verb(root, env, "next")
    stack = view["steps"][0]
    assert stack["status"] == "failed"
    assert "Docker" in stack["error"]["message"] and "Réessayer" in stack["error"]["remedy"]
    assert stack["error"]["raw"]  # le motif brut reste dit à part
    with pytest.raises(ValueError, match="a échoué"):
        _verb(root, env, "next")
    again = _verb(root, env, "retry", step="stack")
    assert again["steps"][0]["status"] == "failed"
    with pytest.raises(ValueError, match="pas en échec"):
        _verb(root, env, "retry", step="compose")
    skipped = _verb(root, env, "skip", step="stack")
    assert skipped["steps"][0]["status"] == "skipped" and skipped["current"] == "compose"
    with pytest.raises(ValueError, match="bloquante"):
        _verb(root, env, "skip", step="compose")
    assert _statuses(_run_to_end(root, env)) == {
        "stack": "skipped",
        "compose": "done",
        "verify.memory": "done",
        "verify.doctor": "done",
    }


def test_sans_project_context_la_composition_est_bloquee_avec_la_commande_init(tmp_path: Path) -> None:
    root = tmp_path / "vierge"
    root.mkdir()
    env = _env()
    _verb(root, env, "start", profile="standard")
    view = _verb(root, env, "next")
    compose = view["steps"][0]
    assert compose["status"] == "failed" and "grimoire init" in compose["error"]["remedy"]


def test_revenir_en_arriere_restaure_le_fichier_d_avant(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    before = (root / "project-context.yaml").read_text()
    env = _env()
    _verb(root, env, "start", profile="standard")
    _verb(root, env, "next")
    assert (root / "project-context.yaml").read_text() != before
    view = fl.launch_state(root)
    assert view["canRollback"] is True
    view = _verb(root, env, "rollback")
    assert _statuses(view)["compose"] == "rolled_back"
    assert (root / "project-context.yaml").read_text() == before
    with pytest.raises(ValueError, match="rien d'appliqué"):
        _verb(root, env, "rollback", step="compose")


def test_l_avancement_est_enregistre_et_reprend_apres_rechargement(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    env = _env()
    _verb(root, env, "start", profile="standard")
    _verb(root, env, "next")  # compose fait
    # Le processus meurt pendant la vérification : l'étape a été écrite « en cours » avant d'agir.
    path = fl._state_path(root)
    state = json.loads(path.read_text())
    state["steps"][1]["status"] = fl.RUNNING
    path.write_text(json.dumps(state), encoding="utf-8")

    reloaded = fl.launch_state(root)  # le rechargement relit le disque, pas la mémoire du processus
    assert reloaded["exists"] and reloaded["interrupted"] == "verify.memory" and reloaded["current"] == "verify.memory"
    assert _statuses(reloaded)["compose"] == "done"
    resumed = _verb(root, _env(), "next")
    assert _statuses(resumed)["verify.memory"] == "done" and resumed["current"] == "verify.doctor"
    assert _statuses(resumed)["compose"] == "done"  # l'étape faite n'est pas rejouée


def test_demarrer_refuse_un_profil_impossible_ou_un_lancement_en_cours(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    env = _env()
    with pytest.raises(ValueError, match="impossible ici"):
        _verb(root, env, "start", profile="complet")
    with pytest.raises(ValueError, match="inconnu"):
        _verb(root, env, "start", profile="x")
    with pytest.raises(ValueError, match="aucun lancement"):
        _verb(root, env, "next")
    with pytest.raises(ValueError, match="verbe inconnu"):
        _verb(root, env, "rm")
    _verb(root, env, "start", profile="graphe")
    _verb(root, env, "next")  # échec de la pile
    with pytest.raises(ValueError, match="en cours"):
        _verb(root, env, "start", profile="standard")
    assert _verb(root, env, "reset")["exists"] is False


def test_routes_get_et_post(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit")
    assert f"{PREFIX}setup/launch" in GET_ROUTES and f"{PREFIX}setup/launch" in POST_ROUTES
    assert workspace_get(root, f"{PREFIX}setup/launch", {})["exists"] is False
