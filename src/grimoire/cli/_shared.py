"""CLI helpers shared by ``app.py`` and the individual command modules.

They live here rather than in ``app.py`` because command modules need them and
``app.py`` imports the command modules: importing back from ``app`` closed a
cycle that only worked through a function-local import.
"""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from typing import Any

from rich.console import Console

from grimoire.__version__ import __version__

console = Console(stderr=True)

_AUDIT_FILENAME = ".grimoire-audit.jsonl"
_AUDIT_MAX_ENTRIES = 5000


def _status_spinner(msg: str, *, show: bool = True) -> Any:
    """Return a Rich Status spinner (or a no-op context manager when silent)."""
    if show:
        return console.status(f"[bold]{msg}[/bold]", spinner="dots")
    return nullcontext()


def _log_operation(command: str, args: dict[str, Any] | None = None, *, ok: bool = True) -> None:
    """Append an entry to the project audit log (best-effort, silent on failure)."""
    import datetime as _dt

    try:
        from grimoire.tools._common import find_project_root

        root = find_project_root()
    except FileNotFoundError:
        return
    log_dir = root / "_grimoire" / "_memory"
    if not log_dir.is_dir():
        return
    log_file = log_dir / _AUDIT_FILENAME
    record = {
        "ts": _dt.datetime.now(tz=_dt.UTC).isoformat(),
        "v": __version__,
        "cmd": command,
        "ok": ok,
    }
    if args:
        record["args"] = {k: str(v) for k, v in args.items()}
    try:
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        # Truncate if too large — single handle avoids race between read and write
        with open(log_file, "r+", encoding="utf-8") as fh:
            lines = fh.readlines()
            if len(lines) > _AUDIT_MAX_ENTRIES:
                keep = lines[-_AUDIT_MAX_ENTRIES:]
                fh.seek(0)
                fh.writelines(keep)
                fh.truncate()
    except OSError as exc:
        if os.environ.get("GRIMOIRE_DEBUG"):
            console.print(f"[dim]Audit log write failed: {exc}[/dim]")
