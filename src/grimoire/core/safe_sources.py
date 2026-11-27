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

import io
import itertools
import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

#: Entrées lues dans un dossier, et fichiers examinés : le reste est ignoré, et dit.
MAX_DIR_ENTRIES = 50_000  # plafond dur : au-delà, le dossier entier est refusé
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


#: Budget total d'une collecte : octets cumulés de corps et nombre de sources lues.
COLLECT_MAX_BYTES = 32 * 1024 * 1024
COLLECT_MAX_FILES = 1_000
BUDGET_REASON = "budget de collecte atteint"


class SourceBudget:
    """Le budget d'UNE collecte : au-delà, toutes les sources restantes sont refusées (ordre trié, déterministe)."""

    def __init__(self, max_bytes: int = COLLECT_MAX_BYTES, max_files: int = COLLECT_MAX_FILES) -> None:
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.used_bytes = 0
        self.used_files = 0
        self.exhausted = False

    def admit(self, size: int) -> bool:
        """Réserve *size* octets pour un fichier de plus ; ``False`` (et définitivement) si le budget est atteint."""
        if self.exhausted or self.used_files + 1 > self.max_files or self.used_bytes + size > self.max_bytes:
            self.exhausted = True
            return False
        self.used_files += 1
        self.used_bytes += size
        return True


# ── Front matter : analysé sous bornes, avant tout usage de ses valeurs ──────────────────────────────

