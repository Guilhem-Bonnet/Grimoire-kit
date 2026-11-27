"""Servir les skills du projet par MCP : ressources et outil, tous adossés au même catalogue.

Trois chemins, une seule source de vérité (:func:`grimoire.mcp.skills_catalogue.build_catalogue`) :

- ``skill://<nom>/SKILL.md``, ressource lisible de l'extension officielle (listée), dont
  :mod:`grimoire.mcp.skills_extension` publie le manifeste ;
- ``grimoire://skill/{slug}``, l'ancienne forme, conservée pour les clients qui ne connaissent
  pas l'extension : un modèle de ressource, donc lisible mais absent de ``resources/list``
  (le skill n'y figure qu'une fois, sous sa forme ``skill://``) ;
- l'outil ``grimoire_skill``, pour les hôtes sans skills natifs.

Aucun des trois ne relit un skill sur disque : les deux ressources servent l'instantané pris au
démarrage, l'outil construit le catalogue de la requête avec les mêmes gardes (lien, taille, nom).
Démon HTTP partagé (#723) : pas de projet résolu à l'enregistrement, donc aucune ressource ;
l'outil, lui, reçoit le projet de la requête.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grimoire.mcp.project import is_shared
from grimoire.mcp.skills_catalogue import (
    ExcludedSkill,
    ServedFile,
    ServedSkill,
    SkillCatalogue,
    build_catalogue,
    safe_text,
)

_LOG = logging.getLogger("grimoire.mcp.skills")

LEGACY_URI = "grimoire://skill/{slug}"
_HOLDER_ATTR = "_grimoire_skills_holder"


class _Holder:
    """L'instantané lu par le modèle de ressource ``grimoire://skill/{slug}`` d'un serveur."""

    def __init__(self, catalogue: SkillCatalogue) -> None:
        self.catalogue = catalogue


def _not_found(message: str) -> Exception:
    try:
        from mcp.server.mcpserver.exceptions import ResourceNotFoundError
    except ImportError:  # repli mcp 1.x
        return ValueError(message)
    return ResourceNotFoundError(message)


def _register_file(server: Any, skill: ServedSkill, file: ServedFile) -> None:
    """Exposer un fichier servi comme ressource ``skill://…`` (les octets du manifeste, rien d'autre)."""
    text = file.text

    def handler() -> str:
        return text

    handler.__name__ = f"skill_file_{skill.name.replace('-', '_')}"
    handler.__doc__ = skill.description
    server.resource(file.uri, name=skill.name, description=skill.description, mime_type="text/markdown")(handler)


def _register_legacy(server: Any, catalogue: SkillCatalogue) -> None:
    """Le modèle ``grimoire://skill/{slug}`` : enregistré une fois par serveur, lit l'instantané courant."""
    holder = getattr(server, _HOLDER_ATTR, None)
    if holder is not None:
        holder.catalogue = catalogue
        return
    holder = _Holder(catalogue)

    def handler(slug: str) -> str:
        skill = holder.catalogue.skill_named(slug)
        if skill is None:
            raise _not_found(f"Aucun skill n'est servi sous le nom {safe_text(slug, 200)}")
        return skill.body

    handler.__name__ = "skill_by_slug"
    handler.__doc__ = "Corps d'un skill Grimoire servi par ce serveur."
    server.resource(LEGACY_URI, name="skill", description="Corps d'un skill Grimoire, par son nom")(handler)
    setattr(server, _HOLDER_ATTR, holder)


def serve(server: Any, project_root: Path, extension: Any = None) -> int:
    """Prendre l'instantané des skills du projet, enregistrer ses ressources ; rend le nombre servi.

    Appelé au démarrage en stdio (un serveur par session : le projet est le ``cwd``). Sans effet
    dans le démon partagé : pas de projet résolu, donc aucun skill servi. Ne lève jamais : un
    serveur qui ne sait pas lire un projet sert quand même ses outils. *extension* reçoit le
    catalogue (``use``) pour répondre à ``skills/list`` et ``skills/get``.
    """
    if is_shared():
        return 0
    try:
        catalogue = build_catalogue(project_root, log=True)
    except Exception:  # un projet illisible ne doit pas empêcher le serveur de démarrer
        _LOG.warning("skills non servis par MCP : projet illisible", exc_info=True)
        return 0
    served: list[ServedSkill] = []
    excluded = list(catalogue.excluded)
    for skill in catalogue.served:
        try:
            for file in skill.files:
                _register_file(server, skill, file)
        except Exception:  # une ressource refusée par le SDK écarte le skill, pas le reste
            _LOG.warning("skill %r non servi par MCP : enregistrement refusé", skill.name, exc_info=True)
            excluded.append(ExcludedSkill(skill.name, "enregistrement de la ressource refusé"))
            continue
        served.append(skill)
    final = SkillCatalogue.of(served, excluded)
    try:
        _register_legacy(server, final)
    except Exception:  # l'ancienne forme est un confort de compatibilité
        _LOG.warning("ressources grimoire://skill/ non servies", exc_info=True)
    if extension is not None:
        extension.use(final)
    return len(served)


def skill_tool_result(slug: str, project_root: Path, error: Callable[[dict[str, Any]], str]) -> str:
    """La réponse de l'outil ``grimoire_skill`` : même catalogue, mêmes gardes que les ressources."""
    try:
        catalogue = build_catalogue(project_root)
    except Exception as exc:  # une erreur globale de lecture est une réponse d'outil, pas un plantage
        return error({"error": f"Skills unreadable ({exc.__class__.__name__})"})
    skill = catalogue.skill_named(slug)
    if skill is not None:
        return json.dumps(
            {"slug": skill.name, "description": skill.description, "body": skill.body}, indent=2, ensure_ascii=False
        )
    payload: dict[str, Any] = {"error": f"Unknown skill: {safe_text(slug, 200)}", "available": [s.name for s in catalogue.served]}
    reason = catalogue.reason_for(slug)
    if reason is not None:
        payload["excluded"] = reason
    return error(payload)
