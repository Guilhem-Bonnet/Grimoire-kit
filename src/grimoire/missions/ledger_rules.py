"""Règles d'attente et de dépendances du ledger, sorties de ``ledger.py`` (plafond de 1500 lignes).

Fonctions sans état : la garde des transitions vers/depuis ``waiting`` (K6), l'attente que
la récolte pose à la place d'un silence, la lecture du graphe de dépendances ``blocks``
(W5-01) et la lecture du journal ligne à ligne, qui rend les lignes illisibles au lieu de les
taire (K12). Le ledger les appelle sous son verrou ; rien ici n'écrit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.lease import MAX_ATTEMPTS, backoff_delay
from grimoire.missions.questions import QUESTION_EVENTS, replay_question
from grimoire.missions.schemas import DEFAULT_CLAIM_TTL_SECONDS, DependencyKind, MissionTask, TaskDependency, TaskState
from grimoire.missions.session_link import resume_command
from grimoire.missions.waiting import Question, ResumeHint, Waiting, answer_hint, new_question_id

if TYPE_CHECKING:
    from grimoire.missions.ledger import LeaseView

__all__ = [
    "UnjudgedClaimError", "blocks_path", "default_claim_expiry", "deferred_waiting", "parse_jsonl", "read_jsonl", "replay_link", "replay_steering", "unclosed_blockers", "unfenced_question",
    "waiting_guard",
]


class UnjudgedClaimError(Exception):
    """Levée par ``branch_hold`` quand le bail n'est plus celui que l'appelant a jugé : la tâche n'est pas jugée à ce tick (K12-SC2-02)."""


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], tuple[int, ...]]:
    """Les objets d'un journal JSON ligne à ligne, et les numéros (base 1) des lignes illisibles, ignorées.

    Une coupure de courant pendant un ajout laisse une dernière ligne tronquée, parfois au milieu
    d'un caractère multi-octet : la ligne est sautée — le rejeu reste celui du préfixe cohérent — et
    son numéro rendu, jamais tu (K12, scénario H). Chaque ligne est décodée seule, en UTF-8 strict :
    un octet tronqué n'emporte pas tout le journal. Le découpage se fait sur ``\\n`` seulement — un
    ``U+2028`` écrit brut par ``ensure_ascii=False`` reste dans sa ligne (``str.splitlines`` la coupait).
    """
    if not path.exists():
        return [], ()
    return parse_jsonl(path.read_bytes())


def parse_jsonl(data: bytes, first_line: int = 1) -> tuple[list[dict[str, Any]], tuple[int, ...]]:
    """Comme :func:`read_jsonl` sur des octets déjà lus ; ``first_line`` numérote la première ligne (queue d'un instantané, K12 x K13)."""
    records: list[dict[str, Any]] = []
    unreadable: list[int] = []
    for number, chunk in enumerate(data.split(b"\n"), start=first_line):
        if not chunk.strip():
            continue
        try:
            raw = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict):
            records.append(raw)
        else:
            unreadable.append(number)
    return records, tuple(unreadable)


def waiting_guard(
    task: MissionTask,
    to_state: TaskState,
    extra_payload: dict[str, Any] | None,
    lease_epoch: int | None,
    waker: bool,
    attested: bool = False,
) -> None:
    """Les trois règles de l'état ``waiting`` (K6, §2.1), tenues ici pour qu'aucune surface ne les contourne.

    - vers ``waiting`` : l'attente est écrite, nommée et valide (``task ask`` / ``task wait``) ;
    - ``waiting → ready`` : le tick seul (:meth:`wake`), jamais un geste ``move`` ;
    - ``waiting → running`` : la même session, au même epoch — reprise sur place, sauf
      ``waiting(human)`` : une question en attente (``task ask``, ou posée par la récolte) ne
      se lève que par la réponse humaine (``ready``), jamais par un agent — hors *attested* (:meth:`resume_attested`).
    """
    if to_state is TaskState.WAITING:
        raw = (extra_payload or {}).get("waiting")
        problem = Waiting.from_dict(raw).problem() if isinstance(raw, dict) else "aucune attente décrite"
        if problem:
            raise GrimoireMissionError(
                f"Transition vers waiting refusée pour {task.id} : {problem} "
                "(poser l'attente par `grimoire task ask` ou `grimoire task wait`)"
            )
    if task.status is TaskState.WAITING and to_state is TaskState.READY and not waker:
        raise GrimoireMissionError(
            f"Transition refusée pour {task.id} : waiting → ready est réservé au tick "
            "(`grimoire task wake`) ; une réponse humaine (`grimoire task answer`) le déclenche"
        )
    if (
        task.status is TaskState.WAITING and to_state is TaskState.RUNNING
        and task.waiting is not None and task.waiting.reason == "human" and not attested
    ):
        asked = task.waiting.question.id if task.waiting.question is not None else "?"
        raise GrimoireMissionError(
            f"Reprise refusée pour {task.id} : waiting(human) attend la réponse de l'humain (question {asked}) ; "
            f"seul un humain la lève, par {answer_hint(task.id, asked if asked != '?' else '')} "
            "— un agent ne reprend pas sur sa propre question (la session reste libre : "
            "`grimoire task claim|attach <autre-tâche>`, `grimoire cockpit serve`)"
        )
    if task.status is TaskState.WAITING and to_state is TaskState.RUNNING and lease_epoch is None:
        raise GrimoireMissionError(
            f"Reprise refusée pour {task.id} : waiting → running est la reprise sur place de la même session, "
            "au même epoch — présenter l'epoch tenu (`grimoire task resume --epoch`)"
        )

