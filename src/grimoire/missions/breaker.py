"""Le disjoncteur de l'orchestrateur : ouvert, il empêche tout lancement de session (#876, lot 2).

Il s'ouvre de deux façons :

- **par comptage** : :func:`record_failure` après chaque échec de lancement (``claude --bg`` refusé, binaire absent)
  ou de session (tâche qui finit en ``failed``, relevée par le janitor) ; au N-ième échec **consécutif** (réglable,
  défaut 3), il s'ouvre. Seule la fin réussie d'une tâche (:func:`record_success`) remet le compteur à zéro : un
  lancement réussi ne dit rien d'une session qui plante ensuite. Une panne réseau passagère (:func:`record_transient`)
  n'entre pas dans ce comptage : elle est comptée à part et rejouée au tick suivant (#1192) ;
- **par un événement externe** : :func:`trip` — le lot 1 l'appelle quand un check est modifié après la création de la
  tâche. Aucun comptage : il s'ouvre tout de suite, avec sa raison.

**Un disjoncteur ouvert par comptage ne reste plus fermé à la main.** Après ``orchestrator.breaker.cooldown_minutes``
(défaut 30) sans lancement, il passe **semi-ouvert** : :func:`admit` admet **un seul** lancement (la sonde). Son succès
referme le disjoncteur ; son échec le rouvre et double le délai (30 → 60 → 120 → 240 min, plafond 4 h). Une sonde sans
verdict au bout d'un délai complet en admet une autre : un lancement perdu ne gèle jamais l'autonomie. Un disjoncteur
ouvert par :func:`trip` (verdict forgé, vérification modifiée) reste fermé à la main, comme avant : ni le temps ni un
succès ne le referment.

**Une tâche ne compte que pour un échec.** Les échecs sont suivis par tâche (*task_id*) : la première fois qu'une
tâche échoue, elle compte pour un échec global ; ses échecs suivants ne comptent plus, et au N-ième la tâche est
**mise à l'écart** (:attr:`BreakerState.parked`) avec sa raison. Une seule tâche qui boucle ne peut donc plus ouvrir
le disjoncteur pour tout le monde. La mise à l'écart sort la tâche de la file d'admission ; ``grimoire orchestrate
breaker reset`` (geste humain) la lève.

L'état est un fichier sous ``_grimoire-output/control/`` ; un fichier illisible vaut **ouvert** (échec fermé). Aucun
appel de LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from grimoire.core.fsio import atomic_write, locked
from grimoire.missions.pause import PAUSE_RELPATH

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "BREAKER_RELPATH", "DEFAULT_COOLDOWN_MINUTES", "MAX_COOLDOWN_MINUTES", "Admission", "BreakerState", "admit",
    "read_breaker", "record_failure", "record_success", "record_transient", "reset", "trip",
]

BREAKER_RELPATH = PAUSE_RELPATH.parent / "breaker.json"
_SCHEMA = "grimoire-orchestrator-breaker/v1"
_HISTORY_MAX = 20
_LOCK_TIMEOUT_S = 10.0
#: Le délai par défaut avant qu'un disjoncteur ouvert par comptage ne passe semi-ouvert (30 min).
DEFAULT_COOLDOWN_MINUTES = 30
#: Le plafond du délai doublé à chaque échec de la sonde semi-ouverte (4 h).
MAX_COOLDOWN_MINUTES = 240


@dataclass(frozen=True, slots=True)
class BreakerState:
    """``open`` : aucun lancement. ``failures`` : les échecs consécutifs comptés. ``transients`` : les pannes réseau
    passagères, comptées à part et sans effet (#1192). ``source`` : ``count``, ``external``, ``unreadable``.

    ``half_open`` : une sonde a été admise, son verdict n'est pas encore connu. ``cooldown_minutes`` : le délai courant
    de réouverture (doublé à chaque échec de la sonde, plafonné). ``task_failures`` : les échecs par tâche depuis son
    dernier succès (première entrée seulement comptée au global). ``parked`` : les tâches mises à l'écart et leur raison.
    """

    open: bool = False
    failures: int = 0
    reason: str = ""
    source: str = ""
    opened_at: str = ""
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    transients: int = 0
    half_open: bool = False
    half_open_at: str = ""
    cooldown_minutes: int = 0
    task_failures: tuple[tuple[str, int], ...] = ()
    parked: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "open": self.open, "failures": self.failures, "reason": self.reason, "source": self.source,
            "opened_at": self.opened_at, "history": [dict(h) for h in self.history], "transients": self.transients,
            "half_open": self.half_open, "half_open_at": self.half_open_at,
            "cooldown_minutes": self.cooldown_minutes,
            "task_failures": dict(self.task_failures), "parked": dict(self.parked),
        }

    def parked_reason(self, task_id: str) -> str:
        """La raison de la mise à l'écart de *task_id* ; vide s'il n'est pas écarté."""
        return dict(self.parked).get(task_id, "")

    def next_attempt_at(self) -> str:
        """L'instant où la sonde semi-ouverte sera admise (ISO 8601) ; vide si ce n'est pas un disjoncteur de comptage.

        ``opened_at`` illisible vaut « maintenant » : le disjoncteur doit pouvoir se rouvrir, jamais rester gelé.
        """
        if not self.open or self.source != "count" or self.half_open:
            return ""
        opened = _parse_moment(self.opened_at)
        if opened is None:
            return _now()
        return (opened + timedelta(minutes=max(1, self.cooldown_minutes or DEFAULT_COOLDOWN_MINUTES))).isoformat(
            timespec="seconds"
        )

    def describe(self) -> str:
        if self.open:
            if self.half_open:
                return (
                    f"SEMI-OUVERT ({self.source or 'inconnu'}) : une seule tentative admise ({self.cooldown_minutes} min) ; "
                    f"son succès referme, son échec double le délai — {self.reason}"
                )
            if self.source == "count":
                next_at = self.next_attempt_at()
                return (
                    f"OUVERT ({self.source}) : {self.reason} — prochaine tentative après {next_at} (semi-ouvert), "
                    "ou `grimoire orchestrate breaker reset` pour le refermer"
                )
            return f"OUVERT ({self.source or 'inconnu'}) : {self.reason} — aucun lancement ; `grimoire orchestrate breaker reset` le referme"
        transient = f", {self.transients} panne(s) réseau non comptée(s)" if self.transients else ""
        parked = f", {len(self.parked)} tâche(s) mise(s) à l'écart" if self.parked else ""
        return f"fermé ({self.failures} échec(s) consécutif(s){transient}{parked})"


@dataclass(frozen=True, slots=True)
class Admission:
    """Ce que la politique du disjoncteur répond à un lancement : ``allowed``, et ``probe`` quand c'est la sonde
    semi-ouverte (la seule tentative admise avant un verdict)."""

    allowed: bool
    probe: bool
    state: BreakerState
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "probe": self.probe, "reason": self.reason, "state": self.state.to_dict()}


