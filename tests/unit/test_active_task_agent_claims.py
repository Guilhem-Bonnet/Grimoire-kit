"""Issue #698 — le claim d'un sous-agent est rattaché à (session_id, agent_id), pas à la session seule.

Un sous-agent (outil ``Agent`` de Claude Code) hérite du ``session_id`` de son
parent et ne peut pas poser ``GRIMOIRE_ACTOR`` pour ses propres hooks. Les hooks
tirés depuis un sous-agent portent en revanche ``agent_id`` et ``agent_type``
(doc Claude Code, hooks : « the input carries the agent_id and agent_type common
input fields that identify the subagent »). Le ``PostToolUse`` qui observe un
``grimoire task claim`` pose cet ``agent_id`` sur le claim ; la résolution de la
tâche active rend ensuite à chaque sous-agent son propre claim, et à la boucle
principale (sans ``agent_id``) l'ordre de #696 puis le seul claim qu'aucun
sous-agent ne tient.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.cmd_task import task_app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_state import resolve_active_task
from grimoire.hosts.decisions.enrolment import claimed_task_in_call, link_agent_claim
from grimoire.hosts.decisions.evidence_trace import decide_evidence_trace
from grimoire.hosts.runtime import normalize_input
from grimoire.missions.service import TaskService

SESSION = "eeeeeeee-0000-4000-8000-00000000000e"
ENV = {"CLAUDE_CODE_SESSION_ID": SESSION}
ACCEPTATION = "le hook resout la tache du sous-agent"

runner = CliRunner()


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_PROJECT_DIR",
    ):
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
    """Un claim lancé depuis la session : orchestrateur ou sous-agent, même environnement."""
    res = runner.invoke(
        task_app, ["claim", task_id, "--project-root", str(root), "--actor", actor], obj={"output": "text"}, env=ENV
    )
    assert res.exit_code == 0, res.output


def _attach(root: Path, task_id: str) -> None:
    res = runner.invoke(task_app, ["attach", task_id, "--project-root", str(root)], obj={"output": "text"}, env=ENV)
    assert res.exit_code == 0, res.output


def _post_tool_use_payload(root: Path, command: str, *, agent_id: str = "", agent_type: str = "") -> dict[str, Any]:
    """Le payload PostToolUse de Claude Code pour un Bash, avec les champs d'un sous-agent s'il y en a un."""
    payload: dict[str, Any] = {
        "hook_event_name": "PostToolUse",
        "session_id": SESSION,
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": "claimed", "stderr": "", "interrupted": False},
    }
    if agent_id:
        payload["agent_id"] = agent_id
        payload["agent_type"] = agent_type or "general-purpose"
    return payload


def _observe_claim(root: Path, task_id: str, *, agent_id: str = "") -> None:
    """Le hook PostToolUse qui voit passer `grimoire task claim <id>` — le vrai chemin de décision."""
    payload = _post_tool_use_payload(root, f"grimoire task claim {task_id} --actor lot-l9", agent_id=agent_id)
    hook = normalize_input(payload, project_root=root, host_id=HostId.CLAUDE_CODE_CLI)
    decide_evidence_trace(hook)


def _resolved(root: Path, *, agent_id: str = "", env: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return resolve_active_task(root, env=env or {}, session_id=SESSION, agent_id=agent_id)


def _claim_agent(root: Path, task_id: str) -> str:
    task = TaskService(root).ledger.get_task(task_id)
    assert task is not None and task.claim is not None
    return task.claim.agent_id


# ── le payload de hook ────────────────────────────────────────────────────────


def test_le_payload_d_un_sous_agent_porte_agent_id(tmp_path: Path) -> None:
    payload = _post_tool_use_payload(tmp_path, "ls", agent_id="agent-0a1b", agent_type="Explore")
    hook = normalize_input(payload, project_root=tmp_path, host_id=HostId.CLAUDE_CODE_CLI)
    assert (hook.session_id, hook.agent_id) == (SESSION, "agent-0a1b")

    main_loop = normalize_input(_post_tool_use_payload(tmp_path, "ls"), project_root=tmp_path)
    assert main_loop.agent_id == ""


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected"),
    [
        ("Bash", {"command": "grimoire task claim GAO-abc-001 --actor lot-l9"}, "GAO-abc-001"),
        ("Bash", {"command": "cd /tmp && grimoire task claim GAO-abc-002"}, "GAO-abc-002"),
        ("Bash", {"command": "grimoire task show GAO-abc-003"}, ""),
        ("Bash", {"command": "echo 'grimoire task claim' in a message"}, ""),
        ("mcp__grimoire__task_claim", {"task_id": "GAO-abc-004", "actor": "x"}, "GAO-abc-004"),
        ("mcp__grimoire__task_show", {"task_id": "GAO-abc-005"}, ""),
        ("Edit", {"file_path": "grimoire task claim GAO-abc-006"}, ""),
    ],
)
def test_claimed_task_in_call(tool_name: str, tool_input: dict[str, Any], expected: str) -> None:
    assert claimed_task_in_call(tool_name, tool_input) == expected


# ── deux sous-agents d'une même session ───────────────────────────────────────


def test_deux_sous_agents_de_la_meme_session_resolvent_chacun_leur_tache(governed: Path) -> None:
    tid_a = _ready(governed, "Sous-agent A")
    tid_b = _ready(governed, "Sous-agent B")
    _claim(governed, tid_a, "lot-l9")
    _observe_claim(governed, tid_a, agent_id="agent-a")
    _claim(governed, tid_b, "lot-l9")
    _observe_claim(governed, tid_b, agent_id="agent-b")

    assert (_claim_agent(governed, tid_a), _claim_agent(governed, tid_b)) == ("agent-a", "agent-b")
    for_a = _resolved(governed, agent_id="agent-a")
    for_b = _resolved(governed, agent_id="agent-b")
    assert (for_a.task_id, for_a.source) == (tid_a, "session_claim")
    assert (for_b.task_id, for_b.source) == (tid_b, "session_claim")
    # Même acteur, même session : sans agent_id, rien ne départage — l'ambiguïté reste nommée.
    assert _resolved(governed).source == "ambiguous"


def test_la_boucle_principale_resout_le_rattachement_explicite_comme_dans_696(governed: Path) -> None:
    tid_main = _ready(governed, "Orchestrateur")
    tid_sub = _ready(governed, "Sous-agent")
    _claim(governed, tid_main, "claude")
    _claim(governed, tid_sub, "lot-l9")
    _observe_claim(governed, tid_sub, agent_id="agent-sub")
    _attach(governed, tid_main)

    assert _resolved(governed).task_id == tid_main
    # Le sous-agent garde la sienne : son propre claim passe avant l'intention de l'orchestrateur.
    assert _resolved(governed, agent_id="agent-sub").task_id == tid_sub


def test_la_boucle_principale_resout_le_seul_claim_qu_aucun_sous_agent_ne_tient(governed: Path) -> None:
    tid_main = _ready(governed, "Orchestrateur")
    tid_sub = _ready(governed, "Sous-agent")
    _claim(governed, tid_main, "claude")
    _observe_claim(governed, tid_main)  # la boucle principale : pas d'agent_id, rien n'est posé
    _claim(governed, tid_sub, "lot-l9")
    _observe_claim(governed, tid_sub, agent_id="agent-sub")

    assert _claim_agent(governed, tid_main) == ""
    active = _resolved(governed)
    assert (active.task_id, active.source) == (tid_main, "session_claim")


def test_un_sous_agent_sans_claim_propre_herite_de_la_regle_de_session(governed: Path) -> None:
    tid_main = _ready(governed, "Orchestrateur")
    _claim(governed, tid_main, "claude")

    assert _resolved(governed, agent_id="agent-lecteur").task_id == tid_main


# ── ce que le rattachement écrit, et ne vole pas ──────────────────────────────


def test_le_rattachement_est_un_evenement_de_plus_jamais_un_vol(governed: Path) -> None:
    tid = _ready(governed, "Tenue par A")
    _claim(governed, tid, "lot-l9")
    _observe_claim(governed, tid, agent_id="agent-a")
    _observe_claim(governed, tid, agent_id="agent-b")  # un second sous-agent voit passer le même claim

    assert _claim_agent(governed, tid) == "agent-a"
    service = TaskService(governed)
    attached = [e.payload for e in service.ledger.list_events(tid) if e.event_type == "task.session_attached"]
    assert [p.get("agent_id") for p in attached] == [None, "agent-a"]
    assert _resolved(governed, agent_id="agent-b").task_id == tid  # seul claim de la session : règle 2 inchangée


def test_un_claim_d_une_autre_session_n_apprend_pas_l_agent(governed: Path) -> None:
    tid = _ready(governed, "Ailleurs")
    res = runner.invoke(
        task_app,
        ["claim", tid, "--project-root", str(governed), "--actor", "claude"],
        obj={"output": "text"},
        env={"CLAUDE_CODE_SESSION_ID": "autre-session"},
    )
    assert res.exit_code == 0, res.output

    hook = normalize_input(
        _post_tool_use_payload(governed, f"grimoire task claim {tid}", agent_id="agent-x"),
        project_root=governed,
        host_id=HostId.CLAUDE_CODE_CLI,
    )
    out = link_agent_claim(hook)

    assert out == {"agent_claim_attached": False, "task_id": tid}
    assert _claim_agent(governed, tid) == ""


def test_agent_id_survit_a_la_serialisation_du_claim(governed: Path) -> None:
    from grimoire.missions.schemas import TaskClaim

    claim = TaskClaim.new("lot-l9", "local").with_session(SESSION, agent_id="agent-a")
    assert TaskClaim.from_dict(claim.to_dict()).agent_id == "agent-a"
    assert "agent_id" not in TaskClaim.new("lot-l9", "local").to_dict()
    assert claim.with_session(SESSION, explicit_at="2026-10-20T00:00:00+00:00").agent_id == "agent-a"
