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
elle-même les a explicitement laissés de côté. `KNOWN_ORPHANS` les
allowliste pour que ce garde-fou ne bloque pas dessus : un nom qui y entre
sans revue humaine reste une régression à surveiller sur PR, cette liste
n'étant pas elle-même vérifiée automatiquement (au-delà d'exclure sa propre
définition du corpus — voir `_iter_source_files`, sous peine de se
référencer elle-même et fausser le calcul). Documenter une entrée ici
ailleurs dans le dépôt (CHANGELOG, doc) la sort légitimement du calcul
d'orphelinage — un nom cité n'est plus « nulle part » — donc ce module ne
prétend pas non plus qu'un nom de `KNOWN_ORPHANS` reste détecté orphelin
pour toujours ; il garantit seulement qu'aucun nom absent de la liste ne
passe en silence.
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


_SELF = Path(__file__).resolve()


def _iter_source_files() -> list[Path]:
    """Fichiers texte suivis par git — jamais ce module lui-même.

    Une fois commité, ce fichier est trouvé par ``git ls-files`` comme
    n'importe quel autre : sans cette exclusion, les noms cités dans
    `KNOWN_ORPHANS` et cette doc-string se référencent eux-mêmes et
    disparaissent du corpus d'orphelins — un faux négatif qui n'existait pas
    tant que le fichier était non suivi (la régression qui a fait échouer la
    CI de #275 la première fois : vert en local avant `git add`, rouge après).
    """
    out = subprocess.run(
        ["git", "ls-files", "--", "*.py", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = [ROOT / line for line in out.splitlines() if line.strip()]
    return [f for f in files if f.is_file() and f.resolve() != _SELF]


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
