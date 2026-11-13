"""Le disjoncteur de l'orchestrateur : ouvert, il empêche tout lancement de session (#876, lot 2).

Il s'ouvre de deux façons :

- **par comptage** : :func:`record_failure` après chaque échec de lancement (``claude --bg`` refusé, binaire absent)
  ou de session (tâche qui finit en ``failed``, relevée par le janitor) ; au N-ième échec **consécutif** (réglable,
  défaut 3), il s'ouvre. Seule la fin réussie d'une tâche (:func:`record_success`) remet le compteur à zéro : un
  lancement réussi ne dit rien d'une session qui plante ensuite. Une panne réseau passagère (:func:`record_transient`)
  n'entre pas dans ce comptage : elle est comptée à part et rejouée au tick suivant (#1192) ;
- **par un événement externe** : :func:`trip` — le lot 1 l'appelle quand un check est modifié après la création de la
  tâche. Aucun comptage : il s'ouvre tout de suite, avec sa raison.

Une fois ouvert, il le reste jusqu'à une action humaine (:func:`reset`, ``grimoire orchestrate breaker reset``) :
ni le temps ni un succès ne le referment. L'état est un fichier sous ``_grimoire-output/control/`` ; un fichier
illisible vaut **ouvert** (échec fermé). Aucun appel de LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from grimoire.core.fsio import atomic_write, locked
from grimoire.missions.pause import PAUSE_RELPATH

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "BREAKER_RELPATH", "BreakerState", "read_breaker", "record_failure", "record_success", "record_transient", "reset",
    "trip",
]

BREAKER_RELPATH = PAUSE_RELPATH.parent / "breaker.json"
_SCHEMA = "grimoire-orchestrator-breaker/v1"
_HISTORY_MAX = 20
_LOCK_TIMEOUT_S = 10.0


@dataclass(frozen=True, slots=True)
class BreakerState:
    """``open`` : aucun lancement. ``failures`` : les échecs consécutifs comptés. ``transients`` : les pannes réseau
    passagères, comptées à part et sans effet (#1192). ``source`` : ``count``, ``external``, ``unreadable``."""

    open: bool = False
    failures: int = 0
    reason: str = ""
    source: str = ""
    opened_at: str = ""
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    transients: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "open": self.open, "failures": self.failures, "reason": self.reason, "source": self.source,
            "opened_at": self.opened_at, "history": [dict(h) for h in self.history], "transients": self.transients,
        }

    def describe(self) -> str:
        if self.open:
            return f"OUVERT ({self.source or 'inconnu'}) : {self.reason} — aucun lancement ; `grimoire orchestrate breaker reset` le referme"
        transient = f", {self.transients} panne(s) réseau non comptée(s)" if self.transients else ""
        return f"fermé ({self.failures} échec(s) consécutif(s){transient})"


def _path(root: Path) -> Path:
    return root / BREAKER_RELPATH


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_breaker(root: Path) -> BreakerState:
    """L'état du disjoncteur ; sans fichier : fermé. Un fichier illisible ou d'un autre schéma : ouvert."""
    path = _path(root)
    if not path.is_file():
        return BreakerState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("$schema") != _SCHEMA:
            raise ValueError("schéma inconnu")
        history = tuple(h for h in raw.get("history", []) if isinstance(h, dict))
        return BreakerState(
            open=bool(raw["open"]), failures=int(raw.get("failures", 0)), reason=str(raw.get("reason", "")),
            source=str(raw.get("source", "")), opened_at=str(raw.get("opened_at", "")), history=history,
            transients=int(raw.get("transients", 0)),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return BreakerState(open=True, reason=f"état du disjoncteur illisible ({type(exc).__name__}) : par prudence, ouvert", source="unreadable")


def _write(root: Path, state: BreakerState) -> None:
    payload = {"$schema": _SCHEMA, **state.to_dict()}
    atomic_write(_path(root), json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _update(root: Path, change: Any) -> BreakerState:
    """Lecture, changement, écriture sous le verrou du fichier : deux écrivains ne perdent pas un comptage."""
    with locked(_path(root), timeout=_LOCK_TIMEOUT_S):
        before = read_breaker(root)
        after: BreakerState = change(before)
        if after != before:
            _write(root, after)
        return after


def _with_history(state: BreakerState, entry: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return (*state.history, entry)[-_HISTORY_MAX:]


def trip(root: Path, reason: str, *, source: str = "external") -> BreakerState:
    """Ouvre le disjoncteur sur un événement externe. Déjà ouvert : la première raison reste, la nouvelle est notée à l'historique."""

    def change(state: BreakerState) -> BreakerState:
        entry = {"at": _now(), "event": "trip", "source": source, "reason": reason}
        if state.open:
            return BreakerState(True, state.failures, state.reason, state.source, state.opened_at,
                                _with_history(state, entry), state.transients)
        return BreakerState(True, state.failures, reason, source, _now(), _with_history(state, entry), state.transients)

    return _update(root, change)


def record_failure(root: Path, threshold: int, detail: str) -> BreakerState:
    """Un échec de plus ; au *threshold*-ième consécutif, le disjoncteur s'ouvre."""

    def change(state: BreakerState) -> BreakerState:
        failures = state.failures + 1
        entry = {"at": _now(), "event": "failure", "failures": failures, "detail": detail}
        if state.open:
            return BreakerState(True, failures, state.reason, state.source, state.opened_at,
                                _with_history(state, entry), state.transients)
        if failures >= max(1, threshold):
            reason = f"{failures} échec(s) consécutif(s), dernier : {detail}"
            return BreakerState(True, failures, reason, "count", _now(), _with_history(state, entry), state.transients)
        return BreakerState(False, failures, "", "", "", _with_history(state, entry), state.transients)

    return _update(root, change)


def record_transient(root: Path, detail: str) -> BreakerState:
    """Une panne réseau passagère (#1192) : comptée à part, notée à l'historique, **sans jamais ouvrir** le disjoncteur.

    L'étape qui l'a rencontrée est rejouée au tick suivant ; seule une session qui échoue vraiment compte au
    disjoncteur. Un disjoncteur déjà ouvert reste ouvert (il ne se referme que par un geste humain).
    """

    def change(state: BreakerState) -> BreakerState:
        entry = {"at": _now(), "event": "transient", "detail": detail}
        return BreakerState(
            state.open, state.failures, state.reason, state.source, state.opened_at,
            _with_history(state, entry), state.transients + 1,
        )

    return _update(root, change)


def record_success(root: Path) -> BreakerState:
    """Une tâche s'est close : le compteur repart de zéro. Un disjoncteur ouvert ne se referme pas pour autant."""

    def change(state: BreakerState) -> BreakerState:
        if state.failures == 0:
            return state
        return BreakerState(state.open, 0, state.reason, state.source, state.opened_at, state.history, state.transients)

    return _update(root, change)


def reset(root: Path, *, actor: str = "human") -> BreakerState:
    """Referme le disjoncteur (geste humain : l'appelant exige l'origine) et remet les compteurs à zéro."""

    def change(state: BreakerState) -> BreakerState:
        entry = {"at": _now(), "event": "reset", "actor": actor, "was_open": state.open}
        return BreakerState(False, 0, "", "", "", _with_history(state, entry), 0)

    return _update(root, change)
