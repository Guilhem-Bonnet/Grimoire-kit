"""Where a project stands in the governed standard.

Three questions a lifecycle hook must answer before it can decide anything:
is this project enrolled at all, under which profile, and which task is the
work charged to. They live here rather than in
:mod:`grimoire.core.agentic_standard` so that the hook path — run on every tool
call — imports a small module instead of the whole standard engine.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from grimoire.bridges.schemas import HostId

# Tout vient de ``standard_generation``, le module léger : ce lecteur tourne dans
# le chemin des hooks, à chaque appel d'outil, et importer le moteur du standard
# pour lire deux chemins et un identifiant coûtait 48 ms par appel.
from grimoire.core.standard_generation import (
    PROFILE_FLOOR_FILE,
    PROFILE_LADDER,
    STANDARD_DIR,
    STANDARD_PROFILE_FILE,
    normalize_task_id,
)
from grimoire.core.yaml_io import load_yaml_mapping_or_empty, load_yaml_with_retry

TASK_BOARD_RELPATH = STANDARD_DIR / "task-board.yaml"

#: Instantané dérivé de ``standard-profile.yaml``/``task-board.yaml``, en JSON —
#: jamais en YAML : ``ruamel.yaml`` coûte ~8-9 ms à sa première charge dans un
#: process, sur le chemin du hook déclenché à *chaque* appel d'outil (issue
#: #419, second lot). ``_grimoire-output/.runs/`` est déjà l'emplacement de
#: l'état de session éphémère et gitignoré ; ce cache y vit au même titre.
_CACHE_RELPATH = Path("_grimoire-output") / ".runs" / "standard-state-cache.json"
_CACHE_VERSION = 1


_WRITES_SUSPENDED: contextvars.ContextVar[bool] = contextvars.ContextVar("grimoire_state_writes_suspended", default=False)


@contextlib.contextmanager
def suspended_state_writes() -> Iterator[None]:
    """Le temps du bloc, aucune écriture d'état de hook (cache, holds) : lecture seule.

    Pour un hook lancé hors de toute racine de projet reconnue : la décision se
    prend, mais il n'y a pas de projet auquel rattacher du cache ni un journal.
    """
    token = _WRITES_SUSPENDED.set(True)
    try:
        yield
    finally:
        _WRITES_SUSPENDED.reset(token)


def state_writes_allowed() -> bool:
    """``False`` sous :func:`suspended_state_writes`."""
    return not _WRITES_SUSPENDED.get()


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
    if write_cache and state_writes_allowed():
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

    Une erreur d'analyse est retentée avant d'être tenue pour définitive
    (:mod:`grimoire.core.yaml_io`) : le board peut être en cours de
    remplacement par un écrivain d'une version antérieure du kit, resté non
    atomique. Ce qui échoue encore vaut ``{}`` ici, mais n'est pas tu : la
    résolution de tâche le rend « illisible » (``_board_problem``) plutôt que
    « aucune tâche ».

    ``ruamel`` est importé par :mod:`grimoire.core.yaml_io` à l'appel, pas au
    chargement des modules : un cache chaud ne paie aucun parseur, et le
    chemin du hook reste léger.
    """
    return load_yaml_mapping_or_empty(path)


def _board_problem(path: Path) -> str:
    """Pourquoi *path* (le board) ne se lit pas, ``""`` s'il est absent ou lisible.

    Un board absent est légitime (ledger seul) ; un board présent dont le YAML
    est cassé, ou dont la racine n'est pas un mapping, ne l'est pas — et
    :func:`_load_mapping`, qui ne lève jamais, le rend comme un board vide.
    """
    if not path.is_file():
        return _not_a_file(path, f"board {TASK_BOARD_RELPATH.as_posix()}")
    from ruamel.yaml.error import YAMLError

    name = f"board {TASK_BOARD_RELPATH.as_posix()}"
    try:
        data = load_yaml_with_retry(path)
    except (OSError, ValueError, YAMLError) as exc:
        return f"{name} illisible ({type(exc).__name__})"
    if data is None:
        return ""
    if not isinstance(data, dict):
        return f"{name} illisible (la racine n'est pas un mapping)"
    tasks = data.get("tasks")
    if tasks is None:
        return ""
    if not isinstance(tasks, list):
        return f"{name} illisible (`tasks:` doit être une liste)"
    if any(not isinstance(task, dict) for task in tasks):
        return f"{name} illisible (une entrée de `tasks:` n'est pas un mapping)"
    return ""


