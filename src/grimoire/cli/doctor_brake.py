"""Le frein de la version d'hôte courante dans `grimoire doctor` (P86).

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le laisse que rétrécir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console


def apply_host_brake_check(
    results: list[dict[str, Any]], *, fmt: str, console: Console, project_root: Path | None = None,
) -> None:
    """Le frein de l'hôte (P86), puis — si le projet est donné — la commande de notification (I1)."""
    _brake(results, fmt=fmt, console=console)
    if project_root is not None:
        from grimoire.cli.doctor_notify import apply_notify_check

        apply_notify_check(results, project_root, fmt=fmt, console=console)


def _brake(results: list[dict[str, Any]], *, fmt: str, console: Console) -> None:
    """Dit si le frein de la version d'hôte courante est prouvé, et sinon comment l'obtenir.

    Jamais FAIL : un frein non prouvé est une question posée à l'humain, pas une panne. Rien n'est dit
    quand l'hôte est introuvable.
    """
    from grimoire.bridges.schemas import HostId
    from grimoire.hosts.contract_probe import brake_state

    version, status, detail = brake_state(HostId.CLAUDE_CODE_CLI)
    if status == "absent":
        return
    results.append({
        "name": "host_brake", "passed": True, "detail": detail, "level": "ok" if status == "proved" else "warn",
        "host_version": version, "status": status,
    })
    if fmt != "json":
        console.print(f"  {'[green]OK[/green]' if status == 'proved' else '[yellow]WARN[/yellow]'}  {detail}")
