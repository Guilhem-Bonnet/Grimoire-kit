"""Faits sourcés de la base de connaissance, et les fichiers qu'elle lit.

Un fait est une affirmation déduite du code, jamais devinée : il nomme ses
sources (``fichier:ligne``) et porte l'empreinte des fichiers qu'il cite, de
sorte qu'un fait se vérifie seul (``--check`` dit lesquels sont touchés par
un changement). Les empreintes réutilisent celles du graphe de code
(:mod:`grimoire.codegraph.freshness`) : même collecte, même hachage, même
comparaison.

Un fait d'absence (« aucune couverture mesurée ») n'a pas de source : il
tient tant que l'ensemble des fichiers lus n'a pas bougé, et un fichier
ajouté (un ``coverage.xml`` qui apparaît) rend la base périmée.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.codegraph.freshness import EXCLUDED_DIRS, collect_sources, combine_hashes

__all__ = [
    "CODE_SUFFIXES",
    "JS_SUFFIXES",
    "MANIFESTS",
    "Fact",
    "collect_project_files",
    "fact_fingerprint",
    "source_file",
]

JS_SUFFIXES: tuple[str, ...] = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
CODE_SUFFIXES: tuple[str, ...] = (".py", *JS_SUFFIXES)

#: Fichiers de la racine lus quand ils existent : manifestes, commandes, couverture.
MANIFESTS: tuple[str, ...] = (
    "pyproject.toml", "setup.cfg", "requirements.txt", "requirements-dev.txt",
    "package.json", "Makefile", "tox.ini", "coverage.xml", "project-context.yaml",
)

#: En plus des exclusions du graphe : l'installation du kit et des hôtes n'est
#: pas le code du projet, ni les sorties de couverture ou de build JS.
_EXTRA_EXCLUDED = frozenset({
    "_grimoire", "target", "vendor", "site-packages", "htmlcov", "coverage", ".next", ".nuxt", "out",
})


def _ignored(key: str) -> bool:
    """Dossier caché ou écrit par le kit (``_grimoire*``) : jamais le code du projet."""
    return any(part.startswith((".", "_grimoire")) for part in key.split("/")[:-1])


def collect_project_files(root: Path) -> dict[str, Path]:
    """``{clé posix relative: fichier}`` de tout ce que la base lit (code et manifestes).

    Une seule liste, pour construire la base et pour la vérifier.
    """
    code = collect_sources(root, [root], EXCLUDED_DIRS | _EXTRA_EXCLUDED, CODE_SUFFIXES)
    files = {key: path for key, path in code.items() if not _ignored(key)}
    for name in MANIFESTS:
        candidate = root / name
        if candidate.is_file():
            files[name] = candidate
    return dict(sorted(files.items()))


def source_file(ref: str) -> str:
    """``src/a.py:12`` → ``src/a.py`` (une source sans ligne est rendue telle quelle)."""
    head, sep, tail = ref.rpartition(":")
    return head if sep and tail.replace("-", "").isdigit() else ref


def fact_fingerprint(sources: tuple[str, ...], hashes: dict[str, str]) -> str:
    """Empreinte des fichiers cités par un fait ; vide pour un fait d'absence."""
    files = {source_file(s) for s in sources}
    if not files:
        return ""
    return combine_hashes({f: hashes.get(f, "missing") for f in files})


@dataclass(frozen=True, slots=True)
class Fact:
    """Une affirmation déduite du code, avec ses sources."""

    id: str
    section: str
    text: str
    sources: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, hashes: dict[str, str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "text": self.text,
            "sources": list(self.sources),
            "fingerprint": fact_fingerprint(self.sources, hashes),
            "data": self.data,
        }
