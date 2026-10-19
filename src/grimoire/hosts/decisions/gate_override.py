"""Contournement tapé d'un refus de clôture : apparié, journalisé avant effet, expirant (W2-06).

Avant ce module, le seul moyen de clore une tâche gouvernée aux gates rouges
était d'attendre que la garde ``Stop`` se relâche d'elle-même (plafond et
signature de W2-02) : aucun contournement décidé par l'humain, donc aucune
trace de qui l'a décidé ni pourquoi.

L'humain tape, comme message entier, ``Override Gate <task_id>: <raison>``.

- **Tapé.** La phrase n'est reçue que par ``UserPromptSubmit``
  (:func:`record_override_request`, appelé par :mod:`.task_context`), d'un
  hôte nommé, hors ``GRIMOIRE_UNATTENDED=1`` (même origine ``user`` que le
  journal des tours de W2-01, :func:`.human_turns.turn_origin`), et seulement
  depuis la session à laquelle le ledger rattache le claim de la tâche — le
  contrôle de ``session_id`` de W2-01 : une sous-session ``claude -p`` a un
  autre ``session_id`` et ne tient pas le claim.
- **Journalisé avant effet.** La ligne ``policy.override`` est écrite avant
  que quoi que ce soit ne change ; si l'écriture échoue, il n'y a pas de
  contournement. Au ``Stop`` (:func:`apply_override`), la ligne appariée
  ``policy.override_consumed`` (même ``override_id``) est écrite *avant* de
  lever le refus ; si elle échoue, le refus reste.
- **Apparié.** Un contournement ne lève qu'un refus : sa moitié consommée le
  referme. Taper la phrase de nouveau en ouvre un autre.
- **Expirant.** Valable tant que le claim de la tâche est celui sous lequel il
  a été tapé : même session, même ``lease_epoch`` (K2), bail vivant, tâche
  ``claimed``/``running``. Un claim rendu, repris (nouvel epoch) ou expiré, un
  ``Stop`` d'une autre session : le refus revient.

Le journal ``<runs>/evidence/<task>/gate-overrides.authority.jsonl`` porte le
suffixe que :mod:`.authority_guard` protège : Write, Edit ou Bash qui l'écrit
sont refusés quel que soit le profil. ``grimoire standard verify`` liste les
contournements consommés, raison et horodatage (:func:`describe_consumed_overrides`).

Limite (principe 7) : un agent qui exécute du code arbitraire peut écrire ce
journal par une voie que la garde de chemin ne prévoit pas ; la trace rend le
contournement visible et coûteux à falsifier, pas impossible.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.events import HookEvent

__all__ = [
    "CONSUMED_EVENT",
    "OVERRIDES_FILENAME",
    "OVERRIDE_EVENT",
    "apply_override",
    "describe_consumed_overrides",
    "overrides_relpath",
    "read_overrides",
    "record_override_request",
]

OVERRIDE_EVENT = "policy.override"
CONSUMED_EVENT = "policy.override_consumed"
#: Suffixe ``.authority.jsonl`` : celui que :data:`.authority_guard.AUTHORITY_SUFFIX`
#: protège (recopié pour que ``UserPromptSubmit`` n'importe pas la garde ; un test l'épingle).
OVERRIDES_FILENAME = "gate-overrides.authority.jsonl"

#: La phrase, message entier : un texte collé qui la cite au milieu ne contourne rien.
_PHRASE = re.compile(r"\AOverride Gate ([^\s:]+)\s*:\s*(\S.*)\Z", re.DOTALL)
_REASON_MAX = 240
_LIMIT = (
    "Limite : un agent qui exécute du code arbitraire peut écrire ce journal par une voie non prévue ; "
    "la trace rend le contournement visible et coûteux à falsifier, pas impossible."
)


def overrides_relpath(task_id: str) -> Path:
    from grimoire.core.standard_generation import RUNS_DIR, normalize_task_id

    return RUNS_DIR / "evidence" / normalize_task_id(task_id) / OVERRIDES_FILENAME


def _now() -> str:
    return datetime.now(UTC).isoformat()


def read_overrides(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    """Les lignes du journal de *task_id* ; ``[]`` s'il est absent ou illisible (aucun contournement)."""
    try:
        raw = (project_root / overrides_relpath(task_id)).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # silent-ok — illisible = aucun contournement : le refus reste (fail-closed)
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except ValueError:  # silent-ok — ligne coupée : ignorée, elle n'ouvre rien
            continue
        if isinstance(data, dict) and data.get("type") in (OVERRIDE_EVENT, CONSUMED_EVENT):
            out.append(data)
    return out


