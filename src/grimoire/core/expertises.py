"""Catalogue d'expertises optionnelles — langages, ingénierie, cloud (issue #616).

Le kit livre par défaut un agent généraliste par archétype (``stack-engineer``,
``ops-engineer``…) plus les skills qu'il attache d'emblée (issue #375). Ce
module ajoute une troisième couche, à la demande : un catalogue déclaratif
(``registry/expertises.yaml``) de compétences supplémentaires — un langage bas
niveau, un patron d'architecture, un fournisseur cloud — que l'utilisateur
choisit ou que la détection recommande, jamais attachées d'office (la
doctrine des artefacts, ``docs/artifact-doctrine.md``, chiffre le coût d'un
skill non attaché : transversal, il se paie à chaque tour de la session).

Trois opérations :

``load_catalog``
    Parse ``registry/expertises.yaml`` en :class:`Expertise` immuables.

``detect_expertises``
    Réutilise :class:`grimoire.core.scanner.StackScanner` pour les langages
    déjà couverts par la détection de pile, ajoute une détection de
    fournisseur Terraform (``provider "aws" { ... }``) pour la famille cloud,
    et rend une :class:`Recommendation` par entrée dont un signal est présent.
    Une entrée sans ``detect`` (les compétences d'ingénierie transversales)
    n'est jamais recommandée — voir le commentaire du registre.

``attach_expertise`` / ``detach_expertise``
    Écrivent (ou nettoient) l'override projet qui rend une expertise
    effective : le corps du skill sous
    ``_grimoire/overrides/skills/<slug>.md``, et le slug ajouté (ou retiré) du
    champ ``skills:`` de l'override partiel de l'agent porteur
    (``_grimoire/overrides/agents/<porteur>.md``, ``extends: kit`` — le même
    mécanisme que :mod:`grimoire.core.override_drift`). Jamais dans
    ``_grimoire/kit/`` : ce tier est regénéré en entier par ``grimoire up``
    et perdrait l'attachement à la prochaine mise à jour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core import layout
from grimoire.core.agentic_standard import _ensure_inside_root
from grimoire.core.exceptions import GrimoireAgentError, GrimoireRegistryError
from grimoire.core.scanner import StackScanner

__all__ = [
    "AttachResult",
    "DetachResult",
    "Expertise",
    "Recommendation",
    "attach_expertise",
    "bundled_expertise_catalog_path",
    "detach_expertise",
    "detect_expertises",
    "load_catalog",
]

_CATALOG_RELPATH = Path("registry") / "expertises.yaml"

#: Familles reconnues, dans l'ordre d'affichage — un id hors de cette liste
#: dans le registre est une erreur de configuration, pas une famille "libre".
FAMILIES: tuple[str, ...] = ("languages", "engineering", "cloud")


def bundled_expertise_catalog_path() -> Path:
    """Chemin du ``registry/expertises.yaml`` embarqué (wheel ou dépôt dev).

    Même résolution double que :func:`grimoire.tools.project_upgrade.
    bundled_blueprint_path` : un wheel installé le force-include sous
    ``grimoire/data/registry/expertises.yaml`` ; une installation éditable
    (ou ce dépôt lui-même) le lit directement à la racine.
    """
    from importlib.resources import files

    try:
        pkg = Path(str(files("grimoire"))) / "data" / _CATALOG_RELPATH
        if pkg.is_file():
            return pkg
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - environnement dégradé
        pass
    repo_root = Path(__file__).resolve().parents[3]
    dev = repo_root / _CATALOG_RELPATH
    if dev.is_file():
        return dev
    raise GrimoireRegistryError(
        f"catalogue d'expertises introuvable (ni grimoire/data/{_CATALOG_RELPATH.as_posix()}, "
        f"ni {_CATALOG_RELPATH.as_posix()} du dépôt) — installation grimoire-kit incomplète"
    )


def _resolve_kit_relative(relpath: str) -> Path:
    """Résout un chemin ``skill:`` du registre (relatif à la racine kit).

    ``archetypes/...`` se résout via :func:`grimoire.archetypes.bundled_path`,
    ``framework/...`` via :func:`grimoire.data.framework_path` — les deux
    résolveurs qui gèrent déjà dev vs wheel pour ces arbres. Toute autre
    racine est une erreur de registre : un skill ne peut vivre ailleurs, ce
    sont les deux seuls arbres que le kit embarque.
    """
    rel = Path(relpath)
    parts = rel.parts
    if not parts:
        raise GrimoireRegistryError(f"chemin de skill vide dans le registre : {relpath!r}")
    if parts[0] == "archetypes":
        from grimoire.archetypes import bundled_path

        return bundled_path().joinpath(*parts[1:])
    if parts[0] == "framework":
        from grimoire.data import framework_path

        return framework_path().joinpath(*parts[1:])
    raise GrimoireRegistryError(
        f"chemin de skill non supporté dans le registre : {relpath!r} "
        "(doit commencer par 'archetypes/' ou 'framework/')"
    )


# ── Modèle ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Expertise:
    """Une entrée du catalogue — pas encore attachée à aucun projet."""

    id: str
    name: str
    summary: str
    family: str
    skill_relpath: str
    """Chemin relatif à la racine kit (``archetypes/...`` ou ``framework/...``)."""
    porteur: str
    porteur_fallback: str | None = None
    detect_stack_scanner: str | None = None
    detect_files: tuple[str, ...] = ()
    detect_terraform_providers: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        """Identifiant du skill une fois attaché — le nom de fichier sans extension."""
        return Path(self.skill_relpath).stem

    def resolve_skill_path(self) -> Path:
        """Chemin absolu du fichier de skill source (embarqué dans le kit)."""
        return _resolve_kit_relative(self.skill_relpath)

    def resolve_porteur(self, project_root: Path) -> str:
        """Agent porteur effectif pour *project_root* : ``porteur`` s'il est
        installé, sinon ``porteur_fallback`` s'il l'est, sinon un refus
        nommé — jamais un attachement silencieux à un agent absent."""
        installed = layout.installed_agents(project_root)
        if self.porteur in installed:
            return self.porteur
        if self.porteur_fallback and self.porteur_fallback in installed:
            return self.porteur_fallback
        candidates = f"« {self.porteur} »" + (f" ou « {self.porteur_fallback} »" if self.porteur_fallback else "")
        raise GrimoireAgentError(
            f"expertise « {self.id} » : aucun agent porteur {candidates} n'est installé dans ce projet "
            "— installez l'archétype correspondant (stack ou infra-ops) avant d'attacher cette expertise."
        )


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Ce que :func:`detect_expertises` rend pour une entrée détectée."""

    id: str
    name: str
    family: str
    reason: str
    confidence: float


