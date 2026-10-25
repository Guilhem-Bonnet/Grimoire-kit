"""Les doubles de l'intendant (#876, lot 5) : un vrai git dans ``tmp_path``, un faux ``gh`` qui enregistre tout.

Git est le vrai : ce que la carte dit d'une branche (fusionnée par arbre, en conflit sur le seul CHANGELOG)
se vérifie sur git. ``gh`` est un double à tables. :class:`Recorder` est l'exécuteur injecté : il garde
chaque appel, et :meth:`Recorder.assert_read_only` refuse toute sous-commande qui écrit.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions.launcher import ExecResult, default_exec
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.steward.sources import Sources

#: Sous-commandes git qu'une lecture peut lancer (``merge-tree`` écrit ses arbres dans un magasin jetable).
READ_ONLY_GIT = frozenset({"rev-parse", "for-each-ref", "rev-list", "merge-base", "merge-tree", "log", "cat-file", "grep", "worktree"})
READ_ONLY_GH = frozenset({("pr", "list"), ("pr", "view"), ("issue", "list"), ("issue", "view")})


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


def commit_file(root: Path, rel: str, text: str, message: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(root, "add", "--", rel)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> Path:
    """``projet`` sur ``main`` (README, CHANGELOG, src/app.py), remote ``origin`` locale et nue."""
    root = tmp_path / "projet"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false")):
        git(root, "config", key, value)
    (root / "README.md").write_text("projet\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n- base\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git(root, "add", "--", "README.md", "CHANGELOG.md", "src/app.py")
    git(root, "commit", "-q", "-m", "init")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    # Le ledger vit dans le dépôt : ignoré comme dans un projet réel, il ne salit pas l'arbre.
    (root / ".git" / "info" / "exclude").write_text("_grimoire-runtime-output/\n", encoding="utf-8")
    return root


class FakeGh:
    """Un faux ``gh`` : PR et issues ouvertes, vues par numéro, PR fusionnées — selon des tables."""

    def __init__(self) -> None:
        self.open_prs: list[dict[str, Any]] = []
        self.merged_prs: list[dict[str, Any]] = []
        self.open_issues: list[dict[str, Any]] = []
        self.issues: dict[int, dict[str, Any]] = {}  # issue view
        self.prs: dict[int, dict[str, Any]] = {}  # pr view

    def __call__(self, argv: list[str]) -> ExecResult:
        sub = tuple(argv[1:3])
        if sub == ("pr", "list"):
            state = argv[argv.index("--state") + 1]
            return ExecResult(0, json.dumps(self.open_prs if state == "open" else self.merged_prs))
        if sub == ("issue", "list"):
            return ExecResult(0, json.dumps(self.open_issues))
        if sub in (("issue", "view"), ("pr", "view")):
            table = self.issues if sub[0] == "issue" else self.prs
            found = table.get(int(argv[3]))
            if found is None:
                return ExecResult(1, "", f"GraphQL: Could not resolve to an issue or pull request with the number of {argv[3]}.")
            return ExecResult(0, json.dumps(found))
        return ExecResult(2, "", "gh : sous-commande inconnue du double")


class Recorder:
    """L'exécuteur injecté : git réel, ``gh`` doublé (ou absent), chaque appel enregistré."""

    def __init__(self, gh: FakeGh | None = None, *, gh_missing: bool = False) -> None:
        self.gh = gh or FakeGh()
        self.gh_missing = gh_missing
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
        self.calls.append(list(argv))
        if argv[0] == "git":
            return default_exec(argv, cwd, env)
        if argv[0] == "gh":
            if self.gh_missing:
                return ExecResult(127, "", "gh : binaire introuvable")
            return self.gh(list(argv))
        return ExecResult(127, "", f"{argv[0]} : binaire non prévu par le double")

    def assert_read_only(self) -> None:
        assert self.calls, "aucun appel enregistré : le double n'a pas été branché"
        for argv in self.calls:
            if argv[0] == "git":
                assert argv[1] == "-C", argv
                sub = argv[3]
                assert sub in READ_ONLY_GIT, f"git {sub} n'est pas une lecture : {argv}"
                if sub == "worktree":
                    assert argv[4] == "list", argv
            else:
                assert argv[0] == "gh", argv
                assert tuple(argv[1:3]) in READ_ONLY_GH, f"gh en écriture : {argv}"


# ── Le décor : un dépôt réel, un ledger, un faux gh ──

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
OLD = "2026-01-01T00:00:00Z"
RECENT = "2026-10-25T11:00:00Z"


@dataclass
class Scene:
    root: Path
    gh: FakeGh
    tasks: dict[str, str] = field(default_factory=dict)
    shas: dict[str, str] = field(default_factory=dict)

    def sources(self, recorder: Recorder | None = None, *, alive: bool = True) -> Sources:
        return Sources(
            self.root,
            exec_=recorder or Recorder(self.gh),
            probe=(lambda: {"S1": "busy"}) if alive else dict,
            now=NOW,
            target="origin/main",
        )


