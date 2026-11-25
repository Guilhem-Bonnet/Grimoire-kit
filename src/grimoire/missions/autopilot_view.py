"""Le pilote automatique vu du cockpit (P83) : ce qui reste à l'humain, le journal des décisions, « Revenir ».

Lecture et gestes de relecture seulement : l'activation reste dans :class:`~grimoire.missions.autopilot.AutopilotStore`.

- :func:`partition` — pilote activé, « À traiter » ne garde que ce que le pilote ne tranchera pas : la liste fermée, les
  questions qu'il a laissées en confiance basse, et celles qu'il ne peut pas prendre (hors du périmètre du mandat). Chacune
  porte sa raison. Une question que l'orchestrateur répondra au prochain tick sort de la liste et du compteur.
- :func:`journal` — les :data:`~grimoire.missions.autopilot.DECIDED_EVENT` du ledger, du plus récent au plus ancien,
  avec ce que « Revenir » ferait.
- :func:`revert` — « Revenir » quand le geste l'est : une tâche abandonnée par le pilote est réouverte, une tâche rendue
  prête (réponse pas encore reprise par un worker) est bloquée pour que l'humain tranche ; une décision dont la réponse
  n'a jamais été inscrite est seulement close : le tick ne retranche plus la question (ni précédent ni défaut,
  elle est reportée à l'humain) ; une session pilote peut encore la trancher par jugement assumé (#1255, point 1,
  décision du propriétaire) ; sinon refus, rien d'écrit.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.file_memo import COARSE_CLOCK_NS
from grimoire.missions.autopilot import (
    DECIDED_EVENT,
    DEFERRED_EVENT,
    AutopilotStore,
    last_deferral,
    load_autopilot_policy,
    reserved_reason,
)
from grimoire.missions.mandate import AuthorityJournal, JournalError
from grimoire.missions.schemas import ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT

if TYPE_CHECKING:
    from grimoire.missions.decisions import PendingDecision
    from grimoire.missions.service import TaskService

__all__ = ["REVERTED_EVENT", "Held", "ViewJournal", "journal", "partition", "revert", "reverted_decisions"]

REVERTED_EVENT = "autopilot.reverted"

#: Pourquoi une question reste à l'humain pilote activé.
RESERVED_KIND = "reserved"
LOW_CONFIDENCE_KIND = "low_confidence"
OUT_OF_MANDATE_KIND = "out_of_mandate"

_OUT_OF_MANDATE = "le pilote ne peut pas la trancher : hors du périmètre du mandat"


_view_lock = threading.Lock()
_view_verified: dict[str, tuple[tuple[int, int, int, int], list[dict[str, Any]]]] = {}
_VIEW_MAX = 8
#: Sous Windows, ``st_ctime_ns`` est la date de création et non de changement : la clé ne verrait pas une réécriture en place.
_WINDOWS = os.name == "nt"


class ViewJournal(AuthorityJournal):
    """Le journal d'autorité pour la **lecture du cockpit** seulement : la chaîne n'est revérifiée que si le fichier a bougé.

    Mesuré le 2026-11-25 sur un poste réel : ~9 400 entrées, la chaîne de hash vérifiée plusieurs fois par projet et
    par requête, soit ~0,8 s de CPU à chaque ouverture de « À traiter ». Ici, une chaîne vérifiée est resservie tant
    que (date, taille, inode) du journal tiennent et que sa date avait déjà plus de deux secondes à la lecture
    (:data:`~grimoire.core.file_memo.COARSE_CLOCK_NS`). La tête, elle, est relue et comparée à chaque appel.

    Garanties : la clé comprend ``st_ctime_ns`` (que l'espace utilisateur ne peut pas fixer : restaurer la date d'un
    journal réécrit ne suffit pas), et rien n'est gardé tant que la date de modification OU de changement est dans la
    fenêtre d'horloge grossière. Toute écriture (:meth:`AuthorityJournal.append`) vérifie la chaîne complète par la lecture
    de la classe mère, jamais par celle-ci : un journal rompu n'est jamais prolongé, même par une écriture d'office
    (révocation) déclenchée depuis la vue. Les lecteurs de la vue (état du mandat, du pilote) voient ce que la lecture
    complète leur aurait donné, à l'octet près, tant que ctime, taille, inode et date tiennent.
    """

    def read(self) -> list[dict[str, Any]]:
        if _WINDOWS:
            return super().read()  # pas de ctime de changement : la vue relit le journal en entier
        head = self._read_head()
        try:
            info = self.path.stat()
        except OSError:
            return super().read()  # absent ou illisible : les refus de la classe mère, tels quels
        sig = (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)
        key = str(self.path)
        with _view_lock:
            known = _view_verified.get(key)
        if known is not None and known[0] == sig:
            entries = known[1]
            if entries and head is None:
                raise JournalError("journal d'autorité non vide sans tête : tête retirée ou journal remplacé")
            if head is not None and (len(entries) <= head[0] or entries[head[0]]["hash"] != head[1]):
                raise JournalError(
                    f"journal d'autorité tronqué : {len(entries)} entrée(s) pour une tête à l'entrée {head[0] + 1}"
                )
            return [dict(entry) for entry in entries]
        entries = super().read()
        if time.time_ns() - max(info.st_mtime_ns, info.st_ctime_ns) > COARSE_CLOCK_NS:
            with _view_lock:
                if len(_view_verified) >= _VIEW_MAX:
                    _view_verified.clear()
                _view_verified[key] = (sig, entries)
        return [dict(entry) for entry in entries]


@dataclass(frozen=True, slots=True)
class Held:
    """Une décision en attente qui reste à l'humain, avec sa raison."""

    decision: PendingDecision
    kind: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "reason": self.reason}


