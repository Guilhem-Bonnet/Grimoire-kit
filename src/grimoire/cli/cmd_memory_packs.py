"""``grimoire memory rules pack`` — packs de règles par stack (W6-08).

Enregistré sur ``rules_app`` à l'import (``cmd_memory_lexical`` l'importe pour
son effet de bord). La logique vit dans :mod:`grimoire.core.stack_packs` ; ce
module n'est que la surface CLI.
"""

from __future__ import annotations

import json

import typer

from grimoire.cli.cmd_memory import _get_fmt, _load_config_context, console
from grimoire.cli.cmd_memory_rules import rules_app
from grimoire.core.layered_rules import LAYERS, analyze
from grimoire.core.stack_packs import (
    StackPack,
    StackPackError,
    evaluate_project,
    install_pack,
    load_packs,
)

__all__ = ["pack_app"]

pack_app = typer.Typer(help="Packs de règles par stack : chaque règle est vérifiée sur le code ou déclarée advisory.")
rules_app.add_typer(pack_app, name="pack")

_layer_opt = typer.Option("project", "--layer", help=f"Couche cible : {', '.join(LAYERS)}.")


def _packs_or_exit() -> tuple[StackPack, ...]:
    try:
        return load_packs()
    except StackPackError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None


def _pack_or_exit(pack_id: str) -> StackPack:
    packs = _packs_or_exit()
    for pack in packs:
        if pack.id == pack_id:
            return pack
    console.print(f"[red]Pack {pack_id!r} inconnu.[/red] Packs : {', '.join(p.id for p in packs) or 'aucun'}.")
    raise typer.Exit(2)


@pack_app.command("list")
def pack_list(ctx: typer.Context) -> None:
    """Lister les packs livrés, avec la part de règles vérifiées et advisory."""
    packs = _packs_or_exit()
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps([
            {"id": p.id, "stack": p.stack, "title": p.title, "rules": len(p.rules), "verified": p.verified, "advisory": p.advisory}
            for p in packs
        ], indent=2, ensure_ascii=False))
        return
    for pack in packs:
        console.print(f"[bold]{pack.id}[/bold] — {pack.title} : {pack.verified} vérifiée(s), {pack.advisory} advisory")


@pack_app.command("show")
def pack_show(ctx: typer.Context, pack_id: str = typer.Argument(..., help="Identifiant du pack.")) -> None:
    """Détailler un pack : chaque règle, son vérificateur ou son statut advisory."""
    pack = _pack_or_exit(pack_id)
    rows = [
        {
            "rule": pr.rule.render(),
            "advisory": pr.advisory,
            "verifier": None if pr.verifier is None else {
                "kind": pr.verifier.kind, "paths": list(pr.verifier.paths),
                "pattern": pr.verifier.pattern, "check": pr.verifier.check,
            },
            "rationale": pr.rationale,
        }
        for pr in pack.rules
    ]
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"id": pack.id, "stack": pack.stack, "title": pack.title, "rules": rows}, indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]{pack.id}[/bold] — {pack.title}")
    for row in rows:
        how = "advisory" if row["advisory"] else f"vérifiée ({row['verifier']['kind']})"  # type: ignore[index]
        console.print(f"  {row['rule']}  [dim]{how}[/dim]")


@pack_app.command("install")
def pack_install(
    ctx: typer.Context,
    pack_id: str = typer.Argument(..., help="Identifiant du pack."),
    layer: str = _layer_opt,
) -> None:
    """Ajouter les règles d'un pack à une couche ; un refus d'une règle n'écrit rien pour elle.

    [dim]Examples:[/dim]
      [cyan]grimoire memory rules pack install python[/cyan]
      [cyan]grimoire memory rules pack install python --layer team[/cyan]
    """
    pack = _pack_or_exit(pack_id)
    cfg, root = _load_config_context()
    outcome = install_pack(root, pack, layer, project_name=cfg.project.name)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "pack": pack.id, "layer": layer, "written": list(outcome.written), "present": list(outcome.present),
            "refused": [{"rule": r, "code": c, "message": m} for r, c, m in outcome.refused],
        }, indent=2, ensure_ascii=False))
    else:
        console.print(f"Pack [bold]{pack.id}[/bold] → couche {layer} : {len(outcome.written)} écrite(s), {len(outcome.present)} déjà présente(s)")
        for rule, _, message in outcome.refused:
            console.print(f"  [red]refusée[/red] {rule} — {message}")
    if outcome.refused:
        raise typer.Exit(1)


@pack_app.command("verify")
def pack_verify(ctx: typer.Context) -> None:
    """Vérifier sur le code du projet les règles de pack installées. Code 1 si l'une est violée."""
    _, root = _load_config_context()
    packs = _packs_or_exit()
    report = evaluate_project(root, analyze(root).layers, packs)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": report.failing == 0,
            "rules_enforced": report.enforced, "rules_advisory": report.advisory,
            "rules_failing": report.failing, "rules_unevaluated": report.unevaluated,
            "rules_unclassified": report.unclassified,
            "rules": [
                {"pack": s.pack, "rule": s.rule, "status": s.status, "reason": s.reason,
                 "findings": [f.render() for f in s.findings]}
                for s in report.statuses
            ],
        }, indent=2, ensure_ascii=False))
    else:
        for s in report.statuses:
            color = {"pass": "green", "fail": "red", "unevaluated": "yellow"}.get(s.status, "dim")
            console.print(f"[{color}]{s.status}[/{color}] {s.rule} [dim]({s.pack})[/dim]")
            for finding in s.findings[:5]:
                console.print(f"    {finding.render()}")
            if s.reason:
                console.print(f"    [dim]{s.reason}[/dim]")
        console.print(
            f"{report.enforced} contrôlée(s) dont {report.failing} violée(s) et {report.unevaluated} non évaluée(s) ; "
            f"{report.advisory} advisory ; {report.unclassified} sans pack."
        )
    if report.failing:
        raise typer.Exit(1)
