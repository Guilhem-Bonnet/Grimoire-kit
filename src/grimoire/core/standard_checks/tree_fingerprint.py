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
--porcelain=v1 -z`` + ``git diff HEAD`` + ``(chemin, sha256 du contenu)`` de
chaque fichier derrière une entrée non suivie (``??``) du status — un fichier
non suivi n'est représenté par git que par son chemin ; sans ce complément,
retoucher son contenu après le run ne changerait jamais l'empreinte, alors
que c'est le cas courant d'un agent qui crée un fichier puis le retouche
(revue de la PR #585, point 2). Hors dépôt git (ou si git échoue) : sha256
de la liste triée ``(chemin, sha256 du contenu)`` de tout fichier sous la
racine.

Le contenu, jamais la ``mtime`` : un ``touch`` (ou une copie qui réécrit les
dates) ne doit pas périmer un run dont le code n'a pas bougé. Les deux modes
mêlent aussi l'identité de la toolchain qui exécute la commande de test
(:func:`_toolchain_identity`) : un même arbre testé avec un autre interpréteur
ou un autre binaire ne réutilise pas le run.

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
import shlex
import shutil
import subprocess
from collections.abc import Sequence
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
    "_grimoire-runtime-output",
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
    except (OSError, subprocess.TimeoutExpired):  # silent-ok — git indisponible : None, repli filesystem
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _untracked_paths(status: str) -> list[str]:
    """Les chemins des entrées ``??`` d'un ``git status --porcelain=v1 -z``."""
    return [entry[3:] for entry in status.split("\0") if entry.startswith("?? ")]


def _content_digest(path: Path) -> str | None:
    """sha256 du contenu de *path* ; ``None`` si le fichier a disparu ou n'est pas lisible.

    Le contenu, jamais la ``mtime`` : un ``touch`` ne doit pas périmer un run.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
    except OSError:  # silent-ok — fichier disparu ou illisible entre listage et lecture : hors empreinte
        return None
    return digest.hexdigest()


def _excluded(rel_posix: str, parts: tuple[str, ...], extra: Sequence[str]) -> bool:
    """*rel_posix* est-il hors empreinte : sous un dossier exclu par nom, ou sous un chemin de *extra* ?"""
    if any(part in _EXCLUDED_DIRS for part in parts):
        return True
    return any(rel_posix == prefix or rel_posix.startswith(f"{prefix}/") for prefix in extra)


def _extra_pathspecs(extra: Sequence[str]) -> tuple[str, ...]:
    """Les exclusions d'un appelant, chemins relatifs à la racine : le chemin lui-même et tout ce qu'il contient."""
    return tuple(spec for prefix in extra for spec in (f":(exclude,glob){prefix}", f":(exclude,glob){prefix}/**"))


def _content_entries(root: Path, rel_path: str, extra: Sequence[str] = ()) -> list[str]:
    """``(chemin, sha256 du contenu)`` pour *rel_path* — un fichier, ou récursivement un dossier.

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
        if _excluded(rel.as_posix(), rel.parts, extra):
            continue
        digest = _content_digest(path)
        if digest is not None:
            entries.append(f"{rel.as_posix()}\t{digest}")
    return entries


def _toolchain_identity(root: Path) -> str:
    """Ce qui exécute la commande de test : la commande, et le binaire résolu sur le ``PATH`` courant.

    Un ``PATH`` qui change (autre toolchain installée, binaire absent) change
    l'identité, donc l'empreinte : un run vert obtenu avec un autre
    interpréteur ou sans le binaire ne vaut pas pour la toolchain présente.
    Ce n'est pas une version du binaire (lancer ``--version`` coûterait un
    sous-processus à chaque gate) : une mise à jour en place du même binaire
    n'est pas détectée — limite écrite dans le rapport.
    """
    from grimoire.core.execution_needs import resolve_need

    need = resolve_need("test-runner", root)
    if not need.resolved or need.command is None:
        return "test-runner:unresolved"
    try:
        tokens = shlex.split(need.command)
    except ValueError:  # silent-ok — commande non découpable : son texte seul fait l'identité
        tokens = []
    executable = shutil.which(tokens[0]) if tokens else None
    return f"test-runner:{need.command}\n{executable or 'absent'}"


def _git_fingerprint(root: Path, extra: Sequence[str] = (), *, include_toolchain: bool = True) -> str | None:
    head = _run_git(root, "rev-parse", "HEAD")
    if head is None:
        return None
    specs = (*_EXCLUDE_PATHSPECS, *_extra_pathspecs(extra))
    status = _run_git(root, "status", "--porcelain=v1", "-z", "--", ".", *specs)
    diff = _run_git(root, "diff", "HEAD", "--", ".", *specs)
    if status is None or diff is None:
        return None
    untracked_entries: list[str] = []
    for rel_path in _untracked_paths(status):
        untracked_entries.extend(_content_entries(root, rel_path, extra))
    digest = hashlib.sha256()
    digest.update(head.encode("utf-8"))
    digest.update(status.encode("utf-8"))
    digest.update(diff.encode("utf-8"))
    digest.update("\n".join(sorted(untracked_entries)).encode("utf-8"))
    if include_toolchain:
        digest.update(_toolchain_identity(root).encode("utf-8"))
    return digest.hexdigest()


def _git_content_fingerprint(root: Path, extra: Sequence[str] = ()) -> str | None:
    """Empreinte de contenu seul d'un dépôt git : ``(chemin, sha256 du contenu)`` des fichiers présents.

    Les fichiers suivis et les non suivis non ignorés (``git ls-files --cached
    --others --exclude-standard``), jamais ``HEAD``, le status ni le diff : un
    ``git commit`` du même contenu ne change aucun octet de l'arbre, donc pas
    l'empreinte. Même format d'entrées que le mode filesystem, mêmes exclusions.
    Un fichier ignoré par git n'y compte pas (comme dans l'empreinte d'un run de test).
    """
    specs = (*_EXCLUDE_PATHSPECS, *_extra_pathspecs(extra))
    listing = _run_git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".", *specs)
    if listing is None:
        return None
    entries: list[str] = []
    for rel_path in sorted({name for name in listing.split("\0") if name}):
        path = root / rel_path
        rel_parts = Path(rel_path).parts
        if not path.is_file() or _excluded(rel_path, rel_parts, extra):
            continue  # supprimé de l'arbre (encore dans l'index), dossier ou sous-module
        digest = _content_digest(path)
        if digest is not None:
            entries.append(f"{rel_path}\t{digest}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _fs_fingerprint(root: Path, extra: Sequence[str] = (), *, include_toolchain: bool = True) -> str:
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if _excluded(rel.as_posix(), rel.parts, extra):
            continue
        digest = _content_digest(path)
        if digest is not None:
            entries.append(f"{rel.as_posix()}\t{digest}")
    fingerprint = hashlib.sha256()
    fingerprint.update("\n".join(entries).encode("utf-8"))
    if include_toolchain:
        fingerprint.update(_toolchain_identity(root).encode("utf-8"))
    return fingerprint.hexdigest()


def compute_tree_fingerprint(
    root: Path,
    *,
    extra_excludes: Sequence[str] = (),
    include_toolchain: bool = True,
    content_only: bool = False,
) -> str:
    """L'empreinte de l'arbre de travail, calculée maintenant — jamais mise en cache.

    *extra_excludes* : chemins relatifs à *root* (POSIX, fichier ou dossier)
    ignorés en plus des exclusions communes, dans les deux modes. Le reçu de
    revue (:mod:`grimoire.missions.review_receipt`, W3-01) y met l'état que le
    kit réécrit lui-même entre le reçu et le gate (journal des tâches, board
    projeté) : sans quoi le premier geste du kit périmerait le reçu. Vide par
    défaut — l'empreinte d'un run de test ne change pas.

    *include_toolchain* : mêle (défaut) l'identité de la toolchain de test
    (:func:`_toolchain_identity`, dont le binaire résolu sur le ``PATH``
    courant). Voulu pour l'empreinte d'un run de test ; faux pour une
    empreinte de contenu seul, comme celle du reçu de revue : relire un code
    ne dépend pas du ``PATH`` du processus qui lance ensuite le gate.

    *content_only* : empreinte de contenu seul, y compris en dépôt git
    (:func:`_git_content_fingerprint`) : ni ``HEAD``, ni status, ni diff, donc
    un ``git commit`` du même contenu ne la change pas. C'est celle du reçu de
    revue (W3-01) : valider ce qui a été relu n'est pas changer le code. Sans
    la toolchain (implique ``include_toolchain=False``). Faux par défaut :
    l'empreinte d'un run de test garde ``HEAD``.

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
    extra = tuple(prefix.strip("/") for prefix in extra_excludes if prefix.strip("/"))
    if content_only:
        include_toolchain = False
    if _is_git_repo(root):
        fingerprint = (
            _git_content_fingerprint(root, extra)
            if content_only
            else _git_fingerprint(root, extra, include_toolchain=include_toolchain)
        )
        if fingerprint is not None:
            return fingerprint
    return _fs_fingerprint(root, extra, include_toolchain=include_toolchain)
