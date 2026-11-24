"""Gemini CLI, hôte d'une session de fond du tick (GAO-orchestrateu-001, après :mod:`.worker_host`, #1281 L4).

Le lanceur garde les gardes, le claim, le ledger et le mandat ; ce module dit comment Gemini CLI lance, identifie,
reprend et arrête une session sans interface, derrière l'interface :class:`~.worker_host.WorkerHost`.

**Ligne de commande** (geminicli.com/docs, lue le 2026-11-12, v0.64.0 annoncée ; l'outil n'est pas installé sur le
poste de développement : tout se teste sur un faux binaire) : ``gemini --prompt=<consigne> --output-format stream-json
--approval-mode <mode> [--model <modèle>] [--resume=<id>]``. La consigne et l'identifiant de reprise passent chacun
en un seul jeton ``--option=valeur`` : ni un message qui commencerait par ``-``, ni un identifiant forgé (``--yolo``)
ne peuvent être lus comme une option. L'identifiant doit en plus être un UUID (:data:`SESSION_ID`) : ``latest`` ou un
numéro d'ordre, que ``--resume`` admet aussi, reprendraient une autre session que celle du worker.

**Environnement** : une liste fermée (:meth:`GeminiWorkerHost.environ`), jamais celui du tick : rien qu'un jeton
ou une clé d'un autre fournisseur (``ANTHROPIC_API_KEY``, ``GH_TOKEN``…) ne passe au processus détaché.

**Pas de session détachée.** Gemini CLI n'en documente aucune : le kit lance le processus lui-même, détaché (nouveau
groupe de processus), sortie ``stream-json`` dans un journal sous ``_grimoire-output/control/workers/`` (hors du
worktree). Le pid est la poignée ; la fin se lit à la mort du processus, le silence à l'âge du journal ; le janitor
l'arrête par ``SIGTERM`` au groupe (POSIX seulement : sous Windows, rien ne part).

**Vivacité** : un pid ne suffit pas (il se recycle). L'identité du processus — heure de démarrage et exécutable
exact (:func:`process_identity`) — est relevée au lancement, inscrite au ledger (``pid_identity``), et comparée à
chaque sonde : un autre processus sous le même pid, ou un lancement sans identité notée, compte pour mort.

**Identité** : le ``session_id`` de l'événement ``init`` du flux, lu dans le journal à partir de l'octet où ce
lancement a commencé à écrire (un lancement précédent de la même tâche ne compte pas).

**Résultat final** : la concaténation des événements ``message`` de rôle ``assistant`` ; l'issue (``status``) vient de
l'événement ``result``, une erreur de l'événement ``error``. Format des événements d'après la page « headless » de
la documentation — non vérifié sur un binaire.

**Permission** : le mode de la politique se traduit par :data:`GEMINI_APPROVAL_MODES` ; un mode sans traduction (ni
``auto`` ni un nom inconnu) refuse le lancement.

**Frein** : aucune session ne part tant que le refus au ``BeforeTool`` n'est pas prouvé pour la version **exacte** de
``gemini`` (:func:`~grimoire.hosts.capabilities.is_fenced`, même règle que la reprise d'un bail Claude Code). Au
2026-11-23, aucune capture de refus Gemini n'est versée (#1304) : le lanceur dit pourquoi et ne lance rien.

Non vérifié (écrit tel quel, rien n'est inventé) : un événement de fin de tour capable de bloquer (``AfterAgent``) ;
le format des fichiers de session de Gemini CLI ; le comportement du mode ``default`` sans interface face à un outil
qui demanderait une confirmation.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.bridges.schemas import HostId
from grimoire.missions.worker_host import (
    GEMINI_HOST,
    SILENCE_S,
    LaunchPlan,
    SessionSpec,
    Spawn,
    SpawnResult,
    _kill_group,
    default_spawn,
    session_env,
)

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec

__all__ = [
    "GEMINI_APPROVAL_MODES",
    "GEMINI_ENV_ALLOWED",
    "GEMINI_HOST",
    "GEMINI_SECRET_ENV",
    "SESSION_ID",
    "GeminiResult",
    "GeminiWorkerHost",
    "gemini_log",
    "gemini_worktree_refusal",
    "process_identity",
    "read_events",
]

#: Mode de permission de la politique → ``--approval-mode`` de Gemini CLI (``default|auto_edit|yolo|plan``).
#: ``dontAsk`` (refuser sans demander ce qui demanderait) n'a pas d'équivalent nommé : ``default``, où rien ne
#: s'approuve seul. ``auto`` (classifieur de Claude Code) n'a aucune traduction honnête : refusé.
GEMINI_APPROVAL_MODES: Mapping[str, str] = {
    "default": "default",
    "dontAsk": "default",
    "acceptEdits": "auto_edit",
    "plan": "plan",
    "bypassPermissions": "yolo",
}
#: Les secrets d'authentification que Gemini CLI lit (geminicli.com/docs, « Authentication », lu le 2026-11-24 :
#: clé Gemini API, clé Vertex AI en mode express) : transmis s'ils sont posés, masqués dans toute trace.
GEMINI_SECRET_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
#: Ce qui passe en plus de :data:`~.worker_guard.ENV_ALLOWED` : l'authentification Gemini (secrets ci-dessus, projet
#: et région Vertex AI, compte de service, choix du mode Vertex/Code Assist), le réseau (mandataires, autorités de
#: certification) et les chemins de configuration de ``gh``/``git``/XDG (des chemins, jamais des jetons : ``gh``
#: s'authentifie par son fichier, comme pour opencode). Absents : ``GH_TOKEN``, ``GITHUB_TOKEN``, toute clé d'un autre
#: fournisseur, et les variables qui déplaceraient les réglages de Gemini CLI (``GEMINI_CLI_SYSTEM_SETTINGS_PATH``,
#: ``GEMINI_CLI_HOME``…) : ``.gemini/settings.json`` du worktree, qui porte le hook ``BeforeTool``, reste celui lu.
GEMINI_ENV_ALLOWED = (
    *GEMINI_SECRET_ENV,
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_GCA",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "GH_CONFIG_DIR",
    "GIT_CONFIG_GLOBAL",
)
#: Le format d'un ``session_id`` Gemini CLI admis à la reprise : un UUID (identifiant de l'événement ``init``).
SESSION_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PS_TIMEOUT_S = 5.0
#: Lectures du journal avant de conclure que l'événement ``init`` n'arrive pas, et la pause entre deux.
_RESOLVE_ATTEMPTS = 10
_RESOLVE_PAUSE_S = 1.0
_POSIX = os.name == "posix"


@dataclass(frozen=True, slots=True)
class GeminiResult:
    """Ce que le flux d'une session dit à la fin : ``status`` (``success``, ``error``…, vide si aucun ``result``)."""

    status: str = ""
    text: str = ""
    error: str = ""
    session_id: str = ""

    @property
    def finished(self) -> bool:
        return bool(self.status)


