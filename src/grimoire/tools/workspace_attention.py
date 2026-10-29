"""La route d'attention — « qu'est-ce qui attend une décision de moi, et où ? » (P22).

La liste « À traiter » de la Flotte ne connaissait que trois raisons (kit en
retard, projet non initialisé, CI rouge), calculées dans le navigateur. Un
checkpoint destructif en attente ou deux tâches en revue ne faisaient pas
apparaître le projet, et une sonde qui expirait le faisait sortir de la liste.
Ce module rassemble côté serveur, pour un projet ou pour tout le registre, ce
qui attend un humain :

- checkpoints de flow non décidés (tous les runs, pas le dernier — P07) et
  questions posées par ``task ask`` ;
- revues dont le gate refuse la clôture, tâches bloquées, consignes non
  accusées, claims dont plus aucune session ne tient la tâche ;
- propositions en attente ;
- PR ouvertes à revoir (P48), avec l'état de leur CI et la tâche du ledger
  liée — la lecture ``gh`` est bornée et partagée
  (:mod:`grimoire.tools.workspace_reviews`), un ``gh`` absent est un « état
  des PR inconnu », jamais une liste vide ;
- kits en retard ;
- l'hygiène de la mémoire (P25) : store contaminé (doublons, identités de
  test), contradictions de ``memory lint``, supersessions orphelines, motifs
  transverses à reconfirmer, dérive de parité, graphe de code périmé — un
  élément typé par constat, chacun avec son geste (``ref.gesture``) ;
- sondes en échec (mémoire, lecture d'une source), projets illisibles ;
- services mémoire requis par un projet qui travaille (session vivante ou
  tâche en cours), arrêtés, en échec ou inconnus.

Refus délibérés :

- **une lecture ratée n'est pas un zéro** (leçon #264) : un projet illisible
  est un élément d'attention avec sa raison, jamais omis ; une sonde mémoire
  expirée dit « état inconnu, sonde expirée » ;
- **anti-bruit des services** : un service ne devient un élément qu'après
  :data:`SERVICE_ALERT_AFTER` sondes négatives consécutives, et se résout seul
  quand il répond de nouveau, avec l'heure (:class:`ServiceWatch`) ;
- **rien n'est décidé ici** : aucune proposition acceptée ni resynchronisée
  (lecture sur disque), aucune transition ; la sonde mémoire lit le cache
  sauf ``probe=True`` (« Réessayer »).

:func:`attention_order` range les éléments par urgence : fonction pure, testée
seule.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.missions.liveness import INCONNU, TERMINE, VIVANT, SessionProbe
from grimoire.tools import workspace_reviews
from grimoire.tools.memory_setup import SERVICE_FAILED, SERVICE_ONLINE, SERVICE_STOPPED, service_state
from grimoire.tools.project_health import kit_alignment
from grimoire.tools.project_registry import load_registry, slug_for_path
from grimoire.tools.workspace_hygiene import HYGIENE_KINDS, cached_report
from grimoire.tools.workspace_portfolio import (
    DEFAULT_LIVE_MINUTES,
    RUNS_RELPATH,
    annotate_cards,
    unread_directives,
)

__all__ = [
    "KINDS",
    "SCHEMA_VERSION",
    "SECTIONS",
    "SERVICE_ALERT_AFTER",
    "ServiceWatch",
    "attention_order",
    "attention_view",
]

SCHEMA_VERSION = "grimoire-workspace-attention/v1"

CHECKPOINT = "checkpoint"
QUESTION = "question"
PROPOSAL = "proposal"
PR_REVIEW = "pr_review"
REVIEW_BLOCKED = "review_blocked"
TASK_BLOCKED = "task_blocked"
DIRECTIVE_UNACKED = "directive_unacked"
CLAIM_ORPHANED = "claim_orphaned"
SERVICE = "service"
UNREADABLE = "unreadable"
PROBE_FAILED = "probe_failed"
KIT_BEHIND = "kit_behind"
SERVICE_RESOLVED = "service_resolved"

#: Les sections, de la plus urgente à la moins urgente, et les types de chacune
#: dans leur ordre. Un type n'apparaît que dans une section.
SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("decide", "À décider", (CHECKPOINT, QUESTION, PROPOSAL)),
    ("review", "PR à revoir", (PR_REVIEW,)),
    ("unblock", "À débloquer", (REVIEW_BLOCKED, TASK_BLOCKED, DIRECTIVE_UNACKED, CLAIM_ORPHANED)),
    ("repair", "À réparer", (SERVICE, UNREADABLE, PROBE_FAILED)),
    ("maintain", "À entretenir", (*HYGIENE_KINDS, KIT_BEHIND)),
    ("resolved", "Résolu automatiquement", (SERVICE_RESOLVED,)),
)
#: Un type que :data:`SECTIONS` ne connaît pas tombe ici : jamais perdu.
OTHER_SECTION = ("other", "Autre")

KINDS: tuple[str, ...] = tuple(kind for _, _, kinds in SECTIONS for kind in kinds)
_SECTION_OF: dict[str, tuple[str, int]] = {
    kind: (section_id, rank) for section_id, _, kinds in SECTIONS for rank, kind in enumerate(kinds)
}

#: Sondes négatives consécutives avant qu'un service devienne un élément d'attention.
SERVICE_ALERT_AFTER = 2
#: Deux lectures plus proches que ce délai (deux onglets, un double clic) ne
#: comptent que pour une sonde : c'est l'intervalle par défaut du pouls.
SERVICE_PROBE_MIN_INTERVAL = timedelta(seconds=5)
#: Durée pendant laquelle « résolu automatiquement à … » reste affiché.
SERVICE_RESOLVED_VISIBLE = timedelta(hours=1)

_SERVICE_WORDS = {SERVICE_STOPPED: "arrêté", SERVICE_FAILED: "en échec"}
_MEMORY_PROBE_EXPIRED = "état inconnu, sonde expirée"
_PROJECT_MAX_WORKERS = 8


# ── Ordre ───────────────────────────────────────────────────────────────────


def _since_key(raw: Any) -> tuple[int, float]:
    """Le plus ancien d'abord ; sans date lisible, en fin de section."""
    if raw:
        try:
            moment = datetime.fromisoformat(str(raw))
        except ValueError:
            return (1, 0.0)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return (0, moment.timestamp())
    return (1, 0.0)


