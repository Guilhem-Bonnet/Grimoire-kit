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

import re
from collections.abc import Iterable
from pathlib import Path

from grimoire.hosts.emitters.base import Degradation, EmittedFile, Emitter, managed_header
from grimoire.hosts.surface import SkillSpec

SHARED_SKILLS_DIR = Path(".agents/skills")
SHARED_SKILLS_GLOB = ".agents/skills/*/SKILL.md"

#: Un nom de dossier sûr : minuscules, chiffres, ``.``, ``_``, ``-`` ; ni ``/`` ni ``..``.
_SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]*")


def safe_skill_slug(slug: str) -> bool:
    """*slug* peut-il nommer un dossier sous ``.agents/skills/`` sans en sortir ?"""
    return bool(_SAFE_SLUG.fullmatch(slug)) and ".." not in slug


def shared_skill_file(skill: SkillSpec) -> EmittedFile:
    """``.agents/skills/<slug>/SKILL.md`` : seuls ``name`` et ``description``, documentés partout.

    Lève :class:`ValueError` pour un nom qui sortirait du dossier (``../x``, ``/abs``) ou
    qu'un hôte refuserait : passer par :func:`shared_skills`, qui l'écarte en le déclarant.
    """
    if not safe_skill_slug(skill.slug):
        msg = f"nom de compétence refusé pour .agents/skills/ : {skill.slug!r}"
        raise ValueError(msg)
    header = Emitter.frontmatter({"name": skill.slug, "description": skill.description})
    return EmittedFile(
        relpath=SHARED_SKILLS_DIR / skill.slug / "SKILL.md",
        content=f"{header}\n{managed_header('.md')}\n\n{skill.body}",
    )


def shared_skills(skills: Iterable[SkillSpec]) -> tuple[list[EmittedFile], list[Degradation]]:
    """Les fichiers des compétences au nom sûr, et la dégradation qui nomme les autres.

    Codex et Gemini passent tous deux par ici : même filtre, même liste, donc le même
    motif possédé ne retire jamais le fichier de l'autre émetteur.
    """
    files: list[EmittedFile] = []
    rejected: list[str] = []
    for skill in skills:
        if safe_skill_slug(skill.slug):
            files.append(shared_skill_file(skill))
        else:
            rejected.append(skill.slug)
    if not rejected:
        return files, []
    gap = Degradation(
        surface="skill names",
        reason=f"Nom de compétence hors de `[a-z0-9][a-z0-9._-]*` (ou avec `..`) : {', '.join(map(repr, rejected))}.",
        fallback="compétence non écrite sous `.agents/skills/` ; la renommer dans sa source.",
    )
    return files, [gap]
