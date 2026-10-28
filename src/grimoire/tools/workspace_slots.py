"""Emplacements mémoire depuis le cockpit (P41) : attribuer, retirer, séparer un projet.

Un *emplacement* est l'endroit où un projet range un type de mémoire : une
collection Qdrant, une collection Weaviate, un espace de noms Redis, une base
Neo4j. Deux projets qui pointent le même emplacement le **partagent**.

Deux actions du catalogue (:mod:`grimoire.tools.workspace_actions`) s'appuient
sur ce module, qui ne calcule que des plans et n'écrit que ce que l'action lui
demande :

``memory.slot`` (classe A)
    Attribuer un emplacement au projet servi, ou le lui retirer. **Configuration
    seulement** : quelques clés de ``memory:`` dans ``project-context.yaml``.
    Aucun souvenir n'est copié, déplacé ni effacé ; le retour arrière restaure
    le fichier d'avant. Attribuer un emplacement que d'autres projets utilisent
    déjà avertit « cet emplacement deviendra partagé avec N » et propose par
    défaut un emplacement neuf. Pour Redis, l'espace de noms dédié est proposé
    avant le conteneur dédié.

``memory.split`` (classe D)
    Séparer le projet servi d'un emplacement partagé, par ``grimoire memory split``
    (P28, :mod:`grimoire.memory.split`) : export JSON obligatoire de la source,
    copie par upsert à identifiants conservés, vérification, puis bascule de la
    configuration. La source n'est jamais touchée ; son nettoyage est un geste
    séparé et différé, qui n'appartient pas à cette action. Neo4j est « non
    séparable » tant que P15 n'est pas livré.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "KINDS",
    "NEO4J_SPLIT_BLOCKER",
    "SlotView",
    "assign_changes",
    "current_slot",
    "sharers",
    "slot_options",
    "split_blockers",
]

#: Par type : clé de l'adresse (serveur) et clé du nom (collection / espace de noms / base).
KINDS: dict[str, dict[str, str]] = {
    "qdrant": {"label": "Qdrant", "location": "qdrant_url", "name": "collection_prefix", "backend": "qdrant-server"},
    "weaviate": {"label": "Weaviate", "location": "weaviate_url", "name": "weaviate_collection", "backend": "weaviate-server"},
    "redis": {"label": "Redis", "location": "redis_url", "name": "collection_prefix", "backend": ""},
    "neo4j": {"label": "Neo4j", "location": "neo4j_uri", "name": "neo4j_database", "backend": ""},
}

NEO4J_SPLIT_BLOCKER = "Neo4j : non séparable tant que P15 (graphe par projet) n'est pas livré"
_REDIS_MODES = ("namespace", "container")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\s]+$")


@dataclass(frozen=True)
class SlotView:
    """Un emplacement tel que la configuration d'un projet le désigne."""

    kind: str
    location: str
    name: str

    @property
    def assigned(self) -> bool:
        return bool(self.location)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "location": self.location, "name": self.name, "assigned": self.assigned}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "projet"


def _raw_block(config: Path) -> dict[str, Any]:
    from grimoire.tools.memory_setup import _raw_memory_block

    return _raw_memory_block(config)


def _project_name(config: Path) -> str:
    try:
        from grimoire.core.config import GrimoireConfig

        return GrimoireConfig.from_yaml(config).project.name or config.parent.name
    except Exception:
        return config.parent.name


def current_slot(config: Path, kind: str) -> SlotView:
    """L'emplacement de *kind* que le fichier désigne (valeurs écrites, sinon défauts)."""
    spec = KINDS[kind]
    block = _raw_block(config)
    name = str(block.get(spec["name"]) or ("neo4j" if kind == "neo4j" else ""))
    if kind in ("qdrant", "weaviate") and block.get("backend") != spec["backend"]:
        return SlotView(kind, "", name)
    if kind == "redis" and block.get("short_term_backend") != "redis":
        return SlotView(kind, "", name)
    return SlotView(kind, str(block.get(spec["location"]) or ""), name)


