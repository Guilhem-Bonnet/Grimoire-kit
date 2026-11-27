"""Les clés d'API non liées à un point d'accès dans `grimoire doctor`.

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le laisse que rétrécir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console


def apply_key_binding_check(results: list[dict[str, Any]], project_root: Path, *, fmt: str, console: Console) -> None:
    """Signale les clés saisies avant leur liaison à un point d'accès. Rien n'est dit sans clé posée.

    WARN, jamais FAIL : une telle clé reste utilisable vers l'adresse officielle de son fournisseur ; vers toute
    autre adresse (passerelle, ``base_url`` personnalisé) elle est refusée jusqu'à une nouvelle saisie. Ne lit que
    l'index du magasin, jamais une valeur de clé.
    """
    from grimoire.providers.secrets import list_secrets, unbound_secrets

    if not list_secrets():
        return
    loose = sorted({f"{entry.provider}/{entry.env_var}" for entry in unbound_secrets()})
    detail = (
        f"clé(s) non liée(s) à un point d'accès : {', '.join(loose)} — valable(s) seulement vers l'adresse officielle ; "
        "`grimoire providers set-key <fournisseur> <VARIABLE>` les lie"
        if loose
        else "clés d'API liées à leur point d'accès"
    )
    results.append({"name": "provider_keys_bound", "passed": not loose, "detail": detail, "level": "warn" if loose else "ok"})
    if fmt != "json":
        console.print(f"  {'[yellow]WARN[/yellow]' if loose else '[green]OK[/green]'}  {detail}")
