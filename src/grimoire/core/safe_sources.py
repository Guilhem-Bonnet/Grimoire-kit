"""Lecture sûre des fichiers de skills, de commandes et d'agents que le kit collecte dans un projet.

Le projet ouvert peut être un dépôt reçu d'ailleurs : son contenu n'est pas fiable. Ce module est
l'unique point de lecture de ces fichiers (collecteurs de :mod:`grimoire.hosts.collect` et catalogue
de skills du serveur MCP) :

- seul un fichier ordinaire est lu (``lstat`` puis ouverture sans suivre de lien ni bloquer, via
  :func:`grimoire.core.regular_file.open_regular`) : ni lien symbolique, ni tube, ni périphérique,
  ni dossier ;
- la lecture est bornée à ``limite + 1`` octets, après un contrôle de la taille annoncée ;
- les dossiers de sources d'un projet restent sous la racine du projet, sans lien en chemin ;
- le nombre d'entrées lues et examinées par dossier est borné ;
- le contenu doit être de l'UTF-8 strict ;
- toute erreur sur UN fichier devient l'exclusion motivée de CE fichier (:class:`Exclusion`), jamais
  une exception qui viderait toute la surface ;
- aucun texte venu d'un fichier n'est relayé tel quel dans une raison ou un affichage
  (:func:`safe_text`) : borné, sans caractère de contrôle, crochets neutralisés.

Module pur : ni le SDK ``mcp``, ni les émetteurs d'hôtes.
"""

from __future__ import annotations

import itertools
import os
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

#: Entrées lues dans un dossier, et fichiers examinés : le reste est ignoré, et dit.
MAX_DIR_ENTRIES = 5_000
MAX_EXAMINED = 500
#: Taille maximale d'un fichier source collecté dans un projet (au-delà : exclu, jamais tronqué).
SOURCE_MAX_BYTES = 1_048_576
#: Longueur d'un nom, d'une raison reprise dans une sortie.
NAME_DISPLAY_MAX = 80
REASON_DISPLAY_MAX = 300
#: Lignes d'exclusions montrées à l'utilisateur ; le reste est compté (« … et N autres »).
MAX_EXCLUSION_LINES = 10


def safe_text(value: object, limit: int = NAME_DISPLAY_MAX) -> str:
    """Un texte venu d'un fichier, rendu sûr pour un affichage : borné, sans contrôle, sans crochets.

    Les caractères non imprimables (contrôle, mise en forme, retours à la ligne) deviennent des
    espaces ; les crochets, qui ouvrent le balisage du moteur d'affichage du CLI, deviennent des
    parenthèses.
    """
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    text = " ".join(text.replace("[", "(").replace("]", ")").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True, slots=True)
class Exclusion:
    """Un fichier de source non collecté, et pourquoi (texte assaini, sans chemin de la machine)."""

    name: str
    reason: str

    @classmethod
    def of(cls, name: object, reason: object) -> Exclusion:
        return cls(safe_text(name), safe_text(reason, REASON_DISPLAY_MAX))

    def line(self) -> str:
        return f"{self.name} ({self.reason})"


def exclusion_lines(items: Iterable[tuple[str, str]], limit: int = MAX_EXCLUSION_LINES) -> list[str]:
    """``nom (raison)`` pour au plus *limit* éléments, puis « … et N autres » : jamais une liste sans borne."""
    lines = [f"{name} ({reason})" for name, reason in items]
    if len(lines) <= limit:
        return lines
    return [*lines[:limit], f"… et {len(lines) - limit} autres"]


def describe_exclusions(items: Iterable[Exclusion], limit: int = MAX_EXCLUSION_LINES) -> tuple[str, list[str]]:
    """``(en-tête compté, lignes bornées)`` pour des exclusions de collecte ; ``("", [])`` sans exclusion.

    Les doublons (même nom, même raison : un fichier vu par deux collecteurs) sont comptés une fois.
    """
    pairs = list(dict.fromkeys((e.name, e.reason) for e in items))
    if not pairs:
        return "", []
    noun = "fichier ignoré" if len(pairs) == 1 else "fichiers ignorés"
    return f"{len(pairs)} {noun} :", exclusion_lines(pairs, limit)


