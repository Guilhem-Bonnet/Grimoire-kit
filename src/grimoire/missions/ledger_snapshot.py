"""Instantané du rejeu du ledger (K13, épopée #705) — un cache dérivé du journal, pas une preuve.

Le journal ``events.jsonl`` est un journal d'audit : sa taille n'est pas bornée et ne
doit pas l'être. Ce qu'on borne, c'est le *coût du rejeu*. Chaque hook est un
processus neuf : sans cache, chacun relit et rejoue tout le journal (≈ 170 ms à
10 000 événements) alors que D21 accorde 100 ms aux hooks et 50 ms au PreToolUse.

Quand un rejeu dépasse :data:`SNAPSHOT_AFTER_MS`, il écrit ``ledger.snapshot.json`` à côté
du journal : l'état projeté (missions, tâches, epoch le plus haut, événements refusés, clés
``idem``), l'``offset`` en octets qu'il couvre et le ``sha256`` de ce préfixe. Un rejeu
suivant restaure l'état, vérifie le hash puis ne rejoue que la suite (rejeu incrémental).

Règles :

- **Un instantané n'est cru que s'il concorde avec le journal.** Schéma, version du kit,
  offset sur une fin de ligne et hash du préfixe doivent concorder ; la moindre divergence
  (journal réécrit, tronqué, instantané illisible ou d'une autre version) l'ignore et ramène
  au rejeu complet, qui fait foi. Le supprimer est sans danger.
- **Limite : le hash lie le préfixe du journal, pas l'état que l'instantané porte.** Tout ce
  que la vérification calcule vient de ``events.jsonl`` seul : un instantané réécrit à la main,
  au hash de préfixe valide, est restauré tel quel, sans aucune trace dans le journal d'audit
  (``test_the_snapshot_proves_the_journal_prefix_not_the_state_it_carries``). Rien ici ne
  l'authentifie. La défense est la garde d'autorité (:mod:`grimoire.hosts.decisions.authority_guard`,
  vecteur ``ledger-snapshot``) : aucune voie d'écriture d'outil reconnue vers ce fichier, quel que
  soit le profil, avec la limite de toute garde de chemin (du code arbitraire la contourne).
- **Les événements ne sont pas dans l'instantané.** Ils restent dans le journal et ne sont
  relus que si un appelant les demande (:func:`all_events`) ; le dernier horodatage, seul
  besoin de ``clock_state``, est porté à côté (:func:`latest_event_time`).
- **Jamais en lecture seule.** Aucun instantané n'est écrit sous
  :func:`~grimoire.core.standard_state.suspended_state_writes`, ni si le dernier événement
  est une ligne incomplète (l'offset ne tomberait pas sur une fin de ligne).
- **Rien tant que le budget tient.** Un ledger dont le rejeu complet reste sous le seuil
  n'écrit rien : c'est la décision de D21, ``instantané seulement si dépassé``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grimoire.core.fsio import atomic_write
from grimoire.missions.lease import parse_instant
from grimoire.missions.ledger_rules import parse_jsonl
from grimoire.missions.schemas import LedgerEvent, Mission, MissionTask

if TYPE_CHECKING:
    from datetime import datetime

    from grimoire.missions.ledger import MissionLedger

__all__ = [
    "SNAPSHOT_AFTER_MS",
    "SNAPSHOT_FILENAME",
    "Cover",
    "Replay",
    "all_events",
    "latest_event_time",
    "resume_from_snapshot",
    "save_if_slow",
]

logger = logging.getLogger(__name__)

SNAPSHOT_FILENAME = "ledger.snapshot.json"
#: Seuil au-delà duquel un rejeu écrit un instantané : la moitié du budget de 100 ms des hooks (D21).
SNAPSHOT_AFTER_MS = 50.0
_SCHEMA = "grimoire-ledger-snapshot/v1"


@dataclass(slots=True)
class Cover:
    """Ce que l'instantané restauré couvre : ``offset`` octets du journal, dont les événements restent à lire."""

    offset: int
    latest: datetime | None


@dataclass(slots=True)
class Replay:
    """Un rejeu en cours : les octets qui restent à rejouer, avec le numéro (base 1) de leur première ligne, le journal lu et l'instant de départ."""

    tail: bytes
    first_line: int
    data: bytes
    started: float
    #: K12 x K13 : les lignes illisibles que l'instantané couvre, que le rejeu de la suite ne relit pas.
    unreadable: tuple[int, ...] = ()


def _producer() -> str:
    from grimoire.__version__ import __version__

    return __version__


def _read_valid(ledger: MissionLedger, data: bytes) -> tuple[dict[str, Any], int] | None:
    """Le contenu de l'instantané et son offset s'il est prouvé par *data*, ``None`` sinon."""
    path = ledger._events_path.parent / SNAPSHOT_FILENAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        offset = raw["offset"]
        valid = (
            raw["$schema"] == _SCHEMA
            and raw["producer"] == _producer()
            and isinstance(offset, int)
            and not isinstance(offset, bool)
            and 0 < offset <= len(data)
            and data[offset - 1 : offset] == b"\n"
            and raw["prefix_sha256"] == hashlib.sha256(data[:offset]).hexdigest()
        )
        return (raw, offset) if valid else None
    except (OSError, ValueError, KeyError, TypeError):
        logger.debug("instantané du ledger illisible : rejeu complet", exc_info=True)
        return None


