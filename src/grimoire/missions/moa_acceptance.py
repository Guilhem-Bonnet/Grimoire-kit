"""Recette MOA : l'acceptation se prononce contre l'exigence, pas contre les critères de l'agent (#1342, lot M4).

L'agent écrit les critères d'une tâche puis les vérifie : il est juge et partie. La recette sort l'agent de ce
rôle. ``grimoire cadrage recette <exigence>`` présente à la MOA (l'humain nommé) :

- l'exigence telle que le cadrage la porte, et son **critère de recette** selon la nature de la demande
  (:data:`RECETTE_CRITERIA`, le tableau de l'épique) ;
- les tâches livrées qui la servent (``extra.covers``) et leurs preuves (état, dernier verdict).

La MOA prononce ``conforme``, ``conforme avec réserve`` ou ``non conforme``, avec un motif. Le prononcé ne vaut que
depuis un **tour humain attesté** : même mécanisme que l'octroi d'un mandat (challenge lié à la session et au
prononcé, puis tour ``user`` postérieur, consommé une seule fois — :func:`grimoire.flows.attestation.attest`).
Sans tour humain, le prononcé est refusé et le challenge reste ouvert.

Ce que le prononcé produit, dans cet ordre :

1. une ligne **scellée au journal d'autorité** (``~/.grimoire/control/``, chaîné par hash, écrit par du code du kit,
   refusé aux agents par le frein de zone). C'est la seule source de vérité de la recette : une ligne forgée dans le
   ledger de tâches (commentaire, évènement, extra de tâche) ne vaut pas recette, :func:`sealed_verdicts` ne lit
   que le journal et n'y retient que les lignes dont l'attestation est ``attested`` ;
2. ``non conforme`` : une tâche MOE rattachée à l'exigence (``extra.covers``) dont **le motif est le critère** ;
3. ``conforme avec réserve`` : une tâche **proposée** pour la réserve, rattachée de même.

Une exigence n'est **close** qu'à ``conforme`` (:func:`is_closed`) : le dernier prononcé scellé fait foi, un
``non conforme`` postérieur la rouvre. Une exigence est **en attente de recette** (:func:`pending_requirements`) quand
des tâches closes la servent, qu'aucune tâche vivante ne la sert encore et que le dernier prononcé n'est pas
``conforme`` ; c'est ce que le cockpit liste.

Limite écrite : « exigence validée » (lot M3) n'existe pas encore comme état. Ici une exigence est recettable
dès que le cadrage la porte et qu'une tâche livrée la couvre ; M3 posera sa validation en amont.
"""

from __future__ import annotations

import contextlib
import hashlib
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.core.cadrage import DEFAULT_CHANTIER
from grimoire.core.cadrage_requirements import Requirement, read_requirements
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.cadrage_plan import covers_of
from grimoire.missions.schemas import MissionTask, TaskState

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from grimoire.flows.attestation import Attestation
    from grimoire.missions.service import TaskService

__all__ = [
    "CONFORME",
    "DEFAULT_NATURE",
    "ENTRY_TYPE",
    "NON_CONFORME",
    "RECETTE_CRITERIA",
    "RESERVE",
    "RecetteBrief",
    "RecetteOutcome",
    "build_brief",
    "cover_key",
    "is_closed",
    "parse_verdict",
    "pending_requirements",
    "pronounce",
    "sealed_verdicts",
]

CONFORME, RESERVE, NON_CONFORME = "conforme", "conforme_reserve", "non_conforme"
VERDICT_LABELS = {CONFORME: "conforme", RESERVE: "conforme avec réserve", NON_CONFORME: "non conforme"}
#: Le type de la ligne que la recette scelle au journal d'autorité.
ENTRY_TYPE = "moa_recette"
#: Le registre des challenges de recette (``<flows>/authority/moa-recette.authority.jsonl``).
RUN_ID = "moa-recette"

#: Le tableau de l'épique #1342 : ce que la MOA recette, selon la nature de la demande.
RECETTE_CRITERIA: dict[str, str] = {
    "nouveau-projet": "le cadrage couvre le besoin",
    "nouvelle-fonctionnalite": "l'usage attendu fonctionne",
    "evolution": "le changement est fait, sans régression",
    "anomalie": "le cas ne se reproduit plus",
    "incident": "le service est rétabli, causes et actions validées",
    "investigation": "la question a une réponse prouvée",
    "analyse": "le rapport permet de décider",
    "recherche": "la recommandation est tranchée et sourcée",
    "maintenance": "le comportement est inchangé, la dette réduite",
    "migration": "la bascule est faite, le retour arrière prouvé",
    "securite": "l'exposition est fermée ou acceptée par la MOA",
}
#: Sans nature déclarée, la plus courante : l'usage attendu fonctionne.
DEFAULT_NATURE = "nouvelle-fonctionnalite"

