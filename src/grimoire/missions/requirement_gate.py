"""L'exigence validée par la MOA, et le gate qui en dépend (#1342, lot M3).

L'agent propose des exigences, seul l'humain déclaré MOA les valide, et une tâche ne devient ``ready`` que si elle
sert une exigence validée. Trois pièces :

- **la validation**, scellée au journal d'autorité (``~/.grimoire/control``, chaîné par hash, écrit par du code du
  kit) — pas au ledger du projet, que le worker écrit : une ligne ``requirement.validated`` forgée au ledger ne
  compte pour rien. Comme le READY scellé et la dérogation de l'arbitre (#1330), elle se lit là où le worker n'écrit
  pas. Elle ne s'inscrit que depuis le canal humain de ``task answer`` / ``task accept`` : jamais depuis une session
  d'agent, jamais sous mandat d'orchestrateur ;
- **l'empreinte** de l'exigence au moment de la validation (titre, priorité, critères) : modifier l'exigence après
  coup retire la validation, la MOA a validé ce texte-là ;
- **le gate** ``proposed → ready`` : bloquant ou signalé selon le projet (:attr:`GovernanceConfig.requirement_mode`),
  les natures ``question`` et ``incident`` exemptées.

Le projet, la MOA et le mode se lisent à la **racine d'origine** du projet, jamais dans le worktree d'une tâche : un
worker n'écrit pas la déclaration qui l'oppose.
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
    from collections.abc import Iterable
    from pathlib import Path

    from grimoire.missions.gates import GateRefusal
    from grimoire.missions.mandate import AuthorityJournal
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "EXEMPTED_EVENT",
    "EXEMPT_NATURES",
    "FLAGGED_EVENT",
    "VALIDATED_ENTRY",
    "RequirementCheck",
    "RequirementRefusedError",
    "check_task",
    "fingerprint",
    "flagged_task_ids",
    "governance_of",
    "nature_of",
    "normalize_key",
    "refusal_of",
    "requirement_of",
    "validate_requirement",
    "validation_of",
]

#: Le type de l'entrée du journal d'autorité qui scelle la validation d'une exigence.
VALIDATED_ENTRY = "requirement_validated"
#: Événements du ledger (information, jamais preuve) : un passage ``ready`` signalé, un passage exempté.
FLAGGED_EVENT = "task.requirement_unvalidated"
EXEMPTED_EVENT = "task.requirement_exempted"
#: Les natures de demande qui ne demandent pas d'exigence : une question n'engage rien ; un incident se rétablit
#: d'abord, son exigence arrive au post-mortem.
EXEMPT_NATURES = frozenset({"question", "incident"})

_KEY = re.compile(r"^(?:(?P<chantier>[a-z0-9][a-z0-9-]*)/)?(?P<id>REQ-\d{1,4})$")
INTERVIEW_REMEDY = (
    "`grimoire cadrage interview` pour cadrer le besoin, puis faire valider l'exigence par la MOA "
    "(`grimoire cadrage approve <exigence>`), et rattacher la tâche (`task add --requirement <id>`)"
)


class RequirementRefusedError(GrimoireMissionError):
    """La validation d'une exigence est refusée ; *code* dit pourquoi."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def normalize_key(raw: str) -> str:
    """``REQ-001`` ou ``<chantier>/REQ-001`` (la clé de ``extra.covers``) ; tout autre texte est refusé."""
    text = str(raw).strip()
    if text.upper().startswith("REQ-"):
        text = "REQ-" + text[4:]
    elif "/REQ-" in text.upper():
        head, _, tail = text.rpartition("/")
        text = f"{head}/REQ-{tail[4:]}"
    if not _KEY.match(text):
        raise GrimoireMissionError(
            f"exigence {raw!r} invalide : attendu REQ-<chiffres>, ou <chantier>/REQ-<chiffres> hors du cadrage principal"
        )
    return text


