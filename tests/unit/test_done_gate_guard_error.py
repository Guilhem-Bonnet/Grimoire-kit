"""W1-06 : une panne du gate « fini » est tracée (``guard.error``), jamais un ALLOW muet ni un BLOCK inventé.

Les défauts relevés en relecture de la première version :

- le verdict fabriquait ``stale=True, blocked=True`` dans TOUS les profils
  (``orchestrated`` et ``governed`` en shadow compris), hors cooldown et plafond ;
- ``guard_error`` était écrit dans ``evidence-log.jsonl`` : ``has_observed_inventory``
  croyait alors qu'une action avait été observée ;
- aucun lecteur : rien dans ``grimoire verify``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_checks.evidence_journal import (
    GUARD_ERROR_EVENT,
    append_guard_event,
    evidence_log_relpath,
    guard_events_relpath,
    has_observed_inventory,
    read_guard_events,
)
from grimoire.core.standard_task_scaffold import scaffold_task_artifacts
from grimoire.hosts.decisions import HookInput, Outcome, decide_evidence_gate
from grimoire.hosts.events import HookEvent

_BOOM = "grimoire.hosts.decisions.done_gate.read_evidence_log"


def _project(root: Path, profile: str) -> Path:
    setup_standard_profile(root, profile_id=profile, task_id="bootstrap")
    import io

    from ruamel.yaml import YAML

    board = root / "_grimoire/standard/task-board.yaml"
    yaml = YAML()
    data = yaml.load(board.read_text(encoding="utf-8"))
    for task in data.get("tasks", []):
        if task.get("task_id") == "bootstrap":
            task["status"] = "in_progress"
    stream = io.StringIO()
    yaml.dump(data, stream)
    board.write_text(stream.getvalue(), encoding="utf-8")
    scaffold_task_artifacts(root, task_id="bootstrap")
    return root


def _stop(root: Path, session: str = "s-1") -> object:
    with patch(_BOOM, side_effect=RuntimeError("boom")):
        return decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id=session))


@pytest.fixture
def enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_DONE_GATE", "enforce")


@pytest.fixture
def shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_DONE_GATE", raising=False)


def test_orchestrated_never_blocks_on_a_crash_but_warns_and_traces(tmp_path: Path, shadow: None) -> None:
    root = _project(tmp_path, "orchestrated")
    for n in range(6):
        decision = _stop(root, f"s-{n}")
        assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert "non évaluable" in decision.context  # type: ignore[attr-defined]
    assert decision.detail["done_gate"]["stale"] is False  # type: ignore[attr-defined]
    events = read_guard_events(root, "bootstrap")
    assert len(events) == 6
    assert events[0]["type"] == GUARD_ERROR_EVENT == "guard.error"
    assert events[0]["guard_id"] == "done_gate"
    assert events[0]["error_type"] == "RuntimeError"


def test_governed_in_shadow_warns_only_and_is_not_capped_into_a_block(tmp_path: Path, shadow: None) -> None:
    root = _project(tmp_path, "governed")
    for _ in range(6):
        decision = _stop(root, "same-session")
        assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert "non évaluable" in decision.context  # type: ignore[attr-defined]
    assert decision.detail["done_gate"]["enforce"] is False  # type: ignore[attr-defined]


def test_governed_enforced_blocks_with_an_honest_reason_then_respects_the_cap(tmp_path: Path, enforce: None) -> None:
    root = _project(tmp_path, "governed")
    first = _stop(root, "s-cap")
    assert first.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert "mutation" not in first.reason  # type: ignore[attr-defined]
    assert "non évaluable" in first.reason  # type: ignore[attr-defined]
    assert "RuntimeError: boom" in first.reason  # type: ignore[attr-defined]
    assert "Relance ``" not in first.reason  # type: ignore[attr-defined]
    # Le cooldown de 60 s s'applique aussi à la panne : le second Stop passe.
    second = _stop(root, "s-cap")
    assert second.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert second.detail["done_gate"]["capped"] is True  # type: ignore[attr-defined]


def test_a_crash_is_not_journaled_as_a_policy_hold(tmp_path: Path, enforce: None) -> None:
    root = _project(tmp_path, "governed")
    with patch("grimoire.hosts.decisions.evidence_gate._record_done_gate_hold") as hold:
        _stop(root)
    hold.assert_not_called()


def test_a_guard_error_is_not_an_observed_action(tmp_path: Path, shadow: None) -> None:
    root = _project(tmp_path, "governed")
    assert not has_observed_inventory(root, "bootstrap")
    _stop(root)
    assert read_guard_events(root, "bootstrap")
    assert not has_observed_inventory(root, "bootstrap")
    assert not (root / evidence_log_relpath("bootstrap")).exists()


def test_verify_reports_a_recorded_guard_error_and_still_flags_the_empty_inventory(
    tmp_path: Path, shadow: None
) -> None:
    from grimoire.core.agentic_standard import check_evidence_gates, verify_standard_profile

    root = _project(tmp_path, "governed")
    _stop(root)
    result = verify_standard_profile(root, task_id="bootstrap")
    ids = {c.id for c in result.checks}
    assert "guard.error_recorded" in ids
    assert "evidence.inventory_placeholder" in ids
    gates = check_evidence_gates(root, task_id="bootstrap", target_state="review")
    assert "evidence.inventory_placeholder" in {c.id for c in gates.checks}


def test_an_unwritable_journal_does_not_break_the_hook(tmp_path: Path, shadow: None) -> None:
    root = _project(tmp_path, "governed")
    target = root / guard_events_relpath("bootstrap")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()  # un répertoire à la place du fichier : l'ouverture échoue (OSError)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert "non évaluable" in decision.context  # type: ignore[attr-defined]


def test_guard_events_round_trip_and_skip_corrupt_lines(tmp_path: Path) -> None:
    append_guard_event(tmp_path, "T-1", {"type": GUARD_ERROR_EVENT, "guard_id": "g"})
    path = tmp_path / guard_events_relpath("T-1")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{pas du json\n")
    append_guard_event(tmp_path, "T-1", {"type": GUARD_ERROR_EVENT, "guard_id": "h"})
    assert [e["guard_id"] for e in read_guard_events(tmp_path, "T-1")] == ["g", "h"]
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["guard_id"] == "g"
    assert read_guard_events(tmp_path, "absent") == []
