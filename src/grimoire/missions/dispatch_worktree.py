"""Ce qu'un dispatch a réellement changé dans l'arbre de travail.

``git diff`` seul juge l'arbre entier : il impute à l'ouvrier tout ce qui était
déjà modifié avant lui et ne voit pas les fichiers qu'il crée. On photographie
donc l'état des fichiers modifiés ou nouveaux avant la cascade, et on compare.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

_GIT_TIMEOUT_S = 60.0
_LISTINGS = (
    ("diff", "--name-only", "--relative", "-z"),
    ("diff", "--cached", "--name-only", "--relative", "-z"),
    ("ls-files", "--others", "--exclude-standard", "-z"),
)


def _state(path: Path) -> str:
    """Taille et date plutôt qu'un hash : l'arbre peut porter de très gros fichiers non suivis."""
    try:
        stat = path.lstat()
    except OSError:
        return "absent"
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def worktree_fingerprint(root: Path) -> dict[str, str] | None:
    """État des fichiers modifiés ou nouveaux, ``None`` hors d'un dépôt git lisible."""
    paths: set[str] = set()
    for listing in _LISTINGS:
        try:
            completed = subprocess.run(
                ["git", *listing],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="surrogateescape",
                timeout=_GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        paths.update(name for name in completed.stdout.split("\0") if name)
    return {name: _state(root / name) for name in paths}


def changed_since(root: Path, before: Mapping[str, str]) -> tuple[str, ...] | None:
    """Chemins dont l'état diffère de ``before``, retours à l'état propre compris."""
    after = worktree_fingerprint(root)
    if after is None:
        return None
    return tuple(sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name)))