class Refused(Exception):  # noqa: N818 - un refus motivé, pas une erreur
    """Un fichier source refusé ; le message est la raison, rédigée ici, jamais reprise d'un fichier."""


def source_dir_issue(directory: Path, root: Path, *, what: str = "sources") -> str | None:
    """Raison de ne pas lire un dossier de sources du projet : il sort de *root* ou passe par un lien.

    ``resolve()`` + ``is_relative_to`` : le dossier résolu doit être exactement le chemin attendu sous la
    racine résolue, donc aucun lien symbolique en chemin ; un dossier absent n'a rien à dire.
    """
    try:
        expected = root.resolve() / directory.relative_to(root)
    except ValueError:
        return f"dossier des {what} hors du projet"
    try:
        resolved = directory.resolve(strict=True)
    except OSError:
        return None  # absent : rien à lire, rien à dire
    if resolved != expected or not resolved.is_relative_to(root.resolve()):
        return f"dossier des {what} hors du projet ou en lien symbolique"
    return None


def read_source_text(
    path: Path,
    *,
    max_bytes: int = SOURCE_MAX_BYTES,
    root: Path | None = None,
    what: str = "fichier source",
) -> str:
    """Le texte UTF-8 du fichier régulier *path* ; :class:`Refused` (la raison) sinon.

    ``lstat`` d'abord (ni lien, ni tube, ni périphérique, ni dossier), taille bornée avant la
    lecture, ouverture sans suivre de lien ni bloquer, lecture bornée à ``max_bytes + 1`` octets.
    Avec *root*, le dossier du fichier doit aussi rester sous la racine, sans lien en chemin.
    """
    from grimoire.core.regular_file import open_regular

    try:
        before = path.lstat()
    except OSError as exc:
        raise Refused(f"{what} illisible ({exc.__class__.__name__})") from exc
    if stat.S_ISLNK(before.st_mode):
        raise Refused(f"{what} en lien symbolique (non suivi)")
    if not stat.S_ISREG(before.st_mode):
        raise Refused(f"{what} qui n'est pas un fichier ordinaire")
    if root is not None and source_dir_issue(path.parent, root) is not None:
        raise Refused(f"{what} hors du projet ou sous un lien symbolique")
    if before.st_size > max_bytes:
        raise Refused(f"{what} de {before.st_size} octets, au-delà de {max_bytes}")
    try:
        with open_regular(path) as handle:
            data = handle.read(max_bytes + 1)
    except (OSError, ValueError) as exc:
        raise Refused(f"{what} refusé à l'ouverture ({exc.__class__.__name__})") from exc
    if len(data) > max_bytes:
        raise Refused(f"{what} au-delà de {max_bytes} octets")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused(f"{what} qui n'est pas de l'UTF-8") from exc


def list_sources(
    directory: Path,
    label: str,
    *,
    suffix: str = ".md",
    skip: Callable[[str], bool] | None = None,
) -> tuple[dict[str, Path], list[Exclusion]]:
    """``nom sans suffixe -> chemin`` des fichiers ``*<suffix>`` d'un dossier, sans rien lire.

    Au plus :data:`MAX_DIR_ENTRIES` entrées lues et :data:`MAX_EXAMINED` retenues (par ordre de
    nom) ; ce qui dépasse est dit dans l'exclusion rendue. Un dossier absent n'est ni une erreur ni
    une exclusion ; un dossier illisible est l'exclusion de *label*. *skip* écarte un nom sans bruit.
    """
    try:
        with os.scandir(directory) as entries:
            names = [e.name for e in itertools.islice(entries, MAX_DIR_ENTRIES)]
            more = next(entries, None) is not None
    except (FileNotFoundError, NotADirectoryError):
        return {}, []
    except OSError as exc:
        return {}, [Exclusion.of(label, f"dossier illisible ({exc.__class__.__name__})")]
    wanted = sorted(n for n in names if n.endswith(suffix) and len(n) > len(suffix) and not (skip and skip(n)))
    excluded: list[Exclusion] = []
    if more or len(wanted) > MAX_EXAMINED:
        excluded.append(Exclusion.of(label, f"dossier trop fourni : {MAX_EXAMINED} fichiers examinés au plus"))
    return {n[: -len(suffix)]: directory / n for n in wanted[:MAX_EXAMINED]}, excluded
