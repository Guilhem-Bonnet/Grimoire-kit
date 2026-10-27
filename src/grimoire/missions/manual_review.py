"""La relecture, sous mandat, d'une tâche faite à la main par une session interactive (#994).

Le relecteur du tick (:mod:`.reviewer`) ne relisait que les tâches lancées par le lanceur (``task.session_launched``).
Sous mandat actif, une tâche faite par une session interactive restait sans voie de relecture : sa session est
refusée (``review.self_review``), une autre session aussi (elle n'est pas inscrite relecteur au journal d'autorité).
Ce module donne au relecteur ce qui lui manque pour la relire par le même chemin que les tâches du tick :

- :func:`manual_tasks` — les tâches en ``needs_verification`` sans lancement de worker, sous mandat actif seulement
  (sans mandat, rien ne change : la revue croisée par une autre session reste ouverte) ;
- :func:`claim_sessions` — toutes les sessions qui ont tenu le claim de la tâche (claim courant et passés,
  ``task.session_attached``) : ce sont ses « workers », un reçu signé par l'une d'elles est forgé ;
- :func:`find_pull_request` — la PR de la tâche, sur le dépôt figé au journal d'autorité : le lien de PR que la tâche
  porte, sinon ``gh pr list --search <id>``, sinon la branche ``task/<id>``. Aucune PR trouvable : pas de relecture,
  jamais celle de la cible entière ;
- :func:`review_tree` — un worktree **détaché** de vérification sur la tête de la PR, récupérée depuis l'URL de
  ``origin`` figée, et comparée à celle que GitHub annonce ; marqué pour qu'un ``grimoire task review`` lancé dedans
  écrive au ledger du projet ;
- :func:`manual_reviews` — pour le merger (:mod:`.merger`) : la dernière relecture d'une tâche manuelle, avec sa PR et
  son worktree de vérification, pour qu'il la fusionne ou constate sa fusion puis l'accepte sous mandat.

**Limites** : le dépôt GitHub d'une tâche manuelle est figé à sa première relecture, pas avant le travail (aucun
worker du tick ne l'a précédé) ; une session qui réécrit la configuration du dépôt avant cette relecture le
détournerait. Le worktree de vérification reste sous le dossier git commun (``grimoire/review/``) après la clôture : il
porte le reçu et le pack de la relecture, et le janitor ne nettoie que les worktrees ``task/<id>``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.safe_git import git_env
from grimoire.missions.launcher import LAUNCHED_EVENT
from grimoire.missions.ledger import SESSION_ATTACHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import TASK_BRANCH_PREFIX, TaskWorktree, _state_dir, mark_worktree

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "MANUAL_KEY",
    "PullRequestRef",
    "claim_sessions",
    "find_pull_request",
    "manual_reviews",
    "manual_tasks",
    "manual_worktree",
    "review_tree",
]

#: La clé, dans un lancement synthétique et dans ``task.review_launched``, qui marque une tâche faite à la main.
MANUAL_KEY = "manual"
_REVIEW_LAUNCHED = "task.review_launched"  # reviewer.REVIEW_LAUNCHED_EVENT, sans import circulaire
_FIELDS = "number,state,headRefName,headRefOid,baseRefName,isCrossRepository"
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_LINK_KEYS = ("pr", "pr_url", "pull_request")
_REVIEW_REFS = "refs/grimoire/review"


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    """La PR d'une tâche faite à la main, telle que GitHub la décrit."""

    number: int
    branch: str
    head: str
    base: str
    state: str


def manual_tasks(service: TaskService) -> dict[str, dict[str, Any]]:
    """Task id → lancement synthétique de chaque tâche en revue sans worker du tick, sous mandat actif ; ``{}`` sinon.

    Un journal d'autorité illisible ne relit rien à la main (échec fermé) : la tâche reste en revue.
    """
    from grimoire.missions.mandate import MandateStore

    try:
        if not MandateStore(service.home_root).state().active:
            return {}
    except Exception:
        return {}
    launched = {e.entity_id for e in service.ledger.list_events() if e.event_type == LAUNCHED_EVENT}
    return {
        task.id: {"session_id": "", "cwd": "", "model": "", MANUAL_KEY: True}
        for task in service.ledger.list_tasks()
        if task.status is TaskState.NEEDS_VERIFICATION and task.id not in launched
    }