def _path(root: Path) -> Path:
    return root / BREAKER_RELPATH


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_moment(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _pairs(raw: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, dict):
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in raw.items()))


def _counts(raw: Any) -> tuple[tuple[str, int], ...]:
    if not isinstance(raw, dict):
        return ()
    out: list[tuple[str, int]] = []
    for key, value in raw.items():
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            out.append((str(key), number))
    return tuple(sorted(out))


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
            transients=int(raw.get("transients", 0)), half_open=bool(raw.get("half_open", False)),
            half_open_at=str(raw.get("half_open_at", "")), cooldown_minutes=int(raw.get("cooldown_minutes", 0) or 0),
            task_failures=_counts(raw.get("task_failures")), parked=_pairs(raw.get("parked")),
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
    """Ouvre le disjoncteur sur un événement externe. Déjà ouvert : la première raison reste, la nouvelle est notée à l'historique.

    Un ``trip`` reste fermé à la main : il annule une éventuelle sonde semi-ouverte et prend la main sur un
    disjoncteur de comptage (source du trip) ; ni le temps ni un succès ne le referment.
    """

    def change(state: BreakerState) -> BreakerState:
        entry = {"at": _now(), "event": "trip", "source": source, "reason": reason}
        if state.open:
            # la première raison reste ; le trip prend la main (plus de sonde de comptage : fermeture humaine)
            return replace(
                state, source=source, half_open=False, half_open_at="", cooldown_minutes=0,
                history=_with_history(state, entry),
            )
        return replace(
            state, open=True, reason=reason, source=source, opened_at=_now(), half_open=False, half_open_at="",
            cooldown_minutes=0, history=_with_history(state, entry),
        )

    return _update(root, change)


