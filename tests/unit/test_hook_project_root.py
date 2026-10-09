"""resolve_project_root : le cwd du payload suit les ``cd`` du shell, pas le projet (issue #717)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hosts.runtime import resolve_project_root

_VARS = ("CLAUDE_PROJECT_DIR", "GRIMOIRE_PROJECT_ROOT", "COPILOT_WORKSPACE_FOLDER")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)


def _project(path: Path) -> Path:
    (path / "_grimoire").mkdir(parents=True)
    return path.resolve()


def test_project_dir_variable_beats_payload_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path / "proj")
    sub = root / "sous" / "dossier"
    sub.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert resolve_project_root({"cwd": str(sub)}) == root


def test_payload_cwd_climbs_to_first_grimoire_root(tmp_path: Path) -> None:
    root = _project(tmp_path / "proj")
    sub = root / "sous" / "dossier"
    sub.mkdir(parents=True)
    assert resolve_project_root({"cwd": str(sub)}) == root


def test_nested_project_launched_inside_stays_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outer = _project(tmp_path / "outer")
    inner = _project(outer / "grimoire-kit")
    (inner / "src").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(inner))
    assert resolve_project_root({"cwd": str(inner / "src")}) == inner
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert resolve_project_root({"cwd": str(inner / "src")}) == inner


def test_outer_session_after_cd_into_nested_keeps_outer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outer = _project(tmp_path / "outer")
    inner = _project(outer / "grimoire-kit")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(outer))
    assert resolve_project_root({"cwd": str(inner)}) == outer


def test_no_marker_keeps_payload_cwd(tmp_path: Path) -> None:
    bare = tmp_path / "nu" / "sous"
    bare.mkdir(parents=True)
    assert resolve_project_root({"cwd": str(bare)}) == bare.resolve()


def test_explicit_wins_over_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path / "proj")
    other = tmp_path / "autre"
    other.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert resolve_project_root({"cwd": str(root)}, explicit=other) == other.resolve()