_VERDICT_WORDS = {
    "conforme": CONFORME,
    "conforme avec reserve": RESERVE,
    "conforme reserve": RESERVE,
    "non conforme": NON_CONFORME,
    "nonconforme": NON_CONFORME,
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.replace("_", " ").replace("-", " ").split()).casefold()


def parse_verdict(raw: str) -> str:
    """``conforme`` / ``conforme avec réserve`` / ``non conforme`` (casse, accents, tirets neutralisés) ; sinon refus."""
    found = _VERDICT_WORDS.get(_fold(raw))
    if found is None:
        raise GrimoireMissionError(
            f"verdict inconnu : {raw!r} (attendu : « conforme », « conforme avec réserve » ou « non conforme »)"
        )
    return found


def cover_key(requirement: str, chantier: str | None = None) -> str:
    """La clé d'``extra.covers`` : nue pour le chantier principal, préfixée par le slug sinon."""
    return requirement if chantier in (None, "", DEFAULT_CHANTIER) else f"{chantier}/{requirement}"


def _norm_id(requirement: str) -> str:
    return requirement.strip().upper()


# ── Journal d'autorité : la seule source de la recette ──────────────────────


def _journal() -> Any:
    from grimoire.missions.mandate import AuthorityJournal

    return AuthorityJournal()


def sealed_verdicts(project_root: Path, journal: Any | None = None) -> dict[str, dict[str, Any]]:
    """Le dernier prononcé scellé de chaque exigence du projet (clé d'``extra.covers`` -> entrée du journal).

    Seules comptent les lignes ``moa_recette`` du projet dont l'attestation est ``attested`` ; le ledger de tâches
    n'est jamais lu. Un journal illisible ou rompu ne porte aucune recette (:class:`JournalError` est avalée : échec
    fermé, aucune exigence n'est close).
    """
    from grimoire.missions.mandate import JournalError

    try:
        entries = (journal or _journal()).read()
    except JournalError:
        return {}
    project = str(project_root.resolve())
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if (
            entry.get("type") == ENTRY_TYPE
            and entry.get("project") == project
            and entry.get("attestation") == "attested"
            and entry.get("verdict") in VERDICT_LABELS
        ):
            out[str(entry.get("requirement", ""))] = entry
    return out


def is_closed(project_root: Path, requirement: str, chantier: str | None = None, journal: Any | None = None) -> bool:
    """Close seulement à ``conforme`` scellé — ni « avec réserve », ni « non conforme », ni une ligne du ledger."""
    sealed = sealed_verdicts(project_root, journal).get(cover_key(_norm_id(requirement), chantier))
    return sealed is not None and sealed.get("verdict") == CONFORME


# ── Présentation ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeliveredTask:
    id: str
    title: str
    state: str
    proof: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "state": self.state, "proof": self.proof}


@dataclass(frozen=True, slots=True)
class RecetteBrief:
    """Ce que la MOA lit avant de prononcer."""

    requirement: str
    title: str
    criteria: tuple[str, ...]
    nature: str
    recette_criterion: str
    delivered: tuple[DeliveredTask, ...]
    live: tuple[str, ...] = ()
    last_verdict: str = ""
    problems: tuple[str, ...] = field(default=())

    @property
    def recettable(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "title": self.title,
            "criteria": list(self.criteria),
            "nature": self.nature,
            "recette_criterion": self.recette_criterion,
            "delivered": [d.to_dict() for d in self.delivered],
            "live": list(self.live),
            "last_verdict": self.last_verdict,
            "problems": list(self.problems),
        }


def _proof_of(task: MissionTask, project_root: Path, service: TaskService) -> str:
    """Le dernier verdict de la tâche (mécanique ou humain), ou ce qui en tient lieu ; jamais une exception."""
    try:
        from grimoire.evidence.service import EvidenceService
        from grimoire.missions.human_acceptance import acceptance_roots

        evidence_root, _proofs = acceptance_roots(service, task.id)
        verdicts = EvidenceService(evidence_root).list_verdicts(task.id)
    except Exception:  # une preuve illisible se dit, elle ne fait pas échouer la présentation
        return "preuve illisible"
    if not verdicts:
        return "aucun verdict rangé"
    last = verdicts[-1]
    return f"{last.verdict.value} ({last.created_by})"


