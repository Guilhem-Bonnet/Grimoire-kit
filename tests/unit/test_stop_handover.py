"""D-12 : une session qui a rendu sa tâche (``needs_verification``) ou l'a garée peut s'arrêter.

Constat du scénario live Claude Code : ``Stop`` résout la tâche d'une session par son claim
``claimed``/``running`` seulement. Une tâche rendue pour vérification n'y est plus : la session
retombe sur ``bootstrap`` et, ayant écrit, se voit refuser une « clôture hors tâche » — le geste
même que le refus de ``Stop`` lui demandait (« Rends-la avant de conclure : task move --to
needs_verification »). Seule l'attente ``waiting`` (K6) était reconnue.

Rendue, la tâche est jugée sur ses gates de preuve : verts, la session sort ; rouges, le refus nomme
la tâche et ce qui manque, pas un travail hors tâche. Bloquée, échouée ou annulée, elle est garée.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_task_scaffold import scaffold_task_artifacts
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

SESSION = "sess-D12"
CRITERION = "le test passe"
S = TaskState


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_UNATTENDED", "GRIMOIRE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    return tmp_path


def _hook(root: Path, event: HookEvent, session: str = SESSION, **payload: Any) -> Any:
    body = {"session_id": session, "hook_event_name": event.value, "cwd": str(root), **payload}
    rendered, decision, _ = run_hook(body, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _edit(root: Path, session: str = SESSION) -> None:
    _hook(root, HookEvent.POST_TOOL_USE, session, tool_name="Edit",
          tool_input={"file_path": str(root / "src/app.py"), "old_string": "a", "new_string": "b"})


def _working(root: Path) -> tuple[TaskService, str]:
    """Une session qui tient sa tâche (running) et a écrit un fichier."""
    service = TaskService(root)
    tid = service.add("Travail", (CRITERION,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="host-claude-code-cli")
    service.transition(tid, S.RUNNING, "claude")
    service.attach_session(tid, SESSION, session_host="claude-code-cli", actor="claude")
    _edit(root)
    return service, tid


def _ready_claims(root: Path, tid: str) -> None:
    """Un claim ledger renseigné, dont la preuve se résout : le gate de preuve standard est vert."""
    ledger = root / "_grimoire-output" / "evidence" / tid / "claim-ledger.md"
    (root / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    text = ledger.read_text(encoding="utf-8").replace(
        "| CL-001 |  | fait |  | hypothèse | faible | vérifier |",
        "| CL-001 | add rend la somme | résultat | test_app.py | prouvé | élevée | utiliser |",
    )
    for question in ("Affirmations bloquantes non prouvées", "Contradictions détectées",
                     "Hypothèses acceptées temporairement", "Preuves à obtenir avant livraison"):
        text = text.replace(f"| {question} |  |", f"| {question} | aucune |")
    ledger.write_text(text, encoding="utf-8")


def _hand_over(root: Path, service: TaskService, tid: str, *, claims: bool) -> None:
    """Pack du ledger et trace de décision, puis ``running → needs_verification`` (gate compris)."""
    scaffold_task_artifacts(root, task_id=tid, profile_id="governed")
    if claims:
        _ready_claims(root, tid)
    evidence = EvidenceService(root / "_grimoire-runtime-output" / "evidence")
    item = EvidenceItem(id="e1", kind=EvidenceKind.TEST, uri="pytest://", digest="d1", summary=CRITERION)
    pack = evidence.create_pack(task_id=tid, profile=EvidenceProfile.STANDARD, items=[item], acceptance=(CRITERION,))
    evidence.verify(pack, acceptance=(CRITERION,))
    service.transition(tid, S.NEEDS_VERIFICATION, "claude")
    assert service.require(tid).status is S.NEEDS_VERIFICATION


def test_witness_a_held_task_still_blocks(governed: Path) -> None:
    _, tid = _working(governed)
    _, decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK and decision.detail["task_id"] == tid


def _stop_tags(root: Path) -> list[list[str]]:
    path = root / "_grimoire-output" / "traces" / "traces.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row["tags"] for row in rows if row["tags"][:1] == ["stop"]]


def test_the_trace_of_a_refused_stop_names_why(governed: Path) -> None:
    """D-12 : le rapport live lit le motif du refus au TraceLedger, pas dans le texte de l'agent."""
    service, tid = _working(governed)
    _hook(governed, HookEvent.STOP, stop_hook_active=False)  # tenue, artefacts absents
    scaffold_task_artifacts(governed, task_id=tid, profile_id="governed")
    _hook(governed, HookEvent.STOP, stop_hook_active=False)  # tenue, gates verts en cours, pas rendue
    _hand_over(governed, service, tid, claims=False)
    _hook(governed, HookEvent.STOP, stop_hook_active=False)  # rendue, claim ledger au gabarit
    _ready_claims(governed, tid)
    _hook(governed, HookEvent.STOP, stop_hook_active=False)  # rendue, gates verts
    missing, unparked, red, green = _stop_tags(governed)[-4:]
    assert missing[:3] == ["stop", "block", "refusal:gates_red"]
    assert unparked[:3] == ["stop", "block", "refusal:task_unparked"]
    assert red[:3] == ["stop", "block", "refusal:gates_red"]
    assert green[:2] == ["stop", "allow"] and not any(t.startswith("refusal:") for t in green)


