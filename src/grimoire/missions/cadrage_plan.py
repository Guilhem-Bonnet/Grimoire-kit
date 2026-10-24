"""Du cadrage aux tâches du ledger (W6-07b, ADP-9) — et ses questions, posées en un lot (ADP-8).

``grimoire cadrage plan`` lit les exigences identifiées (:mod:`grimoire.core.cadrage_requirements`) et
propose **une tâche par exigence** : critères d'acceptation repris tels quels, ``covers: [REQ-…]`` dans
``extra``, priorité tirée du MoSCoW, classe de vérifiabilité calculée par :func:`verifiability.classify`
(jamais déclarée). Rien n'est écrit à côté : les tâches passent par :meth:`TaskService.add`, les questions par
:meth:`TaskService.ask_batch` (W6-07, tranche 1).

- **Idempotent** : une exigence déjà couverte par une tâche du ledger (non annulée) n'en recrée pas.
- **Ambiguïtés en questions** : une exigence sans critère (bloquante : aucune tâche n'en sort), un critère
  sans verdict reconnu (V2, non bloquante : la tâche sort, classée V2), le même identifiant défini deux fois
  dans un fichier (bloquante), deux identifiants pour la même exigence (non bloquante). Chaque question a une
  clé stable (``cadrage:REQ-003:sans-critere``) : une reprise ne la repose pas, une réponse reste acquise.
- **Ancre** : les questions du cadrage vivent sur une tâche du ledger marquée ``extra.cadrage: questions``.
  Un lot qui porte une bloquante la gare en ``waiting(human)`` : une carte dans la vue Décisions du cockpit,
  une notification. La réponse humaine la rend ``ready`` : reporter les réponses dans le cadrage devient une
  tâche comme une autre, dont le verdict est ``grimoire cadrage plan --check``.
- **Une réponse humaine vaut critère** : « à quoi verra-t-on que REQ-003 est satisfaite ? » répondu par un
  humain devient le critère de sa tâche (l'origine est dite) ; la déduction d'un agent, jamais.

Une question bloquante n'est « ouverte » que tant que son ambiguïté existe dans le cadrage : corriger le
fichier la rend caduque sans réponse fabriquée, et l'ancre qui n'attendait plus qu'elle est annulée.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from grimoire.core.cadrage_requirements import CadrageRequirements, Requirement, read_requirements
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.questions import ANSWERED, OPEN, QuestionSpec, records_of, status_of
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskCheck, TaskState, TaskType
from grimoire.missions.verifiability import (
    Verifiability,
    classify,
    classify_criteria,
    is_bare_command,
    reason_for_criteria,
)
from grimoire.missions.waiting import answer_command

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.service import TaskService

__all__ = [
    "ANCHOR",
    "CHECK_COMMAND",
    "PLANNED",
    "Ambiguity",
    "CadragePlan",
    "Proposal",
    "apply_plan",
    "build_plan",
    "covers_of",
    "is_anchor",
]

#: ``extra.cadrage`` d'une tâche : ``plan`` (tirée d'une exigence) ou ``questions`` (l'ancre des questions).
PLANNED, ANCHOR = "plan", "questions"
#: Le verdict de l'ancre : le cadrage est planifié, sans ambiguïté ni question bloquante en suspens.
CHECK_COMMAND = "grimoire cadrage plan --check"
_PRIORITY = {"must": "high", "should": "medium", "could": "low"}
_LIVE_ANCHOR = (TaskState.PROPOSED, TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING, TaskState.WAITING)
_TERMINAL = (TaskState.CLOSED, TaskState.CANCELLED)


def covers_of(task: MissionTask) -> list[str]:
    """Les exigences qu'une tâche déclare couvrir (``extra.covers``)."""
    raw = (task.extra or {}).get("covers")
    return [str(x) for x in raw if str(x).strip()] if isinstance(raw, list) else []


def is_anchor(task: MissionTask) -> bool:
    return (task.extra or {}).get("cadrage") == ANCHOR


