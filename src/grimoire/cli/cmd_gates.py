"""CLI ``grimoire gates`` — les gardes du dépôt du kit, en une commande.

``grimoire gates repo`` lance exactement ce que la CI lance (mêmes scripts,
mêmes arguments) et ce que le pre-commit appelle : un contributeur voit en
local la garde rouge qu'il aurait vue en CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from grimoire.repo_gates import (
    GATE_IDS,
    PROFILES,
    all_passed,
    find_kit_root,
    foreign_package_note,
    run_repo_gates,
)

gates_app = typer.Typer(help="Gardes du dépôt du kit (clone source uniquement).", no_args_is_help=True)

_ROOT_OPTION = typer.Option("--root", help="Racine du clone du kit (défaut : remonte depuis le dossier courant).")
_PROFILE_OPTION = typer.Option("--profile", help=f"dev (branche) ou release (tag) — {', '.join(PROFILES)}.")
_ONLY_OPTION = typer.Option("--only", help=f"Ne lancer que cette garde (répétable) : {', '.join(GATE_IDS)}.")
_JSON_OPTION = typer.Option("--json", help="Sortie JSON.")

_MARK = {"passed": "ok  ", "failed": "FAIL", "unevaluated": "????"}


@gates_app.command("repo")
def gates_repo(
    ctx: typer.Context,
    root: Annotated[Path | None, _ROOT_OPTION] = None,
    profile: Annotated[str, _PROFILE_OPTION] = "dev",
    only: Annotated[list[str] | None, _ONLY_OPTION] = None,
    json_output: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Exécute toutes les gardes du dépôt ; non zéro si l'une échoue ou n'a pas pu être évaluée.

    Gardes : cliquet de taille, cohérence du CHANGELOG, prose émise, waivers
    de dépendances, actions épinglées par SHA, tenets de CONTRIBUTING,
    audience et commandes citées de la documentation, chiffres de vitrine
    sourcés. Une garde « ???? » (script absent, délai dépassé) compte comme
    un échec : non vérifié n'est pas vérifié.

    Hors d'un clone du kit, sort en code 2 : il n'y a rien à garder, et
    répondre « OK » serait mentir.
    """
    kit_root = root.resolve() if root else find_kit_root(Path.cwd())
    if kit_root is None or find_kit_root(kit_root) != kit_root:
        typer.echo("Pas un clone du kit (scripts/check-code-ratchet.py, version.txt, src/grimoire/) : rien à garder.", err=True)
        raise typer.Exit(2)
    try:
        results = run_repo_gates(kit_root, profile=profile, only=only)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    ok = all_passed(results)
    note = foreign_package_note(kit_root)
    as_json = json_output or str((ctx.obj or {}).get("output", "text") or "text") == "json"
    if as_json:
        typer.echo(
            json.dumps(
                {"root": str(kit_root), "profile": profile, "ok": ok, "warnings": [note] if note else [], "results": [r.to_dict() for r in results]},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        if note:
            typer.echo(f"Attention : {note}", err=True)
        for result in results:
            typer.echo(f"{_MARK[result.status]}  {result.id:<15} {result.seconds:5.1f}s")
            if result.status != "passed" and result.detail:
                for line in result.detail.splitlines():
                    typer.echo(f"      {line}")
        failed = [r.id for r in results if r.status == "failed"]
        unevaluated = [r.id for r in results if r.status == "unevaluated"]
        if ok:
            typer.echo(f"\n{len(results)} garde(s) passée(s) (profil {profile}).")
        else:
            typer.echo(
                f"\nÉchec — en défaut : {', '.join(failed) or '—'} ; non évaluées : {', '.join(unevaluated) or '—'}.",
                err=True,
            )
    if not ok:
        raise typer.Exit(1)
