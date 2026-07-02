"""Tests for scripts/depaudit-waivers.py — governed dependency-audit waivers."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "depaudit-waivers.py"


def _load_module():
    name = "depaudit_waivers_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_waivers(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


TODAY = dt.date(2026, 7, 1)


def test_active_waiver_is_emitted(tmp_path: Path) -> None:
    mod = _load_module()
    waivers = _write_waivers(
        tmp_path / "waivers.yaml",
        'waivers:\n  - vulnerability_id: "CVE-2025-3000"\n    expires_at: "2026-09-30"\n',
    )
    assert mod.active_vulnerability_ids(waivers, today=TODAY) == ["CVE-2025-3000"]


def test_expired_waiver_is_dropped(tmp_path: Path) -> None:
    mod = _load_module()
    waivers = _write_waivers(
        tmp_path / "waivers.yaml",
        'waivers:\n  - vulnerability_id: "CVE-2025-3000"\n    expires_at: "2026-06-30"\n',
    )
    assert mod.active_vulnerability_ids(waivers, today=TODAY) == []


def test_invalid_expiry_is_dropped(tmp_path: Path) -> None:
    mod = _load_module()
    waivers = _write_waivers(
        tmp_path / "waivers.yaml",
        'waivers:\n  - vulnerability_id: "CVE-2025-3000"\n    expires_at: "soon"\n',
    )
    assert mod.active_vulnerability_ids(waivers, today=TODAY) == []


def test_missing_file_yields_nothing(tmp_path: Path) -> None:
    mod = _load_module()
    assert mod.active_vulnerability_ids(tmp_path / "absent.yaml", today=TODAY) == []


def test_repo_waivers_file_parses() -> None:
    mod = _load_module()
    # The committed waiver file must always parse; content may legitimately
    # be empty once every waiver expires.
    ids = mod.active_vulnerability_ids(mod.WAIVERS_FILE, today=TODAY)
    assert isinstance(ids, list)
