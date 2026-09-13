"""Which hosts a project's ``grimoire host sync`` and ``up`` are allowed to write to.

Issue #177 (petite version, décision Guilhem du 2026-09-12 : pas de canal
plugin, seulement des hôtes déclarés). ``hosts.enabled`` dans
``project-context.yaml`` est la déclaration explicite ; quand elle est
absente, le système de fichiers du projet — des fichiers qu'un hôte y a déjà
déposés, à la main ou avant l'existence de cette clé — sert de valeur par
défaut, pour qu'aucun projet existant ne perde un fichier au prochain ``up``.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.bridges.schemas import HostId
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError
from grimoire.hosts.capabilities import HOST_ALIASES, resolve_host

#: Ordre canonique — celui de l'issue #177 et du texte d'aide `--host` du CLI.
KNOWN_HOST_ALIASES: tuple[str, ...] = ("claude", "copilot", "gemini", "cursor", "codex")

__all__ = [
    "KNOWN_HOST_ALIASES",
    "alias_for_host",
    "detect_enabled_hosts",
    "enabled_host_ids",
    "resolve_enabled_hosts",
]


def detect_enabled_hosts(project_root: Path) -> tuple[str, ...]:
    """Filesystem-only default: which hosts already have files in *project_root*.

    ``claude`` est toujours inclus quand rien n'est détecté — la persona
    d'entrée (issue #382) suppose qu'un hôte peut démarrer une session, et
    Claude Code est celui dans lequel ce kit lui-même tourne par défaut.
    """
    root = project_root
    found: list[str] = []
    if (root / ".claude").is_dir():
        found.append("claude")
    if (root / ".github" / "copilot-instructions.md").is_file() or (root / ".github" / "agents").is_dir():
        found.append("copilot")
    if (root / "GEMINI.md").is_file():
        found.append("gemini")
    if (root / ".cursor").is_dir():
        found.append("cursor")
    if (root / "AGENTS.md").is_file() and (root / ".codex").exists():
        found.append("codex")
    if not found:
        found.append("claude")
    return tuple(found)


def resolve_enabled_hosts(project_root: Path, cfg: GrimoireConfig | None = None) -> tuple[str, ...]:
    """The project's declared or detected set of enabled host aliases.

    ``cfg`` est accepté pour que les appelants ayant déjà chargé la config
    une fois n'en paient pas une seconde lecture YAML ; omis, cette fonction
    la charge elle-même et traite une config absente ou invalide comme une
    clé ``hosts:`` absente.
    """
    if cfg is None:
        try:
            cfg = GrimoireConfig.from_yaml(project_root / "project-context.yaml")
        except GrimoireConfigError:
            cfg = None
    declared = cfg.hosts.enabled if cfg is not None else None
    if declared is not None:
        return declared
    return detect_enabled_hosts(project_root)


def alias_for_host(host_id: HostId) -> str:
    """Reverse of the canonical entries in :data:`grimoire.hosts.capabilities.HOST_ALIASES`."""
    for alias in KNOWN_HOST_ALIASES:
        if HOST_ALIASES.get(alias) == host_id:
            return alias
    return host_id.value


def enabled_host_ids(project_root: Path, cfg: GrimoireConfig | None = None) -> set[HostId]:
    """:func:`resolve_enabled_hosts` translated to :class:`HostId`, dropping unresolvable aliases."""
    ids: set[HostId] = set()
    for alias in resolve_enabled_hosts(project_root, cfg):
        resolved = resolve_host(alias)
        if resolved is not None:
            ids.add(resolved)
    return ids
