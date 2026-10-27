"""La relecture, sous mandat, d'une tâche faite à la main par une session interactive (#994).

Le relecteur du tick (:mod:`.reviewer`) ne relisait que les tâches lancées par le lanceur (``task.session_launched``).
Sous mandat actif, une tâche faite par une session interactive restait sans voie de relecture : sa session est
refusée (``review.self_review``), une autre session aussi (elle n'est pas inscrite relecteur au journal d'autorité).
Ce module donne au relecteur ce qui lui manque pour la relire par le même chemin que les tâches du tick :

- :func:`manual_tasks` — les tâches en ``needs_verification`` sans lancement de worker, sous mandat actif seulement
  (sans mandat, rien ne change : la revue croisée par une autre session reste ouverte) ;
- :func:`claim_sessions` — toutes les sessions qui ont tenu le claim de la tâche (claim courant et passés,
  ``task.session_attached``) : ce sont ses « workers », un reçu signé par l'une d'elles est forgé ;
- :func:`resolve_target` — la PR à relire, et seulement une PR **rattachée** à la tâche :

  - le dépôt est celui figé au journal d'autorité à l'octroi du mandat actif, jamais celui figé pour une tâche du tick ;
    un dépôt ou une URL de ``origin`` qui ont changé depuis refusent ; sans pin, ou un pin au dépôt ou à l'URL vides,
    rien n'est relu (réoctroyer le mandat le pose) ;
  - rattachée par sa branche ``task/<id>`` ou par l'identifiant **exact** de la tâche dans son titre (jeton entier) ; un
    lien libre (description, consignes, ``extra``) n'est qu'un indice, recoupé par ces mêmes règles ; plusieurs PR
    candidates : rien n'est relu ;
  - créée après la tâche et après son premier claim ; fusionnée, après ce claim aussi, et son commit de fusion doit
    être la tête relue appliquée à la cible (même arbre) ;
  - vers la branche d'intégration du projet (``orchestrator.base``, sinon ``origin/HEAD``), jamais une base lue de la
    PR ; depuis une branche de travail (:data:`WORK_BRANCH_PREFIXES`) qui n'est ni la cible, ni la branche par défaut,
    ni une branche protégée ; noms de branche sûrs (:func:`safe_ref`) avant toute commande ou consigne ;

- :func:`review_tree` — un worktree **détaché** de vérification, recréé à chaque relecture sous un nom imprévisible, sur
  la tête de la PR récupérée depuis l'URL de ``origin`` figée et comparée à celle que GitHub annonce ; marqué pour qu'un
  ``grimoire task review`` lancé dedans écrive au ledger du projet ;
- :func:`manual_reviews` — pour le merger (:mod:`.merger`) : la dernière relecture d'une tâche manuelle. Le pack de
  preuve que la clôture exige est lu dans le worktree de relecture : celui que ``grimoire task review`` y écrit, ou
  celui que la PR a commité. Sans pack (le relecteur ne l'a pas écrit, la PR ne le porte pas), la tâche fusionnée passe
  en ``waiting(human)`` avec la raison, au lieu d'être close.

Une tâche sans PR à relire, ou dont la PR est refusée, attend :data:`DEFER` avant d'être regardée de nouveau
(``task.review_deferred`` au ledger) ; rien n'est écrit au journal d'autorité avant un lancement.

**Limites** : le pin du dépôt date de l'octroi du mandat ; une configuration réécrite avant l'octroi n'est pas vue. Le
worktree de vérification reste sous le dossier git commun (``grimoire/review/``) jusqu'à la relecture suivante de la
tâche : il porte le reçu et le pack ; le janitor ne nettoie que les worktrees ``task/<id>``.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from grimoire.core.safe_git import git_env, same_repo
from grimoire.missions.launcher import LAUNCHED_EVENT
from grimoire.missions.ledger import SESSION_ATTACHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.task_worktree import TASK_BRANCH_PREFIX, TaskWorktree, _state_dir, mark_worktree

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "DEFER",
    "MANUAL_KEY",
    "REVIEW_DEFERRED_EVENT",
    "WORK_BRANCH_PREFIXES",
    "PullRequestRef",
    "claim_sessions",
    "defer",
    "deferred_until",
    "manual_reviews",
    "manual_tasks",
    "manual_worktree",
    "resolve_target",
    "review_tree",
    "safe_ref",
]

#: La clé, dans un lancement synthétique et dans ``task.review_launched``, qui marque une tâche faite à la main.
MANUAL_KEY = "manual"
#: Une tâche manuelle sans PR à relire (ou à PR refusée) : regardée de nouveau après ``until``.
REVIEW_DEFERRED_EVENT = "task.review_deferred"
DEFER = timedelta(hours=1)
#: Les branches de travail qu'une PR manuelle peut porter : jamais une branche d'intégration.
WORK_BRANCH_PREFIXES = ("task/", "feat/", "fix/", "chore/", "docs/", "refactor/", "perf/", "test/", "ci/", "build/",
                        "style/", "aidlc/")
_REVIEW_LAUNCHED = "task.review_launched"  # reviewer.REVIEW_LAUNCHED_EVENT, sans import circulaire
_FIELDS = "number,state,title,headRefName,headRefOid,baseRefName,isCrossRepository,createdAt,mergedAt,mergeCommit"
_SEARCH_LIMIT = 20
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_REF = re.compile(r"[A-Za-z0-9._/-]+")
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
    title: str = ""
    created_at: str = ""
    merged_at: str = ""
    merge_commit: str = ""


def _moment(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.strip())
    except (ValueError, AttributeError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def safe_ref(name: str) -> bool:
    """Un nom de branche qu'on peut mettre dans une commande ou une consigne : ``[A-Za-z0-9._/-]``, sans ``..``, sans
    tiret ni barre en tête (jamais une option), sans barre finale."""
    return bool(_REF.fullmatch(name)) and ".." not in name and not name.startswith(("-", "/")) and not name.endswith("/")


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
    lancement de worker, telle que le ledger la note : ``cwd``, ``branch``, ``into``, ``pr``, ``head``,
    ``session_sha256`` ; ``session_id`` vide — aucune session de worker à reprendre. Le merger la recoupe avec le
    journal d'autorité (:func:`grimoire.missions.review_seal.launch_entry`)."""
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
                "head": str(p.get("head") or ""), "session_sha256": str(p.get("session_sha256") or ""),
            }
    return {tid: payload for tid, payload in found.items() if tid not in launched}


