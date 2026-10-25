"""``grimoire memory retract|unretract|tags|show|reads`` — corriger la mémoire sans la perdre (P23, P13).

Module voisin de :mod:`grimoire.cli.cmd_memory` (au plafond du cliquet de
lignes), câblé sur ``memory_app`` à l'import par ``cmd_memory_lexical``. Chaque
geste passe par :func:`grimoire.memory.gestures.run_gesture`, le même point
d'entrée que l'outil MCP : mêmes refus, même cloisonnement par projet, même
journal des écritures (``_grimoire-output/memory-changes.jsonl``).

* ``retract`` : le souvenir n'est plus servi, il n'est pas supprimé
  (``--include-retracted`` pour le relire) ; une raison est obligatoire.
* ``unretract`` : annule le retrait.
* ``tags`` : ajoute, retire ou remplace les étiquettes.
* ``show`` : lit un souvenir par identifiant, retiré ou non, avec son état.
* ``reads`` : ce que le journal des lectures dit des derniers jours.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from grimoire.cli import cmd_memory
from grimoire.cli.cmd_memory import _get_fmt, console, memory_app
from grimoire.core.exceptions import GrimoireMemoryError

__all__: list[str] = []


def _split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _fail(ctx: typer.Context, message: str, **extra: Any) -> typer.Exit:
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"ok": False, "error": message, **extra}, indent=2, ensure_ascii=False))
    else:
        console.print(f"[red]{message}[/red]")
    return typer.Exit(1)


def _run(ctx: typer.Context, gesture: str, entry_id: str, **kwargs: Any) -> None:
    from grimoire.memory.gestures import run_gesture

    mgr = cmd_memory._load_manager()  # résolu à l'appel : simulable sur cmd_memory
    try:
        payload = run_gesture(mgr, gesture, entry_id, **kwargs)
    except GrimoireMemoryError as exc:
        raise _fail(ctx, str(exc), id=entry_id, gesture=gesture) from None
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]OK[/green] {gesture} {entry_id} — état : {payload['state']}")


_REASON = Annotated[str, typer.Option("--reason", "-r", help="Pourquoi (obligatoire pour retirer, tracé dans le journal).")]


@memory_app.command("retract")
def memory_retract(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Identifiant du souvenir à retirer du service.")],
    reason: _REASON = "",
) -> None:
    """Retire un souvenir du service sans le supprimer.

    Il n'est plus rendu par `memory search`, `recall`, `list` ni par l'outil MCP
    (sauf --include-retracted) mais reste lisible, avec qui l'a retiré, quand et
    pourquoi. Réversible : `memory unretract`. Refusé sur un backend qui ne sait
    pas modifier un souvenir, et sur le souvenir d'un autre projet.
    """
    _run(ctx, "retract", entry_id, reason=reason)


@memory_app.command("unretract")
def memory_unretract(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Identifiant du souvenir à rétablir.")],
    reason: _REASON = "",
) -> None:
    """Annule un retrait : le souvenir est de nouveau servi."""
    _run(ctx, "unretract", entry_id, reason=reason)


@memory_app.command("tags")
def memory_tags(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Identifiant du souvenir à réétiqueter.")],
    add: Annotated[str, typer.Option("--add", help="Étiquettes à ajouter (séparées par des virgules).")] = "",
    remove: Annotated[str, typer.Option("--remove", help="Étiquettes à retirer (séparées par des virgules).")] = "",
    replace: Annotated[
        str | None,
        typer.Option("--set", help="Remplace toutes les étiquettes (exclusif de --add/--remove ; vide = aucune)."),
    ] = None,
    reason: _REASON = "",
) -> None:
    """Ajoute, retire ou remplace les étiquettes d'un souvenir (le texte ne change pas)."""
    _run(
        ctx, "update_tags", entry_id, reason=reason, add=_split(add), remove=_split(remove),
        replace=None if replace is None else _split(replace),
    )


@memory_app.command("show")
def memory_show(
    ctx: typer.Context,
    entry_id: Annotated[str, typer.Argument(help="Identifiant du souvenir.")],
) -> None:
    """Lit un souvenir par identifiant avec son état (live, superseded, retracted), retiré ou non."""
    from grimoire.memory.gestures import entry_state

    mgr = cmd_memory._load_manager()
    entry = mgr.recall(entry_id)
    if entry is None:
        raise _fail(ctx, f"Souvenir introuvable : {entry_id!r}.", id=entry_id)
    payload = {**entry.to_dict(), "state": entry_state(entry, mgr.recall)}
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return
    console.print(f"[bold]{entry.id}[/bold] — état : {payload['state']}")
    console.print(f"  tags : {', '.join(entry.tags) or '—'}")
    mark = (entry.metadata or {}).get("retracted")
    if mark:
        console.print(f"  retiré : {mark}")
    console.print(f"  {entry.text}")


@memory_app.command("reads")
def memory_reads(
    ctx: typer.Context,
    days: Annotated[int, typer.Option("--days", min=1, help="Fenêtre en jours.")] = 7,
    top: Annotated[int, typer.Option("--top", min=1, help="Souvenirs les plus servis à afficher.")] = 10,
) -> None:
    """Ce que le journal des lectures dit : combien de fois chaque souvenir a été servi (recall_count_7d).

    Source : `_grimoire-output/memory-reads.jsonl` (identifiants, requête hachée,
    jamais de contenu). Aucun journal, aucun chiffre : un compte vide est dit tel.
    """
    from grimoire.memory.reads_log import read_counts

    _, root = cmd_memory._load_config_context()
    counts = read_counts(root, days=days)
    ranked = sorted(counts.per_memory.items(), key=lambda kv: (-kv[1], kv[0]))
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(counts.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(f"{counts.reads} lecture(s) sur {days} jour(s), dont {counts.reads_with_results} avec résultat.")
    for entry_id, n in ranked[:top]:
        console.print(f"  {n:>4}  {entry_id}")