def _restore(ledger: MissionLedger, raw: dict[str, Any], offset: int) -> tuple[int, ...] | None:
    """Restaure l'état de *raw* dans *ledger* et rend ses lignes illisibles ; ``None`` (rien n'a été touché) s'il est inexploitable."""
    try:
        state = raw["state"]
        missions = {str(k): Mission.from_dict(v) for k, v in state["missions"].items()}
        tasks = {str(k): MissionTask.from_dict(v) for k, v in state["tasks"].items()}
        epoch_high = {str(k): int(v) for k, v in state["epoch_high"].items()}
        rejected = {str(k): str(v) for k, v in state["rejected"].items()}
        idem = {str(k) for k in state["idem_seen"]}
        unreadable = tuple(int(n) for n in state["unreadable"])
        latest = parse_instant(raw.get("latest_event_at"))
    except (ValueError, KeyError, TypeError, AttributeError):
        logger.debug("instantané du ledger inexploitable : rejeu complet", exc_info=True)
        return None
    ledger._missions, ledger._tasks, ledger._epoch_high = missions, tasks, epoch_high
    ledger._rejected, ledger._idem_seen = rejected, idem
    ledger._snapshot = Cover(offset, latest)
    return unreadable


def resume_from_snapshot(ledger: MissionLedger) -> Replay:
    """Lit le journal et restaure l'instantané s'il est prouvé ; le reste du texte est ce qu'il faut rejouer."""
    started = time.perf_counter()
    data = ledger._events_path.read_bytes()
    found = _read_valid(ledger, data)
    restored = _restore(ledger, *found) if found is not None else None
    if found is not None and restored is not None:
        return Replay(data[found[1] :], data.count(b"\n", 0, found[1]) + 1, data, started, restored)
    return Replay(data, 1, data, started)


def _latest(ledger: MissionLedger) -> datetime | None:
    stamps = [t for t in (parse_instant(e.created_at) for e in ledger._events) if t is not None]
    covered = ledger._snapshot.latest if ledger._snapshot is not None else None
    return max([*stamps, *([covered] if covered is not None else [])], default=None)


def latest_event_time(ledger: MissionLedger) -> datetime | None:
    """L'horodatage le plus récent du journal, sans relire les événements couverts par l'instantané."""
    return _latest(ledger)


def save_if_slow(ledger: MissionLedger, replay: Replay) -> None:
    """Écrit l'instantané si ce rejeu a dépassé le seuil ; sinon ne fait rien."""
    if (time.perf_counter() - replay.started) * 1000 <= SNAPSHOT_AFTER_MS:
        return
    from grimoire.core.standard_state import state_writes_allowed

    if not state_writes_allowed() or not replay.data.endswith(b"\n"):
        return
    latest = _latest(ledger)
    payload = {
        "$schema": _SCHEMA,
        "producer": _producer(),
        "offset": len(replay.data),
        "prefix_sha256": hashlib.sha256(replay.data).hexdigest(),
        "latest_event_at": latest.isoformat() if latest is not None else "",
        "state": {
            "missions": {k: m.to_dict() for k, m in ledger._missions.items()},
            "tasks": {k: t.to_dict() for k, t in ledger._tasks.items()},
            "epoch_high": dict(ledger._epoch_high),
            "rejected": dict(ledger._rejected),
            "idem_seen": sorted(ledger._idem_seen),
            # Sans elles, une ligne tronquée couverte par l'instantané cesserait d'être signalée.
            "unreadable": list(ledger._unreadable),
        },
    }
    try:
        # ``ensure_ascii`` : un titre qui porte une moitié de paire de substitution (échappée dans le
        # journal) reste écrivable en UTF-8 et se relit à l'identique ; ``ensure_ascii=False`` le ferait
        # échouer à l'encodage, et un cache ne doit jamais faire échouer le rejeu qu'il accélère.
        atomic_write(
            ledger._events_path.parent / SNAPSHOT_FILENAME,
            json.dumps(payload, separators=(",", ":")) + "\n",
        )
    except (OSError, ValueError):
        logger.debug("instantané du ledger non écrit", exc_info=True)


def all_events(ledger: MissionLedger) -> list[LedgerEvent]:
    """Tous les événements du journal : ceux que l'instantané couvre sont relus à la demande, une fois."""
    cover = ledger._snapshot
    if cover is not None and cover.offset > 0:
        # Même découpage que le rejeu (``\n`` seul, une ligne illisible sautée) : un U+2028 brut reste dans sa ligne (K12 x K13).
        records, _ = parse_jsonl(ledger._events_path.read_bytes()[: cover.offset])
        covered = [LedgerEvent.from_dict(raw) for raw in records]
        ledger._events = [*covered, *ledger._events]
        ledger._snapshot = Cover(0, cover.latest)
    return ledger._events