def manual_reviews(service: TaskService) -> dict[str, dict[str, Any]]:
    """Task id → la dernière relecture manuelle (``task.review_launched`` marqué :data:`MANUAL_KEY`) d'une tâche sans
    lancement de worker, sous la forme d'un lancement que le merger lit : ``cwd`` (le worktree de vérification),
    ``branch``, ``into``, ``pr``, ``head`` ; ``session_id`` vide — aucune session de worker à reprendre."""
    launched: set[str] = set()
    found: dict[str, dict[str, Any]] = {}
    for event in service.ledger.list_events():
        if event.event_type == LAUNCHED_EVENT:
            launched.add(event.entity_id)
        elif event.event_type == _REVIEW_LAUNCHED and event.payload.get(MANUAL_KEY):
            p = event.payload
            found[event.entity_id] = {
                "session_id": "", "cwd": str(p.get("cwd") or ""), "model": "", MANUAL_KEY: True,
                "branch": str(p.get("branch") or ""), "into": str(p.get("into") or ""), "pr": int(p.get("pr") or 0),
                "head": str(p.get("head") or ""),
            }
    return {tid: payload for tid, payload in found.items() if tid not in launched}


def manual_worktree(task_id: str, launch: dict[str, Any]) -> TaskWorktree:
    """Le worktree de vérification d'une tâche manuelle, vu comme le worktree d'une tâche : sa branche est celle de la PR."""
    return TaskWorktree(task_id, Path(str(launch.get("cwd") or "")), str(launch.get("branch") or ""), "",
                        str(launch.get("into") or ""))


def claim_sessions(service: TaskService, task: MissionTask) -> set[str]:
    """Toutes les sessions qui ont tenu le claim de *task* : claim courant, claims passés, sessions rattachées."""
    found = {task.claim.session_id} if task.claim else set()
    for event in service.ledger.events_for(task.id):
        if event.event_type == SESSION_ATTACHED_EVENT:
            found.add(str(event.payload.get("session_id") or ""))
        elif event.event_type == "task.transitioned" and isinstance(event.payload.get("claim"), dict):
            found.add(str(event.payload["claim"].get("session_id") or ""))
    return {s.strip() for s in found} - {""}


def _linked_number(task: MissionTask, slug: str) -> int:
    """Le dernier lien vers une PR du dépôt *slug* que la tâche porte (``extra``, description, consignes) ; ``0`` sinon."""
    link = re.compile(rf"github\.com/{re.escape(slug)}/pull/(\d+)\b", re.IGNORECASE)
    texts: list[str] = [task.description, *(d.text for d in task.directives)]
    for key in _LINK_KEYS:
        value = task.extra.get(key) if isinstance(task.extra, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            texts.append(f"github.com/{slug}/pull/{value}")
        elif isinstance(value, str):
            texts.append(f"github.com/{slug}/pull/{value}" if value.strip().isdigit() else value)
    found = [int(m.group(1)) for text in texts for m in link.finditer(text or "")]
    return found[-1] if found else 0


def _parse(row: Any) -> tuple[PullRequestRef | None, str]:
    """Une PR lue de gh, ou pourquoi elle ne se relit pas ici (fork, fermée sans fusion, tête illisible)."""
    if not isinstance(row, dict):
        return None, "réponse de gh illisible"
    if row.get("isCrossRepository") is not False:
        return None, f"PR #{row.get('number')} venue d'un autre dépôt (ou origine non dite) : jamais relue ici"
    try:
        pr = PullRequestRef(int(row["number"]), str(row.get("headRefName") or ""), str(row.get("headRefOid") or ""),
                            str(row.get("baseRefName") or ""), str(row.get("state") or "").upper())
    except (KeyError, TypeError, ValueError):
        return None, "réponse de gh illisible"
    if pr.state not in ("OPEN", "MERGED"):
        return None, f"PR #{pr.number} {pr.state} sans fusion : rien à relire"
    if not pr.branch or not pr.base or not _SHA.fullmatch(pr.head):
        return None, f"gh ne donne pas la branche, la cible ou la tête de la PR #{pr.number}"
    return pr, ""


def find_pull_request(
    exec_: Exec, cwd: Path, task: MissionTask, slug: str,
) -> tuple[PullRequestRef | None, str, str]:
    """``(PR, "", "")`` ; ``(None, "none", raison)`` sans PR trouvable ; ``(None, "error", raison)`` si gh échoue ou
    si la PR trouvée ne se relit pas ici. Chaque ``gh`` vise le dépôt figé (``-R``)."""

    def gh(*args: str) -> Any:
        return exec_(["gh", *args, "-R", slug], cwd, None)

    def view(selector: str) -> tuple[PullRequestRef | None, str, str]:
        done = gh("pr", "view", selector, "--json", _FIELDS)
        if done.returncode != 0:
            if "no pull requests found" in (done.stderr or "").lower():
                return None, "none", ""
            return None, "error", f"gh pr view {selector} : {(done.stderr or done.stdout).strip()[:200]}"
        try:
            row = json.loads(done.stdout)
        except ValueError:
            return None, "error", f"gh pr view {selector} : réponse illisible"
        pr, why = _parse(row)
        return pr, ("error" if pr is None else ""), why

    number = _linked_number(task, slug)
    if number:
        pr, kind, why = view(str(number))
        if kind != "none":
            return pr, kind, why
    done = gh("pr", "list", "--search", task.id, "--state", "all", "--json", _FIELDS)
    if done.returncode != 0:
        return None, "error", f"gh pr list --search {task.id} : {(done.stderr or done.stdout).strip()[:200]}"
    try:
        rows = json.loads(done.stdout or "[]")
    except ValueError:
        return None, "error", "gh pr list : réponse illisible"
    alive = [r for r in rows if isinstance(r, dict) and str(r.get("state") or "").upper() in ("OPEN", "MERGED")] \
        if isinstance(rows, list) else []
    if len(alive) > 1:
        shown = ", ".join(f"#{r.get('number')}" for r in alive[:5])
        return None, "error", f"plusieurs PR citent {task.id} ({shown}) : laquelle relire n'est pas tranché"
    if alive:
        pr, why = _parse(alive[0])
        return pr, ("error" if pr is None else ""), why
    pr, kind, why = view(TASK_BRANCH_PREFIX + task.id)
    if kind == "none":
        return None, "none", (f"pas de PR à relire : aucun lien de PR sur la tâche, aucune PR ne cite {task.id}, "
                              f"aucune PR pour {TASK_BRANCH_PREFIX}{task.id}")
    return pr, kind, why


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # arguments fixes, sans shell ; dans le tick, la config qui exécute est neutralisée
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, check=False, env=git_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 124, "", str(exc))