def attention_order(cards: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Range les éléments d'attention en sections, par urgence — fonction pure.

    Chaque élément tombe dans exactement une section, celle de son ``kind`` ;
    un type inconnu va dans « Autre », jamais perdu. Dans une section : le rang
    du type, puis le plus ancien d'abord (``since``, les éléments sans date en
    fin), puis l'ordre d'arrivée — même entrée, même sortie. Les sections de
    :data:`SECTIONS` sont toujours rendues, vides comprises ; « Autre »
    seulement si elle reçoit un élément. Les éléments sont copiés, jamais
    modifiés.
    """
    buckets: dict[str, list[tuple[int, tuple[int, float], int, Mapping[str, Any]]]] = {
        section_id: [] for section_id, _, _ in SECTIONS
    }
    other: list[tuple[int, tuple[int, float], int, Mapping[str, Any]]] = []
    for index, card in enumerate(cards):
        placed = _SECTION_OF.get(str(card.get("kind", "")))
        bucket = buckets[placed[0]] if placed else other
        bucket.append((placed[1] if placed else 0, _since_key(card.get("since")), index, card))

    def _items(entries: list[tuple[int, tuple[int, float], int, Mapping[str, Any]]]) -> list[dict[str, Any]]:
        return [dict(card) for *_, card in sorted(entries, key=lambda e: (e[0], e[1], e[2]))]

    def _section(section_id: str, label: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"id": section_id, "label": label, "items": items, "count": len(items)}

    sections = [_section(section_id, label, _items(buckets[section_id])) for section_id, label, _ in SECTIONS]
    if other:
        sections.append(_section(*OTHER_SECTION, _items(other)))
    return sections


# ── Anti-bruit des services ─────────────────────────────────────────────────


@dataclass
class _Watch:
    state: str = ""
    failures: int = 0
    first_failure_at: datetime | None = None
    probed_at: datetime | None = None
    alerted: bool = False
    resolved_at: datetime | None = None


@dataclass
class ServiceWatch:
    """La mémoire des sondes successives de chaque service (``(service, url)``).

    Une sonde négative isolée ne dit rien (un redémarrage, un port pris une
    seconde) : l'alerte monte à la :data:`SERVICE_ALERT_AFTER`-ième négative
    consécutive. Le service qui répond de nouveau après une alerte est
    « résolu » à l'heure de cette sonde. L'état vit dans le processus du
    cockpit : un redémarrage repart de zéro, ce qui retarde une alerte, jamais
    n'en invente une.
    """

    alert_after: int = SERVICE_ALERT_AFTER
    min_interval: timedelta = SERVICE_PROBE_MIN_INTERVAL
    _watches: dict[tuple[str, str], _Watch] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(
        self, service: str, url: str, now: datetime, probe: Callable[[str, str], str]
    ) -> _Watch:
        """Sonde *service* (au plus une fois par :attr:`min_interval`) et rend son état cumulé."""
        with self._lock:
            watch = self._watches.setdefault((service, url), _Watch())
            if watch.probed_at is not None and now - watch.probed_at < self.min_interval:
                return copy.copy(watch)
            state = probe(service, url)
            watch.probed_at = now
            watch.state = state
            if state == SERVICE_ONLINE:
                if watch.alerted:
                    watch.resolved_at = now
                watch.failures, watch.first_failure_at, watch.alerted = 0, None, False
            else:
                watch.failures += 1
                if watch.first_failure_at is None:
                    watch.first_failure_at = now
                if watch.failures >= self.alert_after:
                    watch.alerted, watch.resolved_at = True, None
            return copy.copy(watch)


_WATCH = ServiceWatch()


# ── Collecte par projet ─────────────────────────────────────────────────────


@dataclass
class _ProjectRead:
    row: dict[str, Any]
    root: Path | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    #: Pourquoi le projet travaille (« session vivante », « tâche en cours »), ou vide.
    activity: str = ""
    #: Les tâches du ledger (``id → titre, état``), pour lier une PR à sa tâche (P48).
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    home_root: Path | None = None
    #: Ce que vaut la lecture des PR du projet : ``{state, detail, repo, count}``.
    reviews: dict[str, Any] = field(default_factory=dict)


def _hm(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%H:%M} UTC"


def _item(
    read: _ProjectRead, kind: str, key: str, title: str, reason: str, *, since: str | None = None, **ref: Any
) -> None:
    slug = read.row["slug"]
    read.items.append({
        "id": f"{kind}:{slug}:{key}",
        "kind": kind,
        "project": {"slug": slug, "name": read.row["name"]},
        "title": title,
        "reason": reason,
        "since": since or None,
        "ref": ref,
    })


def _question_v2(ledger: Any, task_id: str, question: Any) -> dict[str, Any]:
    """La question v2 complète et ses rounds (P92) ; titre et contexte d'une question v1 sont tirés de son texte."""
    from grimoire.missions.question_rounds import rounds_of
    from grimoire.missions.waiting import split_legacy

    data = question.to_dict()
    if not data.get("title"):
        data["title"], legacy_context = split_legacy(question.text, question.kind)
        data.setdefault("context", legacy_context)
    return {"question": data, "rounds": rounds_of(ledger, task_id, question)}


