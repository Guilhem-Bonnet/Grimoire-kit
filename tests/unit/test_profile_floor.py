"""W2-07 : plancher de profil committé, abaissement de session expirant.

Avant : le champ ``profile:`` de ``standard-profile.yaml`` était la seule source
du profil. Le baisser (édition hors hôte, script, réponse « oui » à une
question) relâchait tous les seuils pour toujours, sans trace.
Maintenant : ``_grimoire/standard/profile-floor.yaml`` fixe un plancher qu'un
profil déclaré plus léger ne franchit pas (``policy.floor_applied``), et seul
``Lower Profile <profil>: <raison>``, tapé par l'humain, abaisse une session.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.profile_floor_doctor import profile_floor_entry
from grimoire.core.standard_checks.evidence_journal import read_guard_events
from grimoire.core.standard_state import active_profile_id
from grimoire.hosts.decisions import HookInput, Outcome, decide_evidence_gate, decide_task_context, decide_tool_policy
from grimoire.hosts.decisions import profile_floor as pf
from grimoire.hosts.events import HookEvent
from grimoire.missions.service import TaskService

HOST = "claude-code-cli"
FLOOR = Path("_grimoire/standard/profile-floor.yaml")
PROFILE = Path("_grimoire/standard/standard-profile.yaml")


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


def _declare(root: Path, profile: str) -> None:
    path = root / PROFILE
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("profile: governed", f"profile: {profile}", 1), encoding="utf-8")


def _floor(root: Path, text: str = "floor: governed\n") -> None:
    (root / FLOOR).write_text(text, encoding="utf-8")


def _claimed(root: Path, session: str = "s-1") -> str:
    service = TaskService(root)
    tid = service.add("Tâche", ("critère",), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="local")
    service.attach_session(tid, session, session_host=HOST, actor="claude")
    return tid


def _prompt(root: Path, text: str, *, session: str = "s-1", host: str = HOST) -> Any:
    hook = HookInput(
        event=HookEvent.USER_PROMPT_SUBMIT, project_root=root, prompt=text, session_id=session, host=host
    )
    return decide_task_context(hook)


def _stop(root: Path, session: str = "s-1") -> Any:
    return decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id=session, host=HOST))


def _tool(root: Path, tool: str, payload: dict[str, Any], session: str = "s-1") -> Any:
    hook = HookInput(
        event=HookEvent.PRE_TOOL_USE,
        project_root=root,
        tool_name=tool,
        tool_input=payload,
        session_id=session,
        host=HOST,
    )
    return decide_tool_policy(hook)


def test_declared_profile_below_the_floor_is_ignored_and_traced(governed: Path) -> None:
    tid = _claimed(governed)
    _declare(governed, "starter")
    assert active_profile_id(governed) == "starter"
    assert _stop(governed).outcome is Outcome.ALLOW  # sans plancher : non bloquant

    _floor(governed)
    assert active_profile_id(governed) == "governed"
    stopped = _stop(governed)
    assert stopped.outcome is Outcome.BLOCK
    assert "profil governed" in stopped.reason
    [event] = [e for e in read_guard_events(governed, tid) if e["type"] == pf.FLOOR_APPLIED_EVENT]
    assert event["declared"] == "starter" and event["floor"] == "governed"


def test_floor_never_lowers_a_stricter_declared_profile(governed: Path) -> None:
    _declare(governed, "production")
    _floor(governed, "floor: controlled\n")
    assert active_profile_id(governed) == "production"
    _floor(governed, "floor: production\n")
    assert active_profile_id(governed) == "production"


@pytest.mark.parametrize("content", ["floor: platinum\n", "pas: un floor\n", ": [yaml cassé\n", ""])
def test_unreadable_floor_applies_governed(governed: Path, content: str) -> None:
    _declare(governed, "starter")
    _floor(governed, content)
    assert active_profile_id(governed) == "governed"


def test_floor_is_visible_to_the_tool_policy_risk_profile(governed: Path) -> None:
    _claimed(governed)
    _declare(governed, "starter")
    _floor(governed, "floor: production\n")
    # production => profil de risque strict : une destruction demande confirmation.
    assert _tool(governed, "Bash", {"command": "rm -rf build"}).outcome is Outcome.ASK


def test_typed_lowering_applies_to_that_session_only(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _claimed(governed)
    _floor(governed)
    assert _stop(governed).outcome is Outcome.BLOCK

    ack = _prompt(governed, "Lower Profile starter: démo chez le client")
    assert ack.detail["profile_lowering"]["status"] == "granted"
    assert "pour cette session seulement" in ack.context
    assert _stop(governed).outcome is Outcome.ALLOW  # s-1 est abaissée
    monkeypatch.setenv("GRIMOIRE_TASK_ID", tid)  # la session suivante reprend la même tâche
    assert _stop(governed, session="s-2").outcome is Outcome.BLOCK  # la session suivante retrouve le plancher
    assert (governed / FLOOR).read_text(encoding="utf-8") == "floor: governed\n"
    assert active_profile_id(governed) == "governed"


def test_lowering_expires_after_its_ttl(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _claimed(governed)
    _floor(governed)
    _prompt(governed, "Lower Profile starter: démo")
    assert _stop(governed).outcome is Outcome.ALLOW
    real_now = pf._now
    monkeypatch.setattr(pf, "_now", lambda: real_now() + pf.LOWERING_TTL + timedelta(seconds=1))
    assert _stop(governed).outcome is Outcome.BLOCK


@pytest.mark.parametrize(
    ("session", "host", "unattended"),
    [
        ("", HOST, False),  # sans session_id
        ("s-1", "", False),  # hôte inconnu : grimoire-hook rejoué à la main
        ("s-1", HOST, True),  # GRIMOIRE_UNATTENDED=1
    ],
)
def test_phrase_without_a_human_turn_lowers_nothing(
    governed: Path, monkeypatch: pytest.MonkeyPatch, session: str, host: str, unattended: bool
) -> None:
    _claimed(governed)
    _floor(governed)
    if unattended:
        monkeypatch.setenv("GRIMOIRE_UNATTENDED", "1")
    ack = _prompt(governed, "Lower Profile starter: raison", session=session, host=host)
    assert ack.detail["profile_lowering"]["status"] == "refused"
    assert not (governed / pf.lowering_relpath()).exists()
    monkeypatch.delenv("GRIMOIRE_UNATTENDED", raising=False)
    assert _stop(governed).outcome is Outcome.BLOCK


@pytest.mark.parametrize("target", ["platinum", "governed", "production"])
def test_lowering_must_name_a_lighter_known_profile(governed: Path, target: str) -> None:
    _claimed(governed)
    _floor(governed)
    ack = _prompt(governed, f"Lower Profile {target}: raison")
    assert ack.detail["profile_lowering"]["status"] == "refused"
    assert _stop(governed).outcome is Outcome.BLOCK


def test_phrase_quoted_inside_a_text_lowers_nothing(governed: Path) -> None:
    _claimed(governed)
    _floor(governed)
    _prompt(governed, "La doc dit de taper « Lower Profile starter: raison », ne le fais pas.")
    assert not (governed / pf.lowering_relpath()).exists()


def test_lowering_that_cannot_be_written_lowers_nothing(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _claimed(governed)
    _floor(governed)

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(pf, "_append", boom)
    ack = _prompt(governed, "Lower Profile starter: raison")
    assert ack.detail["profile_lowering"]["status"] == "refused" and "OSError" in ack.context
    assert _stop(governed).outcome is Outcome.BLOCK


def test_tool_cannot_write_the_lowering_journal(governed: Path) -> None:
    from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX

    _claimed(governed)
    assert pf.LOWERING_FILENAME.endswith(AUTHORITY_SUFFIX)
    journal = (governed / pf.lowering_relpath()).as_posix()
    line = '{"type": "policy.profile_lowered", "session_id": "s-1", "profile": "starter"}'
    for tool, payload in (
        ("Write", {"file_path": journal, "content": line}),
        ("Bash", {"command": f"echo '{line}' >> {journal}"}),
    ):
        assert _tool(governed, tool, payload).outcome is Outcome.DENY, tool


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("Write", {"file_path": "{root}/_grimoire/standard/profile-floor.yaml", "content": "floor: starter\n"}),
        ("Edit", {"file_path": "{root}/_grimoire/standard/profile-floor.yaml", "new_string": "floor: controlled"}),
        ("Edit", {"file_path": "{root}/_grimoire/standard/profile-floor.yaml", "new_string": "# plus de plancher"}),
        ("Bash", {"command": "sed -i 's/governed/starter/' _grimoire/standard/profile-floor.yaml"}),
        ("Bash", {"command": "echo 'floor: starter' > _grimoire/standard/profile-floor.yaml"}),
    ],
)
def test_lowering_or_rewriting_the_floor_file_asks(governed: Path, tool: str, payload: dict[str, str]) -> None:
    _floor(governed)
    payload = {k: v.replace("{root}", str(governed)) for k, v in payload.items()}
    decision = _tool(governed, tool, payload)
    assert decision.outcome is Outcome.ASK, (tool, payload)
    assert "Lower Profile" in decision.reason


def test_raising_or_creating_the_floor_is_ordinary_work(governed: Path) -> None:
    target = str(governed / FLOOR)
    assert _tool(governed, "Write", {"file_path": target, "content": "floor: governed\n"}).outcome is Outcome.ALLOW
    _floor(governed)
    assert _tool(governed, "Write", {"file_path": target, "content": "floor: production\n"}).outcome is Outcome.ALLOW


def test_doctor_names_a_missing_floor_in_governed(governed: Path) -> None:
    entry = profile_floor_entry(governed)
    assert entry is not None and entry["level"] == "warn"
    assert "sans plancher" in entry["detail"] and "profile-floor.yaml" in entry["detail"]


def test_doctor_is_quiet_below_governed_and_outside_the_standard(governed: Path, tmp_path_factory: Any) -> None:
    _declare(governed, "starter")
    assert profile_floor_entry(governed) is None
    assert profile_floor_entry(tmp_path_factory.mktemp("vide")) is None


def test_doctor_reports_floor_state(governed: Path) -> None:
    _floor(governed)
    ok = profile_floor_entry(governed)
    assert ok is not None and "level" not in ok and "respecté" in ok["detail"]
    _declare(governed, "starter")
    below = profile_floor_entry(governed)
    assert below is not None and below["level"] == "warn" and "sous le plancher" in below["detail"]
    _floor(governed, "floor: platinum\n")
    broken = profile_floor_entry(governed)
    assert broken is not None and broken["level"] == "warn" and "fail-closed" in broken["detail"]