def gemini_log(root: Path, task_id: str) -> Path:
    """Le journal ``stream-json`` d'un worker Gemini : hors du worktree, sous le dossier d'exécution du projet."""
    return root / "_grimoire-output" / "control" / "workers" / f"{task_id}.gemini.jsonl"


def read_events(log: Path, since: int = 0) -> list[dict[str, Any]]:
    """Les événements JSON du journal à partir de l'octet *since* ; une ligne illisible est ignorée, jamais levée."""
    try:
        with log.open("rb") as handle:
            handle.seek(max(since, 0))
            raw = handle.read()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _linux_identity(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        exe = str(Path(f"/proc/{pid}/exe").readlink())
    except OSError:
        return ""  # mort, zombie (``exe`` illisible) ou processus d'un autre utilisateur : rien d'affirmé
    fields = stat[stat.rfind(")") + 2 :].split()  # le nom (champ 2) peut contenir espaces et parenthèses
    if len(fields) < 20 or fields[0] in ("Z", "X"):
        return ""
    return f"{fields[19]}:{exe}"  # champ 22 : heure de démarrage, en tops d'horloge depuis le démarrage


def _ps_identity(pid: int) -> str:
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            ["ps", "-p", str(pid), "-o", "stat=", "-o", "lstart=", "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
            check=False,
            env={"PATH": os.environ.get("PATH", "/bin:/usr/bin"), "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = done.stdout.strip().split(maxsplit=6) if done.returncode == 0 else []
    if len(parts) < 7 or parts[0].startswith("Z"):
        return ""
    return f"{' '.join(parts[1:6])}:{parts[6]}"  # ``lstart`` (5 mots), puis le chemin de l'exécutable


def process_identity(pid: int) -> str:
    """``heure de démarrage:exécutable exact`` du processus *pid* ; ``""`` s'il est mort, zombie ou illisible.

    Linux : ``/proc/<pid>/stat`` (champ 22) et ``/proc/<pid>/exe``. Autre POSIX (macOS) : ``ps -o lstart,comm``.
    Windows : ``""`` (le worker Gemini n'y part pas).
    """
    if pid <= 0 or not _POSIX:
        return ""
    if Path("/proc/self/stat").is_file():
        return _linux_identity(pid)
    return _ps_identity(pid)


def _process_exists(pid: int) -> bool:
    """Vrai si un processus porte *pid* (signal 0) ; l'identité se vérifie à part (:func:`process_identity`)."""
    if pid <= 0 or not _POSIX:
        return False  # sous Windows, ``os.kill`` termine le processus : on ne sonde pas
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # un autre utilisateur : son identité, illisible, le dira étranger
    return True


def _settings_hook_armed(path: Path) -> bool:
    from grimoire.hosts.emitters.gemini import _owned_entry

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    hooks = data.get("hooks") if isinstance(data, dict) else None
    entries = hooks.get("BeforeTool") if isinstance(hooks, dict) else None
    return isinstance(entries, list) and any(_owned_entry(entry) for entry in entries)


def gemini_worktree_refusal(wt_path: Path) -> str:
    """``""`` si ``.gemini/settings.json`` du worktree porte le hook ``BeforeTool`` du kit ; sinon la raison.

    Pas de synchronisation à la volée (contrairement à Claude Code) : elle écrirait des fichiers non suivis
    ailleurs que sous ``.gemini/`` (``GEMINI.md``…), que le relecteur et le janitor prendraient pour du travail non
    commité. Le projet commite sa surface Gemini (``grimoire host sync``) ; sans elle, rien ne part.
    """
    if _settings_hook_armed(wt_path / ".gemini" / "settings.json"):
        return ""
    return (
        "worktree sans hook BeforeTool Grimoire dans .gemini/settings.json : "
        "lancer `grimoire host sync` et commiter la surface Gemini, puis relancer"
    )


class GeminiWorkerHost:
    """Gemini CLI : ``gemini --prompt=… --output-format stream-json`` lancé en arrière-plan par le kit."""

    name = GEMINI_HOST
    claim_host = "gemini-cli"
    session_host = HostId.GEMINI_CLI.value

    def __init__(
        self,
        binary: str = "gemini",
        *,
        spawn: Spawn | None = None,
        alive: Callable[[int], bool] | None = None,
        kill: Callable[[int], bool] | None = None,
        identify: Callable[[int], str] | None = None,
        resolve_attempts: int = _RESOLVE_ATTEMPTS,
        resolve_pause_s: float = _RESOLVE_PAUSE_S,
    ) -> None:
        self.binary = binary
        self.spawn = spawn if spawn is not None else default_spawn
        self.alive = alive if alive is not None else _process_exists
        self.kill = kill if kill is not None else _kill_group
        self.identify = identify if identify is not None else process_identity
        self.resolve_attempts = resolve_attempts
        self.resolve_pause_s = resolve_pause_s

    # -- lancement -------------------------------------------------------------

    def plan(self, spec: SessionSpec) -> LaunchPlan | str:
        mode = GEMINI_APPROVAL_MODES.get(spec.permission_mode)
        if mode is None:
            known = ", ".join(GEMINI_APPROVAL_MODES)
            return f"mode de permission {spec.permission_mode!r} sans traduction pour Gemini CLI (admis : {known})"
        if spec.resume and not SESSION_ID.fullmatch(spec.resume):
            return f"identifiant de session {spec.resume[:80]!r} hors format pour --resume (UUID attendu)"
        argv = [self.binary, f"--prompt={spec.prompt}", "--output-format", "stream-json", "--approval-mode", mode]
        if spec.model:
            argv.append(f"--model={spec.model}")
        if spec.resume:
            argv.append(f"--resume={spec.resume}")
        env = self.environ()
        secrets = tuple(k for k in GEMINI_SECRET_ENV if k in env)
        return LaunchPlan(tuple(argv), tuple(env.items()), secrets, detached=True)

    @staticmethod
    def environ(source: Mapping[str, str] | None = None) -> dict[str, str]:
        """L'environnement d'une session : une liste fermée, jamais celui du tick.

        :data:`~.worker_guard.ENV_ALLOWED` (comme opencode, sans redirection de ``HOME`` : l'authentification Gemini
        et ``gh`` lisent leurs fichiers dans le ``HOME`` réel), :data:`GEMINI_ENV_ALLOWED`, et la marque « sans
        surveillance ». Le durcissement git du tick (``GIT_CONFIG_*``) n'y est pas plus qu'il n'était dans l'ancien
        environnement complet (:func:`~grimoire.core.safe_git.session_environ` l'en retirait).
        """
        from grimoire.missions.worker_guard import minimal_env

        environ = os.environ if source is None else source
        env = minimal_env({}, source=environ)
        env.update({k: environ[k] for k in GEMINI_ENV_ALLOWED if environ.get(k)})
        env.update(session_env())
        return env

    def start(self, plan: LaunchPlan, cwd: Path, log: Path) -> SpawnResult:
        """Lance le plan en arrière-plan, sortie dans *log* ; refusé sous Windows (suivi par pid POSIX)."""
        if not _POSIX and self.spawn is default_spawn:
            return SpawnResult(0, "worker Gemini CLI non pris en charge sous Windows (suivi par pid POSIX)")
        return self.spawn(plan.argv, cwd, plan.environ(), log)

    def handle(self, output: str) -> str:
        """Le pid noté par :meth:`start` : Gemini CLI n'imprime aucun identifiant en rendant la main."""
        return output.strip() if output.strip().isdigit() else ""

    def version(self, exec_: Exec, cwd: Path) -> str:
        """``gemini --version`` ; ``""`` si illisible (alors aucun frein n'est prouvé)."""
        from grimoire.hosts.host_version import VERSION_PATTERN

        try:
            done = exec_([self.binary, "--version"], cwd, None)
        except (OSError, ValueError):
            return ""
        found = VERSION_PATTERN.search(done.stdout or "") if done.returncode == 0 else None
        return found.group(0) if found else ""

    @staticmethod
    def proof_refusal(version: str, project_root: Path | None = None) -> str:
        """``""`` si le refus au ``BeforeTool`` est prouvé pour *version* exacte ; sinon pourquoi rien ne part."""
        from grimoire.hosts.capabilities import is_fenced

        if not version:
            return "version de Gemini CLI illisible (`gemini --version`) : frein non prouvable"
        if is_fenced(HostId.GEMINI_CLI, version, project_root=project_root):
            return ""
        return (
            f"frein non prouvé pour Gemini CLI {version} : aucune capture d'un refus BeforeTool honoré "
            "pour cette version (#1304)"
        )

    # -- identité, résultat ------------------------------------------------------

    def resolve(
        self,
        exec_: Exec,
        cwd: Path,
        handle: str,
        name: str,
        *,
        log: Path | None = None,
        since: int = 0,
        identity: str = "",
    ) -> str:
        """Le ``session_id`` de l'événement ``init`` écrit par ce lancement ; ``""`` si absent (échec fermé).

        Le processus mort (ou remplacé, si *identity* est donnée) sans ``init`` : inutile d'attendre. Deux ``init``
        différents : ``""``, on ne choisit pas. Un ``session_id`` hors format (:data:`SESSION_ID`) : ``""``, le
        worker ne pourrait pas être repris.
        """
        del exec_, cwd, name
        if log is None:
            return ""
        pid = int(handle) if handle.isdigit() else 0
        for attempt in range(max(self.resolve_attempts, 1)):
            if attempt:
                time.sleep(self.resolve_pause_s)
            found = {str(e.get("session_id") or "") for e in read_events(log, since) if e.get("type") == "init"}
            found.discard("")
            if len(found) == 1:
                session_id = found.pop()
                return session_id if SESSION_ID.fullmatch(session_id) else ""
            if len(found) > 1 or not self._same_process(pid, identity, required=bool(identity)):
                return ""
        return ""

    def _same_process(self, pid: int, identity: str, *, required: bool = True) -> bool:
        """Vrai si *pid* vit et porte *identity* ; sans identité notée : faux (*required*), ou la seule existence."""
        if not self.alive(pid):
            return False
        if not identity:
            return not required
        with contextlib.suppress(OSError, ValueError):
            return self.identify(pid) == identity
        return False

    @staticmethod
    def result(log: Path, since: int = 0) -> GeminiResult:
        """Le résultat final lu dans le flux : texte de l'assistant, issue (``result``), dernière erreur."""
        session_id, status, error = "", "", ""
        parts: list[str] = []
        for event in read_events(log, since):
            kind = event.get("type")
            if kind == "init":
                session_id = str(event.get("session_id") or session_id)
            elif kind == "message" and event.get("role") == "assistant":
                parts.append(str(event.get("content") or ""))
            elif kind == "result":
                status = str(event.get("status") or "unknown")
                detail = event.get("error")
                if isinstance(detail, dict):
                    error = str(detail.get("message") or error)
            elif kind == "error":
                error = str(event.get("message") or event.get("error") or error)
        return GeminiResult(status, "".join(parts), error, session_id)

    # -- vie, arrêt ----------------------------------------------------------------

    def stop(self, handle: str) -> bool:
        """``SIGTERM`` au groupe du processus *handle* (pid) ; vrai si l'arrêt est confirmé.

        Les appelants qui tiennent un lancement du ledger (janitor, arrêt au repos) ne l'appellent qu'après
        :meth:`state` ≠ ``ended``, qui a vérifié l'identité du processus ; le lanceur, sur le pid qu'il vient d'obtenir.
        """
        return self.kill(int(handle) if handle.isdigit() else 0)

    def halt(self, exec_: Exec, cwd: Path, handle: str) -> tuple[bool, str]:
        del exec_, cwd  # le kit tient le pid : aucun processus externe à lancer
        if self.stop(handle):
            return True, f", processus gemini {handle} arrêté"
        return False, f", processus gemini {handle or '?'} NON arrêté"

    def state(self, launch: Mapping[str, Any], *, now: float | None = None) -> str:
        """``busy`` (vivant, journal récent), ``silent`` (vivant mais muet), ``ended`` (processus mort).

        Vivant veut dire : le pid noté porte encore l'identité notée au lancement (``pid_identity``). Un pid recyclé,
        ou un lancement sans identité, compte pour ``ended``.
        """
        raw = str(launch.get("pid") or "0")
        if not self._same_process(int(raw) if raw.isdigit() else 0, str(launch.get("pid_identity") or "")):
            return "ended"
        log = str(launch.get("log") or "")
        try:
            if not log:
                raise FileNotFoundError(log)  # ``Path("")`` serait le dossier courant, toujours présent
            age = (time.time() if now is None else now) - Path(log).stat().st_mtime
        except OSError:
            return "busy"  # pas encore de sortie : le processus démarre
        return "silent" if age > SILENCE_S else "busy"
