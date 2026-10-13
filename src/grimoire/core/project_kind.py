"""Nature du projet — ``greenfield`` ou ``brownfield`` — avec la preuve qui la fonde.

Un projet qui contient déjà du code source n'a pas les mêmes besoins qu'un
dépôt vide : l'un doit d'abord être compris (graphe de code, périmètre
d'impact), l'autre cadré. Le classement vient d'une observation, jamais
d'une déclaration : ``source`` dit d'où il sort et ``evidence`` nomme les
fichiers vus (trois au plus).

Seuls comptent les fichiers de code d'un langage courant ; les dossiers
cachés, les dépendances installées, les sorties de build et les dossiers
``_grimoire*`` écrits par le kit sont ignorés — sinon l'échafaudage du kit
ferait de tout projet neuf un brownfield.

Limite : un dépôt dont le seul code est dans un langage absent de la liste,
ou dans un dossier ignoré, est classé greenfield.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["ProjectKind", "detect_project_kind"]

_SOURCE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb",
    ".php", ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".scala",
})
_IGNORED_DIRS = frozenset({
    "node_modules", "venv", "target", "build", "dist", "__pycache__", "vendor", "site-packages",
})
_MAX_EVIDENCE = 3


@dataclass(frozen=True, slots=True)
class ProjectKind:
    """Classement du projet et son fondement."""

    kind: str
    source: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "source": self.source, "evidence": list(self.evidence)}


def detect_project_kind(root: Path) -> ProjectKind:
    """``brownfield`` dès qu'un fichier de code est trouvé sous *root*, sinon ``greenfield``."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if not d.startswith((".", "_grimoire")) and d not in _IGNORED_DIRS
        )
        for name in sorted(filenames):
            if Path(name).suffix in _SOURCE_SUFFIXES:
                found.append((Path(dirpath) / name).relative_to(root).as_posix())
                if len(found) >= _MAX_EVIDENCE:
                    return ProjectKind("brownfield", "source-files", tuple(found))
    if found:
        return ProjectKind("brownfield", "source-files", tuple(found))
    return ProjectKind("greenfield", "source-files", ())
