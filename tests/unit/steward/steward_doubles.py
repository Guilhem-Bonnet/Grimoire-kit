"""Les doubles de l'intendant (#876, lot 5) : un vrai git dans ``tmp_path``, un faux ``gh`` qui enregistre tout.

Git est le vrai : ce que la carte dit d'une branche (fusionnée, vide, en conflit sur le seul CHANGELOG)
se vérifie sur git. ``gh`` est un double à tables. :class:`Recorder` est l'exécuteur injecté : il garde
chaque appel, et :meth:`Recorder.assert_read_only` refuse toute sous-commande qui écrit.
"""

from __future__ import annotations

import json
import os
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
READ_ONLY_GIT = frozenset(
    {"rev-parse", "for-each-ref", "rev-list", "merge-base", "merge-tree", "log", "cat-file", "grep", "worktree", "status"}
)
READ_ONLY_GH = frozenset({("pr", "list"), ("pr", "view"), ("issue", "list"), ("issue", "view")})

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
OLD = "2026-01-01T00:00:00Z"
RECENT = "2026-10-25T11:00:00Z"


def git(cwd: Path, *args: str, date: str = "") -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else None
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", env=env)
    if done.returncode != 0:  # le stderr dans l'échec : un décor qui casse en CI doit dire pourquoi
        raise AssertionError(f"git {' '.join(args)} : code {done.returncode}\n{done.stderr}")
    return done.stdout.strip()


