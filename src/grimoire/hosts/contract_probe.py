"""Sonde du contrat d'hôte, relancée à chaque nouvelle version (#876, verrou 5).

Le frein (``pretooluse_deny`` : un refus au PreToolUse arrête réellement
l'outil) n'est prouvé en dur que pour la version capturée à la main (Claude Code
2.1.301). Une version de plus et toute reprise tombait en ``waiting(human)``.

Ici la preuve se refait, sur opt-in (``GRIMOIRE_HOST_PROBE=1``), pour la version **exacte** de l'hôte :
deux passes contre le binaire (témoin : un hook qui laisse passer, l'outil
s'exécute ; refus : un hook qui sort en 2, l'outil ne s'exécute pas). Le
résultat est consigné dans l'état utilisateur
(``~/.grimoire/control/host-contracts.json``), une entrée par ``hôte@version``.

Jamais de plage présumée : une preuve ne vaut que pour la version qui l'a
produite. Un échec (binaire absent, délai dépassé, témoin muet, outil exécuté
malgré le refus) est consigné comme échec et vaut « pas de frein » ; il n'est
rejoué qu'après :data:`RETRY_AFTER`. Aucune exception ne remonte à l'appelant.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grimoire.bridges.schemas import HostId
from grimoire.core.fsio import atomic_write

#: Capacité que la sonde établit.
CAPABILITY = "pretooluse_deny"
#: Opt-in du déclenchement automatique (``1``) : la sonde lance l'hôte deux fois avec les
#: identifiants de l'utilisateur (appels payants), donc rien ne part sans lui. Sans opt-in,
#: ``host status --probe`` reste le geste explicite.
PROBE_ENV = "GRIMOIRE_HOST_PROBE"
#: Délai maximal d'une passe de la sonde (secondes).
PROBE_TIMEOUT = 90
#: Un échec n'est rejoué qu'après ce délai.
RETRY_AFTER = timedelta(days=1)

#: Réglages managés de Claude Code sous Linux/WSL (doc « Deploy managed settings ») ; les fichiers de
#: ``managed-settings.d/*.json`` s'y ajoutent, par ordre alphabétique. Les autres systèmes ne sont pas lus.
MANAGED_DIR = Path("/etc/claude-code")

_MARKER = "EXECUTED.marker"
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
_HOSTS = {HostId.CLAUDE_CODE_CLI: "claude"}
#: ``(binaire, mtime_ns, taille)`` → version lue : ``claude --version`` ne se relance pas à chaque
#: bail et à chaque tick pendant la vie du processus ; un binaire remplacé change de clé.
_VERSION_CACHE: dict[tuple[str, int, int], str] = {}


@dataclass(frozen=True, slots=True)
class ProbeProof:
    """Résultat consigné d'une sonde : pour une version d'hôte exacte."""

    host: str
    version: str
    ok: bool
    probed_at: str
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.host}@{self.version}"


def proofs_path() -> Path:
    return Path.home() / ".grimoire" / "control" / "host-contracts.json"


def _load() -> dict[str, dict[str, object]]:
    try:
        data = json.loads(proofs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def proof_for(host_id: HostId, version: str | None) -> ProbeProof | None:
    """La preuve consignée pour *exactement* cette version, ``None`` si aucune."""
    wanted = (version or "").strip()
    if not wanted:
        return None
    entry = _load().get(f"{host_id.value}@{wanted}")
    if not isinstance(entry, dict):
        return None
    return ProbeProof(
        host=host_id.value,
        version=wanted,
        ok=entry.get("ok") is True,
        probed_at=str(entry.get("probed_at", "")),
        reason=str(entry.get("reason", "")),
    )


def _record(proof: ProbeProof) -> None:
    data = _load()
    data[proof.key] = {"ok": proof.ok, "probed_at": proof.probed_at, "reason": proof.reason}
    path = proofs_path()
    with contextlib.suppress(OSError):
        atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def installed_version(host_id: HostId, binary: str | None = None) -> str:
    """Version du binaire de l'hôte (``--version``), ``""`` si illisible ou hôte non sondable."""
    exe = binary or shutil.which(_HOSTS.get(host_id, ""))
    if not exe or host_id not in _HOSTS:
        return ""
    try:
        stat = Path(exe).stat()
        key = (exe, stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if key is not None and key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""  # un échec n'est pas mémorisé : le binaire peut revenir
    found = _VERSION.search(done.stdout or "")
    version = found.group(0) if found else ""
    if key is not None and version:
        _VERSION_CACHE[key] = version
    return version


def _read_settings(path: Path) -> tuple[dict[str, object] | None, str]:
    """``(réglages, "")`` ; ``(None, "")`` si le fichier est absent ; ``(None, motif)`` s'il est présent mais illisible."""
    if not path.is_file():
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"{path} illisible"
    return (data, "") if isinstance(data, dict) else (None, f"{path} n'est pas une table JSON")


def hooks_disabled_reason(
    project_root: Path | None = None, *, home: Path | None = None, managed_dir: Path | None = None
) -> str:
    """Pourquoi les hooks du projet ne tournent pas dans la configuration EFFECTIVE de Claude Code ; ``""`` sinon.

    La sonde teste un dossier temporaire sans les réglages de l'utilisateur : elle ne dit rien d'un
    ``disableAllHooks`` ou d'un ``allowManagedHooksOnly`` réels. Clés et précédence (doc Claude Code,
    pages settings et hooks) : ``disableAllHooks`` vaut la valeur du niveau le plus haut qui la pose
    (managé > local > projet > utilisateur ; une valeur qui n'est pas un booléen est ignorée) ;
    ``allowManagedHooksOnly`` (managé seulement) bloque les hooks utilisateur, projet et local : les hooks
    de Grimoire, posés dans ``.claude/settings.json``, ne tournent alors que si le niveau managé les porte.
    Un fichier présent mais illisible ne permet rien d'affirmer : le motif est rendu (échec fermé).
    ``--settings`` (ligne de commande) n'est pas observable d'ici.
    """
    root = project_root or Path.cwd()
    try:
        user_dir = (
            Path(os.environ["CLAUDE_CONFIG_DIR"])
            if os.environ.get("CLAUDE_CONFIG_DIR")
            else (home or Path.home()) / ".claude"
        )
    except (OSError, RuntimeError):
        return "répertoire utilisateur indéterminable"
    managed = managed_dir if managed_dir is not None else MANAGED_DIR
    ordered = [
        ("utilisateur", user_dir / "settings.json"),
        ("projet", root / ".claude" / "settings.json"),
        ("local", root / ".claude" / "settings.local.json"),
        ("managé", managed / "managed-settings.json"),
        *(("managé", f) for f in (sorted((managed / "managed-settings.d").glob("*.json")) if managed.is_dir() else [])),
    ]
    disabled = ""
    managed_only = False
    managed_has_grimoire = False
    for level, path in ordered:
        data, unreadable = _read_settings(path)
        if unreadable:
            return unreadable
        if data is None:
            continue
        flag = data.get("disableAllHooks")
        if isinstance(flag, bool):
            disabled = f"disableAllHooks=true ({level}, {path})" if flag else ""
        if level == "managé":
            if isinstance(data.get("allowManagedHooksOnly"), bool):
                managed_only = data["allowManagedHooksOnly"] is True
            managed_has_grimoire = managed_has_grimoire or "grimoire" in json.dumps(data.get("hooks", {})).lower()
    if disabled:
        return disabled
    if managed_only and not managed_has_grimoire:
        return "allowManagedHooksOnly=true (managé) sans hook Grimoire : les hooks du projet sont bloqués"
    return ""


def _leg(binary: str, *, deny: bool, timeout: int) -> tuple[bool, bool]:
    """Une passe : ``(hook appelé, outil exécuté)``."""
    with tempfile.TemporaryDirectory(prefix="grimoire-host-probe-") as tmp:
        root = Path(tmp)
        log = root / "hook.log"
        body = f"cat >> '{log}'; " + ("echo 'GRIMOIRE-PROBE-DENY' >&2; exit 2" if deny else "exit 0")
        settings = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": f'sh -c "{body}"'}]}]}}
        (root / ".claude").mkdir()
        (root / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        command = [
            binary, "-p", f"Run exactly: touch {_MARKER}. Do not retry if refused.",
            "--model", "haiku", "--setting-sources", "project", "--max-turns", "4",
            "--output-format", "json", "--allowedTools", "Bash(touch:*)",
        ]  # fmt: skip
        subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=timeout, check=False)
        return log.is_file() and log.stat().st_size > 0, (root / _MARKER).exists()


def probe_host(
    host_id: HostId,
    version: str,
    *,
    binary: str | None = None,
    timeout: int = PROBE_TIMEOUT,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProbeProof:
    """Sonde le frein de *host_id* à *version*, consigne et rend la preuve (jamais d'exception).

    La sonde ne prouve que la version du binaire réellement lancé : si ``--version``
    diffère de *version*, rien n'est lancé ni consigné pour *version*.
    """
    reason = ""
    exe = binary or shutil.which(_HOSTS.get(host_id, "")) or ""
    ok = False
    if host_id not in _HOSTS:
        reason = "hôte non sondable"
    elif not exe:
        reason = "binaire introuvable"
    elif installed_version(host_id, exe) != version:
        return ProbeProof(host_id.value, version, False, "", "le binaire installé n'est pas cette version")
    else:
        try:
            control_hook, control_ran = _leg(exe, deny=False, timeout=timeout)
            deny_hook, deny_ran = _leg(exe, deny=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            reason = f"sonde interrompue : {type(exc).__name__}"
        else:
            if not (control_hook and control_ran):
                reason = "témoin muet : l'outil ne s'est pas exécuté sans refus"
            elif not deny_hook:
                reason = "le hook de refus n'a pas été appelé"
            elif deny_ran:
                reason = "l'outil s'est exécuté malgré le refus"
            else:
                ok = True
    proof = ProbeProof(host_id.value, version, ok, now().isoformat(timespec="seconds"), reason)
    _record(proof)
    return proof


def ensure_probed(host_id: HostId, version: str | None, *, binary: str | None = None) -> ProbeProof | None:
    """Sonde la version si aucune preuve (en dur ou consignée) ne la couvre ; ``None`` si rien à faire.

    Une fois par version : une preuve consignée, réussie ou non, n'est pas rejouée
    avant :data:`RETRY_AFTER`. Opt-in : sans ``GRIMOIRE_HOST_PROBE=1`` rien n'est lancé
    (la sonde coûte deux appels payants) et la version non prouvée garde le comportement sûr,
    une reprise en ``waiting(human)``.
    """
    from grimoire.hosts.capabilities import recorded_contract

    wanted = (version or "").strip()
    if not wanted or host_id not in _HOSTS or os.environ.get(PROBE_ENV, "").strip() != "1":
        return None
    if recorded_contract(host_id).host_version == wanted:
        return None
    known = proof_for(host_id, wanted)
    if known is not None:
        if known.ok:
            return None
        with contextlib.suppress(ValueError):
            if datetime.now(UTC) - datetime.fromisoformat(known.probed_at) < RETRY_AFTER:
                return None
    try:
        return probe_host(host_id, wanted, binary=binary)
    except Exception:
        return None  # fail-closed : pas de frein, jamais d'erreur pour l'utilisateur
