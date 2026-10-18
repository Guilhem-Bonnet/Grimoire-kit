"""``grimoire memory rules`` — règles parsables par couche (W6-03).

Enregistré sur ``memory_app`` à l'import (``cmd_memory_lexical`` l'importe pour
son effet de bord, ce qui évite de faire grossir ``cmd_memory``, ratchet R2).
La logique vit dans :mod:`grimoire.core.layered_rules` ; ce module n'est que la
surface CLI.
"""

from __future__ import annotations

import json

import typer

from grimoire.cli.cmd_memory import _get_fmt, _load_config_context, console, memory_app
from grimoire.core.layered_rules import LAYERS, RuleRefusedError, add_rule, analyze

__all__ = ["rules_app"]

rules_app = typer.Typer(help="Règles parsables par couche : ALWAYS|NEVER <verbe> <objet> [@portée].")
memory_app.add_typer(rules_app, name="rules")

_layer_arg = typer.Argument(..., help=f"Couche : {', '.join(LAYERS)} (la première l'emporte).")
_text_arg = typer.Argument(..., help='La règle, ex. "NEVER edit generated-files @docs/api/**".')


@rules_app.command("add")
def rules_add(ctx: typer.Context, layer: str = _layer_arg, text: str = _text_arg) -> None:
    """Ajouter une règle ; un refus (grammaire, conflit, plafond, frontière) n'écrit rien.

    [dim]Examples:[/dim]
      [cyan]grimoire memory rules add project "ALWAYS run unit-tests before commit"[/cyan]
      [cyan]grimoire memory rules add agent "NEVER edit generated-files @docs/api/**"[/cyan]
    """
    cfg, root = _load_config_context()
    fmt = _get_fmt(ctx)
    try:
        written = add_rule(root, layer, text, project_name=cfg.project.name)
    except RuleRefusedError as exc:
        if fmt == "json":
            typer.echo(json.dumps(exc.to_dict(), indent=2, ensure_ascii=False))
        else:
            console.print(f"[red]{exc}[/red]")
            if exc.remedy:
                console.print(f"[dim]{exc.remedy}[/dim]")
        raise typer.Exit(1) from None
    if fmt == "json":
        typer.echo(json.dumps({
            "written": True,
            "rule": written.rule.render(),
            "layer": layer,
            "shadowed": [r.render() for r in written.shadowed],
        }, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]Règle écrite[/green] dans la couche [bold]{layer}[/bold] : {written.rule.render()}")
    for shadowed in written.shadowed:
        console.print(f"  [yellow]met à l'ombre[/yellow] ({shadowed.layer}) : {shadowed.render()}")


@rules_app.command("list")
def rules_list(ctx: typer.Context) -> None:
    """Lister les règles par couche, avec les jetons estimés face au plafond."""
    from grimoire.core.layered_rules import layer_tokens

    _, root = _load_config_context()
    report = analyze(root)
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "max_tokens_per_layer": report.max_tokens,
            "layers": {
                name: {"tokens": layer_tokens(rules), "rules": [r.render() for r in rules]}
                for name, rules in report.layers.items()
            },
        }, indent=2, ensure_ascii=False))
        return
    for name in LAYERS:
        rules = report.layers.get(name, ())
        console.print(f"[bold]{name}[/bold] — {len(rules)} règle(s), {layer_tokens(rules)}/{report.max_tokens} jetons")
        for rule in rules:
            console.print(f"  {rule.render()}")


@rules_app.command("check")
def rules_check(ctx: typer.Context) -> None:
    """Vérifier les fichiers de règles : grammaire, conflits, plafond. Code 1 sur une erreur."""
    _, root = _load_config_context()
    problems = analyze(root).problems
    errors = [p for p in problems if p.kind != "shadowed" and p.kind != "policy_invalid"]
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({
            "ok": not errors,
            "problems": [{"kind": p.kind, "layer": p.layer, "line": p.line, "message": p.message} for p in problems],
        }, indent=2, ensure_ascii=False))
    else:
        for problem in problems:
            style = "red" if problem in errors else "yellow"
            console.print(f"[{style}]{problem.kind}[/{style}] : {problem.message}")
        if not problems:
            console.print("[green]Règles conformes[/green] — grammaire, conflits et plafond tiennent.")
    if errors:
        raise typer.Exit(1)
