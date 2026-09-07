"""Garde-fou #275 — un accesseur public sans appelant ni test ne doit pas revenir en silence.

Inventaire par AST (même méthode que celle qui a produit #275, 2026-09-04) :
fonctions et méthodes publiques de `src/grimoire/` dont le nom n'apparaît nulle
part ailleurs dans le dépôt — ni `src/`, ni `tests/`, ni `docs/`, ni
`framework/`. Les commandes Typer, les outils MCP (`@x.command(...)`,
`@x.tool(...)`, `@x.callback(...)`) et les `visit_*` d'AST sont exclus
(enregistrés par décorateur / dispatch, pas par appel nommé).

Reproduction manuelle d'un cas :
    grep -rn "NOM_SYMBOLE" --include='*.py' --include='*.md' . | grep -v "def "

#275 a tranché 14 des 17 accesseurs trouvés par cette méthode (11 retirés,
3 branchés sur un appelant réel + test). Les 3 restants (`MissionLedger.
blocked_tasks`, `MissionLedger.events_for`,
`missions.projections.build_cockpit_from_paths`) touchent `missions/`, zone
active en parallèle sur d'autres chantiers au moment de #275 — l'issue
elle-même les a explicitly laissés de côté. `KNOWN_ORPHANS` les documente
comme dette assumée, pas comme trou silencieux : retirer un nom de cette
liste sans le brancher fait échouer le test, l'ajouter sans revue humaine
aussi (la liste est un totalisateur, pas un joker).
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_DECORATOR_DISPATCH_MARKERS = (".command(", ".command)", ".tool(", ".tool)", ".callback(", ".callback)")

# Overrides de méthodes de classes de base stdlib appelées par dispatch interne
# (jamais par nom depuis le code de Grimoire) — même motif que `visit_*`.
# `log_message` : hook de `http.server.BaseHTTPRequestHandler`.
_FRAMEWORK_OVERRIDE_NAMES = frozenset({"log_message"})


# Différés par #275 elle-même — zone missions/ active en parallèle au moment
# de la passe. Voir la doc-string du module.
KNOWN_ORPHANS = frozenset({
    "blocked_tasks",
    "events_for",
    "build_cockpit_from_paths",
})


def _decorator_source(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _is_dispatch_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        src = _decorator_source(dec)
        if any(marker.rstrip("()") in src for marker in {"command", "tool", "callback"}):
            return True
    return False


def _collect_public_defs(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []

    names: list[str] = []

    def visit_body(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name.startswith("_"):
                    continue
                if name.startswith("visit_"):
                    continue
                if name in _FRAMEWORK_OVERRIDE_NAMES:
                    continue
                if _is_dispatch_decorated(node):
                    continue
                names.append(name)
            elif isinstance(node, ast.ClassDef):
                visit_body(node.body)

    visit_body(tree.body)
    return names


def _iter_source_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", "*.py", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = [ROOT / line for line in out.splitlines() if line.strip()]
    return [f for f in files if f.is_file()]


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _build_reference_corpus(files: list[Path]) -> set[str]:
    """Set of identifiers appearing on a non-definition line, repo-wide.

    Mirrors the issue's manual reproduction: grep for the name, then drop
    lines containing "def " (the definition itself, whichever file it's in).
    """
    tokens: set[str] = set()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if "def " in line:
                continue
            tokens.update(_WORD_RE.findall(line))
    return tokens


@pytest.fixture(scope="module")
def orphan_public_accessors() -> set[str]:
    src_files = [f for f in _iter_source_files() if f.suffix == ".py" and f.is_relative_to(ROOT / "src" / "grimoire")]
    candidates: set[str] = set()
    for f in src_files:
        candidates.update(_collect_public_defs(f))

    all_files = _iter_source_files()
    referenced = _build_reference_corpus(all_files)

    return {name for name in candidates if name not in referenced}


def test_no_new_orphan_public_accessor(orphan_public_accessors: set[str]) -> None:
    """Un accesseur public sans appelant ni test doit être nommé dans KNOWN_ORPHANS, jamais muet."""
    unexpected = orphan_public_accessors - KNOWN_ORPHANS
    assert not unexpected, (
        "Nouveaux accesseurs publics sans appelant ni test (voir #275 pour la doctrine "
        f"brancher/retirer) : {sorted(unexpected)}"
    )


def test_known_orphans_are_still_orphan(orphan_public_accessors: set[str]) -> None:
    """La liste de dette assumée ne doit pas mentir : un nom qui redevient couvert doit en sortir."""
    stale = KNOWN_ORPHANS - orphan_public_accessors
    assert not stale, (
        f"Ces accesseurs ont désormais un appelant ou un test : {sorted(stale)} — "
        "retire-les de KNOWN_ORPHANS."
    )