def _parse_entry(raw: dict[str, Any], family: str) -> Expertise:
    detect = raw.get("detect") or {}
    files_raw = detect.get("files") or ()
    providers_raw = detect.get("terraform_providers") or ()
    return Expertise(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        summary=str(raw.get("summary", "")),
        family=family,
        skill_relpath=str(raw["skill"]),
        porteur=str(raw.get("porteur", "stack-engineer")),
        porteur_fallback=(str(raw["porteur_fallback"]) if raw.get("porteur_fallback") else None),
        detect_stack_scanner=(str(detect["stack_scanner"]) if detect.get("stack_scanner") else None),
        detect_files=tuple(str(f) for f in files_raw),
        detect_terraform_providers=tuple(str(p) for p in providers_raw),
    )


def load_catalog(catalog_path: Path | None = None) -> tuple[Expertise, ...]:
    """Parse le registre en :class:`Expertise`, ordre du fichier conservé.

    Lève :class:`GrimoireRegistryError` sur un id dupliqué ou une famille
    inconnue — un catalogue mal formé est un défaut de release, pas une
    tolérance silencieuse à absorber au premier import.
    """
    from grimoire.tools._common import load_yaml

    path = catalog_path or bundled_expertise_catalog_path()
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise GrimoireRegistryError(f"registre d'expertises invalide (pas un mapping) : {path}")

    entries: list[Expertise] = []
    seen: set[str] = set()
    for family in data:
        if family in {"$schema", "version"}:
            continue
        if family not in FAMILIES:
            raise GrimoireRegistryError(f"famille inconnue dans le registre d'expertises : {family!r}")
        for raw in data[family] or []:
            entry = _parse_entry(raw, family)
            if entry.id in seen:
                raise GrimoireRegistryError(f"id d'expertise dupliqué dans le registre : {entry.id!r}")
            seen.add(entry.id)
            entries.append(entry)
    return tuple(entries)


# ── Détection ─────────────────────────────────────────────────────────────────

_TF_PROVIDER_RE = re.compile(r'provider\s+"([\w-]+)"\s*\{')


def _terraform_providers(project_root: Path) -> set[str]:
    """Noms des fournisseurs déclarés dans les blocs ``provider "..." {}``
    des fichiers ``*.tf`` à la racine du projet (pas de parcours récursif :
    même portée que les marqueurs de :class:`StackScanner`)."""
    providers: set[str] = set()
    try:
        tf_files = list(project_root.glob("*.tf"))
    except OSError:  # pragma: no cover - racine illisible
        return providers
    for tf_file in tf_files:
        try:
            text = tf_file.read_text(encoding="utf-8")
        except OSError:
            continue
        providers.update(_TF_PROVIDER_RE.findall(text))
    return providers


