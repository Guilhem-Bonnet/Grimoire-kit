"""Sonde de l'index natif de chromadb avant tout usage produit (#1059).

Sur certaines machines, la roue native de ``hnswlib`` embarquée par chromadb
utilise des instructions que le processeur ne fournit pas. L'import de
``chromadb.segment.impl.vector.local_hnsw`` meurt alors d'une instruction
illégale (SIGILL) — pas une exception Python : le processus entier disparaît.
Constaté en CI le 2026-10-28 sur Python 3.13 / ubuntu-latest.

Le backend ``mempalace`` ouvre ``chromadb.PersistentClient``, qui charge cet
index. Sur une telle machine, la CLI, le MCP et le cockpit mourraient sans
message au premier accès à la mémoire. La sonde ci-dessous essaie l'import
dans un sous-processus : seul lui peut mourir. Son verdict est mis en cache
sous l'état utilisateur, clé = version de chromadb + empreinte machine, pour
ne pas payer un processus à chaque commande.
"""

from __future__ import annotations

import hashlib
import json
import platform
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.core.fsio import atomic_write

#: L'import qui charge effectivement la roue native. ``import chromadb`` seul
#: ne suffit pas : le module fautif est chargé paresseusement par le client.
_PROBE_SNIPPET = "import chromadb.segment.impl.vector.local_hnsw"

_CACHE_FILENAME = "chromadb-native-probe.json"
_CACHE_SCHEMA = 1

#: Un premier import de chromadb peut être lent (disque froid, wheel volumineuse) ;
#: au-delà, la sonde rend sans verdict : ni refus, ni cache.
DEFAULT_PROBE_TIMEOUT_SECONDS = 60.0

#: ``subprocess.run`` réduit à ce que la sonde utilise, pour que les tests
#: simulent un signal sans jamais lancer de vrai sous-processus.
Runner = Callable[..., subprocess.CompletedProcess[str]]


class ChromaNativeIndexUnavailableError(GrimoireMemoryError):
    """L'index natif de chromadb ne peut pas se charger : le backend refuse.

    Refuser vaut mieux que tuer le processus (CLI, MCP, cockpit) sans message
    Python. Le message porte le repli : backend ``local`` ou ``lexical``.
    """


@dataclass(frozen=True, slots=True)
class NativeIndexVerdict:
    """Verdict de la sonde : l'import natif tue-t-il le processus ?"""

    ok: bool
    detail: str
    chromadb_version: str = ""
    machine_key: str = ""
    returncode: int = 0
    signal_name: str = ""
    from_cache: bool = False
    probed_at: str = ""


def installed_chromadb_version() -> str:
    """Version installée de chromadb, ou ``""`` s'il n'est pas installé."""
    try:
        return package_version("chromadb")
    except PackageNotFoundError:
        return ""


def user_state_dir() -> Path:
    """Racine de l'état utilisateur, hors projet."""
    return Path.home() / ".grimoire" / "cache"


def native_probe_cache_path() -> Path:
    """Fichier de cache du verdict : un par état utilisateur."""
    return user_state_dir() / _CACHE_FILENAME


def _machine_fingerprint() -> dict[str, str]:
    """Ce qui fait qu'un même binaire natif marche ou non sur cette machine.

    Le nom du processeur ne suffit pas : deux CPU de même référence peuvent
    exposer des jeux d'instructions différents (virtualisation, microcode). On
    ajoute donc les drapeaux CPU quand le noyau les publie, et la version
    majeure/mineure de Python (la roue native est choisie par ABI).
    """
    cpu_flags = ""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith(("flags", "Features")):
                    _, _, raw = line.partition(":")
                    cpu_flags = raw.strip()
                    break
        except OSError:
            cpu_flags = ""
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": f"{sys.implementation.name}{sys.version_info.major}.{sys.version_info.minor}",
        "cpu_flags": cpu_flags,
    }


