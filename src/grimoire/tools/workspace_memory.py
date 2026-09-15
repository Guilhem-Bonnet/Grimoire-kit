"""Agrégation mémoire multi-projets — dernier volet de l'issue #172.

`memory_link_status()` (`grimoire.tools.memory_link`) est mono-projet : le
niveau Flotte de l'espace Piloter compensait déjà en l'appelant une fois par
projet côté navigateur (`Promise.allSettled`, voir
`web/workspace/spaces/piloter.js::loadFleet`), jamais d'agrégation côté
serveur (PR #468, Refs #172). Ce module ferme ce volet : un calcul serveur qui
réutilise les lecteurs existants — `memory_link_status`,
`grimoire.memory.manager.MemoryManager`,
`grimoire.memory.taxonomy.run_memory_search` (la même chaîne que `grimoire
memory search`) — jamais une nouvelle route de lecture bas niveau, jamais un
second chemin de configuration.

Refus délibérés, tenus ici plutôt que rappelés à chaque appelant :

- aucune écriture ;
- aucune fusion de mémoires entre projets — la recherche croisée n'assemble
  qu'une LISTE de résultats déjà étiquetés par leur projet d'origine, jamais
  leur contenu ;
- aucun projet hors du registre de la machine
  (`grimoire.tools.project_registry.load_registry`) — les chemins viennent
  toujours du registre, jamais du corps de la requête HTTP ;
- un projet dont la mémoire est absente ou illisible est listé avec sa
  raison, jamais compté comme un store vide en silence (leçon #264 : une
  lecture ratée n'est pas un zéro).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.tools.memory_link import memory_link_status
from grimoire.tools.project_registry import load_registry, slug_for_path

__all__ = ["memory_overview", "memory_search"]

OVERVIEW_SCHEMA_VERSION = "grimoire-memory-overview/v1"
SEARCH_SCHEMA_VERSION = "grimoire-memory-search/v1"

_DEFAULT_SEARCH_LIMIT = 10


def _default_scope(project_root: Path) -> str | None:
    """Portée implicite quand la requête ne précise pas ``projects=`` :
    « ce projet », c'est-à-dire le seul projet déjà servi — jamais toute la
    flotte par défaut."""
    return slug_for_path(project_root)


def _select_entries(requested: str | None, project_root: Path) -> list[dict[str, str]]:
    entries = load_registry()
    if requested == "all":
        return entries
    scope = requested or _default_scope(project_root)
    if not scope:
        return []
    slugs = {s.strip() for s in scope.split(",") if s.strip()}
    return [e for e in entries if e.get("slug") in slugs]


def _resolve_readable_root(entry: dict[str, str]) -> tuple[Path | None, str | None]:
    """Racine du projet si elle existe sur disque, sinon une raison nommée."""
    raw = str(entry.get("path", "") or "")
    if not raw:
        return None, "chemin absent du registre"
    project_root = Path(raw)
    if not project_root.is_dir():
        return None, f"dossier introuvable : {raw}"
    return project_root, None


def _lexical_index_state(project_root: Path, status: dict[str, Any]) -> str:
    """État de l'index lexical du projet, jamais déduit du seul backend
    configuré :

    - ``primaire`` — le backend résolu EST le store lexical (FTS5/BM25) ;
    - ``compagnon`` — un backend différent (vecteurs) a un index lexical en
      doublure, utilisé pour la fusion RRF (``MemoryManager.prefers_hybrid``) ;
    - ``absent`` — aucun index lexical : la recherche croisée retombe sur le
      backend seul pour ce projet ;
    - ``inconnu`` — la sonde elle-même a échoué (best-effort, jamais bloquant).
    """
    if status.get("resolvedBackend") == "lexical":
        return "primaire"
    try:
        from grimoire.core.config import GrimoireConfig
        from grimoire.memory.manager import MemoryManager

        cfg = GrimoireConfig.from_yaml(project_root / "project-context.yaml")
        mgr = MemoryManager.from_config(cfg, project_root=project_root)
        return "compagnon" if mgr.prefers_hybrid else "absent"
    except Exception:
        return "inconnu"


def _last_write(project_root: Path, status: dict[str, Any]) -> str | None:
    """Horodatage de la dernière écriture, best-effort.

    ``memory_link_status`` ne le porte pas — son ``detail`` vient de
    ``BackendStatus``, qui ne connaît que le compte d'entrées. Relire les
    entrées une fois ici évite d'étendre ce contrat pour un seul champ ;
    mêmes imports tardifs, même tolérance aux erreurs que le reste du module.
    """
    if not status.get("available") or not status.get("entries"):
        return None
    try:
        from grimoire.core.config import GrimoireConfig
        from grimoire.memory.manager import MemoryManager

        cfg = GrimoireConfig.from_yaml(project_root / "project-context.yaml")
        mgr = MemoryManager.from_config(cfg, project_root=project_root)
        entries = mgr.get_all(limit=None)
    except Exception:
        return None
    stamps = [e.updated_at or e.created_at for e in entries if (e.updated_at or e.created_at)]
    return max(stamps) if stamps else None


def _project_row(entry: dict[str, str]) -> dict[str, Any]:
    slug = str(entry.get("slug", ""))
    row: dict[str, Any] = {
        "slug": slug,
        "name": str(entry.get("name", "")) or slug,
        "path": str(entry.get("path", "")),
        "state": "unreadable",
        "configuredBackend": None,
        "resolvedBackend": None,
        "entries": None,
        "lastWrite": None,
        "lexicalIndex": "inconnu",
        "reason": None,
    }
    project_root, reason = _resolve_readable_root(entry)
    if project_root is None:
        row["reason"] = reason
        return row
    try:
        # probe=True : cette vue est une action explicite et peu fréquente
        # (pas le pouls Piloter, sondé en continu) — l'utilisateur qui ouvre
        # l'aperçu mémoire veut le compte d'entrées réel, pas un état caché
        # potentiellement vide faute de sonde antérieure.
        status = memory_link_status(project_root, probe=True)
    except Exception as exc:
        # `memory_link_status` est déjà best-effort et ne devrait jamais
        # lever — mais une route d'agrégation qui casserait sur UN projet
        # de la flotte serait pire que ce qu'elle prévient (#264).
        row["reason"] = f"lecture impossible : {exc}"
        return row
    row["state"] = status.get("state", "unreadable")
    row["configuredBackend"] = status.get("configuredBackend")
    row["resolvedBackend"] = status.get("resolvedBackend")
    row["entries"] = status.get("entries")
    if status.get("state") == "uninitialized":
        row["reason"] = "projet non initialisé (pas de project-context.yaml)"
    elif status.get("error"):
        row["reason"] = status["error"]
    if status.get("available"):
        row["lexicalIndex"] = _lexical_index_state(project_root, status)
        row["lastWrite"] = _last_write(project_root, status)
    return row


def memory_overview(project_root: Path, requested: str | None) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/overview``.

    ``requested`` est la valeur brute du paramètre ``projects=`` : ``"all"``
    pour tout le registre, une liste ``slug1,slug2`` pour un sous-ensemble,
    ``None``/vide pour « ce projet » (celui que l'hôte sert déjà).
    """
    entries = _select_entries(requested, project_root)
    projects = [_project_row(e) for e in entries]
    readable = [p for p in projects if p["state"] != "unreadable"]
    total_entries = sum(p["entries"] for p in projects if isinstance(p["entries"], int))
    return {
        "schemaVersion": OVERVIEW_SCHEMA_VERSION,
        "requested": requested or "",
        "projects": projects,
        "summary": {
            "count": len(projects),
            "readable": len(readable),
            "totalEntries": total_entries,
        },
    }


