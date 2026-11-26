"""Flotte en direct (#1243 suite) : les lanes d'une session restent des suites de même modèle et même effort, et bornées.

Défaut empêché : la lane gardait ``None`` pour un effort absent alors que l'appel portait ``""`` ; la comparaison
échouait à chaque appel et ouvrait une lane par appel (110 octets chacune, sans borne) pour tout journal sans champ
``effort`` : 18 Mo de réponse à 40 sessions, jamais gardée par le cache de lecture.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from grimoire.tools import workspace_fleet_live as fleet

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 26, 10, 5, tzinfo=UTC)
SESSION = "0f0f0f0f-1e1e1e1e-2d2d2d2d-3c3c3c3c-4b4b4b4b4b4b"


def _assistant(i: int, model: str, *, effort: str | None) -> dict[str, Any]:
    line: dict[str, Any] = {
        "type": "assistant",
        "timestamp": f"2026-11-26T10:{i // 60:02d}:{i % 60:02d}Z",
        "sessionId": SESSION,
        "entrypoint": "cli",
        "message": {"id": f"m{i}", "model": model, "usage": {"input_tokens": 1, "output_tokens": 1}, "content": []},
    }
    if effort is not None:
        line["effort"] = effort
    return line


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from grimoire.core.agentic_standard import setup_standard_profile
    from grimoire.tools import project_registry as reg

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    root = tmp_path / "projet"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    reg.save_registry([{"slug": "projet", "name": "Projet", "path": str(root.resolve())}])
    return root


def _view(project: Path, tmp_path: Path, lines: list[dict[str, Any]]) -> dict[str, Any]:
    directory = tmp_path / "claude-config" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{SESSION}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    fleet.reset_cache()
    view = fleet.fleet_live_view(project, now=NOW)
    session = next(s for s in view["sessions"] if s["id"] == SESSION)
    assert isinstance(session, dict)
    return session


def test_journal_without_effort_keeps_one_lane(project: Path, tmp_path: Path) -> None:
    session = _view(project, tmp_path, [_assistant(i, "claude-sonnet-5-5", effort=None) for i in range(30)])
    assert len(session["lanes"]) == 1  # trente appels, un seul modèle : une seule lane
    assert session["lanes"][0]["effort"] is None
    assert "lanes_dropped" not in session


def test_a_change_of_effort_opens_a_lane(project: Path, tmp_path: Path) -> None:
    lines = [_assistant(i, "claude-sonnet-5-5", effort="high") for i in range(5)]
    lines += [_assistant(i, "claude-sonnet-5-5", effort="low") for i in range(5, 9)]
    session = _view(project, tmp_path, lines)
    assert [lane["effort"] for lane in session["lanes"]] == ["high", "low"]
    assert [lane["model"] for lane in session["lanes"]] == ["claude-sonnet-5-5"] * 2


def test_lanes_are_bounded_and_say_so(project: Path, tmp_path: Path) -> None:
    calls = fleet._MAX_LANES + 50
    models = ("claude-sonnet-5-5", "claude-opus-5-5")
    session = _view(project, tmp_path, [_assistant(i, models[i % 2], effort="high") for i in range(calls)])
    assert len(session["lanes"]) == fleet._MAX_LANES + 1  # les plus récentes fermées, plus la lane ouverte
    assert session["lanes_dropped"] == calls - 1 - fleet._MAX_LANES
    assert session["lanes"][-1]["model"] == models[(calls - 1) % 2]  # la plus récente est gardée
    again = fleet.fleet_live_view(project, now=NOW)  # lecture suivante sans changement : même compte
    assert next(s for s in again["sessions"] if s["id"] == SESSION)["lanes_dropped"] == session["lanes_dropped"]
