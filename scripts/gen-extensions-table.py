#!/usr/bin/env python3
"""Generate the "Extensions disponibles" table of extensions/README.md.

Single source of truth: the ``extension.json`` manifests. The table is
rewritten between the ``extensions-table`` markers; everything outside
the markers is left untouched. Same contract as
``docs/gen-governed-controls.py`` — run from anywhere:

    python scripts/gen-extensions-table.py            # rewrite in place
    python scripts/gen-extensions-table.py --check    # exit 1 on drift
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS_DIR = ROOT / "extensions"
README = EXTENSIONS_DIR / "README.md"
START = "<!-- extensions-table:start (généré par scripts/gen-extensions-table.py) -->"
END = "<!-- extensions-table:end -->"


def first_sentence(text: str) -> str:
    """Keep the table readable: one sentence, no cell-breaking pipes."""
    sentence = text.split(". ")[0].rstrip(".")
    return sentence.replace("|", "\\|").replace("\n", " ")


def render_table() -> str:
    rows = []
    for manifest_path in sorted(EXTENSIONS_DIR.glob("*/extension.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ext_id = manifest["id"]
        kind = manifest.get("kind", "?")
        description = first_sentence(manifest.get("description", ""))
        patterns = ", ".join(manifest.get("patterns", {}).get("implements", []))
        rows.append(f"| [{ext_id}]({ext_id}/extension.json) | `{kind}` | {description} | {patterns} |")
    header = (
        "| Extension | Kind | Description | Patterns |\n"
        "| --- | --- | --- | --- |"
    )
    return "\n".join([header, *rows])


def apply(content: str) -> str:
    try:
        before, rest = content.split(START, 1)
        _, after = rest.split(END, 1)
    except ValueError:
        sys.exit(f"markers not found in {README} — expected {START!r} … {END!r}")
    return f"{before}{START}\n{render_table()}\n{END}{after}"


def main() -> None:
    current = README.read_text(encoding="utf-8")
    updated = apply(current)
    if "--check" in sys.argv:
        if updated != current:
            sys.exit(
                "extensions/README.md is out of sync with the extension.json "
                "manifests — run: python scripts/gen-extensions-table.py"
            )
        return
    if updated != current:
        README.write_text(updated, encoding="utf-8")
        print(f"updated {README.relative_to(ROOT)}")
    else:
        print("already in sync")


if __name__ == "__main__":
    main()
