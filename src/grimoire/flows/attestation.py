"""Décision humaine attestée sur un node ``kind: "checkpoint"`` (W2-01).

Avant ce module, :func:`grimoire.flows.engine.checkpoint_decision_faults` ne
lisait que la *valeur* de ``checkpoint_decision`` : n'importe quel JSON
portant ``"approve"`` franchissait un checkpoint, qu'un humain l'ait décidé
ou que l'agent l'ait écrit seul. Chaque décision est désormais classée :

- ``attested`` — la session qui a présenté le checkpoint (son *challenge*)
  est celle qui décide, et le journal des tours humains
  (:mod:`grimoire.hosts.decisions.human_turns`) porte un tour ``user`` de
  cette même session, postérieur au challenge ;
- ``unattested`` — tout le reste, avec le motif nommé : CLI nue sans
  session, autre session (une sous-session ``claude -p`` a son propre
  ``session_id``), ``GRIMOIRE_UNATTENDED=1``, aucun tour depuis le
  challenge, ou aucun hook de prompt branché (``human_authority_unprovable``).

Décision 2 du plan : un checkpoint déclaré destructif
(``config.checkpoint.destructive: true``) refuse ``unattested`` ; un
checkpoint ordinaire l'accepte avec un avertissement nommé, pendant une
release, avant de le refuser à son tour.

Le journal ``<flows>/authority/<run>.authority.jsonl`` porte les challenges
et les décisions (motif d'``amend`` compris, relu par ``flow status``). Un
challenge reste ouvert jusqu'à la décision qui le consomme : un tour humain
ne sert qu'une décision, et un refus faute d'attestation ne le consomme pas.
Python seul : aucune de ces décisions ne traverse ``grimoire_flows_core``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.fsio import locked
from grimoire.core.standard_state import current_session
from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX, LIMIT_NOTE
from grimoire.hosts.decisions.human_turns import ORIGIN_USER, is_unattended, load_turns

__all__ = [
    "ATTESTED",
    "NO_HUMAN_TURN",
    "UNATTESTED",
    "Attestation",
    "CheckpointGate",
    "CheckpointJournal",
    "attest",
    "destructive_checkpoints",
    "judge_checkpoint",
    "refusal_text",
    "warning_text",
]

ATTESTED = "attested"
UNATTESTED = "unattested"

NO_HUMAN_TURN = "no_human_turn_since_challenge"
_REASONS: dict[str, str] = {
    "unattended": "GRIMOIRE_UNATTENDED=1 : exécution déclarée sans humain",
    "no_challenge": "aucun challenge émis pour ce checkpoint",
    "no_session": "checkpoint présenté hors session d'hôte (CLI nue) : aucune session à lier",
    "other_session": "la décision vient d'une autre session que celle qui a présenté le checkpoint",
    "human_authority_unprovable": "aucun hook de prompt n'a jamais écrit le journal des tours",
    NO_HUMAN_TURN: "aucun tour humain de cette session depuis la présentation du checkpoint",
}

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass(frozen=True, slots=True)
class Attestation:
    """Le classement d'une décision, et pourquoi."""

    status: str
    reason: str = ""
    challenge_id: str = ""
    session_id: str = ""
    turn_ts: str = ""

    @property
    def attested(self) -> bool:
        return self.status == ATTESTED

    def explain(self) -> str:
        return _REASONS.get(self.reason, self.reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "challenge_id": self.challenge_id,
            "session_id": self.session_id,
            "turn_ts": self.turn_ts,
        }


def refusal_text(attestation: Attestation) -> str:
    """Le défaut d'un checkpoint destructif refusé — ``awaiting_human``, la cause, le remède, la limite."""
    return (
        f"awaiting_human : {attestation.reason} — {attestation.explain()}. "
        "Checkpoint destructif : la décision doit être attestée (un tour humain de la session qui l'a "
        "présenté, postérieur au challenge, puis la même décision renvoyée depuis cette session). "
        f"{LIMIT_NOTE} Le jeton cockpit (W1-10) sera le canal le plus fort."
    )


def warning_text(attestation: Attestation) -> str:
    return (
        f"décision unattested ({attestation.reason} — {attestation.explain()}) : acceptée sur ce checkpoint "
        "non destructif, refusée à partir de la prochaine release."
    )


def destructive_checkpoints(blueprint: Mapping[str, Any]) -> frozenset[str]:
    """Les nodes ``checkpoint`` que le blueprint déclare destructifs (``config.checkpoint.destructive``)."""
    out: set[str] = set()
    for node in blueprint.get("nodes") or []:
        if not isinstance(node, dict) or node.get("kind") != "checkpoint":
            continue
        config = (node.get("config") or {}).get("checkpoint") or {}
        if isinstance(config, dict) and config.get("destructive") is True:
            out.add(str(node.get("id")))
    return frozenset(out)


