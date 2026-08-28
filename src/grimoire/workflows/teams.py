"""Manifestes d'équipe — les rôles et la chaîne de handoff, rendus lisibles.

`framework/teams/` décrit trois équipes complètes : membres et rôles, contrats
d'entrée et de sortie, phases de livraison, outils autorisés, et la chaîne
vision → build → ops. Le schéma qui les gouverne est écrit lui aussi.

Rien dans le SDK ne les lisait. Trois fichiers, un schéma, zéro référence : la
brique multi-agents était spécifiée et morte. Ce module la charge, pour qu'un
workflow qui déclare ``team:`` puisse dire avec qui il tourne.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core import layout
from grimoire.data import framework_path


@dataclass(frozen=True, slots=True)
class TeamMember:
    """Un agent dans une équipe, avec son rôle et son caractère obligatoire."""

    name: str
    role: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "required": self.required}


@dataclass(frozen=True, slots=True)
class Team:
    """Une équipe : qui la compose, ce qu'elle produit, à qui elle passe la main."""

    name: str
    display_name: str = ""
    description: str = ""
    specialty: str = ""
    agents: tuple[TeamMember, ...] = ()
    deliverables: tuple[str, ...] = ()
    handoff_to: str = ""
    handoff_trigger: str = ""
    phases: tuple[str, ...] = field(default_factory=tuple)
    path: Path | None = None

    @property
    def required_agents(self) -> tuple[str, ...]:
        return tuple(member.name for member in self.agents if member.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "specialty": self.specialty,
            "agents": [member.to_dict() for member in self.agents],
            "required_agents": list(self.required_agents),
            "deliverables": list(self.deliverables),
            "handoff_to": self.handoff_to,
            "handoff_trigger": self.handoff_trigger,
            "phases": list(self.phases),
            "path": str(self.path) if self.path else "",
        }


def team_dirs(project_root: Path) -> list[Path]:
    """Répertoires de manifestes, tiers du projet d'abord puis cadre en repli."""
    candidates = [
        *(root / layout.TEAMS_SUBDIR for root in layout.search_roots(project_root)),
        framework_path() / "teams",
    ]
    return [path for path in candidates if path.is_dir()]


def _load_yaml(path: Path) -> dict[str, Any]:
    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    try:
        data = yaml.load(io.StringIO(path.read_text(encoding="utf-8")))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _members(raw: Any) -> tuple[TeamMember, ...]:
    if not isinstance(raw, list):
        return ()
    members: list[TeamMember] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        members.append(
            TeamMember(name=name, role=str(item.get("role", "")).strip(), required=bool(item.get("required", True)))
        )
    return tuple(members)


def _deliverables(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        return ()
    items = raw.get("deliverables")
    if not isinstance(items, list):
        return ()
    return tuple(str(item.get("name", "")).strip() for item in items if isinstance(item, dict) and item.get("name"))


def _phases(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        return ()
    items = raw.get("phases")
    if not isinstance(items, list):
        return ()
    return tuple(str(item.get("name", "")).strip() for item in items if isinstance(item, dict) and item.get("name"))


def parse_team(path: Path) -> Team | None:
    """Charge un manifeste ; ``None`` s'il ne porte pas de section ``team``."""
    data = _load_yaml(path).get("team")
    if not isinstance(data, dict):
        return None
    name = str(data.get("name", "")).strip() or path.stem
    raw_handoff = data.get("handoff")
    handoff: dict[str, Any] = raw_handoff if isinstance(raw_handoff, dict) else {}
    return Team(
        name=name,
        display_name=str(data.get("display_name", "")).strip(),
        description=str(data.get("description", "")).strip(),
        specialty=str(data.get("specialty", "")).strip(),
        agents=_members(data.get("agents")),
        deliverables=_deliverables(data.get("outputs")),
        handoff_to=str(handoff.get("to_team", "")).strip(),
        handoff_trigger=str(handoff.get("trigger", "")).strip(),
        phases=_phases(data.get("delivery_workflow")),
        path=path,
    )


def load_teams(project_root: Path) -> list[Team]:
    """Toutes les équipes visibles, dédoublonnées par nom, projet prioritaire."""
    seen: dict[str, Team] = {}
    for directory in team_dirs(project_root):
        for path in sorted(directory.glob("*.yaml")):
            team = parse_team(path)
            if team is None or team.name in seen:
                continue
            seen[team.name] = team
    return sorted(seen.values(), key=lambda t: t.name)


def load_team(project_root: Path, name: str) -> Team | None:
    """Résout une équipe par son nom déclaré ou par le nom de son fichier."""
    wanted = name.strip()
    if not wanted:
        return None
    for team in load_teams(project_root):
        if team.name == wanted or (team.path is not None and team.path.stem == wanted):
            return team
    return None