def _not_a_file(path: Path, name: str) -> str:
    """``""`` si *path* est absent (légitime), sinon pourquoi un chemin présent n'est pas un fichier régulier."""
    if path.exists() or path.is_symlink():
        return f"{name} illisible (dossier ou lien cassé au lieu d'un fichier)"
    return ""


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


def declared_profile_id(project_root: Path, *, write_cache: bool = True) -> str:
    """Le profil que ``standard-profile.yaml`` déclare, ``starter`` à défaut — plancher non appliqué."""
    root = project_root.resolve()

    def compute() -> str:
        profile = _load_mapping(root / STANDARD_PROFILE_FILE).get("profile")
        return str(profile) if profile else "starter"

    return str(_cached_or_fresh(root, "profile_id", STANDARD_PROFILE_FILE, compute, write_cache=write_cache))


def profile_floor_id(project_root: Path, *, write_cache: bool = True) -> str:
    """Le plancher committé (``profile-floor.yaml``, clé ``floor``), ``""`` sans fichier (W2-07).

    Un fichier présent dont la valeur n'est pas un profil connu (YAML cassé,
    clé absente, faute de frappe) vaut ``governed`` : un plancher qu'on ne sait
    pas lire ne doit pas se lire comme « pas de plancher » (fail-closed).

    Relu à chaque appel, jamais mis en cache : l'empreinte mtime+taille du cache
    se recopie, et un ``Write`` du cache suffisait à effacer un plancher qui
    promettait de tenir face aux écritures. Le fichier est petit ; le coût est un
    ``stat`` quand il est absent et un parse YAML quand il est là (non mesuré).
    *write_cache* est conservé pour la symétrie des signatures et n'a plus d'effet.
    """
    del write_cache
    path = project_root.resolve() / PROFILE_FLOOR_FILE
    if not path.is_file():
        return ""
    floor = _load_mapping(path).get("floor")
    return str(floor) if isinstance(floor, str) and floor in PROFILE_LADDER else "governed"


def profile_below_floor(declared: str, floor: str) -> bool:
    """Vrai quand *floor* existe et que *declared* est strictement plus léger (ou inconnu)."""
    if not floor:
        return False
    rank = PROFILE_LADDER.index(declared) if declared in PROFILE_LADDER else -1
    return rank < PROFILE_LADDER.index(floor)


def active_profile_id(project_root: Path, *, write_cache: bool = True) -> str:
    """Profile a project is enrolled in, ``starter`` when it declares none.

    Le plancher committé (:func:`profile_floor_id`, W2-07) s'applique ici : un
    profil déclaré sous le plancher est ignoré, le plancher le remplace. Tous
    les lecteurs (hooks, CLI, MCP, cockpit) voient donc le même profil.

    *write_cache* defaults to ``True`` — the lifecycle hook path (and the CLI)
    both want the derived JSON cache written back so the *next* call is
    cheap. A caller that has promised no side effects at all (an MCP tool
    annotated ``readOnlyHint``) passes ``False``: it still benefits from a
    cache another caller warmed, it just never creates or repairs one itself.
    """
    declared = declared_profile_id(project_root, write_cache=write_cache)
    floor = profile_floor_id(project_root, write_cache=write_cache)
    return floor if profile_below_floor(declared, floor) else declared


