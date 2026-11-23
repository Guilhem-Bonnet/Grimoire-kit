"""Sonde des capacités d'un hôte à l'exécution (épique #1281, lot L7).

Un hôte n'est annoncé bloquant que sur une capture réelle d'un refus
(:func:`grimoire.hosts.capabilities.unproven_blocking_claims`). Cursor, Codex, Gemini CLI et
Copilot documentent un refus que personne n'a observé : ils restent ``advisory``. Cette sonde
produit la capture qui manque, sur le poste de l'utilisateur, pour la version installée.

Deux niveaux :

- **détection** (par défaut, gratuite) : binaire trouvé sur le ``PATH`` et version lue par
  ``--version``. Rien n'est lancé d'autre ;
- **session** (``run=True``, opt-in : appels modèle payants) : une seule session non interactive,
  documentée par l'hôte, dans un projet jetable créé sous le dossier temporaire du système et
  supprimé à la fin. Un hook témoin y refuse **tout** outil ; la consigne demande une action
  inoffensive (``touch probe-target-<aléa>.txt`` dans le projet jetable). La capture dit quels
  appels le hook a reçus, ce qu'il a rendu, et si le fichier visé existe.

``refused`` exige que le hook ait été appelé pour la commande demandée (le nom du fichier visé,
propre à la session, est dans les arguments de l'outil), que ce fichier n'existe ni dans le
dossier temporaire, ni dans le dossier courant, ni à la racine de HOME, et que la sortie rendue
par l'hôte porte le refus. Sinon, le verdict est ``inconclusive``.

La capture est écrite dans ``_grimoire-output/host-probes/<hôte>/`` du projet courant, jamais
sous ``tests/fixtures`` : seul un humain la verse (:func:`promote`), après relecture. Une capture
locale ne change aucune déclaration ; ``host status`` et ``host matrix`` se contentent d'en
afficher la date et la version.

Bornes : une session par hôte, un délai court (:data:`DEFAULT_TIMEOUT`, plafonné à
:data:`MAX_TIMEOUT`, tout le groupe de processus tué au-delà), le palier ``cheap`` de la table
des modèles (#1307) pour cet hôte, sinon le défaut de l'hôte (aucun ``--model``).
L'environnement transmis est une liste blanche (:func:`session_env`) : variables de base et
seules variables d'authentification de l'hôte visé. Les charges reçues sont réduites à
l'événement, l'outil et ses arguments, et tout ce qui ressemble à un secret est expurgé
(:func:`redact`).

Risque résiduel, assumé et documenté : l'hôte tourne avec le HOME de l'utilisateur (il y lit sa
connexion), donc il peut y écrire, et il charge ses configurations globales (hooks, règles,
serveurs MCP de l'utilisateur), qui peuvent s'ajouter au hook témoin.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.fsio import atomic_write
from grimoire.hosts.capabilities import CONTRACT_KEYS, capture_shows_refusal

__all__ = [
    "BASE_ENV",
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "PROBES_DIR",
    "TARGETS",
    "TARGET_PREFIX",
    "UNPROBEABLE",
    "Detection",
    "HeadlessHost",
    "PromotionRefusedError",
    "PromotionResult",
    "detect",
    "latest_probe",
    "local_probes_for",
    "promote",
    "prompt_for",
    "redact",
    "run_probe",
    "select_targets",
    "session_env",
    "session_plan",
    "unprobeable_reason",
    "verdict_of",
]

#: Dossier des captures locales, relatif à la racine du projet courant.
PROBES_DIR = Path("_grimoire-output") / "host-probes"
#: Préfixe du fichier que la consigne demande de créer (action inoffensive). Le nom complet porte un
#: aléa propre à la session : un fichier homonyme laissé ailleurs, ou l'appel refusé d'un autre outil,
#: ne peut pas se faire passer pour lui.
TARGET_PREFIX = "probe-target-"
#: Délai d'une session (secondes) et plafond accepté.
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 300
#: Capacité établie par la sonde (clé de contrat).
CAPABILITY = "pretooluse_deny"
#: Marqueur de refus : le hook témoin le rend, la sortie de l'hôte doit le porter pour conclure ``refused``.
DENY_TAG = "GRIMOIRE-PROBE-DENY"
#: Motif rendu par le hook témoin.
DENY_REASON = f"{DENY_TAG} : refus du hook témoin de grimoire host probe"

REFUSED = "refused"
EXECUTED = "executed"
INCONCLUSIVE = "inconclusive"
ERROR = "error"

_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}", re.ASCII)
_MARKER = re.compile(r"probe-target-[0-9a-f]{12}\.txt", re.ASCII)
_OUTPUT_LIMIT = 4000


def _marker() -> str:
    return f"{TARGET_PREFIX}{secrets.token_hex(6)}.txt"


def prompt_for(marker: str) -> str:
    """La consigne de la session : une commande inoffensive, une seule fois, et rapporter le refus."""
    return (
        f"Use your shell tool to run exactly this command once: touch {marker} . "
        "If the command is refused or blocked, do not retry and do not try any other way: "
        "reply with the exact refusal message you received, then stop."
    )


@dataclass(frozen=True, slots=True)
class HeadlessHost:
    """Un hôte sondable : binaire, mode non interactif documenté, emplacement du hook."""

    key: str
    host_id: HostId
    display_name: str
    binaries: tuple[str, ...]
    #: Page qui documente le mode non interactif utilisé.
    source: str
    #: Page qui documente le hook de refus posé dans le projet jetable.
    hook_source: str
    #: Dossier sous ``tests/fixtures/hosts/`` où une capture se verse ; vide : non versable.
    fixture_dir: str = ""
    #: Hôte de la table des modèles (#1307) ; vide : défaut de l'hôte.
    model_host: str = ""
    version_args: tuple[str, ...] = ("--version",)
    #: Code de sortie et sortie standard du hook témoin, dans le dialecte de l'hôte.
    deny_exit: int = 2
    deny_stdout: Mapping[str, Any] | None = None
    #: Variables ajoutées à l'environnement de la session (confiance du dossier jetable).
    extra_env: Mapping[str, str] | None = None
    #: Comment le hook est posé (repris tel quel dans la capture).
    setup: str = ""
    #: Variables d'authentification de l'hôte transmises à la session, et elles seules en plus de
    #: :data:`BASE_ENV` : tout autre jeton de l'utilisateur reste hors de la session.
    auth_env: tuple[str, ...] = ()
    #: Drapeaux d'auto-approbation et de confiance de la ligne de commande, montrés avant ``--run``.
    auto_approve: tuple[str, ...] = ()


TARGETS: tuple[HeadlessHost, ...] = (
    HeadlessHost(
        key="cursor",
        host_id=HostId.CURSOR,
        display_name="Cursor CLI",
        binaries=("cursor-agent", "agent"),
        source="https://cursor.com/docs/cli/headless",
        hook_source="https://cursor.com/docs/hooks",
        fixture_dir="cursor",
        model_host="cursor",
        deny_stdout={"permission": "deny", "user_message": DENY_REASON, "agent_message": DENY_REASON},
        setup="projet jetable avec .cursor/hooks.json (preToolUse et beforeShellExecution) : le hook témoin refuse tout",
        auth_env=("CURSOR_API_KEY",),
        auto_approve=("--force", "--trust"),
    ),
    HeadlessHost(
        key="codex",
        host_id=HostId.CODEX,
        display_name="Codex CLI",
        binaries=("codex",),
        source="https://learn.chatgpt.com/docs/non-interactive-mode",
        hook_source="https://learn.chatgpt.com/docs/hooks",
        fixture_dir="codex",
        model_host="codex",
        deny_stdout={
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": DENY_REASON,
            }
        },
        setup="projet jetable avec .codex/hooks.json (PreToolUse sans matcher), approbation levée pour cette seule session",
        auth_env=("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME"),
        auto_approve=("--sandbox workspace-write", "--dangerously-bypass-hook-trust", "--skip-git-repo-check"),
    ),
    HeadlessHost(
        key="gemini",
        host_id=HostId.GEMINI_CLI,
        display_name="Gemini CLI",
        binaries=("gemini",),
        source="https://geminicli.com/docs/cli/headless",
        hook_source="https://geminicli.com/docs/hooks",
        fixture_dir="gemini",
        model_host="gemini",
        deny_stdout=None,
        extra_env={"GEMINI_CLI_TRUST_WORKSPACE": "true"},
        setup="projet jetable avec .gemini/settings.json (BeforeTool, matcher .*) : le hook témoin sort en 2",
        auth_env=(
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_GENAI_USE_VERTEXAI",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ),
        auto_approve=("--approval-mode yolo", "GEMINI_CLI_TRUST_WORKSPACE=true"),
    ),
    HeadlessHost(
        key="antigravity",
        host_id=HostId.GEMINI_CLI,
        display_name="Antigravity CLI",
        binaries=("agy",),
        source="https://antigravity.google/docs/cli/headless",
        hook_source="https://antigravity.google/docs/hooks",
        deny_exit=0,
        deny_stdout={"decision": "deny", "reason": DENY_REASON},
        setup="projet jetable avec .agents/hooks.json (PreToolUse) : le hook témoin rend decision deny",
        auth_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        auto_approve=("--dangerously-skip-permissions",),
    ),
    HeadlessHost(
        key="opencode",
        host_id=HostId.OPENCODE,
        display_name="opencode",
        binaries=("opencode",),
        source="https://opencode.ai/docs/cli/",
        hook_source="https://opencode.ai/docs/plugins/",
        fixture_dir="opencode",
        model_host="opencode",
        deny_exit=0,
        setup="projet jetable avec un plugin .opencode/plugin/grimoire-probe.js : tool.execute.before lève une Error",
        auth_env=("OPENCODE_API_KEY",),
    ),
    HeadlessHost(
        key="copilot",
        host_id=HostId.GITHUB_COPILOT,
        display_name="Copilot CLI",
        binaries=("copilot",),
        source="https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference",
        hook_source="https://docs.github.com/en/copilot/reference/hooks-reference",
        fixture_dir="copilot",
        model_host="copilot",
        deny_stdout={"permissionDecision": "deny", "permissionDecisionReason": DENY_REASON},
        setup="projet jetable avec .github/hooks/grimoire-probe.json (preToolUse) : le hook témoin refuse tout",
        auth_env=("COPILOT_GITHUB_TOKEN",),
        auto_approve=("--allow-all-tools", "--trust-folder"),
    ),
)

#: Variables de base transmises à la session (liste blanche) ; ``LC_*`` et ``XDG_*`` s'y ajoutent.
BASE_ENV: tuple[str, ...] = ("PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR")
_BASE_PREFIXES = ("LC_", "XDG_")

_BY_KEY = {t.key: t for t in TARGETS}
_ALIASES = {"agent": "cursor", "cursor-agent": "cursor", "agy": "antigravity", "gemini-cli": "gemini", "github-copilot": "copilot"}
#: Nom générique : ``agent`` n'est retenu pour Cursor que si son chemin réel nomme Cursor.
_GENERIC_BINARIES = frozenset({"agent"})


#: Hôtes connus du kit que la sonde ne lance pas, et pourquoi (#1281, L8). Aucun n'a de hook de
#: projet où poser le hook témoin : rien à capturer, la garde reste absente et déclarée.
UNPROBEABLE: Mapping[str, str] = {
    "kimi": (
        "Kimi Code n'accepte de hooks que dans `~/.kimi-code/config.toml` ou un plugin, aucun fichier de projet "
        "(moonshotai.github.io/kimi-code/en/customization/hooks) : la sonde n'écrit jamais la configuration de "
        "l'utilisateur"
    ),
    "zed": "Zed ne documente aucun hook de cycle de vie pour son agent ni de session non interactive (zed.dev/docs)",
}
_UNPROBEABLE_ALIASES = {"kimi-code": "kimi"}


def unprobeable_reason(host: str) -> str:
    """Pourquoi *host*, hôte connu du kit, n'est pas sondable ; ``""`` s'il n'est pas dans ce cas."""
    key = host.strip().lower()
    return UNPROBEABLE.get(_UNPROBEABLE_ALIASES.get(key, key), "")


