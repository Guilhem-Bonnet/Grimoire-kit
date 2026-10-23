"""Le tick d'orchestration : pause, récolte, réveils, effets réconciliés, admission (K11 et K12, épopée #705, §4.4-§4.6).

Le tick n'est pas résident : c'est une fonction qu'un déclencheur appelle (CLI, session, minuteur
:mod:`.timer`). Il ne fait aucun appel de LLM. Sa première lecture est celle des drapeaux de pause
(:mod:`.pause`), **avant** d'ouvrir le ledger : sous une pause qui porte sur tout le périmètre, le tick sort
sans avoir chargé le ledger ni rien dispatché. Les échéances ``waiting`` continuent de courir (une date reste
une date), mais personne ne les rend tant que la pause dure ; la sortie le dit.

Une pause qui ne porte que sur des tâches nommées laisse le tick tourner : ces tâches sont différées à
l'admission et leurs attentes ``paused`` ne sont pas rendues, les autres suivent leur chemin.

Hors pause, le tick récolte les baux perdus, rend les attentes levées (dont les ``waiting(paused)`` dont la
pause a pris fin, dans l'ordre de la file), constate sur la remote les effets externes restés sans trace
(:mod:`.effects`), puis calcule le plan d'admission. Le dispatch est un point d'accroche (*dispatcher*),
appelé avec les seules tâches admises.

K12 — **idempotent et rejouable** :

- un seul tick écrit à la fois : ``_grimoire-output/control/.tick.lock`` (:func:`grimoire.core.fsio.locked`,
  sans attente). Un second tick qui le trouve pris saute (``busy``, code 0 : un minuteur ne doit pas le lire
  comme une panne). Avec ``flock`` (POSIX), un tick tué rend le verrou avec son processus. Sans ``flock``
  (Windows), le repli est un fichier dont la date fait foi : le tick la rafraîchit tant qu'il tourne
  (``keepalive``), pour qu'un tick de plus de 60 s ne soit pas cassé par un second ; un tick tué laisse ce
  fichier jusqu'à sa péremption (60 s, ou son pid mort sur un POSIX sans ``fcntl``). Le suivant reprend :
  chaque étape relit le ledger et ne réécrit pas ce qui y est déjà (récolte à barrière, différée unique,
  réveil rejoué) ;
- le plan à blanc (``apply=False``, ``orchestrate tick --plan``) **est** le tick appliqué, joué sur une copie
  du ledger : mêmes récoltes, mêmes réveils, et une admission qui voit les tâches que ces gestes rendent.
  Avant K12, l'aperçu calculait l'admission sur le ledger d'avant les gestes : une tâche réveillée n'y était
  pas admise, alors que le tick appliqué l'admettait. Ni board, ni mémoire, ni dispatch ne bougent en plan ;
- un journal aux lignes tronquées (coupure de courant) est rejoué sur ses lignes lisibles, et le tick le dit.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import locked
from grimoire.missions.lease import LEASES_DIRNAME
from grimoire.missions.pause import PAUSE_RELPATH, PauseState, home_root, read_pause

if TYPE_CHECKING:
    from grimoire.missions.admission import Admit, QueuePlan
    from grimoire.missions.effects import EffectOutcome, Probe
    from grimoire.missions.service import TaskService, WakeDecision

__all__ = ["TICK_LOCK_RELPATH", "TickReport", "run_tick"]

#: Le verrou du tick (``fsio`` pose ``_grimoire-output/control/.tick.lock``) : un seul tick écrit à la fois, le second saute.
TICK_LOCK_RELPATH = PAUSE_RELPATH.parent / "tick"
_PLAN_PREFIX = "tick-plan-"
#: Une copie de plan plus vieille que cela vient d'un plan tué : le tick suivant la retire.
_STALE_PLAN_S = 3600.0


@dataclass(frozen=True, slots=True)
class TickReport:
    """Ce qu'un tick a fait. ``status`` : ``paused`` (sorti sur un drapeau), ``idle`` (aucun ledger),
    ``busy`` (un autre tick tient le verrou) ou ``ran``. ``mode`` : ``apply`` ou ``plan`` (joué sur une copie)."""

    status: str
    pause: PauseState
    attention: tuple[str, ...] = ()
    woken: tuple[WakeDecision, ...] = ()
    reaped: tuple[Any, ...] = ()
    plan: QueuePlan | None = None
    dispatched: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    effects: tuple[EffectOutcome, ...] = ()
    mode: str = "apply"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "pause": self.pause.to_dict(),
            "attention": list(self.attention),
            "notes": list(self.notes),
            "woken": [d.to_dict() for d in self.woken],
            "reaped": [d.to_dict() if hasattr(d, "to_dict") else str(d) for d in self.reaped],
            "effects": [e.to_dict() for e in self.effects],
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "dispatched": list(self.dispatched),
        }


def run_tick(
    project_root: Path,
    *,
    ledger_root: Path | None = None,
    apply: bool = True,
    now: datetime | None = None,
    service_factory: Callable[..., TaskService] | None = None,
    dispatcher: Callable[[tuple[Admit, ...]], None] | None = None,
    control_home: Path | None = None,
    effect_probe: Probe | None = None,
) -> TickReport:
    """Un tick. Sous une pause de tout le périmètre : les drapeaux sont lus, le ledger ne l'est pas, rien n'est dispatché.

    *service_factory* est appelé avec ``(project_root[, ledger_root])`` pour ouvrir le service : c'est ce qu'une
    pause évite. *dispatcher* reçoit les tâches admises, jamais sous pause et jamais en plan (``apply=False``).
    *effect_probe* vérifie un effet sur la remote (défaut : ``git ls-remote``, ``gh pr list``).
    """
    state = read_pause(home_root(project_root), control_home=control_home)
    if state.global_stop:
        return TickReport(
            "paused", state, state.attention(),
            notes=("les échéances waiting continuent de courir ; elles ne sont pas rendues tant que la pause dure",),
        )

    if service_factory is None:
        from grimoire.missions.service import TaskService

        service_factory = TaskService
    service = service_factory(project_root) if ledger_root is None else service_factory(project_root, ledger_root)
    if not service.has_ledger:
        return TickReport("idle", state, state.attention(), notes=("aucun ledger : rien à faire",))
    if not apply:
        return _planned(service_factory, service, state, now, effect_probe)

    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(locked(service.home_root / TICK_LOCK_RELPATH, timeout=0, keepalive=True))
        except TimeoutError:
            return TickReport(
                "busy", state, state.attention(),
                notes=("un autre tick tient le verrou du tick : celui-ci saute sans rien écrire ; le suivant reprendra",),
            )
        except OSError as exc:
            raise GrimoireMissionError(
                f"Tick refusé : le verrou {TICK_LOCK_RELPATH} n'a pas pu être pris ({exc}) — "
                "sans lui, deux ticks pourraient récolter la même tâche"
            ) from exc
        _sweep_stale_plans(service.home_root / TICK_LOCK_RELPATH.parent)
        return _one_pass(service, state, now, dispatcher, effect_probe, mode="apply")


def _one_pass(
    service: TaskService,
    state: PauseState,
    now: datetime | None,
    dispatcher: Callable[[tuple[Admit, ...]], None] | None,
    effect_probe: Probe | None,
    *,
    mode: str,
) -> TickReport:
    """Récolte, réveils, effets, admission — les mêmes gestes en plan (sur la copie) et appliqués."""
    from grimoire.missions.effects import reconcile

    report = service.reap(apply=True, now=now, probe_hosts=mode == "apply")
    woken = tuple(service.wake_due(apply=True, now=now, pause=state))
    effects = tuple(reconcile(service, apply=True, probe=effect_probe))
    plan = service.queue_plan(now=now, pause=state)
    dispatched: tuple[str, ...] = ()
    if mode == "apply" and dispatcher is not None and plan.admitted and not plan.capacity.errors:
        dispatcher(plan.admitted)
        dispatched = tuple(a.task_id for a in plan.admitted)
    attention = [*state.attention()]
    if report.clock == "unknown":
        attention.append("horloge incohérente (plus tôt que le dernier événement du ledger) : rien n'est récolté")
    if unreadable := service.ledger.unreadable_lines():
        shown = ", ".join(str(n) for n in unreadable[:5]) + ("…" if len(unreadable) > 5 else "")
        attention.append(
            f"ledger : {len(unreadable)} ligne(s) illisible(s) ignorée(s) au rejeu (n° {shown}) — coupure probable ; "
            "l'état est celui des lignes lisibles"
        )
    for outcome in effects:
        if outcome.status != "reconciled":
            what = "non constaté sur la remote : son job le refera, une fois" if outcome.status == "absent" else "invérifiable"
            attention.append(f"effet {outcome.effect.label()} de {outcome.effect.task_id} {what} ({outcome.evidence})")
    notes = tuple(f"{t} : en pause ({state.mode_for(t)})" for t in sorted({tid for f in state.flags for tid in f.tasks}))
    return TickReport(
        "ran", state, tuple(attention), woken, tuple(report.decisions), plan, dispatched, notes, effects, mode
    )


def _planned(
    factory: Callable[..., TaskService],
    service: TaskService,
    state: PauseState,
    now: datetime | None,
    effect_probe: Probe | None,
) -> TickReport:
    """Le tick appliqué, sur une copie du ledger sous ``_grimoire-output/control/`` (jamais sous ``/tmp``), puis retirée."""
    parent = service.home_root / TICK_LOCK_RELPATH.parent
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=_PLAN_PREFIX, dir=parent))
    try:
        shadow_root = scratch / "ledger"
        _copy_ledger(service.ledger_root, shadow_root)
        shadow = factory(service.home_root, shadow_root)
        shadow.writes_projections = False
        return _one_pass(shadow, state, now, None, effect_probe, mode="plan")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _copy_ledger(source: Path, dest: Path) -> None:
    """Journal, incidents et fichiers de bail, copiés sous le verrou du journal : un instantané cohérent."""
    dest.mkdir(parents=True)
    events = source / "events.jsonl"
    try:
        with locked(events, timeout=30):
            for name in ("events.jsonl", "incidents.jsonl"):
                if (source / name).is_file():
                    shutil.copyfile(source / name, dest / name)
            if (source / LEASES_DIRNAME).is_dir():
                shutil.copytree(source / LEASES_DIRNAME, dest / LEASES_DIRNAME, ignore=shutil.ignore_patterns(".*"))
    except (TimeoutError, OSError) as exc:
        raise GrimoireMissionError(f"Plan refusé : le ledger n'a pas pu être copié sous son verrou ({exc})") from exc


def _sweep_stale_plans(control: Path) -> None:
    """Retire les copies laissées par un plan tué (plus vieilles que :data:`_STALE_PLAN_S`), sous le verrou du tick."""
    if not control.is_dir():
        return
    limit = time.time() - _STALE_PLAN_S
    for leftover in control.glob(f"{_PLAN_PREFIX}*"):
        with contextlib.suppress(OSError):
            if leftover.is_dir() and leftover.stat().st_mtime < limit:
                shutil.rmtree(leftover, ignore_errors=True)