def test_the_hand_over_hint_of_a_claimed_task_is_a_transition_the_ledger_accepts(governed: Path) -> None:
    """``claimed → needs_verification`` est refusé par le ledger : la consigne passe par ``running``."""
    service = TaskService(governed)
    tid = service.add("Travail", (CRITERION,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="host-claude-code-cli")
    service.attach_session(tid, SESSION, session_host="claude-code-cli", actor="claude")
    scaffold_task_artifacts(governed, task_id=tid, profile_id="governed")
    _ready_claims(governed, tid)
    _edit(governed)

    _, decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)

    assert decision.outcome is Outcome.BLOCK and decision.detail.get("task_state") == "claimed", decision.reason
    assert f"`grimoire task move {tid} --to running` puis `--to needs_verification`" in decision.reason


def test_a_task_handed_over_with_green_gates_lets_the_session_stop(governed: Path) -> None:
    service, tid = _working(governed)
    _hand_over(governed, service, tid, claims=True)

    rendered, decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)

    assert decision.outcome is Outcome.ALLOW, decision.reason  # sur la base : « Clôture hors tâche », refusée
    assert decision.detail["parked"] is True and decision.detail["task_id"] == tid
    assert decision.detail["task_state"] == "needs_verification" and decision.detail["ok"] is True
    assert tid in rendered["systemMessage"]


def test_a_task_handed_over_with_red_gates_is_refused_by_name_not_as_unattached_work(governed: Path) -> None:
    service, tid = _working(governed)
    _hand_over(governed, service, tid, claims=False)  # claim ledger resté au gabarit : gate rouge

    _, decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)

    assert decision.outcome is Outcome.BLOCK
    assert tid in decision.reason and "rendue pour vérification" in decision.reason, decision.reason
    assert "hors tâche" not in decision.reason
    assert decision.detail["task_id"] == tid and decision.detail["ok"] is False


def test_a_blocked_task_lets_the_session_stop(governed: Path) -> None:
    service, tid = _working(governed)
    service.transition(tid, S.BLOCKED, "claude", reason="dépendance amont cassée")

    _, decision = _hook(governed, HookEvent.STOP, stop_hook_active=False)

    assert decision.outcome is Outcome.ALLOW, decision.reason
    assert decision.detail["parked"] is True and decision.detail["task_state"] == "blocked"


