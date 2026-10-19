"""Résolution de la cellule « Preuve » du claim-ledger (W3-02, VER-2).

Avant ce module, ``verify_claim_ledger`` ne contrôlait que la non-vacuité de la
cellule : ``src/inexistant.py:12`` valait preuve. Une cellule est maintenant
découpée en jetons ; chaque jeton de forme *référence* doit se résoudre :

- ``chemin:ligne`` (ou ``chemin:début-fin``) : fichier existant sous la racine du
  projet, ligne comprise dans le fichier ; ``@sha256:<hex>`` facultatif épingle
  le contenu (préfixe d'au moins 8 caractères). Les écritures courantes sont
  normalisées : ``#L12``, ``:L12``, ``#L12-L20``, ``:12:`` (deux-points final),
  ``:12:5`` (colonne ignorée), ``Makefile:12`` ; ``chemin`` seul, ``chemin:`` et
  ``chemin::noeud`` (identifiant de test) désignent le fichier entier, qui doit
  exister (et, pour ``::noeud``, citer ce nom) ;
- ``test-run.json#<run_id>`` : le run enregistré *de cette tâche* (le chemin que
  ``record_acceptance_test_run`` écrit, ``acceptance_test_run_relpath``), dont
  l'identifiant est celui qu'il a écrit, dont le verdict est vert et qui porte
  son ``tree_fingerprint`` (un fichier écrit à la main n'en a pas) ;
- ``mem:<id>`` : identifiant de mémoire, résolu par le *lookup* injecté ; sans
  lookup (cas par défaut, aucun backend n'est ouvert pendant une vérification),
  la référence reste non résolue — fail-closed ;
- URL ``http(s)`` : acceptée seulement si elle pointe une archive (Wayback
  Machine, archive.ph, perma.cc) ; aucun accès réseau.

Un jeton dont la tête ressemble à un chemin (``/``, extension, nom connu sans
extension comme ``Makefile``, ou fichier qui existe) est une *tentative de
référence* : s'il ne se résout pas ou si son repère est illisible, il est
``unresolved``, jamais ``unstructured``. Seul le texte libre (``relu le code``,
``10:30``) est rendu ``unstructured`` et la vérification le signale à part.
Toute exception du résolveur est un échec de résolution.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from grimoire.core.standard_checks.controls import _load_recorded_test_run, acceptance_test_run_relpath

__all__ = ["ProofResolution", "resolve_proof_cell"]

_SPLIT_RE = re.compile(r"[;,]\s*|\s+")
# Repère après le chemin : ligne (`:12`, `:L12`, `#L12`), plage (`-20`, `-L20`),
# colonne ignorée (`:12:5`), noeud de test (`::test_y`), deux-points final, empreinte.
_REF_RE = re.compile(
    r"^(?P<path>[^\s:#@]+)"
    r"(?:[:#]L?(?P<start>\d+)(?:-L?(?P<end>\d+))?(?::\d+)?)?"
    r"(?:::(?P<node>[^\s:@]+))?"
    r":?"
    r"(?:@sha256:(?P<sha>[0-9a-fA-F]{8,64}))?$"
)
_TEST_RUN_RE = re.compile(r"^(?P<dir>(?:[^\s#]*/)?)test-run\.json#(?P<run>[A-Za-z0-9_.-]+)$")
_MEMORY_RE = re.compile(r"^mem(?:ory)?:(?P<id>\S+)$")
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_HEAD_SPLIT_RE = re.compile(r"[:#@]")
_EXT_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9_-]+$")
_SHORT_EXT_RE = re.compile(r"\.[A-Za-z0-9]$")
_KNOWN_BARE_NAMES = frozenset(
    {
        "Makefile", "GNUmakefile", "Dockerfile", "Containerfile", "Jenkinsfile", "Rakefile", "Gemfile",
        "Procfile", "Vagrantfile", "Justfile", "justfile", "LICENSE", "README", "CODEOWNERS", "CHANGELOG",
    }
)
_ARCHIVE_HOSTS = frozenset({"web.archive.org", "archive.org", "archive.ph", "archive.today", "archive.is", "perma.cc"})
_TRIM = "`'\"()[]<>.!"

MemoryLookup = Callable[[str], bool]


@dataclass(frozen=True)
class ProofResolution:
    """Verdict de résolution d'un jeton de la cellule Preuve."""

    token: str
    kind: str  # path_line | test_run | memory | url | unstructured | error
    resolved: bool
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"token": self.token, "kind": self.kind, "resolved": self.resolved, "reason": self.reason}


