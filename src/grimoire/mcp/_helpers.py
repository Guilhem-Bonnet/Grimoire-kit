"""Grimoire MCP — core shared utility functions used across handler modules."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireError

# ── Config ────────────────────────────────────────────────────────────────────

def _find_config() -> GrimoireConfig:
    """Find and load the project config from cwd upward."""
    return GrimoireConfig.find_and_load()


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _dump_json(payload: dict[str, Any]) -> str:
    """Serialize MCP tool results consistently."""
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _error(message: str, **context: Any) -> str:
    """Return a structured JSON error payload."""
    return _dump_json({"error": message, **context})


def _parse_json_output(stdout: str) -> Any | None:
    """Parse JSON subprocess output when available."""
    content = stdout.strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


# ── Path helpers ──────────────────────────────────────────────────────────────

def _resolve_path(path_value: str, *, base: Path) -> Path:
    """Resolve a possibly relative path against a base directory."""
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _ensure_within_root(path: Path, *, root: Path, label: str) -> Path:
    """Ensure a resolved path stays within an allowed root."""
    resolved = path.resolve()
    allowed_root = root.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise GrimoireError(
            f"{label} must stay within {allowed_root}"
        ) from exc
    return resolved


def _resolve_path_within(
    path_value: str,
    *,
    base: Path,
    root: Path,
    label: str,
) -> Path:
    """Resolve a path and reject escapes outside the allowed root."""
    return _ensure_within_root(
        _resolve_path(path_value, base=base),
        root=root,
        label=label,
    )


def _relative_display_path(path: Path, *, base: Path) -> str:
    """Return a stable POSIX path relative to a project root when possible."""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _normalize_kit_relative_path(path: str) -> Path:
    """Normalize paths so heuristics work from workspace root or kit root."""
    candidate = Path(path)
    if candidate.parts[:1] == ("grimoire-kit",):
        return Path(*candidate.parts[1:])
    return candidate


def _parse_csv_or_lines(value: str) -> list[str]:
    """Split a comma or newline separated string while preserving ordering."""
    return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]


def _parse_paths_input(paths: str, *, base: Path) -> list[str]:
    """Parse newline/comma-separated paths into normalized project-relative paths."""
    candidates = _parse_csv_or_lines(paths)
    normalized: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        resolved = _resolve_path(candidate, base=base)
        rel = _relative_display_path(resolved, base=base)
        if rel in seen:
            continue
        seen.add(rel)
        normalized.append(rel)

    return normalized


# ── Subprocess ────────────────────────────────────────────────────────────────

def _run_subprocess(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float = 600,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a subprocess and return a JSON-serializable result."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=process_env,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout_seconds}s: {command}",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command not found: {command[0]}",
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }


# ── Kit root discovery ────────────────────────────────────────────────────────

def _find_kit_root(start: Path) -> Path | None:
    """Walk up to find the kit directory containing archetypes/."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / "archetypes").is_dir():
            return parent.resolve()
        nested = parent / "grimoire-kit"
        if (nested / "archetypes").is_dir():
            return nested.resolve()
    return None


# ── Assets root discovery ─────────────────────────────────────────────────────

def _find_assets_root(start: Path) -> Path | None:
    """Walk up to find the grimoire-game-assets directory."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        for candidate in (parent, parent / "grimoire-game-assets"):
            if (candidate / "tools").is_dir() and (candidate / "10-curated").is_dir():
                return candidate.resolve()
    return None


def _resolve_assets_root(project_root: Path, assets_root: str = "") -> Path | None:
    """Resolve the grimoire-game-assets root from the project or workspace."""
    detected_root = _find_assets_root(project_root)
    if assets_root:
        candidate = _resolve_path(assets_root, base=project_root)
        if (
            detected_root
            and candidate == detected_root
            and (candidate / "tools").is_dir()
            and (candidate / "10-curated").is_dir()
        ):
            return candidate
        return None
    return detected_root
