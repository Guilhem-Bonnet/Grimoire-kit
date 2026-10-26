"""Les PR à revoir (P48) : les PR ouvertes du dépôt, l'état de leur CI et la tâche du ledger liée.

Deux lecteurs partagent ce module : la route d'attention (« À traiter ») en
fait une ligne par PR, et ``GET /api/workspace/reviews`` alimente l'écran de
revue. Les deux lisent la même chose : UNE lecture ``gh pr list -R <dépôt>
--state open`` par dépôt, partagée entre projets et entre requêtes.

Ce que la lecture promet :

- **elle ne bloque jamais la route d'attention** : la lecture ``gh`` part dans
  un fil à part (:class:`ReviewCache`), une requête ne l'attend que le temps
  qu'on lui accorde (:data:`ATTENTION_WAIT_S` pour l'attention) ; au-delà,
  elle rend « lecture en cours » (:data:`LOADING`) et l'interface relit plus
  tard. Deux requêtes simultanées partagent la lecture en vol ; une réponse
  vit :data:`CACHE_TTL_S` secondes ;
- **un ``gh`` absent, non authentifié, muet ou lent n'est pas une liste
  vide** : l'état est « inconnu » (:data:`UNKNOWN`) avec sa raison. Un projet
  sans dépôt git ni remote ``origin`` n'a pas de PR possible (:data:`NONE`) :
  rien à dire, rien d'affiché ;
- **lecture seule** : aucune fusion, aucun commentaire, aucune écriture
  GitHub. Le cockpit ouvre la PR sur GitHub, l'humain y fusionne.

Le dépôt est celui que l'orchestrateur lit déjà pour ses créneaux CI
(:func:`grimoire.missions.ci_slots.ci_repository` : ``ci.repository`` de la
politique d'orchestration, sinon le remote ``origin``). La tâche liée suit la
convention du worker (branche ``task/<id>``), sinon un identifiant de tâche du
ledger cité dans la branche, le titre ou le corps de la PR (``Refs GAO-…``).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.missions.ci_slots import NO_PR, _RepositoryUnresolvedError, ci_repository

__all__ = [
    "ATTENTION_WAIT_S",
    "CACHE_TTL_S",
    "LOADING",
    "NONE",
    "OK",
    "SCHEMA_VERSION",
    "UNKNOWN",
    "ReviewCache",
    "ReviewReadError",
    "check_summary",
    "default_reader",
    "linked_task",
    "parse_prs",
    "pr_view",
    "project_reviews",
    "review_reason",
    "reviews_view",
]

SCHEMA_VERSION = "grimoire-workspace-reviews/v1"

#: États d'une lecture : lue, inconnue (gh absent, refusé, muet), en cours, sans PR possible.
OK, UNKNOWN, LOADING, NONE = "ok", "unknown", "loading", "none"

#: Les champs demandés à ``gh pr list`` : tout ce que l'écran de revue montre, en une lecture.
PR_FIELDS = (
    "number,title,url,headRefName,baseRefName,isDraft,author,additions,deletions,changedFiles,body,"
    "createdAt,updatedAt,statusCheckRollup,reviewDecision,files,closingIssuesReferences"
)
_PR_LIMIT = 100
#: Délai maximal d'une lecture ``gh`` : au-delà, l'état est inconnu, jamais une attente sans fin.
READ_TIMEOUT_S = 15.0
#: Durée de vie d'une lecture : la liste « À traiter » se relit souvent, GitHub n'est sondé qu'une fois par fenêtre.
CACHE_TTL_S = 60.0
#: Ce que la route d'attention accorde à une lecture neuve avant de rendre « lecture en cours ».
ATTENTION_WAIT_S = 0.3
#: Ce que l'écran de revue accorde : il n'a que cette lecture à faire.
VIEW_WAIT_S = READ_TIMEOUT_S + 2

_SUCCESS = frozenset({"SUCCESS"})
_SKIPPED = frozenset({"SKIPPED", "NEUTRAL"})
_PENDING = frozenset({"PENDING", "EXPECTED"})
_STATE_RANK = {"passed": 0, "pending": 1, "none": 2, "failed": 3}
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9]")
_TASK_BRANCH = "task/"
_TEST_SUFFIXES = ("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts")
_DOC_SUFFIXES = (".md", ".rst", ".txt")
_CATEGORY_LABELS = {"code": "Code", "tests": "Tests", "docs": "Docs"}
_SHOWN_PATHS = 3


class ReviewReadError(Exception):
    """La lecture des PR n'a pas abouti ; le message dit pourquoi, en français, pour l'interface."""


#: Un lecteur reçoit le dépôt (``owner/repo``) et le dossier du projet ; il rend la liste brute de ``gh``.
PrReader = Callable[[str, Path], list[dict[str, Any]]]


# ── Lecture GitHub ──────────────────────────────────────────────────────────


def parse_prs(raw: str) -> list[dict[str, Any]]:
    """Le JSON de ``gh pr list`` ; une sortie vide ou illisible est une erreur, jamais une liste vide."""
    if not raw.strip():
        raise ReviewReadError("gh n'a rien répondu")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewReadError("réponse de gh illisible") from exc
    if not isinstance(data, list) or not all(isinstance(pr, dict) for pr in data):
        raise ReviewReadError("réponse de gh inattendue")
    return data


def default_reader(repo: str, cwd: Path) -> list[dict[str, Any]]:
    """UNE lecture ``gh pr list -R <repo> --state open`` ; ``gh`` absent, refusé ou lent lève :class:`ReviewReadError`."""
    gh = shutil.which("gh")
    if not gh:
        raise ReviewReadError("gh absent : installe GitHub CLI pour lire les PR")
    argv = [gh, "pr", "list", "-R", repo, "--state", "open", "--json", PR_FIELDS, "--limit", str(_PR_LIMIT)]
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=READ_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewReadError(f"gh n'a pas répondu en {READ_TIMEOUT_S:.0f} s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewReadError("gh indisponible") from exc
    if done.returncode != 0:
        err = done.stderr.lower()
        if "auth login" in err or "not logged" in err or "authentication" in err:
            raise ReviewReadError("gh non authentifié : lance « gh auth login »")
        raise ReviewReadError(f"gh pr list a échoué (code {done.returncode})")
    return parse_prs(done.stdout)


@dataclass(frozen=True, slots=True)
class ReviewRead:
    """Ce qu'une requête obtient du cache : l'état, sa raison, les PR brutes et l'heure de la lecture."""

    state: str
    detail: str = ""
    prs: tuple[dict[str, Any], ...] = ()
    fetched_at: str = ""


