"""Worktree éphémère par tâche et merge série vérifié sur le SHA qui sera fusionné (W5-02).

Avant ce module, ``grimoire task dispatch`` annonçait « pas de worktree
automatique » et le fanout des flows travaillait dans le même répertoire : deux
enfants écrivaient le même arbre. Ici, chaque tâche reçoit :

- **un worktree** ``task/<id>`` (la convention de branche de la fence git K3),
  créé depuis une base *épinglée* : le SHA est résolu une fois, noté au ledger
  (``task.worktree_created``) et dans le dossier git du worktree ;
- **un merge série** : un verrou par dépôt (:func:`grimoire.core.fsio.locked`),
  puis, dans cet ordre, (1) un rejeu — la branche déjà contenue dans la cible —
  rend ``already_merged`` sans rien écrire ; (2) le candidat est calculé *sans
  toucher aucun worktree* (``git merge-tree --write-tree``) : un conflit refuse,
  garde le worktree et passe la tâche en ``blocked`` ; (3) le check s'exécute
  dans un worktree de vérification détaché sur le commit candidat — le SHA qui
  sera fusionné, pas la tête de la branche ; (4) la tête de la branche et la
  cible sont relues : si l'une a bougé depuis, rien n'est fusionné ; (5) la cible
  avance par fast-forward (``merge --ff-only`` si elle est extraite, sinon
  ``update-ref`` en compare-and-swap) jusqu'au commit vérifié, et à lui seul.
- **un seul ledger** : le dossier git du worktree porte un marqueur
  (:data:`MARKER_NAME`) que :class:`grimoire.missions.service.TaskService` lit,
  pour qu'un ``grimoire task ...`` lancé dans le worktree écrive au ledger du
  projet au lieu d'en ouvrir un second sous le worktree.

Ce qu'AI-DLC fait (``aidlc-swarm.ts:305-331``) : refuser un SHA différent du SHA
contrôlé, la tête de branche. Ici, le SHA contrôlé *est* le commit de merge — le
contenu intégré, pas seulement celui de l'enfant — et la cible ne peut avancer
que jusqu'à lui.

Limites écrites : aucun push ; le check est une commande déclarée (``--check``
ou le besoin ``test-runner`` du projet), pas encore une commande approuvée par
empreinte (W3-03) — son empreinte est seulement notée au reçu ; le commit de
merge est créé avec l'identité git configurée du dépôt ; un ``core.hooksPath``
du dépôt s'applique au ``merge --ff-only`` de la cible extraite comme à tout merge.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError, GrimoireMissionError
from grimoire.core.fsio import atomic_write, locked
from grimoire.core.standard_generation import normalize_task_id
from grimoire.missions.schemas import TaskState

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = [
    "MARKER_NAME",
    "MERGED_EVENT",
    "MERGE_REFUSED_EVENT",
    "TASK_BRANCH_PREFIX",
    "WORKTREE_CREATED_EVENT",
    "MergeOutcome",
    "TaskWorktree",
    "create_task_worktree",
    "find_task_worktree",
    "home_of_worktree",
    "merge_task",
]

#: La convention de branche de la fence git (K3) : une tâche, une branche ``task/<id>``.
TASK_BRANCH_PREFIX = "task/"
#: Le marqueur posé dans le dossier git propre au worktree (jamais dans l'arbre de travail).
MARKER_NAME = "grimoire-task-worktree.json"
_MARKER_SCHEMA = "grimoire-task-worktree/v1"
WORKTREE_CREATED_EVENT = "task.worktree_created"
MERGED_EVENT = "task.merged"
MERGE_REFUSED_EVENT = "task.merge_refused"
#: Sous ``<git-common-dir>`` : worktrees de tâche, worktrees de vérification, verrou de merge.
_STATE_DIRNAME = "grimoire"
#: Les variables qui détourneraient ``git -C`` vers un autre dépôt (un hook git les exporte).
_GIT_REDIRECT_VARS = frozenset({
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX",
})
_CHECK_TAIL = 2000


@dataclass(frozen=True, slots=True)
class TaskWorktree:
    """Le worktree d'une tâche, tel que le ledger l'a noté à sa création."""

    task_id: str
    path: Path
    branch: str
    base_sha: str
    into: str

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "path": str(self.path), "branch": self.branch,
                "base_sha": self.base_sha, "into": self.into}


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    """Le verdict d'un merge : ``merged`` ou ``already_merged`` fusionnent (ou l'ont fait), le reste refuse.

    Refus : ``conflict`` (tâche bloquée, worktree gardé), ``check_red``,
    ``unevaluated`` (aucune commande de check), ``head_moved`` (la tête de la
    branche n'est pas le SHA vérifié), ``target_moved`` (la cible a bougé pendant le check).
    """

    task_id: str
    status: str
    reason: str = ""
    into: str = ""
    head_sha: str = ""
    merged_sha: str = ""
    check: dict[str, Any] = field(default_factory=dict)
    conflicts: tuple[str, ...] = ()
    worktree_kept: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"merged", "already_merged"}

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "status": self.status, "ok": self.ok, "reason": self.reason,
                "into": self.into, "head_sha": self.head_sha, "merged_sha": self.merged_sha,
                "check": dict(self.check), "conflicts": list(self.conflicts), "worktree_kept": self.worktree_kept}


# ── git ──────────────────────────────────────────────────────────────────────


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _GIT_REDIRECT_VARS}


def _git(cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False, env=env or _env(),
    )
    if check and done.returncode != 0:
        raise GrimoireMissionError(f"git {' '.join(args)} a échoué ({done.returncode}) : {(done.stderr or done.stdout).strip()}")
    return done


def _rev(repo: Path, ref: str) -> str:
    """Le SHA du commit *ref*, ``""`` s'il n'existe pas."""
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def _state_dir(repo: Path) -> Path:
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return Path(common) / _STATE_DIRNAME


def _checked_out_at(repo: Path, branch: str) -> Path | None:
    """Le worktree où *branch* est extraite, s'il y en a un."""
    current: Path | None = None
    for line in _git(repo, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{branch}" and current is not None:
            return current
    return None


# ── Worktree ─────────────────────────────────────────────────────────────────


def home_of_worktree(project_root: Path) -> tuple[Path, Path] | None:
    """``(racine du projet, racine du ledger)`` si *project_root* est un worktree de tâche, sinon ``None``.

    Aucun sous-processus : un ``.git`` *fichier* (worktree lié) renvoie au dossier
    git propre au worktree, qui porte le marqueur. Un marqueur illisible lève —
    retomber sur un ledger local ouvrirait justement le second journal.
    """
    dot_git = project_root / ".git"
    if not dot_git.is_file():
        return None
    try:
        first = dot_git.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    if not first.startswith("gitdir:"):
        return None
    gitdir = Path(first[len("gitdir:"):].strip())
    marker = (gitdir if gitdir.is_absolute() else project_root / gitdir) / MARKER_NAME
    if not marker.is_file():
        return None
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
        home, ledger = Path(raw["project_root"]), Path(raw["ledger_root"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GrimoireMissionError(f"Marqueur de worktree de tâche illisible ({marker}) : {type(exc).__name__}") from exc
    if raw.get("$schema") != _MARKER_SCHEMA or not home.is_absolute() or not ledger.is_absolute():
        raise GrimoireMissionError(f"Marqueur de worktree de tâche d'un autre schéma ({marker})")
    return home, ledger


def find_task_worktree(service: TaskService, task_id: str) -> TaskWorktree | None:
    """Le dernier worktree que le ledger a noté pour *task_id*."""
    for event in reversed(service.ledger.events_for(task_id)):
        if event.event_type == WORKTREE_CREATED_EVENT:
            p = event.payload
            return TaskWorktree(task_id, Path(str(p["path"])), str(p["branch"]), str(p["base_sha"]), str(p["into"]))
    return None


def create_task_worktree(
    service: TaskService, task_id: str, *, repo: Path, base: str = "HEAD", into: str = "",
    path: Path | None = None, actor: str = "cli",
) -> TaskWorktree:
    """Crée le worktree ``task/<id>`` de *task_id* sur *base*, épinglée à son SHA ; idempotent.

    *into* est la branche où la tâche se fusionnera (défaut : la branche extraite
    de *repo*). Une branche ``task/<id>`` qui existe déjà sans worktree noté au
    ledger est refusée : on n'écrase jamais le travail de quelqu'un.
    """
    service.require(task_id)
    task_id = normalize_task_id(task_id)
    repo = repo.resolve()
    existing = find_task_worktree(service, task_id)
    if existing is not None and existing.path.is_dir():
        return existing
    branch = TASK_BRANCH_PREFIX + task_id
    if _git(repo, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise GrimoireMissionError(f"{branch!r} n'est pas un nom de branche git valide")
    base_sha = _rev(repo, base)
    if not base_sha:
        raise GrimoireMissionError(f"Base introuvable : {base!r}")
    if not into:
        head = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if head.returncode != 0:
            raise GrimoireMissionError("HEAD détachée : préciser la branche cible (--into)")
        into = head.stdout.strip()
    if not _rev(repo, f"refs/heads/{into}"):
        raise GrimoireMissionError(f"Branche cible introuvable : {into!r}")
    if _rev(repo, f"refs/heads/{branch}"):
        raise GrimoireMissionError(f"La branche {branch} existe déjà sans worktree noté au ledger : rien n'est écrasé")
    target = (path or _state_dir(repo) / "worktrees" / task_id).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--quiet", "-b", branch, str(target), base_sha)
    gitdir = Path(_git(target, "rev-parse", "--absolute-git-dir").stdout.strip())
    home = service.home_root
    marker = {"$schema": _MARKER_SCHEMA, "task_id": task_id, "branch": branch, "base_sha": base_sha, "into": into,
              "project_root": str(home), "ledger_root": str(service.ledger_root)}
    atomic_write(gitdir / MARKER_NAME, json.dumps(marker, ensure_ascii=False, sort_keys=True) + "\n")
    wt = TaskWorktree(task_id, target, branch, base_sha, into)
    service.ledger.append_event(WORKTREE_CREATED_EVENT, task_id, "task", actor, wt.to_dict())
    return wt


# ── Merge série ──────────────────────────────────────────────────────────────


def _check_command(service: TaskService, check: str | None) -> tuple[str, str]:
    if check and check.strip():
        return check, "--check"
    from grimoire.core.execution_needs import resolve_need

    need = resolve_need("test-runner", service.home_root)
    return (need.command or "", need.source) if need.resolved else ("", "unresolved")


def _run_check(repo: Path, task_id: str, sha: str, command: str, timeout: float) -> int | None:
    """Exécute *command* dans un worktree détaché sur *sha* ; rend le code de sortie (``None`` : délai dépassé)."""
    place = _state_dir(repo) / "verify" / f"{task_id}-{sha[:12]}"
    if place.exists():
        _git(repo, "worktree", "remove", "--force", str(place), check=False)
    _git(repo, "worktree", "prune", check=False)
    place.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--quiet", "--detach", str(place), sha)
    try:
        done = subprocess.run(  # noqa: S602 - commande déclarée par le projet ou passée par l'humain
            command, shell=True, cwd=place, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, env=_env(),
        )
        return done.returncode
    except subprocess.TimeoutExpired:
        return None
    finally:
        _git(repo, "worktree", "remove", "--force", str(place), check=False)


def _candidate(repo: Path, wt: TaskWorktree, head: str, target: str) -> tuple[str, tuple[str, ...]]:
    """Le commit qui serait fusionné, calculé sans toucher un worktree ; ``("", conflits)`` en cas de conflit."""
    if _is_ancestor(repo, target, head):
        return head, ()  # fast-forward : le candidat est la tête elle-même
    merged = _git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", target, head, check=False)
    lines = [ln for ln in merged.stdout.splitlines() if ln.strip()]
    if merged.returncode == 1:
        return "", tuple(lines[1:]) or ("(conflit sans fichier nommé)",)
    if merged.returncode != 0 or not lines:
        raise GrimoireMissionError(f"git merge-tree a échoué ({merged.returncode}) : {merged.stderr.strip()}")
    # Date du commit de merge = celle de la tête : le même couple (cible, tête) donne le même SHA.
    stamp = _git(repo, "show", "-s", "--format=%ct", head).stdout.strip()
    env = {**_env(), "GIT_AUTHOR_DATE": f"@{stamp} +0000", "GIT_COMMITTER_DATE": f"@{stamp} +0000"}
    message = f"Merge {wt.branch} into {wt.into}"
    return _git(repo, "commit-tree", lines[0], "-p", target, "-p", head, "-m", message, env=env).stdout.strip(), ()


def _block(service: TaskService, task_id: str, reason: str, actor: str) -> str:
    """Passe la tâche en ``blocked`` (via ``running`` depuis ``needs_verification``) ; rend un motif si le gate refuse."""
    try:
        if service.require(task_id).status is TaskState.NEEDS_VERIFICATION:
            service.transition(task_id, TaskState.RUNNING, actor, reason)
        if service.require(task_id).status in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
            service.transition(task_id, TaskState.BLOCKED, actor, reason)
    except GrimoireError as exc:
        return f" ; blocage non écrit : {exc}"
    return ""


def merge_task(
    service: TaskService, task_id: str, *, repo: Path, check: str | None = None, expected_sha: str = "",
    actor: str = "cli", timeout: float = 1800.0,
) -> MergeOutcome:
    """Fusionne la branche de *task_id* dans sa cible, en série, si le check est vert sur le SHA fusionné.

    *expected_sha* : le SHA que l'appelant a relu ; une tête de branche différente refuse.
    """
    service.require(task_id)
    wt = find_task_worktree(service, task_id)
    if wt is None:
        raise GrimoireMissionError(f"{task_id} : aucun worktree de tâche au ledger (grimoire task worktree {task_id})")
    repo = repo.resolve()
    state = _state_dir(repo)
    state.mkdir(parents=True, exist_ok=True)
    try:
        with locked(state / "merge", timeout=timeout + 60):
            outcome = _merge_locked(service, wt, repo, check, expected_sha, actor, timeout)
    except (TimeoutError, OSError) as exc:
        raise GrimoireMissionError(f"Merge refusé : verrou de merge du dépôt indisponible ({exc})") from exc
    if not outcome.ok:
        service.ledger.append_event(MERGE_REFUSED_EVENT, task_id, "task", actor, outcome.to_dict())
    return outcome


def _merge_locked(
    service: TaskService, wt: TaskWorktree, repo: Path, check: str | None, expected_sha: str, actor: str, timeout: float,
) -> MergeOutcome:
    tid, into = wt.task_id, wt.into
    head, target = _rev(repo, f"refs/heads/{wt.branch}"), _rev(repo, f"refs/heads/{into}")
    if not head or not target:
        raise GrimoireMissionError(f"{tid} : branche {wt.branch if not head else into} introuvable")
    kept = wt.path.is_dir()

    def out(status: str, reason: str, **kw: Any) -> MergeOutcome:
        return MergeOutcome(task_id=tid, status=status, reason=reason, into=into, head_sha=head, worktree_kept=kept, **kw)

    if _is_ancestor(repo, head, target):
        return out("already_merged", f"{wt.branch} est déjà contenue dans {into} : rien à fusionner", merged_sha=target)
    if expected_sha and _rev(repo, expected_sha) != head:
        return out("head_moved", f"la tête de {wt.branch} ({head[:12]}) n'est pas le SHA vérifié ({expected_sha[:12]})")
    candidate, conflicts = _candidate(repo, wt, head, target)
    if not candidate:
        reason = f"conflit avec {into} sur {', '.join(conflicts)} : worktree gardé, tâche bloquée"
        reason += _block(service, tid, f"merge W5-02 : {reason}", actor)
        return out("conflict", reason, conflicts=conflicts)
    command, source = _check_command(service, check)
    if not command:
        return out("unevaluated", "aucune commande de check (--check, ou besoin test-runner du projet) : rien n'est fusionné")
    code = _run_check(repo, tid, candidate, command, timeout)
    receipt = {"command": command, "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
               "source": source, "sha": candidate, "tree": _git(repo, "rev-parse", f"{candidate}^{{tree}}").stdout.strip(),
               "exit": code}
    if code != 0:
        why = "délai dépassé" if code is None else f"sortie {code}"
        return out("check_red", f"check rouge sur {candidate[:12]} ({why}) : rien n'est fusionné", check=receipt)
    now_head, now_target = _rev(repo, f"refs/heads/{wt.branch}"), _rev(repo, f"refs/heads/{into}")
    if now_head != head:
        return out("head_moved", f"{wt.branch} a bougé pendant le check ({head[:12]} → {now_head[:12]}) : SHA non vérifié",
                   check=receipt)
    if now_target != target:
        return out("target_moved", f"{into} a bougé pendant le check ({target[:12]} → {now_target[:12]})", check=receipt)
    holder = _checked_out_at(repo, into)
    if holder is not None:
        _git(holder, "merge", "--ff-only", "--quiet", candidate)
    else:
        _git(repo, "update-ref", "-m", f"grimoire: merge {wt.branch}", f"refs/heads/{into}", candidate, target)
    if _rev(repo, f"refs/heads/{into}") != candidate:
        raise GrimoireMissionError(f"{into} n'est pas au SHA vérifié {candidate[:12]} après le merge")
    service.ledger.append_event(MERGED_EVENT, tid, "task", actor,
                                {"into": into, "base_sha": wt.base_sha, "head_sha": head, "merged_sha": candidate, "check": receipt})
    if kept:
        with contextlib.suppress(GrimoireMissionError):
            _git(repo, "worktree", "remove", str(wt.path))  # sans --force : un worktree sale reste là
    kept = wt.path.is_dir()
    return out("merged", f"{wt.branch} fusionnée dans {into} au SHA vérifié {candidate[:12]}", merged_sha=candidate, check=receipt)
