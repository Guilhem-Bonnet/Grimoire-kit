"""Catalogue des skills servis par le serveur MCP : l'unique source de vérité.

Module pur : aucune dépendance au SDK ``mcp``, pour que ``grimoire doctor`` puisse
dire ce qui serait servi sans l'extra optionnel. Il décide *quels* skills sont
servis et *avec quels octets*. Trois chemins s'y adossent et ne lisent jamais un
skill par eux-mêmes : les fichiers ``skill://<nom>/SKILL.md`` de l'extension
officielle ``io.modelcontextprotocol/skills`` (:mod:`grimoire.mcp.skills_extension`),
les ressources ``grimoire://skill/{slug}`` et l'outil ``grimoire_skill``
(:mod:`grimoire.mcp.skills_serving`).

Le serveur ne sert que des fichiers réguliers du projet, sans suivre de lien,
dans des limites de taille, quel que soit le schéma d'URI :

- chaque fichier source est examiné par ``lstat`` d'abord (fichier régulier,
  pas un lien), ouvert sans suivre de lien et lu avec une borne : un lien, un
  tube, un périphérique, un dossier ou un fichier trop gros devient l'exclusion
  motivée *de ce skill*, jamais une exception ni la perte des autres ;
- les dossiers de skills du projet ne passent par aucun lien symbolique ;
- le nombre de fichiers examinés par dossier est borné ;
- un skill du projet qui porte le nom d'un skill livré par le kit est exclu
  (``masque un skill du kit``) et c'est la version du kit qui est servie ;
- un skill dont le nom enfreint la spécification Agent Skills, ou dont le
  ``name`` déclaré diffère du dossier, est exclu avec sa raison, jamais renommé ;
- aucun texte venu d'un fichier n'est relayé tel quel dans une raison : borné,
  sans caractère de contrôle, crochets neutralisés ;
- aucune réponse ne contient de chemin de la machine ;
- rien n'est lu au fil des requêtes du protocole : le catalogue est un instantané.

Un skill du kit est un fichier ``<slug>.md`` : un corps et quelques champs de
frontmatter. Le catalogue sert donc un seul fichier par skill, ``SKILL.md``, rendu
comme le font les émetteurs de fichiers (``name`` égal au slug, ``description``,
et ``license`` / ``compatibility`` quand ils sont renseignés). Les champs qui
élargissent des permissions (``tools``, ``allowed-tools``…) ne sont jamais émis.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

# La lecture sûre (fichier régulier, sans lien, bornée, UTF-8 strict) et l'assainissement des textes sont
# partagés avec les collecteurs de ``hosts.collect`` : un seul code, jamais copié une troisième fois.
from grimoire.core.safe_sources import (
    MAX_DIR_ENTRIES as MAX_DIR_ENTRIES,
)
from grimoire.core.safe_sources import (
    MAX_EXAMINED as MAX_EXAMINED,
)
from grimoire.core.safe_sources import (
    NAME_DISPLAY_MAX as NAME_DISPLAY_MAX,
)
from grimoire.core.safe_sources import (
    REASON_DISPLAY_MAX as REASON_DISPLAY_MAX,
)
from grimoire.core.safe_sources import (
    Refused,
    list_sources,
    read_source_text,
    source_dir_issue,
)
from grimoire.core.safe_sources import (
    safe_text as safe_text,
)

_LOG = logging.getLogger("grimoire.mcp.skills")

SKILL_SCHEME = "skill://"
SKILL_FILE = "SKILL.md"
#: Clé ``_meta`` de l'origine d'un skill (``kit`` ou ``project``), sous un préfixe propre au kit.
ORIGIN_KEY = "io.grimoire/origin"
ORIGIN_KIT = "kit"
ORIGIN_PROJECT = "project"

#: Limite de la spécification (16 Mio par skill ; 512 fichiers, hors d'atteinte
#: puisqu'un skill du kit est un seul fichier).
SPEC_MAX_BYTES = 16 * 1024 * 1024
#: Limites de Claude Code (par fichier texte, par skill) : au-delà, l'hôte tronque ou refuse.
HOST_MAX_FILE_BYTES = 100_000
HOST_MAX_SKILL_BYTES = 400_000
_RULES_BLOCKING = ("name.", "description.", "compatibility.")


def skill_uri(name: str, path: str = SKILL_FILE) -> str:
    """``skill://<nom>/<chemin>`` : le nom est le dernier (et seul) segment du chemin du skill."""
    return f"{SKILL_SCHEME}{name}/{path}"


