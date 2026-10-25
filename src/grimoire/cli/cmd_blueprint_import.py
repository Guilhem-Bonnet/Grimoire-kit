"""``grimoire blueprint import`` (R04) : un flow externe devient un blueprint expérimental.

Branche les trois adaptateurs de ``grimoire.runtime`` (CrewAI, LangGraph, Gas City) sur
un chemin atteignable. L'adaptateur lit la définition externe et en tire une
:class:`~grimoire.runtime.recipes.Recipe` ; cette commande la projette en blueprint v1
(un nœud par étape, chaînés) que ``blueprint validate`` sait relire. Rien n'est exécuté,
et un import dont l'adaptateur refuse la définition (``ok`` faux) n'écrit rien.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any

import typer

SOURCES = ("crewai", "langgraph", "gascity")
_REF_PAR_DEFAUT = "ORC-01"
_CONTRAT = "task-envelope"


def register_import_commands(app: typer.Typer) -> None:
    app.command("import")(blueprint_import)


def _adaptateur(source: str) -> Any:
    if source == "crewai":
        from grimoire.runtime.crewai_adapter import CrewAIAdapter

        return CrewAIAdapter()
    if source == "langgraph":
        from grimoire.runtime.langgraph_adapter import LangGraphAdapter

        return LangGraphAdapter()
    from grimoire.runtime.gascity_converter import GasCityConverter

    return GasCityConverter()


def recipe_to_blueprint(recipe: Any, source: str) -> dict[str, Any]:
    """Projette une recette en blueprint v1 : un nœud par étape, chaînées dans l'ordre."""
    from grimoire.runtime.adapter_base import slugify
    from grimoire.tools.ext_manager import ID_RE

    bp_id = slugify(recipe.name) or slugify(recipe.id) or "flow-importe"
    if not ID_RE.match(bp_id):
        bp_id = "flow-importe"
    noeuds: list[dict[str, Any]] = []
    pris: set[str] = set()
    for i, etape in enumerate(recipe.steps):
        base = slugify(etape.id) or f"etape-{i + 1}"
        nid, n = base, 1
        while nid in pris:  # "A b" et "a-b" se slugifient pareil : -2, -3…
            n += 1
            nid = f"{base}-{n}"
        pris.add(nid)
        pins = []
        if i > 0:
            pins.append({"id": "in", "direction": "in", "contract": _CONTRAT})
        if i < len(recipe.steps) - 1:
            pins.append({"id": "out", "direction": "out", "contract": _CONTRAT})
        noeuds.append({
            "id": nid,
            "kind": "pattern",
            "ref": _REF_PAR_DEFAUT,
            "label": etape.name or etape.id,
            "description": etape.description,
            "pins": pins,
        })
    arcs = [
        {"from": f"{a['id']}.out", "to": f"{b['id']}.in", "contract": _CONTRAT}
        for a, b in pairwise(noeuds)
    ]
    return {
        "blueprintVersion": 1,
        "id": bp_id,
        "name": recipe.name,
        "description": recipe.description,
        "status": "experimental",
        "catalogRef": {"version": "1.0.0"},
        "nodes": noeuds,
        "edges": arcs,
        "extensions": [],
        "meta": {"importedFrom": source, "recipeId": recipe.id, "tags": list(recipe.tags)},
    }


def _refuser_si_invalide(blueprint: dict[str, Any]) -> None:
    """Passe le blueprint au validateur avant toute écriture : un fichier qu'on dit créé doit être relisible."""
    import tempfile

    from grimoire.cli.cmd_blueprint import _validate_report

    with tempfile.TemporaryDirectory() as tmp:
        brouillon = Path(tmp) / f"{blueprint['id']}.blueprint.json"
        brouillon.write_text(json.dumps(blueprint, ensure_ascii=False), encoding="utf-8")
        erreurs, _ = _validate_report(brouillon, None)
    if erreurs:
        typer.secho(f"Import refusé : le blueprint produit est invalide ({erreurs} erreur(s)) ; rien n'a été écrit.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def blueprint_import(
    fichier: Annotated[Path, typer.Argument(help="Définition externe (JSON) : flow CrewAI, graphe LangGraph ou formule Gas City.")],
    source: Annotated[str, typer.Option("--from", help="Origine : crewai, langgraph ou gascity.")],
    out: Annotated[Path | None, typer.Option("--out", help="Fichier de sortie (défaut : <id>.blueprint.json).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Écraser le fichier de sortie s'il existe.")] = False,
) -> None:
    """Importe un flow externe en blueprint `status=experimental`, relisible par `blueprint validate`."""
    if source not in SOURCES:
        typer.secho(f"Error: --from {source!r} inconnu (attendu : {', '.join(SOURCES)})", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    try:
        brut = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.secho(f"Error: {fichier} illisible : {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    if not isinstance(brut, dict):
        typer.secho(f"Error: {fichier} doit contenir un objet JSON", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    recipe, rapport = _adaptateur(source).to_recipe(brut)
    if not rapport.ok:
        typer.secho(f"Import refusé par l'adaptateur {source} :", fg=typer.colors.RED, err=True)
        for erreur in rapport.errors:
            typer.secho(f"  - {erreur}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if not recipe.steps:
        typer.secho("Error: la définition ne contient aucune étape à importer", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    blueprint = recipe_to_blueprint(recipe, source)
    _refuser_si_invalide(blueprint)
    cible = out or Path(f"{blueprint['id']}.blueprint.json")
    if cible.exists() and not force:
        typer.secho(f"Error: {cible} existe déjà (--force pour écraser)", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typer.secho(f"Créé {cible} (status: experimental, {len(blueprint['nodes'])} nœud(s), depuis {source})", fg=typer.colors.GREEN)
    typer.echo(f"  Suite : grimoire blueprint validate {cible}")