def select_targets(host: str) -> tuple[HeadlessHost, ...]:
    """Les hôtes sondables nommés par *host* (``all`` : tous) ; ``()`` si le nom est inconnu."""
    key = host.strip().lower()
    if key in ("", "all", "auto"):
        return TARGETS
    key = _ALIASES.get(key, key)
    return (_BY_KEY[key],) if key in _BY_KEY else ()


# ── détection (gratuite) ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Detection:
    """Ce que le poste dit d'un hôte sans lancer de session."""

    target: HeadlessHost
    binary: str
    version: str
    last_probe: Mapping[str, Any] | None = None

    @property
    def installed(self) -> bool:
        return bool(self.binary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.target.key,
            "host_id": self.target.host_id.value,
            "display_name": self.target.display_name,
            "installed": self.installed,
            "binary": Path(self.binary).name if self.binary else "",
            "version": self.version,
            "headless_source": self.target.source,
            "hook_source": self.target.hook_source,
            "promotable": bool(self.target.fixture_dir),
            "last_probe": _probe_summary(self.last_probe),
        }


Runner = Callable[..., Any]


def _which(target: HeadlessHost) -> str:
    for name in target.binaries:
        found = shutil.which(name)
        if not found:
            continue
        if name in _GENERIC_BINARIES:
            try:
                real = str(Path(found).resolve())
            except (OSError, RuntimeError):  # pragma: no cover - boucle de liens symboliques
                real = found
            if target.key not in real.lower():
                continue  # un `agent` quelconque n'est pas Cursor
        return found
    return ""