@dataclass
class _Entry:
    state: str = LOADING
    detail: str = ""
    prs: tuple[dict[str, Any], ...] = ()
    fetched_at: str = ""
    read_at: float | None = None
    running: threading.Event | None = None


@dataclass
class ReviewCache:
    """Une lecture par dépôt, partagée : en vol, deux requêtes attendent la même ; lue, elle sert :attr:`ttl` secondes.

    La lecture tourne dans un fil démon : une requête qui n'a plus le temps
    d'attendre repart avec l'état connu (ou « lecture en cours » s'il n'y en a
    pas encore) et la lecture continue pour la suivante.
    """

    ttl: float = CACHE_TTL_S
    _entries: dict[str, _Entry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self, repo: str, cwd: Path, reader: PrReader, *, wait: float) -> ReviewRead:
        with self._lock:
            entry = self._entries.setdefault(repo, _Entry())
            if entry.read_at is not None and time.monotonic() - entry.read_at < self.ttl:
                return self._snapshot(entry)
            done = entry.running
            if done is None:
                done = threading.Event()
                entry.running = done
                threading.Thread(
                    target=self._fetch, args=(entry, repo, cwd, reader, done), daemon=True, name="grimoire-reviews"
                ).start()
        done.wait(max(0.0, wait))
        with self._lock:
            return self._snapshot(entry)

    def _fetch(self, entry: _Entry, repo: str, cwd: Path, reader: PrReader, done: threading.Event) -> None:
        try:
            prs: tuple[dict[str, Any], ...] = tuple(reader(repo, cwd))
            state, detail = OK, ""
        except ReviewReadError as exc:
            prs, state, detail = (), UNKNOWN, str(exc)
        except Exception as exc:  # frontière : un lecteur cassé dit « inconnu », il n'éteint pas la route
            prs, state, detail = (), UNKNOWN, f"lecture des PR en échec ({type(exc).__name__})"
        with self._lock:
            entry.state, entry.detail, entry.prs = state, detail, prs
            entry.fetched_at = datetime.now(UTC).isoformat()
            entry.read_at = time.monotonic()
            entry.running = None
        done.set()

    @staticmethod
    def _snapshot(entry: _Entry) -> ReviewRead:
        return ReviewRead(entry.state, entry.detail, entry.prs, entry.fetched_at)