def _append(project_root: Path, task_id: str, entry: dict[str, Any]) -> None:
    """Ajoute *entry* sous verrou. Lève en cas d'échec : l'appelant n'accorde rien sans la ligne."""
    from grimoire.core.fsio import locked

    path = project_root / overrides_relpath(task_id)
    with locked(path), path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _held_claim(project_root: Path, task_id: str) -> Any:
    """Le claim vivant de *task_id* (``claimed``/``running``, bail non expiré), ``None`` sinon ou sur erreur."""
    try:
        from grimoire.core.standard_state import LEDGER_RELPATH
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.schemas import TaskState

        events = project_root.resolve() / LEDGER_RELPATH / "events.jsonl"
        if not events.is_file():
            return None
        task = MissionLedger(events.parent).get_task(task_id)
    except Exception:  # silent-ok — ledger illisible : aucun claim, donc aucun contournement (fail-closed)
        return None
    if task is None or task.claim is None or task.status not in (TaskState.CLAIMED, TaskState.RUNNING):
        return None
    return None if task.claim.is_expired() else task.claim


def _refused(why: str) -> tuple[str, dict[str, Any]]:
    return f"[Grimoire] Contournement refusé : {why}. Aucun gate n'est levé.", {"override": {"status": "refused"}}


def record_override_request(hook: HookInput) -> tuple[str, dict[str, Any]]:
    """``UserPromptSubmit`` : journalise ``policy.override`` si le prompt est la phrase, de la bonne session.

    Rend ``(contexte, détail)`` ; ``("", {})`` quand le prompt n'est pas la phrase.
    """
    match = _PHRASE.match(hook.prompt.strip())
    if hook.event is not HookEvent.USER_PROMPT_SUBMIT or match is None:
        return "", {}
    from grimoire.hosts.decisions.human_turns import ORIGIN_USER, turn_origin

    raw_task, raw_reason = match.groups()
    if turn_origin(hook) != ORIGIN_USER or not hook.session_id.strip():
        return _refused("la phrase ne vient pas d'un tour humain transmis par un hôte nommé (ou GRIMOIRE_UNATTENDED=1)")
    from grimoire.core.standard_generation import normalize_task_id

    try:
        task_id = normalize_task_id(raw_task)
    except ValueError:
        return _refused(f"identifiant de tâche invalide {raw_task!r}")
    claim = _held_claim(hook.project_root, task_id)
    if claim is None or claim.session_id != hook.session_id:
        return _refused(
            f"la tâche {task_id} n'est pas tenue par cette session (claim vivant rattaché à ce session_id). "
            f"`grimoire task attach {task_id}` depuis cette session, puis retape la phrase"
        )
    entry = {
        "type": OVERRIDE_EVENT,
        "override_id": f"OVR-{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "session_id": hook.session_id,
        "lease_epoch": claim.lease_epoch,
        "reason": " ".join(raw_reason.split())[:_REASON_MAX],
        "prompt_sha256": hashlib.sha256(hook.prompt.encode("utf-8", "surrogatepass")).hexdigest(),
        "host": hook.host,
        "ts": _now(),
    }
    try:
        _append(hook.project_root, task_id, entry)
    except Exception as exc:
        return _refused(
            f"le journal {overrides_relpath(task_id).as_posix()} n'a pas pu être écrit ({type(exc).__name__})"
        )
    context = (
        f"[Grimoire] Contournement {entry['override_id']} enregistré pour {task_id} : le prochain refus de clôture "
        f"de cette session est levé, une fois, et tracé (raison : {entry['reason']}). Il expire avec le claim "
        f"(epoch {claim.lease_epoch}) ou la session. {_LIMIT}"
    )
    return context, {"override": {"status": "granted", "override_id": entry["override_id"], "task_id": task_id}}


