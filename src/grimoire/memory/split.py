"""Découpe d'un segment mémoire partagé en un segment par projet (P28).

Cas visé : plusieurs projets ont écrit dans la même collection (``project_name``
différents dans les métadonnées). La découpe lit le segment partagé, le copie
**par upsert à identifiants conservés** vers une collection par projet, vérifie
les comptes et les identifiants, et seulement alors propose de repointer la
configuration de chaque projet concerné.

Garanties, dans l'ordre où elles s'appliquent :

1. aperçu par défaut — rien n'est écrit sans ``--apply`` ;
2. export JSON de la source entière **avant** la première copie ;
3. la source n'est jamais modifiée ni supprimée, y compris en cas de succès :
   la suppression est un geste séparé et explicite, hors de cette commande ;
4. une copie qui échoue ou qui ne se vérifie pas laisse les configurations
   intactes (aucun projet ne bascule vers un segment incomplet) ;
5. rejouable : l'upsert ne crée aucun doublon.

Le module est pur (aucune E/S Typer, backends injectés) pour se tester avec des
backends factices en mémoire.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.migration import portable_metadata

__all__ = [
    "SplitError",
    "SplitGroup",
    "SplitPlan",
    "apply_split",
    "export_entries",
    "plan_split",
    "render_collection_prefix",
    "target_collection",
]

#: Entrées sans ``project_name`` : jamais attribuées par défaut à un projet.
UNASSIGNED = ""


class SplitError(GrimoireMemoryError):
    """La découpe s'est arrêtée ; la source et les configurations sont intactes."""


@dataclass(frozen=True, slots=True)
class SplitGroup:
    """Les entrées d'un projet et la collection qui les recevra."""

    project: str
    collection: str
    ids: tuple[str, ...]
    #: ``project-context.yaml`` à repointer (projet du registre qui partage la source), sinon ``None``.
    config_path: Path | None = None

    @property
    def count(self) -> int:
        return len(self.ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "collection": self.collection,
            "count": self.count,
            "config": str(self.config_path) if self.config_path else None,
        }


@dataclass(frozen=True, slots=True)
class SplitPlan:
    """Ce que la découpe ferait : un groupe par projet, plus les entrées sans projet."""

    source_collection: str
    total: int
    groups: tuple[SplitGroup, ...]
    unassigned: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceCollection": self.source_collection,
            "total": self.total,
            "groups": [g.to_dict() for g in self.groups],
            "unassigned": len(self.unassigned),
        }


@dataclass(slots=True)
class SplitReport:
    """Résultat d'une découpe appliquée."""

    export_path: Path
    copied: dict[str, int] = field(default_factory=dict)
    verified: dict[str, bool] = field(default_factory=dict)
    configs_updated: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "export": str(self.export_path),
            "copied": self.copied,
            "verified": self.verified,
            "configsUpdated": self.configs_updated,
        }


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "projet"


def target_collection(base: str, project: str) -> str:
    """Nom de la collection d'un projet : ``<collection d'origine>-<slug du projet>``."""
    return f"{base}-{slug(project)}"


def plan_split(
    entries: Iterable[MemoryEntry],
    *,
    source_collection: str,
    configs: Mapping[str, Path] | None = None,
    only: Iterable[str] = (),
) -> SplitPlan:
    """Groupe les entrées par ``metadata.project_name``.

    *configs* associe un nom de projet à son ``project-context.yaml`` (projets
    du registre qui partagent la source). *only* restreint la découpe à
    certains projets ; les autres restent dans le segment partagé, intacts.
    """
    wanted = {p for p in only if p}
    rows = list(entries)
    owners: dict[str, list[str]] = defaultdict(list)
    for entry in rows:
        name = str(entry.metadata.get("project_name", UNASSIGNED))
        if name != UNASSIGNED and name not in owners[target_collection(source_collection, name)]:
            owners[target_collection(source_collection, name)].append(name)
    clashes = {c: names for c, names in owners.items() if len(names) > 1}
    if clashes:
        collection, names = next(iter(clashes.items()))
        raise SplitError(
            f"les projets {', '.join(repr(n) for n in names)} ne diffèrent que par la casse ou la ponctuation : "
            f"ils auraient la même collection cible « {collection} » et leurs souvenirs se mélangeraient. "
            "Rien n'est écrit ; harmonisez project.name d'abord."
        )
    by_project: dict[str, list[str]] = defaultdict(list)
    total = 0
    for entry in rows:
        total += 1
        by_project[str(entry.metadata.get("project_name", UNASSIGNED))].append(entry.id)
    groups = tuple(
        SplitGroup(
            project=project,
            collection=target_collection(source_collection, project),
            ids=tuple(ids),
            config_path=(configs or {}).get(project),
        )
        for project, ids in sorted(by_project.items())
        if project != UNASSIGNED and (not wanted or project in wanted)
    )
    return SplitPlan(
        source_collection=source_collection,
        total=total,
        groups=groups,
        unassigned=tuple(by_project.get(UNASSIGNED, ())),
    )