def requirement_of(task: MissionTask) -> str:
    """L'exigence qu'une tâche déclare servir (``extra.requirement``) ; vide si elle n'en porte pas."""
    raw = (task.extra or {}).get("requirement")
    return raw.strip() if isinstance(raw, str) else ""


def nature_of(task: MissionTask) -> str:
    """La nature déclarée de la demande (``extra.nature``), en minuscules ; vide si absente."""
    raw = (task.extra or {}).get("nature")
    return raw.strip().lower() if isinstance(raw, str) else ""


def governance_of(root: Path) -> Any:
    """La section ``governance`` du projet à *root* ; absente ou illisible : la section vide (jamais bloquant)."""
    from grimoire.core.config import GovernanceConfig, GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError

    path = root / "project-context.yaml"
    if not path.is_file():
        return GovernanceConfig()
    try:
        return GrimoireConfig.from_yaml(path).governance
    except GrimoireConfigError:
        return GovernanceConfig()


def _lookup(root: Path, key: str) -> Any:
    from grimoire.core.cadrage_requirements import read_requirements

    match = _KEY.match(key)
    if match is None:
        return None
    chantier = match.group("chantier")
    try:
        found = read_requirements(root, chantier)
    except (ValueError, OSError):
        return None
    return found.by_id().get(match.group("id"))


