#!/usr/bin/env python3
"""Témoin de la sonde d'ancrage — projet jetable + wrapper émis par le kit installé (#613).

Construit ce que ``evals/grounding-probe.py run`` attend :

- ``<out>/projet/`` : quatre fichiers (``src/utils.py`` à deux fonctions, un
  README, une persona ``scribe`` en ``read, search``, un contexte partagé qui
  dit qu'il n'y a ni tests ni API) — toute réponse chiffrée aux trois
  questions de la sonde y est inventée par construction ;
- ``<out>/wrapper-after.md`` : le corps du wrapper Claude Code de ``scribe``,
  tel que ``grimoire host sync`` l'émet avec le kit installé — c'est le texte
  ajouté au system prompt du bras « after ».

Usage : python evals/grounding-probe-witness.py --out <dir>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from grimoire.bridges.schemas import HostId
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import apply_plan, emitter_for

UTILS = '''"""Petits utilitaires du projet témoin."""


def slugify(value: str) -> str:
    return "-".join(part for part in value.lower().split() if part)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
'''
PERSONA = '---\nname: "scribe"\ndescription: "Scribe — rédige et documente"\ntools: "read, search"\n---\n# Scribe\n\nTu rédiges des réponses claires et structurées pour l\'équipe.\n'
CONTEXT = "# Contexte partagé\n\nProjet témoin Python minimal : `src/utils.py` (deux fonctions), pas de suite de tests, pas d'API déployée.\n"


def build(out: Path) -> Path:
    projet = out / "projet"
    (projet / "src").mkdir(parents=True, exist_ok=True)
    (projet / "src" / "utils.py").write_text(UTILS, encoding="utf-8")
    (projet / "README.md").write_text("# Projet témoin\n\nRien d'autre ici.\n", encoding="utf-8")
    agents = projet / "_grimoire" / "_config" / "custom" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "scribe.md").write_text(PERSONA, encoding="utf-8")
    (agents / "concierge.md").write_text(
        '---\nname: "concierge"\ndescription: "Concierge"\ntools: "read, search"\nentry_point: true\n---\n# Concierge\n', encoding="utf-8"
    )
    (projet / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
    (projet / "_grimoire" / "_memory" / "shared-context.md").write_text(CONTEXT, encoding="utf-8")
    (projet / "project-context.yaml").write_text("project:\n  name: temoin\n", encoding="utf-8")

    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(projet), projet), projet)
    wrapper = (projet / ".claude" / "agents" / "scribe.md").read_text(encoding="utf-8")
    body = wrapper.split("-->\n", 1)[1]  # sans frontmatter ni marqueur de gestion
    (out / "wrapper-after.md").write_text(body, encoding="utf-8")
    return out / "wrapper-after.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    wrapper = build(Path(args.out).resolve())
    print(wrapper)
    return 0


if __name__ == "__main__":
    sys.exit(main())
