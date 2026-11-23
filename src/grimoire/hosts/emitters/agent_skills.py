"""Compétences au format ouvert ``.agents/skills/<slug>/SKILL.md``, lu par plusieurs hôtes.

Trois hôtes lisent ce même dossier de projet, avec le même frontmatter (``name`` égal au
nom du dossier, ``description``) :

- Codex : https://learn.chatgpt.com/docs/build-skills ;
- Gemini CLI, où ``.agents/skills/`` est l'alias de ``.gemini/skills/`` et prime sur lui :
  https://geminicli.com/docs/cli/skills/ et https://geminicli.com/docs/cli/creating-skills/ ;
- Antigravity (App et CLI ``agy``), dont c'est l'emplacement par défaut :
  https://antigravity.google/docs/skills et https://antigravity.google/docs/customization-discovery.

Un seul écrivain pour ces trois lecteurs : deux émetteurs synchronisés ensemble écrivent
le même octet au même chemin, et le motif possédé est le même, donc aucun des deux ne
retire le fichier de l'autre.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.hosts.emitters.base import EmittedFile, Emitter, managed_header
from grimoire.hosts.surface import SkillSpec

SHARED_SKILLS_DIR = Path(".agents/skills")
SHARED_SKILLS_GLOB = ".agents/skills/*/SKILL.md"


def shared_skill_file(skill: SkillSpec) -> EmittedFile:
    """``.agents/skills/<slug>/SKILL.md`` : seuls ``name`` et ``description``, documentés partout."""
    header = Emitter.frontmatter({"name": skill.slug, "description": skill.description})
    return EmittedFile(
        relpath=SHARED_SKILLS_DIR / skill.slug / "SKILL.md",
        content=f"{header}\n{managed_header('.md')}\n\n{skill.body}",
    )
