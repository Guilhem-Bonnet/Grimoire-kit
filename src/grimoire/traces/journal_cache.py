"""Lecture incrémentale du journal des traces : relire seulement ce qui s'y est ajouté.

Le journal ne fait que s'ajouter des lignes (``TraceLedger._write_line``) ; or le cockpit le relisait en entier
(98 000 traces, 97 Mo sur un poste réel, ~2,7 s de CPU) à chaque lecture, cinq fois par fiche Piloter à froid,
et il grandit toutes les quelques secondes pendant qu'un agent travaille. Le résultat de la dernière lecture est
gardé par processus avec l'endroit où elle s'est arrêtée : un appel suivant ne parse que les lignes ajoutées
depuis. La réponse est la même qu'une relecture complète — le cache est abandonné, et le journal relu en entier,
dès que le fichier a changé d'inode, rétréci, ou que les octets juste avant le point de reprise ne sont plus
ceux qu'on a lus. Seules les lignes terminées par un saut de ligne sont retenues ; une ligne finale sans saut de
ligne est rendue mais relue ensuite.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from grimoire.traces.schemas import TraceRecord

__all__ = ["load_trace_journal"]


@dataclass(slots=True)
class _ParsedJournal:
    """Ce qu'une lecture complète ou partielle du journal a retenu."""

    inode: int
    #: Octets déjà consommés : toujours en fin de ligne complète.
    offset: int
    #: Les derniers octets consommés, pour reconnaître le même fichier au prochain appel.
    guard: bytes
    records: list[TraceRecord]


_LOCK = threading.Lock()
#: Un journal gardé par chemin ; les plus anciens sortent (un cockpit multi-projets ne retient pas tout).
_CACHE: dict[str, _ParsedJournal] = {}
_CACHE_MAX = 2
_GUARD_BYTES = 64


def clear() -> None:
    """Oublie les journaux gardés (tests)."""
    with _LOCK:
        _CACHE.clear()


def _prefix_unchanged(fh: BinaryIO, parsed: _ParsedJournal, info: os.stat_result) -> bool:
    """Vrai si le fichier ouvert est celui déjà lu et n'a fait que s'allonger depuis *parsed.offset*."""
    if info.st_ino != parsed.inode or info.st_size < parsed.offset:
        return False
    if parsed.offset == 0:
        return True
    fh.seek(parsed.offset - len(parsed.guard))
    return fh.read(len(parsed.guard)) == parsed.guard


def _parse_trace_lines(text: str) -> list[TraceRecord]:
    records: list[TraceRecord] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError, KeyError):
            records.append(TraceRecord.from_dict(json.loads(line)))
    return records


def load_trace_journal(path: Path) -> list[TraceRecord]:
    """Toutes les traces du journal *path*, relues seulement là où il a grandi (liste neuve à chaque appel)."""
    if not path.exists():
        return []
    key = str(path)
    with _LOCK, open(path, "rb") as fh:
        info = os.fstat(fh.fileno())
        parsed = _CACHE.get(key)
        if parsed is not None and not _prefix_unchanged(fh, parsed, info):
            parsed = None
        start = parsed.offset if parsed is not None else 0
        fh.seek(start)
        data = fh.read()
        end = data.rfind(b"\n") + 1
        fresh = _parse_trace_lines(data[:end].decode("utf-8"))
        tail = _parse_trace_lines(data[end:].decode("utf-8"))
        if parsed is None:
            parsed = _ParsedJournal(inode=info.st_ino, offset=0, guard=b"", records=[])
        parsed.records.extend(fresh)
        parsed.offset = start + end
        parsed.guard = (parsed.guard + data[:end])[-_GUARD_BYTES:]
        _CACHE.pop(key, None)
        _CACHE[key] = parsed
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        return [*parsed.records, *tail]