def partition(service: TaskService) -> tuple[bool, list[Held]]:
    """``(pilote actif, ce qui reste à l'humain)``. Pilote coupé : ``(False, [])`` — l'appelant garde tout."""
    from grimoire.missions.decisions import pending_decisions
    from grimoire.missions.mandate import MandateStore
    from grimoire.missions.orchestrator_session import eligible_questions

    root = service.project_root
    store = MandateStore(root, ViewJournal())  # lecture du cockpit : chaîne vérifiée gardée par empreinte de fichier
    if not AutopilotStore(root, store.journal).state().active:
        return False, []
    eligible = {(q.task_id, q.question_id) for q in eligible_questions(service, store)}
    policy = load_autopilot_policy(root)
    held: list[Held] = []
    for decision in pending_decisions(service.ledger):
        questions = decision.questions or (decision.question,)
        if all((decision.task_id, q.id) in eligible for q in questions):
            continue
        task = service.ledger.get_task(decision.task_id)
        events = service.ledger.events_for(decision.task_id)
        kind, reason = OUT_OF_MANDATE_KIND, _OUT_OF_MANDATE
        for q in questions:
            if task is not None and (why := reserved_reason(q, task, root, policy)):
                kind, reason = RESERVED_KIND, why
                break
            if why := last_deferral(events, q.id):
                kind, reason = LOW_CONFIDENCE_KIND, why
                break
        held.append(Held(decision, kind, reason))
    return True, held


# ── Journal ──────────────────────────────────────────────────────────────────


def _unanswered(service: TaskService, task_id: str, question_id: str) -> bool:
    """La décision est inscrite mais sa réponse ne l'a jamais été (arrêt entre les deux) : la question attend encore."""
    after_decision = False
    for event in service.ledger.events_for(task_id):
        if event.event_type == DECIDED_EVENT and event.payload.get("question_id") == question_id:
            after_decision = True
        elif (
            after_decision
            and event.event_type == HUMAN_ANSWERED_EVENT
            and event.payload.get("question_id") == question_id
        ):
            return False
    return after_decision


def _how_to_revert(service: TaskService, task_id: str, question_id: str = "") -> str:
    """Ce que « Revenir » ferait maintenant (``""`` : le geste n'est plus réversible).

    ``unanswered`` : la décision n'a jamais été suivie de sa réponse et la tâche attend toujours cette question ;
    « Revenir » ne fait alors que clore l'entrée, la question reste à l'humain.
    """
    task = service.ledger.get_task(task_id)
    if task is None:
        return ""
    if question_id and task.status is TaskState.WAITING and _unanswered(service, task_id, question_id):
        return "unanswered"
    if task.status is TaskState.FAILED:
        return "reopen"
    if task.status is TaskState.READY:
        return "hold"
    return ""


def _pilot_answer(event: Any) -> bool:
    return bool(event.event_type == HUMAN_ANSWERED_EVENT and event.payload.get("origin") == ORIGIN_ORCHESTRATOR)


def _question_key(event: Any) -> tuple[Any, Any]:
    p = event.payload
    return p.get("task_id") or event.entity_id, p.get("question_id")


