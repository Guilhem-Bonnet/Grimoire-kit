"""Épique #1280, lot C2 : une session Copilot est identifiée, rattachée à la tâche en cours, visible dans la Flotte.

Aucun Copilot n'est lancé : la charge du hook ``sessionStart`` est celle de la fixture
``tests/fixtures/hosts/copilot/copilot-harness/session-start.json`` (camelCase, ``sessionId``), le journal est
composé comme dans ``test_copilot_usage.py``. Non vérifié sur une session Copilot réelle.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.session_link import canonical_host, session_fields
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_fleet_live as fleet
from grimoire.traces.copilot_usage import LOGS_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "hosts" / "copilot" / "copilot-harness"
SESSION = "00000000-0000-4000-8000-0000000000c1"
runner = CliRunner()


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", LOGS_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    root = tmp_path / "projet"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    reg.save_registry([{"slug": "projet", "name": "Projet", "path": str(root.resolve())}])
    fleet.reset_cache()
    return root


def _claimed(projet: Path) -> tuple[TaskService, str]:
    service = TaskService(projet)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, "Un sujet", acceptance=("c'est fait",), owner="guilhem")
    service.transition(task.id, TaskState.READY, "guilhem")
    service.context(task.id)
    service.claim(task.id, "copilot-session", "copilot")
    return service, task.id


def _session_start_payload(projet: Path) -> dict[str, Any]:
    payload = json.loads((FIXTURES / "session-start.json").read_text(encoding="utf-8"))["payloads"][0]
    return {**payload["input"], "cwd": str(projet)}


def test_le_hook_session_start_de_copilot_rattache_la_session_au_claim(projet: Path) -> None:
    service, task_id = _claimed(projet)
    assert service.require(task_id).claim.session_id == ""
    _, _, hook = run_hook(
        _session_start_payload(projet), host_id=HostId.GITHUB_COPILOT, event=HookEvent.SESSION_START, project_root=projet
    )
    assert hook.session_id == SESSION and hook.host == HostId.GITHUB_COPILOT.value
    claim = service.require(task_id).claim
    assert claim.session_id == SESSION
    assert canonical_host(claim.session_host) == HostId.GITHUB_COPILOT.value
    fields = session_fields(service.require(task_id))
    assert fields["session_id"] == SESSION and fields["host"] == HostId.GITHUB_COPILOT.value
    # Aucune commande de reprise inventée : Copilot n'en a pas de documentée dans le dépôt.
    assert fields["resume_command"] == "" and fields["logs_command"] == ""


def test_une_session_ne_vole_pas_un_claim_deja_rattache(projet: Path) -> None:
    service, task_id = _claimed(projet)
    service.attach_session(task_id, "11111111-0000-4000-8000-000000000002", session_host="copilot", actor="copilot-session")
    run_hook(_session_start_payload(projet), host_id=HostId.GITHUB_COPILOT, event=HookEvent.SESSION_START, project_root=projet)
    assert service.require(task_id).claim.session_id == "11111111-0000-4000-8000-000000000002"


def test_task_attach_nomme_l_hote_copilot(projet: Path) -> None:
    service, task_id = _claimed(projet)
    result = runner.invoke(
        app, ["task", "attach", task_id, "--session-id", SESSION, "--host", "copilot", "--project-root", str(projet)]
    )
    assert result.exit_code == 0, result.stdout
    claim = service.require(task_id).claim
    assert (claim.session_id, canonical_host(claim.session_host)) == (SESSION, HostId.GITHUB_COPILOT.value)


def test_task_attach_sans_hote_inchange_et_hote_inconnu_refuse(projet: Path) -> None:
    service, task_id = _claimed(projet)
    refused = runner.invoke(
        app, ["task", "attach", task_id, "--session-id", SESSION, "--host", "inconnu", "--project-root", str(projet)]
    )
    assert refused.exit_code == 2
    assert service.require(task_id).claim.session_id == ""
    plain = runner.invoke(app, ["task", "attach", task_id, "--session-id", SESSION, "--project-root", str(projet)])
    assert plain.exit_code == 0
    assert service.require(task_id).claim.session_host == ""


def test_task_attach_sans_session_json_et_claim_absent(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, task_id = _claimed(projet)
    root = ["--project-root", str(projet)]
    assert runner.invoke(app, ["task", "attach", task_id, *root]).exit_code == 1  # aucune session identifiable
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", SESSION)
    out = runner.invoke(app, ["-o", "json", "task", "attach", task_id, "--host", "copilot", *root])
    assert out.exit_code == 0 and json.loads(out.stdout) == {"task_id": task_id, "session_id": SESSION}
    # Une tâche sans claim : refus net, rien n'est rattaché.
    other = service.ledger.create_task(service.ledger.list_missions()[0].id, "Autre", acceptance=("x",), owner="g")
    assert runner.invoke(app, ["task", "attach", other.id, "--host", "copilot", *root]).exit_code == 1


def test_la_flotte_montre_la_session_copilot_rattachee_a_sa_tache(
    projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, task_id = _claimed(projet)
    run_hook(_session_start_payload(projet), host_id=HostId.GITHUB_COPILOT, event=HookEvent.SESSION_START, project_root=projet)
    logs = tmp_path / "logs"
    journal = logs / SESSION / "events.jsonl"
    journal.parent.mkdir(parents=True)
    events = [
        {"type": "session.start", "data": {"sessionId": SESSION}},
        {"type": "hook.start", "data": {"hookType": "sessionStart", "input": _session_start_payload(projet)}},
        {"type": "assistant.message", "data": {"messageId": "m1", "model": "claude-sonnet-4.6", "outputTokens": 12}},
    ]
    journal.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    monkeypatch.setenv(LOGS_ENV, str(logs))
    view = fleet.fleet_live_view(projet, now=datetime.now(UTC))
    (session,) = view["sessions"]
    assert session["id"] == SESSION and session["host"] == HostId.GITHUB_COPILOT.value
    assert session["task_id"] == task_id
    assert session["links"]["task"].endswith(task_id)
    assert service.require(task_id).claim.session_id == SESSION


def test_la_matrice_dit_comment_la_session_copilot_est_rattachee() -> None:
    from grimoire.hosts.capability_matrix import CellState, build_capability_matrix

    cell = build_capability_matrix(HostId.GITHUB_COPILOT).cell("session_link", "vscode")
    assert cell.state is CellState.DEGRADED
    assert "hook SessionStart" in cell.detail and "task attach --host" in cell.detail
    assert "fixtures" in cell.detail
    claude = build_capability_matrix(HostId.CLAUDE_CODE_CLI).cell("session_link", "host")
    assert "fixtures" not in claude.detail
