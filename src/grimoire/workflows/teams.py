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
from grimoire.core.exceptions import GrimoireError
from grimoire.data import framework_path


class TeamManifestError(GrimoireError):
    """Un manifeste d'équipe existe mais ne se lit pas.

    ``None`` était la réponse pour « pas une équipe » comme pour « fichier
    illisible » : l'équipe manquait au catalogue sans une ligne.
    """

    def __init__(self, path: Path, cause: str) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"{path.name} illisible : {cause}")


@dataclass(frozen=True, slots=True)
class UnreadableManifest:
    """Un fichier de `teams/` que le catalogue n'a pas pu lire, et pourquoi."""

    path: Path
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "reason": self.reason}


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
    """Le manifeste comme table ; :class:`TeamManifestError` s'il ne se lit pas."""
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    yaml = YAML(typ="safe")
    try:
        data = yaml.load(io.StringIO(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise TeamManifestError(path, f"{type(exc).__name__}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TeamManifestError(path, f"attendu une table YAML, trouvé {type(data).__name__}")
    return data


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
    """Charge un manifeste ; ``None`` s'il ne porte pas de section ``team``.

    Lève :class:`TeamManifestError` si le fichier existe et ne se lit pas.
    """
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


@dataclass(frozen=True, slots=True)
class TeamCatalog:
    """Ce que le chargeur a lu, et ce qu'il n'a pas pu lire."""

    teams: tuple[Team, ...] = ()
    unreadable: tuple[UnreadableManifest, ...] = ()


def load_team_catalog(project_root: Path) -> TeamCatalog:
    """Toutes les équipes visibles, dédoublonnées par nom, projet prioritaire —
    et chaque manifeste illisible, nommé plutôt que passé sous silence."""
    seen: dict[str, Team] = {}
    unreadable: list[UnreadableManifest] = []
    for directory in team_dirs(project_root):
        for path in sorted(directory.glob("*.yaml")):
            try:
                team = parse_team(path)
            except TeamManifestError as exc:
                unreadable.append(UnreadableManifest(path, exc.cause))
                continue
            if team is None or team.name in seen:
                continue
            seen[team.name] = team
    return TeamCatalog(tuple(sorted(seen.values(), key=lambda t: t.name)), tuple(unreadable))


def load_teams(project_root: Path) -> list[Team]:
    """Les équipes lisibles ; voir :func:`load_team_catalog` pour les autres."""
    return list(load_team_catalog(project_root).teams)


def load_team(project_root: Path, name: str) -> Team | None:
    """Résout une équipe par son nom déclaré ou par le nom de son fichier."""
    wanted = name.strip()
    if not wanted:
        return None
    for team in load_teams(project_root):
        if team.name == wanted or (team.path is not None and team.path.stem == wanted):
            return team
    return None
