#!/usr/bin/env python3
"""Generate docs/glossaire.md from framework/glossary.yaml.

Single source of truth: ``framework/glossary.yaml`` — the same file the vue de
travail's infobulles read via ``/api/workspace/glossary``
(``web/workspace/glossary.js``). This script is the derivation the
specification requires (§3.2 : « la documentation en dérive ») — nothing here
invents a definition, it renders the ones the glossary already has, in the
order they appear in the YAML.

Run from anywhere:

    python scripts/gen-glossaire-doc.py            # rewrite in place
    python scripts/gen-glossaire-doc.py --check     # exit 1 on drift

``tests/unit/test_glossaire_doc.py`` calls :func:`render` directly and
compares it to the committed file, so a stale page fails the normal unit test
run — no separate CI step is needed to catch it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = ROOT / "framework" / "glossary.yaml"
OUTPUT = ROOT / "docs" / "glossaire.md"


def _entries() -> list[dict[str, Any]]:
    raw = yaml.safe_load(GLOSSARY.read_text(encoding="utf-8"))
    return list(raw["entries"])


def _link(entries_by_id: dict[str, dict[str, Any]], term_id: str) -> str:
    entry = entries_by_id[term_id]
    return f"[{entry['nom']}](#{term_id})"


def render() -> str:
    entries = _entries()
    by_id = {entry["id"]: entry for entry in entries}

    out = [
        "---",
        "audience: référence",
        "---",
        "",
        "# Glossaire",
        "",
        "> Page générée depuis `framework/glossary.yaml` (source unique). Régénérer",
        "> via `python scripts/gen-glossaire-doc.py`. C'est aussi le fichier que lisent",
        "> les infobulles épinglables de la vue de travail",
        "> (`web/workspace/glossary.js`, spécification §3.2) : éditer une définition ici",
        "> n'aurait aucun effet, éditer le YAML met à jour les deux.",
        "",
        f"**{len(entries)} concepts.**",
        "",
    ]

    for entry in entries:
        term_id = entry["id"]
        out += [f"## {entry['nom']} {{: #{term_id} }}", ""]
        out.append(str(entry["définition"]).strip())
        out.append("")
        if entry.get("raccourci"):
            out.append(f"Raccourci : `{entry['raccourci']}`")
            out.append("")
        termes = [t for t in entry.get("termes") or [] if t in by_id]
        if termes:
            liens = ", ".join(_link(by_id, t) for t in termes)
            out.append(f"Termes liés : {liens}")
            out.append("")
        if entry.get("doc"):
            out.append(f"Documentation : [{entry['doc']}](/{entry['doc']})")
            out.append("")

    return "\n".join(out).rstrip("\n") + "\n"


def main() -> int:
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else None
    updated = render()
    if "--check" in sys.argv:
        if updated != current:
            print(
                f"{OUTPUT.relative_to(ROOT)} désynchronisé de {GLOSSARY.relative_to(ROOT)} — "
                "régénérez : python scripts/gen-glossaire-doc.py",
                file=sys.stderr,
            )
            return 1
        print("déjà à jour")
        return 0
    if updated != current:
        OUTPUT.write_text(updated, encoding="utf-8")
        print(f"écrit {OUTPUT.relative_to(ROOT)}")
    else:
        print("déjà à jour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