def _search_project(entry: dict[str, str], query: str, limit: int) -> dict[str, Any]:
    slug = str(entry.get("slug", ""))
    name = str(entry.get("name", "")) or slug
    project_root, reason = _resolve_readable_root(entry)
    if project_root is None:
        return {"slug": slug, "name": name, "results": [], "reason": reason}
    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return {
            "slug": slug, "name": name, "results": [],
            "reason": "projet non initialisé (pas de project-context.yaml)",
        }
    try:
        from grimoire.core.config import GrimoireConfig
        from grimoire.core.exceptions import GrimoireConfigError, GrimoireMemoryError
        from grimoire.memory.manager import MemoryManager
        from grimoire.memory.taxonomy import run_memory_search

        cfg = GrimoireConfig.from_yaml(config_path)
        mgr = MemoryManager.from_config(cfg, project_root=project_root)
        found = run_memory_search(mgr, query, hybrid=mgr.prefers_hybrid, limit=limit)
    except (GrimoireConfigError, GrimoireMemoryError, ImportError, OSError, ValueError) as exc:
        return {"slug": slug, "name": name, "results": [], "reason": str(exc)}
    results = [{**e.to_dict(), "projectSlug": slug, "projectName": name} for e in found]
    return {"slug": slug, "name": name, "results": results, "reason": None}


def memory_search(
    project_root: Path,
    query: str | None,
    requested: str | None,
    *,
    limit: int = _DEFAULT_SEARCH_LIMIT,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/search`` — lecture seule.

    Interroge la recherche lexicale/hybride de chaque projet sélectionné
    séparément — la même chaîne que ``grimoire memory search``
    (``MemoryManager.prefers_hybrid`` + ``run_memory_search``) — et ne
    fusionne que la LISTE des résultats, chacun étiqueté par ``projectSlug``
    en tête de ligne. Aucun contenu ne migre d'un store à l'autre : un projet
    en échec est nommé dans ``projects[].reason``, jamais tu.
    """
    normalized_query = (query or "").strip()
    entries = _select_entries(requested, project_root)
    if not normalized_query:
        return {
            "schemaVersion": SEARCH_SCHEMA_VERSION,
            "query": normalized_query,
            "projects": [],
            "results": [],
            "count": 0,
        }
    per_project = [_search_project(e, normalized_query, limit) for e in entries]
    merged: list[dict[str, Any]] = []
    for found in per_project:
        merged.extend(found["results"])
    # Tri stable : score décroissant, puis slug de projet. Le tri de Python
    # est stable, donc deux résultats à égalité de score restent groupés par
    # projet dans l'ordre du registre plutôt que de se mélanger au hasard —
    # condition testée côté navigateur (ordre reproductible d'un appel à
    # l'autre).
    merged.sort(key=lambda r: (-float(r.get("score") or 0.0), str(r.get("projectSlug", ""))))
    return {
        "schemaVersion": SEARCH_SCHEMA_VERSION,
        "query": normalized_query,
        "projects": [
            {"slug": p["slug"], "name": p["name"], "reason": p["reason"], "count": len(p["results"])}
            for p in per_project
        ],
        "results": merged,
        "count": len(merged),
    }