def _guarded(read: _ProjectRead, source: str, collect: Callable[[], None]) -> None:
    """Une source qu'on ne lit pas devient un élément « sonde en échec », jamais un silence."""
    try:
        collect()
    except Exception as exc:  # frontière : une source cassée n'éteint pas les autres
        _item(read, PROBE_FAILED, source, f"{source} illisible", f"{source} illisible : {exc}")


def _last_events(ledger: Any) -> dict[str, str]:
    """Horodatage du dernier événement de chaque tâche, en une passe."""
    last: dict[str, str] = {}
    for event in ledger.list_events():
        if event.created_at and event.created_at > last.get(event.entity_id, ""):
            last[event.entity_id] = event.created_at
    return last


def _orphan_reason(task: Any, card: dict[str, Any], now: datetime) -> str:
    """Pourquoi plus personne ne tient ce claim — vide si une session le tient, ou si l'on ne sait pas."""
    session = card.get("session")
    if session is None:
        claim = task.claim
        if claim is not None and claim.is_expired(now):
            return "claim expiré, aucune session rattachée"
        return ""
    state = session["liveness"]["state"]
    if state == TERMINE:
        return "la session a déclaré sa fin, le claim reste posé"
    if state == INCONNU and session.get("attention"):
        return str(session["attention"]["reason"])
    return ""


