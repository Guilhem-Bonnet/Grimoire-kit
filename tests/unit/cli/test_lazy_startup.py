"""Import guard for the lazy CLI command tree (issue #405).

``grimoire.cli.app`` used to import every sub-command module eagerly
(``cmd_flow``, ``cmd_host``, ``cmd_memory_lexical``, ``cmd_cockpit``...) just
to build the Typer/Click tree, regardless of which command was actually
requested — see ``grimoire.cli._lazy.LazyTyperGroup``. This test guards the
regression: importing ``grimoire.cli.app`` alone must never pull the heavy
sub-command dependencies into ``sys.modules``.

Run in a clean subprocess: importing anything CLI-related in-process (e.g.
via other test modules already having imported ``grimoire.cli.app`` or one
of its sub-commands) would poison ``sys.modules`` and make the guard
meaningless.
"""

from __future__ import annotations

import subprocess
import sys

#: Modules that must stay unimported by a bare `import grimoire.cli.app` —
#: the exact regression named in issue #405.
_FORBIDDEN_MODULES = (
    "grimoire.flows",
    "grimoire.missions.dispatch",
    "grimoire.memory",
    "grimoire.cli.cmd_cockpit",
)

_LEAK_PROBE = (
    f"leaked = sorted(m for m in {_FORBIDDEN_MODULES!r} if m in sys.modules)\n"
    "print(','.join(leaked))"
)


def _run_script(script: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr}"
    return result


def _last_line(stdout: str) -> str:
    lines = stdout.splitlines()
    return lines[-1] if lines else ""


def test_bare_import_does_not_leak_heavy_submodules() -> None:
    """`import grimoire.cli.app` alone must not import flows/missions/memory/cockpit."""
    script = f"import sys\nimport grimoire.cli.app\n{_LEAK_PROBE}"
    result = _run_script(script)
    leaked_names = [m for m in _last_line(result.stdout).split(",") if m]
    assert leaked_names == [], f"grimoire.cli.app import leaked: {leaked_names}"


def test_version_flag_does_not_leak_heavy_submodules() -> None:
    """`grimoire --version` exits eagerly before any sub-command is resolved."""
    script = (
        "import sys\n"
        "sys.argv = ['grimoire', '--version']\n"
        "from grimoire.cli.app import cli\n"
        "try:\n"
        "    cli()\n"
        "except SystemExit:\n"
        "    pass\n"
        f"{_LEAK_PROBE}"
    )
    result = _run_script(script)
    # stdout also carries "grimoire-kit <version>" (the --version output
    # itself, printed before the guard's own probe line) — only the last
    # line is the leaked-module report. Don't `.strip()` the whole output
    # first: when nothing leaked, that line is empty and `.strip()` would
    # swallow it, leaving the version line looking like the last one.
    leaked_names = [m for m in _last_line(result.stdout).split(",") if m]
    assert leaked_names == [], f"grimoire --version leaked: {leaked_names}"


def test_lazy_submodule_is_imported_on_first_use() -> None:
    """Sanity check for the guard itself: `grimoire flow --help` *does* import cmd_flow.

    Without this, an implementation that simply never registers "flow" at all
    (rather than lazily) would also pass the two tests above.
    """
    script = (
        "import sys\n"
        "sys.argv = ['grimoire', 'flow', '--help']\n"
        "from grimoire.cli.app import cli\n"
        "try:\n"
        "    cli()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('grimoire.cli.cmd_flow' in sys.modules)"
    )
    result = _run_script(script)
    assert _last_line(result.stdout) == "True", f"unexpected output: {result.stdout!r}"