def _inside(root: Path, candidate: Path) -> bool:
    return candidate.resolve().is_relative_to(root.resolve())


def _looks_like_path(root: Path, head: str) -> bool:
    """True quand *head* (le jeton avant tout repère) est une tentative de désigner un fichier.

    Large à dessein : ce qui a l'allure d'un chemin et ne se résout pas est une
    erreur de preuve, pas du texte libre. Reste du texte libre ce qui n'a ni ``/``
    suivi d'une extension, ni extension d'au moins deux lettres, ni nom connu, ni
    fichier correspondant (``10``, ``v1``, ``e.g``, ``lecture/écriture``).
    """
    if not head:
        return False
    last = head.rstrip("/").rsplit("/", 1)[-1]
    if "/" in head and (head.endswith("/") or _EXT_RE.search(last) or _SHORT_EXT_RE.search(last)):
        return True
    if _EXT_RE.search(last) and not last.startswith("."):
        return True
    if head in _KNOWN_BARE_NAMES:
        return True
    try:
        return (root / head).exists()
    except (OSError, ValueError):  # silent-ok — nom illisible = tentative de référence, le résolveur la rejette
        return True


def _resolve_path_line(root: Path, path: str, match: re.Match[str] | None) -> tuple[bool, str]:
    if match is None:
        return False, f"unreadable locator after {path} (expected path:line, path#L12, path::test)"
    target = root / path
    if not _inside(root, target):
        return False, "path escapes the project root"
    if not target.is_file():
        return False, f"file not found: {path}"
    data = target.read_bytes()
    if match["start"]:
        start = int(match["start"])
        end = int(match["end"]) if match["end"] else start
        total = len(data.decode("utf-8", errors="replace").splitlines())
        if start < 1 or end < start or end > total:
            return (
                False,
                f"line {match['start']}{'-' + match['end'] if match['end'] else ''} outside {path} ({total} lines)",
            )
    if match["node"]:
        name = match["node"].rsplit("::", 1)[-1].split("[", 1)[0]
        if name and name not in data.decode("utf-8", errors="replace"):
            return False, f"test node {name} not found in {path}"
    if match["sha"] and not hashlib.sha256(data).hexdigest().startswith(match["sha"].lower()):
        return False, f"sha256 of {path} does not match the pinned hash"
    return True, ""


def _resolve_test_run(root: Path, task_id: str, match: re.Match[str]) -> tuple[bool, str]:
    """Seul le ``test-run.json`` enregistré pour *task_id* compte, jamais un dossier choisi par l'auteur."""
    canonical = acceptance_test_run_relpath(task_id)
    given = match["dir"].rstrip("/")
    if given and posixpath.normpath(given) != canonical.parent.as_posix():
        return False, f"only the test-run.json recorded for this task counts ({canonical.as_posix()}), not {given}/"
    if not (root / canonical).is_file():
        return False, f"test-run.json not found: {canonical.as_posix()}"
    data = _load_recorded_test_run(root, task_id)
    if data is None:
        return False, f"test-run.json unreadable: {canonical.as_posix()}"
    if data.get("run_id") != match["run"]:
        return False, f"run_id {match['run']} not recorded in test-run.json"
    if not data.get("tree_fingerprint"):
        return False, "test-run.json has no tree_fingerprint: not written by record_acceptance_test_run"
    if data.get("ok") is not True:
        return False, f"run {match['run']} is not green (ok={data.get('ok')!r})"
    return True, ""


def _resolve_url(token: str) -> tuple[bool, str]:
    host = (urlparse(token).hostname or "").lower()
    if host in _ARCHIVE_HOSTS:
        return True, ""
    return False, f"URL is not an archive snapshot: {host or token}"


def _resolve_token(root: Path, task_id: str, token: str, memory_lookup: MemoryLookup | None) -> ProofResolution:
    if _URL_RE.match(token):
        kind, (ok, why) = "url", _resolve_url(token)
    elif match := _TEST_RUN_RE.match(token):
        kind, (ok, why) = "test_run", _resolve_test_run(root, task_id, match)
    elif match := _MEMORY_RE.match(token):
        kind = "memory"
        ok, why = (memory_lookup(match["id"]), "") if memory_lookup else (False, "no memory lookup available")
        if memory_lookup and not ok:
            why = f"memory id not found: {match['id']}"
    elif _looks_like_path(root, head := _HEAD_SPLIT_RE.split(token, maxsplit=1)[0]):
        kind, (ok, why) = "path_line", _resolve_path_line(root, head, _REF_RE.match(token))
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
