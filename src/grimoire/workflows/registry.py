"""Découverte et métadonnées des workflows Grimoire.

Le catalogue ne listait qu'un seul des deux répertoires où vivent les
workflows. `.github/prompts/` — sept fichiers d'hygiène — était indexé et
proposé ; `_grimoire/workflows/`, où le scaffold dépose les workflows
d'orchestration multi-agents, ne l'était par personne. Ils étaient installés
dans chaque projet et invocables depuis aucune surface.

Ce module indexe les deux, lit ce que chaque workflow déclare de lui-même
(nature, agents mobilisés, équipe, patterns du catalogue, couches mémoire
touchées) et laisse la CLI ne faire que du rendu.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core import layout
from grimoire.data import framework_path
from grimoire.hosts.collect import parse_frontmatter

#: Un workflow qui double une commande CLI : diagnostic, hygiène, rapport.
KIND_COMMAND = "command"
#: Un workflow qui coordonne plusieurs agents sur plusieurs tours.
KIND_ORCHESTRATION = "orchestration"

#: Provenance, de la plus prioritaire à la moins.
SOURCE_PROJECT = "project"
SOURCE_INSTALLED = "installed"
SOURCE_FRAMEWORK = "framework"

_SOURCE_ORDER = (SOURCE_PROJECT, SOURCE_INSTALLED, SOURCE_FRAMEWORK)

#: Descriptions de repli pour les prompts d'hygiène livrés sans frontmatter.
#: Conservé pour les projets installés avant que les workflows se décrivent
#: eux-mêmes ; un fichier qui porte une `description` fait autorité.
WF_DESCRIPTIONS: dict[str, str] = {
    "grimoire-session-bootstrap": "Reprendre le travail avec contexte complet",
    "grimoire-health-check": "Diagnostic global de santé projet",
    "grimoire-dream": "Consolider les apprentissages inter-sessions",
    "grimoire-pre-push": "Valider avant push (tests/lint/checks)",
    "grimoire-changelog": "Générer un changelog depuis l'historique",
    "grimoire-status": "Obtenir un snapshot rapide du projet",
    "grimoire-self-heal": "Diagnostiquer et réparer les pannes courantes",
}


@dataclass(frozen=True, slots=True)
class WorkflowEntry:
    """Un workflow, avec ce qu'il déclare de lui-même et d'où il vient."""

    slug: str
    path: Path
    source: str
    kind: str = KIND_COMMAND
    description: str = ""
    agents: tuple[str, ...] = ()
    team: str = ""
    patterns: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()

    @property
    def command(self) -> str:
        """La forme invocable, telle qu'elle est tapée dans un chat."""
        return f"/{self.slug}"

    @property
    def is_orchestration(self) -> bool:
        return self.kind == KIND_ORCHESTRATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "slug": self.slug,
            "file": self.path.name,
            "source": self.source,
            "kind": self.kind,
            "description": self.description,
            "agents": list(self.agents),
            "team": self.team,
            "patterns": list(self.patterns),
            "memory": list(self.memory),
            "triggers": list(self.triggers),
            "path": str(self.path),
        }


def workflow_slug(filename: str) -> str:
    """Slug de commande pour un fichier de workflow."""
    if filename.endswith(".prompt.md"):
        return filename[: -len(".prompt.md")]
    return Path(filename).stem


def _tuple_field(raw: Any) -> tuple[str, ...]:
    """Normalise une liste YAML ou une chaîne séparée par des virgules."""
    if isinstance(raw, str):
        items = [part.strip() for part in raw.replace(",", " ").split()]
    elif isinstance(raw, (list, tuple)):
        items = [str(item).strip() for item in raw]
    else:
        return ()
    return tuple(item for item in items if item)


def _triggers(raw: Any) -> tuple[str, ...]:
    """Quand utiliser ce workflow — des phrases, pas des identifiants.

    Séparé de :func:`_tuple_field`, qui découpe sur les espaces : un
    déclencheur est une phrase et ne survivrait pas au découpage.
    """
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        return ()
    return tuple(item.strip() for item in items if item.strip())


def _read_entry(path: Path, source: str, *, default_kind: str) -> WorkflowEntry | None:
    """Construit une entrée à partir du frontmatter, avec des replis sûrs.

    ``None`` quand le fichier ne se déclare pas : sous ``workflows/`` vivent
    aussi des gabarits de rapport, rendus par run et jamais invocables. Un
    workflow entre au catalogue en le disant — trois lignes de frontmatter —
    plutôt qu'en étant deviné depuis son emplacement.
    """
    slug = workflow_slug(path.name)
    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        meta = {}
    if default_kind == KIND_ORCHESTRATION and not meta:
        return None

    kind = str(meta.get("kind", "")).strip() or default_kind
    if kind not in (KIND_COMMAND, KIND_ORCHESTRATION):
        kind = default_kind

    description = str(meta.get("description", "")).strip()
    if not description:
        description = WF_DESCRIPTIONS.get(slug, "")

    return WorkflowEntry(
        slug=slug,
        path=path,
        source=source,
        kind=kind,
        description=description,
        agents=_tuple_field(meta.get("agents")),
        team=str(meta.get("team", "")).strip(),
        patterns=_tuple_field(meta.get("patterns")),
        memory=_tuple_field(meta.get("memory")),
        triggers=_triggers(meta.get("triggers")),
    )


