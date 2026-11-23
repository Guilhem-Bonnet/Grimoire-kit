"""La purge des ``HOME`` jetables des workers opencode (suite de #1310, épique #1281 L4).

Chaque lancement opencode crée un ``HOME`` du kit neuf (:func:`~grimoire.missions.worker_guard.new_opencode_home`) :
``<cache>/grimoire-kit/opencode/<empreinte du projet>/<tâche>-<nonce>``. Le janitor les retire :

- **par tâche** (:func:`purge_task_homes`) : à l'état terminal, chaque dossier noté au ledger pour la tâche
  (lancement ou reprise) dont le processus est mort ;
- **orphelins** (:func:`sweep_orphan_homes`) : un dossier de la racine du projet que le ledger ne rattache à aucune
  tâche encore ouverte, sans processus vivant, et inchangé depuis :data:`ORPHAN_AGE_S` (lancement refusé par la garde,
  abandonné avant d'être noté, tâche d'avant le janitor…).

**Garde.** Rien n'est supprimé hors de la racine opencode du projet : le dossier doit en être un enfant direct au nom
d'un lancement, le chemin résolu doit y rester, et aucun lien symbolique du compte n'est admis, ni sur le chemin ni à
la place du dossier. Un worker vivant garde toujours son dossier ; une tâche en revue garde aussi le sien, la reprise
du même worker le réutilise.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from grimoire.missions.launcher import LAUNCHED_EVENT, WORKER_RESUMED_EVENT
from grimoire.missions.worker_guard import _symlink_refusal, opencode_home

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = ["ORPHAN_AGE_S", "homes_by_task", "purge_home", "purge_task_homes", "sweep_orphan_homes"]

#: Un dossier sans worker ni tâche ouverte, inchangé depuis 7 jours, est un orphelin.
ORPHAN_AGE_S = 7 * 24 * 3600.0
#: Le nom d'un dossier de lancement : ``<id de tâche assaini>-<16 chiffres hexadécimaux>``.
_LAUNCH_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}-[0-9a-f]{16}")


def homes_by_task(service: TaskService) -> dict[str, dict[str, str]]:
    """Pour chaque tâche, ses dossiers notés au ledger (lancement ou reprise) → le dernier pid qui l'a occupé."""
    found: dict[str, dict[str, str]] = {}
    for event in service.ledger.list_events():
        if event.event_type not in (LAUNCHED_EVENT, WORKER_RESUMED_EVENT):
            continue
        home = str(event.payload.get("kit_home") or "")
        if home:
            found.setdefault(event.entity_id, {})[home] = str(event.payload.get("pid") or "")
    return found


def _refusal(root: Path, home: Path) -> str:
    """``""`` si *home* est un dossier de lancement de *root* qu'on peut retirer, sinon la raison de le garder."""
    if home.parent != root or not _LAUNCH_NAME.fullmatch(home.name):
        return "hors de la racine opencode du projet"
    if refusal := _symlink_refusal(root):
        return refusal
    try:
        info = home.lstat()
    except FileNotFoundError:
        return "déjà absent"
    except OSError:
        return "illisible"
    if stat.S_ISLNK(info.st_mode):
        return "lien symbolique"
    if not stat.S_ISDIR(info.st_mode):
        return "pas un dossier"
    if home.resolve().parent != root.resolve():
        return "le chemin résolu sort de la racine opencode du projet"
    return ""


def purge_home(root: Path, home: Path) -> tuple[bool, str]:
    """Retire le dossier de lancement *home* de la racine *root* ; ``(retiré, raison si gardé)``."""
    if refusal := _refusal(root, home):
        return False, refusal
    shutil.rmtree(home)  # les liens du dossier sont retirés, jamais suivis
    return True, ""


def _alive(pid: str, alive: Callable[[int], bool]) -> bool:
    return pid.isdigit() and int(pid) > 0 and alive(int(pid))


def purge_task_homes(
    service: TaskService,
    task_id: str,
    alive: Callable[[int], bool],
    *,
    source: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """L'action ``opencode_home`` du janitor : ``(statut, détail)`` pour les dossiers de lancement de *task_id*.

    ``failed`` tant qu'un worker vit encore (rejoué au tick suivant) ; un dossier hors de la racine ou un lien
    symbolique est gardé et dit (``skipped``) : ce n'est pas au janitor de le retirer.
    """
    homes = homes_by_task(service).get(task_id, {})
    if not homes:
        return "skipped", "aucun HOME opencode noté"
    root = opencode_home(service.home_root, source=source)
    removed: list[str] = []
    kept: list[str] = []
    living: list[str] = []
    for home, pid in homes.items():
        if _alive(pid, alive):
            living.append(f"{Path(home).name} (pid {pid})")
            continue
        try:
            done, why = purge_home(root, Path(home))
        except OSError as exc:
            return "failed", f"HOME opencode {Path(home).name} non retiré : {type(exc).__name__}"
        if done:
            removed.append(Path(home).name)
        elif why != "déjà absent":
            kept.append(f"{home} ({why})")
    if living:
        return "failed", "worker opencode encore vivant, HOME conservé : " + ", ".join(living)
    if kept:
        return "skipped", "HOME opencode conservé : " + ", ".join(kept)
    if not removed:
        return "skipped", "HOME opencode déjà absent"
    return "done", "HOME opencode retiré : " + ", ".join(removed)


def _last_change(home: Path) -> float:
    """La dernière modification du dossier ou de ses entrées directes (sans suivre de lien)."""
    latest = home.lstat().st_mtime
    with os.scandir(home) as entries:
        for entry in entries:
            try:
                latest = max(latest, entry.stat(follow_symlinks=False).st_mtime)
            except OSError:
                continue
    return latest


def sweep_orphan_homes(
    service: TaskService,
    open_tasks: Iterable[str],
    alive: Callable[[int], bool],
    *,
    now: float | None = None,
    source: Mapping[str, str] | None = None,
) -> list[str]:
    """Retire les dossiers orphelins de la racine opencode du projet ; rend les noms retirés.

    Gardé : un dossier noté pour une tâche de *open_tasks* (sa reprise le réutilise), un dossier dont le dernier pid
    vit, un dossier changé depuis moins de :data:`ORPHAN_AGE_S`, et tout ce que :func:`purge_home` refuse.
    """
    root = opencode_home(service.home_root, source=source)
    if _symlink_refusal(root) or not root.is_dir() or root.is_symlink():
        return []
    opened = set(open_tasks)
    held: set[str] = set()
    for tid, homes in homes_by_task(service).items():
        for home, pid in homes.items():
            if tid in opened or _alive(pid, alive):
                held.add(Path(home).name)
    clock = time.time() if now is None else now
    removed: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name in held or _refusal(root, entry):
            continue
        try:
            if clock - _last_change(entry) < ORPHAN_AGE_S:
                continue
            done, _why = purge_home(root, entry)
        except OSError:
            continue
        if done:
            removed.append(entry.name)
    return removed
