"""CLI ``grimoire ceremony`` — route de cérémonie et budget (W6-01).

``suggest`` calcule, sans LLM, la route d'une tâche (une parmi cinq) et la
profondeur de test qu'elle exige ; deux appels sur les mêmes entrées rendent
les mêmes octets. ``budget`` vérifie un fichier de budget de cérémonie (celui
du kit : ``evals/ceremony-budget.yaml``) et sort en 1 au premier écart.

Le choix du modèle n'est pas ici : la route dit quelle cérémonie (revues,
checkpoint, session), pas quel LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

ceremony_app = typer.Typer(
    help="Route de cérémonie déterministe (suggest) et budget de cérémonie (budget).",
    no_args_is_help=True,
)

_DEFAULT_LEDGER = Path("_grimoire-runtime-output/ledger")


def _as_json(ctx: typer.Context, flag: bool) -> bool:
    return flag or str((ctx.obj or {}).get("output", "text") or "text") == "json"


def _test_runner(root: Path) -> str | None:
    from grimoire.core.execution_needs import resolve_need

    return resolve_need("test-runner", root).command


def _from_task(root: Path, ledger_root: Path, task_id: str) -> dict[str, Any]:
    from grimoire.missions.ledger import MissionLedger

    path = ledger_root if ledger_root.is_absolute() else root / ledger_root
    task = MissionLedger(path).get_task(task_id)
    if task is None:
        typer.echo(f"Tâche inconnue : {task_id}", err=True)
        raise typer.Exit(2)
    return {
        "title": task.title,
        "description": task.description,
        "acceptance": task.acceptance,
        "expected_evidence": task.expected_evidence,
        "has_checks": bool(task.checks),
        "declared_risk": task.risk_profile,
    }


def _render_text(data: dict[str, Any]) -> None:
    typer.echo(f"route       : {data['route']}  (tours humains : {data['human_turns']})")
    typer.echo(f"test_depth  : {data['test_depth']} — {data['test_depth_meaning']}")
    typer.echo(f"étapes      : {', '.join(data['steps'])}")
    for fired in data["fired"]:
        typer.echo(f"  règle {fired['rule']} -> au moins {fired['floor']} ({fired['detail']})")
    if not data["fired"]:
        typer.echo("  aucune règle ne monte la route")
    for index in data["indices"]:
        state = "calibré" if index["calibrated"] else "non calibré"
        typer.echo(f"  seuil {index['name']} = {index['value']} ({state}, au {index['as_of']})")
    for limit in data["limits"]:
        typer.echo(f"  limite : {limit}")
    typer.echo(f"empreinte   : {data['inputs_digest']}")


@ceremony_app.command("suggest")
def ceremony_suggest(
    ctx: typer.Context,
    task_id: Annotated[str | None, typer.Argument(help="Tâche du ledger (sinon --title/--acceptance).")] = None,
    title: Annotated[str, typer.Option("--title", help="Titre de la tâche (sans TASK_ID).")] = "",
    acceptance: Annotated[list[str] | None, typer.Option("--acceptance", "-a", help="Critère (répétable).")] = None,
    check: Annotated[list[str] | None, typer.Option("--check", help="Commande déclarée (répétable).")] = None,
    risk: Annotated[str | None, typer.Option("--risk", help="Risque déclaré (ne peut que monter).")] = None,
    file: Annotated[list[str] | None, typer.Option("--file", "-f", help="Fichier touché (répétable).")] = None,
    diff_base: Annotated[
        str | None, typer.Option("--diff-base", help="Fichiers touchés = git diff --name-only REF.")
    ] = None,
    impact: Annotated[
        int | None, typer.Option("--impact", help="Importeurs directs des fichiers touchés, mesurés ailleurs.", min=0)
    ] = None,
    measure_impact: Annotated[
        bool, typer.Option("--measure-impact", help="Compter les importeurs directs des .py touchés (ast).")
    ] = False,
    project_root: Annotated[Path, typer.Option("--project-root", help="Racine du projet.")] = Path(),
    ledger_root: Annotated[Path, typer.Option("--ledger-root", help="Racine du Mission Ledger.")] = _DEFAULT_LEDGER,
    json_output: Annotated[bool, typer.Option("--json", help="Sortie JSON canonique.")] = False,
) -> None:
    """Calcule la route de cérémonie et la profondeur de test, sans LLM.

    Sans --file ni --diff-base, les fichiers sont inconnus : la route ne
    descend pas sous `checked`. Sort en 2 si l'entrée est inutilisable.
    """
    from grimoire.missions.ceremony import changed_files, import_fan_in, inputs_for, render_json, suggest
    from grimoire.missions.schemas import RiskProfile

    root = project_root.resolve()
    if task_id:
        spec = _from_task(root, ledger_root, task_id)
    elif title.strip():
        spec = {"title": title, "description": "", "acceptance": tuple(acceptance or ()), "expected_evidence": (),
                "has_checks": any(c.strip() for c in check or ()), "declared_risk": None}
    else:
        typer.echo("Donner TASK_ID ou --title.", err=True)
        raise typer.Exit(2)
    if risk:
        try:
            declared = RiskProfile(risk)
        except ValueError:
            typer.echo(f"--risk inconnu : {risk!r} (attendu : {', '.join(r.value for r in RiskProfile)})", err=True)
            raise typer.Exit(2) from None
        current = spec["declared_risk"]
        spec["declared_risk"] = declared if current is None else max(
            (current, declared), key=list(RiskProfile).index,
        )
    files: list[str] | None = None
    if file or diff_base:
        files = list(file or ())
        if diff_base:
            try:
                files.extend(changed_files(root, diff_base))
            except RuntimeError as exc:
                typer.echo(f"--diff-base {diff_base} : {exc}", err=True)
                raise typer.Exit(2) from exc
    impact_files = impact
    if impact_files is None and measure_impact and files:
        impact_files = import_fan_in(root, files)
    suggestion = suggest(inputs_for(
        files=files, test_runner=_test_runner(root), impact_files=impact_files, **spec,
    ))
    if _as_json(ctx, json_output):
        typer.echo(render_json(suggestion), nl=False)
        return
    _render_text(suggestion.to_dict())


@ceremony_app.command("budget")
def ceremony_budget(
    ctx: typer.Context,
    file: Annotated[Path, typer.Option("--file", help="Fichier de budget.")] = Path("evals/ceremony-budget.yaml"),
    json_output: Annotated[bool, typer.Option("--json", help="Sortie JSON.")] = False,
) -> None:
    """Vérifie un budget de cérémonie contre les routes du kit ; sort en 1 au premier écart."""
    import json

    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    from grimoire.__version__ import __version__
    from grimoire.missions.ceremony_budget import check_budget

    try:
        doc = YAML(typ="safe").load(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        typer.echo(f"{file} illisible : {exc}", err=True)
        raise typer.Exit(2) from exc
    violations = check_budget(doc, kit_version=__version__)
    if _as_json(ctx, json_output):
        typer.echo(json.dumps({"file": str(file), "kit_version": __version__, "violations": violations},
                              indent=2, ensure_ascii=False))
    elif violations:
        for violation in violations:
            typer.echo(f"✗ {violation}", err=True)
    else:
        typer.echo(f"budget de cérémonie tenu ({file}, kit {__version__})")
    if violations:
        raise typer.Exit(1)