def source_dirs(project_root: Path) -> list[tuple[str, Path, str, str]]:
    """Répertoires indexés : ``(source, chemin, motif, nature par défaut)``.

    Les workflows d'orchestration vivent dans un autre répertoire que les
    prompts — c'est pour cela qu'ils étaient invisibles. Les deux sont indexés,
    projet d'abord, cadre en repli.
    """
    fw = framework_path()
    candidates = [
        (SOURCE_PROJECT, project_root / ".github" / "prompts", "*.prompt.md", KIND_COMMAND),
        (SOURCE_FRAMEWORK, fw / "copilot" / "prompts", "*.prompt.md", KIND_COMMAND),
        (SOURCE_FRAMEWORK, fw / "workflows", "*.md", KIND_ORCHESTRATION),
    ]
    return [entry for entry in candidates if entry[1].is_dir()]


def _installed_workflows(project_root: Path) -> dict[str, Path]:
    """Workflows installés dans le projet, override gagnant sur le kit.

    Passe par :func:`grimoire.core.layout.layered_files` : le répertoire réel
    est ``_grimoire/kit/workflows``, shadowable depuis ``_grimoire/overrides``,
    et un projet non migré garde ses anciens emplacements.
    """
    found = layout.layered_files(project_root, layout.WORKFLOWS_SUBDIR, suffix=".md")
    return {stem: path for stem, path in found.items() if not path.name.endswith(".tpl.md")}


def load_workflows(project_root: Path) -> list[WorkflowEntry]:
    """Tous les workflows visibles depuis *project_root*, dédoublonnés par slug.

    Le projet prime sur l'installé, qui prime sur le cadre : un workflow
    personnalisé n'est jamais masqué par sa version d'origine.
    """
    seen: dict[str, WorkflowEntry] = {}

    for path in sorted(_installed_workflows(project_root).values()):
        entry = _read_entry(path, SOURCE_INSTALLED, default_kind=KIND_ORCHESTRATION)
        if entry is not None:
            seen.setdefault(entry.slug, entry)

    for source, directory, pattern, default_kind in source_dirs(project_root):
        for path in sorted(directory.glob(pattern)):
            if not path.is_file() or path.name.endswith(".tpl.md"):
                continue
            entry = _read_entry(path, source, default_kind=default_kind)
            if entry is None:
                continue
            if source == SOURCE_PROJECT:
                # Un prompt du projet l'emporte sur tout, y compris l'installé.
                seen[entry.slug] = entry
            else:
                seen.setdefault(entry.slug, entry)

    return sorted(seen.values(), key=lambda e: (_SOURCE_ORDER.index(e.source), e.slug))


def find_workflow(project_root: Path, workflow: str) -> WorkflowEntry | None:
    """Résout un workflow par slug ou par nom de fichier."""
    wanted = workflow_slug(workflow) if workflow.endswith(".md") else workflow
    for entry in load_workflows(project_root):
        if entry.slug == wanted:
            return entry
    return None


def find_framework_workflow(project_root: Path, workflow: str) -> WorkflowEntry | None:
    """Résout un workflow **dans le cadre livré**, en ignorant les copies locales.

    ``find_workflow`` applique la précédence de lecture — projet d'abord — qui
    est la bonne pour afficher, et la mauvaise pour installer : « installer
    depuis le cadre » sur un workflow déjà présent dans le projet renverrait la
    copie locale et refuserait l'opération.
    """
    wanted = workflow_slug(workflow) if workflow.endswith(".md") else workflow
    for source, directory, pattern, default_kind in source_dirs(project_root):
        if source != SOURCE_FRAMEWORK:
            continue
        for path in sorted(directory.glob(pattern)):
            if not path.is_file() or path.name.endswith(".tpl.md"):
                continue
            if workflow_slug(path.name) != wanted:
                continue
            return _read_entry(path, source, default_kind=default_kind)
    return None


def orchestrations(project_root: Path) -> list[WorkflowEntry]:
    """Les workflows qui coordonnent plusieurs agents."""
    return [entry for entry in load_workflows(project_root) if entry.is_orchestration]
