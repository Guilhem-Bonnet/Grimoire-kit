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

Limite : l'empreinte porte sur le contenu des ``.py`` du répertoire source,
pas sur la version de l'analyseur ni sur les dépendances installées.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.codegraph.graph import CodeGraph

__all__ = [
    "EXCLUDED_DIRS",
    "RECEIPT_PATH",
    "Freshness",
    "check_freshness",
    "fingerprint_sources",
    "record_index",
]

#: Reçu d'indexation, relatif à la racine du projet.
RECEIPT_PATH = Path("_grimoire-output/codegraph/receipt.json")

_SCHEMA = 1

#: Dossiers jamais lus : l'indexation et l'empreinte partagent cette liste,
#: sinon un fichier compté d'un côté seulement ferait mentir le verdict.
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "source_dir": self.source_dir,
            "recorded_fingerprint": self.recorded_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "added": list(self.added),
            "modified": list(self.modified),
            "removed": list(self.removed),
        }


def _file_hashes(source: Path) -> dict[str, str]:
    """``{chemin relatif posix: sha256 du contenu}`` des ``.py`` sous *source*."""
    hashes: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(source).as_posix()
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _combine(hashes: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(hashes):
        digest.update(f"{rel}\0{hashes[rel]}\n".encode())
    return digest.hexdigest()


def fingerprint_sources(source: Path) -> str:
    """Empreinte des sources Python de *source* (chemins et contenus)."""
    return _combine(_file_hashes(source))


def _resolve_source(root: Path, source_dir: str) -> Path:
    return (root / source_dir).resolve()


def record_index(root: Path, source_dir: str = "src") -> tuple[CodeGraph, dict[str, Any]]:
    """Indexe *source_dir* (relatif à *root*) et écrit le reçu d'empreinte.

    Le reçu est écrit après l'indexation, avec les hachages lus avant elle :
    un fichier modifié pendant l'indexation rend le verdict ``stale`` au
    lieu d'être déclaré frais.
    """
    root = root.resolve()
    source = _resolve_source(root, source_dir)
    hashes = _file_hashes(source)
    graph = CodeGraph()
    graph.index_directory(source, exclude=set(EXCLUDED_DIRS))
    receipt: dict[str, Any] = {
        "schema": _SCHEMA,
        "source_dir": source_dir,
        "fingerprint": _combine(hashes),
        "files": hashes,
        "stats": graph.stats(),
        "indexed_at": datetime.now(UTC).isoformat(),
    }
    target = root / RECEIPT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    return graph, receipt


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
    except (OSError, ValueError, KeyError, TypeError):
        return Freshness(verdict="unverified", reason="receipt_unreadable")
    source = _resolve_source(root, source_dir)
    if not source.is_dir():
        return Freshness(verdict="unverified", reason="source_dir_missing", source_dir=source_dir,
                         recorded_fingerprint=recorded)
    try:
        current_files = _file_hashes(source)
    except OSError:
        return Freshness(verdict="unverified", reason="source_unreadable", source_dir=source_dir,
                         recorded_fingerprint=recorded)
    current = _combine(current_files)
    added = tuple(sorted(set(current_files) - set(recorded_files)))
    removed = tuple(sorted(set(recorded_files) - set(current_files)))
    modified = tuple(sorted(
        rel for rel in set(current_files) & set(recorded_files) if current_files[rel] != recorded_files[rel]
    ))
    verdict = "current" if current == recorded else "stale"
    return Freshness(
        verdict=verdict, source_dir=source_dir, recorded_fingerprint=recorded,
        current_fingerprint=current, added=added, modified=modified, removed=removed,
    )
