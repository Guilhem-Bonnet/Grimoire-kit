"""L'exigence validée par la MOA, et le gate qui en dépend (#1342, lot M3).

L'agent propose des exigences, seul l'humain déclaré MOA les valide, et une tâche ne passe ``ready`` que si elle
sert une exigence validée. Quatre pièces :

- **la validation** (:func:`approve_requirement`), scellée au journal d'autorité (``~/.grimoire/control``, chaîné par
  hash, écrit par du code du kit) et pas au ledger du projet, que le worker écrit : une ligne forgée au ledger ou un
  statut écrit dans un fichier du cadrage ne compte pour rien. Comme le READY scellé (:mod:`.review_seal`) et la
  dérogation de l'arbitre (:mod:`.arbiter`), elle se lit là où le worker n'écrit pas. La fonction établit elle-même
  l'origine (aucun paramètre ne la porte), avec les signaux de ``task comment`` / ``task answer`` : un marqueur de
  session ou de terminal d'agent, ``GRIMOIRE_UNATTENDED`` ou l'orchestrateur en processus refusent d'emblée ; sinon un
  terminal interactif et un « oui » à l'écran sont exigés. Aucun mandat ne couvre ce geste. Ces contrôles
  (:func:`refuse_agent_origin`, :func:`attest_human_turn`) servent aussi la recette (:mod:`.moa_acceptance`, M4) ;
- **le sceau** porte le projet (sa racine d'origine), la clé de l'exigence (``REQ-001`` ou ``<chantier>/REQ-001``),
  la MOA déclarée et l'**empreinte** du texte validé (formulation, priorité, critères, périmètre) : modifier
  l'exigence après coup, changer de MOA ou la citer depuis un autre projet retire la validation ;
- **le rattachement** : une tâche porte l'exigence qu'elle sert (``extra.requirement``, ``task add --requirement``,
  posé par ``cadrage plan``) ; une exigence d'un chantier lié à une autre mission que celle de la tâche ne compte pas ;
- **le gate** ``proposed → ready`` (et, en mode bloquant, ``ready → claimed``, contre une transition forgée au
  ledger ou une exigence modifiée depuis) : refus en ``block``, événement et carte du cockpit en ``warn``. Les
  natures ``question`` et ``incident`` sont exemptées si l'intake déterministe lit le titre de la même façon (une
  nature ne s'exempte pas sur simple déclaration) — l'incident reçoit son exigence au post-mortem ; le passage
  exempté laisse lui aussi un événement. L'ancre des questions du cadrage (marqueur posé par ``cadrage plan``) est
  exemptée : c'est elle qui produit les exigences.

Le projet, la MOA et le mode se lisent à la **racine d'origine** du projet, jamais dans le worktree d'une tâche.

**Limites**, dites telles qu'elles sont : les signaux d'agent sont des variables d'environnement et un terminal ; un
processus du même compte qui les efface, ou qui écrit lui-même au journal d'autorité en recalculant la chaîne, forge un
sceau. Seule la garde de zone de l'hôte (qui refuse aux outils d'agent l'écriture dans la zone de contrôle) l'en
empêche, pas ce module. ``project-context.yaml`` est un fichier du dépôt : repasser le gate en ``warn`` se voit dans
le diff et dans ``grimoire doctor`` (validations scellées alors que le gate n'est plus bloquant). Le gate passe par
:class:`~grimoire.missions.service.TaskService` : les adaptateurs qui transitent sans lui (task-flow, unification,
pont A2A) ne sont rattrapés qu'au claim et à l'admission, en mode bloquant. Une exigence d'un chantier qui n'est lié à
aucune mission se rattache à n'importe quelle tâche.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.schemas import ORIGIN_HUMAN

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from grimoire.core.cadrage_requirements import Requirement
    from grimoire.missions.gates import GateRefusal
    from grimoire.missions.mandate import AuthorityJournal
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "APPROVE_GESTURE",
    "EXEMPTED_EVENT",
    "EXEMPT_NATURES",
    "FLAGGED_EVENT",
    "REMEDY",
    "VALIDATED_ENTRY",
    "GateMode",
    "HumanGesture",
    "RequirementCheck",
    "RequirementRefusedError",
    "approve_requirement",
    "attest_human_turn",
    "chantier_mismatch",
    "check_task",
    "fingerprint",
    "gate_mode",
    "gate_refusal",
    "linked_missions",
    "lookup",
    "nature_of",
    "normalize_key",
    "origin_root",
    "pending_review",
    "queue_refusal",
    "record_ready",
    "record_transition",
    "refuse_agent_origin",
    "requirement_of",
    "sealed_validations",
    "task_fields",
    "transition_refusal",
    "validation_in",
    "validation_of",
]

#: Le type de l'entrée du journal d'autorité qui scelle la validation d'une exigence.
VALIDATED_ENTRY = "requirement_validated"
#: Le canal qui scelle (inscrit dans l'entrée, pour l'audit).
APPROVE_CHANNEL = "cadrage-approve"
#: Événements du ledger (information, jamais preuve) : un passage ``ready`` signalé, un passage exempté.
FLAGGED_EVENT = "task.requirement_unvalidated"
EXEMPTED_EVENT = "task.requirement_exempted"
#: Les natures (:class:`~grimoire.missions.request_kind.RequestKind`) qui ne demandent pas d'exigence : une question
#: n'engage rien ; un incident se rétablit d'abord, son exigence arrive au post-mortem.
EXEMPT_NATURES = frozenset({"question", "incident"})
REMEDY = (
    "`grimoire cadrage interview` pour cadrer le besoin et écrire l'exigence, puis la faire valider par la MOA "
    "depuis son terminal (`grimoire cadrage approve <exigence>`), et rattacher la tâche (`task add --requirement <id>`)"
)

_KEY = re.compile(r"^(?:(?P<chantier>[a-z0-9]+(?:-[a-z0-9]+)*)/)?(?P<id>REQ-\d{1,4})$")


class RequirementRefusedError(GrimoireMissionError):
    """La validation d'une exigence est refusée ; *code* dit pourquoi."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


