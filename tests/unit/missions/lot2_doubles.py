"""Les doubles des lots 2 à 4 de #876 : un faux ``claude`` et un faux ``gh``, plus un vrai dépôt git avec une remote nue.

Aucun test n'appelle le vrai ``claude`` ni le vrai ``gh`` : le lanceur, le janitor, le relecteur et le merger reçoivent
``FakeHost`` comme ``exec_``. Git, lui, est le vrai (dans ``tmp_path``) : ce que les gardes disent d'une branche se vérifie
sur git. Le faux ``gh pr merge`` (lot 4) fait ce que GitHub ferait : un commit squash poussé sur ``main`` de la remote.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
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
        #: ``claude agents --json`` ; ``None`` : les sessions ``--bg`` que ce double a lancées et pas arrêtées.
        self.agents: list[dict[str, str]] | None = None
        self.agents_fail = False  # ``claude agents --json`` sort en erreur : la lecture échoue
        #: Toutes les sessions de fond lancées, comme ``claude agents --json`` les décrit (#966 : ``sessionId`` ≠ ``--session-id``).
        self.bg: list[dict[str, str]] = []
        self.stopped: set[str] = set()  # ids courts arrêtés par ``claude stop`` : ``claude agents`` ne les liste plus
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
        self.merge_error = "Pull request is not mergeable: review required"  # le refus du dépôt quand merge_fails
        self.required_broken = False  # ``gh pr checks --required`` en panne (réseau), pas « aucun check requis »
        self.broken_after_merge = False
        self.cross_repo: set[str] = set()  # branches dont la PR vient d'un fork
        #: La remote que GitHub représente (un dépôt nu) : ce que dit gh ne suit jamais la configuration locale du worker.
        self.real: Path | None = None
        self.pr_files: dict[str, list[str]] = {}  # branche → fichiers annoncés ; absente : le diff base...tête sur la remote
        self.changed_files: dict[str, int] = {}  # branche → changedFiles annoncé ; absent : le nombre d'entrées listées
        # #993 : brouillon, base que GitHub n'a pas rafraîchie, conflit annoncé.
        self.drafts: set[str] = set()  # branches dont la PR est en brouillon ; ``gh pr ready`` l'en sort
        self.ready_fails = False  # ``gh pr ready`` refusé (droits de l'intégration)
        self.pr_base_oids: dict[str, str] = {}  # branche → baseRefOid annoncé ; absente : la cible de la remote
        self.mergeable: dict[str, str] = {}  # branche → ``mergeable`` (MERGEABLE, CONFLICTING) ; absente : non dit
        self.merge_states: dict[str, str] = {}  # branche → ``mergeStateStatus`` (CLEAN, BEHIND, DIRTY) ; absente : non dit
        self.repo = ""  # le dépôt GitHub que ce faux gh sert : un ``-R`` autre est refusé (« could not resolve »)  # gh tombe juste après la fusion : le constat se fera au tick suivant

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
        self.calls.append((list(argv), cwd, env))
        if argv[0] == "claude":
            return self._claude(list(argv), cwd)
        if argv[0] == "gh":
            return self._gh(list(argv), cwd)
        return ExecResult(127, "", f"{argv[0]} : faux binaire inconnu")

    def _claude(self, argv: list[str], cwd: Path) -> ExecResult:
        if argv[1] == "--bg":
            if self.bg_fails:
                return ExecResult(1, "", "claude : échec simulé")
            self._next += 1
            short = f"{self._next:08x}"
            # Comme Claude Code 2.1.310 : ``--session-id`` est ignoré, l'identité commence par l'id court imprimé ;
            # une reprise (``--resume <id>``) garde l'identité reprise.
            resumed = argv[argv.index("--resume") + 1] if "--resume" in argv else ""
            name = argv[argv.index("--name") + 1] if "--name" in argv else ""
            self.bg.append({
                "pid": str(4000 + self._next), "id": short, "cwd": str(cwd), "kind": "background",
                "startedAt": "2026-10-26T00:00:00Z", "sessionId": resumed or short + str(uuid.uuid4())[8:],
                "name": name, "status": "busy", "state": "running",
            })
            return ExecResult(0, f"backgrounded · {short} (running)\n")
        if argv[1] == "stop":
            if self.stop_result.returncode == 0:
                self.stopped.add(argv[2])
            return self.stop_result
        if argv[1:3] == ["agents", "--json"]:
            if self.agents_fail:
                return ExecResult(1, "", "claude agents : échec simulé")
            live = [s for s in self.bg if s["id"] not in self.stopped]
            return ExecResult(0, json.dumps(live if self.agents is None else self.agents))
        return ExecResult(2, "", "sous-commande inconnue")

    def session_environ(self, name: str) -> dict[str, str]:
        """L'environnement que voit la session de fond *name*, comme sous Claude Code 2.1.310 (#966).

        Celui du démon, c'est-à-dire celui du **premier** ``claude --bg`` lancé, quelle que soit la session ; plus
        ``CLAUDE_CODE_SESSION_ID``, que l'hôte exporte avec le vrai identifiant de la session.
        """
        daemon = next((dict(e or {}) for a, _c, e in self.calls if a[:2] == ["claude", "--bg"]), {})
        return {**daemon, "CLAUDE_CODE_SESSION_ID": self.session_of(name)}

    def session_of(self, name: str) -> str:
        """Le ``sessionId`` réel de la dernière session de fond lancée sous ce nom (``grimoire-<id>``, ``grimoire-review-<id>``).

        Un nom qui finit par ``-`` est un préfixe (``grimoire-review-`` : le dernier relecteur lancé, quelle que soit sa tâche).
        """
        return next(
            s["sessionId"] for s in reversed(self.bg) if s["name"] == name or (name.endswith("-") and s["name"].startswith(name))
        )

    def _gh(self, argv: list[str], cwd: Path) -> ExecResult:
        if self.gh_broken:
            return ExecResult(1, "", "gh : réseau indisponible")
        if "-R" in argv:
            target = argv[argv.index("-R") + 1]
            if self.repo and target != self.repo:
                return ExecResult(1, "", f"GraphQL: Could not resolve to a Repository with the name '{target}'.")
            argv = argv[:argv.index("-R")] + argv[argv.index("-R") + 2:]
        if argv[1] == "api" and argv[2].endswith("/files"):
            return self._api_files(argv, cwd)
        if argv[1:3] == ["pr", "view"]:
            branch = argv[3]
            state = self.prs.get(branch)
            if state is None:
                return ExecResult(1, "", "no pull requests found for branch")
            body: dict[str, object] = {
                "state": state, "number": self.pr_numbers.get(branch, 7), "baseRefName": self.pr_bases.get(branch, "main"),
                "isDraft": branch in self.drafts, "mergeCommit": {"oid": self.merge_commits[branch]} if branch in self.merge_commits else None,
                "isCrossRepository": branch in self.cross_repo,
            }
            remote = self._remote(cwd)
            head = self.pr_heads.get(branch) or self._branch_head(cwd, branch)
            base = self._git_remote(remote, "rev-parse", f"refs/heads/{self.pr_bases.get(branch, 'main')}")
            entries = self._entries(cwd, branch, head, base)
            body.update({
                "baseRefOid": self.pr_base_oids.get(branch, base), "files": [{"path": new} for new, _old in entries],  # comme gh : le nouveau nom seul
                "changedFiles": self.changed_files.get(branch, len(entries)),
                "url": f"file://{remote}/pull/{body['number']}",
            })
            if not self.pr_no_head:
                body["headRefOid"] = head
            if branch in self.mergeable:
                body["mergeable"] = self.mergeable[branch]
            if branch in self.merge_states:
                body["mergeStateStatus"] = self.merge_states[branch]
            return ExecResult(0, json.dumps(body))
        if argv[1:3] == ["pr", "checks"]:
            if "--required" in argv and self.required_broken:
                return ExecResult(1, "", "error connecting to api.github.com")
            table = self.required if "--required" in argv else self.checks
            rows = table.get(argv[3])
            if not rows:
                return ExecResult(1, "", "no required checks reported" if "--required" in argv else "no checks reported")
            buckets = {r["bucket"] for r in rows}
            return ExecResult(1 if buckets & {"fail", "cancel"} else 8 if "pending" in buckets else 0, json.dumps(rows))
        if argv[1:3] == ["pr", "merge"]:
            return self._merge(argv, cwd)
        if argv[1:3] == ["pr", "ready"]:
            number = int(argv[3])
            branch = next((b for b in self.prs if self.pr_numbers.get(b, 7) == number and self.prs[b] == "OPEN"), "")
            if self.ready_fails or not branch:
                return ExecResult(1, "", "GraphQL: Resource not accessible by integration (markPullRequestReadyForReview)")
            self.drafts.discard(branch)
            return ExecResult(0, "")
        if argv[1:3] == ["issue", "close"]:
            self.closed_issues.append(argv[3])
            return ExecResult(0, "")
        return ExecResult(2, "", "gh : sous-commande inconnue")

    def _entries(self, cwd: Path, branch: str, head: str, base: str) -> list[tuple[str, str | None]]:
        """``(filename, previous_filename)`` comme l'API REST : un renommage garde son ancien nom."""
        given = self.pr_files.get(branch)
        if given is not None:
            return [(f, None) if isinstance(f, str) else (f[0], f[1]) for f in given]
        if not head or not base:
            return []
        raw = self._git_remote(self._remote(cwd), "diff", "--name-status", "-z", "-M", f"{base}...{head}").split("\0")
        out: list[tuple[str, str | None]] = []
        i = 0
        while i < len(raw) and raw[i]:
            status = raw[i]
            if status[:1] in ("R", "C"):
                out.append((raw[i + 2], raw[i + 1]))
                i += 3
            else:
                out.append((raw[i + 1], None))
                i += 2
        return out

    def _api_files(self, argv: list[str], cwd: Path) -> ExecResult:
        """``gh api repos/<o>/<r>/pulls/<n>/files --paginate --jq '.[] | [.filename, .previous_filename]'``."""
        parts = argv[2].split("/")
        if self.repo and "/".join(parts[1:3]) != self.repo:
            return ExecResult(1, "", "gh: Not Found (HTTP 404)")
        number = int(parts[4])
        branch = next((b for b in self.prs if self.pr_numbers.get(b, 7) == number), "")
        if not branch:
            return ExecResult(1, "", "gh: Not Found (HTTP 404)")
        remote = self._remote(cwd)
        head = self.pr_heads.get(branch) or self._branch_head(cwd, branch)
        base = self._git_remote(remote, "rev-parse", f"refs/heads/{self.pr_bases.get(branch, 'main')}")
        return ExecResult(0, "".join(json.dumps([new, old]) + "\n" for new, old in self._entries(cwd, branch, head, base)))

    def _remote(self, cwd: Path) -> Path:
        return self.real or Path(git(cwd, "remote", "get-url", "origin"))

    @staticmethod
    def _git_remote(remote: Path, *args: str) -> str:
        done = subprocess.run(["git", f"--git-dir={remote}", *args], capture_output=True, text=True, encoding="utf-8", check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    def _branch_head(self, cwd: Path, branch: str) -> str:
        """La tête de la PR, comme GitHub la voit : la branche sur la remote, à défaut la branche locale."""
        found = self._git_remote(self._remote(cwd), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        return found or git(cwd, "rev-parse", f"refs/heads/{branch}")

    def _merge(self, argv: list[str], cwd: Path) -> ExecResult:
        """Squash de la tête de la PR sur ``main`` de la remote, comme GitHub ; ``--match-head-commit`` est tenu."""
        number = int(argv[3])
        branch = next((b for b in self.prs if self.pr_numbers.get(b, 7) == number and self.prs[b] == "OPEN"), "")
        if self.merge_fails or not branch:
            return ExecResult(1, "", self.merge_error if branch else "no pull request found")
        head = self.pr_heads.get(branch) or self._branch_head(cwd, branch)
        expected = argv[argv.index("--match-head-commit") + 1] if "--match-head-commit" in argv else ""
        if expected and expected != head:
            return ExecResult(1, "", "head commit mismatch")
        if not self.merge_lands:
            return ExecResult(0, "")
        remote = self._remote(cwd)  # côté serveur, comme GitHub : sur le dépôt nu, jamais dans le dépôt local
        target = self._git_remote(remote, "rev-parse", "refs/heads/main")
        tree = subprocess.run(["git", f"--git-dir={remote}", "merge-tree", "--write-tree", target, head],
                              capture_output=True, text=True, encoding="utf-8", check=False)
        if tree.returncode != 0:
            return ExecResult(1, "", "Pull request is not mergeable: conflicts")
        env = {**os.environ, "GIT_AUTHOR_NAME": "gh", "GIT_AUTHOR_EMAIL": "gh@example.invalid",
               "GIT_COMMITTER_NAME": "gh", "GIT_COMMITTER_EMAIL": "gh@example.invalid"}
        commit = subprocess.run(["git", f"--git-dir={remote}", "commit-tree", tree.stdout.split()[0], "-p", target, "-m",
                                 f"{branch} (squash)"], capture_output=True, text=True, encoding="utf-8", check=True, env=env).stdout.strip()
        subprocess.run(["git", f"--git-dir={remote}", "update-ref", "refs/heads/main", commit, target], check=True)
        self.prs[branch], self.pr_heads[branch], self.merge_commits[branch] = "MERGED", head, commit
        if self.broken_after_merge:
            self.gh_broken = True
        return ExecResult(0, "")

    def of(self, binary: str, *first: str) -> list[list[str]]:
        return [a for a, _c, _e in self.calls if a[0] == binary and tuple(a[1:1 + len(first)]) == first]
