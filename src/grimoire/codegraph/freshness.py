"""Fraîcheur du code graph — verdict ``current`` / ``stale`` / ``unverified`` par empreinte de source.

Le graphe est une dérivation des fichiers ``.py`` : il ne vaut que tant que
ces fichiers sont ceux qu'il a lus. Un reçu, écrit au moment de l'indexation,
garde l'empreinte (sha256 du contenu de chaque source) ; le verdict la
recalcule à chaque lecture, sans rien geler par hook.

- ``current`` : l'empreinte recalculée égale celle du reçu.
- ``stale`` : elle diffère ; ``added`` / ``modified`` / ``removed`` nomment
  les fichiers (le périmètre à réindexer).
- ``unverified`` : aucun reçu, reçu illisible ou de schéma inconnu, ou
  répertoire source disparu. On ne dit jamais ``current`` sans l'avoir
  comparé : ``reason`` distingue ``no_receipt`` (rien n'a jamais été
  indexé) des cas où un reçu existe mais ne peut pas être lu.

Le reçu décrit la dernière indexation, quelle qu'en soit la cible
(``target``) : ``memory`` pour l'indexation en mémoire de l'outil MCP,
``neo4j`` pour la projection de ``memory graph sync-code``. Une seule liste
de fichiers sert à l'empreinte et à l'indexation, avec les exclusions
enregistrées dans le reçu, appliquées au chemin sous la source (jamais au
chemin absolu du projet).

Limite : l'empreinte porte sur le contenu des ``.py`` des sources, pas sur la
version de l'analyseur ni sur les dépendances installées. Un nom de fichier
non UTF-8 est comparé octet pour octet (``surrogateescape``) et affiché
échappé.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.codegraph.graph import CodeGraph
from grimoire.core import fsio

__all__ = [
    "EXCLUDED_DIRS",
    "RECEIPT_PATH",
    "Freshness",
    "check_freshness",
    "collect_sources",
    "combine_hashes",
    "diff_hashes",
    "fingerprint_sources",
    "hash_files",
    "record_index",
    "record_projection",
]

#: Reçu d'indexation, relatif à la racine du projet.
RECEIPT_PATH = Path("_grimoire-output/codegraph/receipt.json")

_SCHEMA = 1

#: Dossiers jamais lus par défaut. Le reçu garde l'ensemble réellement utilisé :
#: l'indexation et l'empreinte partagent la même liste de fichiers, sinon un
#: fichier compté d'un côté seulement ferait mentir le verdict.
EXCLUDED_DIRS = frozenset({
    "__pycache__", ".venv", "venv", ".git", "node_modules", "_grimoire-output",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", "build", "dist",
})


@dataclass(frozen=True, slots=True)
class Freshness:
    """Verdict de fraîcheur du graphe indexé."""

    verdict: str
    reason: str = ""
    source_dir: str = ""
    recorded_fingerprint: str = ""
    current_fingerprint: str = ""
    added: tuple[str, ...] = field(default_factory=tuple)
    modified: tuple[str, ...] = field(default_factory=tuple)
    removed: tuple[str, ...] = field(default_factory=tuple)
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "source_dir": self.source_dir,
            "target": self.target,
            "recorded_fingerprint": self.recorded_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "added": list(self.added),
            "modified": list(self.modified),
            "removed": list(self.removed),
        }


def _key(path: Path, base: Path) -> str:
    """Clé posix de *path* relative à *base* (absolue si *path* en sort)."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _shown(rel: str) -> str:
    """Nom affichable : un octet non UTF-8 est écrit ``\\xNN`` au lieu d'un substitut."""
    return rel.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def collect_sources(
    base: Path,
    sources: Iterable[Path],
    exclude: Collection[str] = EXCLUDED_DIRS,
    suffixes: tuple[str, ...] = (".py",),
) -> dict[str, Path]:
    """``{clé relative à *base*: fichier}`` des fichiers de *sources* (fichiers ou dossiers).

    *suffixes* : extensions retenues (``.py`` par défaut, celle du graphe ;
    la base de connaissance d'onboarding y ajoute les autres langages).

    Une seule liste pour l'empreinte et pour l'indexation. Les exclusions
    portent sur les noms de dossiers *sous* chaque source : le dossier où vit
    le projet (``build/``, ``venv/`` plus haut dans l'arborescence) n'y compte pas.
    """
    found: dict[str, Path] = {}
    for source in sources:
        if source.is_file():
            if source.suffix in suffixes:
                found[_key(source, base)] = source
            continue
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames[:] = [d for d in dirnames if d not in exclude]
            for name in filenames:
                if name.endswith(suffixes):
                    path = Path(dirpath) / name
                    found[_key(path, base)] = path
    return found


def hash_files(files: dict[str, Path]) -> dict[str, str]:
    """``{clé: sha256 du contenu}``."""
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in files.items()}