def sharers(config: Path, kind: str, registry: list[dict[str, str]], *, slot: SlotView | None = None) -> list[str]:
    """Noms des **autres** projets (registre) dont la configuration désigne le même emplacement."""
    mine = config.resolve()
    target = slot or current_slot(config, kind)
    if not target.assigned:
        return []
    found: dict[str, str] = {}
    for entry in registry:
        raw = str(entry.get("path", ""))
        other = Path(raw) / "project-context.yaml" if raw else None
        if other is None or not other.is_file() or other.resolve() == mine:
            continue
        theirs = current_slot(other, kind)
        if (theirs.location.rstrip("/"), theirs.name) == (target.location.rstrip("/"), target.name):
            found.setdefault(_project_name(other), raw)
    return sorted(found)


def slot_options(config: Path, kind: str, registry: list[dict[str, str]]) -> dict[str, Any]:
    """Ce que le cockpit propose pour *kind* : l'emplacement neuf d'abord, puis les emplacements en usage."""
    me = _slug(_project_name(config))
    mine = current_slot(config, kind)
    existing: dict[tuple[str, str], list[str]] = {}
    for entry in registry:
        raw = str(entry.get("path", ""))
        other = Path(raw) / "project-context.yaml" if raw else None
        if other is None or not other.is_file():
            continue
        theirs = current_slot(other, kind)
        if theirs.assigned:
            existing.setdefault((theirs.location, theirs.name), []).append(_project_name(other))
    options: list[dict[str, Any]] = [
        {"id": "new", "label": "Emplacement neuf (recommandé)", "default": True,
         "location": mine.location, "name": me, "usedBy": []},
    ]
    options += [
        {"id": "existing", "label": "Emplacement en usage", "default": False,
         "location": loc, "name": name, "usedBy": sorted(set(users))}
        for (loc, name), users in sorted(existing.items())
    ]
    out: dict[str, Any] = {"kind": kind, "label": KINDS[kind]["label"], "current": mine.to_dict(), "options": options}
    if kind == "redis":
        out["redisModes"] = [
            {"id": "namespace", "label": "Namespace dédié sur l'instance existante", "default": True},
            {"id": "container", "label": "Conteneur dédié (nouvelle instance)", "default": False},
        ]
    return out


def assign_changes(
    config: Path, params: dict[str, Any], registry: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any]]:
    """Calcule ``(changements de clés, bloquants, avertissements, faits)`` pour ``memory.slot``.

    ``params`` : ``op`` (``assign`` | ``remove``), ``kind``, et pour ``assign`` ``slot``
    (``new`` par défaut, ou ``existing`` avec ``location`` et ``name``), ``redis_mode``
    (``namespace`` par défaut, ou ``container`` avec ``location``).
    """
    blockers: list[str] = []
    warnings: list[str] = []
    kind, op = str(params.get("kind") or ""), str(params.get("op") or "assign")
    if kind not in KINDS:
        return [], [f"type d'emplacement inconnu « {kind} » — attendu : {', '.join(KINDS)}"], [], {}
    if op not in ("assign", "remove"):
        return [], [f"opération inconnue « {op} » — attendu : assign | remove"], [], {}
    spec = KINDS[kind]
    block = _raw_block(config)
    mine = current_slot(config, kind)
    facts: dict[str, Any] = {"kind": kind, "op": op, "before": mine.to_dict(), "options": slot_options(config, kind, registry)}
    target: dict[str, Any] = {}

    if op == "remove":
        if not mine.assigned:
            return [], [f"aucun emplacement {spec['label']} n'est attribué à ce projet"], [], facts
        target[spec["location"]] = ""
        if kind in ("qdrant", "weaviate"):
            target["backend"] = "lexical"
        if kind == "redis":
            target["short_term_backend"] = "sqlite"
        warnings.append("retrait en configuration seulement : les souvenirs de l'emplacement ne sont ni copiés ni effacés")
        facts["after"] = SlotView(kind, "", mine.name).to_dict()
    else:
        choice = str(params.get("slot") or "new")
        mode = str(params.get("redis_mode") or "namespace")
        if choice not in ("new", "existing"):
            blockers.append(f"choix d'emplacement inconnu « {choice} » — attendu : new | existing")
        if kind == "redis" and mode not in _REDIS_MODES:
            blockers.append(f"mode Redis inconnu « {mode} » — attendu : {' | '.join(_REDIS_MODES)}")
        location, name = mine.location, ""
        if choice == "existing":
            location, name = str(params.get("location") or ""), str(params.get("name") or "")
        else:
            name = str(params.get("name") or _slug(_project_name(config)))
            if params.get("location") or (kind == "redis" and mode == "container"):
                location = str(params.get("location") or "")
                if not location and kind == "redis":
                    blockers.append("conteneur Redis dédié : l'adresse (location) du nouveau conteneur est requise")
        if not location:
            blockers.append(f"aucune adresse {spec['label']} connue : indiquez `location`")
        elif not _URL.match(location):
            blockers.append(f"adresse {spec['label']} invalide : « {location} » (schéma://hôte attendu)")
        if not _NAME.match(name):
            blockers.append(f"nom d'emplacement invalide : « {name} »")
        if name == "grimoire" and kind == "redis":
            blockers.append("l'espace de noms « grimoire » est celui de tout le monde : choisissez-en un propre au projet")
        if not blockers:
            target[spec["location"]], target[spec["name"]] = location, name
            if kind in ("qdrant", "weaviate"):
                target["backend"] = spec["backend"]
            if kind == "redis":
                target["short_term_backend"] = "redis"
                facts["redisMode"] = mode
            after = SlotView(kind, location, name)
            facts["after"] = after.to_dict()
            others = sharers(config, kind, registry, slot=after)
            facts["sharedWith"] = others
            if others:
                warnings.append(
                    f"cet emplacement deviendra partagé avec {len(others)} : {', '.join(others)}. "
                    "Un emplacement neuf (proposé par défaut) évite ce partage."
                )
    changes = [
        {"key": key, "old": block.get(key, getattr(_defaults(), key, None)), "new": new, "absent": key not in block}
        for key, new in sorted(target.items())
        if block.get(key) != new
    ]
    if not changes and not blockers:
        blockers.append("rien à changer : la configuration désigne déjà cet emplacement")
    return changes, blockers, warnings, facts