def _expiry(grant: dict[str, Any], hook: HookInput, claim: Any) -> str:
    """``""`` si *grant* vaut encore pour ce ``Stop``, sinon pourquoi il a expiré."""
    if grant.get("session_id") != hook.session_id:
        return "other_session"
    if claim is None:
        return "task_released"
    if claim.session_id != grant.get("session_id") or claim.lease_epoch != grant.get("lease_epoch"):
        return "claim_changed"
    return ""


def apply_override(hook: HookInput, decision: Decision) -> Decision:
    """``Stop`` : lève *decision* (un ``BLOCK`` sur une tâche) si un contournement ouvert et valide existe.

    La ligne ``policy.override_consumed`` est écrite d'abord ; si elle échoue,
    le refus reste, avec la cause. Tout autre verdict est rendu tel quel.
    """
    task_id = str(decision.detail.get("task_id") or "")
    if decision.outcome is not Outcome.BLOCK or not task_id or task_id == "bootstrap":
        return decision
    from grimoire.core.fsio import locked

    try:
        path = hook.project_root / overrides_relpath(task_id)
    except ValueError:
        return decision
    if not path.is_file():
        return decision
    try:
        with locked(path):
            entries = read_overrides(hook.project_root, task_id)
            consumed = {e.get("override_id") for e in entries if e.get("type") == CONSUMED_EVENT}
            open_grants = [
                e for e in entries if e.get("type") == OVERRIDE_EVENT and e.get("override_id") not in consumed
            ]
            if not open_grants:
                return decision
            claim = _held_claim(hook.project_root, task_id)
            valid = [g for g in open_grants if not _expiry(g, hook, claim)]
            if not valid:
                last = open_grants[-1]
                note = (
                    f"\nContournement {last.get('override_id')} expiré ({_expiry(last, hook, claim)}) : "
                    f"retape `Override Gate {task_id}: <raison>` dans la session qui tient la tâche."
                )
                info = {"status": "expired", "override_id": last.get("override_id"), "why": _expiry(last, hook, claim)}
                return replace(decision, reason=decision.reason + note, detail={**decision.detail, "override": info})
            grant = valid[-1]
            entry = {
                "type": CONSUMED_EVENT,
                "override_id": grant.get("override_id"),
                "task_id": task_id,
                "session_id": hook.session_id,
                "reason": grant.get("reason", ""),
                "granted_ts": grant.get("ts", ""),
                "blocked_reason": decision.reason.splitlines()[0][:_REASON_MAX] if decision.reason else "",
                "missing": [str(m) for m in decision.detail.get("missing") or []],
                "ts": _now(),
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception as exc:
        from grimoire.core.standard_checks.evidence_journal import record_guard_error

        record_guard_error(hook.project_root, task_id, "gate_override", type(exc).__name__, str(exc))
        note = f"\nContournement non consommé : journal non inscriptible ({type(exc).__name__}), le refus reste."
        info = {"status": "unrecorded", "error": type(exc).__name__}
        return replace(decision, reason=decision.reason + note, detail={**decision.detail, "override": info})
    context = (
        f"[Grimoire] Contournement {entry['override_id']} consommé pour {task_id} : refus de clôture levé une fois "
        f"(raison : {entry['reason']}). Les gates restent rouges ; `grimoire standard verify --task-id {task_id}` "
        "le liste."
    )
    info = {"status": "consumed", "override_id": entry["override_id"], "reason": entry["reason"]}
    return Decision(outcome=Outcome.ALLOW, context=context, detail={**decision.detail, "override": info})


def describe_consumed_overrides(project_root: Path, task_id: str) -> str:
    """Le message ``policy.override_consumed`` de ``standard verify`` : chaque contournement, raison et horodatage."""
    try:
        consumed = [e for e in read_overrides(project_root, task_id) if e.get("type") == CONSUMED_EVENT]
    except ValueError:
        return ""
    if not consumed:
        return ""
    items = "; ".join(f"{e.get('override_id')} le {e.get('ts')} — « {e.get('reason')} »" for e in consumed)
    return (
        f"{len(consumed)} contournement(s) de gate consommé(s) pour {task_id} : {items}. "
        f"La tâche a été close gates rouges sur décision tapée ; journal : {overrides_relpath(task_id).as_posix()}."
    )