def _combine(hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(hashes):
        digest.update(f"{rel}\0{hashes[rel]}\n".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


#: Empreinte d'un ensemble ``{clé: sha256}`` (chemins et contenus), ordre indifférent.
combine_hashes = _combine


def diff_hashes(recorded: dict[str, str], current: dict[str, str]) -> tuple[tuple[str, ...], ...]:
    """``(ajoutés, modifiés, supprimés)`` entre deux relevés, noms affichables, triés."""
    added = tuple(sorted(_shown(r) for r in set(current) - set(recorded)))
    removed = tuple(sorted(_shown(r) for r in set(recorded) - set(current)))
    modified = tuple(sorted(_shown(r) for r in set(current) & set(recorded) if current[r] != recorded[r]))
    return added, modified, removed


def fingerprint_sources(source: Path) -> str:
    """Empreinte des sources Python de *source* (chemins et contenus)."""
    return _combine(hash_files(collect_sources(source, [source])))


def _resolve_source(root: Path, source_dir: str) -> Path:
    return (root / source_dir).resolve()


def _write_receipt(root: Path, receipt: dict[str, Any]) -> None:
    target = root / RECEIPT_PATH
    # ensure_ascii (défaut) : un nom non UTF-8 s'écrit en échappement JSON ASCII, jamais en octets invalides.
    fsio.atomic_write(target, json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def record_index(root: Path, source_dir: str = "src") -> tuple[CodeGraph, dict[str, Any]]:
    """Indexe *source_dir* (relatif à *root*) et écrit le reçu d'empreinte.

    Le reçu est écrit après l'indexation, avec les hachages lus avant elle :
    un fichier modifié pendant l'indexation rend le verdict ``stale`` au
    lieu d'être déclaré frais. Cible ``memory`` : le graphe n'est gardé que
    par l'appelant, rien n'est écrit dans Neo4j.
    """
    root = root.resolve()
    source = _resolve_source(root, source_dir)
    files = collect_sources(source, [source])
    hashes = hash_files(files)
    graph = CodeGraph()
    for path in files.values():
        graph.index_file(path, root=str(source))
    receipt: dict[str, Any] = {
        "schema": _SCHEMA,
        "target": "memory",
        "source_dir": source_dir,
        "exclude": sorted(EXCLUDED_DIRS),
        "fingerprint": _combine(hashes),
        "files": hashes,
        "stats": graph.stats(),
        "indexed_at": datetime.now(UTC).isoformat(),
    }
    _write_receipt(root, receipt)
    return graph, receipt


def record_projection(
    root: Path,
    sources: Iterable[Path],
    exclude: Collection[str],
    hashes: dict[str, str],
    stats: dict[str, int],
    *,
    target: str = "neo4j",
) -> dict[str, Any]:
    """Écrit le reçu d'une projection (``memory graph sync-code``) une fois qu'elle a réussi.

    *hashes* est calculé par l'appelant avant l'indexation, sur la liste de
    fichiers que :func:`collect_sources` a rendue pour ces *sources* et ces
    *exclude* ; les clés sont relatives à *root*.
    """
    root = root.resolve()
    receipt: dict[str, Any] = {
        "schema": _SCHEMA,
        "target": target,
        "source_dir": ".",
        "sources": [_key(s if s.is_absolute() else root / s, root) for s in sources],
        "exclude": sorted(exclude),
        "fingerprint": _combine(hashes),
        "files": hashes,
        "stats": stats,
        "indexed_at": datetime.now(UTC).isoformat(),
    }
    _write_receipt(root, receipt)
    return receipt


def check_freshness(root: Path) -> Freshness:
    """Verdict de fraîcheur du graphe de *root* ; ne lève jamais."""
    root = root.resolve()
    receipt_file = root / RECEIPT_PATH
    if not receipt_file.is_file():
        return Freshness(verdict="unverified", reason="no_receipt")
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("schema") != _SCHEMA:
            return Freshness(verdict="unverified", reason="receipt_schema_unknown")
        recorded = str(receipt["fingerprint"])
        recorded_files = {str(k): str(v) for k, v in dict(receipt["files"]).items()}
        source_dir = str(receipt["source_dir"])
        target = str(receipt.get("target", "memory"))
        exclude = {str(e) for e in receipt.get("exclude", EXCLUDED_DIRS)}
        raw_sources = receipt.get("sources")
        sources = None if raw_sources is None else [str(s) for s in raw_sources]
    except (OSError, ValueError, KeyError, TypeError):
        return Freshness(verdict="unverified", reason="receipt_unreadable")
    base = _resolve_source(root, source_dir)
    if sources is None and not base.is_dir():
        return Freshness(verdict="unverified", reason="source_dir_missing", source_dir=source_dir,
                         recorded_fingerprint=recorded, target=target)
    scan = [base] if sources is None else [root / s for s in sources]
    try:
        current_files = hash_files(collect_sources(base, scan, exclude))
        current = _combine(current_files)
    except (OSError, UnicodeError):
        return Freshness(verdict="unverified", reason="source_unreadable", source_dir=source_dir,
                         recorded_fingerprint=recorded, target=target)
    added, modified, removed = diff_hashes(recorded_files, current_files)
    verdict = "current" if current == recorded else "stale"
    return Freshness(
        verdict=verdict, source_dir=source_dir, recorded_fingerprint=recorded,
        current_fingerprint=current, added=added, modified=modified, removed=removed, target=target,
    )
