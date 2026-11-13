"""La jurisprudence du pilote automatique : une réponse humaine vaut pour les questions de la même classe.

Constat : la même question de conduite revient à l'humain d'une tâche à l'autre, et rien ne retient sa réponse au-delà
de la tâche où il l'a donnée. Ce module ne retient rien de plus que le ledger : un **précédent** est une réponse
d'origine ``human`` (``task.human_answered``) à une question que **le kit a écrite**, relue à chaque décision.

**Aucun fichier à part.** Les précédents sont *dérivés* des événements du ledger : la réponse humaine, l'oubli
(:data:`FORGOTTEN_EVENT`, écrit par le canal humain) et le « Revenir » du cockpit. Un fichier de précédents qu'un agent
pourrait écrire serait un moyen de se donner raison ; ici, forger un précédent demande de forger une réponse humaine
au ledger — la surface de confiance existante, pas une de plus.

**La classe est calculée par le kit, jamais déclarée** (:func:`question_class`). Elle vient de ce que seul le code du
kit écrit : la cause codée de l'événement d'escalade du relecteur ou du merger (``review:not_ready_cycles``,
``merge:ci_red``…), ou la nature d'une question de la récolte (``reap:resume_branch``…). Le texte, les options et la
raison d'une question n'y entrent pas : une question d'agent n'a pas de classe, donc pas de précédent — un agent ne
rédige pas sa question pour tomber sous une réponse favorable. La liste des classes est fermée
(:data:`PRECEDENT_CLASSES`) : une escalade qui touche à l'acceptation ou à la clôture (pack de preuve absent, clôture
refusée) ou au journal d'autorité, comme toute question « fusionner ? » ou « accepter ? », n'en fait pas partie.

**Seule la dernière réponse humaine d'une classe compte**, et seulement tant qu'elle est en vigueur
(:func:`precedents`). « Abandonner » n'est jamais appliqué d'après un précédent, mais tant que c'est le dernier mot de
l'humain pour la classe, l'option par défaut ne reprend pas contre lui : la question se juge. Par ailleurs : oubliée (``task questions --forget``), reprise (« Revenir » sur l'une de ses applications) ou
expirée (``autopilot.precedent_days``), elle ne s'applique plus, et une réponse plus ancienne ne revient pas à sa
place. Une décision du pilote — recommandation, jugement, option par défaut, précédent appliqué — n'est jamais une
réponse humaine : pas de jurisprudence en chaîne.

**Échec fermé** : un ledger qui ne se lit pas, un événement d'oubli ou une application de précédent sans identifiant
(on ne sait plus ce qui a été retiré ou repris) — :class:`PrecedentsUnreadableError`, aucun précédent n'est appliqué,
et l'appelant le dit.

L'application elle-même (réglage ``autopilot.precedents``, liste fermée, options sûres, worker à l'arrêt, borne par
tâche) est dans :func:`grimoire.missions.orchestrator_session.precedent_plan`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.autopilot import DECIDED_EVENT, DEFAULT_PRECEDENT_DAYS, PRECEDENT_SOURCE, PrecedentRef
from grimoire.missions.autopilot_view import REVERTED_EVENT
from grimoire.missions.schemas import ORIGIN_HUMAN
from grimoire.missions.waiting import HUMAN_ANSWERED_EVENT, QUESTION_ASKED_EVENT, Question

if TYPE_CHECKING:
    from grimoire.missions.schemas import LedgerEvent
    from grimoire.missions.service import TaskService

__all__ = [
    "ACTIVE",
    "EXPIRED",
    "FORGOTTEN",
    "FORGOTTEN_EVENT",
    "MERGE_CAUSES",
    "PRECEDENT_CLASSES",
    "REVERTED",
    "REVIEW_CAUSES",
    "SUPERSEDED",
    "Precedent",
    "PrecedentPlan",
    "PrecedentsUnreadableError",
    "forget",
    "precedents",
    "question_class",
]

#: Un précédent retiré par l'humain (``task questions --forget``), au ledger, sur la tâche de la réponse d'origine.
FORGOTTEN_EVENT = "autopilot.precedent_forgotten"
#: Les événements que le code du kit écrit et qui nomment la question d'une escalade ou d'une récolte.
_REVIEW_ESCALATED = "task.review_escalated"
_MERGE_ESCALATED = "task.merge_escalated"
_REAP_DEFERRED = "task.reap_deferred"
_TRANSITIONED = "task.transitioned"

#: Les causes d'escalade du relecteur qui font une classe (``reviewer._escalate``, champ ``cause`` de l'événement).
REVIEW_CAUSES = frozenset({"not_ready_cycles", "no_worker_session", "unjudgeable_reviews"})
#: Celles du merger. ``no_evidence_pack`` et ``close_refused`` (acceptation, clôture) n'en sont pas, à dessein.
MERGE_CAUSES = frozenset({"ci_red", "conflict", "desync", "stale_ready", "no_worker_session"})
#: Les natures que la récolte écrit elle-même (``ledger_rules.deferred_waiting``).
_REAP_KINDS = frozenset({"resume_branch", "resume_unfenced", "retry_exhausted"})
#: La tâche garée par le tick sur une erreur du fournisseur (``launcher._park_provider_error``).
_PROVIDER_CLASS = "launcher:provider_error"
#: La liste fermée des classes : hors d'elle, une question n'a pas de précédent.
PRECEDENT_CLASSES = frozenset(
    {f"review:{c}" for c in REVIEW_CAUSES}
    | {f"merge:{c}" for c in MERGE_CAUSES}
    | {f"reap:{k}" for k in _REAP_KINDS}
    | {_PROVIDER_CLASS}
)

#: L'état d'un précédent : seul :data:`ACTIVE` s'applique.
ACTIVE = "active"
EXPIRED = "expired"
FORGOTTEN = "forgotten"
REVERTED = "reverted"
SUPERSEDED = "superseded"
_TEXT_SIZE = 400


class PrecedentsUnreadableError(GrimoireMissionError):
    """Les précédents ne se lisent pas au ledger : aucun n'est appliqué (échec fermé), et l'appelant le dit."""


@dataclass(frozen=True, slots=True)
class Precedent:
    """Une réponse humaine à une question écrite par le kit, relue au ledger.

    *id* : l'identifiant de l'événement ``task.human_answered`` qui la porte. *state* : en vigueur
    (:data:`ACTIVE`), ou pourquoi elle ne s'applique plus.
    """

    id: str
    question_class: str
    kind: str
    option: str
    answered_at: str
    question: str
    task_id: str
    question_id: str
    state: str = ACTIVE

    def ref(self) -> PrecedentRef:
        return PrecedentRef(self.id, self.question_class, self.question, self.answered_at)

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id, "class": self.question_class, "kind": self.kind, "option": self.option,
            "answered_at": self.answered_at, "question": self.question, "task_id": self.task_id,
            "question_id": self.question_id, "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class PrecedentPlan:
    """Ce que le pilote tire de la jurisprudence pour une question : l'option à appliquer, ou pourquoi aucune.

    *unreadable* : les précédents ne se lisent pas — rien n'est appliqué et *reason* le dit, pour que l'appelant le
    porte à l'humain (issue de l'étape du tick, raison de la question laissée). *contrary* : la dernière réponse
    humaine de la classe est « abandonner » — elle ne s'applique jamais d'elle-même, mais l'option par défaut ne
    reprend pas contre elle : la question se juge.
    """

    option: str = ""
    precedent: Precedent | None = None
    reason: str = ""
    unreadable: bool = False
    contrary: bool = False


def question_class(events: list[LedgerEvent], question: Question) -> str:
    """La classe de *question* (``""`` : aucune), calculée sur ce que le code du kit a écrit au ledger de sa tâche.

    - escalade du relecteur ou du merger : l'événement d'escalade qui nomme la question porte une ``cause`` codée ;
      celle du merger doit être le « reprendre ou abandonner ? » bloquant (jamais « fusionner ? » ni « accepter ? ») ;
    - récolte : question d'origine ``tick`` dont un ``task.reap_deferred`` porte la nature et le texte ;
    - tâche garée sur une erreur du fournisseur : question d'origine ``tick`` posée par le tick, ``retry_exhausted``.

    Ni le texte, ni les options, ni la raison de la question ne choisissent la classe : un agent les écrit.
    """
    qid = question.id
    for event in events:
        payload = event.payload
        if payload.get("question_id") != qid or question.kind != "choice":
            continue
        cause = payload.get("cause")
        if not isinstance(cause, str):
            continue
        if event.event_type == _REVIEW_ESCALATED and cause in REVIEW_CAUSES:
            return f"review:{cause}"
        if (
            event.event_type == _MERGE_ESCALATED and cause in MERGE_CAUSES
            and payload.get("gesture") == "bounce" and payload.get("blocking") is True
        ):
            return f"merge:{cause}"
    if question.origin != "tick" or question.kind not in _REAP_KINDS:
        return ""
    for event in events:
        asked = event.payload.get("question")
        if not isinstance(asked, dict):
            continue
        if event.event_type == QUESTION_ASKED_EVENT and asked.get("id") == qid:
            return _PROVIDER_CLASS if question.kind == "retry_exhausted" else ""
        if (
            event.event_type == _REAP_DEFERRED and event.payload.get("cause") == question.kind
            and asked.get("text") == question.text
        ):
            return f"reap:{question.kind}"
    return ""


def _asked(events: list[LedgerEvent]) -> dict[str, Question]:
    """Les questions posées sur une tâche, par identifiant : ``task.question_asked``, ou l'attente d'une récolte."""
    found: dict[str, Question] = {}
    for event in events:
        if event.event_type == QUESTION_ASKED_EVENT:
            raw = event.payload.get("question")
        elif event.event_type == _TRANSITIONED:
            waiting = event.payload.get("waiting")
            raw = waiting.get("question") if isinstance(waiting, dict) else None
        else:
            continue
        if isinstance(raw, dict) and raw.get("id"):
            found.setdefault(str(raw["id"]), Question.from_dict(raw))
    return found


def _moment(stamp: str) -> datetime | None:
    try:
        at = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def precedents(
    service: TaskService, *, days: int = DEFAULT_PRECEDENT_DAYS, now: datetime | None = None
) -> list[Precedent]:
    """Les précédents du projet, du plus ancien au plus récent, chacun avec son état — relus au ledger.

    Un précédent par réponse d'origine ``human`` à une question dont le kit a calculé la classe. État :
    :data:`FORGOTTEN` (retiré par l'humain), :data:`REVERTED` (une de ses applications a été reprise par « Revenir »),
    :data:`SUPERSEDED` (une réponse humaine plus récente existe pour la classe), :data:`EXPIRED` (plus de *days* jours,
    ou date illisible), sinon :data:`ACTIVE`. Lève :class:`PrecedentsUnreadableError` si la lecture n'est pas sûre.
    """
    try:
        return _derive(service.ledger.list_events(), days, now or datetime.now(UTC))
    except PrecedentsUnreadableError:
        raise
    except Exception as exc:  # un ledger qui ne se lit pas, un événement qui ne se relit pas : rien n'est appliqué
        raise PrecedentsUnreadableError(f"précédents illisibles (ledger : {type(exc).__name__})") from exc


def _derive(events: list[LedgerEvent], days: int, moment: datetime) -> list[Precedent]:
    """Les précédents lus dans *events* (voir :func:`precedents`)."""
    by_task: dict[str, list[LedgerEvent]] = {}
    forgotten: set[str] = set()
    applied: dict[tuple[str, str], str] = {}
    reverted_keys: set[tuple[str, str]] = set()
    for event in events:
        by_task.setdefault(event.entity_id, []).append(event)
        payload = event.payload
        key = (str(payload.get("task_id") or event.entity_id), str(payload.get("question_id") or ""))
        if event.event_type == FORGOTTEN_EVENT:
            ref = payload.get("precedent")
            if not isinstance(ref, str) or not ref.strip():
                raise PrecedentsUnreadableError(f"précédents illisibles (oubli {event.id} sans identifiant de précédent)")
            forgotten.add(ref.strip())
        elif event.event_type == DECIDED_EVENT and payload.get("recommendation_source") == PRECEDENT_SOURCE:
            used = payload.get("precedent")
            ref = used.get("id") if isinstance(used, dict) else None
            if not isinstance(ref, str) or not ref.strip():
                raise PrecedentsUnreadableError(f"précédents illisibles (décision {event.id} sans précédent nommé)")
            applied[key] = ref.strip()
        elif event.event_type == REVERTED_EVENT:
            reverted_keys.add(key)
    reverted = {ref for key, ref in applied.items() if key in reverted_keys}

    found: list[Precedent] = []
    questions: dict[str, dict[str, Question]] = {}
    for event in events:
        payload = event.payload
        if event.event_type != HUMAN_ANSWERED_EVENT or payload.get("origin") != ORIGIN_HUMAN:
            continue
        task_events = by_task.get(event.entity_id, [])
        if event.entity_id not in questions:
            questions[event.entity_id] = _asked(task_events)
        question = questions[event.entity_id].get(str(payload.get("question_id") or ""))
        option = " ".join(str(payload.get("answer") or "").split())
        if question is None or not option:
            continue
        klass = question_class(task_events, question)
        if klass not in PRECEDENT_CLASSES:
            continue
        found.append(Precedent(
            id=event.id, question_class=klass, kind=question.kind, option=option[:_TEXT_SIZE],
            answered_at=str(event.created_at), question=" ".join(question.text.split())[:_TEXT_SIZE],
            task_id=event.entity_id, question_id=question.id,
        ))
    latest = {p.question_class: p.id for p in found}
    limit = timedelta(days=days)

    def state(precedent: Precedent) -> str:
        if precedent.id in forgotten:
            return FORGOTTEN
        if precedent.id in reverted:
            return REVERTED
        if latest[precedent.question_class] != precedent.id:
            return SUPERSEDED
        at = _moment(precedent.answered_at)
        return ACTIVE if at is not None and moment - at <= limit else EXPIRED

    return [replace(p, state=state(p)) for p in found]


def forget(service: TaskService, precedent_id: str, actor: str, *, origin: str) -> Precedent:
    """Retire le précédent *precedent_id* — **canal humain seulement** — par un événement au ledger.

    *origin* est posé par le canal d'appel, comme pour ``task answer`` : jamais lu d'un champ que l'appelant remplit.
    Aucun mandat n'ouvre ce geste à l'orchestrateur. Refuse un précédent inconnu ou déjà oublié.
    """
    if origin != ORIGIN_HUMAN:
        raise GrimoireMissionError(
            f"Oubli refusé : origine {origin!r}. Seul le canal humain (CLI interactive) retire un précédent — "
            "ni un agent, ni l'orchestrateur sous mandat"
        )
    wanted = precedent_id.strip()
    found = next((p for p in precedents(service) if p.id == wanted), None)
    if found is None:
        raise GrimoireMissionError(f"Précédent {wanted!r} inconnu (`grimoire task questions --precedents` les liste)")
    if found.state == FORGOTTEN:
        raise GrimoireMissionError(f"Précédent {wanted} déjà oublié")
    payload: dict[str, Any] = {
        "precedent": found.id, "task_id": found.task_id, "question_id": found.question_id,
        "class": found.question_class, "option": found.option, "origin": origin, "actor_id": actor,
    }
    service.ledger.append_event(FORGOTTEN_EVENT, found.task_id, "task", actor, payload)
    return replace(found, state=FORGOTTEN)
