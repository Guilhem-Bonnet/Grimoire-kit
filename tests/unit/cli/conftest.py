"""Shared CLI test fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


@pytest.fixture
def cli_project(tmp_path: Path) -> Path:
    """Create an initialised Grimoire project and return its root."""
    result = runner.invoke(app, ["init", str(tmp_path), "--name", "fixture-proj"])
    assert result.exit_code == 0
    return tmp_path


def assert_json_output(output: str, required_keys: list[str]) -> dict[str, Any]:
    """Parse JSON output and assert required keys are present.

    Returns the parsed dict for further assertions.
    """
    data = json.loads(output)
    for key in required_keys:
        assert key in data, f"Missing key '{key}' in JSON output"
    return data