def _defaults() -> Any:
    from grimoire.core.config import MemoryConfig

    return MemoryConfig()


def write_changes(config: Path, changes: list[dict[str, Any]]) -> list[str]:
    """Écrit les clés dans ``memory:`` en conservant commentaires et ordre ; rend les clés écrites."""
    from ruamel.yaml import YAML

    from grimoire.core.fsio import atomic_write

    yaml = YAML()
    yaml.preserve_quotes = True
    with config.open(encoding="utf-8") as fh:
        data = yaml.load(fh)
    if not isinstance(data, dict):
        raise ValueError("project-context.yaml ne contient pas un mapping racine")
    if not isinstance(data.get("memory"), dict):
        data["memory"] = {}
    for change in changes:
        data["memory"][change["key"]] = change["new"]
    out = io.StringIO()
    yaml.dump(data, out)
    atomic_write(config, out.getvalue())
    return [str(c["key"]) for c in changes]


def verify_changes(config: Path, changes: list[dict[str, Any]]) -> dict[str, Any]:
    block = _raw_block(config)
    wrong = [c["key"] for c in changes if block.get(c["key"]) != c["new"]]
    return {"ok": not wrong, "detail": "bloc memory: relu conforme au plan" if not wrong else f"clés non conformes : {wrong}"}


# ── Séparation (classe D) ──────────────────────────────────────────────────────


def split_blockers(config: Path, kind: str) -> list[str]:
    """Pourquoi *kind* ne peut pas être séparé, vide si rien ne s'y oppose."""
    if kind == "neo4j":
        return [NEO4J_SPLIT_BLOCKER]
    if kind not in KINDS:
        return [f"type d'emplacement inconnu « {kind} » — attendu : {', '.join(KINDS)}"]
    if kind == "redis":
        return ["Redis : mémoire chaude non autoritaire — séparez-la par un espace de noms dédié (action memory.slot)"]
    if not current_slot(config, kind).assigned:
        return [f"aucun emplacement {KINDS[kind]['label']} n'est attribué à ce projet"]
    return []


def render_split_config(config: Path, collection: str, kind: str) -> str:
    """Contenu de la configuration repointé vers la collection séparée, selon le type."""
    from grimoire.memory.split import render_collection_prefix

    text = render_collection_prefix(config, collection)
    if kind != "weaviate":
        return text
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(text)
    data["memory"]["weaviate_collection"] = collection
    out = io.StringIO()
    yaml.dump(data, out)
    return out.getvalue()


OpenStore = Callable[[Any, Path, str | None], Any]
