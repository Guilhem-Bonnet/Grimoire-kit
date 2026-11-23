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
  inoffensive (``touch probe-target.txt`` dans le projet jetable). La capture dit si le hook a été
  appelé, ce qu'il a rendu, et si l'action s'est exécutée.

La capture est écrite dans ``_grimoire-output/host-probes/<hôte>/`` du projet courant, jamais
sous ``tests/fixtures`` : seul un humain la verse (:func:`promote`), après relecture. Une capture
locale ne change aucune déclaration ; ``host status`` et ``host matrix`` se contentent d'en
afficher la date et la version.

Bornes : une session par hôte, un délai court (:data:`DEFAULT_TIMEOUT`, plafonné à
:data:`MAX_TIMEOUT`), le palier ``cheap`` de la table des modèles (#1307) pour cet hôte, sinon le
défaut de l'hôte (aucun ``--model``). L'environnement transmis à l'hôte est celui de
l'utilisateur (l'hôte a besoin de ses identifiants) moins les variables ``GRIMOIRE_*`` ; la sonde
n'en lit aucune valeur, et les chemins du poste sont masqués dans la capture.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
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
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "PROBES_DIR",
    "TARGETS",
    "TARGET_FILE",
    "Detection",
    "HeadlessHost",
    "PromotionRefusedError",
    "PromotionResult",
    "detect",
    "latest_probe",
    "local_probes_for",
    "promote",
    "run_probe",
    "select_targets",
    "verdict_of",
]

#: Dossier des captures locales, relatif à la racine du projet courant.
PROBES_DIR = Path("_grimoire-output") / "host-probes"
#: Fichier que la consigne demande de créer dans le projet jetable (action inoffensive).
TARGET_FILE = "probe-target.txt"
#: Délai d'une session (secondes) et plafond accepté.
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 300
#: Capacité établie par la sonde (clé de contrat).
CAPABILITY = "pretooluse_deny"
#: Motif rendu par le hook témoin.
DENY_REASON = "GRIMOIRE-PROBE-DENY : refus du hook témoin de grimoire host probe"

PROMPT = (
    f"Use your shell tool to run exactly this command once: touch {TARGET_FILE} . "
    "If the command is refused or blocked, do not retry and do not try any other way: just stop."
)

REFUSED = "refused"
EXECUTED = "executed"
INCONCLUSIVE = "inconclusive"
ERROR = "error"

_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
_OUTPUT_LIMIT = 4000


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
    ),
)

_BY_KEY = {t.key: t for t in TARGETS}
_ALIASES = {"agent": "cursor", "cursor-agent": "cursor", "agy": "antigravity", "gemini-cli": "gemini", "github-copilot": "copilot"}


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
        if found:
            return found
    return ""


