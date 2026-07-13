#!/usr/bin/env python3
"""Usage inventory of framework/tools/ — portage decision instrument.

For every tracked ``framework/tools/*.py`` file, greps the repo for
references to its basename and classifies it:

- REFERENCED   — referenced from a runtime surface (src/, archetypes/,
                 _grimoire/, extensions/, scripts/, .github/, Makefile,
                 root shell entrypoints, framework/ outside tools/)
- TEST_ONLY    — only referenced from tests/
- DOCS_ONLY    — only referenced from docs/, web/ or markdown files
- INTERNAL     — only referenced by other framework/tools/ files
- UNREFERENCED — no reference anywhere outside itself

Writes ``docs/framework-tools-inventory.md``. Regenerate with:

    python scripts/framework-usage-inventory.py
"""

from __future__ import annotations

import subprocess
import sys
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


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
    ).stdout


def grep_hits(needle: str, paths: list[str]) -> set[str]:
    out = git("grep", "-l", "-F", needle, "--", *paths)
    return set(out.splitlines())


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


def main() -> None:
    tools = sorted(
        rel for rel in git("ls-files", "--", "framework/tools").splitlines()
        if rel.endswith(".py")
    )
    if not tools:
        sys.exit("no framework/tools/*.py files found")

    rows: dict[str, list[tuple[str, int, dict[str, int]]]] = {}
    for rel in tools:
        lines = len((ROOT / rel).read_bytes().splitlines())
        cls, counts = classify(rel)
        rows.setdefault(cls, []).append((rel, lines, counts))

    order = ["UNREFERENCED", "INTERNAL", "DOCS_ONLY", "TEST_ONLY", "REFERENCED"]
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
        " sans usage runtime), REFERENCED (à porter vers src/ à la"
        " demande).",
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
            "| Fichier | Lignes | runtime | tests | docs | interne |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for rel, lines, c in sorted(group, key=lambda r: -r[1]):
            out.append(
                f"| {rel} | {lines} | {c['runtime']} | {c['tests']} |"
                f" {c['docs']} | {c['internal']} |"
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
