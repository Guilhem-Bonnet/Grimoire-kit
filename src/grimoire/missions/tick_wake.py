"""Le réveil du tick sur événement : avancer un tick qui aurait eu lieu, jamais l'exécuter ni l'autoriser.

Le minuteur (:mod:`.timer`) reste le filet de sécurité et sa cadence ne change pas. Mais une réponse humaine, une
acceptation, une reprise après pause, la fin d'une session de worker ou une fusion attendaient le prochain tick (2,5 min
en moyenne, jusqu'à 5). :func:`request_tick` demande qu'il vienne plus tôt : elle démarre l'unité systemd utilisateur du
tick **déjà installée** (``systemctl --user start <unité> --no-block``), et ne lance jamais le tick dans le processus
appelant (le verrou du tick, la pause, le disjoncteur et la politique gardent chacun leur rôle dans le tick lui-même).

Ce que le réveil ne fait jamais (le test de chaque ligne est dans ``tests/unit/missions/test_tick_wake.py``) :

- il n'active rien à la place de l'humain : sans orchestrateur activé dans la politique, sans minuteur posé, sous une
  pause qui porte sur tout le périmètre (projet ou machine) ou disjoncteur ouvert, il ne fait rien et n'écrit rien ;
- il ne se répète pas : au plus un réveil par fenêtre de :data:`DEBOUNCE_S` et par projet, au plus
  :data:`MAX_PER_HOUR` par heure ; au-delà, le minuteur fait le travail ;
- un tick ne se réveille pas lui-même en boucle : un réveil demandé par un tick (:data:`TICK_REASONS`) est refusé si
  le dernier réveil enregistré venait déjà d'un tick, dans les :data:`CHAIN_WINDOW_S` ;
- aucune entrée externe n'atteint la ligne de commande : le motif vient d'une liste fermée, le nom d'unité est dérivé du
  chemin du projet par le code de :mod:`.timer` et revérifié par une expression stricte, l'argv est une liste, sans shell ;
- il ne fait jamais échouer le geste qui l'appelle (:func:`wake_after` avale et journalise toute erreur).

L'état tient dans ``_grimoire-output/control/tick-wake.json`` (écrit par :func:`~grimoire.core.fsio.atomic_write`, lu par
:func:`~grimoire.core.regular_file.open_regular` : un lien, une FIFO ou un fichier abîmé comptent pour « aucun état »).
"""

from __future__ import annotations

import functools
import json
import logging
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from grimoire.core.fsio import atomic_write, locked
from grimoire.core.regular_file import open_regular
from grimoire.missions.schemas import ORIGIN_HUMAN

__all__ = [
    "CHAIN_WINDOW_S",
    "DEBOUNCE_S",
    "MAX_PER_HOUR",
    "TICK_DELAY_S",
    "TICK_REASONS",
    "WAKE_REASONS",
    "WakeOutcome",
    "describe_last_wake",
    "read_last_wake",
    "request_tick",
    "tick_wake_reason",
    "wake_after",
    "wakes_after_human",
]

logger = logging.getLogger(__name__)

#: Au plus un réveil par fenêtre (secondes) et par projet : une rafale de gestes (dix réponses de suite) ne donne qu'un tick.
DEBOUNCE_S = 20
#: Plafond de réveils par heure glissante et par projet ; au-delà, on laisse le minuteur faire.
MAX_PER_HOUR = 12
#: Un réveil demandé par un tick est refusé si le précédent venait déjà d'un tick dans cette fenêtre (secondes) :
#: un tick ne provoque qu'un seul tick de plus, la cadence du minuteur (5 min) reprend ensuite.
CHAIN_WINDOW_S = 360
#: Un réveil demandé par un tick part en différé (secondes) : l'unité est encore active pendant le tick qui le demande,
#: et un ``start`` sur une unité active ne relance rien.
TICK_DELAY_S = 30
_TIMEOUT_S = 5
_LOCK_TIMEOUT_S = 2.0
_HISTORY_MAX = 64

STATE_RELPATH = Path("_grimoire-output") / "control" / "tick-wake.json"
_SCHEMA = "grimoire-tick-wake/v1"
_UNIT = re.compile(r"grimoire-tick-[a-z0-9-]+\.service")

#: La liste fermée des motifs : clé → ce que l'humain lit dans ``orchestrate status``.
WAKE_REASONS: dict[str, str] = {
    "reponse_humaine": "réponse humaine à une question",
    "acceptation": "acceptation humaine d'une tâche",
    "reprise": "reprise après pause",
    "fin_de_session": "fin de session d'un worker",
    "fusion": "fusion observée par le tick",
}
#: Les motifs qu'un tick demande à son propre sujet : soumis à la garde anti-boucle.
TICK_REASONS = frozenset({"fin_de_session", "fusion"})