def _digest(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:10]


@dataclass(frozen=True, slots=True)
class Ambiguity:
    """Ce que le cadrage ne dit pas assez clairement pour en tirer une tâche vérifiable."""

    key: str
    kind: str  # sans-critere | critere-vague | doublon-id | doublon
    requirements: tuple[str, ...]
    text: str
    why: str
    blocking: bool
    options: tuple[str, ...] = ()

    def spec(self) -> QuestionSpec:
        return QuestionSpec(self.text, "choice", self.options, blocking=self.blocking, why=self.why, key=self.key)


def _fold_title(text: str) -> str:
    return " ".join(text.casefold().split())


def find_ambiguities(reqs: CadrageRequirements) -> list[Ambiguity]:
    """Les ambiguïtés du cadrage, dans l'ordre des exigences. Pur : aucune lecture du ledger."""
    found: list[Ambiguity] = []
    by_title: dict[str, list[Requirement]] = {}
    for req in reqs.requirements:
        if not req.planned:
            continue
        label = f"{req.id} « {req.title or 'sans titre'} »"
        if req.conflicts:
            found.append(Ambiguity(
                f"cadrage:{req.id}:doublon-id", "doublon-id", (req.id,),
                f"{req.id} est défini deux fois : quelle formulation fait foi ?",
                "deux formulations du même identifiant dans un fichier du cadrage : aucune tâche n'en sort "
                "avant l'arbitrage", True, tuple(req.conflicts),
            ))
        elif not req.criteria:
            found.append(Ambiguity(
                f"cadrage:{req.id}:sans-critere", "sans-critere", (req.id,),
                f"{label} : à quoi verra-t-on qu'elle est satisfaite ?",
                "aucun critère d'acceptation dans le cadrage : sans lui, pas de tâche vérifiable "
                "(votre réponse devient son critère)", True,
            ))
        elif _textual_class(req.criteria) is Verifiability.V2:
            found.append(Ambiguity(
                f"cadrage:{req.id}:critere-vague:{_digest(*req.criteria)}", "critere-vague", (req.id,),
                f"{label} : comment vérifier « {_vague_criterion(req.criteria)} » ?",
                "critère sans verdict reconnu (V2) : nommez un test, une commande ou une revue", False,
            ))
        if req.title:
            by_title.setdefault(_fold_title(req.title), []).append(req)
    for same in by_title.values():
        if len(same) > 1:
            ids = tuple(r.id for r in same)
            found.append(Ambiguity(
                f"cadrage:{'+'.join(ids)}:doublon", "doublon", ids,
                f"{' et '.join(ids)} disent la même chose (« {same[0].title} ») : les fusionner ?",
                "deux identifiants pour une même exigence : deux tâches pour un même travail", False,
                ("fusionner", "garder les deux"),
            ))
    return found


def _textual_class(criteria: list[str]) -> Verifiability:
    return classify_criteria(criteria)


def _vague_criterion(criteria: list[str]) -> str:
    reason = reason_for_criteria(criteria)
    _, _, quoted = reason.partition(": ")
    return quoted.strip("'\"") or criteria[0]


@dataclass(slots=True)
class Proposal:
    """Une tâche que le plan propose (ou a créée) pour une exigence."""

    requirement: str
    title: str
    acceptance: tuple[str, ...]
    checks: tuple[TaskCheck, ...]
    priority: str
    verifiability: str
    reason: str
    criterion_origin: str = "cadrage"
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "covers": [self.requirement], "title": self.title, "acceptance": list(self.acceptance),
            "checks": [c.cmd for c in self.checks], "priority": self.priority,
            "verifiability": self.verifiability, "reason": self.reason, "criterion_origin": self.criterion_origin,
        }
        if self.task_id:
            data["task_id"] = self.task_id
        return data


