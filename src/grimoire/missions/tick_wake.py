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
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from grimoire.core.fsio import atomic_write, lock_files, locked
from grimoire.core.regular_file import open_regular
from grimoire.missions.schemas import ORIGIN_HUMAN

__all__ = [
    "CHAIN_WINDOW_S",
    "DEBOUNCE_S",
    "MAX_PER_HOUR",
    "TICK_DELAY_S",
    "TICK_REASONS",
    "WAKE_BUDGET_S",
    "WAKE_REASONS",
    "WakeOutcome",
    "describe_last_wake",
    "note_tick",
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
_TIMEOUT_S = 2
_LOCK_TIMEOUT_S = 1.0
#: Budget total (secondes) qu'un geste accorde au réveil (verrou + sous-processus) : au-delà, il rend la main, le réveil
#: continue ou s'abandonne dans son fil détaché, le minuteur fera le travail.
WAKE_BUDGET_S = 3.0
#: Un tick démarré moins de ce nombre de secondes après le précédent est lui-même un tick de réveil : il n'en demande pas
#: un autre. Cette borne ne dépend pas du fichier d'état des réveils (voir :func:`note_tick`).
TICK_GAP_S = 60
TICK_MARK_RELPATH = Path("_grimoire-output") / "control" / "tick-last"
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


def _read_state(path: Path) -> dict[str, Any] | None:
    """L'état ; ``None`` s'il est absent, abîmé, tronqué, d'un autre schéma ou n'est pas un fichier ordinaire (jamais d'exception).

    ``None`` ne vaut pas « aucun réveil récent » : un réveil de tick n'est pas accordé sans état lisible.
    """
    try:
        with open_regular(path) as fh:
            raw = json.loads(fh.read(65536).decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("$schema") != _SCHEMA:
        return None
    last = raw.get("last")
    attempts = raw.get("attempts")
    clean_last = {k: str(v) for k, v in last.items() if k in ("at", "reason", "result", "origin")} if isinstance(last, dict) else {}
    clean_attempts = [float(a) for a in attempts[-_HISTORY_MAX:] if isinstance(a, (int, float)) and not isinstance(a, bool)] \
        if isinstance(attempts, list) else []
    return {"last": clean_last, "attempts": clean_attempts}


def read_last_wake(project_root: Path) -> dict[str, str]:
    """Le dernier réveil tenté (``at``, ``reason``, ``result``, ``origin``) ; ``{}`` s'il n'y en a pas. Lecture seule."""
    from grimoire.missions.pause import home_root

    last = (_read_state(_state_path(home_root(project_root))) or {}).get("last")
    return dict(last) if isinstance(last, dict) else {}


_RESULT_LABELS = {
    "started": "unité du tick démarrée",
    "requested": f"tick demandé dans {TICK_DELAY_S} s (minuteur transitoire créé, tick non confirmé)",
}


def describe_last_wake(project_root: Path) -> str:
    """Une ligne pour ``orchestrate status`` : le dernier réveil, ou son absence. Un tick demandé n'est pas dit « tourné »."""
    last = read_last_wake(project_root)
    if not last:
        return "réveil : aucun (le minuteur fait seul le travail)"
    label = WAKE_REASONS.get(last.get("reason", ""), last.get("reason", "?"))
    result = last.get("result", "?")
    return f"réveil : {last.get('at', '?')} — {label} — {_RESULT_LABELS.get(result, result)}"


def note_tick(project_root: Path, *, now: float | None = None) -> float | None:
    """Note que ce tick démarre et rend l'écart (secondes) avec le début du tick précédent, ``None`` s'il est inconnu.

    L'écart sert à refuser un réveil demandé par un tick qui est lui-même un tick de réveil
    (:data:`TICK_GAP_S`). Il vient de la date d'un fichier-témoin ordinaire, pas du fichier d'état des réveils : effacer
    l'un ne désarme pas l'autre. Témoin absent ou non ordinaire : écart inconnu, donc pas de réveil de tick.
    Ne lève jamais.
    """
    from grimoire.missions.pause import home_root

    mark = home_root(project_root) / TICK_MARK_RELPATH
    moment = time.time() if now is None else now
    try:
        before = mark.lstat()
        gap: float | None = moment - before.st_mtime if stat.S_ISREG(before.st_mode) else None
    except OSError:
        gap = None
    try:
        atomic_write(mark, f"{moment}\n")
        os.utime(mark, (moment, moment))
    except OSError as exc:
        logger.warning("réveil du tick : témoin du tick non écrit (%s)", exc)
        return None
    return gap


def _ordinary_or_absent(path: Path) -> bool:
    """Vrai si *path* n'existe pas ou est un fichier ordinaire (ni lien, ni FIFO, ni dossier)."""
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _binary(name: str) -> str | None:
    """Le chemin ABSOLU de l'outil, résolu une fois ; ``None`` s'il n'est pas absolu, ordinaire et exécutable."""
    found = shutil.which(name)
    if not found or not Path(found).is_absolute():
        return None
    try:
        ordinary = stat.S_ISREG(Path(found).stat().st_mode)
    except OSError:
        return None
    return found if ordinary and os.access(found, os.X_OK) else None


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
    systemctl = _binary("systemctl")
    if systemctl is None:
        return None
    start = [systemctl, "--user", "start", "--no-block", unit]
    if not delayed:
        return start
    systemd_run = _binary("systemd-run")
    if systemd_run is None:
        return None
    return [systemd_run, "--user", "--quiet", "--collect", f"--on-active={TICK_DELAY_S}s", "--", *start]


def request_tick(
    project_root: Path, reason: str, *, run: Runner | None = None, now: float | None = None,
    control_home: Path | None = None, tick_gap: float | None = None,
) -> WakeOutcome:
    """Demande un tick proche ; ne l'exécute jamais ici. Ne lève pas pour un refus : le résultat dit pourquoi.

    *run* est le lanceur d'argv (défaut : ``subprocess.run``, 2 s) ; *now* l'horloge en secondes ; *control_home*
    le dossier de contrôle de la machine (pause globale) ; *tick_gap* l'écart avec le tick précédent
    (:func:`note_tick`), exigé pour un motif de tick.

    Sens fermé pour un réveil de tick (:data:`TICK_REASONS`) : état des réveils absent ou illisible, écart de tick
    inconnu ou de moins de :data:`TICK_GAP_S` : pas de réveil. Un geste humain reste accordé et recrée l'état.
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
    if delayed and (tick_gap is None or tick_gap < TICK_GAP_S):
        return _skipped("écart avec le tick précédent inconnu ou trop court : ce tick est déjà un tick de réveil")
    moment = time.time() if now is None else now
    path = _state_path(root)
    if not all(_ordinary_or_absent(p) for p in lock_files(path)):  # `locked` ouvre ce fichier : une FIFO figerait le geste
        logger.warning("réveil du tick : fichier de verrou non ordinaire, pas de réveil")
        return _skipped("fichier de verrou non ordinaire")
    try:
        with locked(path, timeout=_LOCK_TIMEOUT_S):
            known = _read_state(path)
            if delayed and known is None:
                return _skipped("état des réveils absent ou illisible : le minuteur fait le travail")
            state = known or {}
            attempts = [a for a in state.get("attempts", []) if moment - 3600 < a <= moment]
            last_epoch = max(attempts, default=0.0)
            last = state.get("last", {})
            if last_epoch and 0 <= moment - last_epoch < DEBOUNCE_S:
                return _skipped(f"un réveil date de moins de {DEBOUNCE_S} s")
            if len(attempts) >= MAX_PER_HOUR:
                return _skipped(f"plafond de {MAX_PER_HOUR} réveils par heure atteint : le minuteur fait le travail")
            if delayed and last.get("origin") == "tick" and 0 <= moment - last_epoch < CHAIN_WINDOW_S:
                return _skipped("le dernier réveil venait déjà d'un tick : pas de chaîne")
            outcome = _start(argv, run or _run, delayed=delayed)
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


def _start(argv: list[str], run: Runner, *, delayed: bool = False) -> WakeOutcome:
    try:
        result = run(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("réveil du tick : %s a échoué (%s)", argv[0], exc)
        return WakeOutcome("failed", type(exc).__name__)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:200] or f"code {result.returncode}"
        logger.warning("réveil du tick : %s a répondu %s", argv[0], detail)
        return WakeOutcome("failed", detail)
    # Différé : le minuteur transitoire est créé, le tick n'a pas tourné et rien ne dit qu'il tournera.
    return WakeOutcome("requested" if delayed else "started")


def wake_after(
    project_root: Path, reason: str, *, tick_gap: float | None = None, budget: float = WAKE_BUDGET_S
) -> threading.Thread:
    """Le réveil en fin de geste : toute erreur est avalée et journalisée, le geste n'échoue jamais à cause de lui.

    Le réveil tourne dans un fil détaché (démon) et le geste ne lui accorde que *budget* secondes : verrou ou
    sous-processus lents, le geste rend la main et le minuteur fera le travail. Rend le fil (les tests le joignent).
    """

    def work() -> None:
        try:
            outcome = request_tick(project_root, reason, tick_gap=tick_gap)
            logger.debug("réveil du tick (%s) : %s %s", reason, outcome.status, outcome.detail)
        except Exception:  # un réveil est un confort, jamais une condition du geste
            logger.warning("réveil du tick (%s) : erreur avalée", reason, exc_info=True)

    thread = threading.Thread(target=work, name="grimoire-tick-wake", daemon=True)
    thread.start()
    thread.join(budget)
    if thread.is_alive():
        logger.warning("réveil du tick (%s) : budget de %g s dépassé, le geste rend la main", reason, budget)
    return thread


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
