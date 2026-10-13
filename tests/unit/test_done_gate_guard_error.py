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
    # Six Stop identiques = une ligne (journal borné, W1-06 T1-04).
    assert len(events) == 1
    assert events[0]["type"] == GUARD_ERROR_EVENT == "guard.error"
    assert events[0]["guard_id"] == "done_gate"
    assert events[0]["error_type"] == "RuntimeError"
    assert events[0]["count"] == 6


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
    # La commande nommée existe : `grimoire verify` n'existe pas, `grimoire standard verify` oui.
    assert "grimoire standard verify --task-id bootstrap" in first.reason  # type: ignore[attr-defined]
    assert "`grimoire verify`" not in first.reason  # type: ignore[attr-defined]
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


def _resolve_with_profile_bytes(root: Path, raw: bytes | None) -> tuple[tuple[bool, bool, bool], list[dict]]:
    """Run the resolution against a REAL ``standard-profile.yaml`` (no patching)."""
    from grimoire.core.standard_generation import STANDARD_PROFILE_FILE
    from grimoire.hosts.decisions.done_gate import resolve_done_gate_block

    if raw is not None:
        (root / STANDARD_PROFILE_FILE).write_bytes(raw)
    result = resolve_done_gate_block(root, "bootstrap", "s-1", "governed", "2026-10-09T00:00:00+00:00")
    return result, read_guard_events(root, "bootstrap")


@pytest.mark.parametrize(
    ("raw", "error_type"),
    [
        pytest.param(b"options: [pas ferme\n  done_gate: : enforce\n", None, id="yaml-invalide"),
        pytest.param(b"options:\n  done_gate: enforc\xc3\n", "ReaderError", id="octet-utf8-coupe"),
        pytest.param(b"- une\n- liste\n", "TypeError", id="racine-non-mapping"),
    ],
)
def test_an_unreadable_enforce_option_is_traced_not_silently_shadow(
    tmp_path: Path, shadow: None, raw: bytes, error_type: str | None
) -> None:
    root = _project(tmp_path, "governed")
    result, events = _resolve_with_profile_bytes(root, raw)
    assert result == (False, False, False)
    assert [e["guard_id"] for e in events] == ["done_gate.enforce_option"]
    assert events[0]["type"] == GUARD_ERROR_EVENT
    if error_type is not None:
        assert events[0]["error_type"] == error_type
    else:
        assert events[0]["error_type"]  # le vrai type d'exception YAML, pas un type inventé


def test_a_valid_enforce_option_is_honoured_without_guard_event(tmp_path: Path, shadow: None) -> None:
    from grimoire.hosts.decisions.done_gate import _is_enforced

    root = _project(tmp_path, "governed")
    _resolve_with_profile_bytes(root, b"options:\n  done_gate: enforce\n")
    assert _is_enforced(root, "bootstrap") is True
    assert read_guard_events(root, "bootstrap") == []


def test_a_profile_without_the_option_or_empty_leaves_no_guard_event(tmp_path: Path, shadow: None) -> None:
    root = _project(tmp_path, "governed")
    _, events = _resolve_with_profile_bytes(root, b"")
    assert events == []
    _, events = _resolve_with_profile_bytes(root, b"options:\n  autre: 1\n")
    assert events == []


def test_a_readable_enforce_option_leaves_no_guard_event(tmp_path: Path, shadow: None) -> None:
    from grimoire.hosts.decisions.done_gate import resolve_done_gate_block

    root = _project(tmp_path, "governed")
    resolve_done_gate_block(root, "bootstrap", "s-1", "governed", "2026-10-09T00:00:00+00:00")
    assert read_guard_events(root, "bootstrap") == []