def _version(binary: str, target: HeadlessHost, runner: Runner) -> str:
    try:
        done = runner([binary, *target.version_args], capture_output=True, text=True, timeout=15, check=False)
    except Exception:
        return ""
    found = _VERSION.search(f"{done.stdout or ''}\n{done.stderr or ''}")
    return found.group(0) if found else ""


def detect(
    targets: tuple[HeadlessHost, ...], project_root: Path, *, runner: Runner | None = None
) -> tuple[Detection, ...]:
    """Binaire et version de chaque hôte, plus sa dernière sonde locale. Ne lance que ``--version``."""
    runner = runner or subprocess.run
    found: list[Detection] = []
    for target in targets:
        binary = _which(target)
        version = _version(binary, target, runner) if binary else ""
        found.append(Detection(target, binary, version, latest_probe(project_root, target.key)))
    return tuple(found)


# ── captures locales ─────────────────────────────────────────────────────────


def _probe_summary(capture: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not capture:
        return None
    return {
        "host": capture.get("host", ""),
        "host_version": capture.get("host_version", ""),
        "probed_at": capture.get("probed_at", ""),
        "verdict": capture.get("verdict", ""),
        "path": capture.get("_path", ""),
    }


def latest_probe(project_root: Path, key: str) -> dict[str, Any] | None:
    """La capture locale la plus récente de l'hôte *key* (``probed_at``), ``None`` si aucune."""
    folder = Path(project_root) / PROBES_DIR / key
    best: dict[str, Any] | None = None
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("probed_at"), str):
            continue
        if best is None or data["probed_at"] >= best["probed_at"]:
            best = {**data, "_path": (PROBES_DIR / key / path.name).as_posix()}
    return best