def digest_of(data: bytes) -> str:
    """``sha256:<64 hex minuscules>`` des octets bruts, comme l'exige la spécification."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class ServedFile:
    """Un fichier servi : ``text`` est ce que ``resources/read`` renvoie, ``data`` ses octets."""

    uri: str
    text: str
    data: bytes
    digest: str

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class ServedSkill:
    """Un skill servi : entrée ``frontmatter`` + manifeste complet de ses fichiers."""

    name: str
    description: str
    body: str
    origin: str
    frontmatter: dict[str, Any]
    files: tuple[ServedFile, ...]

    @property
    def uri(self) -> str:
        return skill_uri(self.name)

    def entry(self) -> dict[str, Any]:
        """L'objet ``Skill`` de la spécification (``skills/list`` et ``skills/get``)."""
        return {
            "uri": self.uri,
            "frontmatter": dict(self.frontmatter),
            "resources": [{"uri": f.uri, "digest": f.digest, "size": f.size} for f in self.files],
            "_meta": {ORIGIN_KEY: self.origin},
        }


@dataclass(frozen=True, slots=True)
class ExcludedSkill:
    """Un skill non servi, et pourquoi (texte assaini, sans chemin de la machine)."""

    slug: str
    reason: str


def _excluded(slug: str, reason: str) -> ExcludedSkill:
    return ExcludedSkill(safe_text(slug), safe_text(reason, REASON_DISPLAY_MAX))


@dataclass(frozen=True, slots=True)
class SkillCatalogue:
    """Instantané de ce qui est servi et de ce qui est écarté."""

    served: tuple[ServedSkill, ...] = ()
    excluded: tuple[ExcludedSkill, ...] = ()
    _by_uri: MappingProxyType[str, ServedSkill] = field(default_factory=lambda: MappingProxyType({}), repr=False)
    _by_name: MappingProxyType[str, ServedSkill] = field(default_factory=lambda: MappingProxyType({}), repr=False)

    @classmethod
    def of(cls, served: list[ServedSkill], excluded: list[ExcludedSkill]) -> SkillCatalogue:
        ordered = tuple(sorted(served, key=lambda s: s.name))
        return cls(
            served=ordered,
            excluded=tuple(sorted(excluded, key=lambda e: (e.slug, e.reason))),
            _by_uri=MappingProxyType({s.uri: s for s in ordered}),
            _by_name=MappingProxyType({s.name: s for s in ordered}),
        )

    def skill_at(self, uri: str) -> ServedSkill | None:
        """Le skill dont ``SKILL.md`` a cette URI, ou ``None`` (aucune autre forme n'est reconnue)."""
        return self._by_uri.get(uri)

    def skill_named(self, name: str) -> ServedSkill | None:
        return self._by_name.get(name)

    def reason_for(self, name: str) -> str | None:
        """La raison pour laquelle *name* n'est pas servi, si c'est un skill écarté."""
        wanted = safe_text(name)
        return next((e.reason for e in self.excluded if e.slug == wanted), None)


# ── Lecture bornée d'un fichier source ──────────────────────────────────────────


_Refused = Refused  # un refus motivé : la raison est rédigée par le module partagé, jamais reprise d'un fichier


def _read_source(path: Path) -> str:
    """Le texte UTF-8 du fichier régulier *path* (lecture sûre partagée, borne de l'hôte) ; :class:`Refused` sinon."""
    return read_source_text(path, max_bytes=HOST_MAX_FILE_BYTES)


def _project_dir_issue(directory: Path, project_root: Path) -> str | None:
    """Raison de ne pas lire un dossier de skills du projet : aucun lien ne doit s'y trouver en chemin."""
    return source_dir_issue(directory, project_root, what="skills")


def _names(directory: Path, label: str, excluded: list[ExcludedSkill]) -> dict[str, Path]:
    """``slug -> chemin`` des ``*.md`` d'un dossier, au plus :data:`MAX_EXAMINED`, sans rien lire."""
    found, issues = list_sources(directory, label)
    excluded.extend(ExcludedSkill(i.name, i.reason) for i in issues)
    return found


# ── Rendu d'un skill ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Source:
    slug: str
    origin: str
    name: str
    description: str
    body: str
    license: str
    compatibility: str


def _parse(slug: str, origin: str, text: str) -> _Source:
    from grimoire.hosts.collect import parse_frontmatter

    meta, body = parse_frontmatter(text)
    return _Source(
        slug=slug,
        origin=origin,
        name=str(meta.get("name") or slug),
        description=str(meta.get("description") or slug),
        body=body.strip() + "\n",
        license=str(meta.get("license") or ""),
        compatibility=str(meta.get("compatibility") or ""),
    )


