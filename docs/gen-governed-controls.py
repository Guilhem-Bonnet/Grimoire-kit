#!/usr/bin/env python3
"""Generate docs/governed-controls.md from the agentic-standard pattern catalog.

Single source of truth: framework/agentic-standard/templates/pattern-catalog.yaml.
Run from anywhere: ``python docs/gen-governed-controls.py``.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "framework/agentic-standard/templates/pattern-catalog.yaml"
OUTPUT = ROOT / "docs/governed-controls.md"
ORDER = [
    "context", "memory", "knowledge", "orchestration", "workflow",
    "provider", "security", "governance", "quality", "runtime", "observability",
]


def render() -> str:
    catalog = YAML(typ="safe").load(CATALOG)
    patterns = catalog["patterns"]
    groups: dict[str, list[dict]] = {}
    for pattern in patterns:
        groups.setdefault(pattern["category"], []).append(pattern)
    ordered = [c for c in ORDER if c in groups] + [c for c in sorted(groups) if c not in ORDER]

    out = [
        "# Référence des contrôles gouvernés",
        "",
        "> Page de référence générée depuis `framework/agentic-standard/templates/pattern-catalog.yaml`",
        "> (source unique). Régénérer via `python docs/gen-governed-controls.py`.",
        "",
        f"**{len(patterns)} patterns gouvernés** répartis sur {len(groups)} catégories. Chaque pattern pose un",
        "artefact déclaratif (`_grimoire/standard/*.yaml`) vérifié *fail-closed* par",
        "`grimoire standard verify` / `audit` / `score` / `gate`. Le profil minimal indique à partir",
        "de quelle maturité (`starter → controlled → orchestrated → governed → production`) le pattern",
        "devient pertinent.",
        "",
    ]
    for category in ordered:
        out += [
            f"## {category.capitalize()}",
            "",
            "| Pattern | Profil min | Intention | Artefact | Checks clés |",
            "|---|---|---|---|---|",
        ]
        for pattern in sorted(groups[category], key=lambda x: x["id"]):
            artifacts = ", ".join(f"`{a}`" for a in pattern.get("required_artifacts", [])) or "—"
            checks = ", ".join(f"`{c}`" for c in pattern.get("check_refs", [])) or "—"
            intent = str(pattern.get("intent", "")).replace("|", "\\|")
            out.append(f"| `{pattern['id']}` | {pattern.get('maturity', '')} | {intent} | {artifacts} | {checks} |")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
