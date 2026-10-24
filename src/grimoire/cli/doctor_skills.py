"""Contrôle ``skill_spec`` de ``grimoire doctor`` (issue #874).

Séparé de :mod:`grimoire.cli.app` (fichier au-dessus du seuil de taille du
ratchet) : la seule appelante est la commande ``doctor``. Jamais un ``FAIL`` :
un skill de projet hors spec est une dette qu'il faut nommer avec sa règle, pas
un projet cassé. Les skills livrés par le kit sont bloqués par le test sur l'arbre réel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core import layout
from grimoire.hosts.skill_spec import validate_skill_file

__all__ = ["apply_skill_spec_check"]


def _skill_sources(project_root: Path) -> list[Path]:
    """Fichiers de skill visibles du projet, surcharges avant le niveau kit, un par slug."""
    seen: dict[str, Path] = {}
    for directory in layout.skill_dirs(project_root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            seen.setdefault(path.stem, path)
    return list(seen.values())


def apply_skill_spec_check(project_root: Path, results: list[dict[str, Any]], *, fmt: str, console: Any) -> None:
    """Ajoute l'entrée ``skill_spec`` à *results* et l'affiche (une ligne dans ``cli/app.py``)."""
    sources = _skill_sources(project_root)
    if not sources:
        return
    broken: list[str] = []
    for path in sources:
        for violation in validate_skill_file(path):
            broken.append(f"{path.name} : {violation}")
    if broken:
        detail = (
            f"{len(broken)} écart(s) à la spec Agent Skills : "
            + " ; ".join(broken)
            + " — corrigez le skill (règles : https://agentskills.io/specification)"
        )
        entry: dict[str, Any] = {"name": "skill_spec", "passed": True, "level": "warn", "detail": detail}
        results.append(entry)
        if fmt != "json":
            console.print(f"  [yellow]WARN[/yellow]  {detail}")
        return
    ok_detail = f"{len(sources)} skill(s) conformes à la spec Agent Skills"
    results.append({"name": "skill_spec", "passed": True, "level": "ok", "detail": ok_detail})
    if fmt != "json":
        console.print(f"  [green]OK[/green]  {ok_detail}")
