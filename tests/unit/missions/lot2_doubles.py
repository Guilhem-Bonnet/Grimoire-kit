"""Les doubles du lot 2 de #876 : un faux ``claude`` et un faux ``gh``, plus un vrai dépôt git avec une remote locale nue.

Aucun test n'appelle le vrai ``claude`` ni le vrai ``gh`` : le lanceur et le janitor reçoivent ``FakeHost`` comme
``exec_``. Git, lui, est le vrai (dans ``tmp_path``) : ce que les gardes disent d'une branche se vérifie sur git.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from grimoire.missions.launcher import ExecResult


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    """Un dépôt ``projet`` sur ``main`` avec une remote ``origin`` locale et nue."""
    root = tmp_path / "projet"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("projet\n", encoding="utf-8")
    git(root, "add", "--", "README.md")
    git(root, "commit", "-q", "-m", "init")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    return root


class FakeHost:
    """Un faux ``claude`` et un faux ``gh`` : enregistre chaque appel, répond selon des tables."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, Mapping[str, str] | None]] = []
        self.bg_fails = False
        self.stop_result = ExecResult(0, "stopped\n")
        self.prs: dict[str, str] = {}  # branche → état (MERGED, OPEN, CLOSED) ; absente : aucune PR
        self.gh_broken = False
        self.closed_issues: list[str] = []
        self._next = 0

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
        self.calls.append((list(argv), cwd, env))
        if argv[0] == "claude":
            return self._claude(list(argv))
        if argv[0] == "gh":
            return self._gh(list(argv))
        return ExecResult(127, "", f"{argv[0]} : faux binaire inconnu")

    def _claude(self, argv: list[str]) -> ExecResult:
        if argv[1] == "--bg":
            if self.bg_fails:
                return ExecResult(1, "", "claude : échec simulé")
            self._next += 1
            return ExecResult(0, f"backgrounded · {self._next:08x} (running)\n")
        if argv[1] == "stop":
            return self.stop_result
        return ExecResult(2, "", "sous-commande inconnue")

    def _gh(self, argv: list[str]) -> ExecResult:
        if self.gh_broken:
            return ExecResult(1, "", "gh : réseau indisponible")
        if argv[1:3] == ["pr", "view"]:
            state = self.prs.get(argv[3])
            if state is None:
                return ExecResult(1, "", "no pull requests found for branch")
            return ExecResult(0, json.dumps({"state": state, "number": 7}))
        if argv[1:3] == ["issue", "close"]:
            self.closed_issues.append(argv[3])
            return ExecResult(0, "")
        return ExecResult(2, "", "gh : sous-commande inconnue")

    def of(self, binary: str, *first: str) -> list[list[str]]:
        return [a for a, _c, _e in self.calls if a[0] == binary and tuple(a[1:1 + len(first)]) == first]