#: Le Mission Ledger, source des tâches (ADR-005). Lu ici sans importer le
#: module missions tant que le fichier n'existe pas : le chemin des hooks reste
#: léger sur un projet qui n'a jamais ouvert de tâche.
LEDGER_RELPATH = Path("_grimoire-runtime-output/ledger")
#: Début du problème d'un ledger que le marqueur d'un worktree de tâche désigne sans qu'on puisse le lire
#: (#1259) : marqueur illisible, ou ledger désigné sans ``events.jsonl``. Les décisions le reconnaissent à ce préfixe.
DESIGNATED_LEDGER_PROBLEM = "ledger du projet d'origine invérifiable"


@dataclass(frozen=True, slots=True)
class LedgerLocation:
    """Où lire le Mission Ledger d'une racine de hook (:func:`ledger_location`).

    ``root`` vaut ``None`` quand le ledger désigné est invérifiable : ``problem`` dit pourquoi, et
    l'appelant n'a *aucun* ledger à lire — surtout pas la copie restée dans le worktree.
    """

    root: Path | None
    marked: bool = False
    problem: str = ""


def designated_ledger(project_root: Path) -> tuple[Path, bool]:
    """``(ledger, désigné par un marqueur)`` : le ledger que :class:`~grimoire.missions.service.TaskService` ouvre.

    Dans un worktree de tâche (W5-02), le marqueur du dossier git désigne le ledger du projet
    d'origine : claim, renouvellement, rattachement, consignes et questions y sont écrits.
    Ailleurs, ``<racine>/_grimoire-runtime-output/ledger``. Un marqueur illisible lève
    (:class:`~grimoire.core.exceptions.GrimoireMissionError`) : jamais de repli sur la copie locale.
    """
    root = project_root.resolve()
    if (root / ".git").is_file():
        from grimoire.missions.task_worktree import home_of_worktree

        home = home_of_worktree(root)
        if home is not None:
            return home[1], True
    return root / LEDGER_RELPATH, False


def ledger_location(project_root: Path) -> LedgerLocation:
    """:func:`designated_ledger` sans exception, pour les décisions de hook (#1259).

    Hors worktree de tâche, un ledger absent reste légitime (projet qui n'a jamais ouvert de
    tâche) : ``root`` est rendu, l'appelant teste ``events.jsonl`` comme avant. Désigné par un
    marqueur, il ne l'est plus : marqueur illisible ou ledger sans ``events.jsonl`` (projet
    déplacé, ``git worktree repair``, ledger supprimé) rendent ``root=None`` et le problème.
    """
    from grimoire.core.exceptions import GrimoireMissionError

    try:
        root, marked = designated_ledger(project_root)
    except GrimoireMissionError as exc:
        return LedgerLocation(None, True, f"{DESIGNATED_LEDGER_PROBLEM} : marqueur du worktree de tâche illisible ({exc})")
    if marked and not (root / "events.jsonl").is_file():
        return LedgerLocation(
            None,
            True,
            f"{DESIGNATED_LEDGER_PROBLEM} : {root / 'events.jsonl'} absent, alors que le marqueur du worktree "
            "de tâche le désigne (projet déplacé, worktree réparé, ledger supprimé ?)",
        )
    return LedgerLocation(root, marked)