def _collect_ledger(read: _ProjectRead, now: datetime, host_probe: SessionProbe | None) -> None:
    from grimoire.missions.autopilot_view import partition
    from grimoire.missions.decisions import pending_decisions
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService
    from grimoire.tools.workspace_api import _task_json

    assert read.root is not None
    service = TaskService(read.root)
    read.home_root = service.home_root
    if not service.has_ledger:
        read.row["state"] = "no_ledger"
        read.row["reason"] = "aucun Mission Ledger"
        return
    tasks = service.list_tasks()
    read.tasks = {task.id: {"title": task.title, "status": task.status.value} for task in tasks}
    cards = [_task_json(task) for task in tasks]
    annotate_cards(read.root, service, tasks, cards, now=now, probe=host_probe)
    last = _last_events(service.ledger)

    pilot_on, held = partition(service)
    held_by_task = {h.decision.task_id: h for h in held}
    for decision in pending_decisions(service.ledger):
        pilot = held_by_task.get(decision.task_id)
        if pilot_on and pilot is None:
            continue  # pilote activé : l'orchestrateur la tranchera, elle ne compte pas
        _item(
            read,
            QUESTION,
            decision.task_id,
            decision.title,
            decision.question.text,
            since=decision.since,
            task_id=decision.task_id,
            question_id=decision.question.id,
        )
        read.items[-1].update(_question_v2(service.ledger, decision.task_id, decision.question))
        if pilot is not None:
            read.items[-1]['pilot'] = pilot.to_dict()
    for task, card in zip(tasks, cards, strict=True):
        since = last.get(task.id) or str(task.created_at)
        session = card.get("session")
        if session and session["liveness"]["state"] == VIVANT:
            read.activity = read.activity or "session vivante"
        if task.status in (TaskState.CLAIMED, TaskState.RUNNING):
            read.activity = read.activity or "tâche en cours"
            orphan = _orphan_reason(task, card, now)
            if orphan:
                _item(read, CLAIM_ORPHANED, task.id, task.title, orphan, since=since, task_id=task.id)
        if task.status is TaskState.NEEDS_VERIFICATION:
            try:
                verdict = service.gate(task, TaskState.CLOSED)
            except Exception as exc:  # un gate illisible ne laisse pas passer la revue en silence
                _item(read, REVIEW_BLOCKED, task.id, task.title, f"verdict du gate illisible : {exc}",
                      since=since, task_id=task.id)
            else:
                if verdict.blocked:
                    _item(read, REVIEW_BLOCKED, task.id, task.title,
                          "; ".join(f"{r.evidence} — {r.reason}" for r in verdict.refusals),
                          since=since, task_id=task.id, transition=verdict.transition_id,
                          refusals=[{"evidence": r.evidence, "reason": r.reason, "remedy": r.remedy}
                                    for r in verdict.refusals])
        if card.get("board") == "blocked" and task.status is not TaskState.WAITING:
            _item(read, TASK_BLOCKED, task.id, task.title, str(card.get("blocked_reason") or "bloquée"),
                  since=since, task_id=task.id)
        unread = unread_directives(card)
        if unread:
            _item(read, DIRECTIVE_UNACKED, task.id, task.title,
                  f"{unread} consigne{'s' if unread > 1 else ''} en attente de lecture par la session",
                  since=since, task_id=task.id, unread=unread)


def _collect_checkpoints(read: _ProjectRead) -> None:
    from grimoire.tools.flow_runs import flow_runs_view

    assert read.root is not None
    for run in flow_runs_view(read.root)["runs"]:
        if run.get("status") != "checkpointed":
            continue
        node = run.get("currentNode") or "?"
        _item(read, CHECKPOINT, str(run["runId"]), f"{run['blueprintId']} : checkpoint « {node} »",
              "arrêté au checkpoint, jamais décidé à votre place", since=run.get("createdAt"),
              run_id=run["runId"], blueprint=run["blueprintId"], node=node)


