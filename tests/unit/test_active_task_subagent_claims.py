"""Issue #695 — les sous-agents partagent le session_id du parent : l'attach explicite l'emporte.

Les sous-agents (outil ``Agent`` de Claude Code) héritent de
``CLAUDE_CODE_SESSION_ID`` : leurs ``task claim`` se rattachent à la session de
l'orchestrateur. Avec deux claims rattachés à la même session,
``resolve_active_task`` retombait sur ``ambiguous`` → « Clôture hors tâche »,
même juste après un ``grimoire task attach`` explicite.

Règle : parmi les claims d'une session, le dernier rattachement *explicite*
(``task attach``, marqué dans l'événement ``task.session_attached``) gagne ; sans
rattachement explicite, puis à ``GRIMOIRE_ACTOR`` près, on retombe sur
l'ambiguïté nommée de #686.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_state import resolve_active_task
from grimoire.missions.service import TaskService

SESSION = "dddddddd-0000-4000-8000-00000000000d"
ENV = {"CLAUDE_CODE_SESSION_ID": SESSION}
ACCEPTATION = "le hook resout la tache de la session"

runner = CliRunner()


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _ready(root: Path, title: str) -> str:
    service = TaskService(root)
    added = service.add(title, (ACCEPTATION,), owner="guilhem", actor="claude", ready=True)
    service.context(added.task.id)
    return added.task.id


def _claim(root: Path, task_id: str, actor: str) -> None:
    """Un claim lancé depuis la session (orchestrateur ou sous-agent : même env)."""
    res = runner.invoke(
        task_app, ["claim", task_id, "--project-root", str(root), "--actor", actor], obj={"output": "text"}, env=ENV
    )
    assert res.exit_code == 0, res.output


def _attach(root: Path, task_id: str) -> None:
    res = runner.invoke(
        task_app, ["attach", task_id, "--project-root", str(root)], obj={"output": "text"}, env=ENV
    )
    assert res.exit_code == 0, res.output


def _resolved(root: Path, env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return resolve_active_task(root, env=env or {}, session_id=SESSION)


def test_un_attach_apres_deux_claims_de_la_session_designe_sa_tache(governed: Path) -> None:
    tid_a = _ready(governed, "Orchestrateur")
    tid_b = _ready(governed, "Sous-agent")
    _claim(governed, tid_a, "claude")
    _claim(governed, tid_b, "lot-l9")
    # Les deux claims sont rattachés à la même session : c'est l'ambiguïté de #695.
    assert _resolved(governed).source == "ambiguous"

    _attach(governed, tid_a)

    active = _resolved(governed)
    assert (active.task_id, active.source) == (tid_a, "session_claim")


def test_un_attach_puis_un_claim_de_sous_agent_l_attach_gagne_toujours(governed: Path) -> None:
    tid_a = _ready(governed, "Orchestrateur")
    tid_b = _ready(governed, "Sous-agent")
    _claim(governed, tid_a, "claude")
    _attach(governed, tid_a)
    _claim(governed, tid_b, "lot-l9")  # arrive après : un claim incident ne détrône pas l'intention

    assert _resolved(governed).task_id == tid_a


def test_deux_attach_successifs_le_dernier_gagne(governed: Path) -> None:
    tid_a = _ready(governed, "Premiere")
    tid_b = _ready(governed, "Seconde")
    _claim(governed, tid_a, "claude")
    _claim(governed, tid_b, "lot-l9")

    _attach(governed, tid_a)
    _attach(governed, tid_b)
    assert _resolved(governed).task_id == tid_b

    _attach(governed, tid_a)  # re-déclarer A, déjà rattachée à la session, compte aussi
    assert _resolved(governed).task_id == tid_a


def test_sans_attach_explicite_l_ambiguite_nomme_les_candidates(governed: Path) -> None:
    tid_a = _ready(governed, "Premiere")
    tid_b = _ready(governed, "Seconde")
    _claim(governed, tid_a, "claude")
    _claim(governed, tid_b, "lot-l9")

    active = _resolved(governed)

    assert (active.task_id, active.source) == ("bootstrap", "ambiguous")
    assert set(active.candidates) == {tid_a, tid_b}


def test_sans_attach_l_acteur_connu_departage_les_claims_de_la_session(governed: Path) -> None:
    tid_a = _ready(governed, "Orchestrateur")
    tid_b = _ready(governed, "Sous-agent")
    _claim(governed, tid_a, "claude")
    _claim(governed, tid_b, "lot-l9")

    assert _resolved(governed, {"GRIMOIRE_ACTOR": "lot-l9"}).task_id == tid_b
    assert _resolved(governed, {"GRIMOIRE_ACTOR": "claude"}).task_id == tid_a
    # Un acteur qui ne désigne aucun claim ne tranche rien (et ne devine pas).
    assert _resolved(governed, {"GRIMOIRE_ACTOR": "inconnu"}).task_id == "bootstrap"


def test_un_attach_explicite_est_marque_au_ledger_pas_le_rattachement_automatique(governed: Path) -> None:
    tid_a = _ready(governed, "Auto")
    tid_b = _ready(governed, "Explicite")
    _claim(governed, tid_a, "claude")
    _claim(governed, tid_b, "lot-l9")
    _attach(governed, tid_b)

    service = TaskService(governed)
    auto = [e for e in service.ledger.list_events(tid_a) if e.event_type == "task.session_attached"]
    explicit = [e for e in service.ledger.list_events(tid_b) if e.event_type == "task.session_attached"]
    assert [e.payload.get("explicit", False) for e in auto] == [False]
    assert [e.payload.get("explicit", False) for e in explicit] == [False, True]


def test_un_attach_ne_vole_pas_le_claim_d_une_autre_session(governed: Path) -> None:
    tid = _ready(governed, "Tenue ailleurs")
    _claim(governed, tid, "claude")
    res = runner.invoke(
        task_app,
        ["attach", tid, "--project-root", str(governed)],
        obj={"output": "text"},
        env={"CLAUDE_CODE_SESSION_ID": "autre-session"},
    )
    assert res.exit_code != 0
