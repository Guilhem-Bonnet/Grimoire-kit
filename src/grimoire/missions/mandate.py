"""Mandat de l'orchestrateur et journal d'autorité (#876, lot 1 : verrous 6, 7, 9 et 15).

Les gestes de validation (``task accept``, ``task answer``, ``task approve-check``,
``orchestrate resume``, ``queue release``) exigent l'origine ``human``. Ce module
ajoute un principal distinct, ``orchestrator``, qui agit sous un **mandat** :

- l'humain l'accorde une fois, par le mécanisme d'attestation existant
  (:func:`grimoire.flows.attestation.attest`, un tour humain de la session qui demande —
  voie CLI) ou par le cockpit derrière son jeton et la clé de contrôle (voie cockpit) ;
- il est borné : gestes couverts, types de tâches, plafond de sessions de worker par jour,
  durée ; il ne vaut que pour un projet ;
- il est consigné dans un **journal d'autorité** chaîné par hash
  (``~/.grimoire/control/mandates.authority.jsonl``) : chaque entrée porte ``seq``, ``prev``
  et ``hash`` ; une entrée modifiée ou retirée au milieu rompt la chaîne, et un journal
  illisible ou rompu ne porte aucun mandat (échec fermé) ;
- chaque geste pris sous mandat y est inscrit avec ``origin: orchestrator``, l'identifiant du
  mandat et sa provenance (règle, verdict ou reçu).

Deux gestes ne sont jamais couverts, quel que soit le mandat (:data:`NEVER_COVERED`) :
modifier le mandat et modifier la politique d'escalade. Le second est tenu par une empreinte :
le mandat scelle, à l'octroi, la section ``escalation`` de ``orchestration-policy.yaml`` ; une
politique modifiée depuis révoque le mandat (``escalation_policy_changed``).

Un mandat est révoqué par l'humain (:meth:`MandateStore.revoke`) ou automatiquement sur
événement : échéance, plafond de sessions dépassé, écriture d'agent refusée dans la zone
protégée (:meth:`MandateStore.revoke_on_protected_write`), politique modifiée. Le verdict forgé
(:meth:`MandateStore.revoke_on_forged_verdict`) et l'écriture hors worktree
(:meth:`MandateStore.revoke_on_write_outside_worktree`) sont exposés ici et branchés aux
lots 2 et 3.

**Limites écrites.** Le journal vit sous ``~/.grimoire/control/`` ; le frein ``PreToolUse`` refuse
toute écriture d'agent sous ce dossier par les outils et les voies shell reconnues
(:mod:`grimoire.hosts.decisions.authority_guard`). Un agent qui exécute du code arbitraire
(un script Python quelconque) peut le contourner, et la chaîne de hash ne voit pas la troncature de
la *queue* du journal (retirer une révocation récente rouvre le mandat). Le principal
``orchestrator`` se déclare par ``GRIMOIRE_PRINCIPAL=orchestrator`` ; tant que le lanceur du
lot 2 n'enregistre pas la session de l'orchestrateur (:meth:`MandateStore.register_session`,
rôle ``orchestrator``), cette déclaration ne vaut que par le mandat qu'elle exige.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import locked
from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.missions.schemas import ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR

if TYPE_CHECKING:
    from grimoire.flows.attestation import Attestation
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "BREAKER_FORGED_VERDICT",
    "BREAKER_SEAL_MISMATCH",
    "BREAKER_WRITE_OUTSIDE_WORKTREE",
    "ESCALATION_CATEGORIES",
    "GESTURES",
    "JOURNAL_NAME",
    "NEVER_COVERED",
    "PRINCIPAL_ENV",
    "PROVENANCE_ENV",
    "PROVENANCE_KINDS",
    "AuthorityJournal",
    "Authorization",
    "EscalationPolicy",
    "JournalError",
    "Mandate",
    "MandateRefusedError",
    "MandateRequest",
    "MandateState",
    "MandateStore",
    "control_dir",
    "control_key_path",
    "ensure_control_key",
    "gesture_origin",
    "load_escalation",
    "require_authority",
    "revoke_every_mandate",
    "task_categories",
    "verify_control_key",
]

#: Les gestes qu'un mandat peut couvrir.
GESTURES: tuple[str, ...] = ("accept", "answer", "approve-check", "resume", "queue-release", "merge")
#: Les gestes qu'aucun mandat ne couvre : le mandat lui-même et la politique d'escalade.
NEVER_COVERED: tuple[str, ...] = ("modify-mandate", "modify-escalation-policy")
#: Catégories d'escalade génériques que chaque projet relie à ses propres chemins, commandes, labels.
ESCALATION_CATEGORIES: tuple[str, ...] = ("release", "secrets", "paid", "security", "merge", "accept")
#: Catégorie propre à un geste (les autres gestes n'en ont pas : seule la tâche en porte).
_GESTURE_CATEGORY = {"accept": "accept", "merge": "merge"}
#: D'où vient la décision prise sous mandat.
PROVENANCE_KINDS: tuple[str, ...] = ("rule", "verdict", "receipt")

JOURNAL_NAME = "mandates.authority.jsonl"
KEY_NAME = "cockpit-key"
ORCHESTRATION_FILE = STANDARD_DIR / "orchestration-policy.yaml"
#: Variable qui déclare le principal ``orchestrator`` (lot 2 : posée par le lanceur du tick).
PRINCIPAL_ENV = "GRIMOIRE_PRINCIPAL"
#: ``<kind>:<ref>`` : la provenance d'un geste pris par la CLI sous mandat.
PROVENANCE_ENV = "GRIMOIRE_PROVENANCE"

# Raisons d'inactivité d'un mandat.
NO_MANDATE = "no_mandate"
REVOKED = "revoked"
EXPIRED = "expired"
CAP_EXCEEDED = "cap_exceeded"
JOURNAL_UNREADABLE = "journal_unreadable"
POLICY_CHANGED = "escalation_policy_changed"
MALFORMED = "malformed"
# Motifs de révocation (en plus de ceux ci-dessus).
REVOKE_MANUAL = "manual"
REVOKE_PROTECTED_WRITE = "protected_write"
REVOKE_FORGED_VERDICT = "forged_verdict"
REVOKE_WRITE_OUTSIDE_WORKTREE = "write_outside_worktree"
# Disjoncteur : ce que le lot 2 consomme.
BREAKER_SEAL_MISMATCH = "check_seal_mismatch"
BREAKER_FORGED_VERDICT = "forged_verdict"
BREAKER_WRITE_OUTSIDE_WORKTREE = "write_outside_worktree"

_GENESIS = "0" * 64
_ROLES = ("worker", "reviewer", "orchestrator")


class JournalError(GrimoireMissionError):
    """Le journal d'autorité est illisible ou sa chaîne de hash est rompue : il ne porte plus rien."""


