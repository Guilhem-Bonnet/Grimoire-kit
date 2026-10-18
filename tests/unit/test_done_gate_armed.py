"""W2-09 : le gate « fini » est armable sans variable, à une date, et son mode est tracé partout.

Avant : ``done_gate`` ne bloquait que si ``GRIMOIRE_DONE_GATE=enforce`` (ou l'option de
profil ``done_gate: enforce``) était posé à la main. Un projet gouverné n'avait donc aucun
moyen d'être armé « par défaut », et les verdicts ``no_mutation`` /
``green_check_after_mutation`` rendaient ``enforce: false`` même sous ``enforce``, sans dire
sous quel mode ils avaient été produits.

Maintenant : un mode (``shadow`` | ``enforce``) et sa source sont résolus une fois
(``resolve_done_gate_mode``) et portés par chaque verdict ; une date d'armement (option de
projet ``done_gate_enforce_from`` ou défaut du kit ``GOVERNED_ENFORCE_FROM``) arme un profil
bloquant sans variable. Le défaut livré reste shadow (décision 3 du plan).
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_bash_event,
    build_file_write_event,
    read_guard_events,
)
from grimoire.core.standard_task_scaffold import scaffold_task_artifacts
from grimoire.hosts.decisions import HookInput, Outcome, decide_evidence_gate
from grimoire.hosts.decisions import done_gate as dg
from grimoire.hosts.events import HookEvent

TASK = "bootstrap"
_PROFILE = "_grimoire/standard/standard-profile.yaml"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_DONE_GATE", raising=False)


def _project(root: Path, profile: str = "governed", *, options: str = "") -> Path:
    """Projet enrôlé, tâche en cours, gates de preuve verts, mutation après le dernier check vert."""
    setup_standard_profile(root, profile_id=profile, task_id=TASK)
    board = root / "_grimoire/standard/task-board.yaml"
    yaml = YAML()
    data = yaml.load(board.read_text(encoding="utf-8"))
    for task in data.get("tasks", []):
        if task.get("task_id") == TASK:
            task["status"] = "in_progress"
    stream = io.StringIO()
    yaml.dump(data, stream)
    board.write_text(stream.getvalue(), encoding="utf-8")
    scaffold_task_artifacts(root, task_id=TASK)
    if options:
        path = root / _PROFILE
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + "\noptions:\n" + options, encoding="utf-8")
    return root


def _stale(root: Path) -> None:
    append_evidence_event(root, TASK, build_bash_event("pytest -q", exit_code=0))
    append_evidence_event(root, TASK, build_file_write_event(str(root / "src" / "foo.py")))


def _stop(root: Path, session: str = "s-1") -> object:
    return decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id=session))


def _verdict(decision: object) -> dict:  # type: ignore[type-arg]
    return decision.detail["done_gate"]  # type: ignore[attr-defined,no-any-return]


# --- armer sans variable -------------------------------------------------------------


def test_a_governed_project_armed_by_date_blocks_a_stale_mutation_without_any_variable(tmp_path: Path) -> None:
    root = _project(tmp_path, options="  done_gate_enforce_from: '2026-01-01'\n")
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert "postérieure au dernier check vert" in decision.reason  # type: ignore[attr-defined]
    assert "armé depuis le 2026-01-01" in decision.reason  # type: ignore[attr-defined]
    verdict = _verdict(decision)
    assert (verdict["mode"], verdict["mode_source"], verdict["armed_from"]) == ("enforce", "option_dated", "2026-01-01")
    assert verdict["blocked"] is True


def test_an_unquoted_yaml_date_arms_the_gate_too(tmp_path: Path) -> None:
    root = _project(tmp_path, options="  done_gate_enforce_from: 2026-01-01\n")
    _stale(root)
    assert _stop(root).outcome is Outcome.BLOCK  # type: ignore[attr-defined]


def test_the_kit_default_date_arms_every_governed_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dg, "GOVERNED_ENFORCE_FROM", date(2026, 1, 1))
    root = _project(tmp_path)
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert _verdict(decision)["mode_source"] == "kit_dated"


def test_the_project_date_wins_over_the_kit_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dg, "GOVERNED_ENFORCE_FROM", date(2026, 1, 1))
    root = _project(tmp_path, options="  done_gate_enforce_from: '2999-01-01'\n")
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert _verdict(decision)["mode"] == "shadow"


def test_the_shipped_default_is_shadow_and_says_so(tmp_path: Path) -> None:
    """Décision 3 : cette release livre le mécanisme et la mesure, pas l'armement."""
    assert dg.GOVERNED_ENFORCE_FROM is None
    root = _project(tmp_path)
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    verdict = _verdict(decision)
    assert (verdict["stale"], verdict["blocked"]) == (True, False)
    assert (verdict["mode"], verdict["mode_source"]) == ("shadow", "default")


def test_before_the_date_the_gate_stays_in_shadow_and_names_the_pending_date(tmp_path: Path) -> None:
    root = _project(tmp_path, options="  done_gate_enforce_from: '2999-01-01'\n")
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert "sera armé le 2999-01-01" in decision.context  # type: ignore[attr-defined]
    verdict = _verdict(decision)
    assert (verdict["mode"], verdict["armed_from"]) == ("shadow", "2999-01-01")


