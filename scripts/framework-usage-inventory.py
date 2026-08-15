#!/usr/bin/env python3
"""Usage inventory of framework/tools/ — portage decision instrument.

For every tracked ``framework/tools/*.py`` file, greps the repo for
references to its basename and classifies it:

- REFERENCED   — referenced from a runtime surface (src/, archetypes/,
                 _grimoire/, extensions/, scripts/, .github/, Makefile,
                 root shell entrypoints, framework/ outside tools/)
- TEST_ONLY    — only referenced from tests/
- TRANSITIVE   — loaded at runtime by a REFERENCED tool (importlib on a file
                 path); drainable only after its caller
- DOCS_ONLY    — only referenced from docs/, web/ or markdown files
- INTERNAL     — only referenced by other framework/tools/ files
- UNREFERENCED — no reference anywhere outside itself

Generated indexes that enumerate the whole frozen zone (see
``GENERATED_INDEXES``) are excluded: they cite every tool without calling any,
so counting them collapses every file into REFERENCED.

Writes ``docs/framework-tools-inventory.md``. Regenerate with:

    python scripts/framework-usage-inventory.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "framework-tools-inventory.md"

RUNTIME_PATHS = [
    "src", "archetypes", "_grimoire", "extensions", "scripts", ".github",
    "Makefile", "grimoire-init.sh", "grimoire.sh", "install.sh",
    "pyproject.toml", "mkdocs.yml",
]
TEST_PATHS = ["tests"]
DOCS_PATHS = ["docs", "web", "README.md", "README.fr.md", "ARCHITECTURE.md", "CONTRIBUTING.md"]
TOOLS_PATHS = ["framework"]

# Artefacts générés qui énumèrent la zone gelée fichier par fichier. Ils citent
# chaque outil sans en être un appelant : les compter comme référence classe
# tout en REFERENCED et neutralise l'instrument de décision.
#
# Cas vécu : `scripts/code-ratchet-baseline.json` (mécanisme d'application du
# gel, 3.24.0) liste les 113 fichiers gelés avec leur plafond de lignes. Comme
# `scripts` est une surface runtime, chaque outil héritait d'un hit runtime et
# l'inventaire est devenu aveugle — 0 candidat à la suppression pendant que le
# gel était censé drainer la zone.
GENERATED_INDEXES = {
    "scripts/code-ratchet-baseline.json",  # plafonds du gel — scripts/check-code-ratchet.py
    "web/data/architecture.json",          # données du site — scripts/gen-site-data.py
    "docs/framework-tools-inventory.md",   # cet inventaire lui-même
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
    ).stdout


def grep_hits(needle: str, paths: list[str]) -> set[str]:
    out = git("grep", "-l", "-F", needle, "--", *paths)
    return {h for h in out.splitlines() if h not in GENERATED_INDEXES}


def check_generated_indexes() -> list[str]:
    """Retourne les entrées de GENERATED_INDEXES qui n'existent plus.

    Un renommage silencieux ferait réapparaître le biais sans rien casser :
    on préfère le signaler bruyamment.
    """
    return [rel for rel in sorted(GENERATED_INDEXES) if not (ROOT / rel).exists()]


def classify(rel: str) -> tuple[str, dict[str, int]]:
    name = Path(rel).name
    runtime = set(grep_hits(name, RUNTIME_PATHS))
    tests = grep_hits(name, TEST_PATHS)
    docs = grep_hits(name, DOCS_PATHS)
    tools = {h for h in grep_hits(name, TOOLS_PATHS) if h != rel}
    tools_internal = {h for h in tools if h.startswith("framework/tools/")}
    framework_other = tools - tools_internal
    runtime |= framework_other  # framework/ outside tools counts as runtime
    counts = {
        "runtime": len(runtime),
        "tests": len(tests),
        "docs": len(docs),
        "internal": len(tools_internal),
    }
    if runtime:
        return "REFERENCED", counts
    if tests:
        return "TEST_ONLY", counts
    if docs:
        return "DOCS_ONLY", counts
    if tools_internal:
        return "INTERNAL", counts
    return "UNREFERENCED", counts


# Marqueurs d'un chargement réel : les outils de l'ère shell s'appellent entre
# eux par chemin de fichier (importlib, subprocess), jamais par import Python —
# une simple mention en docstring ne compte pas comme une dépendance.
LOADER_MARKERS = re.compile(
    r"spec_from_file_location|import_module|_import_tool|sys\.executable"
    r"|subprocess|__file__|Path\(|parent\s*/"
)


def load_edges(tools: list[str]) -> dict[str, set[str]]:
    """Qui charge qui, à l'intérieur de framework/tools/."""
    by_name = {Path(rel).name: rel for rel in tools}
    edges: dict[str, set[str]] = {}
    for rel in tools:
        try:
            lines = (ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            edges[rel] = set()
            continue
        found: set[str] = set()
        for i, line in enumerate(lines):
            context = line + " " + (lines[i - 1] if i else "")
            if not LOADER_MARKERS.search(context):
                continue
            for name, target in by_name.items():
                if target != rel and name in line:
                    found.add(target)
        edges[rel] = found
    return edges


def reachable_from(roots: list[str], edges: dict[str, set[str]]) -> dict[str, str]:
    """Fermeture transitive : outil atteint -> outil qui l'a fait atteindre.

    Sans cette passe, un outil chargé au runtime par un outil référencé est
    classé TEST_ONLY et part à la suppression, ce qui casse son appelant.
    """
    seen = dict.fromkeys(roots, "")
    queue = deque(roots)
    while queue:
        current = queue.popleft()
        for target in sorted(edges.get(current, ())):
            if target not in seen:
                seen[target] = current
                queue.append(target)
    return {k: v for k, v in seen.items() if v}


def main() -> None:
    tools = sorted(
        rel for rel in git("ls-files", "--", "framework/tools").splitlines()
        if rel.endswith(".py")
    )
    if not tools:
        sys.exit("no framework/tools/*.py files found")

    for missing in check_generated_indexes():
        print(f"warning: GENERATED_INDEXES entry no longer exists: {missing}", file=sys.stderr)

    verdicts = {rel: classify(rel) for rel in tools}
    roots = [rel for rel, (cls, _) in verdicts.items() if cls == "REFERENCED"]
    via = reachable_from(roots, load_edges(tools))

    rows: dict[str, list[tuple[str, int, dict[str, int]]]] = {}
    for rel in tools:
        lines = len((ROOT / rel).read_bytes().splitlines())
        cls, counts = verdicts[rel]
        if cls != "REFERENCED" and rel in via:
            cls = "TRANSITIVE"
            counts = {**counts, "via": Path(via[rel]).name}
        rows.setdefault(cls, []).append((rel, lines, counts))

    order = ["UNREFERENCED", "INTERNAL", "DOCS_ONLY", "TEST_ONLY", "TRANSITIVE", "REFERENCED"]
    total_lines = sum(n for group in rows.values() for _, n, _ in group)

    out = [
        "# Inventaire d'usage — framework/tools/",
        "",
        f"> Généré le {date.today().isoformat()} par"
        " `python scripts/framework-usage-inventory.py`."
        " Instantané de décision pour le portage/suppression"
        " (cf. framework/FREEZE.md) — régénérer avant tout arbitrage.",
        "",
        f"**{len(tools)} fichiers, {total_lines} lignes.** Classes par"
        " priorité de traitement : UNREFERENCED (suppression candidate),"
        " INTERNAL (référencé uniquement par d'autres outils de tools/),"
        " DOCS_ONLY (réécrire la doc ou porter), TEST_ONLY (test hérité"
        " sans usage runtime), TRANSITIVE (chargé au runtime par un outil"
        " référencé — supprimer l'appelant d'abord), REFERENCED (à porter"
        " vers src/ à la demande).",
        "",
    ]
    for cls in order:
        group = rows.get(cls, [])
        if not group:
            continue
        group_lines = sum(n for _, n, _ in group)
        out += [
            f"## {cls} — {len(group)} fichiers, {group_lines} lignes",
            "",
            "| Fichier | Lignes | runtime | tests | docs | interne | chargé par |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for rel, lines, c in sorted(group, key=lambda r: -r[1]):
            out.append(
                f"| {rel} | {lines} | {c['runtime']} | {c['tests']} |"
                f" {c['docs']} | {c['internal']} | {c.get('via', '—')} |"
            )
        out.append("")

    OUTPUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"written: {OUTPUT.relative_to(ROOT)}")
    for cls in order:
        group = rows.get(cls, [])
        if group:
            print(f"  {cls}: {len(group)} files, {sum(n for _, n, _ in group)} lines")


if __name__ == "__main__":
    main()
