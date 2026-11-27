"""Les verrous de fichier du kit ne sont jamais des fichiers « non suivis » du projet de l'utilisateur.

Le bloc ``.gitignore`` du kit les couvre à l'installation et à la mise à jour, sans toucher aux lignes de l'utilisateur ;
les fichiers à versionner (``comments.json``, ``suggestions.json``) et les ``*.lock`` du projet restent suivis.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from grimoire.core.fsio import locked
from grimoire.core.standard_generation import (
    GITIGNORE_MARKER,
    GITIGNORE_SECTION,
    LOCK_IGNORE,
    ensure_grimoire_gitignore,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git requis")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    assert _git(tmp_path, "init", "-q").returncode == 0
    return tmp_path


def _touch(root: Path, relative: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return relative


def _ignored(root: Path, relative: str) -> bool:
    return _git(root, "check-ignore", "-q", relative).returncode == 0


LOCKS = (
    ".project-context.yaml.lock",
    "_grimoire/cadrage/export/.01-brief.md.lock",
    "_grimoire/cadrage/export/.suggestions.json.lock",
    "_grimoire/cadrage/export/.decoupage.lock",
    "_grimoire/standard/.task-board.yaml.lock",
    "_grimoire/cadrage/.01-brief.md.xlock",
    "_grimoire-output/evidence/.index.json.lock",
)
KEPT = (
    "_grimoire/cadrage/export/comments.json",
    "_grimoire/cadrage/export/suggestions.json",
    "_grimoire/cadrage/export/01-brief.md",
    "yarn.lock",
    "poetry.lock",
    "web/.cache.lock",
)


def test_the_kit_section_ignores_its_locks_and_nothing_else(repo: Path) -> None:
    assert ensure_grimoire_gitignore(repo) is not None
    for lock in LOCKS:
        _touch(repo, lock)
        assert _ignored(repo, lock), lock
    for kept in KEPT:
        _touch(repo, kept)
        assert not _ignored(repo, kept), kept
    status = _git(repo, "status", "--porcelain", "-uall").stdout
    assert ".lock" not in status.replace("yarn.lock", "").replace("poetry.lock", "").replace(".cache.lock", "")


def test_real_lock_files_written_by_the_kit_are_ignored(repo: Path) -> None:
    ensure_grimoire_gitignore(repo)
    target = repo / "_grimoire/cadrage/export/01-brief.md"
    target.parent.mkdir(parents=True)
    target.write_text("brief\n", encoding="utf-8")
    with locked(target):
        pass
    found = [p.relative_to(repo).as_posix() for p in target.parent.iterdir() if p.name.endswith((".lock", ".xlock"))]
    assert found  # le verrou existe bel et bien
    assert all(_ignored(repo, name) for name in found)


def test_the_update_adds_the_locks_once_to_a_section_installed_before(repo: Path) -> None:
    mine = "node_modules/\n*.log\n"
    old = f"{mine}\n{GITIGNORE_MARKER}\n_grimoire-output/.runs/\n_grimoire-output/memory-reads.jsonl\n"
    (repo / ".gitignore").write_text(old, encoding="utf-8")
    assert ensure_grimoire_gitignore(repo) is not None
    text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith(old.rstrip("\n"))  # rien de l'utilisateur n'est réécrit
    assert text.count(LOCK_IGNORE) == 1 and text.count(GITIGNORE_MARKER) == 1
    assert _ignored(repo, _touch(repo, "_grimoire/cadrage/x/.01-brief.md.lock"))
    assert ensure_grimoire_gitignore(repo) is None  # idempotent
    assert (repo / ".gitignore").read_text(encoding="utf-8") == text


def test_a_gitignore_that_is_not_utf8_is_left_alone_and_reported(repo: Path, caplog: pytest.LogCaptureFixture) -> None:
    raw = b"# caf\xe9\nnode_modules/\n"
    (repo / ".gitignore").write_bytes(raw)
    with caplog.at_level("WARNING"):
        assert ensure_grimoire_gitignore(repo) is None  # ne lève pas
    assert (repo / ".gitignore").read_bytes() == raw
    assert "UTF-8" in caplog.text


def test_crlf_line_endings_are_kept(repo: Path) -> None:
    old = f"node_modules/\r\n\r\n{GITIGNORE_MARKER}\r\n_grimoire-output/.runs/\r\n"
    (repo / ".gitignore").write_bytes(old.encode())
    assert ensure_grimoire_gitignore(repo) is not None
    data = (repo / ".gitignore").read_bytes()
    assert data.startswith(old.encode()) and data.count(b"\r\n") == data.count(b"\n")  # aucun LF seul
    assert _ignored(repo, _touch(repo, "_grimoire/cadrage/x/.01-brief.md.lock"))


def test_a_symlinked_gitignore_is_never_written_through(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    elsewhere = tmp_path_factory.mktemp("ailleurs") / "shared-ignore"
    elsewhere.write_text("shared\n", encoding="utf-8")
    (repo / ".gitignore").symlink_to(elsewhere)
    assert ensure_grimoire_gitignore(repo) is None
    assert elsewhere.read_text(encoding="utf-8") == "shared\n"


def test_an_install_on_a_project_without_gitignore_writes_the_whole_section(repo: Path) -> None:
    assert ensure_grimoire_gitignore(repo) == repo / ".gitignore"
    assert (repo / ".gitignore").read_text(encoding="utf-8") == GITIGNORE_SECTION
    assert GITIGNORE_SECTION.count(LOCK_IGNORE) == 1