def export_entries(entries: Iterable[MemoryEntry], path: Path) -> Path:
    """Sauvegarde JSON de toute la source, écrite avant la première copie."""
    rows = [e.to_dict() for e in entries]
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "count": len(rows), "entries": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def apply_split(
    plan: SplitPlan,
    entries: Iterable[MemoryEntry],
    *,
    export_path: Path,
    open_target: Callable[[str], Any],
    render_config: Callable[[Path, str], str] | None = None,
) -> SplitReport:
    """Exécute la découpe. Lève :class:`SplitError` au premier écart, sans toucher aux configurations.

    *open_target(collection)* rend un objet qui expose ``upsert(id, text, user_id=,
    tags=, metadata=)`` et ``get_all()`` (un :class:`~grimoire.memory.manager.MemoryManager`
    en production, un double en test).
    """
    everything = list(entries)
    if len(everything) != plan.total:
        raise SplitError(f"la source a changé depuis l'aperçu ({plan.total} → {len(everything)} entrées) : relancer.")
    report = SplitReport(export_path=export_entries(everything, export_path))
    by_id = {e.id: e for e in everything}

    for group in plan.groups:
        target = open_target(group.collection)
        done = 0
        for entry_id in group.ids:
            entry = by_id[entry_id]
            try:
                target.upsert(
                    entry.id, entry.text, user_id=entry.user_id, tags=entry.tags,
                    metadata=portable_metadata(entry), created_at=entry.created_at,
                )
            except Exception as exc:
                raise SplitError(
                    f"copie interrompue vers « {group.collection} » après {done}/{group.count} entrée(s) : {exc}. "
                    f"Source et configurations intactes ; l'export est dans {report.export_path}. "
                    "Relancer reprend sans doublon."
                ) from exc
            done += 1
        report.copied[group.project] = done
        present = {e.id for e in target.get_all()}
        missing = sorted(set(group.ids) - present)
        report.verified[group.project] = not missing
        if missing:
            raise SplitError(
                f"vérification échouée pour « {group.project} » : {len(missing)} identifiant(s) absent(s) de "
                f"« {group.collection} » (premier : {missing[0]}). Configurations non modifiées."
            )

    if render_config is not None:
        _repoint_configs(plan, render_config, report)
    return report


def _repoint_configs(plan: SplitPlan, render_config: Callable[[Path, str], str], report: SplitReport) -> None:
    """Repointe les configurations en tout ou rien.

    Tout est d'abord rendu (un YAML illisible s'arrête ici, rien n'est touché) ; chaque remplacement est
    atomique (:func:`grimoire.core.fsio.atomic_write`) ; si l'un échoue, ceux déjà faits sont remis tels quels.
    """
    from grimoire.core.fsio import atomic_write

    pending = [(g.config_path, g.collection) for g in plan.groups if g.config_path is not None]
    originals = {path: path.read_text(encoding="utf-8") for path, _ in pending}
    rendered = [(path, render_config(path, collection)) for path, collection in pending]
    done: list[Path] = []
    try:
        for path, text in rendered:
            atomic_write(path, text)
            done.append(path)
    except OSError as exc:
        for path in done:
            atomic_write(path, originals[path])
        raise SplitError(f"écriture des configurations impossible ({exc}) ; aucune n'a été modifiée.") from exc
    report.configs_updated.extend(str(p) for p in done)


def render_collection_prefix(config_path: Path, collection: str) -> str:
    """Contenu de ``project-context.yaml`` avec ``memory.collection_prefix`` repointé, commentaires conservés."""
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.load(fh)
    if not isinstance(data, dict):
        raise SplitError(f"{config_path} ne contient pas un mapping racine")
    memory = data.get("memory")
    if not isinstance(memory, dict):
        data["memory"] = {}
        memory = data["memory"]
    memory["collection_prefix"] = collection
    if memory.get("backend") == "weaviate-server" and memory.get("weaviate_collection"):
        memory["weaviate_collection"] = collection
    out = io.StringIO()
    yaml.dump(data, out)
    return out.getvalue()