def test_another_session_is_not_let_through_by_a_handed_over_task(governed: Path) -> None:
    service, tid = _working(governed)
    _hand_over(governed, service, tid, claims=True)
    _edit(governed, session="autre")
    _, decision = _hook(governed, HookEvent.STOP, session="autre", stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK and decision.detail["blocked_on"] == "no_active_task"


def _unattached(root: Path, session: str = "libre") -> None:
    _edit(root, session=session)


def test_the_no_task_reminder_is_not_repeated_without_new_writes(governed: Path) -> None:
    """Issue #1126 : le rappel « Clôture hors tâche » n'est rendu qu'une fois par palier d'écritures."""
    _unattached(governed)
    _, first = _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    assert first.outcome is Outcome.BLOCK and first.detail["blocked_on"] == "no_active_task"

    _, again = _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    assert again.outcome is Outcome.ALLOW, again.reason


def test_a_new_write_after_the_acknowledgement_is_refused_again(governed: Path) -> None:
    _unattached(governed)
    _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    _unattached(governed)
    _, decision = _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK and decision.detail["mutations"] == 2


# --- Compteur d'écritures acquittées : bornes du journal et de l'adaptateur (issue #1126) ---

NOW = "2026-11-06T00:00:00+00:00"


def _hook_input(root: Path, session: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(project_root=root, session_id=session)


def test_acknowledged_mutations_without_session_id_is_zero_and_writes_nothing(tmp_path: Path) -> None:
    from grimoire.policies.session_state import note_acknowledged_mutations, session_acknowledged_mutations

    note_acknowledged_mutations(tmp_path, "", 5, now_iso=NOW)
    assert session_acknowledged_mutations(tmp_path, "") == 0
    assert not any(tmp_path.rglob("*.json"))


def test_acknowledged_mutations_never_goes_back_down(tmp_path: Path) -> None:
    from grimoire.policies.session_state import (
        note_acknowledged_mutations,
        session_acknowledged_mutations,
        session_state_path,
    )

    note_acknowledged_mutations(tmp_path, "s", 3, now_iso=NOW)
    note_acknowledged_mutations(tmp_path, "s", 1, now_iso=NOW)
    note_acknowledged_mutations(tmp_path, "s", 3, now_iso=NOW)
    assert session_acknowledged_mutations(tmp_path, "s") == 3
    note_acknowledged_mutations(tmp_path, "s", 4, now_iso=NOW)
    assert session_acknowledged_mutations(tmp_path, "s") == 4
    assert session_state_path(tmp_path, "s").is_file()


@pytest.mark.parametrize("bad", ["7", True, None, [4]])
def test_invalid_acknowledged_mutations_in_the_journal_read_as_zero(tmp_path: Path, bad: Any) -> None:
    from grimoire.policies.session_state import session_acknowledged_mutations, session_state_path

    path = session_state_path(tmp_path, "s")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": "s", "acknowledged_mutations": bad}), encoding="utf-8")
    assert session_acknowledged_mutations(tmp_path, "s") == 0


def test_acknowledged_mutation_adapter_without_session_is_zero_and_silent(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.enrolment import acknowledge_mutations, acknowledged_mutation_count

    hook = _hook_input(tmp_path, "")
    acknowledge_mutations(hook, 4)
    assert acknowledged_mutation_count(hook) == 0
    assert not any(tmp_path.rglob("*.json"))


def test_acknowledged_mutation_adapter_swallows_journal_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.hosts.decisions.enrolment import acknowledge_mutations, acknowledged_mutation_count
    from grimoire.policies import session_state

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("journal illisible")

    monkeypatch.setattr(session_state, "session_acknowledged_mutations", boom)
    monkeypatch.setattr(session_state, "note_acknowledged_mutations", boom)
    hook = _hook_input(tmp_path, "s")
    assert acknowledged_mutation_count(hook) == 0
    acknowledge_mutations(hook, 2)  # ne lève pas


def test_acknowledged_mutation_adapter_round_trips(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.enrolment import acknowledge_mutations, acknowledged_mutation_count

    hook = _hook_input(tmp_path, "s")
    acknowledge_mutations(hook, 2)
    assert acknowledged_mutation_count(hook) == 2


def test_each_further_write_after_an_acknowledgement_brings_the_reminder_back(governed: Path) -> None:
    """Le gate n'est pas désarmé : rappel, silence, nouvelle écriture, rappel, silence."""
    _unattached(governed)
    _, first = _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    assert first.outcome is Outcome.BLOCK
    _, quiet = _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    assert quiet.outcome is Outcome.ALLOW

    _unattached(governed)
    _, back = _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    assert back.outcome is Outcome.BLOCK and back.detail["mutations"] == 2
    _, quiet_again = _hook(governed, HookEvent.STOP, session="libre", stop_hook_active=False)
    assert quiet_again.outcome is Outcome.ALLOW