@dataclass(slots=True)
class QuestionState:
    """Où en est une ambiguïté au ledger : jamais posée, ouverte, répondue, posée sans réponse, différée."""

    ambiguity: Ambiguity
    status: str = "new"  # new | open | answered | unanswered | deferred
    task_id: str = ""
    question_id: str = ""
    answer: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        a = self.ambiguity
        data: dict[str, Any] = {
            "key": a.key, "kind": a.kind, "requirements": list(a.requirements), "text": a.text, "why": a.why,
            "blocking": a.blocking, "status": self.status,
        }
        for name in ("task_id", "question_id", "answer", "note"):
            if value := getattr(self, name):
                data[name] = value
        if self.status in ("open", "unanswered") and self.task_id and self.question_id:
            data["answer_with"] = answer_command(self.task_id, self.question_id)
        return data


@dataclass(slots=True)
class CadragePlan:
    """Le plan : exigences, tâches proposées, questions, couverture — et ce qui a été écrit."""

    requirements: CadrageRequirements
    proposals: list[Proposal] = field(default_factory=list)
    covered: dict[str, list[str]] = field(default_factory=dict)  # REQ -> tâches qui la couvrent
    questions: list[QuestionState] = field(default_factory=list)
    orphans: list[dict[str, Any]] = field(default_factory=list)
    unlinked: list[dict[str, str]] = field(default_factory=list)
    anchor: str = ""
    applied: bool = False
    notes: list[str] = field(default_factory=list)

    # ── lectures ──
    @property
    def missing(self) -> list[str]:
        """Exigences sans critère vérifiable : aucun critère (ni réponse humaine, ni tâche qui la couvre), ou
        un critère sans verdict reconnu (V2) auquel aucun humain n'a répondu. Lu du cadrage, pas du ledger :
        le même cadrage rend le même verdict avant et après ``--yes``."""
        out = []
        for q in self.questions:
            req_id = q.ambiguity.requirements[0]
            if q.status == "answered" or q.ambiguity.kind not in ("sans-critere", "critere-vague"):
                continue
            if q.ambiguity.kind == "sans-critere" and req_id in self.covered:
                continue
            out.append(req_id)
        return out

    @property
    def conflicts(self) -> list[str]:
        return [r.id for r in self.requirements.requirements if r.planned and r.conflicts]

    @property
    def uncovered(self) -> list[str]:
        return [r.id for r in self.requirements.requirements if r.planned and r.id not in self.covered]

    @property
    def blocking_unresolved(self) -> list[QuestionState]:
        return [q for q in self.questions if q.ambiguity.blocking and q.status != "answered"]

    def exit_code(self, *, check: bool = False) -> int:
        """1 si le cadrage n'est pas entièrement planifiable : rien d'identifié, critère manquant ou vague,
        conflit ; avec *check*, aussi une question bloquante sans réponse ou une exigence sans tâche."""
        if not self.requirements.identified or self.missing or self.conflicts:
            return 1
        if check and (self.blocking_unresolved or self.uncovered):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        reqs = []
        planned = {p.requirement: p for p in self.proposals}
        for req in self.requirements.requirements:
            row = req.to_dict()
            if not req.planned:
                row["status"] = "out_of_scope"
            elif req.conflicts:
                row["status"] = "conflict"
            elif req.id in self.covered:
                row["status"] = "covered"
                row["tasks"] = list(self.covered[req.id])
            elif req.id in planned:
                row["status"] = "planned"
            else:
                row["status"] = "missing_criterion"
            reqs.append(row)
        return {
            "convention": "REQ-NNN",
            "files_read": list(self.requirements.files_read),
            "identified": self.requirements.identified,
            "applied": self.applied,
            "requirements": reqs,
            "tasks": [p.to_dict() for p in self.proposals],
            "questions": [q.to_dict() for q in self.questions],
            "anchor": self.anchor,
            "coverage": {
                "uncovered": self.uncovered, "orphans": list(self.orphans), "unlinked": list(self.unlinked),
            },
            "missing": self.missing,
            "conflicts": self.conflicts,
            "notes": list(self.notes),
        }