#: Un opérateur ou un agent dit qui il est ; les claims des autres ne comptent plus.
ACTOR_ENV = "GRIMOIRE_ACTOR"
TASK_ENV = "GRIMOIRE_TASK_ID"
#: Identifiant de la session d'hôte, quand l'environnement le porte : posé par
#: l'opérateur (``GRIMOIRE_SESSION_ID``) ou exporté par Claude Code
#: (``CLAUDE_CODE_SESSION_ID``) aux commandes que la session lance.
SESSION_ENV = "GRIMOIRE_SESSION_ID"
#: variable d'environnement exportée par l'hôte → identifiant d'hôte (``HookInput.host``).
HOST_SESSION_ENVS = {"CLAUDE_CODE_SESSION_ID": HostId.CLAUDE_CODE_CLI.value}
#: Exécution déclarée sans humain (le même drapeau que ``hosts.decisions.human_turns.UNATTENDED_ENV``, relu ici sans
#: importer ce module) : la variable d'hôte y prime sur ``GRIMOIRE_SESSION_ID`` (#966, :func:`current_session`).
_UNATTENDED_ENV = "GRIMOIRE_UNATTENDED"
#: Variables qu'un terminal d'agent exporte aux commandes qu'il lance, même sans
#: identifiant de session. Elles disent « un agent tient ce shell », jamais qui il
#: est : ``AI_AGENT`` et ``CLAUDECODE`` ont été observées dans une session Claude
#: Code ; ``CURSOR_AGENT``, ``GEMINI_CLI`` et ``CODEX_SANDBOX*`` sont celles que
#: Cursor, Gemini CLI et Codex documentent, non observées ici. Copilot n'en pose
#: aucune connue : le canal CLI ne s'y fie donc pas seul (voir ``_comment_origin``).
AGENT_MARKER_ENVS = (
    "AI_AGENT",
    "CLAUDECODE",
    "CURSOR_AGENT",
    "GEMINI_CLI",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
)


@dataclass(frozen=True, slots=True)
class ActiveTask:
    """La tâche qu'une session porte, et d'où la réponse vient.

    ``source`` vaut ``env`` (``GRIMOIRE_TASK_ID``), ``ledger_claim`` (claim actif
    du Mission Ledger), ``board`` (unique carte ``in_progress`` du board) ou
    ``bootstrap`` (rien ne désigne de tâche). Une réponse qui ne dit pas d'où
    elle vient ne se vérifie pas.

    Deux sources s'ajoutent avec plusieurs sessions (issue #680) :
    ``session_claim`` (le claim rattaché au ``session_id`` demandeur) et
    ``ambiguous`` (plusieurs claims, aucun rattaché à cette session : la tâche
    reste ``bootstrap`` mais *candidates* nomme les claims en concurrence, pour
    que le message dise lesquels au lieu de « aucune tâche »).

    *expired* (issue #710) : les claims dont le bail est dépassé et qui
    auraient sinon désigné la tâche — rattachés à cette session, ou sans
    session. Ils ne désignent plus rien ; ils sont nommés pour que le message
    dise « claim expiré » plutôt que « aucune tâche ».

    *unreadable* (tri R38/R39) : ce que la résolution n'a pas pu lire — le
    ledger (``events.jsonl``) ou le board (``task-board.yaml``) existe mais est
    corrompu. Une source illisible n'est pas une source vide : quand rien
    d'autre ne désigne de tâche, la source vaut ``unreadable`` (et non
    ``bootstrap``) pour que le message dise « illisible » au lieu de « aucune
    tâche en cours ».
    """

    task_id: str
    source: str
    candidates: tuple[str, ...] = ()
    expired: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()

    @property
    def is_fallback(self) -> bool:
        """``True`` quand aucune tâche n'est désignée (``bootstrap``, ``unreadable`` ou ambiguë)."""
        return self.source in ("bootstrap", "ambiguous", "unreadable")


