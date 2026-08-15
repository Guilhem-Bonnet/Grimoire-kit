"""Aucune surface livrée ne doit annoncer un outil qui n'existe plus.

Le drainage de `framework/` (cf. framework/FREEZE.md) supprime des outils par
lots. Le risque n'est pas la suppression : c'est ce qui continue de les citer.
Un README qui liste un outil absent, un catalogue de résolution qui l'annonce
comme fournisseur, un tool qui le charge par `importlib` — trois façons de
livrer une promesse morte, dont deux se découvrent seulement à l'exécution.

Ce test est le garde-fou générique qui manquait : il vaut pour tous les lots
suivants, sans qu'on ait à se souvenir de balayer les docs à chaque fois.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "framework" / "tools"

# Fichiers qui parlent légitimement du passé ou sont régénérés par le build.
HISTORICAL = {
    "CHANGELOG.md",           # l'historique cite forcément ce qui a existé
    "web/data/architecture.json",  # généré par scripts/gen-site-data.py
}

# Les ADR consignent une décision à une date : on les annote quand le contexte
# change, on ne réécrit pas leur contenu — sinon ce ne sont plus des archives.
HISTORICAL_PREFIXES = ("docs/adr-",)

TOOL_REF = re.compile(r"`?\b([a-z][a-z0-9_-]*\.py)`?")


def _alive_tools() -> set[str]:
    return {p.name for p in TOOLS_DIR.glob("*.py")}


def _surface_files() -> list[Path]:
    """Surfaces où une promesse d'outil engage un utilisateur."""
    files: list[Path] = []
    for pattern in ("README.md", "README.fr.md"):
        candidate = ROOT / pattern
        if candidate.is_file():
            files.append(candidate)
    files += sorted((ROOT / "docs").rglob("*.md"))
    files += sorted(TOOLS_DIR.glob("README.md"))
    files += sorted((ROOT / "archetypes").rglob("*.md"))
    files += sorted((ROOT / "extensions").rglob("*.md"))
    return [
        f for f in files
        if (rel := str(f.relative_to(ROOT))) not in HISTORICAL
        and not rel.startswith(HISTORICAL_PREFIXES)
    ]


def _shipped_python_files() -> set[str]:
    """Tout ce qui est réellement livré, tous dossiers confondus.

    `framework/memory/`, `src/grimoire/` et les archétypes ont aussi des
    fichiers `.py` cités dans les docs — un nom absent de `framework/tools/`
    n'est dangling que s'il n'existe nulle part.
    """
    names: set[str] = set()
    for base in ("framework", "src", "archetypes", "extensions", "scripts"):
        root = ROOT / base
        if root.is_dir():
            names |= {p.name for p in root.rglob("*.py")}
    return names


@pytest.fixture(scope="module")
def alive() -> set[str]:
    tools = _alive_tools()
    assert tools, "framework/tools/ est vide — le test ne vérifierait plus rien"
    return tools


@pytest.fixture(scope="module")
def shipped() -> set[str]:
    return _shipped_python_files()


def test_docs_do_not_promise_removed_tools(shipped: set[str]) -> None:
    """Une doc qui cite `foo.py` alors que plus rien ne livre ce fichier."""
    offenders: dict[str, list[str]] = {}
    for path in _surface_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        cited = {
            name for name in TOOL_REF.findall(text)
            if name not in shipped and (
                f"framework/tools/{name}" in text or f"`{name}`" in text
                or f"python3 {name}" in text
            )
        }
        if cited:
            offenders[str(path.relative_to(ROOT))] = sorted(cited)

    assert not offenders, (
        "documentation qui annonce des outils absents de framework/tools/ :\n"
        + "\n".join(f"  {k} -> {', '.join(v)}" for k, v in sorted(offenders.items()))
    )


def test_tools_do_not_load_removed_tools(shipped: set[str]) -> None:
    """Un outil vivant qui charge un outil supprimé casse à l'exécution."""
    loader = re.compile(
        r"spec_from_file_location|import_module|_import_tool|sys\.executable"
        r"|subprocess|__file__|Path\(|parent\s*/"
    )
    offenders: dict[str, list[str]] = {}
    for path in sorted(TOOLS_DIR.glob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        dead: set[str] = set()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue  # un commentaire n'exécute rien (ex: `.../tool-name.py` en exemple)
            context = line + " " + (lines[i - 1] if i else "")
            if not loader.search(context):
                continue
            for name in TOOL_REF.findall(line):
                if name not in shipped:
                    dead.add(name)
        if dead:
            offenders[path.name] = sorted(dead)

    assert not offenders, (
        "outils qui chargent un outil supprimé :\n"
        + "\n".join(f"  {k} -> {', '.join(v)}" for k, v in sorted(offenders.items()))
    )


def test_tool_resolver_advertises_only_shipped_providers() -> None:
    """Le catalogue de résolution ne doit pas proposer un fournisseur absent."""
    resolver = TOOLS_DIR / "tool-resolver.py"
    if not resolver.is_file():
        pytest.skip("tool-resolver.py drainé")

    text = resolver.read_text(encoding="utf-8", errors="replace")
    advertised = set(re.findall(r'"(?:name|tool)":\s*"([a-z0-9_-]+\.py)"', text))
    missing = sorted(n for n in advertised if not (TOOLS_DIR / n).exists())

    assert not missing, f"fournisseurs annoncés mais non livrés : {', '.join(missing)}"