def commit_file(root: Path, rel: str, text: str | None, message: str, *, date: str = "") -> str:
    """Écrit (ou, avec ``text=None``, retire) *rel* et committe ; rend le SHA."""
    path = root / rel
    if text is None:
        git(root, "rm", "-q", "--", rel)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(root, "add", "--", rel)
    git(root, "commit", "-q", "-m", message, date=date)
    return git(root, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> Path:
    """``projet`` sur ``main`` (README, CHANGELOG, src/app.py), remote ``origin`` locale et nue."""
    root = tmp_path / "projet"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"), ("commit.gpgsign", "false"), ("gc.auto", "0"), ("maintenance.auto", "false")):
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
    (root / ".git" / "info" / "exclude").write_text("_grimoire-runtime-output/\n_grimoire-output/.runs/\n", encoding="utf-8")
    return root


class FakeGh:
    """Un faux ``gh`` : PR et issues ouvertes, vues par numéro, PR fusionnées — selon des tables."""

    def __init__(self) -> None:
        self.open_prs: list[dict[str, Any]] = []
        self.merged_prs: list[dict[str, Any]] = []
        self.open_issues: list[dict[str, Any]] = []
        self.issues: dict[int, dict[str, Any]] = {}  # issue view (une PR y répond aussi, comme le vrai gh)
        self.prs: dict[int, dict[str, Any]] = {}  # pr view

    def __call__(self, argv: list[str]) -> ExecResult:
        sub = tuple(argv[1:3])
        if sub == ("pr", "list"):
            state = argv[argv.index("--state") + 1]
            limit = int(argv[argv.index("--limit") + 1])
            return ExecResult(0, json.dumps((self.open_prs if state == "open" else self.merged_prs)[:limit]))
        if sub == ("issue", "list"):
            return ExecResult(0, json.dumps(self.open_issues[: int(argv[argv.index("--limit") + 1])]))
        if sub in (("issue", "view"), ("pr", "view")):
            number = int(argv[3])
            found = self.prs.get(number) if sub[0] == "pr" else self.issues.get(number) or self.prs.get(number)
            if found is None:
                return ExecResult(1, "", f"GraphQL: Could not resolve to an issue or pull request with the number of {number}.")
            return ExecResult(0, json.dumps(found))
        return ExecResult(2, "", "gh : sous-commande inconnue du double")


class Recorder:
    """L'exécuteur injecté : git réel, ``gh`` doublé (ou absent), chaque appel enregistré avec son environnement."""

    def __init__(self, gh: FakeGh | None = None, *, gh_missing: bool = False) -> None:
        self.gh = gh or FakeGh()
        self.gh_missing = gh_missing
        self.calls: list[list[str]] = []
        self.envs: list[Mapping[str, str] | None] = []

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
        self.calls.append(list(argv))
        self.envs.append(env)
        if argv[0] == "git":
            return default_exec(argv, cwd, env)
        if argv[0] == "gh":
            if self.gh_missing:
                return ExecResult(127, "", "gh : binaire introuvable")
            return self.gh(list(argv))
        return ExecResult(127, "", f"{argv[0]} : binaire non prévu par le double")

    def assert_read_only(self) -> None:
        assert self.calls, "aucun appel enregistré : le double n'a pas été branché"
        for argv, env in zip(self.calls, self.envs, strict=True):
            if argv[0] == "git":
                assert argv[1] == "-C", argv
                sub = argv[3]
                assert sub in READ_ONLY_GIT, f"git {sub} n'est pas une lecture : {argv}"
                if sub == "worktree":
                    assert argv[4] == "list", argv
                if sub == "status":  # sans verrou optionnel, git status ne réécrit pas l'index
                    assert (env or {}).get("GIT_OPTIONAL_LOCKS") == "0", argv
            else:
                assert argv[0] == "gh", argv
                assert tuple(argv[1:3]) in READ_ONLY_GH, f"gh en écriture : {argv}"


# ── Le décor : un dépôt réel, un ledger, un faux gh ──


@dataclass
class Scene:
    root: Path
    gh: FakeGh
    tasks: dict[str, str] = field(default_factory=dict)
    shas: dict[str, str] = field(default_factory=dict)
    worktree: Path | None = None

    def sources(self, recorder: Recorder | None = None, *, alive: bool = True) -> Sources:
        return Sources(
            self.root,
            exec_=recorder or Recorder(self.gh),
            probe=(lambda: {"S1": "busy"}) if alive else dict,
            now=NOW,
            target="origin/main",
        )


def _branch(root: Path, name: str, rel: str, text: str | None, message: str, *, date: str = "") -> str:
    git(root, "checkout", "-q", "-b", name, "main")
    sha = commit_file(root, rel, text, message, date=date)
    git(root, "checkout", "-q", "main")
    return sha


def _pr(root: Path, number: int, head: str, updated: str, *, title: str = "", body: str = "") -> dict[str, Any]:
    return {
        "number": number,
        "title": title or f"PR {head}",
        "body": body,
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


def _journal(root: Path, session_id: str, task_id: str, *, ended: bool) -> None:
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    data = {"session_id": session_id, "task_id": task_id, "started_at": OLD, "updated_at": OLD}
    if ended:
        data["ended_at"] = OLD
    (runs / f"session-{session_id}.json").write_text(json.dumps(data), encoding="utf-8")


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
        "morte": ledger.create_task(mission.id, "Tâche dont la session est finie", acceptance=("ok",)).id,
        "sans_session": ledger.create_task(mission.id, "Tâche réclamée sans session", acceptance=("ok",)).id,
    }
    for key, session in (("tenue", "S1"), ("morte", "S2"), ("sans_session", "")):
        ledger.transition_task(tasks[key], TaskState.READY)
        ledger.claim_task(tasks[key], "agent", "local")
        if session:
            ledger.attach_session(tasks[key], session, session_host="claude-code")
    _journal(root, "S2", tasks["morte"], ended=True)
    ledger.transition_task(tasks["annulee"], TaskState.CANCELLED)

    shas: dict[str, str] = {}
    shas["init"] = git(root, "rev-parse", "HEAD")
    shas["vide"] = shas["init"]
    git(root, "branch", "feat/vide", "main")
    shas["ancetre"] = _branch(root, "feat/ancetre", "a.txt", "a\n", "feat: a")
    git(root, "merge", "-q", "--ff-only", "feat/ancetre")
    # Revient à l'état initial : son arbre est celui d'un commit ancien de la cible.
    shas["retour"] = _branch(root, "feat/retour", "a.txt", None, "revert: a")
    shas["squash"] = _branch(root, "feat/squash", "b.txt", "b\n", "feat: b")
    git(root, "merge", "-q", "--squash", "feat/squash")
    git(root, "commit", "-q", "-m", "feat: b (squash)")
    shas["squash_commit"] = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "feat/changelog", "main")
    commit_file(root, "d.txt", "d\n", "feat: d", date=OLD)
    shas["changelog"] = commit_file(
        root, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- entrée de la branche\n", "docs: changelog d", date=OLD
    )
    git(root, "checkout", "-q", "main")
    shas["semantique"] = _branch(root, "feat/semantique", "src/app.py", "def f():\n    return 2\n", "fix: 2")
    commit_file(root, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- entrée de main\n", "docs: main")
    commit_file(root, "src/app.py", "def f():\n    return 3\n", "fix: 3")
    commit_file(root, "retire.txt", "à retirer\n", "feat: retire")
    commit_file(root, "retire.txt", None, "chore: retrait")
    shas["cite"] = commit_file(root, "c.txt", "c\n", f"feat: travail ({tasks['citee']})")
    shas["orpheline"] = _branch(root, f"task/{tasks['annulee']}", "e.txt", "e\n", "wip")
    shas["tenue"] = _branch(root, f"task/{tasks['tenue']}", "f.txt", "f\n", "wip")
    shas["morte"] = _branch(root, f"task/{tasks['morte']}", "m.txt", "m\n", "wip", date=OLD)
    shas["pr_fusionnee"] = _branch(root, "feat/pr-fusionnee", "g.txt", "g\n", "feat: g")
    shas["empilee"] = _branch(root, "feat/empilee", "k.txt", "k\n", "feat: k")
    shas["fusion_ailleurs"] = _branch(root, "feat/fusion-ailleurs", "l.txt", "l\n", "feat: l")
    shas["hors_cible"] = _branch(root, "feat/hors-cible", "h.txt", "h\n", "feat: h")
    shas["sale"] = _branch(root, "feat/sale", "s.txt", "s\n", "feat: s", date=OLD)
    git(root, "branch", "deadbeef1", "feat/hors-cible")  # une branche qui porte le nom d'un SHA
    git(root, "push", "-q", "origin", "--all")
    git(root, "fetch", "-q", "origin")
    worktree = root.parent / "wt-sale"
    git(root, "worktree", "add", "-q", str(worktree), "feat/sale")
    (worktree / "non-commite.txt").write_text("travail en cours\n", encoding="utf-8")

    gh = FakeGh()
    gh.open_prs = [
        _pr(root, 30, f"task/{tasks['tenue']}", OLD),
        _pr(root, 31, "feat/changelog", OLD),
        _pr(root, 32, "feat/semantique", RECENT),
        _pr(root, 33, f"task/{tasks['morte']}", OLD),
        _pr(root, 35, "feat/sale", OLD),
    ]
    absent = _pr(root, 34, "feat/hors-cible", OLD)
    absent["headRefOid"] = "f" * 40  # une tête jamais récupérée localement
    gh.open_prs.append(absent)
    gh.prs = {p["number"]: p for p in gh.open_prs}
    merged = {"state": "MERGED", "mergedAt": RECENT, "baseRefName": "main", "body": "", "updatedAt": RECENT}
    gh.prs[40] = {
        **merged,
        "number": 40,
        "title": "feat: g",
        "body": f"Livre {tasks['issue_fermee']}, Closes #13",
        "headRefName": "feat/pr-fusionnee",
        "headRefOid": shas["pr_fusionnee"],
        "mergeCommit": {"oid": shas["squash_commit"]},
    }
    gh.prs[41] = {**merged, "number": 41, "title": "x", "headRefName": "feat/fusion-ailleurs", "mergeCommit": {"oid": shas["hors_cible"]}}
    gh.prs[42] = {**merged, "number": 42, "title": "x", "baseRefName": "feat/other", "mergeCommit": {"oid": shas["squash_commit"]}}
    gh.prs[43] = {"number": 43, "state": "CLOSED", "mergedAt": None, "title": "fermée sans fusion", "baseRefName": "main", "mergeCommit": None}
    gh.merged_prs = [
        {"number": 40, "headRefOid": shas["pr_fusionnee"], "baseRefName": "main", "mergeCommit": {"oid": shas["squash_commit"]}},
        {"number": 41, "headRefOid": shas["fusion_ailleurs"], "baseRefName": "main", "mergeCommit": {"oid": shas["hors_cible"]}},
        {"number": 42, "headRefOid": shas["empilee"], "baseRefName": "feat/other", "mergeCommit": {"oid": shas["squash_commit"]}},
    ]
    gh.open_issues = [
        {"number": 50, "title": "À faire", "labels": [{"name": "bug"}], "updatedAt": RECENT, "url": "u"},
        {"number": 11, "title": "Autre", "labels": [], "updatedAt": RECENT, "url": "u"},
    ]

    def issue(number: int, state: str, reason: str = "") -> dict[str, Any]:
        return {"number": number, "state": state, "stateReason": reason, "closedAt": RECENT if state != "OPEN" else None,
                "updatedAt": RECENT, "title": f"issue {number}"}

    gh.issues = {
        5: issue(5, "CLOSED", "COMPLETED"),
        11: issue(11, "OPEN"),
        12: issue(12, "CLOSED", "COMPLETED"),
        13: issue(13, "CLOSED", "COMPLETED"),
        50: issue(50, "OPEN"),
        60: issue(60, "CLOSED", "NOT_PLANNED"),
        61: issue(61, "OPEN"),
    }
    return Scene(root, gh, tasks, shas, worktree)