def test_guard_error_message_is_truncated(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import record_guard_error

    record_guard_error(tmp_path, "T-1", "g", "ValueError", "x" * 5000)
    line = (tmp_path / guard_events_relpath("T-1")).read_text(encoding="utf-8")
    assert len(line) < 600
    message = read_guard_events(tmp_path, "T-1")[0]["error_message"]
    assert message.startswith("x" * 240)
    assert message.endswith("(tronqué)")
    assert len(message) < 260


def test_identical_consecutive_guard_errors_are_written_once_and_distinct_ones_are_kept(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import record_guard_error

    for _ in range(5):
        record_guard_error(tmp_path, "T-1", "g", "ValueError", "boom")
    record_guard_error(tmp_path, "T-1", "g", "ValueError", "autre")
    record_guard_error(tmp_path, "T-1", "g", "ValueError", "boom")
    assert [e["error_message"] for e in read_guard_events(tmp_path, "T-1")] == ["boom", "autre", "boom"]


def test_the_guard_journal_stops_growing_at_its_cap(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import GUARD_EVENTS_MAX, record_guard_error

    for n in range(GUARD_EVENTS_MAX + 20):
        record_guard_error(tmp_path, "T-1", "g", "ValueError", f"boom {n}")
    assert len(read_guard_events(tmp_path, "T-1")) == GUARD_EVENTS_MAX


def test_verify_message_names_a_real_remedy(tmp_path: Path, shadow: None) -> None:
    from grimoire.core.agentic_standard import verify_standard_profile

    root = _project(tmp_path, "governed")
    _stop(root)
    check = next(c for c in verify_standard_profile(root, task_id="bootstrap").checks if c.id == "guard.error_recorded")
    assert "grimoire standard gate check --task-id bootstrap" in check.message
    assert "guard-events.jsonl" in check.message


# --- seconde revue W1-06 : T2-01 (résolution du refus) et T2-02 (compteur, rotation) ---

_NOW = "2026-10-09T00:00:00+00:00"
_RESOLVE = "grimoire.hosts.decisions.done_gate.resolve_done_gate_block"


def _corrupt_caps(root: Path) -> None:
    state = root / evidence_log_relpath("bootstrap").parent / "done-gate-state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"session_blocks": {"s-1": "x"}}), encoding="utf-8")


def test_a_corrupt_session_count_neither_crashes_nor_silences_the_refusal(tmp_path: Path, enforce: None) -> None:
    from grimoire.hosts.decisions.done_gate import resolve_done_gate_block

    root = _project(tmp_path, "governed")
    _corrupt_caps(root)
    assert resolve_done_gate_block(root, "bootstrap", "s-1", "governed", _NOW) == (True, True, False)
    events = read_guard_events(root, "bootstrap")
    assert [e["guard_id"] for e in events] == ["done_gate.caps_state"]
    # L'état est réécrit sain : la corruption n'est tracée qu'une fois.
    resolve_done_gate_block(root, "bootstrap", "s-1", "governed", "2026-10-09T01:00:00+00:00")
    assert len(read_guard_events(root, "bootstrap")) == 1


def test_a_crashed_gate_with_a_corrupt_cap_state_still_blocks_in_enforced_governed(
    tmp_path: Path, enforce: None
) -> None:
    root = _project(tmp_path, "governed")
    _corrupt_caps(root)
    decision = _stop(root, "s-1")
    assert decision.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert decision.detail["done_gate"]["enforce"] is True  # type: ignore[attr-defined]


def test_a_resolution_crash_is_traced_and_fails_closed_in_enforced_governed(tmp_path: Path, enforce: None) -> None:
    root = _project(tmp_path, "governed")
    with patch(_RESOLVE, side_effect=ValueError("état illisible")):
        decision = _stop(root, "s-1")
    assert decision.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert decision.detail["done_gate"]["enforce"] is True  # type: ignore[attr-defined]
    guards = [e["guard_id"] for e in read_guard_events(root, "bootstrap")]
    assert "done_gate.resolution" in guards


def test_a_resolution_crash_stays_non_blocking_without_the_opt_in_or_a_blocking_profile(
    tmp_path: Path, shadow: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, "governed")
    with patch(_RESOLVE, side_effect=ValueError("état illisible")):
        decision = _stop(root, "s-1")
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert decision.detail["done_gate"]["enforce"] is False  # type: ignore[attr-defined]
    assert "done_gate.resolution" in [e["guard_id"] for e in read_guard_events(root, "bootstrap")]
    monkeypatch.setenv("GRIMOIRE_DONE_GATE", "enforce")
    other = _project(tmp_path / "orch", "orchestrated")
    with patch(_RESOLVE, side_effect=ValueError("état illisible")):
        decision = _stop(other, "s-1")
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert decision.detail["done_gate"]["enforce"] is True  # type: ignore[attr-defined]


def test_repeated_identical_guard_errors_are_counted_not_swallowed(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import describe_guard_errors, record_guard_error

    for _ in range(5):
        record_guard_error(tmp_path, "T-1", "g", "ValueError", "boom")
    events = read_guard_events(tmp_path, "T-1")
    assert len(events) == 1
    assert events[0]["count"] == 5
    assert events[0]["last_ts"] >= events[0]["ts"]
    assert describe_guard_errors(tmp_path, "T-1").startswith("5 panne(s)")
    record_guard_error(tmp_path, "T-1", "g", "ValueError", "autre")
    assert describe_guard_errors(tmp_path, "T-1").startswith("6 panne(s)")


def test_a_full_guard_journal_rotates_and_keeps_the_latest_failure(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import (
        GUARD_EVENTS_MAX,
        describe_guard_errors,
        record_guard_error,
    )

    for n in range(GUARD_EVENTS_MAX + 5):
        record_guard_error(tmp_path, "T-1", "g", "ValueError", f"boom {n}")
    record_guard_error(tmp_path, "T-1", "autre", "KeyError", "dernier")
    events = read_guard_events(tmp_path, "T-1")
    assert len(events) == GUARD_EVENTS_MAX
    assert events[-1]["guard_id"] == "autre"
    assert events[0]["error_message"] == "boom 6"
    message = describe_guard_errors(tmp_path, "T-1")
    assert "« autre »" in message
    assert "écartées" in message


# --- troisième relecture W1-06 : T3-01 (octets illisibles), T3-02 (boucle après panne), dédoublonnage ---

_CUT_MULTIBYTE = b'{"type": "guard.error", "guard_id": "g", "error_message": "caf\xc3'  # coupé dans « é »


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_a_guard_journal_cut_inside_a_multibyte_character_keeps_the_in_progress_block(
    tmp_path: Path, enforce: None
) -> None:
    root = _project(tmp_path, "governed")
    _write_bytes(root / guard_events_relpath("bootstrap"), _CUT_MULTIBYTE)
    decision = _stop(root, "s-1")  # ne doit pas lever : un ALLOW silencieux vaudrait un fail-open
    assert decision.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert decision.detail["done_gate"]["reason"] == "error:RuntimeError"  # type: ignore[attr-defined]
    assert [e["guard_id"] for e in read_guard_events(root, "bootstrap")] == ["done_gate"]


def test_an_evidence_log_cut_inside_a_multibyte_character_is_read_line_by_line(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import read_evidence_log

    good = b'{"type": "bash", "command": "ls"}\n'
    _write_bytes(tmp_path / evidence_log_relpath("T-1"), good + b'{"type": "bash", "command": "caf\xc3\n' + good)
    assert [e["command"] for e in read_evidence_log(tmp_path, "T-1")] == ["ls", "ls"]
    assert has_observed_inventory(tmp_path, "T-1")


def test_verify_and_gate_check_survive_an_unreadable_guard_journal(tmp_path: Path, shadow: None) -> None:
    from grimoire.core.agentic_standard import check_evidence_gates, verify_standard_profile

    root = _project(tmp_path, "governed")
    _write_bytes(root / guard_events_relpath("bootstrap"), _CUT_MULTIBYTE)
    _write_bytes(root / evidence_log_relpath("bootstrap"), _CUT_MULTIBYTE)
    verify_standard_profile(root, task_id="bootstrap")
    check_evidence_gates(root, task_id="bootstrap", target_state="review")


def test_the_best_effort_writers_never_raise(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import append_evidence_event, record_guard_error

    _write_bytes(tmp_path / guard_events_relpath("T-1"), _CUT_MULTIBYTE)
    record_guard_error(tmp_path, "T-1", "g", "ValueError", "café")
    record_guard_error(tmp_path, "T-2", "g", "ValueError", "lone surrogate \ud800")  # non encodable en UTF-8
    append_evidence_event(tmp_path, "T-2", {"type": "bash", "command": "echo \ud800"})


def test_an_unreadable_caps_state_is_treated_as_empty(tmp_path: Path, enforce: None) -> None:
    from grimoire.hosts.decisions.done_gate import resolve_done_gate_block

    root = _project(tmp_path, "governed")
    _write_bytes(
        root / evidence_log_relpath("bootstrap").parent / "done-gate-state.json", b'{"last_block_ts": "caf\xc3'
    )
    assert resolve_done_gate_block(root, "bootstrap", "s-1", "governed", _NOW) == (True, True, False)


def test_a_naive_timestamp_in_the_caps_state_counts_as_an_expired_cooldown(tmp_path: Path, enforce: None) -> None:
    from grimoire.hosts.decisions.done_gate import resolve_done_gate_block

    root = _project(tmp_path, "governed")
    state = root / evidence_log_relpath("bootstrap").parent / "done-gate-state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"last_block_ts": "2026-10-09T00:00:00", "session_blocks": {"s-1": 1}}), encoding="utf-8"
    )
    assert resolve_done_gate_block(root, "bootstrap", "s-1", "governed", _NOW) == (True, True, False)
    assert read_guard_events(root, "bootstrap") == []
    # L'état est réécrit avec un horodatage valide : le refroidissement s'applique de nouveau.
    assert resolve_done_gate_block(root, "bootstrap", "s-1", "governed", _NOW) == (True, False, True)


def test_a_resolution_crash_blocks_once_per_session_not_on_every_stop(tmp_path: Path, enforce: None) -> None:
    root = _project(tmp_path, "governed")
    with patch(_RESOLVE, side_effect=ValueError("état illisible")):
        first = _stop(root, "s-1")
        again = [_stop(root, "s-1") for _ in range(4)]
        other = _stop(root, "s-2")
        other_again = _stop(root, "s-2")
    assert first.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert [d.outcome for d in again] == [Outcome.ALLOW] * 4  # type: ignore[attr-defined]
    assert all(d.detail["done_gate"]["capped"] is True for d in again)  # type: ignore[attr-defined]
    assert other.outcome is Outcome.BLOCK  # type: ignore[attr-defined]  # une autre session est prévenue à son tour
    assert other_again.outcome is Outcome.ALLOW  # type: ignore[attr-defined]


def test_the_reason_after_a_resolution_crash_names_the_corrupted_file_and_the_remedy(
    tmp_path: Path, enforce: None
) -> None:
    root = _project(tmp_path, "governed")
    with patch(_RESOLVE, side_effect=ValueError("état illisible")):
        decision = _stop(root, "s-1")
    reason = decision.reason  # type: ignore[attr-defined]
    expected = (evidence_log_relpath("bootstrap").parent / "done-gate-state.json").as_posix()
    assert expected in reason
    assert "supprime" in reason


def test_alternating_guard_errors_are_deduplicated_per_guard(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import describe_guard_errors, record_guard_error

    for _ in range(5):
        record_guard_error(tmp_path, "T-1", "done_gate", "TypeError", "naive")
        record_guard_error(tmp_path, "T-1", "done_gate.resolution", "TypeError", "naive")
    events = read_guard_events(tmp_path, "T-1")
    assert [(e["guard_id"], e.get("count", 1)) for e in events] == [("done_gate", 5), ("done_gate.resolution", 5)]
    # La dernière ligne reste la panne la plus récente.
    record_guard_error(tmp_path, "T-1", "done_gate", "TypeError", "naive")
    events = read_guard_events(tmp_path, "T-1")
    assert [(e["guard_id"], e["count"]) for e in events] == [("done_gate.resolution", 5), ("done_gate", 6)]
    assert describe_guard_errors(tmp_path, "T-1").startswith("11 panne(s)")


def test_an_event_appended_after_a_cut_line_is_not_glued_to_it(tmp_path: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import (
        append_evidence_event,
        read_evidence_log,
        record_guard_error,
    )

    _write_bytes(tmp_path / guard_events_relpath("T-1"), _CUT_MULTIBYTE)
    record_guard_error(tmp_path, "T-1", "g", "ValueError", "après la coupure")
    assert [e["error_message"] for e in read_guard_events(tmp_path, "T-1")] == ["après la coupure"]
    _write_bytes(tmp_path / evidence_log_relpath("T-1"), b'{"type": "bash", "command": "caf\xc3')
    append_evidence_event(tmp_path, "T-1", {"type": "bash", "command": "ls"})
    assert [e["command"] for e in read_evidence_log(tmp_path, "T-1")] == ["ls"]
