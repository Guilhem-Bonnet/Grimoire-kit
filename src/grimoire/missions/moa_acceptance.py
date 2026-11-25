"""La recette de la MOA, prononcée contre l'exigence validée (#1342, lot M4).

L'agent écrit les critères d'une tâche puis les vérifie : il est juge et partie. La recette porte sur le **besoin**,
l'exigence que la MOA a validée (lot M3, :mod:`.requirement_gate`), pas sur les critères de l'agent :

- **la présentation** (:func:`requirement_view`) : l'exigence telle que le cadrage la porte, son critère de recette
  selon la nature de la demande (:data:`~grimoire.missions.request_kind.KIND_PROFILES`, le tableau de l'épique), les
  tâches rattachées (``--requirement``) avec leur état et leurs preuves (dernier verdict et pack de preuve, PR
  fusionnée) ;
- **le verdict** (:func:`pronounce`) : ``conforme``, ``conforme-avec-reserve`` (réserve obligatoire) ou
  ``non-conforme`` (motif obligatoire). Mêmes contrôles d'origine que la validation, par les mêmes fonctions
  (:func:`~grimoire.missions.requirement_gate.refuse_agent_origin` avant toute lecture, puis
  :func:`~grimoire.missions.requirement_gate.attest_human_turn`). Refusé si l'exigence n'est pas validée pour son texte
  actuel, si une tâche qui la sert reste ouverte (elles sont nommées) ou si aucune n'est livrée : pas de recette
  partielle ;
- **le sceau** : le verdict s'inscrit au journal d'autorité avec le projet, le chantier, l'exigence, l'empreinte du
  texte, le verdict, le motif et les tâches jugées. Seul ce sceau compte : une ligne au ledger ou un statut écrit dans
  le cadrage ne vaut pas recette ;
- **les effets** : ``non-conforme`` ouvre une tâche MOE rattachée à l'exigence, le motif pour critère d'acceptation ;
  ``conforme-avec-reserve`` en ouvre une pour la réserve ; toutes deux à l'état ``proposed``. Une tâche close ne se
  rouvre pas dans le ledger : la reprise est une tâche nouvelle qui cite celles qu'elle reprend (``extra.recette``).

L'état d'une exigence (:func:`state_of`) se déduit du cadrage, du journal et des tâches : ``proposee``, ``validee``,
``livree`` (en attente de recette), ``recettee``, ``recettee-avec-reserve`` (la réserve est en cours) ou
``non-conforme`` (la reprise est en cours). Une tâche livrée après le dernier verdict remet l'exigence en attente de
recette : la réserve levée ou la reprise faite, la MOA recette à nouveau, et l'exigence n'est ``recettee`` qu'à
``conforme``.

**Limites** : celles de la validation (M3), mot pour mot — les signaux d'agent sont des variables d'environnement et
un terminal ; un processus du même compte qui les efface, ou qui écrit lui-même au journal d'autorité en recalculant
la chaîne, forge un sceau ; seule la garde de zone de l'hôte l'en empêche. La recette juge ce que la MOA voit : les
preuves affichées sont celles du ledger et du registre de preuves du projet, que le worker écrit ; elles informent la
MOA, elles ne la remplacent pas.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import requirement_gate as rg
from grimoire.missions.schemas import ORIGIN_HUMAN

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from grimoire.core.cadrage_requirements import Requirement
    from grimoire.missions.mandate import AuthorityJournal
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.service import TaskService

__all__ = [
    "ACCEPTED_ENTRY",
    "CONFORME",
    "NON_CONFORME",
    "RECETTE_GESTURE",
    "RESERVE",
    "STATE_LABELS",
    "VERDICTS",
    "RequirementView",
    "TaskProof",
    "acceptance_criterion",
    "parse_verdict",
    "pending_acceptance",
    "pronounce",
    "requirement_view",
    "requirement_views",
    "state_of",
    "verdict_in",
]

#: Le type de l'entrée du journal d'autorité qui scelle un verdict de recette.
ACCEPTED_ENTRY = "requirement_recette"
RECETTE_CHANNEL = "cadrage-recette"
CONFORME, RESERVE, NON_CONFORME = "conforme", "conforme-avec-reserve", "non-conforme"
VERDICTS = (CONFORME, RESERVE, NON_CONFORME)
RECETTE_GESTURE = rg.HumanGesture(
    "recette",
    "grimoire cadrage recette",
    "Ce verdict de recette sera enregistré au nom de la MOA. Est-ce bien vous qui le prononcez ? [o/N] ",
)

PROPOSED, VALIDATED, DELIVERED = "proposee", "validee", "livree"
ACCEPTED, ACCEPTED_RESERVE, REJECTED = "recettee", "recettee-avec-reserve", "non-conforme"
STATE_LABELS = {
    PROPOSED: "proposée (non validée)",
    VALIDATED: "validée",
    DELIVERED: "livrée, en attente de recette",
    ACCEPTED: "recettée",
    ACCEPTED_RESERVE: "recettée avec réserve",
    REJECTED: "non conforme",
}
_DONE, _DROPPED = "closed", "cancelled"
_NO_NATURE = (
    "l'exigence est satisfaite telle que la MOA l'a validée (nature non déclarée : `grimoire cadrage interview --kind`)"
)


def parse_verdict(raw: str) -> str:
    """``conforme``, ``conforme-avec-reserve`` ou ``non-conforme`` (accents, casse et espaces indifférents)."""
    folded = unicodedata.normalize("NFKD", str(raw))
    text = "-".join("".join(c for c in folded if not unicodedata.combining(c)).lower().replace("_", " ").split())
    text = {"conforme-avec-une-reserve": RESERVE, "reserve": RESERVE}.get(text, text)
    if text not in VERDICTS:
        raise rg.RequirementRefusedError("bad_verdict", f"verdict {raw!r} inconnu : attendu {', '.join(VERDICTS)}")
    return text


def acceptance_criterion(nature: str) -> str:
    """Ce que la MOA recette pour cette nature (le tableau de l'épique) ; sans nature, l'exigence telle que validée."""
    from grimoire.missions.request_kind import KIND_PROFILES, parse_kind

    try:
        return KIND_PROFILES[parse_kind(nature)].acceptance
    except ValueError:
        return _NO_NATURE


# ── La vue d'une exigence ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TaskProof:
    """Une tâche qui sert l'exigence : son état et ce qui la prouve (information pour la MOA, jamais un verdict)."""

    id: str
    title: str
    status: str
    verdict: str = ""
    pack: str = ""
    merged: str = ""

    @property
    def delivered(self) -> bool:
        return self.status == _DONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "delivered": self.delivered,
            "verdict": self.verdict,
            "evidence_pack": self.pack,
            "merged": self.merged,
        }