def test_the_date_is_judged_on_the_clock_of_the_call(tmp_path: Path) -> None:
    root = _project(tmp_path, options="  done_gate_enforce_from: '2026-10-20'\n")
    _stale(root)
    hook = HookInput(event=HookEvent.STOP, project_root=root, session_id="s-1")
    assert dg.evaluate_done_gate(hook, TASK, "governed", now_iso="2026-10-19T23:59:59+00:00").mode == "shadow"
    assert dg.evaluate_done_gate(hook, TASK, "governed", now_iso="2026-10-20T00:00:00+00:00").mode == "enforce"


def test_a_non_blocking_profile_is_never_armed_by_a_date(tmp_path: Path) -> None:
    root = _project(tmp_path, "orchestrated", options="  done_gate_enforce_from: '2026-01-01'\n")
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    verdict = _verdict(decision)
    assert (verdict["mode"], verdict["blocked"]) == ("shadow", False)


# --- plafond et retour en observation -----------------------------------------------------


def test_the_armed_gate_refuses_at_most_three_times_per_session(tmp_path: Path) -> None:
    root = _project(tmp_path, options="  done_gate_enforce_from: '2026-01-01'\n")
    _stale(root)
    hook = HookInput(event=HookEvent.STOP, project_root=root, session_id="s-cap")
    stamps = [
        "2026-10-19T00:00:00+00:00",
        "2026-10-19T00:02:00+00:00",
        "2026-10-19T00:04:00+00:00",
        "2026-10-19T00:06:00+00:00",
    ]
    verdicts = [dg.evaluate_done_gate(hook, TASK, "governed", now_iso=stamp) for stamp in stamps]
    assert [v.blocked for v in verdicts] == [True, True, True, False]
    assert verdicts[3].capped is True


def test_an_explicit_shadow_in_the_environment_beats_the_arming_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIMOIRE_DONE_GATE", "shadow")
    root = _project(tmp_path, options="  done_gate_enforce_from: '2026-01-01'\n")
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert (_verdict(decision)["mode"], _verdict(decision)["mode_source"]) == ("shadow", "env")


def test_an_explicit_shadow_option_beats_the_arming_date(tmp_path: Path) -> None:
    root = _project(tmp_path, options="  done_gate: shadow\n  done_gate_enforce_from: '2026-01-01'\n")
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert (_verdict(decision)["mode"], _verdict(decision)["mode_source"]) == ("shadow", "option")


def test_the_explicit_enforce_switches_keep_their_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, options="  done_gate: enforce\n")
    assert dg.resolve_done_gate_mode(root, TASK, "governed") == dg.DoneGateMode("enforce", "option")
    monkeypatch.setenv("GRIMOIRE_DONE_GATE", "enforce")
    assert dg.resolve_done_gate_mode(root, TASK, "governed") == dg.DoneGateMode("enforce", "env")


# --- mode tracé pour chaque décision ---------------------------------------------------------


def test_every_verdict_carries_the_mode_it_was_produced_under(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``no_mutation`` et ``green_check_after_mutation`` aussi : sous enforce, ils le disent."""
    monkeypatch.setenv("GRIMOIRE_DONE_GATE", "enforce")
    quiet = _project(tmp_path / "quiet")
    verdict = _verdict(_stop(quiet))
    assert (verdict["reason"], verdict["enforce"], verdict["mode"], verdict["mode_source"]) == (
        "no_mutation",
        True,
        "enforce",
        "env",
    )
    green = _project(tmp_path / "green")
    append_evidence_event(green, TASK, build_file_write_event(str(green / "src" / "foo.py")))
    append_evidence_event(green, TASK, build_bash_event("pytest -q", exit_code=0))
    verdict = _verdict(_stop(green))
    assert (verdict["reason"], verdict["enforce"], verdict["mode"]) == ("green_check_after_mutation", True, "enforce")


def test_a_crashed_evaluation_still_names_its_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dg, "read_evidence_log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    root = _project(tmp_path, options="  done_gate_enforce_from: '2026-01-01'\n")
    verdict = _verdict(_stop(root))
    assert verdict["reason"] == "error:RuntimeError"
    assert verdict["mode"] in ("shadow", "enforce")
    assert verdict["mode_source"]


# --- date illisible : tracée, jamais un refus inventé ---------------------------------------------


@pytest.mark.parametrize("raw", ["'demain'", "'2026-13-45'", "42", "[2026, 1, 1]"])
def test_an_unreadable_arming_date_stays_shadow_and_is_traced(tmp_path: Path, raw: str) -> None:
    root = _project(tmp_path, options=f"  done_gate_enforce_from: {raw}\n")
    _stale(root)
    decision = _stop(root)
    assert decision.outcome is Outcome.ALLOW  # type: ignore[attr-defined]
    assert _verdict(decision)["mode"] == "shadow"
    events = read_guard_events(root, TASK)
    assert [e["guard_id"] for e in events] == ["done_gate.enforce_option"]


# --- le refus maintenu (W2-02) suit le même interrupteur ----------------------------------------


def test_the_continued_stop_chain_follows_the_arming_date(tmp_path: Path) -> None:
    root = _project(tmp_path, options="  done_gate_enforce_from: '2026-01-01'\n")
    (root / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)  # gate rouge
    first = _stop(root)
    assert first.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    append_evidence_event(root, TASK, build_file_write_event("/outside/progress-1.py"))
    again = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=root, session_id="s-1", stop_active=True))
    assert again.outcome is Outcome.BLOCK  # type: ignore[attr-defined]
    assert "shadow" not in again.detail["stop_signature"]  # type: ignore[attr-defined]
