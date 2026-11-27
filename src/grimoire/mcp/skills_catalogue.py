"""Catalogue des skills servis par MCP (extension ``io.modelcontextprotocol/skills``).

Module pur : aucune dépendance au SDK ``mcp``, pour que ``grimoire doctor`` puisse
dire ce qui serait servi sans l'extra optionnel. Il décide *quels* skills sont
servis et *avec quels octets* ; :mod:`grimoire.mcp.skills_extension` s'occupe du
protocole.

Ce que le kit connaît d'un skill (:func:`grimoire.hosts.collect.collect_skills`)
est un fichier ``<slug>.md`` : un corps et quelques champs de frontmatter, pas un
dossier. Le catalogue sert donc un seul fichier par skill, ``SKILL.md``, rendu
comme le font les émetteurs de fichiers (``name`` égal au slug, ``description``,
et ``license`` / ``compatibility`` quand ils sont renseignés). Les champs qui
élargissent des permissions (``tools``, ``allowed-tools``…) ne sont jamais émis.

Garde-fous, tous appliqués ici et non au fil des requêtes :

- un skill dont le nom enfreint la spécification Agent Skills, ou dont le
  ``name`` déclaré diffère du dossier, est exclu avec sa raison, jamais renommé ;
- un fichier source qui est un lien symbolique, ou qui sort de son dossier, est
  exclu : aucun lien n'est suivi ;
- un contenu binaire (octet nul) ou plus gros que les limites est exclu ;
- aucune réponse ne contient de chemin de la machine : seuls figurent des URI
  ``skill://<nom>/SKILL.md``, des digests et des tailles ;
- rien n'est lu au fil des requêtes : le catalogue est un instantané pris au
  démarrage, et ce qui est servi est exactement ce qui est décrit.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

_LOG = logging.getLogger("grimoire.mcp.skills")

SKILL_SCHEME = "skill://"
SKILL_FILE = "SKILL.md"

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
        }


@dataclass(frozen=True, slots=True)
class ExcludedSkill:
    """Un skill non servi, et pourquoi (sans chemin de la machine)."""

    slug: str
    reason: str


@dataclass(frozen=True, slots=True)
class SkillCatalogue:
    """Instantané de ce qui est servi et de ce qui est écarté."""

    served: tuple[ServedSkill, ...] = ()
    excluded: tuple[ExcludedSkill, ...] = ()
    _by_uri: MappingProxyType[str, ServedSkill] = field(default_factory=lambda: MappingProxyType({}), repr=False)

    @classmethod
    def of(cls, served: list[ServedSkill], excluded: list[ExcludedSkill]) -> SkillCatalogue:
        ordered = tuple(sorted(served, key=lambda s: s.name))
        return cls(
            served=ordered,
            excluded=tuple(sorted(excluded, key=lambda e: e.slug)),
            _by_uri=MappingProxyType({s.uri: s for s in ordered}),
        )

    def skill_at(self, uri: str) -> ServedSkill | None:
        """Le skill dont ``SKILL.md`` a cette URI, ou ``None`` (aucune autre forme n'est reconnue)."""
        return self._by_uri.get(uri)


def _source_issue(slug: str, project_root: Path, bundled: Path) -> str | None:
    """Pourquoi le fichier source de *slug* ne doit pas être lu, ou ``None``.

    Même ordre de recherche que ``collect_skills`` (dossiers du projet, puis
    skills livrés avec le kit). Le premier fichier trouvé fait foi : s'il est
    refusé, on n'en cherche pas un autre, ce serait servir autre chose que ce
    que les autres hôtes reçoivent.
    """
    from grimoire.core import layout

    root = project_root.resolve()
    for directory in (*layout.skill_dirs(project_root), bundled):
        candidate = directory / f"{slug}.md"
        if candidate.is_symlink():
            return "fichier source en lien symbolique (non suivi)"
        if not candidate.exists():
            continue
        base = directory.resolve()
        is_bundled = directory == bundled
        if not is_bundled and not base.is_relative_to(root):
            return "dossier des skills hors du projet"
        if not candidate.resolve().is_relative_to(base):
            return "fichier source hors de son dossier"
        return None
    return "fichier source introuvable"


def _render(spec: Any) -> tuple[str, dict[str, Any]] | str:
    """Le texte de ``SKILL.md`` et son frontmatter relu, ou la raison de l'exclusion."""
    from grimoire.hosts.collect import parse_frontmatter
    from grimoire.hosts.emitters.base import Emitter
    from grimoire.hosts.skill_spec import validate_skill_text

    fields: dict[str, Any] = {"name": spec.slug, "description": spec.description}
    if spec.license:
        fields["license"] = spec.license
    if spec.compatibility:
        fields["compatibility"] = spec.compatibility
    text = f"{Emitter.frontmatter(fields)}\n\n{spec.body}"

    for violation in validate_skill_text(text, slug=spec.slug):
        if violation.rule.startswith(_RULES_BLOCKING):
            return f"hors spécification Agent Skills : {violation}"
    # Le frontmatter publié est celui que l'hôte relira dans les octets servis,
    # pas celui qu'on croit avoir écrit : l'identité exigée par la spécification
    # tient par construction.
    meta, _body = parse_frontmatter(text)
    if str(meta.get("name")) != spec.slug or not str(meta.get("description", "")).strip():
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


def build_catalogue(project_root: Path, *, log: bool = False) -> SkillCatalogue:
    """Lit les skills du projet et rend ce qui est servi, ce qui est écarté.

    Ne lève jamais pour un skill : un fichier illisible est une exclusion. Les
    erreurs de collecte globales (projet absent…) remontent à l'appelant, qui
    décide (le serveur sert alors un catalogue vide, ``doctor`` le dit).
    """
    from grimoire.data import framework_path
    from grimoire.hosts.collect import collect_skills

    bundled = framework_path() / "hosts" / "skills"
    served: list[ServedSkill] = []
    excluded: list[ExcludedSkill] = []
    for spec in collect_skills(project_root):
        reason = _exclusion(spec, project_root, bundled)
        if isinstance(reason, str):
            excluded.append(ExcludedSkill(spec.slug, reason))
            if log:
                _LOG.warning("skill %r non servi par MCP : %s", spec.slug, reason)
            continue
        served.append(reason)
    return SkillCatalogue.of(served, excluded)


def _exclusion(spec: Any, project_root: Path, bundled: Path) -> str | ServedSkill:
    """Un skill prêt à servir, ou la raison de son exclusion."""
    if spec.name != spec.slug:
        return f"name « {spec.name} » différent du dossier « {spec.slug} » (jamais renommé en silence)"
    issue = _source_issue(spec.slug, project_root, bundled)
    if issue is not None:
        return issue
    rendered = _render(spec)
    if isinstance(rendered, str):
        return rendered
    text, meta = rendered
    data = text.encode("utf-8")
    issue = _limit_issue(data)
    if issue is not None:
        return issue
    served_file = ServedFile(skill_uri(spec.slug), text, data, digest_of(data))
    return ServedSkill(spec.slug, spec.description, meta, (served_file,))
