"""Résolution de la cellule « Preuve » du claim-ledger (W3-02, VER-2).

Avant ce module, ``verify_claim_ledger`` ne contrôlait que la non-vacuité de la
cellule : ``src/inexistant.py:12`` valait preuve. Une cellule est maintenant
découpée en jetons ; chaque jeton de forme *référence* doit se résoudre :

- ``chemin:ligne`` (ou ``chemin:début-fin``) : fichier existant sous la racine du
  projet, ligne comprise dans le fichier ; ``@sha256:<hex>`` facultatif épingle
  le contenu (préfixe d'au moins 8 caractères) ;
- ``test-run.json#<run_id>`` : run enregistré, dont l'identifiant est celui écrit
  par ``record_acceptance_test_run`` et dont le verdict est vert ;
- ``mem:<id>`` : identifiant de mémoire, résolu par le *lookup* injecté ; sans
  lookup (cas par défaut, aucun backend n'est ouvert pendant une vérification),
  la référence reste non résolue — fail-closed ;
- URL ``http(s)`` : acceptée seulement si elle pointe une archive (Wayback
  Machine, archive.ph, perma.cc) ; aucun accès réseau.

Un jeton qui n'a pas la forme d'une référence (texte libre) n'est jamais
traité comme résolu : il est rendu ``unstructured`` et la vérification le
signale à part. Toute exception du résolveur est un échec de résolution.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from grimoire.core.standard_generation import EVIDENCE_DIR

__all__ = ["ProofResolution", "resolve_proof_cell"]

_SPLIT_RE = re.compile(r"[;,]\s*|\s+")
_PATH_LINE_RE = re.compile(
    r"^(?P<path>[^\s:#@]*(?:/[^\s:#@]*|\.[A-Za-z0-9_-]+)[^\s:#@]*):(?P<start>\d+)(?:-(?P<end>\d+))?"
    r"(?:@sha256:(?P<sha>[0-9a-fA-F]{8,64}))?$"
)
_TEST_RUN_RE = re.compile(r"^(?P<dir>(?:[^\s#]*/)?)test-run\.json#(?P<run>[A-Za-z0-9_.-]+)$")
_MEMORY_RE = re.compile(r"^mem(?:ory)?:(?P<id>\S+)$")
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_ARCHIVE_HOSTS = frozenset({"web.archive.org", "archive.org", "archive.ph", "archive.today", "archive.is", "perma.cc"})
_TRIM = "`'\"()[]<>.!"

MemoryLookup = Callable[[str], bool]


@dataclass(frozen=True)
class ProofResolution:
    """Verdict de résolution d'un jeton de la cellule Preuve."""

    token: str
    kind: str  # path_line | test_run | memory | url | unstructured
    resolved: bool
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"token": self.token, "kind": self.kind, "resolved": self.resolved, "reason": self.reason}


def _inside(root: Path, candidate: Path) -> bool:
    return candidate.resolve().is_relative_to(root.resolve())


def _resolve_path_line(root: Path, match: re.Match[str]) -> tuple[bool, str]:
    target = root / match["path"]
    if not _inside(root, target):
        return False, "path escapes the project root"
    if not target.is_file():
        return False, f"file not found: {match['path']}"
    start = int(match["start"])
    end = int(match["end"]) if match["end"] else start
    data = target.read_bytes()
    total = len(data.decode("utf-8", errors="replace").splitlines())
    if start < 1 or end < start or end > total:
        return (
            False,
            f"line {match['start']}{'-' + match['end'] if match['end'] else ''} outside {match['path']} ({total} lines)",
        )
    if match["sha"] and not hashlib.sha256(data).hexdigest().startswith(match["sha"].lower()):
        return False, f"sha256 of {match['path']} does not match the pinned hash"
    return True, ""


def _resolve_test_run(root: Path, task_id: str, match: re.Match[str]) -> tuple[bool, str]:
    run_file = root / (match["dir"] or f"{EVIDENCE_DIR.as_posix()}/{task_id}/") / "test-run.json"
    if not _inside(root, run_file) or not run_file.is_file():
        return (
            False,
            f"test-run.json not found: {run_file.relative_to(root) if _inside(root, run_file) else match['dir']}",
        )
    data = json.loads(run_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("run_id") != match["run"]:
        return False, f"run_id {match['run']} not recorded in test-run.json"
    if data.get("ok") is not True:
        return False, f"run {match['run']} is not green (ok={data.get('ok')!r})"
    return True, ""


def _resolve_url(token: str) -> tuple[bool, str]:
    host = (urlparse(token).hostname or "").lower()
    if host in _ARCHIVE_HOSTS:
        return True, ""
    return False, f"URL is not an archive snapshot: {host or token}"


def _resolve_token(root: Path, task_id: str, token: str, memory_lookup: MemoryLookup | None) -> ProofResolution:
    if match := _PATH_LINE_RE.match(token):
        kind, (ok, why) = "path_line", _resolve_path_line(root, match)
    elif match := _TEST_RUN_RE.match(token):
        kind, (ok, why) = "test_run", _resolve_test_run(root, task_id, match)
    elif match := _MEMORY_RE.match(token):
        kind = "memory"
        ok, why = (memory_lookup(match["id"]), "") if memory_lookup else (False, "no memory lookup available")
        if memory_lookup and not ok:
            why = f"memory id not found: {match['id']}"
    elif _URL_RE.match(token):
        kind, (ok, why) = "url", _resolve_url(token)
    else:
        return ProofResolution(token, "unstructured", False, "not a resolvable reference")
    return ProofResolution(token, kind, ok, why)


def resolve_proof_cell(
    root: Path, task_id: str, cell: str, *, memory_lookup: MemoryLookup | None = None
) -> list[ProofResolution]:
    """Un verdict par jeton de *cell* ; jamais d'exception (elle devient un échec)."""
    out: list[ProofResolution] = []
    for raw in _SPLIT_RE.split(cell.strip()):
        token = raw.strip(_TRIM) if not raw.lower().startswith("http") else raw.strip()
        if not token:
            continue
        try:
            out.append(_resolve_token(root, task_id, token, memory_lookup))
        except Exception as exc:  # fail-closed : un résolveur qui casse ne prouve rien
            out.append(ProofResolution(token, "error", False, f"resolver failed: {type(exc).__name__}: {exc}"))
    return out