def _machine_key(fingerprint: dict[str, str]) -> str:
    encoded = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_cache(path: Path, *, chromadb_version: str, machine_key: str) -> NativeIndexVerdict | None:
    """Relit un verdict valide pour cette version de chromadb et cette machine."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != _CACHE_SCHEMA:
        return None
    if payload.get("chromadb_version") != chromadb_version or payload.get("machine_key") != machine_key:
        return None
    ok = payload.get("ok")
    signal_name = str(payload.get("signal", ""))
    # Seuls les refus par signal sont cachés : une erreur d'installation ou un
    # délai dépassé peut se corriger sans changer ni version ni machine.
    if not isinstance(ok, bool) or (not ok and not signal_name):
        return None
    try:
        returncode = int(payload.get("returncode", 0))
    except (TypeError, ValueError):
        returncode = 0
    return NativeIndexVerdict(
        ok=ok,
        detail=str(payload.get("detail", "")),
        chromadb_version=chromadb_version,
        machine_key=machine_key,
        returncode=returncode,
        signal_name=signal_name,
        from_cache=True,
        probed_at=str(payload.get("probed_at", "")),
    )


def _write_cache(path: Path, verdict: NativeIndexVerdict) -> None:
    """Écrit le verdict atomiquement ; un cache non inscriptible ne fait jamais échouer la sonde."""
    payload = {
        "schema": _CACHE_SCHEMA,
        "chromadb_version": verdict.chromadb_version,
        "machine_key": verdict.machine_key,
        "ok": verdict.ok,
        "returncode": verdict.returncode,
        "signal": verdict.signal_name,
        "detail": verdict.detail,
        "probed_at": verdict.probed_at or _now(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        return


def _describe_signal(number: int) -> tuple[str, str]:
    try:
        signum = signal.Signals(number)
    except ValueError:
        return f"signal {number}", "signal inconnu"
    description = signal.strsignal(number) or "sans description"
    return signum.name, description


def probe_native_index(
    *,
    cache_path: Path | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    runner: Runner | None = None,
) -> NativeIndexVerdict:
    """Sonde l'import natif de chromadb, en cache par version + machine.

    Ne lève jamais pour cause de machine : un sous-processus tué par un signal
    rend un verdict ``ok=False`` porteur du nom du signal. Une sonde sans verdict
    (délai, démarrage impossible) ne prouve pas que l'index est fatal : elle ne
    refuse pas, et rien de ce qui n'est pas un signal ou un succès n'est caché.
    """
    chromadb_version = installed_chromadb_version()
    fingerprint = _machine_fingerprint()
    machine_key = _machine_key(fingerprint)
    path = cache_path or native_probe_cache_path()

    if not chromadb_version:
        return NativeIndexVerdict(
            ok=True,
            detail="chromadb n'est pas installé : sonde sans objet",
            from_cache=False,
        )

    cached = _read_cache(path, chromadb_version=chromadb_version, machine_key=machine_key)
    if cached is not None:
        return cached

    run = runner or subprocess.run
    try:
        completed = run(
            [sys.executable, "-c", _PROBE_SNIPPET],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return NativeIndexVerdict(
            ok=True,
            detail=f"sonde sans verdict : pas de réponse en {timeout:.0f} s",
            chromadb_version=chromadb_version,
            machine_key=machine_key,
            probed_at=_now(),
        )
    except OSError as exc:
        return NativeIndexVerdict(
            ok=True,
            detail=f"sonde sans verdict : démarrage impossible ({exc})",
            chromadb_version=chromadb_version,
            machine_key=machine_key,
            probed_at=_now(),
        )

    returncode = int(completed.returncode)
    if returncode == 0:
        verdict = NativeIndexVerdict(
            ok=True,
            detail=f"index natif de chromadb {chromadb_version} chargé",
            chromadb_version=chromadb_version,
            machine_key=machine_key,
            probed_at=_now(),
        )
    elif returncode < 0:
        name, description = _describe_signal(-returncode)
        verdict = NativeIndexVerdict(
            ok=False,
            detail=f"l'import natif de chromadb meurt d'un signal {name} ({description}) sur ce processeur",
            chromadb_version=chromadb_version,
            machine_key=machine_key,
            returncode=returncode,
            signal_name=name,
            probed_at=_now(),
        )
    else:
        tail = (completed.stderr or completed.stdout or "").strip()[-300:]
        suffix = f" : {tail}" if tail else ""
        verdict = NativeIndexVerdict(
            ok=False,
            detail=f"l'import natif de chromadb a échoué (code {returncode}){suffix}",
            chromadb_version=chromadb_version,
            machine_key=machine_key,
            returncode=returncode,
            probed_at=_now(),
        )

    if verdict.ok or verdict.signal_name:
        _write_cache(path, verdict)
    return verdict


def refusal_message(verdict: NativeIndexVerdict) -> str:
    """Message Python clair : pourquoi le backend refuse, et par quoi le remplacer."""
    return (
        "Le backend mémoire « mempalace » est refusé : son index natif (hnswlib, fourni par "
        f"chromadb {verdict.chromadb_version}) ne se charge pas sur cette machine.\n"
        f"Sonde : {verdict.detail}.\n"
        "Grimoire s'arrête ici avec un message au lieu de laisser l'import tuer le processus "
        "(CLI, MCP, cockpit) sans explication.\n"
        "Replis possibles : « local » (JSON, sans dépendance native) ou « lexical » "
        "(SQLite FTS5).\n"
        "  grimoire init --backend local\n"
        "  grimoire init --backend lexical"
    )


def ensure_native_index(
    *,
    cache_path: Path | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    runner: Runner | None = None,
) -> NativeIndexVerdict:
    """Sonde puis refuse le backend ``mempalace`` si l'index natif est fatal."""
    verdict = probe_native_index(cache_path=cache_path, timeout=timeout, runner=runner)
    if not verdict.ok:
        raise ChromaNativeIndexUnavailableError(refusal_message(verdict))
    return verdict