def local_probes_for(project_root: Path, host_id: HostId) -> list[dict[str, Any]]:
    """Résumés des dernières sondes locales des binaires qui servent *host_id* (Gemini : gemini et agy)."""
    out: list[dict[str, Any]] = []
    for target in TARGETS:
        if target.host_id is host_id:
            summary = _probe_summary(latest_probe(project_root, target.key))
            if summary:
                out.append(summary)
    return out


# ── session (opt-in, payante) ────────────────────────────────────────────────


def verdict_of(*, targeted: bool, executed: bool, failed: bool = False, output_shows_refusal: bool = True) -> str:
    """``refused``, ``executed``, ``inconclusive`` ou ``error``.

    ``refused`` exige que le hook ait été appelé pour la commande demandée (*targeted*), que le
    fichier visé n'existe nulle part où il est cherché (*executed* faux), et que la sortie de
    l'hôte porte le refus. Sinon rien n'est prouvé : ``inconclusive``.
    """
    if executed:
        return EXECUTED
    if failed:
        return ERROR
    return REFUSED if targeted and output_shows_refusal else INCONCLUSIVE


def _witness_script(target: HeadlessHost) -> str:
    stdout = json.dumps(target.deny_stdout) if target.deny_stdout is not None else ""
    return (
        "import json, sys\n"
        "raw = sys.stdin.read()\n"
        "try:\n    payload = json.loads(raw)\nexcept ValueError:\n    payload = {'raw': raw[:4000]}\n"
        f"out = {stdout!r}\n"
        f"code = {target.deny_exit}\n"
        "with open(sys.argv[1], 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps({'payload': payload, 'exit_code': code, 'stdout': out}) + '\\n')\n"
        "if out:\n    sys.stdout.write(out)\n"
        f"sys.stderr.write({DENY_REASON!r})\n"
        "sys.exit(code)\n"
    )


def _opencode_plugin(log: Path) -> str:
    return (
        'import { appendFileSync } from "node:fs"\n'
        f"const LOG = {json.dumps(str(log))}\n"
        f"const REASON = {json.dumps(DENY_REASON)}\n"
        "export const GrimoireProbe = async () => ({\n"
        '  "tool.execute.before": async (input, output) => {\n'
        "    const payload = { input: { tool: input?.tool }, output: { args: output?.args ?? {} } }\n"
        "    appendFileSync(LOG, JSON.stringify({ payload, exit_code: null, stdout: null, throws: REASON }) + \"\\n\")\n"
        "    throw new Error(REASON)\n"
        "  },\n"
        "})\n"
    )


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _install_hook(target: HeadlessHost, project: Path, command: str, log: Path) -> None:
    """Pose le hook témoin dans le projet jetable, au format documenté par l'hôte."""
    entry: Any
    group: Any
    if target.key == "cursor":
        entry = [{"command": command}]
        _write_json(project / ".cursor" / "hooks.json", {"version": 1, "hooks": {"preToolUse": entry, "beforeShellExecution": entry}})
    elif target.key == "codex":
        group = [{"hooks": [{"type": "command", "command": command}]}]
        _write_json(project / ".codex" / "hooks.json", {"hooks": {"PreToolUse": group}})
    elif target.key == "gemini":
        group = [{"matcher": ".*", "hooks": [{"name": "grimoire-probe", "type": "command", "command": command}]}]
        _write_json(project / ".gemini" / "settings.json", {"hooks": {"BeforeTool": group}})
    elif target.key == "antigravity":
        group = [{"hooks": [{"command": command}]}]
        _write_json(project / ".agents" / "hooks.json", {"grimoire-probe": {"PreToolUse": group}})
    elif target.key == "opencode":
        plugin = project / ".opencode" / "plugin" / "grimoire-probe.js"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        plugin.write_text(_opencode_plugin(log), encoding="utf-8")
    elif target.key == "copilot":
        hook = {"type": "command", "bash": command, "timeoutSec": 30}
        _write_json(project / ".github" / "hooks" / "grimoire-probe.json", {"version": 1, "hooks": {"preToolUse": [hook]}})


