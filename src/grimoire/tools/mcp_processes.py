"""Les processus ``grimoire-mcp`` vivants sur ce poste (issue #723).

Chaque session d'hôte lance son serveur MCP en stdio ; au-delà de quelques
sessions, la somme des RSS se voit (11 processus pour ≈ 540 Mo mesurés le
2026-10-09). ``grimoire doctor`` et ``grimoire mcp status`` comptent ces
processus pour proposer le serveur partagé — et disent « non mesuré » quand
``/proc`` n'existe pas (macOS, Windows), jamais un chiffre deviné. Aucune
dépendance (pas de ``psutil``) : ``/proc/<pid>/cmdline`` et ``status``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

PROC_ROOT = Path("/proc")
# Au-delà, doctor avertit : trois sessions, c'est un poste de travail ;
# davantage, c'est le kanban ou plusieurs IDE, et un démon fait mieux.
STDIO_WARN_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class McpProcess:
    pid: int
    rss_kb: int
    argv: tuple[str, ...]
    shared: bool  # ``--http`` : le démon partagé, pas un serveur de session


def _exe_name(argv0: str) -> str:
    name = Path(argv0).name.lower()
    return name.removesuffix(".exe")


def classify(argv: Sequence[str]) -> bool | None:
    """``True``/``False`` pour un serveur MCP du kit (partagé ou non), ``None`` sinon."""
    if not argv:
        return None
    exe = _exe_name(argv[0])
    rest = list(argv[1:])
    python = exe.startswith("python")
    is_server = (
        exe == "grimoire-mcp"
        or (python and rest[:2] == ["-m", "grimoire.mcp.server"])
        or (python and rest[:4] == ["-m", "grimoire", "mcp", "serve"])
        or (exe == "grimoire" and rest[:2] == ["mcp", "serve"])
    )
    if not is_server:
        return None
    return "--http" in rest


def _rss_kb(status_text: str) -> int:
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return 0


def scan(proc_root: Path = PROC_ROOT, *, self_pid: int | None = None) -> list[McpProcess] | None:
    """Les serveurs MCP du kit vivants, ou ``None`` quand rien ne peut être mesuré."""
    if not proc_root.is_dir():
        return None
    me = os.getpid() if self_pid is None else self_pid
    found: list[McpProcess] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            argv = tuple(a.decode("utf-8", "replace") for a in (entry / "cmdline").read_bytes().split(b"\0") if a)
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # le processus a disparu entre deux lectures, ou n'est pas à nous
        shared = classify(argv)
        if shared is None:
            continue
        found.append(McpProcess(pid=int(entry.name), rss_kb=_rss_kb(status), argv=argv, shared=shared))
    return sorted(found, key=lambda p: p.pid)


def stdio_servers(processes: Sequence[McpProcess]) -> list[McpProcess]:
    return [p for p in processes if not p.shared]


def total_rss_kb(processes: Sequence[McpProcess]) -> int:
    return sum(p.rss_kb for p in processes)
