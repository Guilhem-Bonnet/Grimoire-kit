"""``grimoire steward map|verdicts|report`` : l'intendant du projet, en lecture seule (#876, lot 5).

La logique vit dans :mod:`grimoire.steward` ; ici, les options, la sortie texte/JSON et l'écriture
des seuls fichiers de ``_grimoire-output/steward/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from grimoire.missions.launcher import Exec, default_exec
from grimoire.steward.sources import Sources

steward_app = typer.Typer(
    help="L'intendant : état des lieux en lecture seule — carte, verdicts prouvés par du code, rapport.",
    no_args_is_help=True,
)

#: L'exécuteur des processus externes (git, gh) — remplacé par un double dans les tests.
EXEC: Exec = default_exec

RootOpt = Annotated[Path, typer.Option("--project-root", help="Racine du projet.")]
TargetOpt = Annotated[str, typer.Option("--cible", help="Référence cible des fusions et des preuves.")]
RepoOpt = Annotated[str, typer.Option("--repo", help="Dépôt GitHub OWNER/REPO (défaut : déduit par gh).")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Sortie JSON.")]
HoursOpt = Annotated[int, typer.Option("--inactivite-heures", min=1, help="Seuil d'inactivité d'une PR, en heures.")]
ProbeOpt = Annotated[bool, typer.Option("--sonde/--sans-sonde", help="Interroger l'hôte (claude agents) sur la vivacité des sessions.")]
LocalOpt = Annotated[bool, typer.Option("--locales-seulement", help="Ne pas analyser les branches distantes.")]


def _json(ctx: typer.Context, flag: bool) -> bool:
    return flag or str((ctx.obj or {}).get("output", "text")) == "json"


def _sources(root: Path, target: str, repo: str, probe: bool) -> Sources:
    from grimoire.missions.liveness import probe_claude_agents

    return Sources(root.resolve(), exec_=EXEC, probe=probe_claude_agents if probe else None, target=target, repo=repo)


def _map(src: Sources, hours: int, local_only: bool) -> dict[str, Any]:
    from grimoire.steward.project_map import build_map

    return build_map(src, inactivity_hours=hours, include_remote=not local_only)


def _validate(src: Sources, file: Path) -> dict[str, Any]:
    from grimoire.steward.verdicts import VerdictsFileError, load_verdicts, validate

    try:
        data = load_verdicts(file)
    except VerdictsFileError as exc:
        typer.echo(f"steward verdicts: {exc}", err=True)
        raise typer.Exit(2) from exc
    return validate(src, data, source=str(file))


@steward_app.command("map")
def map_command(
    ctx: typer.Context,
    project_root: RootOpt = Path(),
    target: TargetOpt = "origin/main",
    repo: RepoOpt = "",
    hours: HoursOpt = 48,
    probe: ProbeOpt = True,
    local_only: LocalOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """La carte du projet (grimoire-steward-map/v1) : tâches, branches, worktrees, PR, issues et signaux.

    Déterministe, sans LLM ; ce qui ne se mesure pas (gh absent, hors ligne) est marqué « non mesuré ».
    Écrite aussi dans _grimoire-output/steward/map.json.
    """
    from grimoire.steward.report import write_output

    src = _sources(project_root, target, repo, probe)
    carte = _map(src, hours, local_only)
    path = write_output(src.root, "map.json", carte)
    if _json(ctx, as_json):
        typer.echo(json.dumps(carte, indent=2, ensure_ascii=False))
        return
    resume = carte["resume"]
    typer.echo(f"Carte du projet ({carte['cible']['ref']} = {str(carte['cible']['sha'])[:12]}) -> {path}")
    for section in ("taches", "branches", "worktrees", "prs", "issues"):
        part = carte[section]
        extra = "" if part.get("mesure") else f" (non mesuré : {part.get('raison', '')})"
        typer.echo(f"  {section:10} {resume[section]}{extra}")
    for signal in carte["signaux"]:
        typer.echo(f"  signal {signal['type']} {signal['ref']} : {signal['detail']}")


@steward_app.command("verdicts")
def verdicts_command(
    ctx: typer.Context,
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False, help="Fichier grimoire-steward-verdicts/v1.")],
    project_root: RootOpt = Path(),
    target: TargetOpt = "origin/main",
    repo: RepoOpt = "",
    probe: ProbeOpt = True,
    as_json: JsonOpt = False,
) -> None:
    """Revérifie chaque preuve d'un fichier de verdicts ; un verdict non prouvé est rejeté avec sa raison.

    N'écrit que _grimoire-output/steward/validation.json. Code 1 si un verdict est rejeté, 2 si le fichier est illisible.
    """
    from grimoire.steward.report import write_output

    src = _sources(project_root, target, repo, probe)
    result = _validate(src, file)
    path = write_output(src.root, "validation.json", result)
    if _json(ctx, as_json):
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for entry in result["etablis"]:
            typer.echo(f"établi  {entry['element']} [{entry['verdict']}]")
        for entry in result["rejetes"]:
            typer.echo(f"REJETÉ  {entry['element']} [{entry['verdict']}] : " + " ; ".join(entry.get("raisons") or []))
        typer.echo(f"{result['resume']['etablis']} établi(s), {result['resume']['rejetes']} rejeté(s) -> {path}")
    if result["rejetes"]:
        raise typer.Exit(1)


@steward_app.command("report")
def report_command(
    ctx: typer.Context,
    verdicts: Annotated[
        Path | None, typer.Option("--verdicts", exists=True, dir_okay=False, help="Fichier de verdicts à valider.")
    ] = None,
    project_root: RootOpt = Path(),
    target: TargetOpt = "origin/main",
    repo: RepoOpt = "",
    hours: HoursOpt = 48,
    probe: ProbeOpt = True,
    local_only: LocalOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Le rapport : carte + verdicts établis et rejetés, regroupés par action proposée. Aucune action n'est faite.

    Écrit _grimoire-output/steward/report.json et report.txt.
    """
    from grimoire.steward.report import build_report, render_text, write_output

    src = _sources(project_root, target, repo, probe)
    carte = _map(src, hours, local_only)
    validation = _validate(src, verdicts) if verdicts is not None else None
    report = build_report(carte, validation)
    write_output(src.root, "report.json", report)
    text = render_text(report)
    write_output(src.root, "report.txt", text)
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False) if _json(ctx, as_json) else text, nl=False)
    if _json(ctx, as_json):
        typer.echo("")
