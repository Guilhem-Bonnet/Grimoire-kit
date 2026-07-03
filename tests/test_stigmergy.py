"""Smoke tests for the legacy framework/tools/stigmergy.py entrypoint."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "framework" / "tools" / "stigmergy.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("stigmergy_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_root(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_module_exposes_legacy_entrypoints() -> None:
    legacy_module = _load_module()
    assert callable(legacy_module.deposit_pheromone)
    assert callable(legacy_module.bulk_deposit)
    assert callable(legacy_module.sense_pheromones)
    assert callable(legacy_module.main)


def test_cli_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "stigmergy" in (result.stdout + result.stderr).lower()


def test_cli_emit_and_sense_json_roundtrip(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    emit = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(root),
            "emit",
            "--type",
            "NEED",
            "--location",
            "src/auth",
            "--text",
            "review needed",
            "--agent",
            "dev",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert emit.returncode == 0

    sense = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(root), "sense", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert sense.returncode == 0
    payload = json.loads(sense.stdout)
    assert len(payload) == 1
    assert payload[0]["pheromone"]["pheromone_type"] == "NEED"
    assert "current_intensity" in payload[0]


def test_zone_cap_amplifies_strongest_when_full(tmp_path: Path) -> None:
    legacy_module = _load_module()
    root = _make_root(tmp_path)

    for index in range(legacy_module.MAX_ACTIVE_PER_ZONE):
        legacy_module.deposit_pheromone(
            root,
            "ALERT",
            "ci/build",
            f"signal {index}",
            f"agent{index}",
            intensity=0.6 + index * 0.02,
        )

    board = legacy_module.load_board(root)
    active = [p for p in board.pheromones if not p.resolved and p.location == "ci/build"]
    strongest_id = max(active, key=lambda p: p.intensity).pheromone_id
    reinforcements_before = max(active, key=lambda p: p.intensity).reinforcements

    legacy_module.deposit_pheromone(root, "ALERT", "ci/build", "signal overflow", "agent-extra")

    board_after = legacy_module.load_board(root)
    active_after = [p for p in board_after.pheromones if not p.resolved and p.location == "ci/build"]
    assert len(active_after) == legacy_module.MAX_ACTIVE_PER_ZONE
    strongest_after = next(p for p in active_after if p.pheromone_id == strongest_id)
    assert strongest_after.reinforcements > reinforcements_before


def test_self_amplification_blocked(tmp_path: Path) -> None:
    legacy_module = _load_module()
    _make_root(tmp_path)
    board = legacy_module.PheromoneBoard()
    pheromone = legacy_module.emit_pheromone(board, "NEED", "zone", "msg", "agent-alpha", intensity=0.5)
    result = legacy_module.amplify_pheromone(board, pheromone.pheromone_id, "agent-alpha")

    assert result is not None
    assert result.intensity == 0.5
    assert result.reinforcements == 0
    assert "agent-alpha" not in result.reinforced_by