def current_session(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """``(session_id, host)`` que l'environnement désigne — ``("", "")`` s'il n'en désigne aucun.

    ``GRIMOIRE_SESSION_ID`` (explicite, hôte inconnu) prime sur la variable que
    l'hôte exporte. Jamais de supposition par « journal le plus récent » : avec
    plusieurs sessions concurrentes, ce serait rattacher au hasard.

    Exception (#966) : sous ``GRIMOIRE_UNATTENDED=1``, la variable d'hôte prime.
    Une session sans humain est lancée par l'hôte (``claude --bg``) ; c'est lui
    qui dit qui agit. Un ``GRIMOIRE_SESSION_ID`` y est un résidu du démon des
    sessions de fond, qui le transmet tel quel à toutes celles qu'il lance. Sans
    variable d'hôte (CI, pilote), ``GRIMOIRE_SESSION_ID`` reste lu.
    """
    environ = os.environ if env is None else env
    explicit = str(environ.get(SESSION_ENV, "")).strip()
    if explicit and str(environ.get(_UNATTENDED_ENV, "")).strip() != "1":
        return explicit, ""
    for name, host in HOST_SESSION_ENVS.items():
        value = str(environ.get(name, "")).strip()
        if value:
            return value, host
    return (explicit, "") if explicit else ("", "")


def agent_marker(env: Mapping[str, str] | None = None) -> str:
    """Le nom de la première variable de :data:`AGENT_MARKER_ENVS` posée, ``""`` sinon."""
    environ = os.environ if env is None else env
    for name in AGENT_MARKER_ENVS:
        if str(environ.get(name, "")).strip().lower() not in ("", "0", "false"):
            return name
    return ""


def current_session_id(env: Mapping[str, str] | None = None) -> str:
    """Le ``session_id`` d'hôte de :func:`current_session` — ``""`` s'il n'y en a pas."""
    return current_session(env)[0]


class _Claim(NamedTuple):
    """Un claim actif tel que la résolution le lit."""

    task_id: str
    session_id: str
    actor: str
    #: ISO 8601 du dernier ``task attach`` explicite, ``""`` sinon (issue #695).
    explicit_at: str
    #: Bail dépassé (:meth:`TaskClaim.is_expired`, issue #710) : le claim ne
    #: désigne plus aucune tâche, il n'est plus lu que pour le dire.
    expired: bool = False
    #: Le sous-agent que le hook a vu réclamer (issue #698), ``""`` pour la boucle principale.
    agent_id: str = ""


def _held_claims(project_root: Path, *, actor: str = "") -> list[_Claim]:
    """Les claims ``claimed``/``running``, expirés compris — ``session_id`` vide si non rattaché.

    Ne lève jamais : un ledger illisible vaut « aucun claim » pour cet appelant ;
    la résolution de tâche, elle, passe par :func:`_held_claims_checked` et sait
    le dire.
    """
    return _held_claims_checked(project_root, actor=actor)[0]


def _held_claims_checked(project_root: Path, *, actor: str = "") -> tuple[list[_Claim], str, dict[str, str]]:
    """Les claims tenus, ce que le ledger a d'illisible (``""`` s'il se lit en entier), et les tâches garées.

    Troisième valeur : les tâches ``waiting``, de leur identifiant vers la session
    du claim qui les a garées (``""`` si le ledger n'en porte aucun). Ce ne sont
    pas des claims tenus — la tâche est garée — mais la résolution les connaît
    pour ne pas les reprendre depuis un ``GRIMOIRE_TASK_ID`` ou une carte de board
    périmée quand la session ne les détient pas (issue #1153).

    Un ledger absent est légitime (projet qui n'a jamais ouvert de tâche) : pas
    de problème. Un ledger présent mais dont des lignes sont corrompues, ou que
    le rejeu ne peut pas lire du tout, est signalé : l'appelant ne doit pas le
    prendre pour « aucune tâche ».
    """
    location = ledger_location(project_root)  # #1259 : le ledger du projet d'origine dans un worktree de tâche
    if location.root is None:
        return [], location.problem, {}
    events = location.root / "events.jsonl"
    if not events.is_file():
        return [], _not_a_file(events, f"ledger {LEDGER_RELPATH.as_posix()}/events.jsonl"), {}
    try:
        from grimoire.missions.ledger import MissionLedger
        from grimoire.missions.schemas import TaskState

        ledger = MissionLedger(events.parent)
        tasks = ledger.list_tasks()
        bad_lines = ledger.unreadable_lines()
    except Exception as exc:  # frontière de hook : ne jamais casser une session
        return [], f"ledger {LEDGER_RELPATH.as_posix()}/events.jsonl illisible ({type(exc).__name__})", {}
    problem = ""
    if bad_lines:
        shown = ", ".join(str(n) for n in bad_lines[:5]) + (" …" if len(bad_lines) > 5 else "")
        problem = f"ledger {LEDGER_RELPATH.as_posix()}/events.jsonl : ligne(s) illisible(s) ({shown})"
    active = [t for t in tasks if t.status in (TaskState.CLAIMED, TaskState.RUNNING) and t.claim is not None]
    if actor:
        active = [t for t in active if t.claim is not None and t.claim.actor_id == actor]
    claims = [
        _Claim(
            t.id,
            t.claim.session_id,
            t.claim.actor_id,
            t.claim.attached_explicit_at,
            t.claim.is_expired(),
            t.claim.agent_id,
        )
        for t in active
        if t.claim is not None
    ]
    parked = {t.id: (t.claim.session_id if t.claim is not None else "") for t in tasks if t.status is TaskState.WAITING}
    return claims, problem, parked


def _active_claims(project_root: Path, *, actor: str = "") -> list[_Claim]:
    """Les claims vivants : ``claimed``/``running`` *et* bail non expiré (issue #710).

    Un claim expiré ne désigne plus la tâche de personne — ni de la session qui
    le portait, ni d'une autre : le bail est la seule preuve que quelqu'un y
    travaille encore. Ne lève jamais : un ledger illisible vaut « aucun claim ».
    """
    return [c for c in _held_claims(project_root, actor=actor) if not c.expired]


def claimed_task_ids(project_root: Path, *, actor: str = "") -> list[str]:
    """Tâches ``claimed`` ou ``running`` du ledger — celles de *actor* seulement s'il est nommé.

    Ne lève jamais : un ledger illisible vaut « aucun claim », et la résolution
    continue sur le board.
    """
    return [claim.task_id for claim in _active_claims(project_root, actor=actor)]


def _claim_of_session(mine: list[_Claim], actor: str, agent_id: str = "") -> str | None:
    """La tâche qu'une session désigne parmi ses claims, ``None`` si elle n'en désigne pas une seule.

    Un seul claim rattaché : c'est lui. Plusieurs (issue #695 : les sous-agents
    héritent du ``session_id`` de leur parent et rattachent leurs claims à la
    session de l'orchestrateur) : d'abord le claim que *ce* sous-agent tient
    (issue #698 : ``agent_id`` du payload de hook, posé sur le claim par le hook
    qui a observé le ``task claim``), puis le dernier rattachement *explicite* —
    une déclaration d'intention vaut plus qu'un claim incident — puis le claim
    unique de ``GRIMOIRE_ACTOR`` dans cette session, enfin, pour la boucle
    principale (sans ``agent_id``), le seul claim qu'aucun sous-agent ne tient.
    Une égalité (même horodatage explicite, plusieurs claims du même acteur) ne
    départage rien : l'appelant retombe sur l'ambiguïté nommée.
    """
    if len(mine) == 1:
        return mine[0].task_id
    if agent_id:
        own = [c for c in mine if c.agent_id == agent_id]
        if len(own) == 1:
            return own[0].task_id
    explicit = [c for c in mine if c.explicit_at]
    if explicit:
        latest = max(c.explicit_at for c in explicit)
        winners = [c for c in explicit if c.explicit_at == latest]
        if len(winners) == 1:
            return winners[0].task_id
    if actor:
        by_actor = [c for c in mine if c.actor == actor]
        if len(by_actor) == 1:
            return by_actor[0].task_id
    if not agent_id:
        unowned = [c for c in mine if not c.agent_id]
        if len(unowned) == 1:
            return unowned[0].task_id
    return None


def resolve_active_task(
    project_root: Path,
    *,
    env: Mapping[str, str] | None = None,
    write_cache: bool = True,
    session_id: str = "",
    agent_id: str = "",
) -> ActiveTask:
    """Task a lifecycle hook should evaluate, with the rule that chose it.

    Resolution order:

    1. ``GRIMOIRE_TASK_ID`` — an operator saying which task this session is about.
    2. The claim attached to this session (``task.session_attached``, issue
       #680): the ``claimed``/``running`` task whose claim carries *session_id*
       — passed by a hook from its payload, else read from the environment
       (:func:`current_session_id`). With several sessions on one project this
       is what tells their tasks apart. When one session holds several claims
       (subagents inherit their parent's session id, issue #695) the latest
       explicit ``grimoire task attach`` wins, then the claim of
       ``GRIMOIRE_ACTOR`` when it is unique in the session. A hook fired from
       a subagent passes the host's *agent_id* (issue #698): the claim that
       subagent was seen taking is resolved first, so two subagents of one
       session each get their own task; the main loop (no *agent_id*) falls
       back on the one claim no subagent holds.
    3. The Mission Ledger's single ``claimed``/``running`` task, restricted to
       ``GRIMOIRE_ACTOR``'s claims when that is set. The ledger is the source
       (ADR-005); a claim is visible here the moment it is written, whether or
       not the board has been re-projected since. When the caller knows its
       session, a claim attached to *another* session is never inherited
       (issue #710): only a claim with no session yet qualifies — the one the
       hook then attaches (:func:`grimoire.hosts.decisions.enrolment.link_session`).
       A caller with no identifiable session (CLI outside any host) cannot
       tell whose claim it is and keeps the single claim.
    4. The board's single ``in_progress`` task — a project whose board was
       written by hand, or imported, and has no ledger.
    5. ``bootstrap``.

    A task the ledger records as ``waiting`` is parked, not current: a session
    that does not hold its claim never takes it from ``GRIMOIRE_TASK_ID`` or
    from a stale board card (issue #1153). A claimed task, waiting or not,
    keeps the behaviour above — ``waiting`` simply never qualifies as a
    ``claimed``/``running`` claim, so a session that parked its own task falls
    back here and :func:`grimoire.hosts.decisions.enrolment.parked_task_of_session`
    names it at ``Stop``.

    Claims whose lease has expired (:meth:`TaskClaim.is_expired`, issue #710)
    take part in none of these rules, a session's own included: the answer
    falls back and names them in ``expired``. Hence a claim without a session
    is retained only while its lease runs.

    Several claims and none attached to this session are ambiguous: the answer
    is ``bootstrap`` with source ``ambiguous`` and the competing claims in
    ``candidates`` — never a silent fallback. Two in-progress cards on the
    board are skipped as before.

    *write_cache* — see :func:`active_profile_id`: ``False`` for a caller that
    must never write anything (a ``readOnlyHint`` MCP tool).
    """
    environ = os.environ if env is None else env
    override = str(environ.get(TASK_ENV, "")).strip()
    session = session_id.strip() or current_session_id(environ)
    actor = str(environ.get(ACTOR_ENV, "")).strip()
    # Un seul rejeu du ledger par résolution (K13) : les claims tenus servent à la règle 2
    # comme à la règle 3, qui les relisaient chacune en entier ; les tâches garées servent
    # à écarter une attente que cette session ne détient pas (issue #1153).
    every_held, ledger_problem, waiting = _held_claims_checked(project_root)
    # Une tâche ``waiting`` n'est pas un claim tenu : elle est garée. Une session qui ne
    # détient pas son claim ne la reprend pas comme tâche courante, même nommée par
    # ``GRIMOIRE_TASK_ID`` ou par une carte de board périmée. Réclamée par la session,
    # elle garde le comportement d'avant.
    parked_elsewhere = {tid for tid, claim_session in waiting.items() if not (session and claim_session == session)}
    named = normalize_task_id(override) if override else ""
    if named and named not in parked_elsewhere:
        return ActiveTask(named, "env")

    ledger_unreadable = (ledger_problem,) if ledger_problem else ()
    if session:
        mine = [c for c in every_held if not c.expired and c.session_id == session]
        chosen = _claim_of_session(mine, actor, agent_id.strip())
        if chosen is not None:
            try:
                return ActiveTask(normalize_task_id(chosen), "session_claim", unreadable=ledger_unreadable)
            except ValueError:
                pass

    held = [c for c in every_held if c.actor == actor] if actor else every_held
    claims = [c for c in held if not c.expired]
    # Issue #710 : un claim d'une autre session ne désigne jamais la tâche de
    # celle-ci — B retombe sur bootstrap/ambiguous, jamais sur la tâche de A.
    if len(claims) == 1 and (not session or claims[0].session_id in ("", session)):
        try:
            return ActiveTask(normalize_task_id(claims[0].task_id), "ledger_claim", unreadable=ledger_unreadable)
        except ValueError:
            pass
    expired = tuple(c.task_id for c in held if c.expired and c.session_id in ("", session))

    root = project_root.resolve()

    def compute() -> dict[str, Any]:
        board_path = root / TASK_BOARD_RELPATH
        problem = _board_problem(board_path)
        tasks = _load_mapping(board_path).get("tasks")
        if not isinstance(tasks, list):
            return {"in_progress": [], "problem": problem}
        return {
            "in_progress": [
                str(task.get("task_id", ""))
                for task in tasks
                if isinstance(task, dict) and str(task.get("status", "")) == "in_progress" and task.get("task_id")
            ],
            "problem": problem,
        }

    board_state = _cached_or_fresh(root, "board_state", TASK_BOARD_RELPATH, compute, write_cache=write_cache)
    in_progress = board_state.get("in_progress", []) if isinstance(board_state, dict) else []
    # Un dossier ou un lien cassé a la même empreinte qu'un board absent (stat échoue) : jamais lu du cache.
    board_file = root / TASK_BOARD_RELPATH
    board_problem = (
        _not_a_file(board_file, f"board {TASK_BOARD_RELPATH.as_posix()}") if not board_file.is_file() else ""
    ) or (str(board_state.get("problem", "")) if isinstance(board_state, dict) else "")
    unreadable = tuple(p for p in (ledger_problem, board_problem) if p)
    # Le board projette les claims du ledger en `in_progress` : sans ce filtre,
    # un claim expiré ou tenu par une autre session reviendrait par la règle 4.
    # Une carte `in_progress` que le ledger sait garée en `waiting` est périmée
    # (projection interrompue, board écrit à la main) : elle ne désigne rien.
    disqualified = {
        c.task_id for c in every_held if c.expired or (session and c.session_id not in ("", session))
    } | parked_elsewhere
    in_progress = [tid for tid in in_progress if tid not in disqualified]
    if len(in_progress) == 1:
        return ActiveTask(normalize_task_id(in_progress[0]), "board", unreadable=unreadable)
    if len(claims) > 1:
        return ActiveTask("bootstrap", "ambiguous", tuple(c.task_id for c in claims), expired, unreadable)
    if unreadable:
        # Une source corrompue n'est pas une source vide : dire « illisible », pas « aucune tâche ».
        return ActiveTask("bootstrap", "unreadable", expired=expired, unreadable=unreadable)
    return ActiveTask("bootstrap", "bootstrap", expired=expired)


def active_task_id(
    project_root: Path,
    *,
    env: Mapping[str, str] | None = None,
    write_cache: bool = True,
    session_id: str = "",
    agent_id: str = "",
) -> str:
    """Task a lifecycle hook should evaluate — see :func:`resolve_active_task`."""
    return resolve_active_task(
        project_root, env=env, write_cache=write_cache, session_id=session_id, agent_id=agent_id
    ).task_id
