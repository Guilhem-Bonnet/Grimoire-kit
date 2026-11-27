"""``grimoire up`` complète le ``.gitignore`` d'un projet déjà installé (verrous du kit).

Constat réel (3.129.0) : ``ensure_grimoire_gitignore`` n'était appelée que par la mise en place du profil standard ;
sur un projet installé, ``up`` ne la rejouait pas (``--no-standard``, profil préservé…), et le verrou restait suivi.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.standard_generation import GITIGNORE_MARKER, GITIGNORE_SECTION, LOCK_IGNORE_LINES

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git requis")

NOTICE = ".gitignore : lignes du kit ajoutées"
USER_LINES = "node_modules/\n_grimoire/_memory/session-state.md\n.env\n"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


def _up(project: Path, *extra: str) -> str:
    result = CliRunner().invoke(app, ["up", str(project), "--backend", "local", *extra])
    assert result.exit_code == 0, result.output
    return result.output


def _installed(tmp_path: Path, gitignore: str | None) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    _up(project)
    gi = project / ".gitignore"
    if gitignore is None:
        gi.unlink(missing_ok=True)
    else:
        gi.write_bytes(gitignore.encode("utf-8"))
    return project


def _ignored(project: Path, relative: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(project), "check-ignore", "-q", relative], capture_output=True, check=False,
    ).returncode == 0


def test_up_adds_the_kit_block_to_an_installed_project_without_one(tmp_path: Path) -> None:
    project = _installed(tmp_path, USER_LINES)
    out = _up(project)
    text = (project / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith(USER_LINES)
    assert GITIGNORE_MARKER in text
    assert _ignored(project, ".project-context.yaml.lock")
    assert NOTICE in out
    before = (project / ".gitignore").read_bytes()
    assert NOTICE not in _up(project)
    assert (project / ".gitignore").read_bytes() == before


def test_up_completes_an_old_kit_block_without_the_lock_lines(tmp_path: Path) -> None:
    old = USER_LINES + f"\n{GITIGNORE_MARKER}\n# Session runs\n_grimoire-output/runs/\n"
    project = _installed(tmp_path, old)
    out = _up(project)
    text = (project / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith(old.rstrip())
    for line in LOCK_IGNORE_LINES:
        assert text.splitlines().count(line) == 1, line
    assert _ignored(project, "_grimoire/cadrage/.x.md.lock")
    assert NOTICE in out
    assert NOTICE not in _up(project)


def test_up_adds_the_lines_even_when_the_standard_step_is_skipped(tmp_path: Path) -> None:
    project = _installed(tmp_path, USER_LINES)
    out = _up(project, "--no-standard")
    assert _ignored(project, ".project-context.yaml.lock")
    assert NOTICE in out


def test_up_dry_run_writes_nothing(tmp_path: Path) -> None:
    project = _installed(tmp_path, USER_LINES)
    out = _up(project, "--dry-run")
    assert (project / ".gitignore").read_text(encoding="utf-8") == USER_LINES
    assert NOTICE not in out


def test_up_keeps_crlf_and_leaves_a_symlinked_gitignore_alone(tmp_path: Path) -> None:
    project = _installed(tmp_path, "a/\r\nb/\r\n")
    _up(project)
    raw = (project / ".gitignore").read_bytes()
    assert raw.startswith(b"a/\r\nb/\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")

    other = tmp_path / "other"
    other.write_text("x/\n", encoding="utf-8")
    (project / ".gitignore").unlink()
    (project / ".gitignore").symlink_to(other)
    assert NOTICE not in _up(project)
    assert other.read_text(encoding="utf-8") == "x/\n"


def test_up_leaves_a_non_utf8_gitignore_intact(tmp_path: Path) -> None:
    project = _installed(tmp_path, USER_LINES)
    raw = b"caf\xe9/\n"
    (project / ".gitignore").write_bytes(raw)
    assert NOTICE not in _up(project)
    assert (project / ".gitignore").read_bytes() == raw


def test_up_gives_a_project_without_gitignore_the_kit_section(tmp_path: Path) -> None:
    project = _installed(tmp_path, None)
    _up(project, "--no-standard")
    assert (project / ".gitignore").read_text(encoding="utf-8") == GITIGNORE_SECTION