_CACHE = ReviewCache()


# ── Une PR, lisible ─────────────────────────────────────────────────────────


def check_summary(rollup: Any) -> dict[str, Any]:
    """Les checks d'une PR comptés : verts, en échec, ignorés (``SKIPPED``, ``NEUTRAL``), en cours.

    ``state`` : ``failed`` dès un échec, sinon ``pending`` dès un check en
    cours, sinon ``passed`` s'il y a des checks, ``none`` sinon. Les échecs sont
    nommés, avec le lien de leur journal quand GitHub en donne un.
    """
    green = failed = skipped = pending = 0
    failing: list[dict[str, str]] = []
    for check in rollup if isinstance(rollup, list) else []:
        if not isinstance(check, dict):
            continue
        if "status" in check or check.get("__typename") == "CheckRun":
            if str(check.get("status", "")).upper() != "COMPLETED":
                pending += 1
                continue
            outcome = str(check.get("conclusion") or "").upper()
        else:  # StatusContext (API historique)
            outcome = str(check.get("state") or "").upper()
            if outcome in _PENDING:
                pending += 1
                continue
        if outcome in _SUCCESS:
            green += 1
        elif outcome in _SKIPPED:
            skipped += 1
        else:
            failed += 1
            url = str(check.get("detailsUrl") or check.get("targetUrl") or "")
            failing.append({
                "name": str(check.get("name") or check.get("context") or "check"),
                "workflow": str(check.get("workflowName") or ""),
                "conclusion": outcome.lower() or "inconnu",
                "url": url if url.startswith("https://") else "",
            })
    total = green + failed + skipped + pending
    state = "failed" if failed else "pending" if pending else "passed" if total else "none"
    return {
        "state": state, "passed": green, "failed": failed, "skipped": skipped, "pending": pending,
        "total": total, "failing": failing,
    }


def linked_task(pr: Mapping[str, Any], task_ids: Iterable[str]) -> str:
    """La tâche du ledger liée à la PR : branche ``task/<id>``, sinon un identifiant cité (branche, titre, corps).

    Seuls les identifiants connus du ledger comptent, et dans le texte libre
    seulement ceux qui portent un tiret (``GAO-…``) : un mot courant qui
    serait aussi un identifiant (« bootstrap ») ne lie rien.
    """
    ids = set(task_ids)
    branch = str(pr.get("headRefName") or "")
    if branch.startswith(_TASK_BRANCH) and branch[len(_TASK_BRANCH):] in ids:
        return branch[len(_TASK_BRANCH):]
    text = " ".join(str(pr.get(key) or "") for key in ("headRefName", "title", "body"))
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if "-" in token and token in ids:
            return token
    return ""


def _category(path: str) -> str:
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    if low.startswith("tests/") or "/tests/" in low or name.startswith("test_") or name.endswith(_TEST_SUFFIXES):
        return "tests"
    if low.startswith("docs/") or name.endswith(_DOC_SUFFIXES):
        return "docs"
    return "code"


def _file_groups(paths: list[str]) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for path in paths:
        groups.setdefault(_category(path), []).append(path)
    return [
        {"kind": kind, "label": _CATEGORY_LABELS[kind], "count": len(groups[kind]),
         "paths": [p.rsplit("/", 1)[-1] for p in groups[kind][:_SHOWN_PATHS]]}
        for kind in ("code", "tests", "docs") if kind in groups
    ]