def _requirement_of(project_root: Path, requirement: str, chantier: str | None) -> Requirement | None:
    return read_requirements(project_root, chantier).by_id().get(requirement)


def _covering(service: TaskService, key: str) -> list[MissionTask]:
    return [t for t in service.list_tasks() if key in covers_of(t)]


def build_brief(
    project_root: Path,
    service: TaskService,
    requirement: str,
    *,
    nature: str = DEFAULT_NATURE,
    chantier: str | None = None,
    journal: Any | None = None,
) -> RecetteBrief:
    """La présentation de *requirement* à la MOA : exigence, critère de recette, tâches livrées et preuves."""
    req_id = _norm_id(requirement)
    if nature not in RECETTE_CRITERIA:
        raise GrimoireMissionError(f"nature inconnue : {nature!r} (attendu : {', '.join(RECETTE_CRITERIA)})")
    found = _requirement_of(project_root, req_id, chantier)
    key = cover_key(req_id, chantier)
    tasks = _covering(service, key)
    delivered = tuple(
        DeliveredTask(t.id, t.title, t.status.value, _proof_of(t, project_root, service))
        for t in tasks
        if t.status is TaskState.CLOSED
    )
    live = tuple(t.id for t in tasks if t.status not in (TaskState.CLOSED, TaskState.CANCELLED))
    problems: list[str] = []
    if found is None:
        problems.append(f"{req_id} n'est pas une exigence du cadrage (04-exigences.md, 05-cahier-des-charges.md)")
    if not delivered:
        problems.append(f"aucune tâche close ne sert {req_id} : rien n'est livré à recetter")
    if live:
        problems.append(f"des tâches servent encore {req_id} ({', '.join(live)}) : la recette attend leur livraison")
    sealed = sealed_verdicts(project_root, journal).get(key)
    return RecetteBrief(
        requirement=req_id,
        title=found.title if found else "",
        criteria=tuple(found.criteria) if found else (),
        nature=nature,
        recette_criterion=RECETTE_CRITERIA[nature],
        delivered=delivered,
        live=live,
        last_verdict=str(sealed["verdict"]) if sealed else "",
        problems=tuple(problems),
    )


def pending_requirements(
    project_root: Path, service: TaskService, chantier: str | None = None, journal: Any | None = None
) -> list[dict[str, Any]]:
    """Les exigences en attente de recette : livrées, plus rien de vivant, pas encore ``conforme`` (le cockpit les liste)."""
    sealed = sealed_verdicts(project_root, journal)
    tasks = service.list_tasks()
    out: list[dict[str, Any]] = []
    for req in read_requirements(project_root, chantier).requirements:
        if not req.planned:
            continue
        key = cover_key(req.id, chantier)
        mine = [t for t in tasks if key in covers_of(t)]
        done = [t.id for t in mine if t.status is TaskState.CLOSED]
        alive = [t for t in mine if t.status not in (TaskState.CLOSED, TaskState.CANCELLED)]
        last = sealed.get(key)
        if not done or alive or (last is not None and last.get("verdict") == CONFORME):
            continue
        out.append(
            {
                "requirement": req.id,
                "title": req.title,
                "delivered": done,
                "last_verdict": str(last["verdict"]) if last else "",
            }
        )
    return out


# ── Prononcé ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RecetteOutcome:
    """Le résultat d'une demande de prononcé : refusé (non attesté, challenge ouvert) ou scellé."""

    sealed: bool
    attestation: Attestation
    verdict: str
    requirement: str
    seal: dict[str, Any] | None = None
    task_id: str = ""
    task_state: str = ""

    @property
    def closed(self) -> bool:
        return self.sealed and self.verdict == CONFORME

    def to_dict(self) -> dict[str, Any]:
        return {
            "sealed": self.sealed,
            "verdict": self.verdict,
            "requirement": self.requirement,
            "closed": self.closed,
            "task_id": self.task_id,
            "task_state": self.task_state,
            "attestation": self.attestation.to_dict(),
        }


def _pronouncement_sha(requirement: str, verdict: str, reason: str) -> str:
    return hashlib.sha256(f"{requirement}\n{verdict}\n{reason}".encode()).hexdigest()


