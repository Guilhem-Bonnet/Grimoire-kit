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
--porcelain=v1 -z`` + ``git diff HEAD`` + ``(chemin, taille, mtime_ns)`` de
chaque fichier derrière une entrée non suivie (``??``) du status — un fichier
non suivi n'est représenté par git que par son chemin ; sans ce complément,
retoucher son contenu après le run ne changerait jamais l'empreinte, alors
que c'est le cas courant d'un agent qui crée un fichier puis le retouche
(revue de la PR #585, point 2). Hors dépôt git (ou si git échoue) : sha256
de la liste triée ``(chemin, taille, mtime_ns)`` de tout fichier sous la
racine.

``_grimoire-output/`` et les caches d'outillage non déterministes
(``.pytest_cache``, ``__pycache__``, ``.ruff_cache``, ``.mypy_cache``,
``.hypothesis``, ``.coverage``) sont exclus des deux modes — le premier
parce que le mécanisme mesuré y écrit lui-même (auto-invalidation), les
seconds parce qu'une commande de test les régénère à chaque run, avec un
contenu qui change sans que le code change (revue de la PR #585, point 1) :
sur un projet sans ``.gitignore`` adapté, les compter aurait périmé le run
dès son propre enregistrement.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

__all__ = ["compute_tree_fingerprint"]

#: Dossiers jamais comptés dans l'empreinte, quel que soit le mode de calcul —
#: `_grimoire-output` parce que le mécanisme lui-même y écrit (auto-
#: invalidation) ; les caches d'outillage parce qu'une commande de test les
#: régénère à chaque run sans que le code change (revue de la PR #585, point
#: 1) ; les autres parce qu'ils ne sont pas des sources (dépendances
#: installées, artefacts de build, métadonnées VCS).
_EXCLUDED_DIRS = frozenset({
    "_grimoire-output",
    ".venv",
    "node_modules",
    "target",
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    ".coverage",
})

#: Magie de pathspec git : chaque nom exclu de la commande elle-même, jamais
#: un filtrage a posteriori de la sortie. La magie ``glob`` est nécessaire —
#: sans elle, ``**`` n'a pas le sens « n'importe quelle profondeur » que ce
#: module lui donne (vérifié empiriquement : ``:(exclude)**/<nom>/**`` seul,
#: sans ``glob``, ne filtre qu'une occurrence à la racine). Les deux formes
#: sont nécessaires ensemble : ``**/<nom>/**`` filtre un dossier (l'entrée
#: elle-même, neuve et non suivie, ou son contenu énuméré individuellement
#: sous un répertoire déjà suivi) mais jamais un fichier plat du même nom
#: (``.coverage``) ; ``**/<nom>`` (sans le ``/**`` final) filtre ce fichier
#: plat mais jamais le contenu d'un dossier.
_EXCLUDE_PATHSPECS: tuple[str, ...] = tuple(
    spec
    for name in _EXCLUDED_DIRS
    for spec in (f":(exclude,glob)**/{name}", f":(exclude,glob)**/{name}/**")
)

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


def _untracked_paths(status: str) -> list[str]:
    """Les chemins des entrées ``??`` d'un ``git status --porcelain=v1 -z``."""
    return [entry[3:] for entry in status.split("\0") if entry.startswith("?? ")]


def _stat_entries(root: Path, rel_path: str) -> list[str]:
    """``(chemin, taille, mtime_ns)`` pour *rel_path* — un fichier, ou récursivement un dossier.

    Les caches d'outillage restent exclus même s'ils apparaissent à
    l'intérieur d'un dossier par ailleurs neuf et non suivi (ex. un dossier
    de sortie de test qui contiendrait aussi un ``__pycache__``) — même
    filtre que le mode filesystem, pour ne jamais diverger.
    """
    target = root / rel_path
    candidates = sorted(target.rglob("*")) if target.is_dir() else [target]
    entries: list[str] = []
    for path in candidates:
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
    return entries


def _git_fingerprint(root: Path) -> str | None:
    head = _run_git(root, "rev-parse", "HEAD")
    if head is None:
        return None
    status = _run_git(root, "status", "--porcelain=v1", "-z", "--", ".", *_EXCLUDE_PATHSPECS)
    diff = _run_git(root, "diff", "HEAD", "--", ".", *_EXCLUDE_PATHSPECS)
    if status is None or diff is None:
        return None
    untracked_entries: list[str] = []
    for rel_path in _untracked_paths(status):
        untracked_entries.extend(_stat_entries(root, rel_path))
    digest = hashlib.sha256()
    digest.update(head.encode("utf-8"))
    digest.update(status.encode("utf-8"))
    digest.update(diff.encode("utf-8"))
    digest.update("\n".join(sorted(untracked_entries)).encode("utf-8"))
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

    Appelée par ``record_acceptance_test_run`` **après** l'exécution de la
    commande de test (revue de la PR #585, point 1) : l'état de référence
    d'un run est celui qu'il a laissé derrière lui, pas celui d'avant — les
    caches d'outillage qu'une commande de test régénère (voir
    ``_EXCLUDED_DIRS``) sont de toute façon exclus, donc ce choix ne les
    concerne plus, mais il reste le bon modèle pour tout ce qu'une commande
    de test pourrait légitimement écrire sous les sources (fichiers générés
    commités par la suite, par exemple).
    """
    if _is_git_repo(root):
        fingerprint = _git_fingerprint(root)
        if fingerprint is not None:
            return fingerprint
    return _fs_fingerprint(root)
