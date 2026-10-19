"""Pause globale, par projet ou par tâche : ``drain`` et ``hold`` (K11, épopée #705, plan §4.4).

Avant ce lot, ``PAUSED`` n'était émis nulle part (``runtime/schemas.py``), ``admit(paused=)`` n'avait
aucun appelant qui le calcule, et aucun geste ne disait « stoppe tout » sans tuer les sessions.

Une pause est un **drapeau fichier**, lu sans ouvrir le ledger :

- ``<projet>/_grimoire-output/control/pause.json`` porte sur le projet. Avec ``tasks: [...]`` il ne porte que
  sur ces tâches ;
- ``<état utilisateur>/control/pause.json`` (``$GRIMOIRE_CONTROL_HOME``, sinon ``~/.grimoire/control``) porte
  sur tous les projets de la machine.

Le tick et le frein d'outil lisent les deux (au plus deux lectures) et appliquent l'**union** : ``hold``
l'emporte sur ``drain``. Un drapeau **illisible** (JSON invalide, permission, schéma ou mode inconnu) vaut
``drain`` à son niveau, avec une ligne d'attention : une garde qui ne sait pas lire refuse, elle ne laisse pas passer.

- ``drain`` : rien n'est admis, les agents en cours finissent leur tâche ;
- ``hold`` : en plus, sur un hôte dont le refus d'outil est prouvé (``hosts.capabilities``), le frein refuse les
  outils mutants, sauf **une** écriture, le point de reprise ``waiting(paused)``. Sur un hôte sans frein, il se
  dégrade en ``drain`` et le dit (:func:`effective_mode`).

Ce module est pur (aucun ledger, aucun appel de LLM). Les écritures passent par
:func:`grimoire.core.fsio.atomic_write` : un lecteur voit l'ancien drapeau ou le nouveau, jamais un fichier tronqué.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.fsio import atomic_write

if TYPE_CHECKING:
    from grimoire.missions.schemas import MissionTask

__all__ = [
    "CONTROL_HOME_ENV",
    "DRAIN",
    "HOLD",
    "PAUSE_LIFTED_CAUSE",
    "PAUSE_RELPATH",
    "SCOPE_GLOBAL",
    "SCOPE_PROJECT",
    "PauseError",
    "PauseFlag",
    "PauseState",
    "clear_pause",
    "effective_mode",
    "global_pause_path",
    "home_root",
    "pause_claim_guard",
    "project_pause_path",
    "read_flag",
    "read_pause",
    "write_pause",
]

#: Le drapeau de pause du projet, relatif à la racine du projet d'origine.
PAUSE_RELPATH = Path("_grimoire-output") / "control" / "pause.json"
#: Détourne la racine de l'état utilisateur (tests, isolation) ; sinon ``~/.grimoire/control``.
CONTROL_HOME_ENV = "GRIMOIRE_CONTROL_HOME"
SCHEMA = "grimoire-pause/v1"

DRAIN = "drain"
HOLD = "hold"
MODES: tuple[str, ...] = (DRAIN, HOLD)
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"

#: La cause portée par le réveil d'une tâche ``waiting(paused)`` dont la pause est levée : l'admission ne
#: lui fait pas perdre son ancienneté (:func:`grimoire.missions.admission.ready_since`).
PAUSE_LIFTED_CAUSE = "pause levée"


class PauseError(ValueError):
    """Une demande de pause ou de reprise que le kit refuse d'écrire."""


@dataclass(frozen=True, slots=True)
class PauseFlag:
    """Un drapeau lu. ``unreadable`` non vide : le fichier n'a pas pu être lu, il vaut ``drain`` pour tout son périmètre."""

    scope: str
    mode: str
    tasks: tuple[str, ...] = ()
    since: str = ""
    actor: str = ""
    reason: str = ""
    unreadable: str = ""

    def covers(self, task_id: str | None) -> bool:
        """Le drapeau porte sur *task_id* ; ``None`` interroge le périmètre du projet entier."""
        if not self.tasks:
            return True
        return task_id is not None and task_id in self.tasks

    @property
    def label(self) -> str:
        where = f"tâches {', '.join(self.tasks)}" if self.tasks else "tout le périmètre"
        origin = "machine" if self.scope == SCOPE_GLOBAL else "projet"
        suffix = f" (illisible : {self.unreadable})" if self.unreadable else ""
        return f"{self.mode} {origin}, {where}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope, "mode": self.mode, "tasks": list(self.tasks), "since": self.since,
            "actor": self.actor, "reason": self.reason, "unreadable": self.unreadable,
        }


