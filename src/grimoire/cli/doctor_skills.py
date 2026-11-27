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
from grimoire.core.safe_sources import Exclusion, list_sources, safe_text, source_dir_issue
from grimoire.hosts.skill_spec import validate_skill_file

__all__ = ["apply_skill_spec_check"]


def _skill_sources(project_root: Path, notes: list[str] | None = None) -> list[Path]:
    """Fichiers de skill visibles du projet, surcharges avant le niveau kit, un par slug.

    Listage borné par :mod:`grimoire.core.safe_sources`, sans lien en chemin de dossier ; un dossier refusé
    est dit dans *notes* (jamais lu).
    """
    seen: dict[str, Path] = {}
    for directory in layout.skill_dirs(project_root):
        if not directory.is_dir():
            continue
        label = directory.name
        issue = source_dir_issue(directory, project_root, what="skills")
        if issue is not None:
            if notes is not None:
                notes.append(Exclusion.of(label, issue).line())
            continue
        found, issues = list_sources(directory, label)
        if notes is not None:
            notes.extend(i.line() for i in issues)
        for stem, path in found.items():
            seen.setdefault(stem, path)
    return list(seen.values())


def apply_skill_spec_check(project_root: Path, results: list[dict[str, Any]], *, fmt: str, console: Any) -> None:
    """Ajoute l'entrée ``skill_spec`` à *results* et l'affiche (une ligne dans ``cli/app.py``)."""
    broken: list[str] = []
    sources = _skill_sources(project_root, broken)
    if not sources and not broken:
        return
    for path in sources:
        for violation in validate_skill_file(path, project_root):
            broken.append(f"{safe_text(path.name)} : {violation}")
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
