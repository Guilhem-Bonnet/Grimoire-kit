"""``grimoire memory rules`` — règles parsables par couche (W6-03), règles apprises (W6-04).

Enregistré sur ``memory_app`` à l'import (``cmd_memory_lexical`` l'importe pour
son effet de bord, ce qui évite de faire grossir ``cmd_memory``, ratchet R2).
La logique vit dans :mod:`grimoire.core.layered_rules` ; ce module n'est que la
surface CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from grimoire.cli.cmd_memory import _get_fmt, _load_config_context, console, memory_app
from grimoire.core.layered_rules import LAYERS, RuleRefusedError, add_rule, analyze
from grimoire.core.rule_resolution import (
    EMITTED_LAYERS,
    STATUS_EFFECTIVE,
    emitted_rules,
    normalize_path,
    resolve_rules,
    rule_identity,
)

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


@rules_app.command("explain")
def rules_explain(ctx: typer.Context, path: str = typer.Argument(..., help="Chemin relatif à la racine du projet.")) -> None:
    """Dire quelles règles valent pour un fichier, dans quel ordre, et d'où elles viennent.

    [dim]Examples:[/dim]
      [cyan]grimoire memory rules explain src/api/handler.py[/cyan]
      [cyan]grimoire -o json memory rules explain docs/guide.md[/cyan]
    """
    try:
        target = normalize_path(path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    _, root = _load_config_context()
    report = analyze(root)
    every = [r for layer in LAYERS for r in report.layers.get(layer, ())]
    # « émise » se juge sur la décision de `host sync` (résolution sans chemin), pas sur celle de ce chemin.
    delivered = {rule_identity(rr.rule) for rr in emitted_rules(resolve_rules(every))}
    resolved = resolve_rules(every, target)
    rows = [{**rr.to_dict(), "emitted": rule_identity(rr.rule) in delivered} for rr in resolved]
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"path": target, "emitted_layers": list(EMITTED_LAYERS), "rules": rows}, indent=2, ensure_ascii=False))
        return
    console.print(f"[bold]{target}[/bold] — {len(rows)} règle(s) applicable(s)")
    for row in rows:
        mark = "" if row["status"] == STATUS_EFFECTIVE else f" [yellow]({row['status']})[/yellow]"
        sent = "" if row["emitted"] else " [dim](non émise)[/dim]"
        console.print(f"  {row['rank']}. {row['layer']} : {row['rule']}{mark}{sent}")


# ── Règles apprises d'événements observés (W6-04) ────────────────────────────

_LEDGER_OPT = typer.Option(None, "--ledger-root", help="Racine du Mission Ledger (défaut : _grimoire-runtime-output/ledger).")


def _learned_failure(ctx: typer.Context, exc: Exception) -> typer.Exit:
    """Un refus lisible, en JSON comme en texte, et le code 1."""
    payload: dict[str, object] = (
        exc.to_dict() if isinstance(exc, RuleRefusedError) else {"error": str(exc), "refused": True}
    )
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        console.print(f"[red]{exc}[/red]")
        if isinstance(exc, RuleRefusedError) and exc.remedy:
            console.print(f"[dim]{exc.remedy}[/dim]")
    return typer.Exit(1)


@rules_app.command("propose")
def rules_propose(
    ctx: typer.Context,
    min_tasks: int = typer.Option(2, "--min-tasks", help="Tâches distinctes exigées par signature (2 au moins)."),
    ledger_root: Path | None = _LEDGER_OPT,
) -> None:
    """Proposer des règles depuis les refus de gate observés ; rien n'est écrit dans les couches.

    [dim]Examples:[/dim]
      [cyan]grimoire -o json memory rules propose[/cyan]
    """
    from grimoire.core.learned_rules import RuleProposalError, propose_rules
    from grimoire.missions.gates import GatesFileError

    _, root = _load_config_context()
    try:
        report = propose_rules(root, ledger_root=ledger_root, min_tasks=min_tasks)
    except (RuleProposalError, GatesFileError, ValueError) as exc:
        raise _learned_failure(ctx, exc) from None
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return
    console.print(
        f"{len(report.created)} créée(s), {len(report.updated)} mise(s) à jour, {len(report.reopened)} rouverte(s), "
        f"{len(report.unchanged)} inchangée(s), {report.below_threshold} signature(s) sous le seuil de {report.min_tasks} tâches"
    )
    for skipped in report.skipped:
        console.print(f"  [yellow]écartée[/yellow] {skipped['transition']} / {skipped['evidence']} : {skipped['reason']}")


@rules_app.command("proposals")
def rules_proposals(
    ctx: typer.Context,
    pending: bool = typer.Option(False, "--pending", help="Seulement celles qui attendent une décision."),
) -> None:
    """Lister les propositions de règle, avec leurs preuves ; aucune session de flow n'est requise."""
    from grimoire.core.learned_rules import RuleProposalError, list_rule_proposals

    _, root = _load_config_context()
    try:
        found = list_rule_proposals(root, status="pending" if pending else None)
    except RuleProposalError as exc:
        raise _learned_failure(ctx, exc) from None
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps({"proposals": found}, indent=2, ensure_ascii=False))
        return
    if not found:
        console.print("[dim]Aucune proposition. `grimoire memory rules propose` les tire des refus de gate.[/dim]")
    for proposal in found:
        console.print(
            f"[bold]{proposal['id']}[/bold] ({proposal['status']}) : {proposal['rule']} — "
            f"{proposal.get('distinct_tasks', 0)} tâche(s), {proposal.get('occurrences', 0)} refus"
        )