@dataclass(frozen=True, slots=True)
class PauseState:
    """L'union des drapeaux du projet et de la machine."""

    flags: tuple[PauseFlag, ...] = ()

    def applying(self, task_id: str | None) -> tuple[PauseFlag, ...]:
        return tuple(f for f in self.flags if f.covers(task_id))

    def mode_for(self, task_id: str | None) -> str | None:
        """``hold``, ``drain`` ou ``None`` : ``hold`` l'emporte sur ``drain`` (union des deux niveaux)."""
        modes = {f.mode for f in self.applying(task_id)}
        if HOLD in modes:
            return HOLD
        return DRAIN if modes else None

    def blocks_admission(self, task_id: str | None) -> bool:
        return self.mode_for(task_id) is not None

    @property
    def global_stop(self) -> bool:
        """Un drapeau sans liste de tâches : le tick n'a rien à faire et sort."""
        return any(not f.tasks for f in self.flags)

    @property
    def active(self) -> bool:
        return bool(self.flags)

    def reason_for(self, task_id: str | None) -> str:
        """La phrase de ``Defer(pause)`` et du refus de claim : quel drapeau, quel mode."""
        flags = self.applying(task_id)
        if not flags:
            return ""
        if any(f.unreadable for f in flags) and not any(not f.unreadable for f in flags):
            return "pause illisible : traitée comme drain, aucune admission"
        return "pause demandée (" + "; ".join(f.label for f in flags) + ") : aucune admission"

    def attention(self) -> tuple[str, ...]:
        """Les lignes d'attention : un drapeau illisible se voit, il ne se devine pas."""
        return tuple(f"pause illisible ({f.scope}) : {f.unreadable}" for f in self.flags if f.unreadable)

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active, "global_stop": self.global_stop, "flags": [f.to_dict() for f in self.flags]}


def effective_mode(mode: str | None, *, fenced: bool) -> tuple[str | None, str]:
    """``(mode appliqué, mention)`` : ``hold`` sur un hôte sans frein se dégrade en ``drain`` et le dit."""
    if mode == HOLD and not fenced:
        return DRAIN, "hold dégradé en drain (hôte sans frein)"
    return mode, ""


# ── Chemins ────────────────────────────────────────────────────────────────────


def home_root(project_root: Path) -> Path:
    """La racine du projet d'origine : dans un worktree de tâche (W5-02), le drapeau est celui du projet, pas du worktree."""
    root = project_root.resolve()
    if not (root / ".git").is_file():
        return root
    from grimoire.missions.task_worktree import home_of_worktree

    home = home_of_worktree(root)
    return home[0] if home is not None else root


def project_pause_path(project_root: Path) -> Path:
    return home_root(project_root) / PAUSE_RELPATH


def global_pause_path(control_home: Path | None = None) -> Path | None:
    """Le drapeau de la machine ; ``None`` quand aucun répertoire personnel n'existe (il ne peut alors pas y avoir de drapeau)."""
    if control_home is not None:
        return control_home / "pause.json"
    env = os.environ.get(CONTROL_HOME_ENV, "").strip()
    if env:
        return Path(env).expanduser() / "pause.json"
    try:
        return Path.home() / ".grimoire" / "control" / "pause.json"
    except RuntimeError:
        return None


# ── Lecture ────────────────────────────────────────────────────────────────────


def _unreadable(scope: str, why: str) -> PauseFlag:
    return PauseFlag(scope, DRAIN, unreadable=why)