def deferred_waiting(
    task: MissionTask, view: LeaseView, cause: str, attempts: int, question: str, moment: datetime,
    reopens: datetime | None = None,
) -> Waiting:
    """L'attente que la récolte pose à la place d'un silence : quota, ou une décision humaine nommée (§2.3, §2.6).

    Le quota porte une échéance : *reopens* si elle est future, sinon un délai
    ``backoff_delay`` des tentatives (au moins un cran) — le tick la rend alors ``ready``.
    """
    hint = ResumeHint(
        session_id=view.session_id, host=view.host,
        resume_command=resume_command(view.host, view.session_id),
    )
    since = moment.isoformat()
    if cause == "quota":
        due = reopens if reopens is not None and reopens > moment else moment + backoff_delay(max(attempts, 1))
        return Waiting("quota", due.isoformat(), None, hint, since)
    if cause == "retry_exhausted":
        text = (
            f"{task.id} a perdu son bail {attempts} fois (plafond {MAX_ATTEMPTS}) : "
            "la relancer encore, ou l'abandonner ?"
        )
        options, kind = ("réessayer", "abandonner"), "retry_exhausted"
    else:  # resume_unfenced (hôte sans frein) ou resume_branch (commits non poussés, K12) : le texte est fourni
        text, options, kind = question, ("reprendre", "abandonner"), cause
    return Waiting("human", "", Question(new_question_id(), kind, text, options, "tick", since), hint, since)

def unfenced_question(view: LeaseView) -> str:
    """La question de §2.4 : un hôte sans frein vérifié ne se relance pas, on demande (K3)."""
    who = f"la session {view.session_id}" if view.session_id else f"la session de {view.actor_id}"
    version = f" {view.host_version}" if view.host_version else ", version inconnue"
    return (
        f"{who} (hôte {view.host}{version}, sans frein) ne donne plus signe ; reprendre ? "
        "Frein non prouvé pour cette version : `orchestrator.probe_host: true` (politique) la sonde au prochain tick "
        "(deux appels payants à l'hôte), ou `grimoire host status --probe`."
    )

def unclosed_blockers(tasks: Mapping[str, MissionTask], task: MissionTask) -> list[str]:
    """Les dépendances ``blocks`` de *task* qui ne sont pas closes, chacune avec son état."""
    out: list[str] = []
    for dep in task.dependencies:
        if dep.kind is not DependencyKind.BLOCKS:
            continue
        target = tasks.get(dep.target)
        if target is None:
            out.append(f"{dep.target} (inconnue du ledger)")
        elif target.status is TaskState.CANCELLED:
            # Terminal : elle ne sera jamais close (#906). Le lien se retire, il ne se contourne pas.
            out.append(
                f"{dep.target} (cancelled, ne sera jamais close : si elle est remplacée, "
                f"`grimoire task unlink {task.id} --depends-on {dep.target} --reason …` puis `task link` vers la remplaçante)"
            )
        elif target.status is not TaskState.CLOSED:
            out.append(f"{dep.target} ({target.status.value})")
    return out

