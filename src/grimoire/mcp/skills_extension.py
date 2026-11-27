"""Extension MCP officielle « Skills » (``io.modelcontextprotocol/skills``, SEP-2640).

Le serveur sert les skills du projet à côté de ses outils : un client qui
connaît l'extension les charge à la demande (dans Claude Code, un skill servi
par le serveur ``grimoire`` s'invoque ``/grimoire:<skill>``), sans rien
installer dans le projet.

Mécanisme : l'interface d'extension du SDK ``mcp`` (>= 2.0, ``MCPServer(extensions=…)``)
annonce la capacité dans ``server/discover`` et enregistre deux méthodes,
``skills/list`` et ``skills/get``. Les fichiers eux-mêmes passent par
``resources/read`` : chaque ``SKILL.md`` est enregistré comme ressource
``skill://<nom>/SKILL.md``, avec exactement les octets que le manifeste décrit.
Spécification : ``specification/beta/skills.mdx`` du dépôt
``modelcontextprotocol/ext-skills``.

Ce qui n'est pas implémenté, et donc pas annoncé : ``resources/directory/read``
(``directoryRead: false``). Un skill du kit est un fichier unique, il n'y a
aucun répertoire à lister.

Démon HTTP partagé (#723) : son ``cwd`` n'est le projet de personne et il n'a
pas de projet résolu à l'enregistrement. La capacité reste annoncée (elle est
fixée à la construction), mais le catalogue reste vide : ``skills/list`` rend
une liste vide, ce que la spécification permet, ``skills/get`` et la lecture
d'une URI ``skill://`` répondent « inconnu ». Les skills restent joignables
par ``grimoire_skill`` et les ressources ``grimoire://skill/{slug}``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.extension import Extension, MethodBinding
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, PaginatedRequestParams, RequestParams

from grimoire.mcp.project import is_shared
from grimoire.mcp.skills_catalogue import (
    ExcludedSkill,
    ServedFile,
    SkillCatalogue,
    build_catalogue,
)

_LOG = logging.getLogger("grimoire.mcp.skills")

EXTENSION_ID = "io.modelcontextprotocol/skills"

#: Entrées par page de ``skills/list`` : un skill n'est jamais coupé entre deux pages.
PAGE_SIZE = 50
#: Fraîcheur annoncée. Le catalogue est un instantané du démarrage ; « private » parce
#: qu'il dépend du projet, donc ne se partage pas entre utilisateurs.
TTL_MS = 300_000
CACHE_SCOPE = "private"
_CURSOR_PREFIX = "o"


class _GetSkillParams(RequestParams):
    uri: str


def _invalid(message: str) -> MCPError:
    return MCPError(code=INVALID_PARAMS, message=message)


def _page_start(cursor: str | None, total: int) -> int:
    """Décode un curseur opaque ; un curseur qui n'est pas le nôtre est ``-32602``."""
    if cursor is None:
        return 0
    digits = cursor[len(_CURSOR_PREFIX) :]
    if not cursor.startswith(_CURSOR_PREFIX) or not digits.isdigit() or int(digits) > total:
        raise _invalid("curseur de pagination invalide")
    return int(digits)


class SkillsExtension(Extension):
    """Sert le catalogue de skills du projet ; vide tant que :meth:`serve` n'a pas été appelé."""

    identifier = EXTENSION_ID

    def __init__(self) -> None:
        self._catalogue = SkillCatalogue()

    @property
    def catalogue(self) -> SkillCatalogue:
        return self._catalogue

    def settings(self) -> dict[str, Any]:
        return {"directoryRead": False}

    def methods(self) -> list[MethodBinding]:
        return [
            MethodBinding("skills/list", PaginatedRequestParams, self._list),
            MethodBinding("skills/get", _GetSkillParams, self._get),
        ]

    def serve(self, server: Any, project_root: Path) -> int:
        """Prendre l'instantané des skills du projet et enregistrer leurs fichiers ; rend leur nombre.

        Appelé au démarrage en stdio (un serveur par session, le projet est le
        ``cwd``). Sans effet dans le démon partagé : pas de projet résolu, donc
        aucun skill servi. Ne lève jamais : un serveur qui ne sait pas lire un
        projet sert quand même ses outils.
        """
        if is_shared():
            return 0
        try:
            catalogue = build_catalogue(project_root, log=True)
        except Exception:  # un projet illisible ne doit pas empêcher le serveur de démarrer
            _LOG.warning("skills non servis par MCP : projet illisible", exc_info=True)
            return 0
        served = []
        excluded = list(catalogue.excluded)
        for skill in catalogue.served:
            try:
                for file in skill.files:
                    _register_file(server, skill.name, skill.description, file)
            except Exception:  # une ressource refusée par le SDK écarte le skill, pas le reste
                _LOG.warning("skill %r non servi par MCP : enregistrement refusé", skill.name, exc_info=True)
                excluded.append(ExcludedSkill(skill.name, "enregistrement de la ressource refusé"))
                continue
            served.append(skill)
        self._catalogue = SkillCatalogue.of(served, excluded)
        return len(served)

    async def _list(self, _ctx: Any, params: PaginatedRequestParams) -> dict[str, Any]:
        skills = self._catalogue.served
        start = _page_start(params.cursor, len(skills))
        page = skills[start : start + PAGE_SIZE]
        result: dict[str, Any] = {
            "resultType": "complete",
            "skills": [s.entry() for s in page],
            "ttlMs": TTL_MS,
            "cacheScope": CACHE_SCOPE,
        }
        if start + PAGE_SIZE < len(skills):
            result["nextCursor"] = f"{_CURSOR_PREFIX}{start + PAGE_SIZE}"
        return result

    async def _get(self, _ctx: Any, params: _GetSkillParams) -> dict[str, Any]:
        skill = self._catalogue.skill_at(params.uri)
        if skill is None:
            raise _invalid(f"Aucun skill n'est servi à {params.uri}")
        return {"resultType": "complete", "skill": skill.entry(), "ttlMs": TTL_MS, "cacheScope": CACHE_SCOPE}


def _register_file(server: Any, name: str, description: str, file: ServedFile) -> None:
    """Exposer un fichier servi comme ressource ``skill://…`` (les octets du manifeste, rien d'autre)."""
    text = file.text

    def handler() -> str:
        return text

    handler.__name__ = f"skill_file_{name.replace('-', '_')}"
    handler.__doc__ = description
    server.resource(file.uri, name=name, description=description, mime_type="text/markdown")(handler)


#: L'instance enregistrée par ``grimoire.mcp.server`` à la construction du serveur.
SKILLS_EXTENSION = SkillsExtension()
