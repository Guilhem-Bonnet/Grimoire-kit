"""Vérificateur du pattern ``skill-classification-matrix`` (issue #636).

La règle ``skills.classified-before-use`` (``rule-packs.yaml``) exige qu'un
skill soit classé — ``core_template_candidates``, ``project_pack`` ou
``incubator`` sous ``managed_sources.skills`` de ``hook-registry.yaml`` —
avant d'être promu. Rien ne le vérifiait : ``patterns.*`` contrôle la forme
du catalogue, pas la classification des skills.

Périmètre : les skills **du projet**, c'est-à-dire ceux du palier
``_grimoire/overrides/skills/`` que :func:`grimoire.hosts.collect.collect_skills`
déploie sur les hôtes à côté des skills livrés par le kit. Les skills du kit
(``framework/hosts/skills/`` et le palier ``_grimoire/kit/skills/`` que le
scaffolder recopie) sont des gabarits cœur par construction : les exiger
dans la liste ferait rougir tout projet neuf sans rien apprendre. Les
dossiers ``.github/skills/`` et ``.claude/skills/`` sont des *sorties* de la
synchronisation d'hôte, pas des sources ; ils ne sont pas lus.

Une entrée de classification désigne un skill par son chemin relatif, par le
nom de son fichier (``slug.md``) ou par le dossier d'un ``SKILL.md``
(``.github/skills/<slug>/SKILL.md``), ce qui laisse les gabarits existants
valides.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from grimoire.core import layout
from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check, _load_yaml_file
from grimoire.core.standard_generation import STANDARD_DIR

__all__ = ["_verify_skill_classification", "classified_skill_keys"]

REGISTRY_PATH = STANDARD_DIR / "hook-registry.yaml"
CLASSES = ("core_template_candidates", "project_pack", "incubator")


def _entry_keys(entry: str) -> set[str]:
    """Les identités qu'une entrée de classification peut désigner."""
    raw = entry.strip().replace("\\", "/")
    if not raw:
        return set()
    path = PurePosixPath(raw)
    keys = {raw.lstrip("./")}
    if path.name.upper() == "SKILL.MD" and path.parent.name:
        keys.add(path.parent.name)
    elif path.suffix.lower() == ".md":
        keys.add(path.stem)
    else:
        keys.add(path.name)
    return keys


def classified_skill_keys(data: Any) -> set[str]:
    """Every slug or path ``managed_sources.skills`` classifies, whatever the class."""
    managed = data.get("managed_sources") if isinstance(data, dict) else None
    skills = managed.get("skills") if isinstance(managed, dict) else None
    if not isinstance(skills, dict):
        return set()
    keys: set[str] = set()
    for class_name in CLASSES:
        entries = skills.get(class_name)
        for entry in entries if isinstance(entries, list) else []:
            keys |= _entry_keys(str(entry))
    return keys


def _project_skills(root: Path) -> list[Path]:
    overrides_dir = layout.skill_dirs(root)[0]
    if not overrides_dir.is_dir():
        return []
    return sorted(path for path in overrides_dir.glob("*.md") if path.is_file())


def _verify_skill_classification(root: Path, result: StandardVerificationResult) -> None:
    skills = _project_skills(root)
    if not skills:
        return
    data = _load_yaml_file(root, REGISTRY_PATH, result) if (root / REGISTRY_PATH).is_file() else None
    keys = classified_skill_keys(data)
    unclassified = [
        path.stem
        for path in skills
        if path.stem not in keys and path.relative_to(root).as_posix() not in keys
    ]
    if unclassified:
        _add_check(
            result,
            "skills.unclassified",
            "warning",
            "Project skill(s) without a class under managed_sources.skills "
            f"(core_template_candidates, project_pack or incubator): {', '.join(unclassified)}.",
            path=REGISTRY_PATH,
        )
