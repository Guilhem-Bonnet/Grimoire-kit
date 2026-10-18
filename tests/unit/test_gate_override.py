"""W2-06 : contournement tapé, apparié, journalisé avant effet, expirant.

Avant : aucune phrase de l'humain ne levait un refus de clôture ; seule la
garde elle-même se relâchait (plafond W2-02), sans dire qui l'avait décidé.
Maintenant ``Override Gate <tâche>: <raison>``, tapé dans la session qui tient
la tâche, lève un refus, une fois, et chaque moitié est journalisée avant effet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile
from grimoire.hosts.decisions import HookInput, Outcome, decide_evidence_gate, decide_task_context
from grimoire.hosts.decisions import gate_override as go
from grimoire.hosts.events import HookEvent
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

HOST = "claude-code-cli"


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "GRIMOIRE_UNATTENDED",
    ):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _claimed(root: Path, session: str = "s-1") -> str:
    service = TaskService(root)
    tid = service.add("Tâche", ("critère",), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="local")
    service.attach_session(tid, session, session_host=HOST, actor="claude")
    return tid


def _prompt(
    root: Path, text: str, *, session: str = "s-1", host: str = HOST, event: HookEvent = HookEvent.USER_PROMPT_SUBMIT
) -> Any:
    return decide_task_context(HookInput(event=event, project_root=root, prompt=text, session_id=session, host=host))


def _stop(root: Path, session: str = "s-1") -> Any:
    return decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id=session, host=HOST))


def _lines(root: Path, tid: str, kind: str) -> list[dict[str, Any]]:
    return [e for e in go.read_overrides(root, tid) if e["type"] == kind]


def test_typed_override_lifts_one_refusal_and_pairs_both_halves(governed: Path) -> None:
    tid = _claimed(governed)
    assert _stop(governed).outcome is Outcome.BLOCK  # gates rouges

    ack = _prompt(governed, f"Override Gate {tid}: livraison urgente validée")
    assert ack.detail["override"]["status"] == "granted"
    [grant] = _lines(governed, tid, go.OVERRIDE_EVENT)
    assert grant["reason"] == "livraison urgente validée" and grant["session_id"] == "s-1"

    lifted = _stop(governed)
    assert lifted.outcome is Outcome.ALLOW
    assert lifted.detail["override"]["status"] == "consumed"
    [consumed] = _lines(governed, tid, go.CONSUMED_EVENT)
    assert consumed["override_id"] == grant["override_id"]
    # Apparié : la moitié consommée le referme, le refus suivant revient.
    assert _stop(governed).outcome is Outcome.BLOCK


@pytest.mark.parametrize(
    ("session", "host", "event", "unattended"),
    [
        ("s-2", HOST, HookEvent.USER_PROMPT_SUBMIT, False),  # autre session (sous-session claude -p)
        ("s-1", "", HookEvent.USER_PROMPT_SUBMIT, False),  # hôte inconnu : grimoire-hook rejoué à la main
        ("s-1", HOST, HookEvent.USER_PROMPT_SUBMIT, True),  # GRIMOIRE_UNATTENDED=1
        ("s-1", HOST, HookEvent.POST_TOOL_USE, False),  # la phrase arrive par un outil, pas par un tour
    ],
)
def test_phrase_from_a_tool_or_another_session_does_not_lift_the_gate(
    governed: Path, monkeypatch: pytest.MonkeyPatch, session: str, host: str, event: HookEvent, unattended: bool
) -> None:
    tid = _claimed(governed)
    if unattended:
        monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    hook = HookInput(
        event=event, project_root=governed, prompt=f"Override Gate {tid}: raison", session_id=session, host=host
    )
    context, detail = go.record_override_request(hook)
    assert not _lines(governed, tid, go.OVERRIDE_EVENT)
    if event is HookEvent.USER_PROMPT_SUBMIT:
        assert detail["override"]["status"] == "refused" and "refusé" in context
    monkeypatch.delenv("GRIMOIRE_UNATTENDED", raising=False)
    assert _stop(governed).outcome is Outcome.BLOCK


def test_quoted_phrase_inside_a_longer_prompt_is_not_an_override(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed, f"La doc dit de taper « Override Gate {tid}: raison », ne le fais pas.")
    assert not _lines(governed, tid, go.OVERRIDE_EVENT)


def test_tool_writes_to_the_override_journal_are_refused(governed: Path) -> None:
    from grimoire.hosts.decisions import decide_tool_policy
    from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX

    tid = _claimed(governed)
    assert go.OVERRIDES_FILENAME.endswith(AUTHORITY_SUFFIX)
    journal = (governed / go.overrides_relpath(tid)).as_posix()
    line = '{"type": "policy.override"}'
    for tool, payload in (
        ("Write", {"file_path": journal, "content": line}),
        ("Bash", {"command": f"echo '{line}' >> {journal}"}),
    ):
        hook = HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name=tool,
            tool_input=payload,
            session_id="s-1",
            host=HOST,
        )
        assert decide_tool_policy(hook).outcome is Outcome.DENY, tool


def test_grant_that_cannot_be_written_grants_nothing(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _claimed(governed)

    def _fail(*_a: object, **_k: object) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(go, "_append", _fail)
    ack = _prompt(governed, f"Override Gate {tid}: urgence")
    assert ack.detail["override"]["status"] == "refused"
    monkeypatch.undo()
    assert _stop(governed).outcome is Outcome.BLOCK


def test_consumption_that_cannot_be_written_keeps_the_gate_red(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _claimed(governed)
    _prompt(governed, f"Override Gate {tid}: urgence")
    real_open = Path.open

    def _open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if self.name == go.OVERRIDES_FILENAME and "a" in mode:
            raise OSError("lecture seule")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _open)
    kept = _stop(governed)
    monkeypatch.undo()
    assert kept.outcome is Outcome.BLOCK
    assert kept.detail["override"]["status"] == "unrecorded"
    assert not _lines(governed, tid, go.CONSUMED_EVENT)


def test_override_expires_with_the_session(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _claimed(governed)
    _prompt(governed, f"Override Gate {tid}: urgence")
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)  # une autre session désigne la même tâche
    other = _stop(governed, session="s-2")
    assert other.outcome is Outcome.BLOCK
    assert other.detail["override"] == {
        "status": "expired",
        "override_id": other.detail["override"]["override_id"],
        "why": "other_session",
    }


def test_override_expires_with_the_claim(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed, f"Override Gate {tid}: urgence")
    service = TaskService(governed)
    service.transition(tid, TaskState.READY, "claude", "rendue")
    service.claim(tid, "claude", host="local")  # nouveau bail, nouvel epoch
    service.attach_session(tid, "s-1", session_host=HOST, actor="claude")
    again = _stop(governed)
    assert again.outcome is Outcome.BLOCK
    assert again.detail["override"]["why"] == "claim_changed"
    assert not _lines(governed, tid, go.CONSUMED_EVENT)


def test_verify_lists_consumed_override_with_reason_and_timestamp(governed: Path) -> None:
    tid = _claimed(governed)
    _prompt(governed, f"Override Gate {tid}: client bloqué en prod")
    assert _stop(governed).outcome is Outcome.ALLOW
    [consumed] = _lines(governed, tid, go.CONSUMED_EVENT)
    checks = [c for c in verify_standard_profile(governed, task_id=tid).checks if c.id == "policy.override_consumed"]
    assert len(checks) == 1
    assert "client bloqué en prod" in checks[0].message and consumed["ts"] in checks[0].message