def _matches_file_marker(project_root: Path, pattern: str) -> bool:
    if pattern.endswith("/"):
        return (project_root / pattern.rstrip("/")).is_dir()
    if "*" in pattern or "?" in pattern:
        return any(True for _ in project_root.glob(pattern))
    return (project_root / pattern).exists()


def detect_expertises(project_root: Path, *, catalog: tuple[Expertise, ...] | None = None) -> list[Recommendation]:
    """Recommandations pour *project_root*, triées par famille puis id.

    Ne modifie rien : une recommandation est une suggestion, l'attachement
    reste un choix explicite (``grimoire expertise add``), conformément à la
    doctrine des artefacts (un skill ne s'attache jamais tout seul).
    """
    root = project_root.resolve()
    entries = catalog if catalog is not None else load_catalog()
    scan = StackScanner(root).scan()
    scanned = {d.name: d for d in scan.stacks}
    tf_providers = _terraform_providers(root)

    recommendations: list[Recommendation] = []
    for entry in entries:
        reasons: list[str] = []
        confidence = 0.0

        if entry.detect_stack_scanner and entry.detect_stack_scanner in scanned:
            detection = scanned[entry.detect_stack_scanner]
            reasons.append("marqueur(s) détecté(s) : " + ", ".join(detection.evidence))
            confidence = max(confidence, detection.confidence)

        for pattern in entry.detect_files:
            if _matches_file_marker(root, pattern):
                reasons.append(f"fichier détecté : {pattern}")
                confidence = max(confidence, 0.6)

        matched_providers = [p for p in entry.detect_terraform_providers if p in tf_providers]
        if matched_providers:
            reasons.append("provider Terraform détecté : " + ", ".join(matched_providers))
            confidence = max(confidence, 0.9)

        if reasons:
            recommendations.append(
                Recommendation(
                    id=entry.id,
                    name=entry.name,
                    family=entry.family,
                    reason=" ; ".join(reasons),
                    confidence=min(confidence, 1.0),
                )
            )

    recommendations.sort(key=lambda r: (r.family, r.id))
    return recommendations


# ── Attachement (override projet) ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AttachResult:
    expertise_id: str
    slug: str
    porteur: str
    skill_ref: str
    agent_override_ref: str
    already_attached: bool


@dataclass(frozen=True, slots=True)
class DetachResult:
    expertise_id: str
    slug: str
    porteur: str
    skill_removed: bool
    agent_override_ref: str
    was_attached: bool


#: Forme de `forge_server.SLUG_RE`, dupliquée pour ne pas en dépendre — garde
#: de forme sur un slug de skill lu depuis un frontmatter projet, avant toute
#: construction de chemin (CodeQL py/path-injection 599/600, `scaffold.py`,
#: `docs/security/code-scanning-triage-2026-09.md`).
_SKILL_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _safe_skill_source(skills_src: Path, slug: str) -> Path | None:
    """Chemin du skill `slug` sous `skills_src`, ou `None` (forme invalide
    ou hors racine, `_ensure_inside_root` #573)."""
    if not _SKILL_SLUG_RE.match(slug):
        return None
    candidate = skills_src / f"{slug}.md"
    try:
        _ensure_inside_root(skills_src, candidate, label=f"Skill source {slug!r}")
    except ValueError:
        return None
    return candidate


def _skills_override_dir(project_root: Path) -> Path:
    return layout.overrides_dir(project_root) / layout.SKILLS_SUBDIR


def _agents_override_dir(project_root: Path) -> Path:
    return layout.overrides_dir(project_root) / layout.AGENTS_SUBDIR


def _read_agent_skills(path: Path) -> list[str]:
    from grimoire.hosts.collect import parse_frontmatter

    if not path.is_file():
        return []
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    raw = meta.get("skills") or []
    return [str(s) for s in raw]


def _dump_override(meta: dict[str, Any]) -> str:
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(meta, buf)
    return f"---\n{buf.getvalue()}---\n"


def _validate_or_rollback(project_root: Path, restore: dict[Path, str | None]) -> None:
    """Après une écriture, vérifie que la surface se construit — sinon
    remet chaque fichier de *restore* dans son état antérieur (contenu, ou
    absence — auquel cas le fichier créé est supprimé) et propage l'erreur.
    Même discipline que :func:`grimoire.core.override_drift.convert_override`."""
    from grimoire.hosts import collect

    try:
        skills = collect.collect_skills(project_root)
        collect.collect_agents(project_root, known_skills=frozenset(s.slug for s in skills))
    except GrimoireAgentError:
        for path, previous in restore.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(previous, encoding="utf-8")
        raise