def fingerprint(requirement: Any) -> str:
    """Empreinte de ce que la MOA valide : titre, priorité, critères (ordre indifférent)."""
    body = json.dumps(
        [
            requirement.id,
            requirement.title.strip(),
            requirement.priority,
            sorted(c.strip() for c in requirement.criteria),
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


def validate_requirement(
    root: Path,
    key: str,
    *,
    origin: str,
    actor: str,
    journal: AuthorityJournal | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scelle la validation de l'exigence *key* par la MOA déclarée ; rend l'entrée du journal.

    *origin* vient du canal d'appel (:func:`grimoire.missions.mandate.gesture_origin` sur
    ``cmd_task._comment_origin``), jamais d'un champ libre : tout ce qui n'est pas ``human`` est refusé — session
    d'agent, terminal non vérifié, orchestrateur sous mandat. Le geste n'est d'ailleurs pas de ceux qu'un mandat
    peut couvrir.
    """
    if origin != ORIGIN_HUMAN:
        raise RequirementRefusedError(
            "human_required",
            f"validation refusée : origine {origin!r}. Seul l'humain MOA valide une exigence — ni un agent, ni "
            "l'orchestrateur, même sous mandat. Lance `grimoire cadrage approve` depuis ton terminal.",
        )
    key = normalize_key(key)
    project = root.resolve()
    gov = governance_of(project)
    if not gov.declared:
        raise RequirementRefusedError(
            "no_moa",
            "validation refusée : aucune MOA déclarée. Nomme-la dans project-context.yaml "
            '(governance: {roles: {moa: "Prénom Nom"}}).',
        )
    requirement = _lookup(project, key)
    if requirement is None:
        raise RequirementRefusedError(
            "unknown_requirement",
            f"exigence {key} introuvable dans le cadrage : `grimoire cadrage interview`, puis l'écrire.",
        )
    if requirement.conflicts:
        raise RequirementRefusedError(
            "conflict",
            f"exigence {key} en conflit (deux formulations dans un même fichier) : à trancher avant de la valider.",
        )
    entry = _journal(journal).append(
        {
            "type": VALIDATED_ENTRY,
            "project": str(project),
            "requirement": key,
            "fingerprint": fingerprint(requirement),
            "moa": gov.moa,
            "actor": actor,
            "origin": ORIGIN_HUMAN,
            "ts": (now or datetime.now(UTC)).isoformat(),
        }
    )
    return entry


def validation_of(
    root: Path, key: str, *, journal: AuthorityJournal | None = None
) -> tuple[dict[str, Any] | None, str]:
    """``(entrée, raison)`` : la validation scellée qui vaut pour le texte *actuel* de l'exigence, ou pourquoi aucune.

    Un journal illisible ou rompu ne valide rien (échec fermé) ; une exigence modifiée depuis sa validation non plus.
    """
    from grimoire.missions.mandate import JournalError

    project = root.resolve()
    requirement = _lookup(project, key)
    if requirement is None:
        return None, f"exigence {key} introuvable dans le cadrage du projet"
    try:
        entries = _journal(journal).read()
    except JournalError as exc:
        return None, f"journal d'autorité illisible : {exc}"
    current = fingerprint(requirement)
    seen = False
    for entry in reversed(entries):
        if (
            entry.get("type") != VALIDATED_ENTRY
            or entry.get("project") != str(project)
            or entry.get("requirement") != key
        ):
            continue
        if entry.get("fingerprint") == current and entry.get("origin") == ORIGIN_HUMAN:
            return entry, ""
        seen = True
    if seen:
        return None, f"exigence {key} modifiée depuis sa validation : la MOA doit la revalider"
    return None, f"exigence {key} non validée par la MOA"


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    """Où en est la tâche vis-à-vis de l'exigence : ``ok``, ``exempt`` ou ``gap`` (avec la raison)."""

    state: str
    detail: str = ""
    nature: str = ""

    @property
    def gap(self) -> bool:
        return self.state == "gap"


def check_task(home_root: Path, task: MissionTask, *, journal: AuthorityJournal | None = None) -> RequirementCheck:
    """La tâche sert-elle une exigence validée par la MOA ? Sans écrire, sans exiger : le gate décide quoi en faire."""
    nature = nature_of(task)
    if nature in EXEMPT_NATURES:
        return RequirementCheck("exempt", f"nature {nature!r} exemptée", nature)
    key = requirement_of(task)
    if not key:
        return RequirementCheck("gap", f"{task.id} ne cite aucune exigence (`task add --requirement <id>`)")
    try:
        key = normalize_key(key)
    except GrimoireMissionError as exc:
        return RequirementCheck("gap", str(exc))
    entry, reason = validation_of(home_root, key, journal=journal)
    if entry is None:
        return RequirementCheck("gap", reason)
    return RequirementCheck("ok", f"{key} validée par {entry.get('moa', '')}")


def refusal_of(
    home_root: Path, task: MissionTask, *, journal: AuthorityJournal | None = None
) -> tuple[GateRefusal | None, RequirementCheck, str]:
    """``(refus, contrôle, mode)`` pour ``proposed → ready`` : le refus n'existe que si le mode est bloquant."""
    from grimoire.missions.gates import GateRefusal

    mode = governance_of(home_root).requirement_mode
    check = check_task(home_root, task, journal=journal)
    if not check.gap or mode != "blocking":
        return None, check, mode
    return GateRefusal("requirement_validated", check.detail, INTERVIEW_REMEDY), check, mode


def record_ready(ledger: Any, home_root: Path, task: MissionTask, actor: str) -> None:
    """Après ``proposed → ready`` : un passage exempté ou signalé laisse un événement (information, jamais preuve).

    Un passage bloqué n'arrive pas ici. En mode signalé, l'absence d'exigence validée n'arrête rien : elle se lit
    au cockpit et dans ``task trace``. Best-effort : un événement qui ne s'écrit pas ne défait pas la transition.
    """
    try:
        check = check_task(home_root, task)
        if check.state == "exempt":
            ledger.append_event(
                EXEMPTED_EVENT, task.id, "task", actor, {"nature": check.nature, "detail": check.detail}
            )
        elif check.gap:
            ledger.append_event(FLAGGED_EVENT, task.id, "task", actor, {"detail": check.detail})
    except Exception:  # noqa: S110 — observabilité : jamais au prix de la transition elle-même
        pass


def flagged_task_ids(events: Iterable[Any]) -> set[str]:
    """Les tâches dont un passage ``ready`` a été signalé sans exigence validée (information pour le cockpit)."""
    return {e.entity_id for e in events if e.event_type == FLAGGED_EVENT}
