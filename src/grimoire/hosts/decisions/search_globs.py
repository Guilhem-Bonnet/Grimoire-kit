"""Ce qu'un outil de recherche lit, pour le refus des secrets (#1280).

Une recherche (``Grep``/``Glob`` de Claude Code, ``grep``/``glob``/``rg`` du harness
Copilot) lit ce qu'elle parcourt : elle est jugée comme une lecture. Deux voies :

- les chemins cherchés et les motifs de fichiers, comparés aux regex des familles
  de secrets (:data:`grimoire.hosts.secrets.SECRET_RULES`) comme un chemin lu ;
- chaque motif de fichiers, confronté à un échantillon de noms de secrets tiré des
  globs de ces mêmes familles, et à des témoins neutres. Un motif qui attrape un nom
  de secret sans attraper aucun témoin vise des secrets (``.env.*``, ``*.{pem,p12}``,
  ``id_*``) ; un motif large (``**/*``, ``*.py``, ``*.json``) attrape des témoins et
  reste permis, comme une lecture dont le chemin ne nomme aucun secret.

Le motif de contenu d'un ``Grep`` (``pattern``) n'est jamais lu : c'est une regex sur
le texte, chercher ``\\.env`` dans le code est légitime.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
from itertools import pairwise
from typing import Any

from grimoire.hosts.secrets import SECRET_RULES

#: Noms entiers (``glob`` en sous-chaîne prendrait un outil MCP ``set_global_…``).
SEARCH_NAMES = frozenset({"grep", "glob", "rg"})

#: Arguments qui désignent *où* une recherche lit : ``glob`` (filtre de fichiers de
#: ``Grep``), ``pattern`` de ``Glob`` (motif de chemin). Jamais ``pattern`` de ``Grep``.
_FILE_GLOB_KEYS = ("glob",)
_GLOB_TOOL_PATTERN_KEYS = ("pattern",)

#: Méta-caractères retirés pour la comparaison aux regex : ``**/*.pem`` se lit ``/.pem``.
_GLOB_META = str.maketrans({"*": "", "?": "", "[": "", "]": "", "!": "", "{": " ", "}": " ", ",": " "})

#: Borne de l'expansion des accolades : au-delà, le motif n'est pas développé plus loin.
_MAX_EXPANSIONS = 64


def _sample(glob: str) -> str:
    """Un nom concret qu'attrape le glob d'une famille (``**/.env.*`` → ``.env.x``)."""
    name = glob.removeprefix("**/").replace("/**", "/x").replace("*", "x")
    return name


#: Un nom de secret par glob de famille, dérivé des familles : il suit leur liste.
SECRET_SAMPLES: tuple[str, ...] = tuple(dict.fromkeys(_sample(g) for rule in SECRET_RULES for g in rule.globs))

#: Fichiers ordinaires d'un projet. Un motif qui en attrape un est un motif large.
NEUTRAL_SAMPLES: tuple[str, ...] = (
    "main.py",
    "README.md",
    "package.json",
    "config.yaml",
    "config.yml",
    "index.ts",
    "index.js",
    "Cargo.toml",
    "notes.txt",
    "Makefile",
    ".gitignore",
    "src/app.go",
)


def expand_braces(glob: str) -> tuple[str, ...]:
    """``a{b,c}d`` → ``abd``, ``acd`` (imbrication comprise, au plus :data:`_MAX_EXPANSIONS`)."""
    results = [glob]
    expanded: list[str] = []
    while results:
        current = results.pop()
        start = current.find("{")
        if start < 0 or len(expanded) + len(results) >= _MAX_EXPANSIONS:
            expanded.append(current)
            continue
        depth, end, cuts = 0, -1, [start]
        for index in range(start, len(current)):
            char = current[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
            elif char == "," and depth == 1:
                cuts.append(index)
        if end < 0:
            expanded.append(current)
            continue
        cuts.append(end)
        head, tail = current[:start], current[end + 1 :]
        results.extend(head + current[a + 1 : b] + tail for a, b in pairwise(cuts))
    return tuple(dict.fromkeys(expanded))


def _catches(glob: str, name: str) -> bool:
    """*glob* attrape-t-il *name*, à la racine ou sous un dossier ?"""
    return fnmatchcase(name, glob) or fnmatchcase(f"d/{name}", glob)


@lru_cache(maxsize=256)
def glob_secret(glob: str) -> str:
    """Le nom de secret qu'attrape *glob* sans attraper de témoin neutre, ou ``""``.

    Testé sur le motif entier et sur son dernier segment (``src/**/.env*`` → ``.env*``),
    chacun avec ses témoins : un segment large ne couvre pas un motif entier ciblé.
    """
    value = glob.strip().replace("\\", "/").lower()
    if not value:
        return ""
    for variant in expand_braces(value):
        tail = variant.rsplit("/", 1)[-1]
        for candidate, samples, neutral in (
            (variant, SECRET_SAMPLES, NEUTRAL_SAMPLES),
            (tail, tuple(s.rsplit("/", 1)[-1] for s in SECRET_SAMPLES), NEUTRAL_SAMPLES),
        ):
            hit = next((s for s in samples if _catches(candidate, s.lower())), "")
            if hit and not any(_catches(candidate, n.lower()) for n in neutral):
                return hit
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v.strip()]
    return []


def file_globs(name: str, payload: dict[str, Any]) -> list[str]:
    """Les motifs de fichiers d'un appel de recherche *name* (nom en minuscules)."""
    keys = _FILE_GLOB_KEYS + (_GLOB_TOOL_PATTERN_KEYS if name == "glob" else ())
    return [glob for key in keys for glob in _string_list(payload.get(key))]


def search_locations(name: str, payload: dict[str, Any], targets: tuple[str, ...]) -> list[str]:
    """Ce qu'une recherche parcourt, sous une forme que les regex de secrets reconnaissent.

    Le chemin cherché est un dossier autant qu'un fichier : il est aussi testé suivi
    d'un séparateur. ``paths`` (liste) compte comme ``path``. Les motifs de fichiers
    sont testés tels quels puis sans leurs jokers.
    """
    paths = [*targets, *_string_list(payload.get("paths"))]
    found = [*paths[len(targets) :], *(f"{p.rstrip('/')}/" for p in paths)]
    for glob in file_globs(name, payload):
        found.extend((glob, glob.translate(_GLOB_META)))
    return found
