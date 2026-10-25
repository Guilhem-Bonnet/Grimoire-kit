"""``grimoire plans`` : lire le registre des plans (R08).

Lecture seule. Le registre (``deprecated-plans-registry.yaml``) dit quels plans sont
actifs, absorbés, archivés ou en incubation ; la logique de lecture et de validation
vit dans :mod:`grimoire.missions.plans_registry`, ce module ne fait que la présenter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

plans_app = typer.Typer(help="Plans : le registre des plans (actifs, absorbés, archivés, en incubation).", no_args_is_help=True)

# La liste part sur stdout (redirigeable), les messages sur stderr.
console = Console(stderr=True)

DEFAULT_REGISTRY = Path("_grimoire-runtime-output/planning-artifacts/deprecated-plans-registry.yaml")


@plans_app.callback()
def _plans_group() -> None:
    """Le registre des plans, en lecture seule."""


@plans_app.command("list")
def plans_list(
    ctx: typer.Context,
    status: Annotated[str, typer.Option("--status", help="Ne montrer que ce statut : active, absorbed, archive, incubator.")] = "",
    check: Annotated[bool, typer.Option("--check", help="Valide le registre (ids uniques, statuts, chemins présents) ; code 1 s'il est incohérent.")] = False,
    registry: Annotated[Path, typer.Option("--registry", help="Fichier du registre, relatif à la racine du projet.")] = DEFAULT_REGISTRY,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.", show_default=False)] = Path(),
) -> None:
    """Liste les plans du registre : identifiant, statut, titre, et par quoi un plan absorbé l'a été.

    Lecture seule. Sans registre, la commande le dit et sort en erreur : elle
    n'en invente pas un. `--check` vérifie aussi que chaque chemin existe.
    """
    from grimoire.missions.plans_registry import _VALID_STATUSES, load_plans_registry

    path = registry if registry.is_absolute() else project_root / registry
    if status and status not in _VALID_STATUSES:
        console.print(f"[red]✗[/red] statut inconnu : {escape(status)} — parmi {', '.join(sorted(_VALID_STATUSES))}")
        raise typer.Exit(2)
    if not path.is_file():
        console.print(f"[red]✗[/red] registre des plans introuvable : {escape(str(path))} (`--registry` en désigne un autre)")
        raise typer.Exit(1)
    try:
        loaded = load_plans_registry(path)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        console.print(f"[red]✗[/red] registre illisible : {escape(str(path))} ({escape(type(exc).__name__)} : {escape(str(exc))})")
        raise typer.Exit(1) from exc
    plans = [p for p in loaded.plans if not status or p.status == status]
    validation = loaded.validate(project_root) if check else None
    if validation is not None and not validation.ok:
        for issue in validation.issues:
            console.print(f"[red]✗[/red] {escape(issue)}")
    if str((ctx.obj or {}).get("output", "text")) == "json":
        payload = {"target_plan": loaded.target_plan, "unified_backlog": loaded.unified_backlog,
                   "count": len(plans), "plans": [p.to_dict() for p in plans]}
        if validation is not None:
            payload["validation"] = {"ok": validation.ok, "issues": validation.issues, "stats": validation.stats}
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for p in plans:
            absorbed = f"\t-> {p.absorbed_by}" if p.absorbed_by else ""
            typer.echo(f"{p.id}\t{p.status}\t{p.title}{absorbed}")
        console.print(f"[dim]{len(plans)} plan(s) sur {len(loaded.plans)} ; plan cible : {escape(loaded.target_plan or '-')}[/dim]")
    if validation is not None and not validation.ok:
        raise typer.Exit(1)