# ── Clés, rattachement, nature ──────────────────────────────────────────────


def normalize_key(raw: str, chantier: str | None = None) -> str:
    """``REQ-001`` ou ``<chantier>/REQ-001`` (la clé de ``extra.covers``) ; tout autre texte est refusé.

    *chantier* préfixe une clé nue ; ``principal`` (le chantier par défaut) garde la clé nue.
    """
    from grimoire.core.cadrage import DEFAULT_CHANTIER

    text = str(raw).strip()
    head, sep, tail = text.rpartition("/")
    if tail[:4].upper() == "REQ-":
        tail = "REQ-" + tail[4:]
    if not sep and chantier and chantier != DEFAULT_CHANTIER:
        head, sep = chantier, "/"
    if sep and head == DEFAULT_CHANTIER:
        head, sep = "", ""
    text = f"{head}{sep}{tail}"
    if not _KEY.match(text):
        raise GrimoireMissionError(
            f"exigence {raw!r} invalide : attendu REQ-<chiffres>, ou <chantier>/REQ-<chiffres> hors du cadrage principal"
        )
    return text


def requirement_of(task: MissionTask) -> str:
    """L'exigence qu'une tâche déclare servir : ``extra.requirement``, sinon l'unique ``covers`` de ``cadrage plan``."""
    extra = task.extra or {}
    raw = extra.get("requirement")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    covers = extra.get("covers")
    if isinstance(covers, list) and len(covers) == 1 and isinstance(covers[0], str):
        return covers[0].strip()
    return ""


def nature_of(task: MissionTask) -> str:
    """La nature déclarée de la demande (``extra.nature``, une valeur de ``RequestKind``) ; vide si absente."""
    raw = (task.extra or {}).get("nature")
    return raw.strip().lower() if isinstance(raw, str) else ""


# ── Mode du gate ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GateMode:
    """Le mode effectif (``block`` ou ``warn``), la MOA déclarée, et pourquoi la section ne se lit pas (s'il y a lieu)."""

    mode: str
    moa: str = ""
    declared: bool = False
    problem: str = ""

    @property
    def blocking(self) -> bool:
        return self.mode == "block"