def _rev(cwd: Path, ref: str) -> str:
    done = _git(cwd, "rev-parse", "--verify", "--quiet", ref)
    return done.stdout.strip() if done.returncode == 0 else ""


def review_tree(service: TaskService, task_id: str, pr: PullRequestRef, origin_url: str) -> tuple[Path | None, str]:
    """Le worktree détaché de vérification sur la tête de *pr*, ou pourquoi il n'est pas posé.

    La tête est récupérée à refspec explicite depuis *origin_url* (figée au journal d'autorité) vers une ref du tick,
    par la branche de la PR puis par ``refs/pull/<n>/head`` (une branche supprimée après fusion), et doit être celle
    que GitHub annonce. Une URL réécrite par la configuration (``url.*.insteadOf``) refuse.
    """
    home = service.home_root
    if not origin_url:
        return None, "URL de origin non figée au journal d'autorité : rien n'est récupéré"
    if _git(home, "config", "--get-regexp", r"^url\..*\.(push)?insteadof$").stdout.strip():
        return None, "la configuration du dépôt réécrit des URL (url.*.insteadOf) : rien n'est récupéré"
    ref = f"{_REVIEW_REFS}/{task_id}"
    for source in (f"refs/heads/{pr.branch}", f"refs/pull/{pr.number}/head"):
        done = _git(home, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", origin_url,
                    f"+{source}:{ref}")
        if done.returncode == 0 and _rev(home, f"{ref}^{{commit}}") == pr.head:
            break
    else:
        return None, (f"tête {pr.head[:12]} de la PR #{pr.number} introuvable sur la remote (ni {pr.branch}, "
                      f"ni refs/pull/{pr.number}/head) : la remote et GitHub ne disent pas la même chose")
    try:
        place = _state_dir(home) / "review" / f"{task_id}-{pr.head[:12]}"
    except Exception as exc:
        return None, f"dossier git commun illisible ({type(exc).__name__})"
    if not (place / ".git").is_file() or _rev(place, "HEAD^{commit}") != pr.head:
        if place.exists():
            _git(home, "worktree", "remove", "--force", str(place))
        _git(home, "worktree", "prune")
        place.parent.mkdir(parents=True, exist_ok=True)
        added = _git(home, "worktree", "add", "--quiet", "--detach", str(place), pr.head)
        if added.returncode != 0:
            return None, f"worktree de vérification non posé : {(added.stderr or added.stdout).strip()[:200]}"
    mark_worktree(service, TaskWorktree(task_id, place, pr.branch, "", pr.base))
    return place, ""
