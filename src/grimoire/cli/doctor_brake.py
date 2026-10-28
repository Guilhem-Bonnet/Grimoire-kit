"""Le frein de la version d'hôte courante dans `grimoire doctor` (P86).

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le laisse que rétrécir.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console


def apply_host_brake_check(results: list[dict[str, Any]], *, fmt: str, console: Console) -> None:
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
