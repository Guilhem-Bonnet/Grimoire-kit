"""``grimoire context-pack`` — matérialise un context-pack durable de repo."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from grimoire.tools.context_pack import build_context_pack, default_output_path

console = Console()

_root_arg = typer.Argument(Path(), help="Racine du repo à contextualiser.")
_out_opt = typer.Option(None, "--out", "-o", help="Fichier de sortie (défaut : repo-contexts/).")
_ttl_opt = typer.Option(30, "--ttl-days", help="Durée de vie du context-pack (jours).")
_json_opt = typer.Option(False, "--json", help="Émettre le context-pack sur stdout.")


def context_pack_command(
    root: Path = _root_arg,
    out: Path | None = _out_opt,
    ttl_days: int = _ttl_opt,
    as_json: bool = _json_opt,
) -> None:
    """Écrit un context-pack durable (contrat catalogue) pour le repo."""
    root = root.resolve()
    pack = build_context_pack(root, ttl_days=ttl_days)
    if as_json:
        typer.echo(json.dumps(pack, ensure_ascii=False, indent=2))
        return
    out_path = out or default_output_path(root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    score = pack["scorecard"]
    console.print(f"[green]context-pack écrit[/green] : {out_path}")
    console.print(
        f"  {score['included']} sources incluses · suffisance : {score['sufficiency']}"
    )