def attach_expertise(project_root: Path, expertise: Expertise) -> AttachResult:
    """Rend *expertise* effective pour *project_root* — override projet
    uniquement, jamais dans ``_grimoire/kit/`` (regénéré par ``grimoire up``).

    Idempotent : réattacher une expertise déjà attachée ne duplique rien et
    rend ``already_attached=True``.
    """
    from grimoire.core.override_drift import compute_kit_source_hash

    root = project_root.resolve()
    porteur = expertise.resolve_porteur(root)
    slug = expertise.slug

    skill_dst = _skills_override_dir(root) / f"{slug}.md"
    agent_override_path = _agents_override_dir(root) / f"{porteur}.md"
    kit_agent_path = layout.kit_dir(root) / layout.AGENTS_SUBDIR / f"{porteur}.md"

    skill_previous = skill_dst.read_text(encoding="utf-8") if skill_dst.is_file() else None
    agent_previous = agent_override_path.read_text(encoding="utf-8") if agent_override_path.is_file() else None

    current_skills = (
        _read_agent_skills(agent_override_path) if agent_override_path.is_file() else _read_agent_skills(kit_agent_path)
    )
    already_attached = slug in current_skills and skill_dst.is_file()

    if not already_attached:
        skill_dst.parent.mkdir(parents=True, exist_ok=True)
        skill_dst.write_text(expertise.resolve_skill_path().read_text(encoding="utf-8"), encoding="utf-8")

        new_skills = list(dict.fromkeys([*current_skills, slug]))  # dédoublonne, ordre stable
        meta: dict[str, Any] = {"extends": "kit", "skills": new_skills}
        if agent_override_path.is_file():
            from grimoire.hosts.collect import parse_frontmatter

            existing_meta, _ = parse_frontmatter(agent_previous or "")
            meta = {**existing_meta, "extends": "kit", "skills": new_skills}
        meta["kit_source_hash"] = compute_kit_source_hash(kit_agent_path)

        agent_override_path.parent.mkdir(parents=True, exist_ok=True)
        agent_override_path.write_text(_dump_override(meta), encoding="utf-8")

        _validate_or_rollback(
            root,
            {skill_dst: skill_previous, agent_override_path: agent_previous},
        )

    return AttachResult(
        expertise_id=expertise.id,
        slug=slug,
        porteur=porteur,
        skill_ref=skill_dst.relative_to(root).as_posix(),
        agent_override_ref=agent_override_path.relative_to(root).as_posix(),
        already_attached=already_attached,
    )


def detach_expertise(project_root: Path, expertise: Expertise) -> DetachResult:
    """Retire *expertise* de *project_root* : le slug quitte le ``skills:``
    de l'override porteur, et le fichier de skill est supprimé — le garder
    sans qu'aucun agent le référence le rendrait transversal (chargé à
    chaque tour de la session, voir ``grimoire.hosts.emitters.claude_code``),
    ce qui serait pire que l'état avant attachement."""
    from grimoire.hosts.collect import parse_frontmatter

    root = project_root.resolve()
    porteur = expertise.resolve_porteur(root)
    slug = expertise.slug

    skill_dst = _skills_override_dir(root) / f"{slug}.md"
    agent_override_path = _agents_override_dir(root) / f"{porteur}.md"

    if not agent_override_path.is_file():
        # L'override d'agent a pu disparaître autrement qu'en passant par ici
        # (retiré à la main, `override drift` reconverti…) sans emporter le
        # fichier de skill : orphelin, plus aucun agent ne le référence par
        # son slug, exactement le skill transversal fantôme que #375 a
        # corrigé si on le laisse sur disque. Le supprimer ne dépend donc pas
        # de la présence de l'override porteur.
        skill_removed = False
        if skill_dst.is_file():
            skill_dst.unlink()
            skill_removed = True
        return DetachResult(
            expertise_id=expertise.id,
            slug=slug,
            porteur=porteur,
            skill_removed=skill_removed,
            agent_override_ref=agent_override_path.relative_to(root).as_posix(),
            was_attached=False,
        )

    agent_previous = agent_override_path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(agent_previous)
    current_skills = [str(s) for s in (meta.get("skills") or [])]
    was_attached = slug in current_skills

    skill_previous = skill_dst.read_text(encoding="utf-8") if skill_dst.is_file() else None
    skill_removed = False

    if was_attached:
        new_meta = {**meta, "skills": [s for s in current_skills if s != slug]}
        agent_override_path.write_text(_dump_override(new_meta), encoding="utf-8")
        if skill_dst.is_file():
            skill_dst.unlink()
            skill_removed = True
        _validate_or_rollback(root, {agent_override_path: agent_previous, skill_dst: skill_previous})

    return DetachResult(
        expertise_id=expertise.id,
        slug=slug,
        porteur=porteur,
        skill_removed=skill_removed,
        agent_override_ref=agent_override_path.relative_to(root).as_posix(),
        was_attached=was_attached,
    )
