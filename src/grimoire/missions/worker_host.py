"""L'hôte d'une session de fond du tick : ce qui dépend de lui pour lancer un worker (épique #1281, lot L4).

Le lanceur (:mod:`.launcher`) garde les gardes, le claim, le ledger et le mandat. Ce que l'hôte décide est ici,
derrière :class:`WorkerHost` : la ligne de commande et l'environnement, le passage du modèle, le mode de
permission, la consigne, la lecture de l'identité de session et l'arrêt. ``orchestrator.roles.worker.host`` choisit
l'hôte ; absent, c'est Claude Code, à l'identique d'avant (preuve : ``tests/unit/missions/test_worker_host_parity.py``).

**Claude Code** (:class:`ClaudeCodeWorkerHost`) : ``claude --bg --name grimoire-<id> [--settings …] [lean]
[--model …] [--permission-mode …] "<consigne>"``. ``--bg`` rend la main et imprime l'identifiant court ; l'identité se
lit ensuite dans ``claude agents --json``.

**opencode** (:class:`OpencodeWorkerHost`) : ``opencode run --format json --title grimoire-<id> --model
<fournisseur>/<modèle> "<consigne>"`` — la commande non interactive documentée
(https://opencode.ai/docs/cli/#run ; options lues dans ``opencode run --help`` 1.17.11). Elle ne se détache pas : le
kit la lance en arrière-plan, dans son propre groupe de processus, sortie JSON dans un journal hors du worktree.
L'identité se lit dans ``opencode session list --format json`` (titre et dossier), la fin par la mort du
processus, le silence par l'âge du journal.

- Fournisseur et clé : ``OPENCODE_CONFIG_CONTENT`` (configuration en ligne, https://opencode.ai/docs/config/,
  précédence « Inline config ») ne porte que ``{env:VAR}`` (substitution documentée au même endroit) ; la valeur de
  la clé, lue au magasin de secrets (:func:`~grimoire.providers.secrets.env_for_provider`), ne passe que dans
  l'environnement du processus. Jamais dans l'argv, jamais au ledger.
- Garde : le plugin ``.opencode/plugin/grimoire-guard.js`` émis par le kit doit être chargé dans le worktree.
  ``opencode debug config`` (sans appel modèle) rend la configuration résolue, ``plugin`` compris ; sans le plugin du
  kit dans cette liste, le worker ne démarre pas (:meth:`OpencodeWorkerHost.guard_refusal`). ``--pure`` (« run
  without external plugins ») n'est jamais passé.
- Permission : ``opencode run`` rejette d'office toute demande d'autorisation (source ``run.ts`` de la 1.17.11). Le
  mode du projet se traduit par :data:`OPENCODE_PERMISSION_ARGS` ; un mode sans traduction refuse le lancement.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:
    from grimoire.missions.launcher import Exec
    from grimoire.missions.schemas import MissionTask
    from grimoire.missions.worker_guard import Runner, RunResult
    from grimoire.providers.registry import ProviderSpec

__all__ = [
    "CLAUDE_CODE_HOST",
    "GEMINI_HOST",
    "LEAN_SESSION_ARGS",
    "LEAN_TOOLS",
    "OPENCODE_HOST",
    "OPENCODE_PERMISSION_ARGS",
    "PROJECT_HOSTS",
    "WORKER_HOSTS",
    "ClaudeCodeWorkerHost",
    "LaunchPlan",
    "OpencodeRoute",
    "OpencodeWorkerHost",
    "SessionSpec",
    "SpawnResult",
    "WorkerHost",
    "default_spawn",
    "opencode_route",
    "worker_host",
]

CLAUDE_CODE_HOST = "claude-code"
OPENCODE_HOST = "opencode"
#: Gemini CLI (:mod:`.gemini_host`) : l'hôte du projet quand ``orchestrator.host`` le nomme.
GEMINI_HOST = "gemini"
#: Les valeurs admises pour ``roles.worker.host`` ; la première est le défaut.
WORKER_HOSTS: tuple[str, ...] = (CLAUDE_CODE_HOST, OPENCODE_HOST, GEMINI_HOST)
#: Les valeurs admises pour ``orchestrator.host`` (l'hôte du projet) ; la première est le défaut.
PROJECT_HOSTS: tuple[str, ...] = (CLAUDE_CODE_HOST, GEMINI_HOST)
#: Mesuré le 2026-11-06 : 45 k jetons de contexte au premier tour sans ces options, 10 k avec. Sans ``Skill`` ni ``Agent``.
#: ``--tools`` est variadique : en deux jetons, il avale la consigne qui le suit (relecteur muet, #1160).
LEAN_TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
LEAN_SESSION_ARGS = ("--strict-mcp-config", f"--tools={LEAN_TOOLS}")
#: ``backgrounded · 0d310bc4 (…)`` : la ligne que ``claude --bg`` imprime (vérifié avec Claude Code 2.1.310).
BG_ID = re.compile(r"backgrounded\s*\S?\s*([0-9a-f]{8})\b")

#: Mode de permission de la politique → options d'``opencode run``. ``opencode run`` rejette d'office toute demande
#: d'autorisation : ``acceptEdits``, ``default``, ``dontAsk`` et ``auto`` gardent la table de l'hôte (ce qui
#: demanderait est refusé). ``plan`` prend l'agent ``plan`` intégré (https://opencode.ai/docs/agents/), qui n'écrit
#: pas. ``bypassPermissions`` accepte ce qui n'est pas explicitement refusé (``--dangerously-skip-permissions``).
OPENCODE_PERMISSION_ARGS: Mapping[str, tuple[str, ...]] = {
    "acceptEdits": (),
    "default": (),
    "dontAsk": (),
    "auto": (),
    "plan": ("--agent", "plan"),
    "bypassPermissions": ("--dangerously-skip-permissions",),
}
#: Fournisseur canonique du kit (:func:`~grimoire.providers.capabilities.canonical_provider`) → identifiant opencode
#: (catalogue models.dev, https://opencode.ai/docs/providers/ ; ``moonshotai`` pour Moonshot AI). ``local`` est un
#: fournisseur déclaré en ligne (``@ai-sdk/openai-compatible``, section Ollama de la même page).
OPENCODE_PROVIDERS: Mapping[str, str] = {"deepseek": "deepseek", "moonshot": "moonshotai", "local": "ollama"}
#: L'URL officielle de chaque fournisseur hébergé (champ ``api`` du catalogue models.dev que lit opencode 1.17.11),
#: posée explicitement dans la configuration en ligne : un ``baseURL`` venu d'ailleurs ne reçoit jamais la clé.
OPENCODE_BASE_URLS: Mapping[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "moonshotai": "https://api.moonshot.ai/v1",
}
#: Variables qui changeraient la découverte de la configuration ou des plugins d'opencode : jamais transmises.
OPENCODE_FORBIDDEN_ENV = (
    "OPENCODE_PURE",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    "OPENCODE_PERMISSION",
)
_OPENAI_COMPATIBLE_NPM = "@ai-sdk/openai-compatible"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: Lectures de ``opencode session list`` avant de conclure qu'une session lancée est introuvable, et la pause entre deux.
_RESOLVE_ATTEMPTS = 10
_RESOLVE_PAUSE_S = 1.0
#: Le suivi d'un worker opencode (sonde et arrêt par pid, groupe de processus) est POSIX ; sous Windows, rien ne part.
_POSIX = os.name == "posix"
#: Un journal opencode muet depuis plus longtemps ne renouvelle plus le bail : la récolte juge (bail de 20 min).
SILENCE_S = 15 * 60.0


@dataclass(frozen=True, slots=True)
class OpencodeRoute:
    """Le fournisseur et le modèle d'un worker opencode, résolus (aucune valeur secrète ici)."""

    provider: str  # identifiant du registre (``deepseek``)
    opencode_provider: str  # identifiant opencode (``deepseek``, ``moonshotai``, ``ollama``)
    model: str  # nom du modèle chez ce fournisseur (``deepseek-v4-flash``)
    key_var: str = ""  # variable de la clé dans le magasin ; vide pour un fournisseur local
    base_url: str = ""  # l'URL officielle (hébergé) ou la boucle locale OpenAI-compatible (local)
    source: str = ""  # ``table`` (dispatch.models) ou ``registry`` (palier du registre) ou ``policy``

    @property
    def qualified_model(self) -> str:
        return f"{self.opencode_provider}/{self.model}"

    def inline_config(self) -> str:
        """Le JSON d'``OPENCODE_CONFIG_CONTENT`` : le fournisseur, sa clé par ``{env:VAR}``, jamais sa valeur."""
        entry: dict[str, Any]
        if self.key_var:
            entry = {"options": {"apiKey": f"{{env:{self.key_var}}}", "baseURL": self.base_url}}
        else:
            entry = {
                "npm": _OPENAI_COMPATIBLE_NPM,
                "name": f"{self.provider} (local)",
                "options": {"baseURL": self.base_url},
                "models": {self.model: {"name": self.model}},
            }
        return json.dumps({"provider": {self.opencode_provider: entry}, "model": self.qualified_model}, sort_keys=True)


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Ce que le lanceur demande à l'hôte : une session neuve (*resume* vide) ou la reprise de *resume*."""

    name: str
    prompt: str
    model: str = ""
    permission_mode: str = ""
    lean: bool = False
    settings: Path | None = None  # Claude Code : les réglages du fournisseur compatible Anthropic (``--settings``)
    resume: str = ""
    opencode: OpencodeRoute | None = None
    kit_home: Path | None = None  # opencode : ``HOME`` et dossiers XDG du kit (:func:`.worker_guard.opencode_home`)


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """La ligne de commande et l'environnement d'un lancement ; ``secret_keys`` nomme les clés à ne jamais tracer."""

    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    secret_keys: tuple[str, ...] = ()
    detached: bool = False  # vrai : le kit détache le processus (opencode) ; faux : l'hôte rend la main (``--bg``)

    def environ(self) -> dict[str, str]:
        return dict(self.env)

    def traced_env(self) -> dict[str, str]:
        """L'environnement tel qu'il peut s'écrire : les valeurs secrètes masquées."""
        return {k: ("***" if k in self.secret_keys else v) for k, v in self.env}