def reverted_decisions(events: list[Any]) -> dict[str, str]:
    """``{identifiant de la décision: date de la reprise}`` : une reprise porte sur **une** décision, jamais sur la question.

    Une reprise nomme sa décision (``decision_id``). Une reprise ancienne, sans identifiant, vaut pour les décisions de la
    même question inscrites avant elle (l'ordre du ledger) : jamais pour une décision postérieure. *events* est dans
    l'ordre du ledger.

    Règle de lecture, insensible à l'ordre d'écriture entre « Revenir » et la réponse : une reprise ``unanswered`` ne
    couvre sa décision D que si aucune réponse du pilote ne suit D **avant la décision suivante** de la même question
    (une réponse appartient à la décision qui la précède immédiatement). Sinon la réponse est appliquée : la reprise est
    sans effet, la décision reste visible et se défait par le chemin normal. La réponse à une décision postérieure ne
    rouvre donc pas une décision que l'humain a reprise sans qu'elle ait été appliquée.
    """
    decisions: dict[tuple[Any, Any], list[tuple[str, int]]] = {}
    answers: dict[tuple[Any, Any], list[int]] = {}
    for index, event in enumerate(events):
        if event.event_type == DECIDED_EVENT:
            decisions.setdefault(_question_key(event), []).append((event.id, index))
        elif _pilot_answer(event):
            answers.setdefault(_question_key(event), []).append(index)

    def answered(key: tuple[Any, Any], at: int) -> bool:
        following = min((i for _id, i in decisions.get(key, []) if i > at), default=len(events))
        return any(at < i < following for i in answers.get(key, []))

    out: dict[str, str] = {}
    for index, event in enumerate(events):
        if event.event_type != REVERTED_EVENT:
            continue
        key = _question_key(event)
        named = event.payload.get("decision_id")
        known = [(i, at) for i, at in decisions.get(key, []) if at < index]
        for decision_id, at in [(i, at) for i, at in known if i == str(named)] if named else known:
            if event.payload.get("how") == "unanswered" and answered(key, at):
                continue
            out[decision_id] = event.created_at
    return out


def journal(service: TaskService, limit: int = 200) -> list[dict[str, Any]]:
    """Les décisions du pilote, la plus récente d'abord ; chacune dit si elle a été reprise et si elle se défait."""
    events = service.ledger.list_events()
    reverted = reverted_decisions(events)
    out: list[dict[str, Any]] = []
    for event in reversed(events):
        if event.event_type != DECIDED_EVENT:
            continue
        p = event.payload
        task = service.ledger.get_task(str(p.get("task_id") or ""))
        undo = (
            ""
            if event.id in reverted
            else _how_to_revert(service, str(p.get("task_id") or ""), str(p.get("question_id") or ""))
        )
        out.append(
            {
                "id": event.id,
                "ts": event.created_at,
                "task_id": p.get("task_id", ""),
                "title": task.title if task is not None else "",
                "question_id": p.get("question_id", ""),
                "question": p.get("question", ""),
                "kind": p.get("kind", ""),
                "option": p.get("option", ""),
                "justification": p.get("justification", ""),
                "confidence": p.get("confidence", ""),
                "source": p.get("recommendation_source", ""),
                "recommended": p.get("recommended", ""),
                "precedent": p.get("precedent") if isinstance(p.get("precedent"), dict) else None,
                "reverted_at": reverted.get(event.id, ""),
                "revert": undo,
            }
        )
        if len(out) >= limit:
            break
    return out


def revert(service: TaskService, task_id: str, question_id: str, actor: str) -> dict[str, Any]:
    """« Revenir » sur la décision du pilote pour *question_id* : rouvre la tâche ou la rend à l'humain.

    Refuse (rien n'est écrit) si aucune décision du pilote ne porte cette question, si elle a déjà été reprise, ou si la
    tâche a déjà avancé (un worker a repris sa réponse : le geste n'est plus réversible).
    """
    history = service.ledger.events_for(task_id)
    decided = [e for e in history if e.event_type == DECIDED_EVENT and e.payload.get("question_id") == question_id]
    if not decided:
        raise GrimoireMissionError(f"{task_id} : aucune décision du pilote pour la question {question_id}")
    done = reverted_decisions(history)
    if decided[-1].id in done:  # la reprise porte sur la dernière décision : une décision postérieure reste reprenable
        raise GrimoireMissionError(f"{task_id} : la décision du pilote pour {question_id} est déjà reprise")
    how = _how_to_revert(service, task_id, question_id)
    option = str(decided[-1].payload.get("option") or "")
    if how == "unanswered":
        # Rien n'a été appliqué : la question reste posée, et le pilote ne la retranche plus (report à l'humain, le même
        # que celui d'une confiance basse : ni précédent ni option par défaut ne la reprennent au tick suivant).
        service.ledger.append_event(
            DEFERRED_EVENT,
            task_id,
            "task",
            actor,
            {
                "task_id": task_id,
                "question_id": question_id,
                "reason": "décision du pilote reprise par l'humain : à lui de trancher",
            },
        )
    elif how == "reopen":
        service.transition(task_id, TaskState.READY, actor, f"décision du pilote reprise par l'humain : {option}")
    elif how == "hold":
        service.transition(
            task_id,
            TaskState.BLOCKED,
            actor,
            f"décision du pilote reprise par l'humain : « {option} » est à retrancher",
        )
    else:
        raise GrimoireMissionError(
            f"{task_id} : la décision du pilote n'est plus réversible (la tâche a déjà repris sa réponse)"
        )
    service.ledger.append_event(
        REVERTED_EVENT,
        task_id,
        "task",
        actor,
        {
            "task_id": task_id,
            "question_id": question_id,
            "decision_id": decided[-1].id,
            "how": how,
            "option": option,
            "at": datetime.now(UTC).isoformat(),
        },
    )
    return {"task_id": task_id, "question_id": question_id, "how": how}
