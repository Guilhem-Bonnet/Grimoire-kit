#!/usr/bin/env python3
"""Compare ``grimoire --help`` / ``grimoire <cmd> --help`` across two source trees.

Written for issue #405 (lazy CLI command tree): the fix defers importing
sub-command modules until they're actually needed, and the guard against a
regression is that ``--help`` output — top-level panel/row order included —
stays byte-identical to before. Rather than eyeball a diff by hand, this
script runs both trees (same interpreter, same ``COLUMNS``, colour disabled)
and reports every difference.

Usage
-----
    # Compare the working tree against another checkout of the same repo
    # (e.g. a worktree of the base commit, or a second clone):
    python scripts/compare-cli-help.py --before /path/to/pre-405/src --after src

    # Or against the current tree's own git history (uses `git worktree`
    # under the hood; requires the ref to exist locally):
    python scripts/compare-cli-help.py --before-ref origin/main

Exits non-zero (and prints every mismatching command) if anything differs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every top-level command name, in no particular order — hidden aliases
# (`dbg`, `wf`, `serve`) included, since their own `--help` must also be
# unaffected even though they don't appear in the top-level listing.
TOP_LEVEL_COMMANDS = [
    "version", "init", "doctor", "status", "add", "remove", "agent-miss",
    "validate", "lint", "update", "up", "context-pack", "migrate", "upgrade",
    "serve", "diff", "schema", "check", "merge", "setup", "env", "history",
    "repair",
    "memory", "hooks", "cadrage", "debugger", "dbg", "registry", "workflows",
    "wf", "standard", "ext", "blueprint", "flow", "cockpit", "task",
    "stigmergy", "features", "host", "providers", "proposals", "web",
    "config", "completion", "self", "plugins",
]


def _capture_help(python_exe: str, src: Path, home: Path, *args: str) -> str:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "COLUMNS": "100",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PYTHONPATH": str(src),
    }
    proc = subprocess.run(
        [python_exe, "-m", "grimoire.cli.app", *args, "--help"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return proc.stdout + proc.stderr


def _resolve_ref_worktree(ref: str, tmp_root: Path) -> Path:
    wt = tmp_root / "before-ref"
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), ref],
        check=True,
        capture_output=True,
        text=True,
    )
    return wt / "src"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before", type=Path, default=None, help="Path to the 'before' src/ directory.")
    parser.add_argument("--after", type=Path, default=ROOT / "src", help="Path to the 'after' src/ directory (default: this checkout's src/).")
    parser.add_argument("--before-ref", default=None, help="Git ref to check out (via a temporary worktree) as the 'before' tree, instead of --before.")
    parser.add_argument("--commands", nargs="*", default=None, help="Restrict to these command names (default: every known top-level command).")
    args = parser.parse_args(argv)

    commands = args.commands if args.commands is not None else TOP_LEVEL_COMMANDS

    with tempfile.TemporaryDirectory(prefix="grimoire-help-diff-") as tmp:
        tmp_root = Path(tmp)
        home = tmp_root / "home"
        home.mkdir()

        if args.before_ref:
            before_src = _resolve_ref_worktree(args.before_ref, tmp_root)
        elif args.before:
            before_src = args.before
        else:
            parser.error("one of --before or --before-ref is required")
            return 2

        python_exe = sys.executable
        mismatches: list[str] = []

        before_top = _capture_help(python_exe, before_src, home)
        after_top = _capture_help(python_exe, args.after, home)
        if before_top != after_top:
            mismatches.append("TOP-LEVEL --help")

        for cmd in commands:
            before_out = _capture_help(python_exe, before_src, home, cmd)
            after_out = _capture_help(python_exe, args.after, home, cmd)
            if before_out != after_out:
                mismatches.append(cmd)

        if args.before_ref:
            subprocess.run(
                ["git", "-C", str(ROOT), "worktree", "remove", "--force", str(tmp_root / "before-ref")],
                check=False,
                capture_output=True,
            )

    if mismatches:
        print(f"MISMATCH on {len(mismatches)} target(s): {', '.join(mismatches)}")
        print("Re-run with just one name via --commands <name> and diff the two --help outputs by hand.")
        return 1

    print(f"OK — {len(commands) + 1} --help outputs (top-level + {len(commands)} commands) are byte-identical.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