def _collect_proposals(read: _ProjectRead) -> None:
    from grimoire.proposals import list_proposals

    assert read.root is not None
    for proposal in list_proposals(read.root, sync=False):
        if proposal.status != "pending":
            continue
        _item(read, PROPOSAL, proposal.slug, f"{proposal.artifact_type} « {proposal.specialty} »",
              f"proposée après {proposal.count} observation(s)",
              since=proposal.created_at or proposal.first_seen, slug=proposal.slug)


@dataclass(frozen=True, slots=True)
class _ReviewSource:
    """Comment lire les PR : le lecteur ``gh`` (injectable), le cache partagé et l'attente accordée."""

    reader: workspace_reviews.PrReader | None = None
    cache: workspace_reviews.ReviewCache | None = None
    wait: float = workspace_reviews.ATTENTION_WAIT_S


#: Les états de tâche dont la PR est relue puis fusionnée par le tick ; ``waiting``, ``blocked``,
#: ``failed`` en sont absents : la tâche est revenue vers l'humain.
_PILOTED_STATES = frozenset({"ready", "claimed", "running", "needs_verification"})
_PILOT_GESTURES = frozenset({"accept", "merge"})


def _pilot_covers_reviews(root: Path) -> bool:
    """Vrai si un mandat actif couvre ``accept`` et ``merge`` : le relecteur du tick relit alors les PR de tâches."""
    from grimoire.missions.mandate import MandateStore

    state = MandateStore(root).state()
    return state.active and state.mandate is not None and set(state.mandate.gestures) >= _PILOT_GESTURES


def _piloted(pr: Mapping[str, Any]) -> bool:
    """Une PR rattachée à une tâche que le tick prend en charge, et que le merger n'a pas lâchée (CI en échec)."""
    task = pr["task"]
    return bool(task) and task["status"] in _PILOTED_STATES and pr["checks"]["state"] != "failed"


def _collect_reviews(read: _ProjectRead, source: _ReviewSource) -> None:
    """Une ligne par PR ouverte à revoir ; un état inconnu devient une sonde en échec, jamais un silence."""
    assert read.root is not None
    got = workspace_reviews.project_reviews(
        read.root, home_root=read.home_root, tasks=read.tasks,
        reader=source.reader, cache=source.cache, wait=source.wait,
    )
    piloted = 0
    if got["prs"] and _pilot_covers_reviews(read.root):
        piloted = sum(1 for pr in got["prs"] if _piloted(pr))
    read.reviews = {"state": got["state"], "detail": got["detail"], "repo": got["repo"],
                    "count": len(got["prs"]) - piloted}
    if piloted:  # sans mandat, la charge utile reste celle d'avant
        read.reviews["piloted"] = piloted
    if got["state"] == workspace_reviews.UNKNOWN:
        _item(read, PROBE_FAILED, "pr", "PR à revoir", f"état des PR inconnu : {got['detail']}", repo=got["repo"])
    for pr in got["prs"]:
        if piloted and _piloted(pr):
            continue
        task = pr["task"] or {}
        _item(read, PR_REVIEW, f"{got['repo']}#{pr['number']}", f"#{pr['number']} {pr['title']}",
              workspace_reviews.review_reason(pr), since=pr["created_at"] or None,
              pr=pr["number"], repo=got["repo"], url=pr["url"], branch=pr["branch"],
              linked_task=task.get("id", ""), checks=pr["checks"],
              additions=pr["additions"], deletions=pr["deletions"])


def _collect_kit(read: _ProjectRead) -> None:
    assert read.root is not None
    kit = kit_alignment(read.root)
    if kit.get("scaffolded") and kit.get("catalogAvailable") and not kit.get("upToDate") and kit.get("behind"):
        _item(read, KIT_BEHIND, "kit", "kit en retard", f"{kit['behind']} fichier(s) en retard sur le kit",
              behind=kit["behind"], files=kit.get("behindFiles", []))


