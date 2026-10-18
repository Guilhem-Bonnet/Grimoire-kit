"""CLI ``grimoire route`` — expliquer, mesurer et évaluer le routeur de LLM (K9, issue #774).

``explain <tâche>`` rejoue la dernière décision ``task.routed`` de la tâche à partir de ses
entrées enregistrées et dit si elle redonne exactement la même ; ``report`` compte (réussite
par modèle, escalade, accord shadow) ; ``eval --set <jeu>`` applique le seuil D16 avant toute
activation. Le routeur ne lit ni quota ni budget : aucune de ces commandes n'en accepte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from ruamel.yaml import YAML

route_app = typer.Typer(help="Routeur de LLM : explain, report, eval (mode shadow par défaut).", no_args_is_help=True)
console = Console()

_PROJECT_ROOT_OPTION = typer.Option("--project-root", help="Racine du projet.", show_default=False)
_LEDGER_ROOT_OPTION = typer.Option("--ledger-root", help="Racine du Mission Ledger (relative au projet).")
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")
_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _as_json(ctx: typer.Context, flag: bool) -> bool:
    return flag or str((ctx.obj or {}).get("output", "text") or "text") == "json"


def _ledger(root: Path, ledger_root: Path) -> Any:
    from grimoire.missions.ledger import MissionLedger

    path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    return MissionLedger(path)


@route_app.command("explain")
def route_explain(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Identifiant de la tâche.")],
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Rejoue la dernière décision de routage d'une tâche, depuis `inputs_digest`.

    Sort en 1 si la décision rejouée diffère de celle qui a été enregistrée (entrées ou
    politique altérées), en 2 si la tâche n'a aucun événement `task.routed`.
    """
    from grimoire.missions.route_dispatch import ROUTED_EVENT, replay_event

    events = [e for e in _ledger(project_root.resolve(), ledger_root).list_events(task_id) if e.event_type == ROUTED_EVENT]
    if not events:
        typer.echo(f"Aucune décision de routage pour {task_id} (`task.routed` absent).", err=True)
        raise typer.Exit(2)
    payload = events[-1].payload
    replayed, same, digest = replay_event(payload)
    out = {
        "task_id": task_id,
        "mode": payload.get("mode"),
        "outcome": payload.get("outcome"),
        "reasons": payload.get("reasons", []),
        "decision": payload.get("decision"),
        "replayable": replayed is not None,
        "replay_identical": same,
        "inputs_digest": digest or None,
    }
    if _as_json(ctx, json_output):
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold]{task_id}[/bold] — {out['outcome']} (mode {out['mode']})")
        for reason in out["reasons"]:
            console.print(f"  - {reason}")
        for excluded in (out["decision"] or {}).get("excluded", []):
            console.print(f"  [dim]écarté : {excluded.get('model')} ({excluded.get('reason')})[/dim]")
        if replayed is None:
            console.print("[dim]Pas d'entrées enregistrées : refus ou erreur, rien à rejouer.[/dim]")
        else:
            console.print(f"Rejeu : {'identique' if same else '[red]DIFFÉRENT[/red]'} (empreinte {digest[:12]})")
    if not same:
        raise typer.Exit(1)


@route_app.command("report")
def route_report(
    ctx: typer.Context,
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    ledger_root: Annotated[Path, _LEDGER_ROOT_OPTION] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Réussite et escalade par couple, accord shadow : « non mesuré » sous 5 observations."""
    from grimoire.missions.route_report import build_report

    report = build_report(_ledger(project_root.resolve(), ledger_root))
    if _as_json(ctx, json_output):
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
        return
    table = Table(title="Réussite au premier essai (pass^1)")
    for column in ("type", "classe", "modèle", "obs.", "pass^1"):
        table.add_column(column)
    for row in report["models"]:
        table.add_row(row["task_type"], row["verifiability"], row["model"], str(row["observations"]), row["pass1_label"])
    console.print(table)
    shadow = report["shadow_comparison"]
    console.print(f"Accord shadow : {shadow['agreement']} ({shadow['agree']}/{shadow['compared']} dispatchs comparés)")
    console.print(f"Décisions tracées : {report['routed']['events']} {report['routed']['by_outcome']}")


@route_app.command("eval")
def route_eval(
    ctx: typer.Context,
    set_path: Annotated[Path, typer.Option("--set", help="Jeu fixe de tâches V0/V1 avec résultats enregistrés.")],
    project_root: Annotated[Path, _PROJECT_ROOT_OPTION] = Path(),
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Seuil D16 hors ligne : pass^1 du routeur >= base - 1 tâche, coût connu <= base.

    Un coût inconnu, un résultat manquant ou un jeu de moins de 20 tâches rendent
    « non mesuré » (sortie 3) : on n'active pas. Sortie 1 si le seuil n'est pas tenu.
    """
    from grimoire.missions.route_report import NOT_MEASURED, EvalSetError, evaluate
    from grimoire.missions.router import provider_view
    from grimoire.missions.routing_policy import load_policy

    root = project_root.resolve()
    load = load_policy(root)
    if load.policy is None:
        typer.echo(f"Politique de routage invalide, aucune évaluation : {load.error}", err=True)
        raise typer.Exit(2)
    try:
        result = evaluate(YAML(typ="safe").load(set_path.read_text(encoding="utf-8")), provider_view(root, offline=True), policy=load.policy)
    except (EvalSetError, OSError) as exc:
        typer.echo(f"Jeu d'évaluation inutilisable : {exc}", err=True)
        raise typer.Exit(2) from exc
    if _as_json(ctx, json_output):
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(f"[bold]Verdict : {result['verdict']}[/bold] ({result['tasks']} tâches)")
        console.print(f"  routeur : {result['router']['pass']} réussies, coût {result['router']['cost']}")
        console.print(f"  base    : {result['baseline']['pass']} réussies, coût {result['baseline']['cost']}")
        for reason in result["reasons"]:
            console.print(f"  - {reason}")
    if result["verdict"] == NOT_MEASURED:
        raise typer.Exit(3)
    if result["verdict"] != "pass":
        raise typer.Exit(1)