class MandateRefusedError(GrimoireMissionError):
    """Un geste pris sous mandat est refusé ; *code* dit pourquoi."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(ts: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def control_dir() -> Path:
    """``~/.grimoire/control`` : la zone que le frein refuse aux agents (#886 d)."""
    return Path.home() / ".grimoire" / "control"


def control_key_path() -> Path:
    return control_dir() / KEY_NAME


def ensure_control_key() -> Path:
    """Crée (une fois) la clé de contrôle du cockpit, 0600 ; le kit l'écrit, aucun agent ne la lit.

    Le jeton de session du cockpit est injecté dans toute page HTML servie sur la loopback : un
    processus local qui récupère la page le lit. Il ne suffit donc pas à accorder un mandat ;
    l'octroi exige en plus cette clé, que l'humain lit depuis son terminal.
    """
    path = control_key_path()
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secrets.token_urlsafe(32) + "\n")
    return path


def verify_control_key(given: object) -> bool:
    """Vrai si *given* est la clé de contrôle ; absente, illisible ou vide : faux (échec fermé)."""
    if not isinstance(given, str) or not given:
        return False
    try:
        expected = control_key_path().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(expected) and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


# ── Journal chaîné ───────────────────────────────────────────────────────────


def _canon(body: Mapping[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _link(prev: str, body: Mapping[str, Any]) -> str:
    return hashlib.sha256(f"{prev}\n{_canon(body)}".encode("ascii")).hexdigest()


class AuthorityJournal:
    """Entrées chaînées par hash, ajout seul, sous verrou. Écrit par du code du kit, jamais par un outil d'agent."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or control_dir() / JOURNAL_NAME

    def read(self) -> list[dict[str, Any]]:
        """Les entrées, chaîne vérifiée ; absent : ``[]`` ; illisible ou rompu : :class:`JournalError`."""
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise JournalError(f"journal d'autorité illisible : {type(exc).__name__}") from exc
        out: list[dict[str, Any]] = []
        prev = _GENESIS
        for index, line in enumerate(raw.splitlines()):
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise JournalError(f"journal d'autorité : ligne {index + 1} illisible") from exc
            if not isinstance(entry, dict):
                raise JournalError(f"journal d'autorité : ligne {index + 1} n'est pas un objet")
            body = {k: v for k, v in entry.items() if k != "hash"}
            if entry.get("seq") != index or entry.get("prev") != prev or entry.get("hash") != _link(prev, body):
                raise JournalError(f"journal d'autorité : chaîne de hash rompue à la ligne {index + 1}")
            prev = str(entry["hash"])
            out.append(entry)
        return out

    def append(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        with locked(self.path):
            entries = self.read()  # un journal rompu n'est jamais prolongé
            prev = str(entries[-1]["hash"]) if entries else _GENESIS
            body = {**entry, "seq": len(entries), "prev": prev}
            full = {**body, "hash": _link(prev, body)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(full, ensure_ascii=True) + "\n")
        return full


# ── Politique d'escalade ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EscalationPolicy:
    """Ce que le projet délègue à l'orchestrateur ; tout le reste reste humain.

    Section ``escalation`` de ``_grimoire/standard/orchestration-policy.yaml`` : une catégorie
    de :data:`ESCALATION_CATEGORIES` vaut ``human`` ou ``orchestrator``. Absente, illisible ou
    invalide : tout est humain (*error* dit pourquoi dans le dernier cas).
    """

    delegated: frozenset[str] = frozenset()
    error: str = ""

    @property
    def sha256(self) -> str:
        if self.error:
            return "invalid:" + hashlib.sha256(self.error.encode("utf-8")).hexdigest()
        table = {c: ("orchestrator" if c in self.delegated else "human") for c in ESCALATION_CATEGORIES}
        return hashlib.sha256(_canon(table).encode("ascii")).hexdigest()

    def delegates(self, category: str) -> bool:
        return not self.error and category in self.delegated


def load_escalation(project_root: Path) -> EscalationPolicy:
    path = project_root / ORCHESTRATION_FILE
    if not path.is_file():
        return EscalationPolicy()
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # une politique qui ne se lit pas ne délègue rien
        return EscalationPolicy(error=f"{ORCHESTRATION_FILE.as_posix()} illisible : {type(exc).__name__}")
    section = data.get("escalation") if isinstance(data, Mapping) else None
    if section is None:
        return EscalationPolicy()
    if not isinstance(section, Mapping):
        return EscalationPolicy(error="`escalation` n'est pas un mapping catégorie → human|orchestrator")
    delegated: set[str] = set()
    for category, owner in section.items():
        if category not in ESCALATION_CATEGORIES:
            return EscalationPolicy(error=f"catégorie d'escalade inconnue : {category!r}")
        if owner not in ("human", "orchestrator"):
            return EscalationPolicy(
                error=f"`escalation.{category}` : 'human' ou 'orchestrator' attendu, reçu {owner!r}"
            )
        if owner == "orchestrator":
            delegated.add(str(category))
    return EscalationPolicy(frozenset(delegated))


def task_categories(task: MissionTask) -> frozenset[str]:
    """Les catégories d'escalade qu'une tâche touche : son type, son profil de risque et ses ``labels``."""
    from grimoire.missions.schemas import RiskProfile, TaskType

    found: set[str] = set()
    if task.type is TaskType.SECURITY or task.risk_profile is RiskProfile.SECURITY_CRITICAL:
        found.add("security")
    if task.risk_profile is RiskProfile.RELEASE:
        found.add("release")
    labels = task.extra.get("labels") if isinstance(task.extra, Mapping) else None
    if isinstance(labels, list | tuple):
        found.update(
            str(label).strip().lower() for label in labels if str(label).strip().lower() in ESCALATION_CATEGORIES
        )
    return frozenset(found)


# ── Mandat ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MandateRequest:
    """Ce que l'humain octroie — validé ; son empreinte relie un challenge à la même demande relancée."""

    gestures: tuple[str, ...]
    task_types: tuple[str, ...]
    max_sessions_per_day: int
    until_raw: str

    @classmethod
    def build(
        cls, gestures: Iterable[str], task_types: Iterable[str], max_sessions_per_day: int, until: str
    ) -> MandateRequest:
        from grimoire.flows.autonomy import parse_until
        from grimoire.missions.schemas import TaskType

        wanted = tuple(dict.fromkeys(str(g).strip() for g in gestures if str(g).strip()))
        if not wanted:
            raise MandateRefusedError(MALFORMED, "mandat sans geste : nomme au moins un geste couvert")
        for gesture in wanted:
            if gesture in NEVER_COVERED:
                raise MandateRefusedError(
                    MALFORMED,
                    f"{gesture} n'est jamais couvert par un mandat (le mandat et la politique d'escalade restent humains)",
                )
            if gesture not in GESTURES:
                raise MandateRefusedError(MALFORMED, f"geste inconnu : {gesture!r} — parmi {', '.join(GESTURES)}")
        types = tuple(dict.fromkeys(str(t).strip() for t in task_types if str(t).strip()))
        known = {t.value for t in TaskType}
        if not types or any(t not in known for t in types):
            raise MandateRefusedError(
                MALFORMED, f"types de tâche invalides ou absents : parmi {', '.join(sorted(known))}"
            )
        if (
            isinstance(max_sessions_per_day, bool)
            or not isinstance(max_sessions_per_day, int)
            or not 1 <= max_sessions_per_day <= 1000
        ):
            raise MandateRefusedError(MALFORMED, "plafond de sessions par jour : entier entre 1 et 1000")
        try:
            parse_until(until)
        except Exception as exc:
            raise MandateRefusedError(MALFORMED, str(exc)) from exc
        return cls(wanted, types, max_sessions_per_day, until)

    @property
    def sha256(self) -> str:
        body = {
            "g": sorted(self.gestures),
            "t": sorted(self.task_types),
            "n": self.max_sessions_per_day,
            "u": self.until_raw,
        }
        return hashlib.sha256(_canon(body).encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class Mandate:
    id: str
    project: str
    gestures: tuple[str, ...]
    task_types: tuple[str, ...]
    max_sessions_per_day: int
    until: str
    escalation_sha256: str
    granted_at: str
    via: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "gestures": list(self.gestures),
            "task_types": list(self.task_types),
            "max_sessions_per_day": self.max_sessions_per_day,
            "until": self.until,
            "escalation_sha256": self.escalation_sha256,
            "granted_at": self.granted_at,
            "via": self.via,
        }


@dataclass(frozen=True, slots=True)
class MandateState:
    """Le mandat du projet réévalué maintenant : actif, ou la raison pour laquelle il ne l'est pas."""

    mandate: Mandate | None
    reason: str = ""
    detail: str = ""
    sessions_today: int = 0

    @property
    def active(self) -> bool:
        return self.mandate is not None and not self.reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "reason": self.reason,
            "detail": self.detail,
            "sessions_today": self.sessions_today,
            "mandate": self.mandate.to_dict() if self.mandate else None,
        }


@dataclass(frozen=True, slots=True)
class Authorization:
    """Un geste couvert : de quoi le journaliser et le tracer (``origin: orchestrator``)."""

    mandate_id: str
    gesture: str
    task_id: str
    provenance: Mapping[str, str] = field(default_factory=dict)
    origin: str = ORIGIN_ORCHESTRATOR

    @property
    def actor_label(self) -> str:
        return f"{ORIGIN_ORCHESTRATOR}:{self.mandate_id}"


def _provenance(raw: Mapping[str, Any] | None) -> dict[str, str]:
    kind = str((raw or {}).get("kind", "")).strip()
    ref = str((raw or {}).get("ref", "")).strip()
    if kind not in PROVENANCE_KINDS or not ref:
        raise MandateRefusedError(
            MALFORMED,
            f"provenance absente : un geste sous mandat cite sa règle, son verdict ou son reçu ({'|'.join(PROVENANCE_KINDS)}:<réf>)",
        )
    return {"kind": kind, "ref": ref}


class MandateStore:
    """Le mandat d'un projet, lu et écrit dans le journal d'autorité."""

    def __init__(self, project_root: Path, journal: AuthorityJournal | None = None) -> None:
        self.root = project_root.resolve()
        self.project = str(self.root)
        self.journal = journal or AuthorityJournal()

    # ── Octroi ───────────────────────────────────────────────────────────────

    def grant(
        self,
        request: MandateRequest,
        *,
        origin: str,
        via: str,
        attestation: Attestation | None = None,
        now: datetime | None = None,
    ) -> Mandate:
        """Inscrit le mandat. *origin* vient du canal (cockpit derrière son jeton, tour humain attesté), jamais d'un champ libre."""
        if origin != ORIGIN_HUMAN:
            raise MandateRefusedError(
                "human_required",
                f"octroi refusé : origine {origin!r}. Seul l'humain accorde un mandat — pas l'orchestrateur, pas un agent",
            )
        from grimoire.flows.autonomy import parse_until

        moment = now or _now()
        mandate_id = f"MND-{uuid.uuid4().hex[:12]}"
        until = parse_until(request.until_raw, moment).isoformat()
        entry = self.journal.append(
            {
                "type": "grant",
                "mandate_id": mandate_id,
                "project": self.project,
                "gestures": list(request.gestures),
                "task_types": list(request.task_types),
                "max_sessions_per_day": request.max_sessions_per_day,
                "until": until,
                "escalation_sha256": load_escalation(self.root).sha256,
                "via": via,
                "attestation": attestation.status if attestation else "",
                "session_id": attestation.session_id if attestation else "",
                "ts": moment.isoformat(),
            }
        )
        return _mandate_of(entry)

    def request_grant(
        self, request: MandateRequest, env: Mapping[str, str] | None = None, now: datetime | None = None
    ) -> tuple[Mandate | None, Attestation]:
        """Voie CLI : inscrit le mandat si la demande est attestée par un tour humain ; sinon émet son challenge.

        Même mécanisme que ``grimoire autonomy grant`` (:meth:`grimoire.flows.autonomy.GrantJournal.request`) :
        le challenge est celui de cette demande et de cette session, un refus ne le consomme pas, un
        tour humain ne sert qu'une décision (registre commun des tours consommés).
        """
        from grimoire.core.standard_state import current_session
        from grimoire.flows.attestation import CheckpointJournal, attest

        entries = self.journal.read()
        session_id, _host = current_session(env)
        used = {e.get("challenge_id") for e in entries if e.get("type") == "grant"}
        challenge = next(
            (
                e
                for e in reversed(entries)
                if e.get("type") == "grant_challenge"
                and e.get("request_sha256") == request.sha256
                and e.get("project") == self.project
                and e.get("session_id", "") == session_id
            ),
            None,
        )
        if challenge is not None and challenge.get("challenge_id") in used:
            challenge = None
        if challenge is None:
            challenge = self.journal.append(
                {
                    "type": "grant_challenge",
                    "challenge_id": f"MCH-{uuid.uuid4().hex[:12]}",
                    "request_sha256": request.sha256,
                    "project": self.project,
                    "session_id": session_id,
                    "ts": (now or _now()).isoformat(),
                }
            )
        turns = CheckpointJournal(self.root / "_grimoire-runtime-output" / "flows", "autonomy-grants")
        attestation = attest(self.root, challenge, env, turns.consumed_turns())
        if not attestation.attested:
            return None, attestation
        mandate = self.grant(request, origin=ORIGIN_HUMAN, via="attestation", attestation=attestation, now=now)
        turns.consume_turn(mandate.id, attestation)
        self.journal.append(
            {
                "type": "grant_challenge_used",
                "challenge_id": attestation.challenge_id,
                "mandate_id": mandate.id,
                "ts": mandate.granted_at,
            }
        )
        return mandate, attestation

    # ── Révocation ───────────────────────────────────────────────────────────

    def revoke(self, reason: str = REVOKE_MANUAL, detail: str = "", now: datetime | None = None) -> Mandate | None:
        """Révoque le mandat actif du projet — toujours permis : retirer une autorité n'en demande aucune.

        Rend le mandat révoqué, ``None`` s'il n'y en avait pas d'actif. API que les lots 2 et 3 appellent
        sur événement (voir ``revoke_on_*``).
        """
        state = self._state(now or _now(), auto_revoke=False)
        if state.mandate is None or not state.active:
            return None
        self._write_revocation(state.mandate.id, reason or REVOKE_MANUAL, detail, now or _now())
        return state.mandate

    def _write_revocation(self, mandate_id: str, reason: str, detail: str, moment: datetime) -> None:
        self.journal.append(
            {
                "type": "revoke",
                "mandate_id": mandate_id,
                "reason": reason,
                "detail": detail[:400],
                "ts": moment.isoformat(),
            }
        )

    def revoke_on_protected_write(self, detail: str) -> Mandate | None:
        """Un agent a tenté d'écrire dans la zone protégée (refus du frein) : le mandat tombe.

        Le frein appelle :func:`revoke_every_mandate` (le journal est commun à tous les projets).
        """
        return self.revoke(REVOKE_PROTECTED_WRITE, detail)

    def revoke_on_forged_verdict(self, task_id: str, detail: str) -> Mandate | None:
        """Branché au lot 3 (reviewer) : un verdict forgé ouvre le disjoncteur et révoque."""
        self.trip_breaker(task_id, BREAKER_FORGED_VERDICT, detail)
        return self.revoke(REVOKE_FORGED_VERDICT, f"{task_id} : {detail}")

    def revoke_on_write_outside_worktree(self, task_id: str, detail: str) -> Mandate | None:
        """Branché au lot 2 (worker) : une écriture hors du worktree de la tâche ouvre le disjoncteur et révoque."""
        self.trip_breaker(task_id, BREAKER_WRITE_OUTSIDE_WORKTREE, detail)
        return self.revoke(REVOKE_WRITE_OUTSIDE_WORKTREE, f"{task_id} : {detail}")

    # ── État ─────────────────────────────────────────────────────────────────

    def state(self, now: datetime | None = None) -> MandateState:
        """Le mandat du projet réévalué maintenant ; une révocation constatée ici (échéance, plafond) est inscrite une fois."""
        return self._state(now or _now(), auto_revoke=True)

    def _state(self, moment: datetime, *, auto_revoke: bool) -> MandateState:
        try:
            entries = self.journal.read()
        except JournalError as exc:
            return MandateState(None, JOURNAL_UNREADABLE, str(exc))
        grants = [e for e in entries if e.get("type") == "grant" and e.get("project") == self.project]
        if not grants:
            return MandateState(None, NO_MANDATE)
        mandate = _mandate_of(grants[-1])
        if mandate is None:
            return MandateState(None, MALFORMED, "mandat illisible")
        revocation = next(
            (e for e in reversed(entries) if e.get("type") == "revoke" and e.get("mandate_id") == mandate.id), None
        )
        sessions = _sessions_on(entries, mandate.id, moment)
        if revocation is not None:
            return MandateState(
                mandate,
                REVOKED,
                f"{revocation.get('reason', '')} : {revocation.get('detail', '')}".strip(" :"),
                sessions,
            )
        until = _parse(mandate.until)
        verdict: tuple[str, str] | None = None
        if until is None:
            verdict = (EXPIRED, "échéance illisible")
        elif moment >= until:
            verdict = (EXPIRED, f"échéance {until.isoformat()}")
        elif sessions > mandate.max_sessions_per_day:
            verdict = (
                CAP_EXCEEDED,
                f"{sessions} sessions aujourd'hui pour un plafond de {mandate.max_sessions_per_day}",
            )
        if verdict is None:
            return MandateState(mandate, "", "", sessions)
        if auto_revoke:
            self._write_revocation(mandate.id, verdict[0], verdict[1], moment)
        return MandateState(mandate, verdict[0], verdict[1], sessions)

    # ── Sessions (plafond, provenance du reçu) ───────────────────────────────

    def register_session(self, session_id: str, role: str, task_id: str = "", now: datetime | None = None) -> None:
        """Inscrit une session lancée par le kit. Un worker au-delà du plafond du jour est refusé et révoque le mandat.

        Le lanceur du lot 2 y inscrit chaque worker ; le reviewer du lot 3 y inscrit sa session, ce qui
        l'atteste (verrou 7). Hors mandat actif, rien ne s'inscrit : l'attestation n'existe que sous mandat.
        """
        if role not in _ROLES or not session_id.strip():
            raise MandateRefusedError(MALFORMED, f"session invalide : rôle {role!r}, identifiant {session_id!r}")
        moment = now or _now()
        state = self.state(moment)
        if not state.active or state.mandate is None:
            raise MandateRefusedError(
                state.reason or NO_MANDATE, f"session non inscrite : aucun mandat actif ({state.reason or NO_MANDATE})"
            )
        if role == "worker" and state.sessions_today >= state.mandate.max_sessions_per_day:
            self._write_revocation(
                state.mandate.id,
                CAP_EXCEEDED,
                f"plafond de {state.mandate.max_sessions_per_day} sessions par jour",
                moment,
            )
            raise MandateRefusedError(
                CAP_EXCEEDED,
                f"plafond de {state.mandate.max_sessions_per_day} sessions par jour atteint : mandat révoqué",
            )
        self.journal.append(
            {
                "type": "session",
                "mandate_id": state.mandate.id,
                "session_id": session_id.strip(),
                "role": role,
                "task_id": task_id,
                "ts": moment.isoformat(),
            }
        )

    def sessions(self, role: str = "", task_id: str = "") -> list[dict[str, Any]]:
        """Les sessions inscrites pour ce projet (tous mandats confondus), filtrées par rôle et par tâche."""
        entries = self.journal.read()
        mine = {e["mandate_id"] for e in entries if e.get("type") == "grant" and e.get("project") == self.project}
        return [
            e
            for e in entries
            if e.get("type") == "session"
            and e.get("mandate_id") in mine
            and (not role or e.get("role") == role)
            and (not task_id or e.get("task_id") == task_id)
        ]

    # ── Autorisation d'un geste ──────────────────────────────────────────────

    def authorize(
        self,
        gesture: str,
        task: MissionTask | None,
        provenance: Mapping[str, Any] | None,
        now: datetime | None = None,
        *,
        record: bool = True,
    ) -> Authorization:
        """Autorise *gesture* sur *task* sous le mandat, et le journalise ; lève :class:`MandateRefusedError` sinon.

        Avec ``record=False`` le geste n'est pas inscrit : l'appelant qui a encore une condition à vérifier
        (le reçu de revue) l'inscrit par :meth:`record_gesture` une fois tout passé, pour que le journal ne
        dise pas « pris » d'un geste refusé ensuite.

        Chaque refus nomme sa raison. Une garde qui ne peut pas lire le mandat ou le journal refuse.
        """
        moment = now or _now()
        task_id = task.id if task is not None else ""
        if gesture in NEVER_COVERED:
            raise MandateRefusedError("never_covered", f"{gesture} n'est jamais couvert par un mandat : geste d'humain")
        if gesture not in GESTURES:
            raise MandateRefusedError("unknown_gesture", f"geste inconnu : {gesture!r}")
        state = self.state(moment)
        if not state.active or state.mandate is None:
            raise MandateRefusedError(state.reason or NO_MANDATE, _inactive_text(gesture, state))
        mandate = state.mandate
        policy = load_escalation(self.root)
        if policy.sha256 != mandate.escalation_sha256:
            self._write_revocation(
                mandate.id, POLICY_CHANGED, "la politique d'escalade a changé depuis l'octroi", moment
            )
            raise MandateRefusedError(
                POLICY_CHANGED,
                f"{gesture} refusé : la politique d'escalade a changé depuis l'octroi du mandat {mandate.id} — mandat révoqué, geste d'humain",
            )
        if gesture not in mandate.gestures:
            raise MandateRefusedError(
                "gesture_not_covered", f"{gesture} refusé : le mandat {mandate.id} ne couvre pas ce geste"
            )
        if task is not None and task.type.value not in mandate.task_types:
            raise MandateRefusedError(
                "task_type_not_covered",
                f"{gesture} refusé sur {task.id} : le mandat {mandate.id} ne couvre pas le type {task.type.value}",
            )
        wanted = set(task_categories(task)) if task is not None else set()
        if gesture in _GESTURE_CATEGORY:
            wanted.add(_GESTURE_CATEGORY[gesture])
        kept = sorted(c for c in wanted if not policy.delegates(c))
        if kept:
            why = f" ({policy.error})" if policy.error else ""
            raise MandateRefusedError(
                "escalation",
                f"{gesture} refusé{f' sur {task_id}' if task_id else ''} : la politique du projet garde {', '.join(kept)} à l'humain{why}",
            )
        auth = Authorization(mandate.id, gesture, task_id, _provenance(provenance))
        if record:
            self.record_gesture(auth, moment)
        return auth

    def record_gesture(self, auth: Authorization, now: datetime | None = None) -> None:
        """Inscrit un geste pris sous mandat : ``origin: orchestrator``, le mandat, la provenance."""
        self.journal.append(
            {
                "type": "gesture",
                "mandate_id": auth.mandate_id,
                "gesture": auth.gesture,
                "task_id": auth.task_id,
                "origin": ORIGIN_ORCHESTRATOR,
                "provenance": dict(auth.provenance),
                "ts": (now or _now()).isoformat(),
            }
        )

    # ── Verrou 7 : le reçu de revue sous mandat ──────────────────────────────

    def check_receipt_provenance(self, task: MissionTask, receipt: Any) -> str:
        """Rend la session du relecteur si le reçu vaut sous mandat, lève sinon.

        Sous mandat, un reçu n'est valable que si son relecteur diffère de l'auteur (le worker : l'acteur
        qui tient la tâche, ses sessions inscrites) et si sa provenance est attestée : l'identifiant de
        session qu'il porte (``reviewer_session``) figure au journal d'autorité en rôle ``reviewer`` pour
        cette tâche, et n'est celui d'aucun worker.
        """
        reviewer = str(getattr(receipt, "reviewer_id", "") or "").strip()
        session = str(getattr(receipt, "reviewer_session", "") or "").strip()
        authors = {
            a
            for a in (
                task.owner,
                task.claim.actor_id if task.claim else "",
                task.claim.session_id if task.claim else "",
            )
            if a
        }
        try:
            registered = self.sessions(task_id=task.id)
        except JournalError as exc:
            raise MandateRefusedError(JOURNAL_UNREADABLE, f"reçu refusé : {exc}") from exc
        workers = {str(e["session_id"]) for e in registered if e.get("role") == "worker"}
        reviewers = {str(e["session_id"]) for e in registered if e.get("role") == "reviewer"}
        if not reviewer or reviewer in authors or reviewer.removeprefix("agent:") in authors:
            raise MandateRefusedError(
                "receipt_self_review",
                f"reçu refusé sur {task.id} : le relecteur {reviewer!r} est l'auteur — une relecture est faite par un autre",
            )
        if not session:
            raise MandateRefusedError(
                "receipt_unattested",
                f"reçu refusé sur {task.id} : aucune session de relecteur déclarée (`reviewer_session`)",
            )
        if session in workers or session in authors:
            raise MandateRefusedError(
                "receipt_self_review", f"reçu refusé sur {task.id} : la session {session!r} est celle du worker"
            )
        if session not in reviewers:
            raise MandateRefusedError(
                "receipt_unattested",
                f"reçu refusé sur {task.id} : la session {session!r} ne figure pas au journal d'autorité comme relecteur de cette tâche",
            )
        return session

    # ── Verrou 9 : checks scellés à la création ──────────────────────────────

    def seal_checks(self, task_id: str, checks: Sequence[Any], created_by: str = "") -> str:
        """Scelle l'empreinte des ``--check`` de *task_id* à sa création ; une fois (le premier scellé fait foi)."""
        fingerprint = _checks_fingerprint(checks)
        entries = self.journal.read()
        if any(
            e.get("type") == "seal" and e.get("project") == self.project and e.get("task_id") == task_id
            for e in entries
        ):
            return fingerprint
        self.journal.append(
            {
                "type": "seal",
                "project": self.project,
                "task_id": task_id,
                "fingerprint": fingerprint,
                "created_by": created_by,
                "ts": _now().isoformat(),
            }
        )
        return fingerprint

    def verify_seal(self, task_id: str, checks: Sequence[Any]) -> None:
        """Lève si les checks de *task_id* ne sont pas ceux scellés ; l'écart ouvre le disjoncteur (lot 2)."""
        try:
            entries = self.journal.read()
        except JournalError as exc:
            raise MandateRefusedError(JOURNAL_UNREADABLE, f"checks non approuvables sous mandat : {exc}") from exc
        seal = next(
            (
                e
                for e in entries
                if e.get("type") == "seal" and e.get("project") == self.project and e.get("task_id") == task_id
            ),
            None,
        )
        if seal is None:
            raise MandateRefusedError(
                "seal_missing", f"checks de {task_id} non scellés à la création : approbation réservée à l'humain"
            )
        current = _checks_fingerprint(checks)
        if current != seal.get("fingerprint"):
            self.trip_breaker(
                task_id,
                BREAKER_SEAL_MISMATCH,
                f"empreinte scellée {str(seal.get('fingerprint'))[:12]}, présente {current[:12]}",
            )
            raise MandateRefusedError(
                "seal_broken",
                f"checks de {task_id} modifiés depuis leur création : non approuvables sous mandat, disjoncteur ouvert",
            )

    # ── Disjoncteur ──────────────────────────────────────────────────────────

    def trip_breaker(self, task_id: str, kind: str, detail: str) -> dict[str, Any]:
        """Ouvre un événement « disjoncteur » que le lot 2 consomme (:meth:`breaker_events`)."""
        return self.journal.append(
            {
                "type": "breaker",
                "project": self.project,
                "task_id": task_id,
                "kind": kind,
                "detail": detail[:400],
                "ts": _now().isoformat(),
            }
        )

    def breaker_events(self, task_id: str = "") -> list[dict[str, Any]]:
        return [
            e
            for e in self.journal.read()
            if e.get("type") == "breaker"
            and e.get("project") == self.project
            and (not task_id or e.get("task_id") == task_id)
        ]

    def gestures_taken(self, task_id: str = "") -> list[dict[str, Any]]:
        """Les gestes pris sous mandat sur ce projet, avec ``origin: orchestrator``."""
        entries = self.journal.read()
        mine = {e["mandate_id"] for e in entries if e.get("type") == "grant" and e.get("project") == self.project}
        return [
            e
            for e in entries
            if e.get("type") == "gesture"
            and e.get("mandate_id") in mine
            and (not task_id or e.get("task_id") == task_id)
        ]


def _mandate_of(entry: Mapping[str, Any]) -> Mandate | None:
    try:
        return Mandate(
            id=str(entry["mandate_id"]),
            project=str(entry["project"]),
            gestures=tuple(str(g) for g in entry["gestures"]),
            task_types=tuple(str(t) for t in entry["task_types"]),
            max_sessions_per_day=int(entry["max_sessions_per_day"]),
            until=str(entry["until"]),
            escalation_sha256=str(entry["escalation_sha256"]),
            granted_at=str(entry.get("ts", "")),
            via=str(entry.get("via", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _sessions_on(entries: Sequence[Mapping[str, Any]], mandate_id: str, moment: datetime) -> int:
    today = moment.astimezone(UTC).date()
    count = 0
    for e in entries:
        if e.get("type") == "session" and e.get("mandate_id") == mandate_id and e.get("role") == "worker":
            when = _parse(e.get("ts"))
            if when is not None and when.astimezone(UTC).date() == today:
                count += 1
    return count


def _checks_fingerprint(checks: Sequence[Any]) -> str:
    from grimoire.missions.check_gate import CheckSpec

    specs = [CheckSpec(c.cmd, c.expect_exit, c.expect_stdout_contains, c.timeout_s) for c in checks]
    return hashlib.sha256("\n".join(sorted({s.sha256 for s in specs})).encode("ascii")).hexdigest()


def _inactive_text(gesture: str, state: MandateState) -> str:
    if state.reason == NO_MANDATE:
        return f"{gesture} refusé : aucun mandat n'a été accordé à l'orchestrateur sur ce projet — geste d'humain"
    return f"{gesture} refusé : mandat inactif ({state.reason}{' — ' + state.detail if state.detail else ''}) — geste d'humain"


# ── Entrée commune des gestes ────────────────────────────────────────────────

_MARKER = re.compile(r"^(rule|verdict|receipt):(.+)$")


def gesture_origin(human_origin: str, env: Mapping[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Le canal d'un geste CLI : ``(origine, provenance)``.

    *human_origin* est ce que :func:`grimoire.cli.cmd_task._comment_origin` a établi. Un humain établi
    reste humain. Sinon, ``GRIMOIRE_PRINCIPAL=orchestrator`` déclare le principal orchestrateur (la
    provenance se lit dans ``GRIMOIRE_PROVENANCE``, ``<kind>:<ref>``) ; le reste garde son origine.
    Déclarer le principal ne donne rien sans mandat : :func:`require_authority` le vérifie.
    """
    environ = os.environ if env is None else env
    if human_origin == ORIGIN_HUMAN:
        return human_origin, {}
    if str(environ.get(PRINCIPAL_ENV, "")).strip().lower() != ORIGIN_ORCHESTRATOR:
        return human_origin, {}
    found = _MARKER.match(str(environ.get(PROVENANCE_ENV, "")).strip())
    return ORIGIN_ORCHESTRATOR, ({"kind": found.group(1), "ref": found.group(2).strip()} if found else {})


def require_authority(
    project_root: Path,
    gesture: str,
    task: MissionTask | None,
    origin: str,
    *,
    what: str,
    provenance: Mapping[str, Any] | None = None,
    record: bool = True,
) -> Authorization | None:
    """Le contrôle d'origine d'un geste : ``None`` pour l'humain, une :class:`Authorization` sous mandat, sinon un refus.

    Remplace ``if origin != ORIGIN_HUMAN: raise`` : sans mandat valide qui couvre le geste et le type de
    tâche, l'origine ``orchestrator`` est refusée comme toute autre origine non humaine. Une garde en
    erreur refuse (échec fermé).
    """
    if origin == ORIGIN_HUMAN:
        return None
    if origin != ORIGIN_ORCHESTRATOR:
        raise GrimoireMissionError(
            f"{what} : origine {origin!r}. Seul le canal humain (cockpit, CLI interactive), ou l'orchestrateur "
            "sous un mandat qui couvre ce geste, valide — un agent ne valide pas son propre travail"
        )
    try:
        store = MandateStore(project_root)
        _require_orchestrator_session(store)
        return store.authorize(gesture, task, provenance, record=record)
    except MandateRefusedError:
        raise
    except Exception as exc:  # une garde qui échoue ne laisse pas passer
        raise MandateRefusedError(
            "guard_error", f"{what} : le mandat n'a pas pu être vérifié ({type(exc).__name__}: {exc})"
        ) from exc


def _require_orchestrator_session(store: MandateStore) -> None:
    """Si le lanceur a inscrit des sessions d'orchestrateur, l'appelant doit en être une."""
    from grimoire.core.standard_state import current_session

    registered = {str(e["session_id"]) for e in store.sessions(role="orchestrator")}
    if registered and current_session()[0] not in registered:
        raise MandateRefusedError(
            "principal_unattested",
            "le principal orchestrator n'est pas attesté : la session appelante n'est pas celle que le kit a lancée",
        )


def revoke_every_mandate(reason: str, detail: str, journal: AuthorityJournal | None = None) -> list[str]:
    """Révoque chaque mandat actif du poste, tous projets confondus ; rend leurs identifiants.

    Pour un événement qui touche la zone protégée elle-même (une écriture d'agent refusée sous
    ``~/.grimoire/control/``) : le journal est commun à tous les projets, la confiance qu'il porte
    tombe pour tous. Un journal illisible ne se prolonge pas : il ne porte déjà aucun mandat.
    """
    store_journal = journal or AuthorityJournal()
    hit: list[str] = []
    try:
        entries = store_journal.read()
    except JournalError:
        return hit
    projects = {str(e["project"]) for e in entries if e.get("type") == "grant" and e.get("project")}
    for project in sorted(projects):
        revoked = MandateStore(Path(project), store_journal).revoke(reason, detail)
        if revoked is not None:
            hit.append(revoked.id)
    return hit
