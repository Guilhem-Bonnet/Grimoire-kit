"""Shared CLI utilities — constants, helpers, and audit log.

Imported by all cmd_*.py modules to avoid circular dependencies and
ensure a single source of truth for cross-cutting concerns.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from grimoire.__version__ import __version__

# ── Shared console ────────────────────────────────────────────────────────────

console = Console(stderr=True)

# ── Semantic exit codes ───────────────────────────────────────────────────────

_EXIT_OK = 0
_EXIT_USER = 1      # user/input error (missing file, bad arg, validation fail)
_EXIT_CONFIG = 2    # configuration error (parse error, missing key)

# ── Output format helper ──────────────────────────────────────────────────────


def _get_fmt(ctx: typer.Context) -> str:
    """Return the output format from context — 'text' or 'json'."""
    return (ctx.obj or {}).get("output", "text")


# ── YAML round-trip helpers ───────────────────────────────────────────────────


def _load_yaml_rw(config_path: Path) -> tuple[Any, Any]:
    """Load YAML preserving formatting (for round-trip editing)."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.load(fh)
    return yaml, data


def _save_yaml_rw(yaml: Any, data: Any, config_path: Path) -> None:
    """Write YAML back preserving formatting."""
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


# ── Config resolution ─────────────────────────────────────────────────────────


def _find_config(path: Path) -> Path:
    """Resolve project-context.yaml — walk up directories if needed."""
    from grimoire.tools._common import find_project_root

    target = path.resolve()
    config_path = target / "project-context.yaml"
    if config_path.is_file():
        return config_path
    try:
        root = find_project_root(target)
        return root / "project-context.yaml"
    except (FileNotFoundError, PermissionError, OSError):
        console.print("[red]Not a Grimoire project[/red] — run [bold]grimoire init[/bold] first.")
        raise typer.Exit(_EXIT_USER) from None


# ── Audit log ─────────────────────────────────────────────────────────────────

_AUDIT_FILENAME = ".grimoire-audit.jsonl"
_AUDIT_MAX_ENTRIES = 5000


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