def read_flag(path: Path, scope: str) -> PauseFlag | None:
    """Le drapeau de *path* ; ``None`` seulement si le fichier n'existe pas. Tout le reste, illisible, vaut ``drain``."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        return _unreadable(scope, f"{type(exc).__name__}: {exc}")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return _unreadable(scope, f"JSON invalide ({exc})")
    if not isinstance(data, dict):
        return _unreadable(scope, "le drapeau doit être un objet JSON")
    mode = data.get("mode")
    if mode not in MODES:
        return _unreadable(scope, f"mode {mode!r} inconnu (attendu : {', '.join(MODES)})")
    tasks = data.get("tasks", [])
    if tasks is None:
        tasks = []
    if not isinstance(tasks, list) or not all(isinstance(t, str) and t.strip() for t in tasks):
        return _unreadable(scope, "tasks doit être une liste d'identifiants de tâche")

    def text(key: str) -> str:
        value = data.get(key, "")
        return value if isinstance(value, str) else ""

    return PauseFlag(
        scope, str(mode), tuple(t.strip() for t in tasks), text("since"), text("actor"), text("reason")
    )


def read_pause(project_root: Path, *, control_home: Path | None = None) -> PauseState:
    """Les drapeaux du projet et de la machine : deux lectures au plus, jamais le ledger."""
    flags: list[PauseFlag] = []
    project = read_flag(project_pause_path(project_root), SCOPE_PROJECT)
    if project is not None:
        flags.append(project)
    machine_path = global_pause_path(control_home)
    machine = read_flag(machine_path, SCOPE_GLOBAL) if machine_path is not None else None
    if machine is not None:
        flags.append(machine)
    return PauseState(tuple(flags))


# ── Écriture ───────────────────────────────────────────────────────────────────


def _target(project_root: Path, scope: str, control_home: Path | None) -> Path:
    if scope == SCOPE_PROJECT:
        return project_pause_path(project_root)
    if scope == SCOPE_GLOBAL:
        path = global_pause_path(control_home)
        if path is None:
            raise PauseError("aucun répertoire personnel : la pause globale n'a pas d'emplacement")
        return path
    raise PauseError(f"portée inconnue : {scope!r} (project ou global)")


def write_pause(
    project_root: Path,
    *,
    mode: str,
    scope: str = SCOPE_PROJECT,
    tasks: Iterable[str] = (),
    actor: str = "cli",
    reason: str = "",
    now: datetime | None = None,
    control_home: Path | None = None,
) -> PauseFlag:
    """Pose le drapeau. Une nouvelle pause de tâches s'ajoute à une pause de tâches de même mode ; sinon elle remplace.

    Une pause de tâches sous une pause de tout le périmètre est refusée si elle ne la renforce pas
    (un seul drapeau par niveau : ``drain`` projet + ``hold`` d'une tâche ne tient pas dans un fichier).
    """
    if mode not in MODES:
        raise PauseError(f"mode inconnu : {mode!r} (attendu : {', '.join(MODES)})")
    names = tuple(dict.fromkeys(t.strip() for t in tasks if t and t.strip()))
    path = _target(project_root, scope, control_home)
    existing = read_flag(path, scope)
    if existing is not None and not existing.unreadable:
        if names and not existing.tasks:
            if existing.mode == mode or existing.mode == HOLD:
                raise PauseError(
                    f"déjà couvert : une pause {existing.mode} porte sur tout le périmètre ({scope}) ; "
                    "`orchestrate resume` la lève avant d'en poser une plus fine"
                )
            raise PauseError(
                f"une pause {existing.mode} couvre déjà tout le périmètre ({scope}) : un hold de tâches n'y tient pas "
                "dans un seul drapeau, passer le périmètre entier en hold"
            )
        if names and existing.tasks and existing.mode == mode:
            names = tuple(dict.fromkeys((*existing.tasks, *names)))
    stamp = (now or datetime.now(UTC)).isoformat()
    flag = PauseFlag(scope, mode, names, stamp, actor, reason)
    payload = {
        "schema": SCHEMA, "mode": mode, "tasks": list(names), "since": stamp, "actor": actor, "reason": reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return flag


def clear_pause(
    project_root: Path,
    *,
    scope: str = SCOPE_PROJECT,
    tasks: Iterable[str] = (),
    control_home: Path | None = None,
) -> PauseFlag | None:
    """Lève la pause. Sans *tasks*, le drapeau est supprimé ; avec, ces tâches en sortent et le drapeau reste pour les autres.

    Renvoie le drapeau tel qu'il était (``None`` s'il n'y en avait pas). Un drapeau illisible se supprime : c'est
    le seul moyen de le réparer. Une reprise de tâches sur une pause de tout le périmètre est refusée.
    """
    path = _target(project_root, scope, control_home)
    existing = read_flag(path, scope)
    if existing is None:
        return None
    names = tuple(dict.fromkeys(t.strip() for t in tasks if t and t.strip()))
    if names and not existing.unreadable:
        if not existing.tasks:
            raise PauseError(
                f"la pause {existing.mode} ({scope}) porte sur tout le périmètre : la lever pour une seule tâche "
                "n'est pas possible, `orchestrate resume` sans --task"
            )
        left = tuple(t for t in existing.tasks if t not in names)
        if left:
            payload = {
                "schema": SCHEMA, "mode": existing.mode, "tasks": list(left), "since": existing.since,
                "actor": existing.actor, "reason": existing.reason,
            }
            atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            return existing
    path.unlink(missing_ok=True)
    return existing


# ── Garde de claim ─────────────────────────────────────────────────────────────


def pause_claim_guard(
    root: Path, *, control_home: Path | None = None
) -> Callable[[Mapping[str, MissionTask], MissionTask], str]:
    """La garde que le ledger appelle sous son verrou, avec celle du WIP : une pause refuse l'admission.

    Elle lit les deux drapeaux à chaque appel (jamais le ledger). Le ledger l'appelle avec la tâche telle
    qu'elle est *avant* la transition :

    - ``ready`` (un claim) : refusé sous toute pause qui la couvre, ``drain`` comme ``hold``, illisible compris ;
    - ``waiting`` (une reprise) : refusée sous ``hold``, et pour une attente ``paused`` tant que la pause dure ;
      un agent qui attendait la CI reprend sous ``drain``, il finit sa tâche ;
    - ``needs_verification`` (reprise après preuve rejetée) : refusée sous ``hold`` seulement.
    """
    from grimoire.missions.schemas import TaskState

    def guard(_tasks: Mapping[str, MissionTask], task: MissionTask) -> str:
        state = read_pause(root, control_home=control_home)
        mode = state.mode_for(task.id)
        if mode is None:
            return ""
        if task.status is TaskState.READY:
            return state.reason_for(task.id)
        if task.status is TaskState.WAITING and task.waiting is not None and task.waiting.reason == "paused":
            return state.reason_for(task.id)
        if mode == HOLD and task.status in (TaskState.WAITING, TaskState.NEEDS_VERIFICATION):
            return state.reason_for(task.id)
        return ""

    return guard
