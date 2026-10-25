"""Les doubles des lots 2 à 4 de #876 : un faux ``claude`` et un faux ``gh``, plus un vrai dépôt git avec une remote nue.

Aucun test n'appelle le vrai ``claude`` ni le vrai ``gh`` : le lanceur, le janitor, le relecteur et le merger reçoivent
``FakeHost`` comme ``exec_``. Git, lui, est le vrai (dans ``tmp_path``) : ce que les gardes disent d'une branche se vérifie
sur git. Le faux ``gh pr merge`` (lot 4) fait ce que GitHub ferait : un commit squash poussé sur ``main`` de la remote.
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
    git(root, "remote", "set-head", "origin", "main")  # origin/HEAD : la branche par défaut du remote
    return root


class FakeHost:
    """Un faux ``claude`` et un faux ``gh`` : enregistre chaque appel, répond selon des tables."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, Mapping[str, str] | None]] = []
        self.bg_fails = False
        self.stop_result = ExecResult(0, "stopped\n")
        self.prs: dict[str, str] = {}  # branche → état (MERGED, OPEN, CLOSED) ; absente : aucune PR
        self.pr_heads: dict[str, str] = {}  # branche → headRefOid ; absente : la tête locale de la branche
        self.pr_no_head = False  # JSON partiel : ni headRefOid
        self.gh_broken = False
        self.closed_issues: list[str] = []
        self._next = 0
        # Lot 4 (merger) : checks, protection de branche et fusion de la PR.
        self.checks: dict[str, list[dict[str, str]]] = {}  # branche → checks ``{name, bucket}`` ; absente : aucun check
        self.required: dict[str, list[dict[str, str]]] = {}  # branche → checks requis ; absente : aucune protection
        self.pr_numbers: dict[str, int] = {}  # branche → numéro ; absente : 7
        self.pr_bases: dict[str, str] = {}  # branche → cible ; absente : main
        self.merge_commits: dict[str, str] = {}  # branche → commit de fusion, posé par ``pr merge``
        self.merge_lands = True  # False : ``pr merge`` sort en 0 sans rien fusionner (file de fusion, réglage du dépôt)
        self.merge_fails = False
        self.broken_after_merge = False  # gh tombe juste après la fusion : le constat se fera au tick suivant

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
        self.calls.append((list(argv), cwd, env))
        if argv[0] == "claude":
            return self._claude(list(argv))
        if argv[0] == "gh":
            return self._gh(list(argv), cwd)
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

    def _gh(self, argv: list[str], cwd: Path) -> ExecResult:
        if self.gh_broken:
            return ExecResult(1, "", "gh : réseau indisponible")
        if argv[1:3] == ["pr", "view"]:
            branch = argv[3]
            state = self.prs.get(branch)
            if state is None:
                return ExecResult(1, "", "no pull requests found for branch")
            body: dict[str, object] = {
                "state": state, "number": self.pr_numbers.get(branch, 7), "baseRefName": self.pr_bases.get(branch, "main"),
                "isDraft": False, "mergeCommit": {"oid": self.merge_commits[branch]} if branch in self.merge_commits else None,
            }
            if not self.pr_no_head:
                body["headRefOid"] = self.pr_heads.get(branch) or git(cwd, "rev-parse", f"refs/heads/{branch}")
            return ExecResult(0, json.dumps(body))
        if argv[1:3] == ["pr", "checks"]:
            table = self.required if "--required" in argv else self.checks
            rows = table.get(argv[3])
            if not rows:
                return ExecResult(1, "", "no required checks reported" if "--required" in argv else "no checks reported")
            buckets = {r["bucket"] for r in rows}
            return ExecResult(1 if buckets & {"fail", "cancel"} else 8 if "pending" in buckets else 0, json.dumps(rows))
        if argv[1:3] == ["pr", "merge"]:
            return self._merge(argv, cwd)
        if argv[1:3] == ["issue", "close"]:
            self.closed_issues.append(argv[3])
            return ExecResult(0, "")
        return ExecResult(2, "", "gh : sous-commande inconnue")

    def _merge(self, argv: list[str], cwd: Path) -> ExecResult:
        """Squash de la tête de la PR sur ``main`` de la remote, comme GitHub ; ``--match-head-commit`` est tenu."""
        number = int(argv[3])
        branch = next((b for b in self.prs if self.pr_numbers.get(b, 7) == number and self.prs[b] == "OPEN"), "")
        if self.merge_fails or not branch:
            return ExecResult(1, "", "gh : fusion refusée (simulée)")
        head = self.pr_heads.get(branch) or git(cwd, "rev-parse", f"refs/heads/{branch}")
        expected = argv[argv.index("--match-head-commit") + 1] if "--match-head-commit" in argv else ""
        if expected and expected != head:
            return ExecResult(1, "", "head commit mismatch")
        if not self.merge_lands:
            return ExecResult(0, "")
        git(cwd, "fetch", "-q", "origin", "main")
        target = git(cwd, "rev-parse", "refs/remotes/origin/main")
        tree = subprocess.run(["git", "-C", str(cwd), "merge-tree", "--write-tree", target, head],
                              capture_output=True, text=True, encoding="utf-8", check=False)
        if tree.returncode != 0:
            return ExecResult(1, "", "Pull request is not mergeable: conflicts")
        commit = git(cwd, "commit-tree", tree.stdout.split()[0], "-p", target, "-m", f"{branch} (squash)")
        git(cwd, "push", "-q", "origin", f"{commit}:refs/heads/main")
        self.prs[branch], self.pr_heads[branch], self.merge_commits[branch] = "MERGED", head, commit
        if self.broken_after_merge:
            self.gh_broken = True
        return ExecResult(0, "")

    def of(self, binary: str, *first: str) -> list[list[str]]:
        return [a for a, _c, _e in self.calls if a[0] == binary and tuple(a[1:1 + len(first)]) == first]