@dataclass(slots=True)
class RequirementView:
    """Ce que la MOA voit d'une exigence, et l'état qui s'en déduit."""

    key: str
    title: str = ""
    priority: str = ""
    criteria: tuple[str, ...] = ()
    nature: str = ""
    validated: bool = False
    reason: str = ""
    fingerprint: str = ""
    tasks: list[TaskProof] = field(default_factory=list)
    verdict: dict[str, Any] | None = None
    state: str = PROPOSED

    @property
    def open_tasks(self) -> list[TaskProof]:
        return [t for t in self.tasks if not t.delivered]

    @property
    def delivered_tasks(self) -> list[TaskProof]:
        return [t for t in self.tasks if t.delivered]

    def to_dict(self) -> dict[str, Any]:
        verdict = self.verdict or {}
        return {
            "requirement": self.key,
            "title": self.title,
            "priority": self.priority,
            "criteria": list(self.criteria),
            "nature": self.nature,
            "acceptance": acceptance_criterion(self.nature),
            "validated": self.validated,
            "reason": self.reason,
            "state": self.state,
            "state_label": STATE_LABELS[self.state],
            "tasks": [t.to_dict() for t in self.tasks],
            "last_verdict": (
                {k: verdict.get(k) for k in ("verdict", "motif", "tasks", "moa", "ts", "seq")} if verdict else None
            ),
        }


def verdict_in(
    entries: Iterable[dict[str, Any]], project: str, key: str, current: str, moa: str
) -> dict[str, Any] | None:
    """Le dernier verdict scellé qui vaut : ce projet, cette clé, le canal de la recette, l'origine humaine,
    l'empreinte *current* du texte et la MOA *moa* déclarée aujourd'hui."""
    wanted = {
        "type": ACCEPTED_ENTRY,
        "project": project,
        "requirement": key,
        "origin": ORIGIN_HUMAN,
        "channel": RECETTE_CHANNEL,
        "fingerprint": current,
        "moa": moa,
    }
    for entry in reversed(list(entries)):
        if all(entry.get(k) == v for k, v in wanted.items()) and entry.get("verdict") in VERDICTS:
            return dict(entry)
    return None