def pr_view(pr: Mapping[str, Any], *, repo: str, tasks: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Une PR brute de ``gh`` rendue lisible : CI comptée, tâche liée, fichiers groupés, issues fermées."""
    raw_files, raw_author, raw_issues = pr.get("files"), pr.get("author"), pr.get("closingIssuesReferences")
    files: list[Any] = raw_files if isinstance(raw_files, list) else []
    paths = [str(f.get("path")) for f in files if isinstance(f, dict) and f.get("path")]
    author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
    issues: list[Any] = raw_issues if isinstance(raw_issues, list) else []
    task_id = linked_task(pr, tasks)
    task = tasks.get(task_id) if task_id else None
    url = str(pr.get("url") or "")

    def _int(key: str) -> int:
        value = pr.get(key)
        return value if isinstance(value, int) else 0

    return {
        "number": _int("number"),
        "title": str(pr.get("title") or ""),
        "url": url if url.startswith("https://") else "",
        "repo": repo,
        "branch": str(pr.get("headRefName") or ""),
        "base": str(pr.get("baseRefName") or ""),
        "draft": bool(pr.get("isDraft")),
        "author": str(author.get("login") or ""),
        "additions": _int("additions"),
        "deletions": _int("deletions"),
        "changed_files": _int("changedFiles") or len(paths),
        "created_at": str(pr.get("createdAt") or ""),
        "updated_at": str(pr.get("updatedAt") or ""),
        "review_decision": str(pr.get("reviewDecision") or ""),
        "checks": check_summary(pr.get("statusCheckRollup")),
        "task": {"id": task_id, "title": str(task.get("title") or ""), "status": str(task.get("status") or "")}
        if task_id and task is not None else None,
        "issues": [i["number"] for i in issues if isinstance(i, dict) and isinstance(i.get("number"), int)],
        "files": _file_groups(paths),
        "changelog": any(p.rsplit("/", 1)[-1].upper() == "CHANGELOG.MD" for p in paths),
        "paths": paths,
        "overlaps": [],
    }


def review_reason(pr: Mapping[str, Any]) -> str:
    """La raison d'une ligne « PR à revoir » : l'état de la CI en mots, puis la tâche liée."""
    checks = pr["checks"]
    if checks["state"] == "failed":
        names = ", ".join(f["name"] for f in checks["failing"][:2])
        ci = f"{checks['failed']} échec{'s' if checks['failed'] > 1 else ''} de CI : {names}"
    elif checks["state"] == "pending":
        ci = f"CI en cours : {checks['pending']} check{'s' if checks['pending'] > 1 else ''} sur {checks['total']}"
    elif checks["state"] == "passed":
        ci = f"CI verte : {checks['passed']} vert{'s' if checks['passed'] > 1 else ''}"
        if checks["skipped"]:
            ci += f", {checks['skipped']} ignoré{'s' if checks['skipped'] > 1 else ''}"
    else:
        ci = "aucun check rapporté"
    task = pr.get("task")
    return f"{ci} · tâche {task['id']}" if task else f"{ci} · aucune tâche liée"


def readiness_key(pr: Mapping[str, Any]) -> tuple[int, int, int]:
    """Les plus prêtes d'abord : CI verte, puis en cours, puis sans check, puis en échec (le moins d'échecs d'abord)."""
    checks = pr["checks"]
    return (_STATE_RANK.get(checks["state"], 2), checks["failed"], pr["number"])


def _mark_overlaps(prs: list[dict[str, Any]]) -> None:
    """Les autres PR ouvertes du même dépôt qui touchent un même fichier : un croisement à regarder avant de fusionner."""
    for pr in prs:
        mine = set(pr["paths"])
        pr["overlaps"] = sorted(
            other["number"] for other in prs
            if other is not pr and other["repo"] == pr["repo"] and mine & set(other["paths"])
        )


# ── Par projet, puis pour la vue ────────────────────────────────────────────


def _in_git(root: Path) -> bool:
    return any((folder / ".git").exists() for folder in (root, *root.parents))


def project_reviews(
    root: Path,
    *,
    home_root: Path | None = None,
    tasks: Mapping[str, Mapping[str, Any]] | None = None,
    reader: PrReader | None = None,
    cache: ReviewCache | None = None,
    wait: float = ATTENTION_WAIT_S,
) -> dict[str, Any]:
    """Les PR ouvertes à revoir d'un projet : ``{state, detail, repo, prs, drafts, fetched_at}``.

    Les brouillons sont comptés à part (``drafts``) : ils n'attendent pas de revue.
    """
    out: dict[str, Any] = {"state": NONE, "detail": "", "repo": "", "prs": [], "drafts": 0, "fetched_at": ""}
    if not _in_git(root):
        out["detail"] = "pas un dépôt git : aucune PR possible"
        return out
    try:
        repo = ci_repository(home_root or root, root)
    except _RepositoryUnresolvedError as exc:
        out["state"] = NONE if exc.status == NO_PR else UNKNOWN
        out["detail"] = exc.detail
        return out
    got = (cache or _CACHE).read(repo, root, reader or default_reader, wait=wait)
    views = [pr_view(pr, repo=repo, tasks=tasks or {}) for pr in got.prs]
    ready = sorted((v for v in views if not v["draft"]), key=readiness_key)
    out.update(state=got.state, detail=got.detail, repo=repo, prs=ready, drafts=len(views) - len(ready),
               fetched_at=got.fetched_at)
    return out


def _task_index(root: Path) -> tuple[Path, dict[str, dict[str, Any]]]:
    from grimoire.missions.service import TaskService

    service = TaskService(root)
    if not service.has_ledger:
        return service.home_root, {}
    tasks = {t.id: {"title": t.title, "status": t.status.value} for t in service.list_tasks()}
    return service.home_root, tasks


def _read_entry(
    entry: Mapping[str, Any], *, reader: PrReader | None, cache: ReviewCache | None, wait: float
) -> dict[str, Any]:
    slug = str(entry.get("slug", ""))
    project = {"slug": slug, "name": str(entry.get("name", "")) or slug}
    raw = str(entry.get("path", "") or "")
    root = Path(raw) if raw else None
    if root is None or not root.is_dir():
        return {"project": project, "state": UNKNOWN, "detail": "dossier du projet introuvable", "repo": "",
                "prs": [], "drafts": 0, "fetched_at": ""}
    try:
        home_root, tasks = _task_index(root)
    except Exception:  # un ledger illisible ne prive pas de la liste des PR, seulement du lien aux tâches
        home_root, tasks = root, {}
    result = project_reviews(root, home_root=home_root, tasks=tasks, reader=reader, cache=cache, wait=wait)
    return {"project": project, **result}


def reviews_view(
    project_root: Path,
    *,
    portfolio: bool = False,
    slug: str | None = None,
    reader: PrReader | None = None,
    cache: ReviewCache | None = None,
    wait: float = VIEW_WAIT_S,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/reviews`` : les PR à revoir, les plus prêtes d'abord.

    Même portée que la route d'attention : le projet servi, tout le registre
    (*portfolio*) ou un projet *slug*. Une PR vue par deux projets du même
    dépôt n'apparaît qu'une fois. ``sources`` dit, par projet, ce que vaut la
    lecture : un « inconnu » n'est jamais rendu comme une liste vide.
    """
    from grimoire.tools.workspace_attention import _entries

    entries = _entries(project_root, portfolio=portfolio, slug=slug)
    if entries:
        with ThreadPoolExecutor(max_workers=min(len(entries), 8), thread_name_prefix="grimoire-reviews") as pool:
            reads = list(pool.map(lambda e: _read_entry(e, reader=reader, cache=cache, wait=wait), entries))
    else:
        reads = []
    seen: set[tuple[str, int]] = set()
    prs: list[dict[str, Any]] = []
    for read in reads:
        for pr in read["prs"]:
            key = (pr["repo"], pr["number"])
            if key in seen:
                continue
            seen.add(key)
            prs.append({**pr, "project": read["project"]})
    _mark_overlaps(prs)
    prs.sort(key=readiness_key)
    for pr in prs:
        pr.pop("paths", None)
    sources = [
        {"project": r["project"], "state": r["state"], "detail": r["detail"], "repo": r["repo"],
         "count": len(r["prs"]), "drafts": r["drafts"], "fetched_at": r["fetched_at"]}
        for r in reads
    ]
    states = {s["state"] for s in sources}
    overall = LOADING if LOADING in states else UNKNOWN if UNKNOWN in states else OK if OK in states else NONE
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "portfolio" if portfolio else "project",
        "state": overall,
        "sources": sources,
        "prs": prs,
        "count": len(prs),
    }