class CheckpointJournal:
    """Challenges et décisions de checkpoint d'un run — ajout seul, sous verrou."""

    def __init__(self, flows_root: Path, run_id: str) -> None:
        safe = run_id if _SAFE_ID.match(run_id) else "h-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
        self.path = flows_root / "authority" / f"{safe}{AUTHORITY_SUFFIX}"

    def entries(self) -> list[dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        out: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    def _append(self, entry: dict[str, Any]) -> None:
        with locked(self.path), self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def open_challenge(self, node_id: str) -> dict[str, Any] | None:
        """Le dernier challenge de *node_id* qu'aucune décision n'a encore consommé."""
        entries = self.entries()
        consumed = {e.get("challenge_id") for e in entries if e.get("type") == "decision"}
        for entry in reversed(entries):
            if entry.get("type") == "challenge" and entry.get("node_id") == node_id:
                return None if entry.get("challenge_id") in consumed else entry
        return None

    def issue_challenge(self, node_id: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Émet le challenge de *node_id*, lié à la session courante — ou rend celui encore ouvert."""
        existing = self.open_challenge(node_id)
        if existing is not None:
            return existing
        session_id, _host = current_session(env)
        entry = {
            "type": "challenge",
            "challenge_id": f"CHL-{uuid.uuid4().hex[:12]}",
            "node_id": node_id,
            "session_id": session_id,
            "ts": _now(),
        }
        self._append(entry)
        return entry

    def record_decision(self, node_id: str, decision: str, reason: str, attestation: Attestation) -> None:
        self._append(
            {
                "type": "decision",
                "challenge_id": attestation.challenge_id,
                "node_id": node_id,
                "decision": decision,
                "reason": reason,
                "attestation": attestation.status,
                "attestation_reason": attestation.reason,
                "session_id": attestation.session_id,
                "turn_ts": attestation.turn_ts,
                "ts": _now(),
            }
        )

    def decisions(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {k: e.get(k, "") for k in ("node_id", "decision", "reason", "attestation", "attestation_reason", "ts")}
            for e in self.entries()
            if e.get("type") == "decision"
        )


def attest(project_root: Path, challenge: Mapping[str, Any] | None, env: Mapping[str, str] | None = None) -> Attestation:
    """Classe la décision soumise maintenant contre *challenge*."""
    decider, _host = current_session(env)
    challenge_id = str((challenge or {}).get("challenge_id", ""))

    def _no(reason: str) -> Attestation:
        return Attestation(UNATTESTED, reason, challenge_id, decider)

    if is_unattended(env):
        return _no("unattended")
    if challenge is None:
        return _no("no_challenge")
    owner = str(challenge.get("session_id", ""))
    if not owner:
        return _no("no_session")
    if decider != owner:
        return _no("other_session")
    turns = load_turns(project_root)
    if turns is None:
        return _no("human_authority_unprovable")
    since = _parse(str(challenge.get("ts", "")))
    if since is None:
        return _no("no_challenge")
    for turn in reversed(turns):
        when = _parse(turn.ts)
        if turn.session_id == owner and turn.origin == ORIGIN_USER and when is not None and when > since:
            return Attestation(ATTESTED, "", challenge_id, decider, turn.ts)
    return _no(NO_HUMAN_TURN)


#: Les décisions qui font avancer un run : seules elles exigent une attestation.
_ADVANCING = ("approve", "amend")


@dataclass(frozen=True, slots=True)
class CheckpointGate:
    """Ce que la décision soumise vaut : classement, refus éventuel, avertissement éventuel."""

    decision: str
    reason: str
    attestation: Attestation
    refusal: str = ""
    warning: str = ""


def judge_checkpoint(
    journal: CheckpointJournal,
    project_root: Path,
    node_id: str,
    output: Mapping[str, Any],
    *,
    destructive: bool,
    env: Mapping[str, str] | None = None,
) -> CheckpointGate | None:
    """Classe la décision de *output* ; ``None`` si elle n'est pas une décision valide (déjà un défaut nommé).

    ``reject`` est classé mais jamais refusé : il ne fait pas avancer le run.
    ``approve``/``amend`` non attestés sont refusés sur un checkpoint
    destructif (``refusal``), acceptés avec ``warning`` ailleurs.
    """
    decision = output.get("checkpoint_decision")
    if decision not in ("approve", "reject", "amend"):
        return None
    reason = str(output.get("checkpoint_reason") or "")
    attestation = attest(project_root, journal.open_challenge(node_id), env)
    if decision not in _ADVANCING or attestation.attested:
        return CheckpointGate(str(decision), reason, attestation)
    if destructive:
        return CheckpointGate(str(decision), reason, attestation, refusal=refusal_text(attestation))
    return CheckpointGate(str(decision), reason, attestation, warning=warning_text(attestation))