def state_of(validated: bool, tasks: list[TaskProof], verdict: dict[str, Any] | None) -> str:
    """L'état de l'exigence (voir le module) : seul le sceau donne ``recettee``."""
    if not validated:
        return PROPOSED
    last = str((verdict or {}).get("verdict") or "")
    if any(not t.delivered for t in tasks):  # du travail en cours : la reprise, la réserve, ou une suite
        return {NON_CONFORME: REJECTED, RESERVE: ACCEPTED_RESERVE}.get(last, VALIDATED)
    delivered = {t.id for t in tasks}
    judged = {str(t) for t in (verdict or {}).get("tasks") or ()}
    if delivered and (not last or delivered - judged):
        return DELIVERED
    return {CONFORME: ACCEPTED, RESERVE: ACCEPTED_RESERVE, NON_CONFORME: REJECTED}.get(last, VALIDATED)


def _interview_kind(root: Path, chantier: str | None) -> str:
    """La nature enregistrée par l'entretien de cadrage du chantier (``""`` si aucune ou illisible)."""
    from grimoire.core.cadrage import cadrage_dir
    from grimoire.core.cadrage_interview import STATE_FILE

    try:
        data = json.loads((cadrage_dir(root, chantier) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("kind") or "") if isinstance(data, dict) else ""


def _nature(root: Path, key: str, tasks: Iterable[MissionTask]) -> str:
    """La nature de la demande : celle des tâches qui servent l'exigence si elles s'accordent, sinon l'entretien."""
    natures = {rg.nature_of(t) for t in tasks} - {""}
    if len(natures) == 1:
        return natures.pop()
    head, _, _ = key.rpartition("/")
    return _interview_kind(root, head or None)


def _serving(home: Path, key: str, tasks: Iterable[MissionTask], linked: dict[str, str]) -> list[MissionTask]:
    """Les tâches qui citent *key* et sont du bon chantier ; les tâches annulées ne servent plus rien."""
    out = []
    for task in tasks:
        if task.status.value == _DROPPED:
            continue
        raw = rg.requirement_of(task)
        try:
            cited = rg.normalize_key(raw) if raw else ""
        except GrimoireMissionError:  # une référence illisible ne sert aucune exigence
            continue
        if cited == key and not rg.chantier_mismatch(home, task, key, linked):
            out.append(task)
    return out


def _proof(home: Path, service: TaskService | None, task: MissionTask) -> TaskProof:
    """État et preuves d'une tâche livrée : dernier verdict et pack de preuve, PR fusionnée (best-effort)."""
    from grimoire.missions.ledger import EXTERNAL_MERGED_EVENT
    from grimoire.missions.task_worktree import MERGED_EVENT

    proof = TaskProof(task.id, task.title, task.status.value)
    if service is None or not proof.delivered:
        return proof
    verdict = pack = merged = ""
    evidence = home / "_grimoire-runtime-output" / "evidence"
    try:
        if evidence.is_dir():
            from grimoire.evidence.service import EvidenceService

            found = EvidenceService(evidence).get_latest_verdict(task.id)
            if found is not None:
                verdict, pack = found.verdict.value, found.evidence_pack_id
        for event in reversed(service.ledger.events_for(task.id)):
            if event.event_type in (MERGED_EVENT, EXTERNAL_MERGED_EVENT):
                sha = str(event.payload.get("merged_sha") or "")
                merged = f"PR #{event.payload.get('pr', '?')} fusionnée à {sha[:12]}"
                break
    except Exception:  # noqa: S110 — une preuve illisible s'affiche absente, elle ne casse pas la recette
        pass
    return TaskProof(task.id, task.title, task.status.value, verdict, pack, merged)


def _entries(journal: AuthorityJournal | None) -> tuple[list[dict[str, Any]], str]:
    from grimoire.missions.mandate import AuthorityJournal, JournalError

    try:
        return (journal or AuthorityJournal()).read(), ""
    except JournalError as exc:
        return [], f"journal d'autorité illisible : {exc}"


@dataclass(slots=True)
class _Context:
    """Ce qui se lit une fois pour plusieurs exigences : projet, MOA, journal, tâches, chantiers liés."""

    project: Path
    moa: str
    declared: bool
    entries: list[dict[str, Any]]
    journal_problem: str
    tasks: list[MissionTask]
    linked: dict[str, str]
    service: TaskService | None = None
    requirements: dict[str | None, dict[str, Requirement]] = field(default_factory=dict)

    def requirement(self, key: str) -> Requirement | None:
        from grimoire.core.cadrage_requirements import read_requirements

        head, _, ident = key.rpartition("/")
        chantier = head or None
        if chantier not in self.requirements:
            try:
                self.requirements[chantier] = read_requirements(self.project, chantier).by_id()
            except (ValueError, OSError):
                self.requirements[chantier] = {}
        return self.requirements[chantier].get(ident)


def _context(
    root: Path, *, service: TaskService | None, tasks: list[MissionTask] | None, journal: AuthorityJournal | None
) -> _Context:
    project = rg.origin_root(root)
    mode = rg.gate_mode(project)
    entries, problem = _entries(journal) if mode.declared else ([], "")
    if tasks is None:
        tasks = service.list_tasks() if service is not None and service.has_ledger else []
    linked = rg.linked_missions(project) if tasks else {}
    return _Context(project, mode.moa, mode.declared, entries, problem, tasks, linked, service)


def _view(ctx: _Context, key: str, *, proofs: bool) -> RequirementView:
    requirement = ctx.requirement(key)
    view = RequirementView(key)
    serving = _serving(ctx.project, key, ctx.tasks, ctx.linked)
    view.tasks = [_proof(ctx.project, ctx.service if proofs else None, t) for t in serving]
    view.nature = _nature(ctx.project, key, serving)
    if requirement is None:
        view.reason = f"exigence {key} introuvable dans le cadrage du projet"
        return view
    view.title, view.priority, view.criteria = (
        requirement.title.strip(),
        requirement.priority,
        tuple(requirement.criteria),
    )
    if not ctx.declared:
        view.reason = "aucune MOA déclarée : aucune exigence ne peut être validée ni recettée"
        return view
    if ctx.journal_problem:
        view.reason = ctx.journal_problem
        return view
    view.fingerprint = rg.fingerprint(key, requirement)
    entry, view.reason = rg.validation_in(ctx.entries, str(ctx.project), key, view.fingerprint, ctx.moa)
    view.validated = entry is not None
    if view.validated:
        view.verdict = verdict_in(ctx.entries, str(ctx.project), key, view.fingerprint, ctx.moa)
    view.state = state_of(view.validated, view.tasks, view.verdict)
    return view


def requirement_view(
    root: Path,
    key: str,
    *,
    service: TaskService | None = None,
    journal: AuthorityJournal | None = None,
    proofs: bool = True,
) -> RequirementView:
    """La présentation d'une exigence à la MOA (lecture seule)."""
    key = rg.normalize_key(key)
    return _view(_context(root, service=service, tasks=None, journal=journal), key, proofs=proofs)


def requirement_views(
    root: Path,
    chantier: str | None = None,
    *,
    service: TaskService | None = None,
    journal: AuthorityJournal | None = None,
) -> list[RequirementView]:
    """L'état de chaque exigence identifiée du chantier (``cadrage status``), sans les preuves."""
    from grimoire.core.cadrage_requirements import read_requirements

    ctx = _context(root, service=service, tasks=None, journal=journal)
    found = read_requirements(ctx.project, chantier)
    keys = [rg.normalize_key(r.id, chantier) for r in found.requirements if r.planned]
    return [_view(ctx, key, proofs=False) for key in keys]


def pending_acceptance(
    home_root: Path, tasks: list[MissionTask], *, journal: AuthorityJournal | None = None
) -> list[str]:
    """Pour le cockpit : les exigences livrées en attente de recette, parmi celles que les tâches citent.

    Vide sans MOA déclarée, avant toute lecture du journal ; l'appelant lit le mode du gate avant d'appeler.
    """
    ctx = _context(home_root, service=None, tasks=tasks, journal=journal)
    if not ctx.declared:
        return []
    keys: set[str] = set()
    for task in tasks:
        raw = rg.requirement_of(task)
        if task.status.value == _DONE and raw:  # seule une tâche livrée met une exigence en attente de recette
            try:
                keys.add(rg.normalize_key(raw))
            except GrimoireMissionError:  # une référence illisible ne cite aucune exigence
                continue
    return [key for key in sorted(keys) if _view(ctx, key, proofs=False).state == DELIVERED]


# ── Le verdict (le geste de la MOA) ─────────────────────────────────────────


def _refuse_unready(view: RequirementView) -> None:
    if not view.validated:
        raise rg.RequirementRefusedError(
            "not_validated",
            f"recette refusée : {view.reason or f'exigence {view.key} non validée'}. La recette porte sur l'exigence "
            "validée par la MOA (`grimoire cadrage approve`).",
        )
    pending = view.open_tasks
    if pending:
        listed = ", ".join(f"{t.id} ({t.status})" for t in pending)
        raise rg.RequirementRefusedError(
            "not_delivered",
            f"recette refusée : des tâches qui servent {view.key} restent ouvertes : {listed}. Pas de recette partielle : "
            "terminez-les ou annulez celles qui ne servent plus l'exigence.",
        )
    if not view.delivered_tasks:
        raise rg.RequirementRefusedError(
            "not_delivered",
            f"recette refusée : aucune tâche livrée ne sert {view.key} (rattachez-les avec `task add --requirement`).",
        )


def _follow_up(
    service: TaskService,
    view: RequirementView,
    verdict: str,
    motif: str,
    entry: dict[str, Any],
    actor: str,
    missions: dict[str, str],
) -> str:
    """La tâche MOE qui suit un verdict non conforme ou avec réserve, à l'état ``proposed`` ; rend son id."""
    from grimoire.missions.request_kind import parse_kind

    label = "Réserve de recette" if verdict == RESERVE else "Recette non conforme"
    short = motif if len(motif) <= 90 else motif[:89] + "…"
    reopens = [t.id for t in view.delivered_tasks]
    try:
        nature = parse_kind(view.nature).value if view.nature else ""
    except ValueError:
        nature = ""
    added = service.add(
        f"{label} {view.key} : {short}",
        (motif,),
        mission_id=missions.get(reopens[0], "") if reopens else "",
        actor=actor,
        origin="recette",
        description=f"Verdict « {verdict} » de la MOA sur {view.key} « {view.title} » : {motif}",
        requirement=view.key,
        nature=nature,
        extra={"recette": {"verdict": verdict, "seq": entry.get("seq"), "reopens": reopens}},
    )
    return added.task.id


def pronounce(
    root: Path,
    key: str,
    verdict: str,
    motif: str = "",
    *,
    actor: str,
    service: TaskService | None = None,
    journal: AuthorityJournal | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scelle le verdict de la MOA sur l'exigence *key*, puis ouvre la tâche de suite ; rend ce qui a été fait.

    L'origine n'est pas un paramètre : la fonction l'établit comme :func:`~.requirement_gate.approve_requirement`.
    """
    from grimoire.missions.mandate import AuthorityJournal
    from grimoire.missions.service import TaskService

    rg.refuse_agent_origin(RECETTE_GESTURE)
    verdict = parse_verdict(verdict)
    motif = " ".join(str(motif).split())
    if verdict != CONFORME and not motif:
        what = "la réserve" if verdict == RESERVE else "le motif"
        raise rg.RequirementRefusedError(
            "motif_required", f"recette refusée : {what} est obligatoire avec « {verdict} »."
        )
    key = rg.normalize_key(key)
    project = rg.origin_root(root)
    mode = rg.gate_mode(project)
    if not mode.declared:
        raise rg.RequirementRefusedError(
            "no_moa", "recette refusée : aucune MOA déclarée" + (f" ({mode.problem})" if mode.problem else "") + "."
        )
    service = service or TaskService(project)
    journal = journal or AuthorityJournal()
    view = requirement_view(project, key, service=service, journal=journal, proofs=False)
    _refuse_unready(view)
    rg.attest_human_turn(RECETTE_GESTURE)
    head, _, _ = key.rpartition("/")
    entry = journal.append(
        {
            "type": ACCEPTED_ENTRY,
            "project": str(project),
            "chantier": head or "principal",
            "requirement": key,
            "fingerprint": view.fingerprint,
            "title": view.title,
            "verdict": verdict,
            "motif": motif,
            "tasks": [t.id for t in view.delivered_tasks],
            "nature": view.nature,
            "moa": mode.moa,
            "actor": actor,
            "origin": ORIGIN_HUMAN,
            "channel": RECETTE_CHANNEL,
            "ts": (now or datetime.now(UTC)).isoformat(),
        }
    )
    out: dict[str, Any] = {"entry": entry, "task_id": "", "error": ""}
    if verdict != CONFORME:
        missions = {t.id: t.mission_id for t in service.list_tasks()}
        try:
            out["task_id"] = _follow_up(service, view, verdict, motif, entry, actor, missions)
        except Exception as exc:  # le verdict est scellé : on dit ce qui manque, on ne le défait pas
            out["error"] = f"verdict scellé, mais la tâche de suite n'a pas pu être créée : {exc}"
    out["state"] = requirement_view(project, key, service=service, journal=journal, proofs=False).state
    return out
