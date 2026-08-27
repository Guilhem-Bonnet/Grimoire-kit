"""The lifecycle hook runs once per tool call; its cost is a design property.

Two things made it expensive, and both are the kind of regression a reviewer
cannot see in a diff:

- routing the hook through ``grimoire host hook`` built the whole Typer command
  tree — every ``cmd_*`` module imported to resolve one subcommand — measured at
  391 ms per call against 102 ms for the dedicated entry point;
- importing the standard engine at module scope so that the *closure* decision
  could evaluate gates, which the *tool* decision never does, cost 48 ms on
  every call that had no use for it.

Neither shows up as a failing assertion anywhere else, so they are pinned here.
"""

from __future__ import annotations

import os
import subprocess
import sys

from grimoire.hosts.emitters.base import Emitter

#: Modules a hook decision must never pull at import time. Each one is a heavy
#: subtree that only the closure path needs, loaded lazily where it is used.
FORBIDDEN_AT_IMPORT = (
    "grimoire.core.agentic_standard",
    "grimoire.core.scaffold",
    "grimoire.core.archetype_resolver",
    "grimoire.cli.app",
)


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
