"""The lifecycle hook runs once per tool call; its cost is a design property.

Three things made it expensive, and all three are the kind of regression a
reviewer cannot see in a diff:

- routing the hook through ``grimoire host hook`` built the whole Typer command
  tree — every ``cmd_*`` module imported to resolve one subcommand — measured at
  391 ms per call against 102 ms for the dedicated entry point;
- importing the standard engine at module scope so that the *closure* decision
  could evaluate gates, which the *tool* decision never does, cost 48 ms on
  every call that had no use for it;
- ``grimoire.hosts.decisions`` was one 918-line module holding all seven
  decisions, so a ``PreToolUse`` call paid for the policy engine *and* the
  activation/evidence-gate machinery it never runs — the same anti-pattern as
  the Typer tree above, on the hook side instead of the CLI side (issue #419).
  Splitting it into a package, one submodule per decision, resolved lazily by
  ``grimoire.hosts.decisions.run_decision``, means a decision imports only
  what *it* needs. ``grimoire.hosts.surface`` (the agent/model IR —
  ``AgentSpec``, ``ModelAffinity``, the Rust-optional fingerprint machinery)
  moved out of the hot path the same way: ``grimoire.hosts.events`` now holds
  the three enums (``HookEvent``, ``ToolVerb``, ``Enforcement``) every hook
  call actually needs, and ``grimoire.hosts.__init__`` no longer imports
  ``ProjectSurface`` eagerly just to re-export a name nothing outside tests
  imports from the package root.

None of this shows up as a failing assertion anywhere else, so it is pinned
here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from grimoire.hosts.emitters.base import Emitter

#: Modules a hook decision must never pull at import time. Each one is a heavy
#: subtree that only the closure path (or a specific decision) needs, loaded
#: lazily where it is used.
FORBIDDEN_AT_IMPORT = (
    "grimoire.core.agentic_standard",
    "grimoire.core.scaffold",
    "grimoire.core.archetype_resolver",
    "grimoire.cli.app",
    # issue #419: merely importing the runtime module — no decision run yet —
    # must not pull the agent/model IR, the policy engine, or the trace
    # ledger. Those are one specific decision's business, not the wire
    # layer's.
    "grimoire.hosts.surface",
    "grimoire.policies.engine",
    "grimoire.policies.schemas",
    "grimoire.traces.ledger",
)

#: Every decision's own submodule under the ``grimoire.hosts.decisions``
#: package (issue #419) — running one decision must import only its own.
_DECISION_SUBMODULES = (
    "grimoire.hosts.decisions.activation",
    "grimoire.hosts.decisions.task_context",
    "grimoire.hosts.decisions.tool_policy",
    "grimoire.hosts.decisions.evidence_trace",
    "grimoire.hosts.decisions.evidence_gate",
    "grimoire.hosts.decisions.subagent_gate",
    "grimoire.hosts.decisions.context_capsule",
)

#: decision id -> (its own submodule, a payload that keeps its ``Decision``
#: cheap to build on an unenrolled project — no extra imports incidental to
#: the test, like the trace ledger's, muddying the "only its own module"
#: assertion below).
_DECISION_CASES: dict[str, tuple[str, dict[str, object]]] = {
    "grimoire.activation": ("grimoire.hosts.decisions.activation", {"hook_event_name": "SessionStart"}),
    "grimoire.task-context": ("grimoire.hosts.decisions.task_context", {"hook_event_name": "UserPromptSubmit"}),
    "grimoire.tool-policy": (
        "grimoire.hosts.decisions.tool_policy",
        {"tool_name": "Read", "tool_input": {"file_path": "README.md"}},
    ),
    "grimoire.evidence-trace": (
        "grimoire.hosts.decisions.evidence_trace",
        {"tool_name": "Read", "tool_input": {"file_path": "README.md"}},
    ),
    "grimoire.evidence-gate": ("grimoire.hosts.decisions.evidence_gate", {"hook_event_name": "Stop"}),
    "grimoire.subagent-gate": ("grimoire.hosts.decisions.subagent_gate", {"hook_event_name": "SubagentStop"}),
    "grimoire.context-capsule": ("grimoire.hosts.decisions.context_capsule", {"hook_event_name": "PreCompact"}),
}

#: Very generous ceiling for one ``grimoire-hook`` call end to end (process
#: start, its decision's own imports, execution, JSON out) — not the 50 ms
#: target from issue #419 itself (a shared CI runner is not the dedicated
#: machine that target was measured on), just a guard against the anti-
#: pattern coming back: an eager import that pulls the whole standard engine,
#: the full agent/model IR, or a sibling decision's dependencies. Doubled on
#: Windows for the same reason as ``_COCKPIT_REFRESH_BUDGET_SECONDS`` in
#: ``tests/test_invariants.py`` — the same scenario reliably costs more there
#: without any code having changed, and the guard should catch a real
#: regression, not the runner's own weather.
_HOOK_CALL_BUDGET_SECONDS = 1.6 if sys.platform.startswith("win") else 0.8


def test_generated_hooks_use_the_dedicated_entry_point() -> None:
    command = Emitter.hook_command("claude", "Stop")
    assert command.startswith("grimoire-hook ")
    # `grimoire host hook` still works for humans; it is simply not what a
    # configuration fired on every tool call should invoke.
    assert not command.startswith("grimoire host ")


def test_the_hook_path_does_not_import_the_standard_engine() -> None:
    """Importing the runtime must stay cheap, and stay cheap on purpose."""
    probe = (
        "import sys; import grimoire.hosts.runtime; "
        f"print(','.join(m for m in {FORBIDDEN_AT_IMPORT!r} if m in sys.modules))"
    )
    # The subprocess must see the same package as the test, not whichever
    # grimoire happens to be installed in the ambient environment.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, env=env
    )
    leaked = result.stdout.strip()
    assert not leaked, (
        f"le chemin des hooks importe {leaked} au chargement — "
        "chaque appel d'outil le paie ; importer à l'usage, pas au module"
    )


def test_the_closure_decision_still_reaches_the_engine() -> None:
    """Deferring the import must not have removed the capability."""
    from grimoire.hosts.decisions import _gate_summary

    assert callable(_gate_summary)
    source = _gate_summary.__doc__ or ""
    del source  # the behavioural proof lives in test_hosts.py; this pins reachability
    from grimoire.core.agentic_standard import check_evidence_gates

    assert callable(check_evidence_gates)


def _run_one_decision(decision_id: str, payload: dict[str, object], project_root: str) -> subprocess.CompletedProcess[str]:
    """Run *decision_id* end to end (``grimoire.hosts.runtime.main``) in a clean subprocess.

    A real ``main()`` call, not a bare ``run_decision`` import, so the probe
    exercises exactly what a host actually invokes: stdin JSON in, rendered
    JSON out, through the real argv parsing.
    """
    probe = (
        "import sys, os, json\n"
        f"sys.argv = ['grimoire-hook', '--host', 'claude-code', '--event', 'PreToolUse', "
        f"'--project-root', {project_root!r}, '--decision', {decision_id!r}]\n"
        "r, w = os.pipe()\n"
        f"os.write(w, {json.dumps(payload)!r}.encode('utf-8'))\n"
        "os.close(w)\n"
        "os.dup2(r, 0)\n"
        "from grimoire.hosts.runtime import main\n"
        "main()\n"
        f"print(','.join(m for m in {_DECISION_SUBMODULES!r} if m in sys.modules), file=sys.stderr)\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    return subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        encoding="utf-8",
    )


@pytest.mark.parametrize("decision_id", sorted(_DECISION_CASES))
def test_running_one_decision_imports_only_its_own_submodule(decision_id: str, tmp_path: Path) -> None:
    """issue #419: a ``--decision`` run must import its own submodule, never a sibling's.

    ``grimoire.hosts.decisions`` used to be one 918-line module: whichever
    decision ran, all seven were already loaded. Splitting it into a package
    only pays off if this stays true after the split, not just at the moment
    it lands — a well-meaning future edit that pulls a sibling's helper back
    into module scope would silently undo the whole point.
    """
    own_submodule, payload = _DECISION_CASES[decision_id]
    others = [m for m in _DECISION_SUBMODULES if m != own_submodule]
    result = _run_one_decision(decision_id, payload, str(tmp_path))
    loaded = set(result.stderr.strip().split(",")) if result.stderr.strip() else set()
    assert own_submodule in loaded, (
        f"{decision_id} n'a même pas chargé son propre module {own_submodule} — "
        f"la table de résolution de grimoire.hosts.decisions est-elle à jour ? (stdout={result.stdout!r})"
    )
    leaked = loaded & set(others)
    assert not leaked, (
        f"exécuter {decision_id} a chargé {sorted(leaked)} — une décision ne doit importer "
        "que son propre module, jamais celui d'une décision sœur (issue #419)"
    )


def test_a_hook_call_stays_within_a_generous_time_budget(tmp_path: Path) -> None:
    """A portable regression guard, not the 50 ms target itself.

    ``scripts/bench-rust-cores.py --macro-runs 15`` is how issue #419's
    numeric target is actually measured, on one dedicated machine, before and
    after — a shared, possibly noisy CI runner is not that machine, and a
    tight assertion here would flake on the runner's own weather rather than
    on a real regression. This budget exists only to catch the anti-pattern
    coming back wholesale (an eager import of the standard engine, the full
    agent/model IR, or every decision's dependencies at once) — see
    ``_HOOK_CALL_BUDGET_SECONDS`` for why it is wide and platform-aware.
    """
    payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf _grimoire-output/tmp"}}
    started = time.perf_counter()
    _run_one_decision("grimoire.tool-policy", payload, str(tmp_path))
    elapsed = time.perf_counter() - started
    assert elapsed < _HOOK_CALL_BUDGET_SECONDS, (
        f"grimoire-hook PreToolUse (tool-policy) a mis {elapsed:.2f}s, "
        f"largement au-delà du budget de garde ({_HOOK_CALL_BUDGET_SECONDS}s) — "
        "un import redevenu gourmand au chargement ? Voir issue #419."
    )
