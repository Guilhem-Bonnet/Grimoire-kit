"""Where a project stands in the governed standard.

Three questions a lifecycle hook must answer before it can decide anything:
is this project enrolled at all, under which profile, and which task is the
work charged to. They live here rather than in
:mod:`grimoire.core.agentic_standard` so that the hook path — run on every tool
call — imports a small module instead of the whole standard engine.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Tout vient de ``standard_generation``, le module léger : ce lecteur tourne dans
# le chemin des hooks, à chaque appel d'outil, et importer le moteur du standard
# pour lire deux chemins et un identifiant coûtait 48 ms par appel.
from grimoire.core.standard_generation import (
    STANDARD_DIR,
    STANDARD_PROFILE_FILE,
    normalize_task_id,
)

TASK_BOARD_RELPATH = STANDARD_DIR / "task-board.yaml"

#: Instantané dérivé de ``standard-profile.yaml``/``task-board.yaml``, en JSON —
#: jamais en YAML : ``ruamel.yaml`` coûte ~8-9 ms à sa première charge dans un
#: process, sur le chemin du hook déclenché à *chaque* appel d'outil (issue
#: #419, second lot). ``_grimoire-output/.runs/`` est déjà l'emplacement de
#: l'état de session éphémère et gitignoré ; ce cache y vit au même titre.
_CACHE_RELPATH = Path("_grimoire-output") / ".runs" / "standard-state-cache.json"
_CACHE_VERSION = 1


def _stat_fingerprint(path: Path) -> list[int] | None:
    """``[mtime_ns, size]`` pour *path*, ou ``None`` si absent/illisible.

    L'empreinte, pas le contenu : un ``stat`` coûte un ordre de grandeur de
    moins qu'un parse YAML, et suffit à détecter un fichier changé entre deux
    appels de hook.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def _read_cache_entries(root: Path) -> dict[str, Any]:
    """La table ``entries`` du cache, ou ``{}`` — jamais une exception.

    Un cache absent, tronqué, au mauvais format JSON ou d'une version
    inconnue n'est jamais une erreur au sens du hook : c'est juste une
    absence, qui retombe sur la relecture YAML.
    """
    path = root / _CACHE_RELPATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _write_cache_entries(root: Path, entries: dict[str, Any]) -> None:
    """Écriture atomique (fichier temporaire + ``os.replace``) — best-effort.

    Un cache qui échoue à s'écrire ne doit jamais faire échouer un hook : la
    prochaine invocation retombera simplement sur la relecture YAML, comme si
    le cache n'existait pas.
    """
    path = root / _CACHE_RELPATH
    payload = json.dumps({"version": _CACHE_VERSION, "entries": entries}, sort_keys=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _cached_or_fresh(
    root: Path, key: str, relpath: Path, compute: Callable[[], Any], *, write_cache: bool = True
) -> Any:
    """La valeur en cache pour *relpath* si son empreinte tient toujours, sinon fraîche.

    Une absence, une corruption ou un fichier source modifié retombent toutes
    sur *compute* (qui lit le YAML pour de vrai). Le résultat frais rafraîchit
    le cache sur disque sauf si *write_cache* est ``False`` — un appelant qui a
    promis de ne rien écrire (un outil MCP annoté lecture seule, par exemple)
    lit le cache s'il est chaud mais ne le crée ni ne le corrige jamais lui-même,
    laissant ce travail au prochain appelant qui, lui, écrit. Le
    rafraîchissement lui-même reste best-effort : voir :func:`_write_cache_entries`.
    """
    fingerprint = _stat_fingerprint(root / relpath)
    entries = _read_cache_entries(root)
    cached = entries.get(key)
    if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint and "value" in cached:
        return cached["value"]
    value = compute()
    if write_cache:
        entries[key] = {"path": str(relpath), "fingerprint": fingerprint, "value": value}
        _write_cache_entries(root, entries)
    return value


def invalidate_cache(project_root: Path) -> None:
    """Purge le cache d'état du standard — à appeler juste après avoir écrit le YAML qu'il dérive.

    Pas requis pour la correction : l'empreinte périmée régénère déjà un
    cache obsolète au prochain appel de hook. Mais sans cet appel, c'est cet
    appel *suivant* qui verrait le changement — celui qui vient de l'écrire
    ne le verrait pas avant. Best-effort, comme le reste du cache : un échec
    de suppression est rattrapé par le mismatch d'empreinte.
    """
    path = project_root.resolve() / _CACHE_RELPATH
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _load_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML mapping, or an empty one — never raise at a hook boundary.

    ``ruamel.yaml`` is imported here, not at module scope: a warm cache means
    neither this function nor the parser it needs ever runs, and the whole
    point of the cache is to keep ``ruamel`` off that path.
    """
    if not path.is_file():
        return {}
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(path)
    except (OSError, ValueError, YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def task_from_board(board: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    """The board entry for *task_id*, or an empty mapping when it has none."""
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list):
        return {}
    for task in tasks:
        if isinstance(task, dict) and str(task.get("task_id", "")) == task_id:
            return task
    return {}


def board_omits_task(project_root: Path, task: Mapping[str, Any]) -> bool:
    """True when a board exists and does not declare the task being checked.

    The distinction matters at the evidence gate. Every gate requirement is
    indexed on a named board state, so a task the board ignores has an empty
    state and owes nothing: the gate answers ``ok`` on an identifier that does
    not exist, and a typo in a hook or a CI job turns the gate decorative.

    A project with no board at all is a different case, deliberately left
    alone: the ``starter`` profile generates none, and the Stop hook relies on
    an unknown state owing no evidence (see ``_STATES_WITHOUT_EVIDENCE`` in
    :mod:`grimoire.hosts.decisions`).
    """
    if task:
        return False
    return (project_root / TASK_BOARD_RELPATH).is_file()


def is_standard_enrolled(project_root: Path) -> bool:
    """True when *project_root* carries generated standard artifacts.

    Lifecycle hooks that fail closed on red gates must not be installed — nor
    fire — on a project that has no gates: they would block every closure on
    an absence. Callers gate on this.
    """
    root = project_root.resolve()
    return (root / STANDARD_PROFILE_FILE).is_file() or (root / TASK_BOARD_RELPATH).is_file()


def active_profile_id(project_root: Path, *, write_cache: bool = True) -> str:
    """Profile a project is enrolled in, ``starter`` when it declares none.

    *write_cache* defaults to ``True`` — the lifecycle hook path (and the CLI)
    both want the derived JSON cache written back so the *next* call is
    cheap. A caller that has promised no side effects at all (an MCP tool
    annotated ``readOnlyHint``) passes ``False``: it still benefits from a
    cache another caller warmed, it just never creates or repairs one itself.
    """
    root = project_root.resolve()

    def compute() -> str:
        profile = _load_mapping(root / STANDARD_PROFILE_FILE).get("profile")
        return str(profile) if profile else "starter"

    return str(_cached_or_fresh(root, "profile_id", STANDARD_PROFILE_FILE, compute, write_cache=write_cache))


#: Le Mission Ledger, source des tâches (ADR-005). Lu ici sans importer le
#: module missions tant que le fichier n'existe pas : le chemin des hooks reste
#: léger sur un projet qui n'a jamais ouvert de tâche.
LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
#: Un opérateur ou un agent dit qui il est ; les claims des autres ne comptent plus.
ACTOR_ENV = "GRIMOIRE_ACTOR"
TASK_ENV = "GRIMOIRE_TASK_ID"


@dataclass(frozen=True, slots=True)
class ActiveTask:
    """La tâche qu'une session porte, et d'où la réponse vient.

    ``source`` vaut ``env`` (``GRIMOIRE_TASK_ID``), ``ledger_claim`` (claim actif
    du Mission Ledger), ``board`` (unique carte ``in_progress`` du board) ou
    ``bootstrap`` (rien ne désigne de tâche). Une réponse qui ne dit pas d'où
    elle vient ne se vérifie pas.
    """

    task_id: str
    source: str


def claimed_task_ids(project_root: Path, *, actor: str = "") -> list[str]:
    """Tâches ``claimed`` ou ``running`` du ledger — celles de *actor* seulement s'il est nommé.

    Ne lève jamais : un ledger illisible vaut « aucun claim », et la résolution
    continue sur le board.
    """
    events = project_root.resolve() / LEDGER_RELPATH / "events.jsonl"
    if not events.is_file():
        return []
    try:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.schemas import TaskState

        tasks = MissionLedger(events.parent).list_tasks()
    except Exception:  # frontière de hook : ne jamais casser une session
        return []
    active = [t for t in tasks if t.status in (TaskState.CLAIMED, TaskState.RUNNING)]
    if actor:
        active = [t for t in active if t.claim is not None and t.claim.actor_id == actor]
    return [t.id for t in active]


def resolve_active_task(
    project_root: Path, *, env: Mapping[str, str] | None = None, write_cache: bool = True
) -> ActiveTask:
    """Task a lifecycle hook should evaluate, with the rule that chose it.

    Resolution order:

    1. ``GRIMOIRE_TASK_ID`` — an operator saying which task this session is about.
    2. The Mission Ledger's active claim: the single ``claimed``/``running``
       task, restricted to ``GRIMOIRE_ACTOR``'s claims when that is set. The
       ledger is the source (ADR-005); a claim is visible here the moment it is
       written, whether or not the board has been re-projected since.
    3. The board's single ``in_progress`` task — a project whose board was
       written by hand, or imported, and has no ledger.
    4. ``bootstrap``.

    Two concurrent claims (or two in-progress cards) are ambiguous: that level
    is skipped rather than guessed at, and ``GRIMOIRE_TASK_ID`` decides.

    *write_cache* — see :func:`active_profile_id`: ``False`` for a caller that
    must never write anything (a ``readOnlyHint`` MCP tool).
    """
    environ = os.environ if env is None else env
    override = str(environ.get(TASK_ENV, "")).strip()
    if override:
        return ActiveTask(normalize_task_id(override), "env")

    claimed = claimed_task_ids(project_root, actor=str(environ.get(ACTOR_ENV, "")).strip())
    if len(claimed) == 1:
        try:
            return ActiveTask(normalize_task_id(claimed[0]), "ledger_claim")
        except ValueError:
            pass

    root = project_root.resolve()

    def compute() -> list[str]:
        tasks = _load_mapping(root / TASK_BOARD_RELPATH).get("tasks")
        if not isinstance(tasks, list):
            return []
        return [
            str(task.get("task_id", ""))
            for task in tasks
            if isinstance(task, dict) and str(task.get("status", "")) == "in_progress" and task.get("task_id")
        ]

    in_progress = _cached_or_fresh(
        root, "board_in_progress", TASK_BOARD_RELPATH, compute, write_cache=write_cache
    )
    if len(in_progress) == 1:
        return ActiveTask(normalize_task_id(in_progress[0]), "board")
    return ActiveTask("bootstrap", "bootstrap")


def active_task_id(project_root: Path, *, env: Mapping[str, str] | None = None, write_cache: bool = True) -> str:
    """Task a lifecycle hook should evaluate — see :func:`resolve_active_task`."""
    return resolve_active_task(project_root, env=env, write_cache=write_cache).task_id