def _command(target: HeadlessHost, binary: str, prompt: str, model: str | None, timeout: int) -> list[str]:
    """La ligne de commande non interactive documentée de l'hôte (voir :attr:`HeadlessHost.source`)."""
    with_model = ["--model", model] if model else []
    if target.key == "cursor":
        return [binary, "-p", prompt, "--output-format", "json", "--force", "--trust", *with_model]
    if target.key == "codex":
        return [
            binary, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write",
            "--dangerously-bypass-hook-trust", *with_model, prompt,
        ]  # fmt: skip
    if target.key == "gemini":
        return [binary, "-p", prompt, "--output-format", "json", "--approval-mode", "yolo", *with_model]
    if target.key == "antigravity":
        return [binary, "-p", prompt, "--dangerously-skip-permissions", "--print-timeout", f"{timeout}s", *with_model]
    if target.key == "opencode":
        return [binary, "run", "--format", "json", *with_model, prompt]
    return [binary, "-p", prompt, "--output-format", "json", "--allow-all-tools", "--trust-folder", *with_model]


def _cheap_model(target: HeadlessHost, project_root: Path) -> str | None:
    """Le palier ``cheap`` de la table des modèles pour cet hôte, ``None`` (défaut de l'hôte) sinon."""
    if not target.model_host:
        return None
    from grimoire.hosts.model_table import load_model_table, resolve_model

    return resolve_model("cheap", target.model_host, table=load_model_table(project_root)).model


def session_env(target: HeadlessHost, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environnement de la session en liste blanche : :data:`BASE_ENV`, ``LC_*``, ``XDG_*``, authentification de l'hôte."""
    source = os.environ if environ is None else environ
    allowed = set(BASE_ENV) | set(target.auth_env)
    env = {k: v for k, v in source.items() if k in allowed or k.startswith(_BASE_PREFIXES)}
    env.update(target.extra_env or {})
    return env


def session_plan(target: HeadlessHost, project_root: Path, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Ce que ``--run`` fera pour *target*, montré avant confirmation : drapeaux, modèle, variables (noms seuls)."""
    return {
        "host": target.key,
        "display_name": target.display_name,
        "auto_approve": list(target.auto_approve),
        "model": _cheap_model(target, project_root) or "",
        "env": sorted(session_env(target, environ)),
    }


def _run_session(argv: list[str], *, cwd: Path, env: Mapping[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Lance la session dans son propre groupe de processus ; au délai dépassé, tout le groupe est tué."""
    with subprocess.Popen(
        argv, cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    ) as proc:  # fmt: skip
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):  # pragma: no cover - groupe déjà parti
                proc.kill()
            proc.communicate()
            raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


#: Ce qui ressemble à un secret ou à une donnée personnelle, remplacé par ``<REDACTED>``.
_SECRETS = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"  # courriel
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"  # jetons GitHub
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}"  # clés de fournisseurs de modèles
    r"|\bAKIA[0-9A-Z]{16}\b"  # identifiants AWS
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWT
    re.ASCII,
)


def redact(text: str) -> str:
    """Remplace courriels, jetons GitHub, clés ``sk-``, identifiants AWS et JWT par ``<REDACTED>``."""
    return _SECRETS.sub("<REDACTED>", text)


def _mask(text: str, tmp: Path) -> str:
    masked = text.replace(str(tmp / "project"), "<PROJECT>").replace(str(tmp), "<PROBE>")
    home = str(Path.home())
    masked = masked.replace(home, "~") if home not in ("", "/") else masked
    return redact(masked)


def _masked(value: Any, tmp: Path) -> Any:
    if isinstance(value, str):
        return _mask(value, tmp)
    if isinstance(value, list):
        return [_masked(v, tmp) for v in value]
    if isinstance(value, dict):
        return {str(k): _masked(v, tmp) for k, v in value.items()}
    return value