# ── construire le plan (lecture seule) ───────────────────────────────────────


def _ledger_tasks(service: TaskService) -> list[MissionTask]:
    return service.list_tasks() if service.has_ledger else []


def _question_states(tasks: list[MissionTask], found: list[Ambiguity]) -> list[QuestionState]:
    """Pour chaque ambiguïté, son état au ledger — une réponse humaine sur n'importe quelle ancre est acquise."""
    anchors = [t for t in tasks if is_anchor(t)]
    states = []
    for amb in found:
        state = QuestionState(amb)
        for task in anchors:
            for record in records_of(task):
                if record.key != amb.key:
                    continue
                status = status_of(task, record)
                if status == ANSWERED:
                    state = QuestionState(amb, "answered", task.id, record.id, record.answer)
                    break
                if status == OPEN and state.status != "answered":
                    state = QuestionState(amb, "open", task.id, record.id)
                elif state.status == "new":
                    state = QuestionState(amb, "unanswered", task.id, record.id)
            if state.status == "answered":
                break
        states.append(state)
    return states


def _proposal(req: Requirement, criteria: list[str], origin: str) -> Proposal:
    checks = tuple(
        TaskCheck(cmd=c.strip().strip("`").strip())
        for c in criteria
        if c.strip().startswith("`") and c.strip().rstrip(".").endswith("`") and is_bare_command(c)
    )
    title = f"{req.id} — {req.title or 'exigence sans titre'}"
    probe = MissionTask(
        id="cadrage-plan", mission_id="", title=title, status=TaskState.PROPOSED, type=TaskType.IMPLEMENTATION,
        risk_profile=RiskProfile.STANDARD, acceptance=tuple(criteria), created_at="", checks=checks,
    )
    klass = classify(probe)
    reason = reason_for_criteria(criteria)
    if klass is Verifiability.V1 and not checks and _textual_class(criteria) is Verifiability.V0:
        reason = "critères mécaniques sans commande déclarée : écrivez la commande seule entre accents graves"
    return Proposal(
        req.id, title, tuple(criteria), checks, _PRIORITY.get(req.priority, ""), klass.value, reason, origin,
    )


def build_plan(root: Path, service: TaskService) -> CadragePlan:
    """Le plan du cadrage de *root*, sans rien écrire (le ledger absent n'est pas créé)."""
    reqs = read_requirements(root)
    plan = CadragePlan(reqs)
    tasks = _ledger_tasks(service)
    live = [t for t in tasks if t.status is not TaskState.CANCELLED]
    known = set(reqs.by_id())
    for task in live:
        for req_id in covers_of(task):
            plan.covered.setdefault(req_id, []).append(task.id)
        unknown = [r for r in covers_of(task) if r not in known]
        if unknown:
            plan.orphans.append({"task_id": task.id, "title": task.title, "covers": unknown})
        elif not covers_of(task) and not is_anchor(task) and task.status not in _TERMINAL and reqs.identified:
            plan.unlinked.append({"task_id": task.id, "title": task.title})
    found = find_ambiguities(reqs)
    plan.questions = _question_states(tasks, found)
    answers = {q.ambiguity.requirements[0]: q for q in plan.questions
               if q.ambiguity.kind == "sans-critere" and q.status == "answered"}
    for req in reqs.requirements:
        if not req.planned or req.conflicts or req.id in plan.covered:
            continue
        if req.criteria:
            plan.proposals.append(_proposal(req, req.criteria, "cadrage"))
        elif req.id in answers:
            state = answers[req.id]
            plan.proposals.append(_proposal(req, [state.answer], f"réponse humaine {state.question_id}"))
            plan.notes.append(
                f"{req.id} : critère pris de la réponse humaine à {state.question_id} — reportez-le dans le cadrage"
            )
    anchors = [t for t in tasks if is_anchor(t) and t.status in _LIVE_ANCHOR]
    plan.anchor = anchors[-1].id if anchors else ""
    return plan