def manual_worktree(task_id: str, sealed: dict[str, Any]) -> TaskWorktree:
    """Le worktree de vérification d'une tâche manuelle, d'après son lancement scellé : branche et base de la PR."""
    return TaskWorktree(task_id, Path(str(sealed.get("cwd") or "")), str(sealed.get("branch") or ""), "",
                        str(sealed.get("base") or ""))


def claim_sessions(service: TaskService, task: MissionTask) -> set[str]:
    """Toutes les sessions qui ont tenu le claim de *task* : claim courant, claims passés, sessions rattachées."""
    found = {task.claim.session_id} if task.claim else set()
    for event in service.ledger.events_for(task.id):
        if event.event_type == SESSION_ATTACHED_EVENT:
            found.add(str(event.payload.get("session_id") or ""))
        elif event.event_type == "task.transitioned" and isinstance(event.payload.get("claim"), dict):
            found.add(str(event.payload["claim"].get("session_id") or ""))
    return {s.strip() for s in found} - {""}


def _not_before(service: TaskService, task: MissionTask) -> datetime | None:
    """L'instant après lequel la PR doit être créée (et fusionnée) : le premier claim, à défaut la création de la tâche."""
    claims = [e.created_at for e in service.ledger.events_for(task.id)
              if e.event_type == "task.transitioned" and e.payload.get("to_state") == TaskState.CLAIMED.value]
    return _moment(claims[0] if claims else task.created_at)


def deferred_until(service: TaskService, task_id: str, since: str, now: datetime) -> str:
    """L'échéance d'un report en cours (posé depuis *since*, l'entrée en revue), ``""`` s'il n'y en a pas."""
    for event in reversed(service.ledger.events_for(task_id)):
        if event.event_type == REVIEW_DEFERRED_EVENT and event.created_at >= since:
            until = str(event.payload.get("until") or "")
            moment = _moment(until)
            return until if moment is not None and moment > now else ""
    return ""


def defer(service: TaskService, task_id: str, why: str, now: datetime, actor: str) -> str:
    """Note au ledger qu'une tâche manuelle n'est pas relue avant :data:`DEFER` ; rend l'échéance."""
    until = (now + DEFER).isoformat()
    service.ledger.append_event(REVIEW_DEFERRED_EVENT, task_id, "task", actor, {"task_id": task_id, "until": until,
                                                                                  "why": why[:300]})
    return until


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