def blocks_path(tasks: Mapping[str, MissionTask], start: str, goal: str) -> list[str]:
    """Un chemin de dépendances ``blocks`` de *start* vers *goal* (``[start, …, goal]``), ``[]`` s'il n'y en a pas."""
    stack = [(start, [start])]
    seen: set[str] = set()
    while stack:
        node, path = stack.pop()
        if node == goal:
            return path
        if node in seen:
            continue
        seen.add(node)
        current = tasks.get(node)
        if current is None:
            continue
        for dep in current.dependencies:
            if dep.kind is DependencyKind.BLOCKS:
                stack.append((dep.target, [*path, dep.target]))
    return []


def replay_link(tasks: dict[str, MissionTask], raw: dict[str, Any], payload: dict[str, Any], *, remove: bool = False) -> None:
    """Ajoute la dépendance d'un événement de lien à la tâche source, sans doublon ; *remove* la retire (#906)."""
    tid = str(raw.get("entity_id") or payload.get("task_id") or "")
    current = tasks.get(tid)
    if current is None:
        return
    try:
        if "dependency" in payload:
            dep = TaskDependency.from_dict(payload["dependency"])
        else:
            dep = TaskDependency(DependencyKind(str(payload.get("kind", "blocks"))), str(payload["depends_on"]))
    except (KeyError, ValueError, TypeError):
        return
    if remove:
        kept = [d.to_dict() for d in current.dependencies if d != dep]
        if len(kept) != len(current.dependencies):
            tasks[tid] = MissionTask.from_dict({**current.to_dict(), "dependencies": kept})
    elif dep not in current.dependencies:
        tasks[tid] = MissionTask.from_dict(
            {**current.to_dict(), "dependencies": [d.to_dict() for d in (*current.dependencies, dep)]}
        )


def replay_steering(tasks: dict[str, MissionTask], evt_type: str, payload: dict[str, Any], at: str = "") -> None:
    """Pilotage humain (issue #638) : priorité et journal de consignes ; questions et réponses (W6-07).

    Chaque événement ne touche qu'un champ dérivé de la tâche ; le texte
    d'une consigne n'est jamais réécrit — livraison et accusé de réception
    s'ajoutent comme horodatages sur l'entrée existante. *at* : l'horodatage de l'événement.
    """
    tid = payload.get("task_id", "")
    task = tasks.get(tid)
    if task is None:
        return
    data = task.to_dict()
    if evt_type in QUESTION_EVENTS:
        replay_question(data, evt_type, payload, at)
    elif evt_type == "task.prioritized":
        data["priority"] = payload.get("to_priority", "")
    elif evt_type == "task.directive_added":
        data["directives"] = [*data.get("directives", []), dict(payload.get("directive", {}))]
    elif evt_type == "task.directive_delivered":
        ids, at = set(payload.get("directive_ids", [])), str(payload.get("delivered_at", ""))
        data["directives"] = [
            {**d, "delivered_at": d.get("delivered_at") or at} if d.get("id") in ids else d
            for d in data.get("directives", [])
        ]
    elif evt_type == "task.directive_acknowledged":
        did, at = payload.get("directive_id"), str(payload.get("acknowledged_at", ""))
        data["directives"] = [
            {**d, "acknowledged_at": d.get("acknowledged_at") or at} if d.get("id") == did else d
            for d in data.get("directives", [])
        ]
    tasks[tid] = MissionTask.from_dict(data)


def default_claim_expiry(claim: Any, claimed_at: str) -> Any:
    """Un claim écrit sans ``expires_at`` reçoit celui de son événement plus le TTL par défaut (W1-07).

    ``TaskClaim.is_expired`` tient « pas d'expiration » pour « jamais » : un claim hérité d'avant
    les baux gardait ses fichiers à vie. Le rejeu le dérive de l'horodatage de l'événement qui l'a
    posé — déterministe, sans écrire à la lecture — et un claim plus vieux que le TTL est alors
    expiré, donc libère ses fichiers. Sans horodatage lisible rien n'est inventé : le claim reste tel quel.
    """
    if not isinstance(claim, dict) or claim.get("expires_at"):
        return claim
    try:
        at = datetime.fromisoformat(claimed_at)
    except ValueError:
        return claim
    return {**claim, "expires_at": (at + timedelta(seconds=DEFAULT_CLAIM_TTL_SECONDS)).isoformat()}