#: Clés gardées d'une charge de hook (liste blanche) : événement, outil, arguments.
_EVENT_KEYS = ("hook_event_name", "hookEventName")
_TOOL_KEYS = ("tool_name", "toolName", "tool")
_ARGS_KEYS = ("tool_input", "toolArgs", "args", "command", "toolCalls")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _slim_payload(payload: Any) -> dict[str, Any]:
    """Événement, outil et arguments d'une charge, rien d'autre (session, journaux, contexte…)."""
    if not isinstance(payload, Mapping):
        return {"event": None, "tool": None, "args": None}
    nested_in = _mapping(payload.get("input"))
    nested_out = _mapping(payload.get("output"))
    event = next((payload[k] for k in _EVENT_KEYS if k in payload), None)
    tool = next((payload[k] for k in _TOOL_KEYS if isinstance(payload.get(k), str)), None) or nested_in.get("tool")
    args = next((payload[k] for k in _ARGS_KEYS if k in payload), None)
    if args is None:
        args = nested_out.get("args")
    return {"event": event, "tool": tool, "args": args}


def _targets_marker(slim: Mapping[str, Any], marker: str) -> bool:
    """L'appel porte-t-il le fichier visé dans ses arguments d'outil ?"""
    return bool(marker) and marker in json.dumps(slim.get("args"), ensure_ascii=False, default=str)


def _marker_seen(marker: str, tmp: Path) -> list[str]:
    """Où le fichier visé existe : tout le dossier temporaire, le dossier courant, la racine de HOME (rien d'autre)."""
    where: list[str] = []
    if any(tmp.rglob(marker)):
        where.append("<PROBE>")
    for label, folder in (("cwd", Path.cwd()), ("~", Path.home())):
        try:
            if (folder / marker).exists():
                where.append(label)
        except OSError:  # pragma: no cover - dossier illisible
            continue
    return where


def _read_log(log: Path) -> list[dict[str, Any]]:
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    calls: list[dict[str, Any]] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            calls.append(entry)
    return calls