def _collect_memory(read: _ProjectRead, probe: bool) -> None:
    from grimoire.tools.memory_link import memory_link_status

    assert read.root is not None
    status = memory_link_status(read.root, probe=probe)
    if status.get("state") != "unavailable":
        return
    error = str(status.get("error") or "backend indisponible")
    reason = f"{_MEMORY_PROBE_EXPIRED} ({error})" if "expir" in error else f"sonde mémoire en échec : {error}"
    _item(read, PROBE_FAILED, "memory", "mémoire", reason, since=status.get("probedAt"),
          backend=status.get("resolvedBackend") or status.get("configuredBackend"))


def _collect_hygiene(read: _ProjectRead) -> None:
    """Les constats d'hygiène de la mémoire, repris du rapport de santé (cache, jamais de réseau)."""
    assert read.root is not None
    report = cached_report(read.root, slug=read.row["slug"], name=read.row["name"])
    for item in report.get("hygiene", []):
        _item(read, item["kind"], item["key"], item["title"], item["reason"], since=item.get("since"), **item["ref"])


def _live_journal(root: Path, now: datetime) -> bool:
    """Un journal de session non terminé, écrit dans la fenêtre de vie, même sans tâche."""
    runs = root / RUNS_RELPATH
    if not runs.is_dir():
        return False
    window = timedelta(minutes=DEFAULT_LIVE_MINUTES)
    for path in runs.glob("session-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            moment = datetime.fromisoformat(str(data.get("updated_at") or data.get("started_at")))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if not data.get("ended_at") and timedelta(0) <= now - moment <= window:
            return True
    return False


def _required_services(root: Path) -> list[tuple[str, str]]:
    from grimoire.core.config import GrimoireConfig
    from grimoire.tools.memory_setup import configured_services

    config_path = root / "project-context.yaml"
    if not config_path.is_file():
        return []
    return configured_services(GrimoireConfig.from_yaml(config_path).memory)


def _read_project(
    entry: Mapping[str, Any],
    *,
    now: datetime,
    probe_memory: bool,
    host_probe: SessionProbe | None,
    reviews: _ReviewSource | None = None,
) -> _ProjectRead:
    slug = str(entry.get("slug", ""))
    raw = str(entry.get("path", "") or "")
    read = _ProjectRead(row={
        "slug": slug, "name": str(entry.get("name", "")) or slug, "path": raw, "state": "unreadable", "reason": None,
    })
    if not raw:
        read.row["reason"] = "chemin absent du registre"
    elif not Path(raw).is_dir():
        read.row["reason"] = f"dossier introuvable : {raw}"
    if read.row["reason"]:
        _item(read, UNREADABLE, "project", "projet illisible", read.row["reason"])
        return read
    read.root = Path(raw)
    read.row["state"] = "ok"
    try:
        _collect_ledger(read, now, host_probe)
    except Exception as exc:  # frontière : un ledger abîmé n'efface ni le projet ni les autres sources
        read.row["state"] = "unreadable"
        read.row["reason"] = f"ledger illisible : {exc}"
        _item(read, UNREADABLE, "ledger", "projet illisible", read.row["reason"])
    if not read.activity and _live_journal(read.root, now):
        read.activity = "session vivante"
    _guarded(read, "checkpoints", lambda: _collect_checkpoints(read))
    _guarded(read, "propositions", lambda: _collect_proposals(read))
    _guarded(read, "PR", lambda: _collect_reviews(read, reviews or _ReviewSource()))
    _guarded(read, "kit", lambda: _collect_kit(read))
    _guarded(read, "mémoire", lambda: _collect_memory(read, probe_memory))
    _guarded(read, "hygiène", lambda: _collect_hygiene(read))
    return read


def _collect_services(
    reads: list[_ProjectRead], *, now: datetime, watch: ServiceWatch, probe: Callable[[str, str], str]
) -> None:
    """Les services requis par les projets qui travaillent : une sonde par service, partagée entre projets."""
    for read in reads:
        if read.root is None or not read.activity:
            continue
        try:
            required = _required_services(read.root)
        except Exception as exc:
            _item(read, PROBE_FAILED, "services", "services", f"configuration mémoire illisible : {exc}")
            continue
        for service, url in required:
            seen = watch.observe(service, url, now, probe)
            if seen.alerted:
                word = _SERVICE_WORDS.get(seen.state, "inconnu")
                since = seen.first_failure_at
                _item(read, SERVICE, service, f"{service} {word}",
                      f"requis ({read.activity}) — {seen.failures} sondes négatives consécutives"
                      + (f" depuis {_hm(since)}" if since else ""),
                      since=since.isoformat() if since else None, service=service, url=url, state=seen.state)
            elif seen.resolved_at is not None and now - seen.resolved_at <= SERVICE_RESOLVED_VISIBLE:
                _item(read, SERVICE_RESOLVED, service, f"{service} de retour",
                      f"résolu automatiquement à {_hm(seen.resolved_at)}",
                      since=seen.resolved_at.isoformat(), service=service, url=url,
                      resolved_at=seen.resolved_at.isoformat())


def _dedupe_reviews(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une PR vue par deux projets du même dépôt (deux worktrees) : le premier du registre la garde."""
    seen: set[tuple[Any, Any]] = set()
    kept: list[dict[str, Any]] = []
    for item in items:
        if item["kind"] == PR_REVIEW:
            key = (item["ref"].get("repo"), item["ref"].get("pr"))
            if key in seen:
                continue
            seen.add(key)
        kept.append(item)
    return kept


def _entries(project_root: Path, *, portfolio: bool, slug: str | None) -> list[dict[str, Any]]:
    registry = load_registry()
    if portfolio:
        if not slug:
            return registry
        chosen = [e for e in registry if e.get("slug") == slug]
        if not chosen:
            raise FileNotFoundError(f"projet inconnu au registre : {slug}")
        return chosen
    root = project_root.resolve()
    own = slug_for_path(root)
    named = next((e for e in registry if e.get("slug") == own), None) if own else None
    return [{"slug": own or root.name, "name": (named or {}).get("name") or root.name, "path": str(root)}]


def attention_view(
    project_root: Path,
    *,
    portfolio: bool = False,
    slug: str | None = None,
    probe: bool = False,
    now: datetime | None = None,
    host_probe: SessionProbe | None = None,
    service_probe: Callable[[str, str], str] | None = None,
    watch: ServiceWatch | None = None,
    pr_reader: workspace_reviews.PrReader | None = None,
    review_cache: workspace_reviews.ReviewCache | None = None,
    pr_wait: float = workspace_reviews.ATTENTION_WAIT_S,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/attention``.

    Sans *portfolio*, le projet servi seul ; avec, tout le registre, ou le
    seul projet *slug* (inconnu : ``FileNotFoundError``, un 404). *probe*
    relance la sonde mémoire au lieu de lire son cache (« Réessayer »).
    Les projets sont lus en parallèle ; les services, une fois par service.
    Les PR (P48) : *pr_reader* remplace ``gh``, *review_cache* le cache
    partagé, *pr_wait* l'attente accordée à une lecture neuve ; une PR vue
    par deux projets du même dépôt n'est comptée qu'une fois, et
    ``reviews`` dit par projet si la lecture est encore en cours.
    """
    source = _ReviewSource(pr_reader, review_cache, pr_wait)
    moment = now or datetime.now(UTC)
    entries = _entries(project_root, portfolio=portfolio, slug=slug)
    if entries:
        with ThreadPoolExecutor(
            max_workers=min(len(entries), _PROJECT_MAX_WORKERS), thread_name_prefix="grimoire-attention"
        ) as pool:
            reads = list(pool.map(
                lambda e: _read_project(e, now=moment, probe_memory=probe, host_probe=host_probe, reviews=source),
                entries,
            ))
    else:
        reads = []
    _collect_services(
        reads, now=moment, watch=watch or _WATCH, probe=service_probe or service_state
    )
    items = _dedupe_reviews(item for read in reads for item in read.items)
    sections = attention_order(items)
    active = [i for i in items if i["kind"] != SERVICE_RESOLVED]
    counts: dict[str, int] = {}
    for item in active:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generated_at": moment.isoformat(),
        "scope": "portfolio" if portfolio else "project",
        "filters": {"project": slug or ""},
        "probed": probe,
        "sections": sections,
        "count": len(active),
        "counts": counts,
        "projects": [read.row for read in reads],
        "reviews": [{"project": read.row["slug"], **read.reviews} for read in reads if read.reviews],
        "summary": {
            "projects": len(reads),
            "readable": sum(1 for read in reads if read.row["state"] != "unreadable"),
        },
    }