@dataclass(frozen=True, slots=True)
class SpawnResult:
    """Un processus lancé en arrière-plan : son pid, ou pourquoi il n'a pas démarré."""

    pid: int = 0
    error: str = ""


#: ``(argv, cwd, env, journal)`` → processus détaché. Injectable : aucun test ne lance un vrai hôte.
Spawn = Callable[[Sequence[str], Path, Mapping[str, str], Path], SpawnResult]


def default_spawn(argv: Sequence[str], cwd: Path, env: Mapping[str, str], log: Path) -> SpawnResult:
    """Lance *argv* sans shell, détaché (nouveau groupe de processus), sortie dans *log* ; jamais d'exception.

    *env* est passé tel quel : rien de l'environnement du tick ne s'y ajoute (:func:`.worker_guard.minimal_env`).
    """
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as out:
            proc = subprocess.Popen(  # arguments fixes, sans shell
                list(argv),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except FileNotFoundError:
        return SpawnResult(0, f"{argv[0]} : binaire introuvable")
    except OSError as exc:
        return SpawnResult(0, f"{argv[0]} : {exc}")
    return SpawnResult(proc.pid)


def session_env() -> dict[str, str]:
    """La seule marque passée à une session de fond : « sans surveillance », jamais une identité (voir le lanceur)."""
    return {"GRIMOIRE_UNATTENDED": "1"}


class WorkerHost(Protocol):
    """Ce qui dépend de l'hôte pour lancer, identifier et arrêter une session de fond."""

    name: str  # valeur de ``roles.worker.host``
    claim_host: str  # hôte noté au claim
    session_host: str  # hôte noté au rattachement de la session

    def plan(self, spec: SessionSpec) -> LaunchPlan | str:
        """La ligne de commande et l'environnement, ou le motif d'un refus."""
        ...

    def handle(self, output: str) -> str:
        """L'identifiant que la sortie du lancement donne (``""`` s'il n'y en a pas)."""
        ...

    def resolve(self, exec_: Exec, cwd: Path, handle: str, name: str) -> str:
        """L'identité de session, lue après le lancement ; ``""`` si introuvable (échec fermé)."""
        ...

    def halt(self, exec_: Exec, cwd: Path, handle: str) -> tuple[bool, str]:
        """Arrête la session : ``(arrêt confirmé, fin de phrase)``."""
        ...


class ClaudeCodeWorkerHost:
    """Claude Code : ``claude --bg``, exactement ce que le lanceur faisait avant L4."""

    name = CLAUDE_CODE_HOST
    claim_host = "claude-code-cli"
    session_host = "claude-code-cli"

    def __init__(self, binary: str = "claude") -> None:
        self.binary = binary

    def plan(self, spec: SessionSpec) -> LaunchPlan:
        routed: list[str] = ["--settings", str(spec.settings)] if spec.settings is not None else []
        if spec.lean:
            routed += LEAN_SESSION_ARGS
        mode = ["--permission-mode", spec.permission_mode] if spec.permission_mode else []
        if spec.resume:
            # Une conversation reprise garde son modèle ; seuls ses réglages (fournisseur, lean) sont repassés.
            argv = [self.binary, "--bg", "--resume", spec.resume, *routed, *mode, spec.prompt]
        else:
            # Pas de ``--session-id`` : ``claude --bg`` l'ignore (#966), l'identité se lit après le lancement.
            model = ["--model", spec.model] if spec.model else []
            argv = [self.binary, "--bg", "--name", spec.name, *routed, *model, *mode, spec.prompt]
        return LaunchPlan(tuple(argv), tuple(session_env().items()))

    def handle(self, output: str) -> str:
        match = BG_ID.search(output)
        return match.group(1) if match else ""

    def resolve(self, exec_: Exec, cwd: Path, handle: str, name: str) -> str:
        from grimoire.missions.launcher import resolve_background_session

        return resolve_background_session(exec_, cwd, handle, self.binary, name=name, session_cwd=cwd)

    def halt(self, exec_: Exec, cwd: Path, handle: str) -> tuple[bool, str]:
        result = exec_([self.binary, "stop", handle], cwd, None)
        if result.returncode == 0:
            return True, f", session {handle} arrêtée"
        why = (result.stderr or result.stdout).strip()[:200] or f"sortie {result.returncode}"
        return False, f", session {handle} NON arrêtée ({why})"


def _pid_alive(pid: int, marker: bytes = b"opencode") -> bool:
    """Vrai si *pid* vit et porte *marker* dans sa ligne de commande (un pid recyclé par un autre programme ne compte
    pas) ; *marker* vaut ``opencode`` par défaut, ``gemini`` pour :class:`~.gemini_host.GeminiWorkerHost`."""
    if pid <= 0 or not _POSIX:
        return False  # sous Windows, ``os.kill`` termine le processus : on ne sonde pas
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # il vit, sous un autre utilisateur : on ne le dit pas mort
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.is_file():
        with contextlib.suppress(OSError):
            return marker in cmdline.read_bytes()
    return True


def _kill_group(pid: int) -> bool:
    """Arrête le groupe de processus de *pid* (``SIGTERM``) ; vrai si le groupe n'existe plus ou a reçu le signal."""
    if pid <= 0 or not _POSIX:
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return True


class OpencodeWorkerHost:
    """opencode : ``opencode run`` lancé en arrière-plan par le kit, garde du kit vérifiée avant le départ."""

    name = OPENCODE_HOST
    claim_host = "opencode"
    session_host = "host-opencode"

    def __init__(
        self,
        binary: str = "opencode",
        *,
        spawn: Spawn | None = None,
        alive: Callable[[int], bool] | None = None,
        kill: Callable[[int], bool] | None = None,
        resolve_attempts: int = _RESOLVE_ATTEMPTS,
        resolve_pause_s: float = _RESOLVE_PAUSE_S,
        runner: Runner | None = None,
    ) -> None:
        from grimoire.missions.worker_guard import exact_run

        self.binary = binary
        self.runner: Runner = runner if runner is not None else exact_run
        self.spawn = spawn if spawn is not None else default_spawn
        self.alive = alive if alive is not None else _pid_alive
        self.kill = kill if kill is not None else _kill_group
        self.resolve_attempts = resolve_attempts
        self.resolve_pause_s = resolve_pause_s

    # -- lancement -------------------------------------------------------------

    def plan(self, spec: SessionSpec) -> LaunchPlan | str:
        route = spec.opencode
        if route is None:
            return "worker opencode sans fournisseur résolu"
        if spec.kit_home is None:
            return "worker opencode sans HOME du kit"
        if spec.permission_mode not in OPENCODE_PERMISSION_ARGS:
            known = ", ".join(OPENCODE_PERMISSION_ARGS)
            return f"mode de permission {spec.permission_mode!r} sans traduction pour opencode (admis : {known})"
        argv = [self.binary, "run", "--format", "json"]
        argv += ["--session", spec.resume] if spec.resume else ["--title", spec.name]
        # ``--`` : la consigne n'est jamais lue comme une option (``opencode run`` ajoute ``args["--"]`` au message).
        argv += ["--model", route.qualified_model, *OPENCODE_PERMISSION_ARGS[spec.permission_mode], "--", spec.prompt]
        env = self.probe_env(route, spec.kit_home)
        secret_keys: tuple[str, ...] = ()
        if route.key_var:
            from grimoire.providers.secrets import env_for_provider

            value = env_for_provider(route.provider).get(route.key_var, "")
            if not value:
                return f"{route.provider} : clé absente du magasin ({route.key_var})"
            env[route.key_var] = value
            secret_keys = (route.key_var,)
        return LaunchPlan(tuple(argv), tuple(env.items()), secret_keys, detached=True)

    @staticmethod
    def probe_env(route: OpencodeRoute | None, kit_home: Path) -> dict[str, str]:
        """L'environnement minimal d'une commande opencode, sans la clé (:func:`.worker_guard.minimal_env`)."""
        from grimoire.missions.worker_guard import kit_dirs, minimal_env

        extra = {**session_env(), "OPENCODE_DISABLE_AUTOUPDATE": "1"}
        if route is not None:
            extra["OPENCODE_CONFIG_CONTENT"] = route.inline_config()
        extra.update(kit_dirs(kit_home))
        env = minimal_env(extra)
        for name in OPENCODE_FORBIDDEN_ENV:
            env.pop(name, None)
        return env

    def start(self, plan: LaunchPlan, cwd: Path, log: Path) -> SpawnResult:
        """Lance le plan en arrière-plan ; la sortie JSON d'``opencode run`` va dans *log*.

        Le kit suit le processus par son pid et son groupe (POSIX) : sous Windows, le lancement est refusé.
        """
        if not _POSIX and self.spawn is default_spawn:
            return SpawnResult(0, "worker opencode non pris en charge sous Windows (suivi par pid POSIX)")
        return self.spawn(plan.argv, cwd, plan.environ(), log)

    def handle(self, output: str) -> str:
        """Le pid noté par :meth:`start` (``opencode run`` n'imprime pas d'identifiant à rendre la main)."""
        return output.strip() if output.strip().isdigit() else ""

    def _probe(self, args: list[str], kit_home: Path | None) -> RunResult | None:
        """Lance ``opencode <args>`` dans le dossier de sondes du kit, après le contrôle de ce dossier ; ``None`` sinon.

        Jamais dans le worktree ni dans le dépôt principal : le dossier de sondes est un dépôt git vide du kit
        (:func:`.worker_guard.probe_dir`), qui borne la découverte des plugins, avec le ``HOME`` du kit.
        """
        from grimoire.missions.worker_guard import home_refusal, probe_dir, probe_refusal

        if kit_home is None or home_refusal(kit_home) or probe_refusal(probe_dir(kit_home)):
            return None
        return self.runner([self.binary, *args], probe_dir(kit_home), self.probe_env(None, kit_home), "")

    def version(self, exec_: Exec, cwd: Path, *, kit_home: Path | None = None) -> str:
        """``opencode --version``, lancé dans le dossier de sondes du kit (:meth:`_probe`)."""
        from grimoire.hosts.host_version import VERSION_PATTERN

        del exec_, cwd
        done = self._probe(["--version"], kit_home)
        if done is None:
            return ""
        found = VERSION_PATTERN.search(done.stdout or "") if done.returncode == 0 else None
        return found.group(0) if found else ""

    # -- garde -----------------------------------------------------------------

    def guard_refusal(self, cwd: Path, route: OpencodeRoute, *, kit_home: Path) -> str:
        """``""`` si la garde du kit tient pour un worker opencode dans *cwd* ; sinon un motif fixe (jamais une sortie).

        Dans l'ordre, et rien d'opencode ne tourne avant que les deux premiers tiennent :

        1. :func:`~grimoire.missions.worker_guard.integrity_refusal` : seul le plugin de garde du kit, à l'octet,
           peut être chargé (vérifié sans lancer opencode) ;
        2. :func:`~grimoire.missions.worker_guard.hook_witness_refusal` : le programme de garde refuse une charge
           témoin ;
        3. ``opencode debug config`` (environnement minimal, sans la clé) : le plugin du kit est le seul plugin
           résolu, et le fournisseur garde l'URL que le kit a posée. Sa sortie n'est jamais recopiée.
        """
        from grimoire.hosts.emitters.opencode import PLUGIN_RELPATH
        from grimoire.missions.worker_guard import hook_witness_refusal, integrity_refusal

        if refusal := integrity_refusal(cwd, kit_home=kit_home):
            return refusal
        env = self.probe_env(route, kit_home)
        if refusal := hook_witness_refusal(cwd, env, self.runner):
            return refusal
        done = self.runner([self.binary, "debug", "config"], cwd, env, "")
        if done.returncode != 0:
            return f"`opencode debug config` a échoué (sortie {done.returncode}) : garde non vérifiable"
        try:
            resolved = json.loads(done.stdout)
        except ValueError:
            resolved = None
        if not isinstance(resolved, dict):
            return "`opencode debug config` illisible : garde non vérifiable"
        loaded = resolved.get("plugin")
        plugin = cwd / PLUGIN_RELPATH
        if not isinstance(loaded, list) or not any(_same_file(spec, plugin) for spec in loaded):
            return f"opencode ne charge pas {PLUGIN_RELPATH.as_posix()} dans ce worktree : garde du kit inactive"
        if len(loaded) != 1:
            return "opencode résout d'autres plugins que celui du kit : garde non exclusive"
        provider = resolved.get("provider")
        entry = provider.get(route.opencode_provider) if isinstance(provider, dict) else None
        options = entry.get("options") if isinstance(entry, dict) else None
        if not isinstance(options, dict) or options.get("baseURL") != route.base_url:
            return "le fournisseur résolu par opencode ne pointe pas sur l'URL posée par le kit"
        if any(resolved.get(key) not in (None, {}) for key in ("agent", "command", "mode")):
            return "opencode résout des agents, des modes ou des commandes venus du projet ou de l'utilisateur"
        return ""

    # -- identité, fin, silence ------------------------------------------------

    def sessions(self, exec_: Exec, cwd: Path, *, kit_home: Path | None = None) -> list[dict[str, Any]] | None:
        """``opencode session list`` (toutes les sessions), lancé hors du worktree, dans *kit_home*."""
        del exec_, cwd
        done = self._probe(["session", "list", "--format", "json"], kit_home)
        if done is None or done.returncode != 0:
            return None
        try:
            raw = json.loads(done.stdout)
        except ValueError:
            return None
        return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else None

    def resolve(self, exec_: Exec, cwd: Path, handle: str, name: str, *, kit_home: Path | None = None) -> str:
        """L'identifiant de la session titrée *name* dans *cwd* (``opencode session list``) ; ``""`` sinon.

        Plusieurs candidates (un titre réutilisé dans le même dossier) : ``""``, on ne choisit pas.
        """
        del handle  # le pid ne nomme pas la session
        for attempt in range(max(self.resolve_attempts, 1)):
            if attempt:
                time.sleep(self.resolve_pause_s)
            listed = self.sessions(exec_, cwd, kit_home=kit_home)
            if listed is None:
                continue
            found = {
                str(s.get("id") or "") for s in listed if s.get("title") == name and _same_dir(s.get("directory"), cwd)
            }
            found.discard("")
            if len(found) == 1:
                return found.pop()
            if len(found) > 1:
                return ""
        return ""

    def stop(self, handle: str) -> bool:
        """``SIGTERM`` au groupe du processus *handle* (pid) ; vrai si l'arrêt est confirmé."""
        return self.kill(int(handle) if handle.isdigit() else 0)

    def halt(self, exec_: Exec, cwd: Path, handle: str) -> tuple[bool, str]:
        del exec_, cwd  # aucun processus externe : le kit tient le pid
        if self.stop(handle):
            return True, f", processus opencode {handle} arrêté"
        return False, f", processus opencode {handle or '?'} NON arrêté"

    def state(self, launch: Mapping[str, Any], *, now: float | None = None) -> str:
        """``busy`` (processus vivant, journal récent), ``silent`` (vivant mais muet), ``ended`` (processus mort)."""
        pid = int(str(launch.get("pid") or "0")) if str(launch.get("pid") or "0").isdigit() else 0
        if not self.alive(pid):
            return "ended"
        log = Path(str(launch.get("log") or ""))
        try:
            age = (time.time() if now is None else now) - log.stat().st_mtime
        except OSError:
            return "busy"  # pas encore de sortie : le processus démarre
        return "silent" if age > SILENCE_S else "busy"


def _same_dir(seen: object, expected: Path) -> bool:
    try:
        return Path(str(seen)).resolve() == expected.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _same_file(spec: object, expected: Path) -> bool:
    """Vrai si l'entrée ``plugin`` *spec* (``file:///…`` ou chemin) désigne *expected*."""
    text = str(spec)
    if text.startswith("file://"):
        text = unquote(urlsplit(text).path)
    try:
        return Path(text).resolve() == expected.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def worker_host(name: str, *, claude_bin: str = "claude") -> WorkerHost:
    """L'hôte nommé par ``roles.worker.host`` ; vide ou ``claude-code`` : Claude Code."""
    if name == GEMINI_HOST:
        from grimoire.missions.gemini_host import GeminiWorkerHost

        return GeminiWorkerHost()
    return OpencodeWorkerHost() if name == OPENCODE_HOST else ClaudeCodeWorkerHost(claude_bin)


def _loopback_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in {"http", "https"} and (parts.hostname or "").lower() in _LOOPBACK_HOSTS


def _local_base_url(provider: ProviderSpec) -> str:
    """L'URL OpenAI-compatible d'un fournisseur local, déduite d'``anthropic_base_url`` (``…/v1``) ; boucle locale seule.

    Ollama sert les deux API sur le même port : ``/v1`` pour OpenAI (https://docs.ollama.com/api/openai-compatibility).
    """
    url = (provider.anthropic_base_url or "").strip().rstrip("/")
    if not _loopback_url(url):
        return ""
    return url if url.endswith("/v1") else f"{url}/v1"


def opencode_route(
    root: Path,
    provider_id: str,
    tier: str,
    task: MissionTask,
    *,
    model: str = "",
    busy_providers: frozenset[str] = frozenset(),
    now: Any = None,
) -> tuple[OpencodeRoute | None, str]:
    """``(route, motif)`` du worker opencode de *task* vers *provider_id* ; pas de route : le motif, jamais vide.

    Le modèle vient, dans l'ordre : ``roles.worker.model`` ; la table palier x hôte x fournisseur pour l'hôte opencode
    (``dispatch.models.opencode.<fournisseur>.<palier>``, :func:`~grimoire.hosts.model_table.resolve_model`, un
    modèle sans appel d'outils connu à « non » écarté) ; le premier modèle non suspendu du palier dans le registre,
    sinon du palier ``cheap`` (comme la route compatible Anthropic). La politique de données, la clé et la règle
    « un worker à la fois par fournisseur local » s'appliquent comme pour :func:`~.worker_provider.route_worker`.
    """
    from datetime import datetime

    from grimoire.hosts.model_table import load_model_table, resolve_model
    from grimoire.providers.capabilities import canonical_provider
    from grimoire.providers.data_policy import data_policy_refusal
    from grimoire.providers.registry import read_registry
    from grimoire.providers.secrets import env_for_provider, provider_key_var
    from grimoire.providers.state import available_models, load_state

    if not provider_id:
        return None, "roles.worker.provider vide : le worker opencode n'a pas de fournisseur"
    provider = next((p for p in read_registry(root) if p.id == provider_id and p.enabled), None)
    if provider is None:
        return None, f"{provider_id} : fournisseur absent ou désactivé dans le registre"
    canonical = canonical_provider(provider.id, provider.provider_type)
    oc_provider = OPENCODE_PROVIDERS.get(canonical, "")
    if not oc_provider:
        return (
            None,
            f"{provider.id} : fournisseur non pris en charge par le worker opencode (admis : deepseek, moonshot, local)",
        )
    base_url = ""
    if canonical == "local":
        base_url = _local_base_url(provider)
        if not base_url:
            return (
                None,
                f"{provider.id} : un fournisseur local doit servir une boucle locale (127.0.0.1, localhost, ::1)",
            )
        if provider.id in busy_providers:
            return None, f"{provider.id} : un seul worker à la fois par fournisseur local, déjà occupé"
    if refusal := data_policy_refusal(root, provider, task.data_class):
        return None, refusal
    key_var = ""
    if canonical != "local":
        key_var = provider_key_var(provider.id)
        if not key_var or not env_for_provider(provider.id).get(key_var):
            return None, f"{provider.id} : clé absente du magasin ({key_var or provider.id.upper() + '_API_KEY'})"
    chosen, source = model, "policy" if model else ""
    if not chosen and tier:
        from grimoire.missions.launcher import SESSION_REQUIRES, provider_capabilities

        choice = resolve_model(
            tier,
            OPENCODE_HOST,
            canonical,
            table=load_model_table(root),
            capabilities=provider_capabilities(root, provider.id),
            requires=SESSION_REQUIRES,
        )
        chosen, source = choice.model or "", "table" if choice.model else ""
    if not chosen:
        moment = now or datetime.now().astimezone()
        state = load_state(root)
        models = available_models(provider, tier, state, now=moment) or available_models(
            provider, "cheap", state, now=moment
        )
        chosen, source = (models[0].id, "registry") if models else ("", "")
    if not chosen:
        return (
            None,
            f"{provider.id} : aucun modèle pour le palier {tier or 'cheap'} (table `dispatch.models.opencode` ou registre)",
        )
    return OpencodeRoute(
        provider.id, oc_provider, chosen, key_var, base_url or OPENCODE_BASE_URLS[oc_provider], source
    ), ""


#: Ce que le ``.gitignore`` posé dans ``.opencode/`` ignore : le plugin émis et ce qu'opencode installe lui-même
#: (les mêmes entrées que le ``.gitignore`` qu'il écrit). Rien d'autre : un fichier ajouté se voit dans ``git status``.
OPENCODE_IGNORED = (
    "plugin/grimoire-guard.js",
    "node_modules",
    "package.json",
    "package-lock.json",
    "bun.lock",
    ".gitignore",
)


def prepare_opencode_worktree(wt_path: Path) -> str:
    """Pose le plugin de garde du kit dans le worktree ; ``""`` ou la raison d'un refus.

    Le plugin est écrit depuis l'émetteur, à l'octet (:func:`.worker_guard.integrity_refusal`, qui refuse aussi tout
    autre code ou configuration opencode). Le ``.gitignore`` de ``.opencode/`` ne nomme que :data:`OPENCODE_IGNORED`.
    """
    from grimoire.missions.worker_guard import integrity_refusal

    if refusal := integrity_refusal(wt_path):
        return refusal
    from grimoire.core.fsio import atomic_write

    ignore = wt_path / ".opencode" / ".gitignore"
    expected = "".join(f"{line}\n" for line in OPENCODE_IGNORED)
    try:
        current = ignore.read_text(encoding="utf-8") if ignore.is_file() and not ignore.is_symlink() else None
    except OSError:
        current = None
    if current != expected:  # absent, lien ou contenu différent : remplacé, pour qu'aucun ajout ne soit masqué
        atomic_write(ignore, expected)
    return ""


def worker_log(root: Path, task_id: str) -> Path:
    """Le journal JSON d'un worker opencode : hors du worktree (aucun fichier non commité), sous les sorties du kit."""
    return root / "_grimoire-output" / "control" / "workers" / f"{task_id}.opencode.jsonl"
