"""Conformité d'un skill à la spécification Agent Skills (issue #874).

Le kit émet des ``SKILL.md`` pour Claude Code et Copilot, deux hôtes qui suivent
https://agentskills.io/specification. Rien ne vérifiait que ce qui partait chez
l'utilisateur était conforme : un skill hors spec était émis tel quel, et l'hôte
le refusait ou le tronquait sans que ``doctor`` ni le gate ne le signalent.

Ce module ne contrôle que les règles de la spec. Il ne reprend pas la règle
``^grimoire-`` de l'ancien skill-validator : ``stack-*`` et ``meta-*`` sont légitimes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from grimoire.hosts.collect import parse_frontmatter

NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
BODY_MAX_LINES = 500

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1", re.MULTILINE | re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


@dataclass(frozen=True, slots=True)
class SkillViolation:
    """Une règle de la spec enfreinte, avec la valeur qui l'enfreint."""

    rule: str
    value: str
    message: str

    def __str__(self) -> str:
        return self.message


def _name_violations(name: str) -> list[SkillViolation]:
    """Règles de ``name`` : charset, tirets, longueur (spec : ``[a-z0-9-]``, 1 à 64)."""
    found: list[SkillViolation] = []
    if len(name) > NAME_MAX:
        found.append(
            SkillViolation(
                "name.length", name, f"name : {len(name)} caractères, {NAME_MAX} au maximum (valeur reçue « {name} »)"
            )
        )
    if _NAME_RE.fullmatch(name) is not None:
        return found
    if any(c.isupper() for c in name):
        reason = "majuscule interdite, [a-z0-9-] seulement"
    elif "--" in name:
        reason = "tiret doublé interdit"
    elif name.startswith("-") or name.endswith("-"):
        reason = "tiret initial ou final interdit"
    else:
        reason = "caractère interdit, [a-z0-9-] seulement"
    found.append(SkillViolation("name.format", name, f"name : {reason} (valeur reçue « {name} »)"))
    return found


def _local_targets(body: str) -> list[str]:
    """Cibles locales des liens du corps, hors blocs et spans de code (exemples, pas références)."""
    prose = _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", body))
    targets: list[str] = []
    for match in _LINK_RE.finditer(prose):
        target = match.group(1).split("#", 1)[0].split("?", 1)[0]
        if target and not _SCHEME_RE.match(target):
            targets.append(target)  # ni URL, ni ancre pure
    return targets


def _reference_violations(body: str) -> list[SkillViolation]:
    """Liens locaux du corps : relatifs, sans sortir du dossier du skill."""
    found: list[SkillViolation] = []
    for target in _local_targets(body):
        if target.startswith("/"):
            found.append(
                SkillViolation(
                    "references.relative", target, f"références : chemin absolu « {target} » (relatif requis)"
                )
            )
        elif ".." in target.split("/"):
            found.append(
                SkillViolation(
                    "references.escape", target, f"références : « {target} » remonte hors du dossier du skill"
                )
            )
    return found


def _chain_violations(skill_file: Path, body: str) -> list[SkillViolation]:
    """« Un seul niveau depuis SKILL.md » : un fichier référencé n'en référence pas un autre.

    La spec vise les chaînes de références, pas le nombre de segments du chemin
    (``scripts/x/y.py`` est un seul niveau). Seuls les ``.md`` présents sont lus.
    """
    found: list[SkillViolation] = []
    for target in _local_targets(body):
        referenced = skill_file.parent / target
        if referenced.suffix != ".md" or not referenced.is_file():
            continue
        try:
            nested = _local_targets(referenced.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if nested:
            found.append(
                SkillViolation(
                    "references.depth",
                    target,
                    f"références : « {target} » renvoie à son tour vers « {nested[0]} » "
                    "(un seul niveau depuis SKILL.md)",
                )
            )
    return found


def validate_skill_text(text: str, *, slug: str) -> tuple[SkillViolation, ...]:
    """Contrôle le contenu complet d'un skill (frontmatter + corps) nommé ``slug``.

    ``slug`` est le nom du fichier source, qui devient le dossier et le ``name``
    émis. Un ``name`` déclaré dans le frontmatter doit lui être identique.
    """
    meta, body = parse_frontmatter(text)
    found = _name_violations(slug)

    declared = meta.get("name")
    if declared is not None and str(declared) != slug:
        found.append(
            SkillViolation(
                "name.dir",
                str(declared),
                f"name : doit être identique au dossier « {slug} » (valeur reçue « {declared} »)",
            )
        )

    description = meta.get("description")
    description_text = "" if description is None else str(description)
    if not description_text.strip():
        found.append(SkillViolation("description.empty", "", "description : vide (1 à 1024 caractères requis)"))
    elif len(description_text) > DESCRIPTION_MAX:
        found.append(
            SkillViolation(
                "description.length",
                str(len(description_text)),
                f"description : {len(description_text)} caractères, {DESCRIPTION_MAX} au maximum",
            )
        )

    compatibility = meta.get("compatibility")
    if compatibility is not None:
        compat_text = str(compatibility)
        if not 1 <= len(compat_text) <= COMPATIBILITY_MAX:
            found.append(
                SkillViolation(
                    "compatibility.length",
                    str(len(compat_text)),
                    f"compatibility : {len(compat_text)} caractères, 1 à {COMPATIBILITY_MAX} requis",
                )
            )

    lines = len(body.splitlines())
    if lines >= BODY_MAX_LINES:
        found.append(
            SkillViolation("body.lines", str(lines), f"corps : {lines} lignes, moins de {BODY_MAX_LINES} requises")
        )

    found.extend(_reference_violations(body))
    return tuple(found)


def validate_skill_file(path: Path) -> tuple[SkillViolation, ...]:
    """Contrôle un fichier de skill ; le slug est le nom du fichier, ou du dossier pour ``<slug>/SKILL.md``."""
    slug = path.parent.name if path.name == "SKILL.md" else path.stem
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return (SkillViolation("file.unreadable", path.name, f"fichier illisible en UTF-8 ({exc.__class__.__name__})"),)
    found = validate_skill_text(text, slug=slug)
    if path.name != "SKILL.md":
        return found
    return found + tuple(_chain_violations(path, parse_frontmatter(text)[1]))