def admit(
    root: Path, *, cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES, now: datetime | None = None
) -> Admission:
    """Le disjoncteur admet-il ce lancement ? Un disjoncteur de comptage ouvert depuis *cooldown_minutes* passe
    semi-ouvert et admet **une** sonde ; les appels suivants tant que son verdict n'est pas connu sont refusés.

    Un ``trip`` (source autre que ``count``) ne passe jamais semi-ouvert. Une sonde sans verdict depuis un délai
    complet en admet une autre : un lancement perdu ne gèle pas l'autonomie. L'admission elle-même est atomique
    (verrou du fichier) : deux étages du même tick ne consomment pas deux sondes.
    """
    moment = now or datetime.now(UTC)
    decided: Admission | None = None

    def change(state: BreakerState) -> BreakerState:
        nonlocal decided
        if not state.open:
            decided = Admission(True, False, state)
            return state
        if state.source != "count":
            decided = Admission(False, False, state, f"disjoncteur ouvert : {state.reason}")
            return state
        delay = timedelta(minutes=max(1, state.cooldown_minutes or cooldown_minutes))
        if state.half_open:
            started = _parse_moment(state.half_open_at)
            if started is not None and moment - started < delay:
                decided = Admission(
                    False, False, state,
                    "disjoncteur semi-ouvert : la tentative en cours n'a pas encore rendu son verdict",
                )
                return state
            stamp = moment.isoformat(timespec="seconds")
            entry = {"at": stamp, "event": "half_open_retry", "detail": "sonde sans verdict : une autre est admise"}
            after = replace(state, half_open_at=stamp, history=_with_history(state, entry))
            decided = Admission(True, True, after)
            return after
        opened = _parse_moment(state.opened_at)
        if opened is not None and moment - opened < delay:
            next_at = (opened + delay).isoformat(timespec="seconds")
            decided = Admission(
                False, False, state,
                f"disjoncteur ouvert : {state.reason} — prochaine tentative après {next_at}",
            )
            return state
        stamp = moment.isoformat(timespec="seconds")
        entry = {"at": stamp, "event": "half_open", "detail": f"délai de {delay.total_seconds() / 60:.0f} min écoulé"}
        after = replace(state, half_open=True, half_open_at=stamp, history=_with_history(state, entry))
        decided = Admission(True, True, after)
        return after

    state = _update(root, change)
    return decided if decided is not None else Admission(True, False, state)