def gate_mode(root: Path) -> GateMode:
    """Le mode du gate pour le projet d'origine *root*.

    Sans ``project-context.yaml``, sans section ``governance.roles`` ou sans bascule ``requirement_gate: block`` :
    ``warn`` (aucun projet existant n'est bloqué). Une section ``governance`` mal formée **qui demandait** ``block``
    reste bloquante (échec fermé) : corrompre la MOA ne désarme pas le gate ; sans MOA lisible, rien ne se valide.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    from grimoire.core.config import REQUIREMENT_GATE_BLOCK, GovernanceConfig
    from grimoire.core.exceptions import GrimoireConfigError

    path = root / "project-context.yaml"
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, UnicodeDecodeError, YAMLError):
        return GateMode("warn", problem="project-context.yaml illisible")
    section = data.get("governance") if isinstance(data, dict) else None
    try:
        gov = GovernanceConfig.from_dict(section)
    except GrimoireConfigError as exc:
        asked = isinstance(section, dict) and section.get("requirement_gate") == REQUIREMENT_GATE_BLOCK
        return GateMode("block" if asked else "warn", problem=f"section governance mal formée : {exc}")
    return GateMode(gov.requirement_mode, gov.moa, gov.declared)


# ── Exigence et empreinte ───────────────────────────────────────────────────


def lookup(root: Path, key: str) -> Requirement | None:
    """L'exigence *key* telle que le cadrage de *root* la déclare aujourd'hui ; absente ou illisible : ``None``."""
    from grimoire.core.cadrage_requirements import read_requirements

    match = _KEY.match(key)
    if match is None:
        return None
    try:
        found = read_requirements(root, match.group("chantier"))
    except (ValueError, OSError):
        return None
    return found.by_id().get(match.group("id"))


def fingerprint(key: str, requirement: Requirement) -> str:
    """Empreinte de ce que la MOA valide : la clé (chantier compris), la formulation, la priorité, les critères
    (ordre indifférent) et le périmètre. Un caractère changé dans l'un d'eux change l'empreinte."""
    body = json.dumps(
        [
            key,
            requirement.title.strip(),
            requirement.priority,
            sorted(c.strip() for c in requirement.criteria),
            bool(requirement.out_of_scope),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("ascii")).hexdigest()


def _journal(journal: AuthorityJournal | None) -> AuthorityJournal:
    if journal is not None:
        return journal
    from grimoire.missions.mandate import AuthorityJournal

    return AuthorityJournal()


def origin_root(root: Path) -> Path:
    """La racine d'origine du projet : depuis le worktree d'une tâche, le projet qui le porte (jamais le worktree)."""
    from grimoire.missions.task_worktree import home_of_worktree

    resolved = root.resolve()
    home = home_of_worktree(resolved)
    return home[0].resolve() if home is not None else resolved


# ── Validation (le geste de la MOA) ─────────────────────────────────────────


def _agent_signal() -> str:
    """Le signal qui fait de l'appelant une exécution d'agent ou sans humain ; ``""`` s'il n'y en a aucun.

    Les mêmes signaux que :func:`grimoire.cli.cmd_task._comment_origin` : ``GRIMOIRE_UNATTENDED=1``, une session
    d'hôte identifiée, une variable de terminal d'agent, et l'orchestrateur en processus (le tick).
    """
    import os

    from grimoire.core.standard_state import agent_marker, current_session_id
    from grimoire.missions.mandate import acting_in_process

    if os.environ.get("GRIMOIRE_UNATTENDED", "").strip() == "1":
        return "exécution déclarée sans humain (GRIMOIRE_UNATTENDED)"
    if acting_in_process():
        return "orchestrateur en processus"
    if current_session_id():
        return "session d'hôte identifiée"
    marker = agent_marker()
    return f"terminal d'agent ({marker})" if marker else ""


@dataclass(frozen=True, slots=True)
class HumanGesture:
    """Un geste réservé à la MOA : son nom (dans les refus), la commande à lancer soi-même, la question à l'écran.

    La validation (M3) et la recette (M4) passent par les mêmes contrôles d'origine (:func:`refuse_agent_origin`, puis
    :func:`attest_human_turn`) : seuls les mots changent.
    """

    noun: str
    command: str
    question: str


APPROVE_GESTURE = HumanGesture(
    "validation",
    "grimoire cadrage approve",
    "Cette validation sera enregistrée au nom de la MOA. Est-ce bien vous qui la donnez ? [o/N] ",
)


def _confirm_human_turn(question: str = APPROVE_GESTURE.question) -> bool:
    """Le tour humain : un terminal interactif (entrée et sortie d'erreur) et un « oui » donné à l'écran."""
    import sys

    try:
        interactive = sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return False
    try:
        sys.stderr.write(question)
        sys.stderr.flush()
        answer = sys.stdin.readline().strip().lower()
    except (OSError, ValueError):
        return False
    return answer in ("o", "oui", "y", "yes")


def _refuse_origin(why: str, gesture: HumanGesture = APPROVE_GESTURE) -> RequirementRefusedError:
    return RequirementRefusedError(
        "human_required",
        f"{gesture.noun} refusée : {why}. Seule la MOA, un humain, prononce ce geste depuis son terminal — ni un "
        f"agent, ni l'orchestrateur, même sous mandat. Lancez `{gesture.command}` vous-même.",
    )


def refuse_agent_origin(gesture: HumanGesture = APPROVE_GESTURE) -> None:
    """Refuse d'emblée (avant toute lecture) un appel venu d'un agent, de l'orchestrateur ou sans humain."""
    why = _agent_signal()
    if why:
        raise _refuse_origin(why, gesture)


def attest_human_turn(gesture: HumanGesture = APPROVE_GESTURE) -> None:
    """Exige le tour humain (terminal interactif et « oui » à l'écran) ; sinon rien ne doit être scellé."""
    if not _confirm_human_turn(gesture.question):
        raise _refuse_origin(
            "tour humain non attesté (pas de terminal interactif, ou pas de « oui » à l'écran)", gesture
        )


def approve_requirement(
    root: Path,
    key: str,
    *,
    actor: str,
    journal: AuthorityJournal | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scelle la validation de l'exigence *key* par la MOA déclarée ; rend l'entrée du journal.

    L'origine n'est pas un paramètre : la fonction l'établit elle-même (:func:`_agent_signal`, puis
    :func:`_confirm_human_turn`), que l'appel vienne de la CLI ou de l'API Python. Un marqueur d'agent refuse
    d'emblée, avant toute lecture ; sans terminal interactif ni « oui » à l'écran, rien n'est scellé.
    """
    refuse_agent_origin(APPROVE_GESTURE)
    key = normalize_key(key)
    project = origin_root(root)
    mode = gate_mode(project)
    if not mode.declared:
        raise RequirementRefusedError(
            "no_moa",
            "validation refusée : aucune MOA déclarée"
            + (f" ({mode.problem})" if mode.problem else "")
            + '. Nommez-la dans project-context.yaml (governance: {roles: {moa: "Prénom Nom"}}).',
        )
    requirement = lookup(project, key)
    if requirement is None:
        raise RequirementRefusedError(
            "unknown_requirement",
            f"exigence {key} introuvable dans le cadrage : `grimoire cadrage interview`, puis l'écrire "
            "(04-exigences.md ou 05-cahier-des-charges.md, une ligne `- REQ-001 : …`).",
        )
    if requirement.conflicts:
        raise RequirementRefusedError(
            "conflict", f"exigence {key} en conflit (deux formulations dans un même fichier) : à trancher avant."
        )
    if not requirement.title.strip():
        raise RequirementRefusedError("no_text", f"exigence {key} sans formulation : rien à valider.")
    if not requirement.planned:
        raise RequirementRefusedError("out_of_scope", f"exigence {key} hors périmètre (ou Won't) : rien à valider.")
    attest_human_turn(APPROVE_GESTURE)
    return _journal(journal).append(
        {
            "type": VALIDATED_ENTRY,
            "project": str(project),
            "requirement": key,
            "fingerprint": fingerprint(key, requirement),
            "title": requirement.title.strip(),
            "moa": mode.moa,
            "actor": actor,
            "origin": ORIGIN_HUMAN,
            "channel": APPROVE_CHANNEL,
            "ts": (now or datetime.now(UTC)).isoformat(),
        }
    )


def validation_of(
    root: Path, key: str, *, journal: AuthorityJournal | None = None
) -> tuple[dict[str, Any] | None, str]:
    """``(entrée, raison)`` : la validation scellée qui vaut pour le texte **actuel** de l'exigence, ou pourquoi aucune.

    Seul le journal d'autorité compte. Une entrée vaut si elle porte ce projet, cette clé, l'empreinte du texte
    actuel, l'origine humaine et la MOA déclarée aujourd'hui. Journal illisible ou rompu : rien n'est validé.
    """
    from grimoire.missions.mandate import JournalError

    project = origin_root(root)
    mode = gate_mode(project)
    if not mode.declared:
        return None, "aucune MOA déclarée : aucune exigence ne peut être validée" + (
            f" ({mode.problem})" if mode.problem else ""
        )
    requirement = lookup(project, key)
    if requirement is None:
        return None, f"exigence {key} introuvable dans le cadrage du projet"
    try:
        entries = _journal(journal).read()
    except JournalError as exc:
        return None, f"journal d'autorité illisible : {exc}"
    return validation_in(entries, str(project), key, fingerprint(key, requirement), mode.moa)


def validation_in(
    entries: Iterable[dict[str, Any]], project: str, key: str, current: str, moa: str
) -> tuple[dict[str, Any] | None, str]:
    """La règle de :func:`validation_of` sur des entrées déjà lues (le cockpit et la recette lisent le journal une
    fois pour plusieurs exigences) : projet, clé, canal, origine humaine, empreinte *current* et MOA *moa*."""
    stale = other_moa = False
    for entry in reversed(list(entries)):
        if (
            entry.get("type") != VALIDATED_ENTRY
            or entry.get("project") != project
            or entry.get("requirement") != key
            or entry.get("origin") != ORIGIN_HUMAN
            or entry.get("channel") != APPROVE_CHANNEL
        ):
            continue
        if entry.get("fingerprint") != current:
            stale = True
        elif entry.get("moa") != moa:
            other_moa = True
        else:
            return entry, ""
    if stale:
        return None, f"exigence {key} modifiée depuis sa validation : la MOA doit la revalider"
    if other_moa:
        return None, f"exigence {key} validée par une autre MOA que celle déclarée ({moa}) : à revalider"
    return None, f"exigence {key} non validée par la MOA ({moa})"


def sealed_validations(root: Path, *, journal: AuthorityJournal | None = None) -> int:
    """Le nombre de validations d'exigence scellées pour ce projet (journal illisible : 0, le doctor le dit ailleurs)."""
    from grimoire.missions.mandate import JournalError

    project = str(origin_root(root))
    try:
        entries = _journal(journal).read()
    except JournalError:
        return 0
    return sum(1 for e in entries if e.get("type") == VALIDATED_ENTRY and e.get("project") == project)


# ── Le contrôle d'une tâche ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    """Où en est la tâche vis-à-vis de l'exigence : ``ok``, ``exempt`` ou ``gap`` (avec la raison)."""

    state: str
    detail: str = ""
    nature: str = ""
    requirement: str = ""

    @property
    def gap(self) -> bool:
        return self.state == "gap"


def linked_missions(home_root: Path) -> dict[str, str]:
    """Chantier → mission liée (les chantiers sans mission ou illisibles n'y figurent pas ; tout illisible : ``{}``)."""
    from grimoire.core.chantiers import list_chantiers

    try:
        chantiers = list_chantiers(home_root)
    except Exception:  # un chantier illisible ne fonde aucune décision ici
        return {}
    return {c.slug: c.mission for c in chantiers if c.mission and not c.error}


def chantier_mismatch(home_root: Path, task: MissionTask, key: str, linked: dict[str, str] | None = None) -> str:
    """Une exigence d'un chantier lié à une autre mission que celle de la tâche, ou l'inverse ; ``""`` si cohérent.

    *linked* (:func:`linked_missions`) évite de relire les chantiers pour chaque tâche.
    """
    from grimoire.core.cadrage import DEFAULT_CHANTIER

    if linked is None:
        linked = linked_missions(home_root)
    wanted = _KEY.match(key)
    key_chantier = (wanted.group("chantier") if wanted else None) or DEFAULT_CHANTIER
    target = linked.get(key_chantier, "")
    if target and task.mission_id != target:
        return f"{key} appartient au chantier {key_chantier!r}, lié à la mission {target}, pas à {task.mission_id}"
    owners = sorted(slug for slug, mission in linked.items() if mission == task.mission_id)
    if owners and key_chantier not in owners:
        return f"la mission {task.mission_id} est celle du chantier {owners[0]!r} : {key} est l'exigence d'un autre chantier"
    return ""


def _is_cadrage_anchor(task: MissionTask) -> bool:
    """La tâche ancre des questions du cadrage (``cadrage plan``) : marqueur posé par le kit à sa création."""
    from grimoire.missions.cadrage_plan import is_anchor

    return is_anchor(task)


def _intake_nature(title: str) -> str:
    """La nature que l'intake déterministe lit dans *title* (``""`` si illisible) : une exemption ne se déclare pas seule."""
    from grimoire.missions.request_kind import classify_request_kind

    try:
        return str(classify_request_kind(title)["kind"])
    except (ValueError, KeyError):
        return ""


def check_task(home_root: Path, task: MissionTask, *, journal: AuthorityJournal | None = None) -> RequirementCheck:
    """La tâche sert-elle une exigence validée par la MOA ? Sans écrire : le gate décide quoi en faire."""
    nature = nature_of(task)
    if _is_cadrage_anchor(task):
        return RequirementCheck("exempt", "ancre des questions du cadrage : elle produit les exigences", "cadrage")
    exempt_detail = ""
    if nature in EXEMPT_NATURES:
        proposed = _intake_nature(task.title)
        if proposed == nature:
            later = " ; l'exigence se pose au post-mortem" if nature == "incident" else ""
            return RequirementCheck("exempt", f"nature {nature!r} exemptée{later}", nature)
        exempt_detail = f" (nature {nature!r} déclarée, mais l'intake lit le titre comme {proposed or 'illisible'!r} : pas d'exemption)"
    raw = requirement_of(task)
    if not raw:
        return RequirementCheck("gap", f"{task.id} ne cite aucune exigence{exempt_detail}", nature)
    try:
        key = normalize_key(raw)
    except GrimoireMissionError as exc:
        return RequirementCheck("gap", str(exc), nature, raw)
    mismatch = chantier_mismatch(home_root, task, key)
    if mismatch:
        return RequirementCheck("gap", mismatch, nature, key)
    entry, reason = validation_of(home_root, key, journal=journal)
    if entry is None:
        return RequirementCheck("gap", reason, nature, key)
    return RequirementCheck("ok", f"{key} validée par la MOA {entry.get('moa', '')}", nature, key)


def gate_refusal(home_root: Path, task: MissionTask, *, journal: AuthorityJournal | None = None) -> GateRefusal | None:
    """Le refus du gate d'exigence pour cette tâche : seulement en mode ``block`` et sans exigence validée."""
    from grimoire.missions.gates import GateRefusal

    mode = gate_mode(home_root)
    if not mode.blocking:
        return None
    check = check_task(home_root, task, journal=journal)
    if not check.gap:
        return None
    return GateRefusal("requirement_validated", f"{check.detail} — à faire : {REMEDY}", REMEDY)


def task_fields(requirement: str, nature: str) -> dict[str, str]:
    """Les champs ``extra`` d'une tâche à sa création : l'exigence normalisée, la nature validée (vides : rien)."""
    from grimoire.missions.request_kind import parse_kind

    fields: dict[str, str] = {}
    if requirement.strip():
        fields["requirement"] = normalize_key(requirement)
    if nature.strip():
        try:
            fields["nature"] = parse_kind(nature).value
        except ValueError as exc:
            raise GrimoireMissionError(str(exc)) from exc
    return fields


def transition_refusal(home_root: Path, task: MissionTask, target: Any) -> GateRefusal | None:
    """Le refus du gate pour ``task.status → target`` : ``proposed → ready``, et ``ready → claimed`` (en mode bloquant
    seulement, comme tout refus) contre une transition ``ready`` forgée au ledger ou une exigence modifiée depuis."""
    from grimoire.missions.schemas import TaskState

    if (task.status, target) not in {(TaskState.PROPOSED, TaskState.READY), (TaskState.READY, TaskState.CLAIMED)}:
        return None
    return gate_refusal(home_root, task)


def queue_refusal(home_root: Path) -> Callable[[MissionTask], str] | None:
    """En mode bloquant, la raison qui écarte une tâche ``ready`` de la file d'admission ; ``None`` sinon."""
    if not gate_mode(home_root).blocking:
        return None

    def refused(task: MissionTask) -> str:
        found = gate_refusal(home_root, task)
        return f"exigence non validée par la MOA : {found.reason}" if found is not None else ""

    return refused


def record_transition(ledger: Any, home_root: Path, previous: Any, moved: MissionTask, actor: str) -> None:
    """Après une transition écrite : seul ``proposed → ready`` laisse un événement (:func:`record_ready`)."""
    from grimoire.missions.schemas import TaskState

    if previous is TaskState.PROPOSED and moved.status is TaskState.READY:
        record_ready(ledger, home_root, moved, actor)


def record_ready(ledger: Any, home_root: Path, task: MissionTask, actor: str) -> None:
    """Après ``proposed → ready`` : un passage exempté ou signalé laisse un événement (information, jamais preuve).

    Un passage refusé n'arrive pas ici. En mode ``warn``, l'absence d'exigence validée n'arrête rien : elle se lit
    au cockpit et dans ``task trace``. Best-effort : un événement qui ne s'écrit pas ne défait pas la transition.
    """
    try:
        check = check_task(home_root, task)
        if check.state == "exempt":
            ledger.append_event(
                EXEMPTED_EVENT, task.id, "task", actor, {"nature": check.nature, "detail": check.detail}
            )
        elif check.gap:
            payload = {"detail": check.detail, "mode": gate_mode(home_root).mode, "remedy": REMEDY}
            ledger.append_event(FLAGGED_EVENT, task.id, "task", actor, payload)
    except Exception:  # noqa: S110 — observabilité : jamais au prix de la transition elle-même
        pass


def pending_review(home_root: Path, tasks: Iterable[MissionTask], events: Iterable[Any]) -> dict[str, list[str]]:
    """Pour le cockpit : ce qui reste à faire valider ou à cadrer après coup.

    - ``unvalidated`` : tâches ouvertes passées ``ready`` sans exigence validée ;
    - ``questions`` : tâches ouvertes exemptées comme question ;
    - ``postmortem`` : incidents exemptés, ouverts ou clos, tant qu'aucune exigence ne leur est rattachée.

    Vide quand le projet ne déclare pas de MOA (le doctor avertit déjà de l'absence de section). L'appelant teste
    :func:`gate_mode` avant de lire les événements, qui coûtent sur un gros ledger.
    """
    out: dict[str, list[str]] = {"unvalidated": [], "questions": [], "postmortem": []}
    if not gate_mode(home_root).declared:
        return out
    flagged: set[str] = set()
    exempted: set[str] = set()
    for event in events:
        if event.event_type == FLAGGED_EVENT:
            flagged.add(event.entity_id)
        elif event.event_type == EXEMPTED_EVENT:
            exempted.add(event.entity_id)
    for task in tasks:
        status = task.status.value
        if status == "cancelled":
            continue
        if task.id in flagged and status != "closed" and check_task(home_root, task).gap:
            out["unvalidated"].append(task.id)
        elif task.id in exempted:
            nature = nature_of(task)
            if nature == "incident" and not requirement_of(task):
                out["postmortem"].append(task.id)
            elif nature == "question" and status != "closed":
                out["questions"].append(task.id)
    return out