def _render(src: _Source) -> tuple[str, dict[str, Any]] | str:
    """Le texte de ``SKILL.md`` et son frontmatter relu, ou la raison de l'exclusion."""
    from grimoire.hosts.collect import parse_frontmatter
    from grimoire.hosts.emitters.base import Emitter
    from grimoire.hosts.skill_spec import validate_skill_text

    fields: dict[str, Any] = {"name": src.slug, "description": src.description}
    if src.license:
        fields["license"] = src.license
    if src.compatibility:
        fields["compatibility"] = src.compatibility
    text = f"{Emitter.frontmatter(fields)}\n\n{src.body}"

    for violation in validate_skill_text(text, slug=src.slug):
        if violation.rule.startswith(_RULES_BLOCKING):
            return f"hors spécification Agent Skills : {safe_text(violation, REASON_DISPLAY_MAX)}"
    # Le frontmatter publié est celui que l'hôte relira dans les octets servis,
    # pas celui qu'on croit avoir écrit : l'identité exigée par la spécification
    # tient par construction.
    meta, _body = parse_frontmatter(text)
    if str(meta.get("name")) != src.slug or not str(meta.get("description", "")).strip():
        return "frontmatter illisible une fois rendu"
    return text, dict(meta)


def _limit_issue(data: bytes) -> str | None:
    if b"\x00" in data:
        return "contenu binaire (octet nul)"
    if len(data) > HOST_MAX_FILE_BYTES:
        return f"SKILL.md de {len(data)} octets, au-delà de {HOST_MAX_FILE_BYTES}"
    if len(data) > HOST_MAX_SKILL_BYTES or len(data) > SPEC_MAX_BYTES:
        return f"skill de {len(data)} octets, au-delà de {HOST_MAX_SKILL_BYTES}"
    return None


def _serve_one(slug: str, path: Path, origin: str) -> ServedSkill | str:
    """Un skill prêt à servir, ou la raison de son exclusion. Ne lève pas."""
    try:
        src = _parse(slug, origin, _read_source(path))
        if src.name != src.slug:
            return (
                f"name « {safe_text(src.name)} » différent du dossier « {safe_text(slug)} » "
                "(jamais renommé en silence)"
            )
        rendered = _render(src)
        if isinstance(rendered, str):
            return rendered
        text, meta = rendered
        data = text.encode("utf-8")
        issue = _limit_issue(data)
        if issue is not None:
            return issue
    except _Refused as exc:  # raison rédigée par nous : seule à être reprise telle quelle
        return str(exc)
    except Exception as exc:  # une erreur sur UN fichier est l'exclusion de CE skill
        return f"fichier illisible ({exc.__class__.__name__})"
    served_file = ServedFile(skill_uri(slug), text, data, digest_of(data))
    return ServedSkill(slug, str(meta.get("description", slug)), src.body, origin, meta, (served_file,))


def build_catalogue(project_root: Path, *, log: bool = False) -> SkillCatalogue:
    """Lit les skills du kit et du projet ; rend ce qui est servi et ce qui est écarté.

    Aucune erreur *sur un skill* ne remonte : un fichier illisible, non UTF-8, trop
    gros, en lien, un tube, un périphérique ou un dossier est une exclusion motivée de
    ce seul skill, et les autres restent servis. Seules les erreurs globales (le
    dossier des skills du kit introuvable, par exemple) remontent à l'appelant, qui
    décide : le serveur sert alors un catalogue vide, ``doctor`` le dit.
    """
    from grimoire.core import layout
    from grimoire.core.standard_state import is_standard_enrolled
    from grimoire.data import framework_path

    kit_dir = framework_path() / "hosts" / "skills"
    try:
        enrolled = is_standard_enrolled(project_root)
    except Exception:  # une sonde de gouvernance illisible ne retire pas les autres skills
        enrolled = False
    served: list[ServedSkill] = []
    excluded: list[ExcludedSkill] = []

    kit = _names(kit_dir, "kit/skills", excluded)
    project: dict[str, Path] = {}
    for directory in layout.skill_dirs(project_root):
        label = f"{directory.parent.name}/{directory.name}"
        issue = _project_dir_issue(directory, project_root)
        if issue is not None:
            excluded.append(_excluded(label, issue))
            continue
        for slug, path in _names(directory, label, excluded).items():
            project.setdefault(slug, path)

    for slug in sorted(kit.keys() | project.keys()):
        if slug == "grimoire-evidence" and not enrolled:
            continue  # un protocole pour des gates que le projet n'a pas n'est que du bruit
        if slug in kit and slug in project:
            excluded.append(_excluded(slug, "masque un skill du kit : renomme-le"))
        origin, path = (ORIGIN_KIT, kit[slug]) if slug in kit else (ORIGIN_PROJECT, project[slug])
        result = _serve_one(slug, path, origin)
        if isinstance(result, str):
            excluded.append(_excluded(slug, result))
        else:
            served.append(result)
    if log:
        for item in excluded:
            _LOG.warning("skill %r non servi par MCP : %s", item.slug, item.reason)
    return SkillCatalogue.of(served, excluded)