def _default_branch(home: Path) -> str:
    head = _git(home, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    return head.removeprefix("origin/") if head.startswith("origin/") else ""


def _linked_number(task: MissionTask, slug: str) -> int:
    """Le dernier lien vers une PR du dépôt *slug* que la tâche porte (``extra``, description, consignes) ; ``0`` sinon.

    Un indice seulement : la PR qu'il désigne doit encore être rattachée à la tâche (:func:`_belongs`).
    """
    link = re.compile(rf"(?:^|[^A-Za-z0-9.-])github\.com/{re.escape(slug)}/pull/(\d+)\b", re.IGNORECASE)
    texts: list[str] = [task.description, *(d.text for d in task.directives)]
    for key in _LINK_KEYS:
        value = task.extra.get(key) if isinstance(task.extra, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            texts.append(f"https://github.com/{slug}/pull/{value}")
        elif isinstance(value, str):
            texts.append(f"https://github.com/{slug}/pull/{value}" if value.strip().isdigit() else value)
    found = [int(m.group(1)) for text in texts for m in link.finditer(text or "")]
    return found[-1] if found else 0


def _belongs(row: dict[str, Any], task_id: str) -> bool:
    """Rattachée à la tâche : sa branche est ``task/<id>``, ou son titre porte l'identifiant exact (jeton entier)."""
    if str(row.get("headRefName") or "") == TASK_BRANCH_PREFIX + task_id:
        return True
    token = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(task_id)}(?![A-Za-z0-9_-])")
    return bool(token.search(str(row.get("title") or "")))


def _parse(row: dict[str, Any]) -> PullRequestRef | None:
    try:
        commit = row.get("mergeCommit")
        return PullRequestRef(
            int(row["number"]), str(row.get("headRefName") or ""), str(row.get("headRefOid") or ""),
            str(row.get("baseRefName") or ""), str(row.get("state") or "").upper(), str(row.get("title") or ""),
            str(row.get("createdAt") or ""), str(row.get("mergedAt") or ""),
            str(commit.get("oid") or "") if isinstance(commit, dict) else "",
        )
    except (KeyError, TypeError, ValueError):
        return None


def _candidates(exec_: Exec, cwd: Path, task: MissionTask, slug: str) -> tuple[list[dict[str, Any]], str]:
    """Les PR ouvertes ou fusionnées rattachées à la tâche (indice, ``task/<id>``, titre) ; ``(…, erreur)`` si gh échoue."""

    def gh(*args: str) -> Any:
        return exec_(["gh", *args, "-R", slug], cwd, None)

    def view(selector: str) -> tuple[dict[str, Any] | None, str]:
        done = gh("pr", "view", selector, "--json", _FIELDS)
        if done.returncode != 0:
            if "no pull requests found" in (done.stderr or "").lower():
                return None, ""
            return None, f"gh pr view {selector} : {(done.stderr or done.stdout).strip()[:200]}"
        try:
            row = json.loads(done.stdout)
        except ValueError:
            return None, f"gh pr view {selector} : réponse illisible"
        return (row, "") if isinstance(row, dict) else (None, f"gh pr view {selector} : réponse illisible")

    rows: list[dict[str, Any]] = []
    hint = _linked_number(task, slug)
    if hint:  # un indice illisible ou erroné (PR introuvable, lien faux) est ignoré : la recherche continue
        row, _error = view(str(hint))
        if row is not None:
            rows.append(row)
    row, error = view(TASK_BRANCH_PREFIX + task.id)
    if error:
        return [], error
    if row is not None:
        rows.append(row)
    done = gh("pr", "list", "--search", f"{task.id} in:title", "--state", "all", "--limit", str(_SEARCH_LIMIT),
              "--json", _FIELDS)
    if done.returncode != 0:
        return [], f"gh pr list --search {task.id} : {(done.stderr or done.stdout).strip()[:200]}"
    try:
        listed = json.loads(done.stdout or "[]")
    except ValueError:
        return [], "gh pr list : réponse illisible"
    rows += [r for r in listed if isinstance(r, dict)] if isinstance(listed, list) else []
    seen: dict[int, dict[str, Any]] = {}
    for row in rows:  # une PR de fork n'est jamais candidate : elle ne bloque pas la relecture en s'ajoutant au compte
        if _belongs(row, task.id) and str(row.get("state") or "").upper() in ("OPEN", "MERGED") \
                and row.get("isCrossRepository") is False:
            seen.setdefault(int(row.get("number") or 0), row)
    return list(seen.values()), ""


def _branch_refusal(exec_: Exec, cwd: Path, slug: str, pr: PullRequestRef, into: str, default: str) -> str:
    """Pourquoi la branche de la PR n'est pas une branche de travail relisible ; ``""`` si elle l'est."""
    if not safe_ref(pr.branch):
        return f"nom de branche de la PR #{pr.number} refusé (caractères hors [A-Za-z0-9._/-] ou ..)"
    if pr.branch in (into, default) or not pr.branch.startswith(WORK_BRANCH_PREFIXES):
        return (f"la PR #{pr.number} part de {pr.branch!r}, qui n'est pas une branche de travail "
                f"({', '.join(WORK_BRANCH_PREFIXES)}) distincte de {into!r}")
    done = exec_(["gh", "api", f"repos/{slug}/branches/{quote(pr.branch, safe='')}", "--jq", ".protected"], cwd, None)
    if done.returncode == 0 and done.stdout.strip() == "true":
        return f"la PR #{pr.number} part de la branche protégée {pr.branch!r}"
    return ""  # protection illisible : la cible et la branche par défaut sont déjà refusées


def resolve_target(
    service: TaskService, exec_: Exec, task: MissionTask, *, policy_base: str, pin: dict[str, str] | None,
    current: dict[str, str],
) -> tuple[PullRequestRef | None, str, str]:
    """``(PR, "", cible)`` à relire ; sinon ``(None, genre, raison)`` : ``none`` (aucune PR rattachée), ``refused``
    (une garde refuse : dépôt, base, branche, dates, plusieurs PR) ou ``error`` (gh en échec)."""
    home = service.home_root
    if pin is None:
        return None, "refused", ("aucun dépôt GitHub figé au journal d'autorité avant cette relecture : réoctroyer le "
                                 "mandat (l'octroi fige le dépôt et l'URL de origin), rien n'est relu d'ici là")
    if current.get("repo") != pin["repo"] or current.get("origin_url", "").strip() != pin["origin_url"].strip():
        return None, "refused", (f"le dépôt du projet ({current.get('repo') or '?'}) ou l'URL de origin ont changé depuis "
                                 f"le pin du journal d'autorité ({pin['repo']}) : rien n'est relu")
    into = policy_base or _default_branch(home)
    if not into or not safe_ref(into):
        return None, "refused", "branche d'intégration introuvable ou illisible (orchestrator.base, origin/HEAD)"
    rows, error = _candidates(exec_, home, task, pin["repo"])
    if error:
        return None, "error", error
    if not rows:
        return None, "none", (f"pas de PR à relire : aucune PR ouverte ou fusionnée de branche {TASK_BRANCH_PREFIX}"
                              f"{task.id} ou dont le titre porte {task.id}")
    if len(rows) > 1:
        shown = ", ".join(f"#{r.get('number')}" for r in rows[:5])
        return None, "refused", f"plusieurs PR rattachées à {task.id} ({shown}) : laquelle relire n'est pas tranché"
    row = rows[0]
    pr = _parse(row)
    if pr is None or row.get("isCrossRepository") is not False or not _SHA.fullmatch(pr.head):
        return None, "refused", f"PR #{row.get('number')} illisible ou venue d'un autre dépôt : jamais relue ici"
    if pr.base != into:
        return None, "refused", f"la PR #{pr.number} vise {pr.base!r}, pas la branche d'intégration {into!r}"
    if why := _branch_refusal(exec_, home, pin["repo"], pr, into, _default_branch(home)):
        return None, "refused", why
    floor, created = _not_before(service, task), _moment(pr.created_at)
    if floor is None or created is None or created <= floor:
        return None, "refused", f"la PR #{pr.number} n'a pas été créée après la tâche et son premier claim"
    if pr.state == "MERGED":
        merged = _moment(pr.merged_at)
        if merged is None or merged <= floor or not _SHA.fullmatch(pr.merge_commit):
            return None, "refused", f"la PR #{pr.number} a été fusionnée avant le claim de la tâche (ou sans commit de fusion)"
    return pr, "", into


def review_tree(service: TaskService, task_id: str, pr: PullRequestRef, origin_url: str) -> tuple[Path | None, str]:
    """Le worktree détaché de vérification sur la tête de *pr*, recréé à chaque relecture, ou pourquoi il n'est pas posé.

    La tête est récupérée à refspec explicite depuis *origin_url* (figée au journal d'autorité) vers une ref du tick,
    par la branche de la PR puis par ``refs/pull/<n>/head``, et doit être celle que GitHub annonce. Une PR fusionnée :
    son commit de fusion doit être dans la cible récupérée, et son arbre celui de la tête relue appliquée à son premier
    parent. Une URL réécrite par la configuration (``url.*.insteadOf``) refuse. Les worktrees de relecture précédents
    de la tâche sont retirés ; le nouveau porte un nom imprévisible.
    """
    home = service.home_root
    if not origin_url.strip():
        return None, "URL de origin non figée au journal d'autorité : rien n'est récupéré"
    if _git(home, "config", "--get-regexp", r"^url\..*\.(push)?insteadof$").stdout.strip():
        return None, "la configuration du dépôt réécrit des URL (url.*.insteadOf) : rien n'est récupéré"

    def fetch(source: str, ref: str) -> str:
        done = _git(home, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", origin_url,
                    f"+{source}:{ref}")
        return _rev(home, f"{ref}^{{commit}}") if done.returncode == 0 else ""

    ref = f"{_REVIEW_REFS}/{task_id}"
    if pr.head not in (fetch(f"refs/heads/{pr.branch}", ref), fetch(f"refs/pull/{pr.number}/head", ref)):
        return None, (f"tête {pr.head[:12]} de la PR #{pr.number} introuvable sur la remote (ni {pr.branch}, "
                      f"ni refs/pull/{pr.number}/head) : la remote et GitHub ne disent pas la même chose")
    if pr.state == "MERGED" and (why := _merge_refusal(home, pr, fetch(f"refs/heads/{pr.base}", f"{ref}-base"))):
        return None, why
    try:
        parent = _state_dir(home) / "review"
    except Exception as exc:
        return None, f"dossier git commun illisible ({type(exc).__name__})"
    _retire_trees(home, parent, task_id)
    place = parent / f"{task_id}-{pr.head[:12]}-{uuid.uuid4().hex[:12]}"
    if place.exists():
        return None, f"dossier de relecture {place.name} déjà présent : rien n'est réutilisé"
    parent.mkdir(parents=True, exist_ok=True)
    added = _git(home, "worktree", "add", "--quiet", "--detach", str(place), pr.head)
    if added.returncode != 0:
        return None, f"worktree de vérification non posé : {(added.stderr or added.stdout).strip()[:200]}"
    if unsafe := same_repo(home, place):
        _git(home, "worktree", "remove", "--force", str(place))
        return None, f"worktree de vérification refusé : {unsafe}"
    mark_worktree(service, TaskWorktree(task_id, place, pr.branch, "", pr.base))
    return place, ""


def _merge_refusal(home: Path, pr: PullRequestRef, target: str) -> str:
    """``""`` si le commit de fusion est dans la cible et porte l'arbre de la tête relue appliquée à son premier parent."""
    if not target or _git(home, "merge-base", "--is-ancestor", pr.merge_commit, target).returncode != 0:
        return f"commit de fusion {pr.merge_commit[:12]} de la PR #{pr.number} absent de {pr.base}"
    parent = _rev(home, f"{pr.merge_commit}^1")
    merged = _git(home, "merge-tree", "--write-tree", "--no-messages", parent, pr.head) if parent else None
    tree = merged.stdout.split()[0] if merged is not None and merged.returncode == 0 and merged.stdout.split() else ""
    if not tree or tree != _rev(home, f"{pr.merge_commit}^{{tree}}"):
        return (f"le commit de fusion {pr.merge_commit[:12]} n'est pas la tête relue {pr.head[:12]} appliquée à "
                f"{pr.base} : la relecture ne couvrirait pas ce qui a été fusionné")
    return ""


def _retire_trees(home: Path, parent: Path, task_id: str) -> None:
    """Retire (``--force``) les worktrees de relecture précédents de *task_id* : jamais un dossier réutilisé."""
    mine = re.compile(rf"{re.escape(task_id)}-[0-9a-f]{{12}}-[0-9a-f]{{12}}")  # jamais GAO-1-bis pour GAO-1
    listing = _git(home, "worktree", "list", "--porcelain").stdout.splitlines()
    for line in listing:
        path = Path(line[len("worktree "):]) if line.startswith("worktree ") else None
        if path is not None and path.resolve().parent == parent.resolve() and mine.fullmatch(path.name):
            _git(home, "worktree", "remove", "--force", str(path))
    _git(home, "worktree", "prune")