@rules_app.command("accept")
def rules_accept(
    ctx: typer.Context,
    proposal_id: str = typer.Argument(..., help="Identifiant de la proposition."),
    layer: str = typer.Option("project", "--layer", help=f"Couche : {', '.join(LAYERS)}."),
) -> None:
    """Accepter une proposition : la règle passe par les mêmes contrôles que `rules add`."""
    from grimoire.core.learned_rules import RuleProposalError, accept_rule_proposal

    cfg, root = _load_config_context()
    try:
        record = accept_rule_proposal(root, proposal_id, layer=layer, project_name=cfg.project.name)
    except (RuleProposalError, RuleRefusedError) as exc:
        raise _learned_failure(ctx, exc) from None
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(record, indent=2, ensure_ascii=False))
        return
    console.print(f"[green]Acceptée[/green] dans la couche [bold]{layer}[/bold] : {record['rule']}")


@rules_app.command("reject")
def rules_reject(ctx: typer.Context, proposal_id: str = typer.Argument(..., help="Identifiant de la proposition.")) -> None:
    """Refuser une proposition ; elle ne revient qu'une fois ses tâches distinctes doublées."""
    from grimoire.core.learned_rules import RuleProposalError, reject_rule_proposal

    _, root = _load_config_context()
    try:
        record = reject_rule_proposal(root, proposal_id)
    except RuleProposalError as exc:
        raise _learned_failure(ctx, exc) from None
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(record, indent=2, ensure_ascii=False))
        return
    console.print(f"[yellow]Refusée[/yellow] : {record['rule']}")


@rules_app.command("effect")
def rules_effect(
    ctx: typer.Context,
    min_tasks: int = typer.Option(5, "--min-tasks", help="Tâches exposées exigées de chaque côté de l'acceptation."),
    ledger_root: Path | None = _LEDGER_OPT,
) -> None:
    """Mesurer l'effet de chaque règle acceptée sur la signature qu'elle vise.

    [dim]Examples:[/dim]
      [cyan]grimoire -o json memory rules effect[/cyan]
    """
    from grimoire.core.learned_rules import RuleProposalError, rule_effects
    from grimoire.missions.gates import GatesFileError

    _, root = _load_config_context()
    try:
        report = rule_effects(root, ledger_root=ledger_root, min_tasks=min_tasks)
    except (RuleProposalError, GatesFileError, ValueError) as exc:
        raise _learned_failure(ctx, exc) from None
    if _get_fmt(ctx) == "json":
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
        return
    summary = report["summary"]
    console.print(
        f"{summary['effective']} effective, {summary['unproven']} unproven, {summary['harmful']} harmful "
        f"(seuil : {report['min_tasks']} tâches de chaque côté)"
    )
    for row in report["rules"]:
        retire = " [red]— retrait proposé[/red]" if row["retire_suggested"] else ""
        console.print(f"  [bold]{row['status']}[/bold] {row['rule']} : {row['reason']}{retire}")