def _version(binary: str, target: HeadlessHost, runner: Runner) -> str:
    try:
        done = runner([binary, *target.version_args], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
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


def verdict_of(*, hook_called: bool, executed: bool, failed: bool = False) -> str:
    """``refused`` (hook appelé, action non exécutée), ``executed``, ``inconclusive`` ou ``error``."""
    if executed:
        return EXECUTED
    if failed:
        return ERROR
    return REFUSED if hook_called else INCONCLUSIVE


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


def _command(target: HeadlessHost, binary: str, model: str | None, timeout: int) -> list[str]:
    """La ligne de commande non interactive documentée de l'hôte (voir :attr:`HeadlessHost.source`)."""
    with_model = ["--model", model] if model else []
    if target.key == "cursor":
        return [binary, "-p", PROMPT, "--output-format", "json", "--force", "--trust", *with_model]
    if target.key == "codex":
        return [
            binary, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write",
            "--dangerously-bypass-hook-trust", *with_model, PROMPT,
        ]  # fmt: skip
    if target.key == "gemini":
        return [binary, "-p", PROMPT, "--output-format", "json", "--approval-mode", "yolo", *with_model]
    if target.key == "antigravity":
        return [binary, "-p", PROMPT, "--dangerously-skip-permissions", "--print-timeout", f"{timeout}s", *with_model]
    if target.key == "opencode":
        return [binary, "run", "--format", "json", *with_model, PROMPT]
    return [binary, "-p", PROMPT, "--output-format", "json", "--allow-all-tools", "--trust-folder", *with_model]


def _cheap_model(target: HeadlessHost, project_root: Path) -> str | None:
    """Le palier ``cheap`` de la table des modèles pour cet hôte, ``None`` (défaut de l'hôte) sinon."""
    if not target.model_host:
        return None
    from grimoire.hosts.model_table import load_model_table, resolve_model

    return resolve_model("cheap", target.model_host, table=load_model_table(project_root)).model


def _session_env(target: HeadlessHost) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GRIMOIRE_")}
    env.update(target.extra_env or {})
    return env


def _mask(text: str, tmp: Path) -> str:
    masked = text.replace(str(tmp / "project"), "<PROJECT>").replace(str(tmp), "<PROBE>")
    home = str(Path.home())
    return masked.replace(home, "~") if home not in ("", "/") else masked


def _masked(value: Any, tmp: Path) -> Any:
    if isinstance(value, str):
        return _mask(value, tmp)
    if isinstance(value, list):
        return [_masked(v, tmp) for v in value]
    if isinstance(value, dict):
        return {k: _masked(v, tmp) for k, v in value.items()}
    return value


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

    Jamais d'exception pour une panne de l'hôte (binaire disparu, délai dépassé) : la capture
    porte alors le verdict ``error``. Rien n'est écrit hors du dossier temporaire et de
    ``_grimoire-output/host-probes/<hôte>/``.
    """
    runner = runner or subprocess.run
    timeout = max(10, min(int(timeout), MAX_TIMEOUT))
    exe = binary or _which(target)
    found = version or (_version(exe, target, runner) if exe else "")
    model = _cheap_model(target, project_root)
    started = now()
    observed: dict[str, Any] = {"tool_executed": False, "marker_file_created": False, "hook_called": False}
    output: dict[str, Any] = {"stdout": "", "stderr": ""}
    payloads: dict[str, Any] = {}
    argv: list[str] = []
    failure = ""
    with tempfile.TemporaryDirectory(prefix="grimoire-capability-probe-") as raw_tmp:
        tmp = Path(raw_tmp).resolve()
        project = tmp / "project"
        project.mkdir()
        log = tmp / "hook.log"
        witness = tmp / "witness.py"
        witness.write_text(_witness_script(target), encoding="utf-8")
        hook_command = " ".join(shlex.quote(p) for p in (sys.executable, "-I", str(witness), str(log)))
        _install_hook(target, project, hook_command, log)
        argv = _command(target, exe or target.binaries[0], model, timeout)
        if not exe:
            failure = "binaire introuvable"
        elif not found:
            # Un binaire sans version lisible (lanceur d'installation, shim) ne prouverait rien : pas de session.
            failure = "version illisible : session non lancée"
        else:
            try:
                done = runner(
                    argv, cwd=project, env=_session_env(target), capture_output=True, text=True,
                    timeout=timeout, check=False, stdin=subprocess.DEVNULL,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                failure = f"délai dépassé ({timeout} s)"
                observed["timed_out"] = True
            except (OSError, subprocess.SubprocessError) as exc:
                failure = f"session interrompue : {type(exc).__name__}"
            else:
                observed["host_exit_code"] = done.returncode
                output = {
                    "stdout": _mask(str(done.stdout or "")[-_OUTPUT_LIMIT:], tmp),
                    "stderr": _mask(str(done.stderr or "")[-_OUTPUT_LIMIT:], tmp),
                }
        executed = (project / TARGET_FILE).exists()
        calls = _read_log(log)
        observed.update({"tool_executed": executed, "marker_file_created": executed, "hook_called": bool(calls)})
        observed["hook_calls"] = len(calls)
        if calls:
            first = calls[0]
            payloads["pre_tool_use"] = _masked(first.get("payload"), tmp)
            if first.get("throws"):
                observed["hook_throws"] = first["throws"]
            else:
                observed["hook_exit_code"] = first.get("exit_code")
                observed["hook_stdout"] = _stdout_json(first.get("stdout"))
        captured_with = _mask(" ".join(shlex.quote(a) for a in argv), tmp)
    verdict = verdict_of(hook_called=bool(observed["hook_called"]), executed=bool(observed["tool_executed"]), failed=bool(failure))
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
        "headless_source": target.source,
        "hook_source": target.hook_source,
        "model": model or "",
        "setup": target.setup,
        "verdict": verdict,
        "reason": failure,
        "observed": observed,
        "payloads": payloads,
        "output": output,
        "promotable": verdict == REFUSED and capture_shows_refusal({"observed": observed}) and bool(target.fixture_dir),
    }
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


def _load_capture(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PromotionRefusedError(f"capture illisible : {path} ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise PromotionRefusedError(f"capture illisible : {path} n'est pas une table JSON")
    return data


def promote(capture_path: Path, fixtures_root: Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> PromotionResult:
    """Verse une capture **vérifiée** sous ``<fixtures_root>/<hôte>/`` et met à jour ``manifest.json``.

    Refusée (:class:`PromotionRefusedError`) si la capture ne montre pas un refus honoré
    (:func:`capture_shows_refusal` et verdict ``refused``), si l'hôte n'a pas de dossier de
    fixtures, si la version manque, ou si le manifeste porte d'autres preuves d'une autre version.
    Le profil et le contrat de ``capabilities.py`` restent à éditer à la main : la promotion rend
    la liste de ces gestes, et les tests de contrat échouent tant qu'ils ne sont pas faits.
    """
    capture = _load_capture(Path(capture_path))
    target = _BY_KEY.get(str(capture.get("probe_host", "")))
    if target is None or not target.fixture_dir:
        raise PromotionRefusedError(f"hôte non versable : {capture.get('probe_host') or capture.get('host')!s}")
    if capture.get("verdict") != REFUSED or not capture_shows_refusal(capture):
        raise PromotionRefusedError("la capture ne montre pas un refus honoré par l'hôte (verdict, tool_executed, hook)")
    if capture.get("capability") != CAPABILITY:
        raise PromotionRefusedError(f"capacité inattendue : {capture.get('capability')!s}")
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
    name = f"01-pretooluse-deny-probe-{version}.json"
    relpath = f"{target.fixture_dir}/{name}"
    fixture = folder / name
    if fixture.exists():
        raise PromotionRefusedError(f"fixture déjà versée : {relpath}")
    clean = {k: v for k, v in capture.items() if not k.startswith("_")}
    clean["promoted_on"] = now().date().isoformat()
    atomic_write(fixture, json.dumps(clean, indent=2, ensure_ascii=False) + "\n")
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
    return PromotionResult(fixture, manifest_path, relpath, instructions)