def record_failure(
    root: Path, threshold: int, detail: str, *, task_id: str = "",
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES, now: datetime | None = None,
) -> BreakerState:
    """Un échec de plus ; au *threshold*-ième échec **global** consécutif, le disjoncteur s'ouvre.

    *task_id* range l'échec sous la tâche : seule la **première** fois qu'elle échoue compte au comptage global ; au
    *threshold*-ième échec de la même tâche, elle est mise à l'écart avec sa raison, sans ouvrir le disjoncteur pour les
    autres. Un échec pendant la sonde semi-ouverte rouvre le disjoncteur et double le délai (plafond 4 h).
    """
    moment = now or datetime.now(UTC)
    stamp = moment.isoformat(timespec="seconds")

    def change(state: BreakerState) -> BreakerState:
        counts = dict(state.task_failures)
        parked = dict(state.parked)
        counted = True
        if task_id:
            attempts = counts.get(task_id, 0) + 1
            counts[task_id] = attempts
            counted = attempts == 1 and task_id not in parked
            if attempts >= max(1, threshold) and task_id not in parked:
                parked[task_id] = f"{attempts} échec(s) de la tâche, dernier : {detail}"
        entry: dict[str, Any] = {"at": stamp, "event": "failure", "detail": detail}
        if task_id:
            entry["task"] = task_id
            entry["task_failures"] = counts[task_id]
        if counted:
            entry["failures"] = state.failures + 1
        failures = state.failures + (1 if counted else 0)
        history = _with_history(state, entry)
        task_failures = tuple(sorted(counts.items()))
        parked_tasks = tuple(sorted(parked.items()))
        if state.open:
            if state.half_open:  # l'échec de la sonde : on rouvre et on double le délai, plafonné à 4 h
                delay = min(max(1, state.cooldown_minutes or cooldown_minutes) * 2, MAX_COOLDOWN_MINUTES)
                reason = f"tentative semi-ouverte échouée : {detail}"
                return replace(
                    state, failures=failures, reason=reason, source="count", opened_at=stamp, half_open=False,
                    half_open_at="", cooldown_minutes=delay, history=history,
                    task_failures=task_failures, parked=parked_tasks,
                )
            return replace(state, failures=failures, history=history, task_failures=task_failures, parked=parked_tasks)
        if failures >= max(1, threshold):
            reason = f"{failures} échec(s) consécutif(s), dernier : {detail}"
            return replace(
                state, open=True, failures=failures, reason=reason, source="count", opened_at=stamp,
                half_open=False, half_open_at="", cooldown_minutes=max(1, cooldown_minutes), history=history,
                task_failures=task_failures, parked=parked_tasks,
            )
        return replace(state, failures=failures, history=history, task_failures=task_failures, parked=parked_tasks)

    return _update(root, change)


def record_transient(root: Path, detail: str) -> BreakerState:
    """Une panne réseau passagère (#1192) : comptée à part, notée à l'historique, **sans jamais ouvrir** le disjoncteur.

    L'étape qui l'a rencontrée est rejouée au tick suivant ; seule une session qui échoue vraiment compte au
    disjoncteur. Un disjoncteur déjà ouvert reste ouvert (sauf son délai de sonde, qui continue de courir).
    """

    def change(state: BreakerState) -> BreakerState:
        entry = {"at": _now(), "event": "transient", "detail": detail}
        return replace(state, transients=state.transients + 1, history=_with_history(state, entry))

    return _update(root, change)


def record_success(root: Path, *, task_id: str = "", now: datetime | None = None) -> BreakerState:
    """Une tâche s'est close : le compteur repart de zéro.

    Le succès de la sonde semi-ouverte **referme** un disjoncteur ouvert par comptage ; un disjoncteur ouvert par
    ``trip`` ne se referme pas pour autant. *task_id* remet à zéro les échecs de cette tâche et lève sa mise à l'écart.
    """
    moment = now or datetime.now(UTC)
    stamp = moment.isoformat(timespec="seconds")

    def change(state: BreakerState) -> BreakerState:
        counts = dict(state.task_failures)
        parked = dict(state.parked)
        if task_id:
            counts.pop(task_id, None)
            parked.pop(task_id, None)
        task_failures = tuple(sorted(counts.items()))
        parked_tasks = tuple(sorted(parked.items()))
        if state.open and state.source == "count":
            entry = {"at": stamp, "event": "close", "detail": "succès d'une tâche : le disjoncteur se referme"}
            return replace(
                state, open=False, failures=0, reason="", source="", opened_at="", half_open=False,
                half_open_at="", cooldown_minutes=0, history=_with_history(state, entry),
                task_failures=task_failures, parked=parked_tasks,
            )
        if state.failures == 0 and task_failures == state.task_failures and parked_tasks == state.parked:
            return state
        return replace(state, failures=0, task_failures=task_failures, parked=parked_tasks)

    return _update(root, change)


def reset(root: Path, *, actor: str = "human") -> BreakerState:
    """Referme le disjoncteur (geste humain : l'appelant exige l'origine), remet les compteurs à zéro et lève les
    mises à l'écart."""

    def change(state: BreakerState) -> BreakerState:
        entry = {"at": _now(), "event": "reset", "actor": actor, "was_open": state.open}
        return replace(
            state, open=False, failures=0, reason="", source="", opened_at="", half_open=False, half_open_at="",
            cooldown_minutes=0, task_failures=(), parked=(), transients=0, history=_with_history(state, entry),
        )

    return _update(root, change)