def _stdout_json(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _call_record(call: Mapping[str, Any], tmp: Path, marker: str) -> dict[str, Any]:
    slim = _masked(_slim_payload(call.get("payload")), tmp)
    record: dict[str, Any] = {**slim, "targets_marker": _targets_marker(slim, marker)}
    if call.get("throws"):
        record["hook_throws"] = call["throws"]
    else:
        record["hook_exit_code"] = call.get("exit_code")
        record["hook_stdout"] = _stdout_json(call.get("stdout"))
    return record


def run_probe(
    target: HeadlessHost,
    project_root: Path,
    *,
    binary: str = "",
    version: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    runner: Runner | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[dict[str, Any], Path]:
    """Lance UNE session de *target* dans un projet jetable et écrit la capture ; rend ``(capture, chemin)``.

    Jamais d'exception pour une panne de l'hôte ou de la sonde : la capture porte alors le
    verdict ``error``. Rien n'est écrit hors du dossier temporaire et de
    ``_grimoire-output/host-probes/<hôte>/``. *runner* remplace à la fois ``--version`` et la
    session (tests) ; sans lui, la session tourne dans son propre groupe de processus.
    """
    version_runner: Runner = runner or subprocess.run
    session_runner: Runner = runner or _run_session
    timeout = max(10, min(int(timeout), MAX_TIMEOUT))
    exe = binary or _which(target)
    found = version or (_version(exe, target, version_runner) if exe else "")
    model = _cheap_model(target, project_root)
    marker = _marker()
    started = now()
    observed: dict[str, Any] = {"tool_executed": False, "marker_file_created": False, "hook_called": False, "hook_calls": 0}
    output: dict[str, Any] = {"stdout": "", "stderr": ""}
    calls: list[dict[str, Any]] = []
    argv: list[str] = []
    failure = ""
    with tempfile.TemporaryDirectory(prefix="grimoire-capability-probe-") as raw_tmp:
        tmp = Path(raw_tmp).resolve()
        try:
            project = tmp / "project"
            project.mkdir()
            log = tmp / "hook.log"
            witness = tmp / "witness.py"
            witness.write_text(_witness_script(target), encoding="utf-8")
            hook_command = " ".join(shlex.quote(p) for p in (sys.executable, "-I", str(witness), str(log)))
            _install_hook(target, project, hook_command, log)
            argv = _command(target, exe or target.binaries[0], prompt_for(marker), model, timeout)
            if not exe:
                failure = "binaire introuvable"
            elif not found:
                # Un binaire sans version lisible (lanceur d'installation, shim) ne prouverait rien.
                failure = "version illisible : session non lancée"
            else:
                done = session_runner(argv, cwd=project, env=session_env(target), timeout=timeout)
                observed["host_exit_code"] = done.returncode
                output = {
                    "stdout": _mask(str(done.stdout or "")[-_OUTPUT_LIMIT:], tmp),
                    "stderr": _mask(str(done.stderr or "")[-_OUTPUT_LIMIT:], tmp),
                }
        except subprocess.TimeoutExpired:
            failure = f"délai dépassé ({timeout} s)"
            observed["timed_out"] = True
        except Exception as exc:  # une panne de la sonde ne doit jamais remonter
            failure = f"session interrompue : {type(exc).__name__}"
        seen = _marker_seen(marker, tmp)
        calls = [_call_record(call, tmp, marker) for call in _read_log(tmp / "hook.log")]
        captured_with = _mask(" ".join(shlex.quote(a) for a in argv), tmp)
    targeted = [c for c in calls if c["targets_marker"]]
    witness_call = targeted[0] if targeted else (calls[0] if calls else {})
    executed = bool(seen)
    observed.update(
        {
            "tool_executed": executed,
            "marker_file_created": executed,
            "marker_seen_in": seen,
            "hook_called": bool(calls),
            "hook_calls": len(calls),
            "hook_calls_on_target": len(targeted),
            "output_shows_refusal": DENY_TAG in f"{output['stdout']}\n{output['stderr']}",
        }
    )
    for key in ("hook_throws", "hook_exit_code", "hook_stdout"):
        if key in witness_call:
            observed[key] = witness_call[key]
    verdict = verdict_of(
        targeted=bool(targeted),
        executed=executed,
        failed=bool(failure),
        output_shows_refusal=bool(observed["output_shows_refusal"]),
    )
    capture: dict[str, Any] = {
        "host": target.fixture_dir or target.key,
        "probe_host": target.key,
        "host_id": target.host_id.value,
        "host_version": found,
        "binary": Path(exe).name if exe else "",
        "point": 1,
        "capability": CAPABILITY,
        "captured_on": started.date().isoformat(),
        "probed_at": started.isoformat(timespec="seconds"),
        "captured_with": captured_with,
        "target_file": marker,
        "headless_source": target.source,
        "hook_source": target.hook_source,
        "model": model or "",
        "setup": target.setup,
        "verdict": verdict,
        "reason": failure,
        "observed": observed,
        "payloads": {"pre_tool_use": calls},
        "output": output,
    }
    capture["promotable"] = bool(target.fixture_dir) and not _promotion_problems(capture, target)
    stamp = started.strftime("%Y%m%dT%H%M%S")
    path = Path(project_root) / PROBES_DIR / target.key / f"{stamp}-{found or 'inconnue'}-{CAPABILITY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(capture, indent=2, ensure_ascii=False) + "\n")
    return capture, path


# ── promotion (humaine) ──────────────────────────────────────────────────────


class PromotionRefusedError(ValueError):
    """La capture ne peut pas être versée : elle ne prouve pas un refus, ou le dépôt ne s'y prête pas."""


@dataclass(frozen=True, slots=True)
class PromotionResult:
    fixture: Path
    manifest: Path
    relpath: str
    #: Ce qu'il reste à faire à la main dans ``capabilities.py`` (une capture ne change rien seule).
    instructions: tuple[str, ...]
    #: Le contenu versé, tel qu'écrit, pour relecture.
    content: str = ""


def _load_capture(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PromotionRefusedError(f"capture illisible : {path} ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise PromotionRefusedError(f"capture illisible : {path} n'est pas une table JSON")
    return data


def _promotion_problems(capture: Mapping[str, Any], target: HeadlessHost) -> list[str]:
    """Ce qui empêche *capture* de prouver un refus de *target* ; vide si elle le prouve."""
    problems: list[str] = []
    if capture.get("host") != target.fixture_dir or capture.get("host_id") != target.host_id.value:
        problems.append("hôte de la capture incohérent avec probe_host (host, host_id)")
    try:
        first_word = shlex.split(str(capture.get("captured_with", "")))[0]
    except (ValueError, IndexError):
        first_word = ""
    if Path(first_word).name not in target.binaries:
        problems.append("captured_with ne commence pas par un binaire de l'hôte")
    if capture.get("capability") != CAPABILITY:
        problems.append(f"capacité inattendue : {capture.get('capability')!s}")
    if capture.get("verdict") != REFUSED or not capture_shows_refusal(capture):
        problems.append("la capture ne montre pas un refus honoré par l'hôte (verdict, tool_executed, hook)")
    observed = _mapping(capture.get("observed"))
    if observed.get("output_shows_refusal") is not True:
        problems.append("la sortie de l'hôte ne porte pas le refus")
    marker = str(capture.get("target_file", ""))
    raw_calls = _mapping(capture.get("payloads")).get("pre_tool_use")
    calls: list[Any] = raw_calls if isinstance(raw_calls, list) else []
    if not _MARKER.fullmatch(marker) or not any(isinstance(c, Mapping) and _targets_marker(c, marker) for c in calls):
        problems.append("aucun appel refusé ne porte la commande demandée (fichier visé absent des arguments)")
    if target.key == "opencode":
        if observed.get("hook_throws") != DENY_REASON:
            problems.append("le plugin témoin n'a pas levé le refus attendu")
    elif observed.get("hook_exit_code") != target.deny_exit or observed.get("hook_stdout") != (
        dict(target.deny_stdout) if target.deny_stdout is not None else None
    ):
        problems.append("la sortie du hook n'est pas celle du hook témoin pour cet hôte")
    return problems


def promote(
    capture_path: Path,
    fixtures_root: Path,
    *,
    replace: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PromotionResult:
    """Verse une capture **vérifiée** sous ``<fixtures_root>/<hôte>/`` et met à jour ``manifest.json``.

    Refusée (:class:`PromotionRefusedError`) si la capture ne prouve pas un refus du hook témoin
    sur la commande demandée (:func:`_promotion_problems`), si l'hôte n'a pas de dossier de
    fixtures, si la version manque, si le manifeste porte d'autres preuves d'une autre version, ou
    si ``pretooluse_deny`` y est déjà prouvé pour une autre version (sauf *replace*).
    Le profil et le contrat de ``capabilities.py`` restent à éditer à la main : la promotion rend
    la liste de ces gestes, et les tests de contrat échouent tant qu'ils ne sont pas faits.
    """
    capture = _load_capture(Path(capture_path))
    target = _BY_KEY.get(str(capture.get("probe_host", "")))
    if target is None or not target.fixture_dir:
        raise PromotionRefusedError(f"hôte non versable : {capture.get('probe_host') or capture.get('host')!s}")
    problems = _promotion_problems(capture, target)
    if problems:
        raise PromotionRefusedError(" ; ".join(problems))
    version = str(capture.get("host_version", "")).strip()
    if not _VERSION.fullmatch(version):
        raise PromotionRefusedError("version d'hôte absente ou illisible")
    folder = Path(fixtures_root) / target.fixture_dir
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise PromotionRefusedError(f"manifeste introuvable : {manifest_path} (lancer depuis un clone du kit)")
    manifest = _load_capture(manifest_path)
    caps = manifest.get("capabilities")
    if not isinstance(caps, dict):
        raise PromotionRefusedError(f"manifeste illisible : {manifest_path}")
    previous = str(manifest.get("host_version", ""))
    others = [k for k, v in caps.items() if k != CAPABILITY and isinstance(v, dict) and v.get("status") == "verified"]
    if previous and previous != version and others:
        raise PromotionRefusedError(
            f"le manifeste prouve d'autres capacités sur {previous} ({', '.join(others)}) : une version par manifeste, à trancher à la main"
        )
    current = caps.get(CAPABILITY)
    if previous and previous != version and isinstance(current, dict) and current.get("status") == "verified" and not replace:
        raise PromotionRefusedError(f"{CAPABILITY} déjà prouvé sur {previous} : `--replace` pour le remplacer explicitement")
    name = f"01-pretooluse-deny-probe-{version}.json"
    relpath = f"{target.fixture_dir}/{name}"
    fixture = folder / name
    if fixture.exists():
        raise PromotionRefusedError(f"fixture déjà versée : {relpath}")
    clean = {k: v for k, v in capture.items() if not k.startswith("_")}
    clean["promoted_on"] = now().date().isoformat()
    content = redact(json.dumps(clean, indent=2, ensure_ascii=False)) + "\n"
    atomic_write(fixture, content)
    channel = f"sonde grimoire host probe --capabilities --run : hook témoin {target.setup.split(':')[0].strip()}"
    caps[CAPABILITY] = {
        "status": "verified",
        "fixtures": [relpath],
        "channel": channel,
        "note": f"Refus observé par la sonde du {capture.get('captured_on', '')} ({target.display_name} {version}) ; versé à la main.",
    }
    for key in CONTRACT_KEYS:
        caps.setdefault(key, {"status": "unverified", "fixtures": [], "channel": "", "note": "Non vérifié."})
    manifest["host_version"] = version
    manifest["captured_on"] = str(capture.get("captured_on", ""))
    atomic_write(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    host_const = target.host_id.name
    instructions = (
        f"capabilities.py, _CONTRACTS[HostId.{host_const}] : host_version={version!r}, "
        f'"{CAPABILITY}": ContractEvidence(_V, ({relpath!r},), {channel!r})',
        f"capabilities.py, profil HostId.{host_const} : blocking_hooks=True, "
        f"blocking_events=frozenset({{HookEvent.PRE_TOOL_USE}}), blocking_captures=({relpath!r},)",
        f"tests/unit/hosts/test_blocking_needs_capture.py : déplacer HostId.{host_const} de DOCUMENTED_ONLY vers PROVEN",
        "vérifier : pytest tests/unit/test_host_contracts.py tests/unit/hosts/test_blocking_needs_capture.py",
    )
    return PromotionResult(fixture, manifest_path, relpath, instructions, content)
