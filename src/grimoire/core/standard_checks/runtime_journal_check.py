"""Vérificateur du pattern ``runtime-journal`` (issue #636).

Le pattern promet « context, decision, hook, gate, and score events for
auditability » ; jusqu'ici aucun check ne le portait (``checks: []`` dans
``capability-map.yaml``), et seul ``_score_dimensions`` jetait un œil au
journal, sans identifiant de check ni message. Trois contrôles, préfixe
``journal.`` (réservé à la dimension ``runtime_journal`` dans
``registry.DIMENSION_CHECK_PREFIXES``) :

- ``journal.missing`` : aucun journal sur disque. Avertissement par défaut
  (un projet neuf n'a rien journalisé) ; erreur en ``governed``/``production``
  dès que le Mission Ledger porte un claim — du travail a eu lieu, rien ne
  l'atteste ;
- ``journal.line_invalid`` : une ligne qui n'est pas un événement (JSON
  illisible, ou sans ``event_type``/``timestamp``). Erreur : un journal
  corrompu n'est pas auditable ;
- ``journal.stale`` : le claim le plus récent du ledger est plus jeune que le
  dernier événement journalisé, au-delà d'une marge. Avertissement : rien
  n'a été journalisé depuis que quelqu'un a pris une tâche.

La marge (:data:`STALE_GRACE_S`) absorbe l'ordre réel des écritures — la porte
est vérifiée puis le claim écrit, quelques millisecondes plus tard — sans
quoi tout claim serait « stale » par construction. Elle n'est pas mesurée ;
c'est un seuil de départ, documenté comme tel.

Ce module reparse le journal plutôt que d'importer
``agentic_standard.audit_runtime_events`` : ``agentic_standard`` importe les
vérificateurs, l'inverse ferait un cycle.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from grimoire.core.standard_checks.base import StandardProfile, StandardVerificationResult, _add_check

__all__ = ["JOURNAL_PATH", "STALE_GRACE_S", "_verify_runtime_journal"]

#: Même chemin que ``agentic_standard.EVENT_JOURNAL_FILE`` (non importable d'ici, voir docstring).
JOURNAL_PATH = Path("_grimoire-output/events/runtime-journal.jsonl")
#: Chemin du Mission Ledger, même valeur que ``standard_state.LEDGER_RELPATH``.
LEDGER_EVENTS_PATH = Path("_grimoire-runtime-output/ledger/events.jsonl")
#: Événement du ledger qui matérialise un claim (``missions.ledger.SESSION_ATTACHED_EVENT``).
CLAIM_EVENT = "task.session_attached"
#: Marge entre le dernier claim et le dernier événement journalisé avant de parler de retard.
STALE_GRACE_S = 300


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:  # silent-ok — horodatage illisible = pas de date, l'appelant le traite comme absent
        return None
    return parsed if parsed.tzinfo is not None else None


def _newest_claim(root: Path) -> datetime | None:
    """Horodatage du claim le plus récent du ledger, ou ``None`` sans ledger ni claim.

    Lecture ligne à ligne, sans rejouer le ledger : une ligne illisible est
    ignorée ici (``MissionLedger`` la signale déjà de son côté).
    """
    path = root / LEDGER_EVENTS_PATH
    if not path.is_file():
        return None
    newest: datetime | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if CLAIM_EVENT not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:  # silent-ok — ligne du ledger illisible, déjà signalée par MissionLedger
            continue
        if not isinstance(event, dict) or event.get("event_type") != CLAIM_EVENT:
            continue
        stamp = _parse_ts(event.get("created_at"))
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    return newest


def _verify_runtime_journal(root: Path, profile: StandardProfile, result: StandardVerificationResult) -> None:
    strict = profile.id in {"governed", "production"}
    journal = root / JOURNAL_PATH
    newest_claim = _newest_claim(root)
    if not journal.is_file():
        claimed = newest_claim is not None
        _add_check(
            result,
            "journal.missing",
            "error" if strict and claimed else "warning",
            (
                "Runtime journal is absent although the mission ledger records a claim: "
                "work happened and nothing attests it."
                if claimed
                else "Runtime journal is absent: no context, decision, hook, gate or score event has been recorded yet."
            ),
            path=JOURNAL_PATH,
        )
        return

    invalid: list[int] = []
    newest_event: datetime | None = None
    for line_no, line in enumerate(journal.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid.append(line_no)
            continue
        if not isinstance(event, dict) or not event.get("event_type") or not event.get("timestamp"):
            invalid.append(line_no)
            continue
        stamp = _parse_ts(event.get("timestamp"))
        if stamp is not None and (newest_event is None or stamp > newest_event):
            newest_event = stamp
    if invalid:
        shown = ", ".join(str(n) for n in invalid[:10]) + (", …" if len(invalid) > 10 else "")
        _add_check(
            result,
            "journal.line_invalid",
            "error",
            f"Runtime journal has {len(invalid)} line(s) that are not events (line {shown}): the journal is not auditable.",
            path=JOURNAL_PATH,
        )
    if newest_claim is not None and (
        newest_event is None or (newest_claim - newest_event).total_seconds() > STALE_GRACE_S
    ):
        since = newest_event.isoformat() if newest_event else "never"
        _add_check(
            result,
            "journal.stale",
            "warning",
            f"Runtime journal is stale: last claim at {newest_claim.isoformat()}, last journaled event {since}.",
            path=JOURNAL_PATH,
        )