# ── appliquer (écritures, toutes par TaskService) ────────────────────────────


def _anchor_task(service: TaskService, plan: CadragePlan, actor: str) -> MissionTask:
    if plan.anchor:
        return service.require(plan.anchor)
    added = service.add(
        "Cadrage : trancher les questions des exigences",
        ("Les réponses sont reportées dans _grimoire/cadrage/ et le cadrage est planifié : "
         f"`{CHECK_COMMAND}` rend 0",),
        actor=actor, owner=actor, checks=(TaskCheck(cmd=CHECK_COMMAND),), extra={"cadrage": ANCHOR},
    )
    plan.anchor = added.task.id
    return added.task


def _retire_moot_anchor(service: TaskService, plan: CadragePlan, actor: str) -> None:
    """Une ancre garée dont aucune bloquante ouverte n'est plus d'actualité : annulée, sans réponse fabriquée."""
    if not plan.anchor:
        return
    task = service.require(plan.anchor)
    if task.status is not TaskState.WAITING:
        return
    current = {q.ambiguity.key for q in plan.questions}
    waiting_open = [r for r in records_of(task) if status_of(task, r) == OPEN and r.question.blocking]
    if waiting_open and not any(r.key in current for r in waiting_open):
        with contextlib.suppress(GrimoireMissionError):
            service.cancel(task.id, "questions du cadrage caduques : le cadrage a été corrigé", actor, force=True)
            plan.notes.append(f"{task.id} annulée : ses questions bloquantes n'ont plus d'objet dans le cadrage")
            plan.anchor = ""


def _post_questions(service: TaskService, plan: CadragePlan, actor: str) -> None:
    pending = [q for q in plan.questions if q.status in ("new", "unanswered")]
    if not pending:
        return
    anchor = _anchor_task(service, plan, actor)
    can_park = anchor.status in (TaskState.PROPOSED, TaskState.READY) or (
        anchor.status in (TaskState.CLAIMED, TaskState.RUNNING) and anchor.claim is not None
        and anchor.claim.actor_id == actor
    )
    blocking = [q for q in pending if q.ambiguity.blocking]
    if blocking and can_park and anchor.status in (TaskState.PROPOSED, TaskState.READY):
        try:
            if anchor.status is TaskState.PROPOSED:
                service.transition(anchor.id, TaskState.READY, actor)
            service.claim(anchor.id, actor)
        except GrimoireMissionError as exc:
            can_park = False
            plan.notes.append(f"questions bloquantes non posées : {anchor.id} n'a pas pu être réservée ({exc})")
    asked = pending if can_park else [q for q in pending if not q.ambiguity.blocking]
    for q in pending:
        if q not in asked:
            q.status, q.task_id = "deferred", anchor.id
            q.note = "posée quand le lot en cours sera tranché"
    if not asked:
        return
    outcome = service.ask_batch(anchor.id, [q.ambiguity.spec() for q in asked], actor)
    by_key = {r.key: r for r in records_of(outcome.task)}
    for q in asked:
        record = by_key.get(q.ambiguity.key)
        if record is not None:
            q.status, q.task_id, q.question_id = "open", anchor.id, record.id


def apply_plan(service: TaskService, plan: CadragePlan, actor: str) -> CadragePlan:
    """Écrit le plan : les tâches proposées, puis le lot de questions sur l'ancre. Idempotent."""
    for proposal in plan.proposals:
        added = service.add(
            proposal.title, proposal.acceptance, actor=actor, checks=proposal.checks, priority=proposal.priority,
            extra={"cadrage": PLANNED, "covers": [proposal.requirement]},
        )
        proposal.task_id = added.task.id
        plan.covered.setdefault(proposal.requirement, []).append(added.task.id)
    _retire_moot_anchor(service, plan, actor)
    _post_questions(service, plan, actor)
    plan.applied = True
    return plan
