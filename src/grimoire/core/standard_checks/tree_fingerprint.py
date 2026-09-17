"""Empreinte de l'arbre de travail — détecte un run de test périmé (issue #582 lot B, suite).

Un run enregistré par ``record_acceptance_test_run`` (:mod:`grimoire.core.
standard_checks.acceptance_test_run`) n'a de valeur que s'il correspond
encore au code présent : sans cette empreinte, un agent peut lancer
``gate run-tests`` tôt puis modifier le code sans jamais relancer les tests,
et le gate reste vert sur du code jamais exercé — exactement le trou que ce
lot doit fermer (revue de la PR #585). Ce module calcule l'empreinte, une
seule fois, pour l'écrivain (au moment du run) et le lecteur (à la
vérification) — jamais deux implémentations qui pourraient diverger.

Dans un dépôt git : sha256 de ``git rev-parse HEAD`` + ``git status
--porcelain=v1 -z`` + ``git diff HEAD``, ``_grimoire-output/`` exclu des deux
dernières commandes (ce dossier est écrit PAR le mécanisme qu'on mesure —
l'inclure invaliderait chaque run par lui-même dès l'écriture de
``test-run.json``). Hors dépôt git (ou si git échoue) : sha256 de la liste
triée ``(chemin, taille, mtime_ns)`` de tout fichier sous la racine,
``_grimoire-output/``, ``.venv``, ``node_modules``, ``target``, ``.git``
exclus.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

__all__ = ["compute_tree_fingerprint"]

#: Dossiers jamais comptés dans l'empreinte, quel que soit le mode de calcul —
#: `_grimoire-output` parce que le mécanisme lui-même y écrit (auto-
#: invalidation), les autres parce qu'ils ne sont pas des sources (dépendances
#: installées, artefacts de build, métadonnées VCS).
_EXCLUDED_DIRS = frozenset({"_grimoire-output", ".venv", "node_modules", "target", ".git"})

#: Magie de pathspec git : un seul répertoire exclu de la commande elle-même,
#: jamais un filtrage a posteriori de sa sortie.
_EXCLUDE_PATHSPEC = ":(exclude)_grimoire-output/**"

#: Court : une empreinte doit être quasi instantanée, jamais un budget de
#: temps notable dans un `gate check` par ailleurs volontairement sûr.
_GIT_TIMEOUT_S = 15.0


def _is_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def _run_git(root: Path, *args: str) -> str | None:
    """Une commande git, sortie texte UTF-8 ; ``None`` sur tout échec — jamais une exception."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git_fingerprint(root: Path) -> str | None:
    head = _run_git(root, "rev-parse", "HEAD")
    if head is None:
        return None
    status = _run_git(root, "status", "--porcelain=v1", "-z", "--", ".", _EXCLUDE_PATHSPEC)
    diff = _run_git(root, "diff", "HEAD", "--", ".", _EXCLUDE_PATHSPEC)
    if status is None or diff is None:
        return None
    digest = hashlib.sha256()
    digest.update(head.encode("utf-8"))
    digest.update(status.encode("utf-8"))
    digest.update(diff.encode("utf-8"))
    return digest.hexdigest()


def _fs_fingerprint(root: Path) -> str:
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(f"{rel.as_posix()}\t{stat.st_size}\t{stat.st_mtime_ns}")
    digest = hashlib.sha256()
    digest.update("\n".join(entries).encode("utf-8"))
    return digest.hexdigest()


def compute_tree_fingerprint(root: Path) -> str:
    """L'empreinte de l'arbre de travail, calculée maintenant — jamais mise en cache.

    Git-aware (voir le docstring du module) ; retombe sur le parcours du
    système de fichiers quand *root* n'est pas un dépôt git, ou quand git
    échoue lui-même (dépôt cloné en profondeur 1 sans historique exploitable,
    par exemple) — cette fonction ne lève jamais, un dépôt git cassé ou
    incomplet retombe sur l'empreinte filesystem plutôt que de bloquer
    l'appelant.
    """
    if _is_git_repo(root):
        fingerprint = _git_fingerprint(root)
        if fingerprint is not None:
            return fingerprint
    return _fs_fingerprint(root)