#: Le bloc de front matter d'un fichier de source (même forme que celle de ``hosts.collect``).
FRONTMATTER_RE = re.compile(r"\A(?:﻿)?(?:<!--.*?-->\s*)?---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
FRONTMATTER_MAX_BYTES = 32 * 1024
FRONTMATTER_MAX_EVENTS = 5_000


def frontmatter_issue(text: str) -> str | None:
    """Raison de ne pas analyser le front matter de *text*, ou ``None``.

    Le front matter d'un skill, d'une commande ou d'un agent est une déclaration à plat : ni ancre ni alias
    (qui se développent à l'analyse), un bloc de quelques dizaines de Ko, un nombre borné d'éléments. Le
    contrôle lit le flux d'événements, sans construire de valeur, donc en temps linéaire ; il précède
    l'analyse quel que soit son backend. Un YAML mal formé n'est pas refusé ici : l'analyse le tolère.
    """
    match = FRONTMATTER_RE.match(text)
    if match is None:
        return None
    block = match.group(1)
    if len(block) > FRONTMATTER_MAX_BYTES:
        return f"front matter au-delà de {FRONTMATTER_MAX_BYTES} octets"
    from ruamel.yaml import YAML

    try:
        for count, event in enumerate(YAML(typ="safe").parse(io.StringIO(block)), 1):
            if type(event).__name__ == "AliasEvent" or getattr(event, "anchor", None) is not None:
                return "front matter avec ancre ou alias (refusé)"
            if count > FRONTMATTER_MAX_EVENTS:
                return "front matter trop fourni"
    except Exception:  # un YAML mal formé est toléré par l'analyse, ce n'est pas une bombe
        return None
    return None


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


def context_path_issue(root: Path, rel: str) -> str | None:
    """Raison de refuser un chemin déclaré par un agent (``context:``), ou ``None``.

    Le chemin doit être relatif, rester sous *root*, ne passer par aucun lien symbolique (fichier ni dossier)
    et désigner un fichier ordinaire. Un chemin absent du projet n'est pas refusé ici (``None``) : c'est une
    absence, que l'appelant traite à part.
    """
    if not rel or "\x00" in rel or Path(rel).is_absolute() or rel.startswith(("/", "\\")) or PureWindowsPath(rel).drive:
        return "chemin de contexte absolu ou invalide (refusé)"
    norm = os.path.normpath(rel)
    if norm == "." or norm == ".." or norm.startswith(".." + os.sep):
        return "chemin de contexte hors du projet (refusé)"
    root_resolved = root.resolve()
    expected = root_resolved / norm
    try:
        resolved = (root_resolved / rel).resolve(strict=True)
    except OSError:
        return None  # absent (ou illisible) : l'absence est une erreur de build, pas une exclusion
    if resolved != expected or not resolved.is_relative_to(root_resolved):
        return "chemin de contexte hors du projet ou en lien symbolique (refusé)"
    try:
        regular = stat.S_ISREG(expected.lstat().st_mode)
    except OSError:
        return None
    return None if regular else "chemin de contexte qui n'est pas un fichier ordinaire (refusé)"


def read_context_blocks(root: Path, paths: tuple[str, ...], max_total_bytes: int) -> str:
    """``### chemin`` + contenu de chaque fichier déclaré, dans l'ordre ; un fichier refusé est sauté.

    Lecture sûre sous *root* (relatif, ordinaire, sans lien, hors de rien d'autre que le projet), taille par
    fichier bornée et budget cumulé *max_total_bytes* (les suivants sont sautés une fois atteint).
    """
    blocs: list[str] = []
    budget = SourceBudget(max_bytes=max_total_bytes)
    for rel in paths:
        if context_path_issue(root, rel) is not None:
            continue
        try:
            text = read_source_text(root / rel, root=root, budget=budget, frontmatter=False)
        except Refused:
            continue
        blocs.append(f"### {rel}\n\n{text.strip()}")
    return "\n\n".join(blocs)


def read_source_text(
    path: Path,
    *,
    max_bytes: int = SOURCE_MAX_BYTES,
    root: Path | None = None,
    what: str = "fichier source",
    budget: SourceBudget | None = None,
    frontmatter: bool = True,
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
    if budget is not None and not budget.admit(before.st_size):
        raise Refused(BUDGET_REASON)
    try:
        with open_regular(path) as handle:
            data = handle.read(max_bytes + 1)
    except (OSError, ValueError) as exc:
        raise Refused(f"{what} refusé à l'ouverture ({exc.__class__.__name__})") from exc
    if len(data) > max_bytes:
        raise Refused(f"{what} au-delà de {max_bytes} octets")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused(f"{what} qui n'est pas de l'UTF-8") from exc
    if frontmatter:
        issue = frontmatter_issue(text)
        if issue is not None:
            raise Refused(issue)
    return text


def list_sources(
    directory: Path,
    label: str,
    *,
    suffix: str = ".md",
    skip: Callable[[str], bool] | None = None,
) -> tuple[dict[str, Path], list[Exclusion]]:
    """``nom sans suffixe -> chemin`` des fichiers ``*<suffix>`` d'un dossier, sans rien lire.

    Sélection déterministe, indépendante de l'ordre d'énumération du système de fichiers : tous les
    noms sont lus (sans ``stat``) jusqu'au plafond dur :data:`MAX_DIR_ENTRIES`, triés, et les
    :data:`MAX_EXAMINED` premiers sont retenus ; le reste est compté dans l'exclusion rendue. Au-delà
    du plafond dur, le dossier ENTIER est refusé (aucun fichier pris : résultat stable). Un dossier
    absent n'est ni une erreur ni une exclusion ; un dossier illisible est l'exclusion de *label*.
    *skip* écarte un nom sans bruit.
    """
    try:
        with os.scandir(directory) as entries:
            names = [e.name for e in itertools.islice(entries, MAX_DIR_ENTRIES + 1)]
    except (FileNotFoundError, NotADirectoryError):
        return {}, []
    except OSError as exc:
        return {}, [Exclusion.of(label, f"dossier illisible ({exc.__class__.__name__})")]
    if len(names) > MAX_DIR_ENTRIES:
        return {}, [Exclusion.of(label, f"dossier trop fourni : plus de {MAX_DIR_ENTRIES} entrées, dossier refusé")]
    wanted = sorted(n for n in names if n.endswith(suffix) and len(n) > len(suffix) and not (skip and skip(n)))
    excluded: list[Exclusion] = []
    if len(wanted) > MAX_EXAMINED:
        excluded.append(
            Exclusion.of(label, f"dossier trop fourni : {len(wanted) - MAX_EXAMINED} fichiers non examinés")
        )
    return {n[: -len(suffix)]: directory / n for n in wanted[:MAX_EXAMINED]}, excluded