def _branch(root: Path, name: str, rel: str, text: str, message: str) -> str:
    git(root, "checkout", "-q", "-b", name, "main")
    sha = commit_file(root, rel, text, message)
    git(root, "checkout", "-q", "main")
    return sha


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    root = make_repo(tmp_path)
    ledger = TaskService(root).ledger
    mission = ledger.create_mission(title="Intendant", origin="test")
    tasks = {
        "issue_fermee": ledger.create_task(mission.id, "Corriger le rendu (#12)", acceptance=("rendu juste",)).id,
        "citee": ledger.create_task(mission.id, "Tâche citée par un commit", acceptance=("ok",)).id,
        "tenue": ledger.create_task(mission.id, "Tâche tenue par une session", acceptance=("ok",)).id,
        "annulee": ledger.create_task(mission.id, "Tâche annulée", acceptance=("ok",)).id,
    }
    ledger.transition_task(tasks["tenue"], TaskState.READY)
    ledger.claim_task(tasks["tenue"], "agent", "local")
    ledger.attach_session(tasks["tenue"], "S1", session_host="claude-code")
    ledger.transition_task(tasks["annulee"], TaskState.CANCELLED)

    shas: dict[str, str] = {}
    shas["ancetre"] = _branch(root, "feat/ancetre", "a.txt", "a\n", "feat: a")
    git(root, "merge", "-q", "--ff-only", "feat/ancetre")
    shas["squash"] = _branch(root, "feat/squash", "b.txt", "b\n", "feat: b")
    git(root, "merge", "-q", "--squash", "feat/squash")
    git(root, "commit", "-q", "-m", "feat: b (squash)")
    shas["squash_commit"] = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "feat/changelog", "main")
    commit_file(root, "d.txt", "d\n", "feat: d")
    shas["changelog"] = commit_file(
        root, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- entrée de la branche\n", "docs: changelog d"
    )
    git(root, "checkout", "-q", "main")
    shas["semantique"] = _branch(root, "feat/semantique", "src/app.py", "def f():\n    return 2\n", "fix: 2")
    commit_file(root, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- entrée de main\n", "docs: main")
    commit_file(root, "src/app.py", "def f():\n    return 3\n", "fix: 3")
    shas["cite"] = commit_file(root, "c.txt", "c\n", f"feat: travail ({tasks['citee']})")
    shas["orpheline"] = _branch(root, f"task/{tasks['annulee']}", "e.txt", "e\n", "wip")
    shas["tenue"] = _branch(root, f"task/{tasks['tenue']}", "f.txt", "f\n", "wip")
    shas["pr_fusionnee"] = _branch(root, "feat/pr-fusionnee", "g.txt", "g\n", "feat: g")
    shas["hors_cible"] = _branch(root, "feat/hors-cible", "h.txt", "h\n", "feat: h")
    git(root, "push", "-q", "origin", "--all")
    git(root, "fetch", "-q", "origin")

    gh = FakeGh()

    def pr(number: int, head: str, updated: str, title: str = "") -> dict[str, object]:
        return {
            "number": number,
            "title": title or f"PR {head}",
            "headRefName": head,
            "headRefOid": git(root, "rev-parse", f"refs/heads/{head}"),
            "baseRefName": "main",
            "author": {"login": "app/claude-ops"},
            "createdAt": OLD,
            "updatedAt": updated,
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}],
            "url": f"https://example.invalid/pull/{number}",
            "state": "OPEN",
            "mergedAt": None,
            "mergeCommit": None,
        }

    gh.open_prs = [
        pr(30, f"task/{tasks['tenue']}", OLD),
        pr(31, "feat/changelog", OLD),
        pr(32, "feat/semantique", RECENT),
    ]
    gh.prs = {p["number"]: p for p in gh.open_prs}  # type: ignore[misc]
    gh.prs[40] = {"number": 40, "state": "MERGED", "mergedAt": RECENT, "mergeCommit": {"oid": shas["squash_commit"]}}
    gh.prs[41] = {"number": 41, "state": "MERGED", "mergedAt": RECENT, "mergeCommit": {"oid": shas["hors_cible"]}}
    gh.merged_prs = [{"number": 40, "headRefOid": shas["pr_fusionnee"]}]
    gh.open_issues = [{"number": 50, "title": "À faire", "labels": [{"name": "bug"}], "updatedAt": RECENT, "url": "u"}]
    gh.issues = {
        12: {"number": 12, "state": "CLOSED", "closedAt": RECENT, "updatedAt": RECENT, "title": "rendu"},
        50: {"number": 50, "state": "OPEN", "closedAt": None, "updatedAt": RECENT, "title": "À faire"},
    }
    return Scene(root, gh, tasks, shas)