P = ParamSpec("P")
R = TypeVar("R")
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True, slots=True)
class WakeOutcome:
    """``status`` : ``started`` (l'unité a été démarrée), ``failed`` (systemd a refusé : compté et noté) ou ``skipped`` (rien fait)."""

    status: str
    detail: str = ""


def _skipped(why: str) -> WakeOutcome:
    return WakeOutcome("skipped", why)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _state_path(root: Path) -> Path:
    return root / STATE_RELPATH


def _read_state(path: Path) -> dict[str, Any]:
    """L'état ; ``{}`` s'il est absent, abîmé, d'un autre schéma ou n'est pas un fichier ordinaire (jamais d'exception)."""
    try:
        with open_regular(path) as fh:
            raw = json.loads(fh.read(65536).decode("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("$schema") != _SCHEMA:
        return {}
    last = raw.get("last")
    attempts = raw.get("attempts")
    clean_last = {k: str(v) for k, v in last.items() if k in ("at", "reason", "result", "origin")} if isinstance(last, dict) else {}
    clean_attempts = [float(a) for a in attempts[-_HISTORY_MAX:] if isinstance(a, (int, float)) and not isinstance(a, bool)] \
        if isinstance(attempts, list) else []
    return {"last": clean_last, "attempts": clean_attempts}


def read_last_wake(project_root: Path) -> dict[str, str]:
    """Le dernier réveil tenté (``at``, ``reason``, ``result``, ``origin``) ; ``{}`` s'il n'y en a pas. Lecture seule."""
    from grimoire.missions.pause import home_root

    last = _read_state(_state_path(home_root(project_root))).get("last")
    return dict(last) if isinstance(last, dict) else {}


def describe_last_wake(project_root: Path) -> str:
    """Une ligne pour ``orchestrate status`` : le dernier réveil, ou son absence."""
    last = read_last_wake(project_root)
    if not last:
        return "réveil : aucun (le minuteur fait seul le travail)"
    label = WAKE_REASONS.get(last.get("reason", ""), last.get("reason", "?"))
    return f"réveil : {last.get('at', '?')} — {label} — {last.get('result', '?')}"


def _enabled(root: Path) -> bool:
    """Un rôle autonome est activé dans la politique : sans lui, le tick ne lance rien et n'a pas à être avancé."""
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy

    policy = load_orchestrator_policy(root).policy
    return policy is not None and (
        policy.worker.enabled or policy.reviewer.enabled or policy.merger.enabled or policy.orchestrator.enabled
        or policy.arbiter.enabled
    )


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # argv en liste, éléments fixes ou dérivés du code de timer.py, sans shell
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_TIMEOUT_S, check=False
    )


def _argv(unit: str, *, delayed: bool) -> list[str] | None:
    """La ligne de commande : ``systemctl --user start <unité> --no-block``, ou en différé par ``systemd-run`` ; ``None`` sans l'outil."""
    start = ["systemctl", "--user", "start", "--no-block", unit]
    if not delayed:
        return start if shutil.which("systemctl") else None
    if not (shutil.which("systemctl") and shutil.which("systemd-run")):
        return None
    return ["systemd-run", "--user", "--quiet", "--collect", f"--on-active={TICK_DELAY_S}s", "--", *start]


def request_tick(
    project_root: Path, reason: str, *, run: Runner | None = None, now: float | None = None,
    control_home: Path | None = None,
) -> WakeOutcome:
    """Demande un tick proche ; ne l'exécute jamais ici. Ne lève pas pour un refus : le résultat dit pourquoi.

    *run* est le lanceur d'argv (défaut : ``subprocess.run``, 5 s) ; *now* l'horloge en secondes ; *control_home*
    le dossier de contrôle de la machine (pause globale).
    """
    from grimoire.missions.breaker import read_breaker
    from grimoire.missions.pause import home_root, read_pause
    from grimoire.missions.timer import _unit_name, unit_dir

    if reason not in WAKE_REASONS:
        return _skipped(f"motif hors liste fermée : {reason!r}")
    root = home_root(project_root)
    unit = f"{_unit_name(root)}.service"
    timer_file = unit_dir() / f"{_unit_name(root)}.timer"
    if not timer_file.is_file() or not _UNIT.fullmatch(unit):
        return _skipped("minuteur non installé : le réveil n'active rien")
    if not _enabled(root):
        return _skipped("orchestrateur non activé pour ce projet")
    if read_pause(root, control_home=control_home).global_stop:
        return _skipped("pause en vigueur")
    if read_breaker(root).open:
        return _skipped("disjoncteur ouvert")
    delayed = reason in TICK_REASONS
    argv = _argv(unit, delayed=delayed)
    if argv is None:
        return _skipped("systemd absent : le minuteur ou le cron fera le travail")
    moment = time.time() if now is None else now
    path = _state_path(root)
    try:
        with locked(path, timeout=_LOCK_TIMEOUT_S):
            state = _read_state(path)
            attempts = [a for a in state.get("attempts", []) if moment - 3600 < a <= moment]
            last_epoch = max(attempts, default=0.0)
            last = state.get("last", {})
            if last_epoch and 0 <= moment - last_epoch < DEBOUNCE_S:
                return _skipped(f"un réveil date de moins de {DEBOUNCE_S} s")
            if len(attempts) >= MAX_PER_HOUR:
                return _skipped(f"plafond de {MAX_PER_HOUR} réveils par heure atteint : le minuteur fait le travail")
            if delayed and last.get("origin") == "tick" and 0 <= moment - last_epoch < CHAIN_WINDOW_S:
                return _skipped("le dernier réveil venait déjà d'un tick : pas de chaîne")
            outcome = _start(argv, run or _run)
            record = {"at": _now(), "reason": reason, "result": f"{outcome.status} : {outcome.detail}" if outcome.detail else outcome.status,
                      "origin": "tick" if delayed else "geste"}
            try:
                atomic_write(path, json.dumps(
                    {"$schema": _SCHEMA, "last": record, "attempts": [*attempts, moment][-_HISTORY_MAX:]},
                    ensure_ascii=False, indent=2, sort_keys=True,
                ) + "\n")
            except OSError as exc:  # l'unité est déjà démarrée : on le dit, sans mémoire le prochain geste le saura au plus tard par le minuteur
                logger.warning("réveil du tick : état non écrit (%s)", exc)
            return outcome
    except (TimeoutError, OSError) as exc:
        logger.warning("réveil du tick : verrou tenu ou illisible (%s)", exc)
        return _skipped(f"état du réveil indisponible ({type(exc).__name__})")


def _start(argv: list[str], run: Runner) -> WakeOutcome:
    try:
        result = run(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("réveil du tick : %s a échoué (%s)", argv[0], exc)
        return WakeOutcome("failed", type(exc).__name__)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:200] or f"code {result.returncode}"
        logger.warning("réveil du tick : %s a répondu %s", argv[0], detail)
        return WakeOutcome("failed", detail)
    return WakeOutcome("started")


def wake_after(project_root: Path, reason: str) -> None:
    """Le réveil en fin de geste : toute erreur est avalée et journalisée, le geste n'échoue jamais à cause de lui."""
    try:
        outcome = request_tick(project_root, reason)
        logger.debug("réveil du tick (%s) : %s %s", reason, outcome.status, outcome.detail)
    except Exception:  # un réveil est un confort, jamais une condition du geste
        logger.warning("réveil du tick (%s) : erreur avalée", reason, exc_info=True)


def wakes_after_human(reason: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Décore une méthode de ``TaskService`` à argument ``origin`` : réveille le tick après un geste **humain** réussi.

    Les gestes d'un agent ou de l'orchestrateur sous mandat ne réveillent rien (ils sont déjà dans un tick ou dans une
    session) ; un geste qui lève n'a rien à faire avancer.
    """

    def decorate(method: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(method)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = method(*args, **kwargs)
            if kwargs.get("origin") == ORIGIN_HUMAN:
                root = getattr(args[0], "project_root", None)
                if isinstance(root, Path):
                    wake_after(root, reason)
            return result

        return wrapper

    return decorate


def tick_wake_reason(report: Any) -> str | None:
    """Le motif de réveil d'un tick qui a **fait** quelque chose, ``None`` sinon (un tick vide ne se réveille pas)."""
    if getattr(report, "status", "") != "ran" or getattr(report, "mode", "") != "apply":
        return None
    merged = any(m.action == "merge" and m.status == "done" for m in getattr(report, "merges", ())) or any(
        m.status == "done" for m in getattr(report, "external_merges", ())
    )
    if merged:
        return "fusion"
    return "fin_de_session" if getattr(report, "reaped", ()) else None