def _open_task(
    service: TaskService,
    verdict: str,
    requirement: str,
    key: str,
    motif: str,
    seal_seq: int,
    actor: str,
) -> tuple[str, str]:
    """La tâche que le prononcé ouvre : MOE rouverte (``non conforme``) ou réserve proposée (``avec réserve``)."""
    extra = {"covers": [key], "recette": {"requirement": requirement, "verdict": verdict, "seal_seq": seal_seq}}
    if verdict == NON_CONFORME:
        title = f"Recette non conforme — {requirement} : reprise par la MOE"
        description = (
            f"La MOA a prononcé « non conforme » sur {requirement} : le motif est le critère de cette tâche. "
            "À livrer à la MOE, puis nouvelle recette."
        )
    else:
        title = f"Réserve de recette — {requirement}"
        description = f"La MOA a prononcé « conforme avec réserve » sur {requirement} : la réserve est le critère."
    added = service.add(
        title,
        (motif,),
        actor=actor,
        description=description,
        extra=extra,
        priority="high" if verdict == NON_CONFORME else "",
    )
    task = added.task
    if (
        verdict == NON_CONFORME
    ):  # « rouvre » : une tâche à reprendre ; refusée par le gate (profil gouverné) elle reste proposée
        with contextlib.suppress(GrimoireMissionError):
            service.transition(task.id, TaskState.READY, actor)
        task = service.require(task.id)
    return task.id, task.status.value


def pronounce(
    project_root: Path,
    service: TaskService,
    requirement: str,
    verdict: str,
    reason: str,
    *,
    nature: str = DEFAULT_NATURE,
    chantier: str | None = None,
    actor: str = "moa",
    env: Mapping[str, str] | None = None,
    journal: Any | None = None,
) -> RecetteOutcome:
    """Prononce la recette : refusée sans tour humain attesté, sinon scellée au journal d'autorité, puis ses suites.

    Premier appel d'une session : émet le challenge de ce prononcé (exigence, verdict, motif) et rend un refus
    ``no_human_turn``. L'humain parle dans un tour, la commande est relancée : le tour postérieur au challenge
    atteste, il est consommé, la ligne est scellée. Changer le verdict ou le motif émet un nouveau challenge.
    """
    from grimoire.flows.attestation import FLOWS_RELPATH, CheckpointJournal, attest

    req_id = _norm_id(requirement)
    verdict = parse_verdict(verdict)
    motif = reason.strip()
    if not motif:
        raise GrimoireMissionError("un motif est requis : une recette sans motif ne dit pas ce qui a été jugé")
    brief = build_brief(project_root, service, req_id, nature=nature, chantier=chantier, journal=journal)
    if not brief.recettable:
        raise GrimoireMissionError("recette impossible : " + " ; ".join(brief.problems))

    root = project_root.resolve()
    key = cover_key(req_id, chantier)
    sha = _pronouncement_sha(key, verdict, motif)
    registry = CheckpointJournal(root / FLOWS_RELPATH, RUN_ID)
    node = f"recette:{key}"
    challenge = registry.issue_challenge(node, env, bound={"pronouncement_sha256": sha})
    attestation = attest(root, challenge, env, registry.consumed_turns())
    if not attestation.attested:
        return RecetteOutcome(False, attestation, verdict, req_id)

    seal_id = f"REC-{uuid.uuid4().hex[:12]}"
    # record_decision consomme le tour : il est brûlé avant le sceau, un échec ensuite ne le rend pas.
    registry.record_decision(node, verdict, motif, attestation, bound={"pronouncement_sha256": sha})
    seal = (journal or _journal()).append(
        {
            "type": ENTRY_TYPE,
            "seal_id": seal_id,
            "project": str(root),
            "requirement": key,
            "verdict": verdict,
            "reason": motif,
            "reason_sha256": sha,
            "nature": nature,
            "tasks": [d.id for d in brief.delivered],
            "attestation": attestation.status,
            "session_id": attestation.session_id,
            "turn_ts": attestation.turn_ts,
            "challenge_id": attestation.challenge_id,
            "ts": datetime.now(UTC).isoformat(),
        }
    )
    task_id = task_state = ""
    if verdict in (NON_CONFORME, RESERVE):
        task_id, task_state = _open_task(service, verdict, req_id, key, motif, int(seal["seq"]), actor)
    return RecetteOutcome(True, attestation, verdict, req_id, seal, task_id, task_state)
