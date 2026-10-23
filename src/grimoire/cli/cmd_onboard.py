"""``grimoire onboard`` — documenter un projet existant à partir de son code, sans modèle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

console = Console()

onboard_app = typer.Typer(help="Documenter un projet existant : base de connaissance dérivée du code, vérifiable.")


@onboard_app.command("brownfield")
def brownfield(
    path: Annotated[Path, typer.Argument(help="Racine du projet à documenter.")] = Path(),
    check: Annotated[bool, typer.Option("--check", help="Ne rien écrire : dire si la base est encore vraie (code 1 si périmée ou invérifiable).")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Sortie JSON.")] = False,
    no_memory: Annotated[bool, typer.Option("--no-memory", help="Ne pas poser le renvoi dans `_grimoire/_memory/shared-context.md`.")] = False,
) -> None:
    """Écrit `_grimoire-output/knowledge/project-knowledge.{md,json}` : composants, points d'entrée,
    commandes, dépendances, conventions, zones à risque, carte des tests, questions ouvertes.

    Déterministe et sans modèle : chaque fait cite sa source (fichier:ligne) et son empreinte.
    """
    from grimoire.onboarding.brownfield import (
        build_knowledge,
        check_knowledge,
        sync_shared_context,
        write_knowledge,
    )

    root = path.resolve()
    if not root.is_dir():
        console.print(f"[red]Dossier introuvable :[/red] {path}")
        raise typer.Exit(2)
    if check:
        fresh = check_knowledge(root)
        if as_json:
            typer.echo(json.dumps(fresh.to_dict(), indent=2, ensure_ascii=False))
        elif fresh.verdict == "current":
            console.print("[green]à jour[/green] — le code n'a pas changé depuis la base.")
        elif fresh.verdict == "stale":
            changed = [*fresh.modified, *fresh.added, *fresh.removed]
            console.print(f"[yellow]périmée[/yellow] — {len(changed)} fichier(s) changé(s) : {', '.join(changed[:5])}"
                          + (" …" if len(changed) > 5 else ""))
            if fresh.stale_facts:
                console.print(f"  faits touchés ({len(fresh.stale_facts)}) : {', '.join(fresh.stale_facts[:5])}")
            console.print("  Régénérer : [cyan]grimoire onboard brownfield[/cyan]")
        else:
            console.print(f"[yellow]invérifiable[/yellow] ({fresh.reason}) — "
                          "générer la base : [cyan]grimoire onboard brownfield[/cyan]")
        raise typer.Exit(0 if fresh.verdict == "current" else 1)

    kb = build_knowledge(root)
    json_path, md_path = write_knowledge(root, kb)
    memory = None if no_memory else sync_shared_context(root, kb)
    summary = kb["summary"]
    if as_json:
        typer.echo(json.dumps({
            "markdown": md_path.relative_to(root).as_posix(), "json": json_path.relative_to(root).as_posix(),
            "shared_context": memory.relative_to(root).as_posix() if memory else None,
            "fingerprint": kb["fingerprint"], "summary": summary,
        }, indent=2, ensure_ascii=False))
        return
    sections = summary["by_section"]
    console.print(f"[green]Base écrite[/green] : {md_path.relative_to(root).as_posix()}")
    console.print(
        f"  {summary['files_read']} fichiers lus · {sections.get('components', 0)} composants · "
        f"{sections.get('entry_points', 0)} points d'entrée · {sections.get('risks', 0)} zones à risque"
    )
    known = sum(1 for c in summary["commands"].values() if c["status"] != "unknown")
    console.print(f"  commandes : {known}/{len(summary['commands'])} connues · {summary['questions']} question(s) ouverte(s)")
    if memory:
        console.print(f"  renvoi posé dans {memory.relative_to(root).as_posix()} (lu par les agents)")
    console.print(f"  Poser les questions : [cyan]grimoire task ask <tâche> --from {json_path.relative_to(root).as_posix()}[/cyan]",
                  soft_wrap=True)
