"""Tests for :mod:`grimoire.hosts.decisions.repetition_guard` (issue #668).

Rouge-avant : chaque test ci-dessous échoue avant le module (import
inexistant) ; vert après. Le design retenu (revue à plusieurs voix,
``_scratch/party-oss/pilotage.md``, idée A) lit ``pi-warden/src/stuck.ts``
(MIT) pour la seule notion reprise ici : une répétition exacte d'échec dans
une fenêtre glissante — jamais le juge, jamais ``churn``/répétition de
succès.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from grimoire.hosts.decisions._shared import HookInput, Outcome
from grimoire.hosts.decisions.evidence_trace import decide_evidence_trace
from grimoire.hosts.decisions.repetition_guard import (
    REPEAT_THRESHOLD,
    WINDOW_SIZE,
    RepetitionState,
    evaluate_and_record,
    load_repetition_state,
    repetition_path,
    save_repetition_state,
)
from grimoire.hosts.events import HookEvent

SESSION = "sess-rep-1"


def _hook(project_root: Path, *, command: str, exit_code: int | None, session_id: str = SESSION) -> HookInput:
    tool_response: dict[str, object] = {}
    if exit_code is not None:
        tool_response["exit_code"] = exit_code
    return HookInput(
        event=HookEvent.POST_TOOL_USE,
        project_root=project_root,
        tool_name="Bash",
        tool_input={"command": command},
        tool_response=tool_response,
        session_id=session_id,
    )


# ── 3 échecs identiques → looping ────────────────────────────────────────────


def test_three_identical_failures_trigger_looping(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts = classify_tool("Bash", {"command": "pytest -q"})
    verdict = None
    for _ in range(3):
        verdict = evaluate_and_record(_hook(tmp_path, command="pytest -q", exit_code=1), facts)
    assert verdict is not None
    assert verdict.looping is True
    assert verdict.repeats == REPEAT_THRESHOLD


def test_two_identical_failures_do_not_yet_trigger_looping(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts = classify_tool("Bash", {"command": "pytest -q"})
    verdict = None
    for _ in range(2):
        verdict = evaluate_and_record(_hook(tmp_path, command="pytest -q", exit_code=1), facts)
    assert verdict is not None
    assert verdict.looping is False


# ── 3 succès identiques → rien ───────────────────────────────────────────────


def test_three_identical_successes_never_trigger_looping(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts = classify_tool("Bash", {"command": "git status"})
    verdict = None
    for _ in range(5):
        verdict = evaluate_and_record(_hook(tmp_path, command="git status", exit_code=0), facts)
    assert verdict is not None
    assert verdict.looping is False


# ── Sorties différentes → rien ───────────────────────────────────────────────


def test_different_outputs_never_trigger_looping(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts = classify_tool("Bash", {"command": "pytest -q"})
    verdict = None
    for i in range(4):
        hook = HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=tmp_path,
            tool_name="Bash",
            tool_input={"command": "pytest -q"},
            tool_response={"exit_code": 1, "content": f"AssertionError: case {i} diverges"},
            session_id=SESSION,
        )
        verdict = evaluate_and_record(hook, facts)
    assert verdict is not None
    assert verdict.looping is False


# ── Horodatage différent dans la sortie → toujours détecté ──────────────────


def test_a_different_timestamp_in_the_output_is_still_detected(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts = classify_tool("Bash", {"command": "pytest -q"})
    verdict = None
    for i in range(3):
        hook = HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=tmp_path,
            tool_name="Bash",
            tool_input={"command": "pytest -q"},
            tool_response={
                "exit_code": 1,
                "content": f"FAILED test_x.py — took {i + 10}.{i}ms at 2026-09-2{i}T10:0{i}:00Z (pid {12000 + i})",
            },
            session_id=SESSION,
        )
        verdict = evaluate_and_record(hook, facts)
    assert verdict is not None
    assert verdict.looping is True, "la normalisation doit masquer durée/horodatage/pid"


# ── Fenêtre glissante ─────────────────────────────────────────────────────────


def test_a_repeat_that_rolled_out_of_the_window_no_longer_counts(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    fail_facts = classify_tool("Bash", {"command": "pytest -q"})
    # Deux échecs identiques...
    for _ in range(2):
        evaluate_and_record(_hook(tmp_path, command="pytest -q", exit_code=1), fail_facts)
    # ...puis assez d'appels différents pour les faire sortir de la fenêtre de 12.
    filler_facts = classify_tool("Bash", {"command": "ls"})
    verdict = None
    for i in range(WINDOW_SIZE):
        hook = HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=tmp_path,
            tool_name="Bash",
            tool_input={"command": f"ls dir-{i}"},
            tool_response={"exit_code": 0},
            session_id=SESSION,
        )
        verdict = evaluate_and_record(hook, filler_facts)
    assert verdict is not None
    assert verdict.looping is False
    # Un 3e échec identique au tout début ne doit plus voir les deux premiers.
    verdict = evaluate_and_record(_hook(tmp_path, command="pytest -q", exit_code=1), fail_facts)
    assert verdict is not None
    assert verdict.repeats == 1
    assert verdict.looping is False

    state = load_repetition_state(tmp_path, SESSION)
    assert len(state.entries) == WINDOW_SIZE


# ── Contexte injecté une seule fois par série ────────────────────────────────


def test_the_nudge_fires_once_per_series(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts = classify_tool("Bash", {"command": "pytest -q"})
    verdicts = [evaluate_and_record(_hook(tmp_path, command="pytest -q", exit_code=1), facts) for _ in range(5)]
    assert verdicts[0] is not None and verdicts[1] is not None
    assert verdicts[0].nudge == "" and verdicts[1].nudge == ""
    assert verdicts[2] is not None and verdicts[2].nudge != "", "3e répétition : le nudge doit s'armer"
    assert verdicts[3] is not None and verdicts[3].nudge == "", "4e répétition : même série, pas de second nudge"
    assert verdicts[4] is not None and verdicts[4].nudge == ""


def test_a_new_distinct_failing_series_can_nudge_again(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts_a = classify_tool("Bash", {"command": "pytest -q"})
    facts_b = classify_tool("Bash", {"command": "ruff check ."})
    for _ in range(3):
        evaluate_and_record(_hook(tmp_path, command="pytest -q", exit_code=1), facts_a)
    verdicts_b = [
        evaluate_and_record(_hook(tmp_path, command="ruff check .", exit_code=1), facts_b) for _ in range(3)
    ]
    assert verdicts_b[-1] is not None
    assert verdicts_b[-1].looping is True
    assert verdicts_b[-1].nudge != "", "une nouvelle série distincte doit pouvoir nudger de nouveau"


# ── Fichier d'état corrompu → pas d'exception ────────────────────────────────


def test_a_corrupted_state_file_never_raises(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    path = repetition_path(tmp_path, SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json at all", encoding="utf-8")

    facts = classify_tool("Bash", {"command": "pytest -q"})
    verdict = evaluate_and_record(_hook(tmp_path, command="pytest -q", exit_code=1), facts)
    assert verdict is not None
    assert verdict.looping is False  # état neuf : une seule entrée, pas encore de répétition

    state = load_repetition_state(tmp_path, SESSION)
    assert len(state.entries) == 1


def test_load_repetition_state_degrades_on_a_missing_file(tmp_path: Path) -> None:
    state = load_repetition_state(tmp_path, "sess-absent")
    assert state.entries == []
    assert state.session_id == "sess-absent"


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    state = RepetitionState.new("sess-rt")
    state.entries.append({"tool": "Bash", "call_hash": "a", "output_hash": "b", "failed": True, "ts": "x"})
    save_repetition_state(tmp_path, state)
    reloaded = load_repetition_state(tmp_path, "sess-rt")
    assert reloaded.entries == state.entries


def test_no_session_id_returns_none_without_writing_anything(tmp_path: Path) -> None:
    from grimoire.hosts.decisions.tool_facts import classify_tool

    facts = classify_tool("Bash", {"command": "pytest -q"})
    hook = HookInput(
        event=HookEvent.POST_TOOL_USE,
        project_root=tmp_path,
        tool_name="Bash",
        tool_input={"command": "pytest -q"},
        tool_response={"exit_code": 1},
        session_id="",
    )
    verdict = evaluate_and_record(hook, facts)
    assert verdict is None
    assert not (tmp_path / "_grimoire-output" / ".runs").exists()


# ── Intégration : decide_evidence_trace attache le verdict et le nudge ──────


def test_decide_evidence_trace_attaches_the_repetition_verdict(tmp_path: Path) -> None:
    session_id = "sess-integ-1"
    decision = None
    for _ in range(3):
        decision = decide_evidence_trace(
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=tmp_path,
                tool_name="Bash",
                tool_input={"command": "pytest -q"},
                tool_response={"exit_code": 1},
                session_id=session_id,
            )
        )
    assert decision is not None
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["repetition"] == {"looping": True, "repeats": 3}
    assert "même appel, même échec, 3 fois" in decision.context
    assert "change d'approche ou demande de l'aide" in decision.context


def test_decide_evidence_trace_never_blocks_on_repetition(tmp_path: Path) -> None:
    session_id = "sess-integ-2"
    decision = None
    for _ in range(6):
        decision = decide_evidence_trace(
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=tmp_path,
                tool_name="Bash",
                tool_input={"command": "pytest -q"},
                tool_response={"exit_code": 1},
                session_id=session_id,
            )
        )
    assert decision is not None
    assert decision.outcome is Outcome.ALLOW  # jamais un DENY/BLOCK, quel que soit le nombre de répétitions


def test_decide_evidence_trace_repetition_never_breaks_on_a_corrupted_state_file(tmp_path: Path) -> None:
    session_id = "sess-integ-3"
    path = repetition_path(tmp_path, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    decision = decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=tmp_path,
            tool_name="Bash",
            tool_input={"command": "pytest -q"},
            tool_response={"exit_code": 1},
            session_id=session_id,
        )
    )
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["repetition"] == {"looping": False, "repeats": 1}


# ── Budget : PostToolUse réel ─────────────────────────────────────────────────


def test_post_tool_use_budget_with_the_repetition_guard(tmp_path: Path) -> None:
    """Médiane sur 5 exécutions du hook ``PostToolUse`` réel (point d'entrée
    ``grimoire-hook``), fenêtre de répétition déjà remplie. Rapportée dans la
    PR — pas un import lourd (voir le docstring du module) ; plafond large de
    non-régression, pas la cible d'un budget serré (même esprit que
    ``tests/security/test_injections.py::test_pre_tool_use_budget_with_a_filled_memory``).
    """
    from grimoire.core.agentic_standard import setup_standard_profile

    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    session_id = "sess-budget-rep"
    state = RepetitionState.new(session_id)
    for i in range(WINDOW_SIZE):
        state.entries.append(
            {"tool": "Bash", "call_hash": f"h{i}", "output_hash": f"o{i}", "failed": False, "ts": ""}
        )
    save_repetition_state(tmp_path, state)

    src_root = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src_root)}
    payload = json.dumps(
        {
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {"exit_code": 1},
        }
    )

    durations_ms: list[float] = []
    last_stdout = ""
    for _ in range(5):
        started = time.perf_counter()
        result = subprocess.run(
            [
                sys.executable, "-m", "grimoire.hosts.runtime",
                "--host", "claude", "--event", "PostToolUse", "--project-root", str(tmp_path),
            ],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=10,
        )
        durations_ms.append((time.perf_counter() - started) * 1000)
        last_stdout = result.stdout

    median_ms = statistics.median(durations_ms)
    print(
        f"[hook-budget] PostToolUse médiane sur 5 exécutions (fenêtre de répétition remplie) : "
        f"{median_ms:.1f} ms — échantillon : {[round(d, 1) for d in durations_ms]}"
    )
    assert json.loads(last_stdout) is not None
    assert median_ms < 250, f"médiane {median_ms:.1f} ms — régression franche par rapport au coût de base"
