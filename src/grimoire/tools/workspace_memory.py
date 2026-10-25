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
  lecture ratée n'est pas un zéro) ;
- aucun store compté deux fois : des projets qui partagent la même
  collection sur le même serveur (ou le même fichier) n'y ajoutent leurs
  entrées qu'une fois dans ``totalEntries``.

L'aperçu ouvre un seul ``MemoryManager`` par projet, réutilisé par la sonde,
l'index lexical et la dernière écriture, et sonde les projets en parallèle :
la durée suit le projet le plus lent, pas la somme des projets.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from grimoire.memory.supersession import INCLUDE_ALL
from grimoire.tools.memory_link import memory_link_status
from grimoire.tools.project_registry import load_registry, slug_for_path
from grimoire.tools.workspace_knowledge import describe, journal_index, open_readonly, owner_of, ownership

__all__ = ["RRF_K", "fuse_by_rank", "memory_overview", "memory_search"]

OVERVIEW_SCHEMA_VERSION = "grimoire-memory-overview/v1"
SEARCH_SCHEMA_VERSION = "grimoire-memory-search/v1"

_DEFAULT_SEARCH_LIMIT = 10
#: Constante de la fusion par rang réciproque (même valeur que ``retrieval.py``).
RRF_K = 60
# Borne du parallélisme de l'aperçu : chaque projet sonde déjà jusqu'à trois
# services, une flotte entière ne doit pas ouvrir des dizaines de connexions.
_OVERVIEW_MAX_WORKERS = 8


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
        # Le projet que l'hôte sert déjà, même absent du registre (l'atelier
        # `grimoire serve`) : son chemin vient de l'hôte, jamais de la requête.
        return [] if requested else [{"slug": "", "name": "", "path": str(project_root)}]
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


def _lexical_index_state(manager: Any, status: dict[str, Any]) -> str:
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
        return "compagnon" if manager.prefers_hybrid else "absent"
    except Exception:
        return "inconnu"


def _last_write(manager: Any, status: dict[str, Any]) -> str | None:
    """Horodatage de la dernière écriture, best-effort.

    ``memory_link_status`` ne le porte pas — son ``detail`` vient de
    ``BackendStatus``, qui ne connaît que le compte d'entrées. Relire les
    entrées une fois ici évite d'étendre ce contrat pour un seul champ ;
    même tolérance aux erreurs que le reste du module.
    """
    if not status.get("available") or not status.get("entries"):
        return None
    try:
        entries = manager.get_all(limit=None, **INCLUDE_ALL)
    except Exception:
        return None
    stamps = [e.updated_at or e.created_at for e in entries if (e.updated_at or e.created_at)]
    return max(stamps) if stamps else None


def _open_manager(project_root: Path) -> tuple[Any, Any, str | None]:
    """``(config, manager, raison)`` — le seul ``MemoryManager`` du projet.

    Config absente ou illisible : ``(None, None, None)``, ``memory_link_status``
    nomme lui-même ce cas. Backend impossible à ouvrir : la raison, sans
    nouvel essai plus loin.
    """
    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return None, None, None
    try:
        from grimoire.core.config import GrimoireConfig

        cfg = GrimoireConfig.from_yaml(config_path)
    except Exception:
        return None, None, None
    try:
        from grimoire.memory.manager import MemoryManager

        return cfg, MemoryManager.from_config(cfg, project_root=project_root), None
    except Exception as exc:
        return cfg, None, str(exc)


def _store_key(cfg: Any, project_root: Path) -> tuple[str, str] | None:
    try:
        from grimoire.memory.manager import store_identity

        return store_identity(cfg, project_root)
    except Exception:
        return None


def _project_row(entry: dict[str, str]) -> tuple[dict[str, Any], tuple[str, str] | None]:
    """Ligne d'aperçu d'un projet et l'identité de son store (dédoublonnage)."""
    slug = str(entry.get("slug", ""))
    row: dict[str, Any] = {
        "slug": slug,
        "name": str(entry.get("name", "")) or slug or Path(str(entry.get("path", "") or ".")).name,
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
        return row, None
    cfg, manager, manager_error = _open_manager(project_root)
    if manager_error is not None:
        row["state"] = "unavailable"
        row["configuredBackend"] = cfg.memory.backend
        row["reason"] = manager_error
        return row, None
    try:
        # probe=True : cette vue est une action explicite et peu fréquente
        # (pas le pouls Piloter, sondé en continu) — l'utilisateur qui ouvre
        # l'aperçu mémoire veut le compte d'entrées réel, pas un état caché
        # potentiellement vide faute de sonde antérieure.
        status = memory_link_status(project_root, probe=True, manager=manager)
    except Exception as exc:
        # `memory_link_status` est déjà best-effort et ne devrait jamais
        # lever — mais une route d'agrégation qui casserait sur UN projet
        # de la flotte serait pire que ce qu'elle prévient (#264).
        row["reason"] = f"lecture impossible : {exc}"
        return row, None
    row["state"] = status.get("state", "unreadable")
    row["configuredBackend"] = status.get("configuredBackend")
    row["resolvedBackend"] = status.get("resolvedBackend")
    row["entries"] = status.get("entries")
    if status.get("state") == "uninitialized":
        row["reason"] = "projet non initialisé (pas de project-context.yaml)"
    elif status.get("error"):
        row["reason"] = status["error"]
    if status.get("available") and manager is not None:
        row["lexicalIndex"] = _lexical_index_state(manager, status)
        row["lastWrite"] = _last_write(manager, status)
    store_key = _store_key(cfg, project_root) if cfg is not None else None
    return row, store_key


def _total_entries(rows: list[tuple[dict[str, Any], tuple[str, str] | None]]) -> int:
    """Somme des entrées lues, chaque store (emplacement + collection) une
    seule fois. Une ligne sans identité connue compte pour elle-même."""
    seen: set[tuple[str, str]] = set()
    total = 0
    for row, key in rows:
        if not isinstance(row["entries"], int):
            continue
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        total += row["entries"]
    return total


def memory_overview(project_root: Path, requested: str | None) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/overview``.

    ``requested`` est la valeur brute du paramètre ``projects=`` : ``"all"``
    pour tout le registre, une liste ``slug1,slug2`` pour un sous-ensemble,
    ``None``/vide pour « ce projet » (celui que l'hôte sert déjà).
    """
    entries = _select_entries(requested, project_root)
    if entries:
        with ThreadPoolExecutor(
            max_workers=min(len(entries), _OVERVIEW_MAX_WORKERS),
            thread_name_prefix="grimoire-memory-overview",
        ) as pool:
            rows = list(pool.map(_project_row, entries))
    else:
        rows = []
    projects = [row for row, _ in rows]
    readable = [p for p in projects if p["state"] != "unreadable"]
    total_entries = _total_entries(rows)
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


def _score_kind(mgr: Any) -> str:
    """Ce que ``score`` mesure : ``rrf`` (rang fusionné), ``bm25`` (lexical) ou ``backend`` (brut)."""
    if mgr.prefers_hybrid:
        return "rrf"
    return "bm25" if type(mgr.backend).__name__ in {"LexicalMemoryBackend", "TantivyMemoryBackend"} else "backend"


def _search_project(
    entry: dict[str, str], query: str, limit: int, *, include_superseded: bool = False, include_stale: bool = False,
) -> dict[str, Any]:
    """Les résultats d'UN projet, classés par sa propre recherche, entrées des autres projets écartées.

    Dans un emplacement partagé, une entrée d'un autre projet n'est jamais
    rendue : elle n'est que comptée (``hiddenOther``), comme une entrée sans
    projet quand d'autres projets du registre lisent le même store.
    """
    slug = str(entry.get("slug", ""))
    name = str(entry.get("name", "")) or slug
    project_root, reason = _resolve_readable_root(entry)
    if project_root is None:
        return {"slug": slug, "name": name, "results": [], "reason": reason}
    cfg, mgr, reason = open_readonly(project_root)
    if mgr is None:
        return {"slug": slug, "name": name, "results": [], "reason": reason}
    name = name or str(cfg.project.name or project_root.name)
    owners = ownership(cfg, project_root)
    try:
        from grimoire.core.exceptions import GrimoireConfigError, GrimoireMemoryError
        from grimoire.memory.taxonomy import run_memory_search

        # Le double de la limite : des entrées d'autres projets seront écartées.
        found = run_memory_search(
            mgr, query, hybrid=mgr.prefers_hybrid, limit=limit * 2,
            include_superseded=include_superseded, include_stale=include_stale,
        )
    except (GrimoireConfigError, GrimoireMemoryError, ImportError, OSError, ValueError) as exc:
        return {"slug": slug, "name": name, "results": [], "reason": str(exc)}
    kept = []
    others: Counter[str] = Counter()
    unattributed = 0
    for item in found:
        visibility = owners.visibility(item)
        if visibility == "own":
            kept.append(item)
        elif visibility == "other":
            others[owner_of(item)] += 1
        else:
            unattributed += 1
    kind = _score_kind(mgr)
    recall = mgr.backend.recall
    journal = journal_index(project_root)
    results = [
        {
            **describe(item, slug=slug, name=name, recall=recall, journal=journal),
            "score": item.score, "scoreKind": kind, "projectRank": rank,
        }
        for rank, item in enumerate(kept[:limit], start=1)
    ]
    return {
        "slug": slug, "name": name, "results": results, "reason": None,
        "hiddenStale": int(getattr(mgr, "hidden_stale", 0) or 0),
        "hiddenOther": [{"name": n, "count": c} for n, c in sorted(others.items())],
        "hiddenUnattributed": unattributed,
        "sharedWith": list(owners.shared_with),
        "owner": owners.mine,
    }


def fuse_by_rank(per_project: list[dict[str, Any]], *, k: int = RRF_K) -> list[dict[str, Any]]:
    """Fusion des listes classées de chaque projet par rang réciproque (RRF).

    Les scores bruts des projets ne se comparent pas (cosinus, BM25, RRF
    local) : seul le rang de chaque résultat dans SA liste compte,
    ``1 / (k + rang)``. Égalité (même rang dans deux projets) : slug du projet,
    pour un ordre indépendant de celui du registre. Le score brut reste
    porté par le résultat, nommé par ``scoreKind``.
    """
    fused: list[dict[str, Any]] = []
    for project in per_project:
        for rank, result in enumerate(project["results"], start=1):
            fused.append({**result, "fleetScore": 1.0 / (k + rank)})
    fused.sort(key=lambda r: (-r["fleetScore"], str(r.get("projectSlug", ""))))
    return fused


def memory_search(
    project_root: Path,
    query: str | None,
    requested: str | None,
    *,
    limit: int = _DEFAULT_SEARCH_LIMIT,
    include_superseded: bool = False,
    include_stale: bool = False,
) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/search`` — lecture seule.

    Interroge la recherche lexicale/hybride de chaque projet sélectionné
    séparément — la même chaîne que ``grimoire memory search``
    (``MemoryManager.prefers_hybrid`` + ``run_memory_search``) — puis fusionne
    les LISTES par rang (:func:`fuse_by_rank`), chaque résultat étiqueté par
    ``projectSlug``. Aucun contenu ne migre d'un store à l'autre : un projet en
    échec est nommé dans ``projects[].reason``, jamais tu.
    """
    normalized_query = (query or "").strip()
    entries = _select_entries(requested, project_root)
    if not normalized_query:
        return {
            "schemaVersion": SEARCH_SCHEMA_VERSION,
            "query": normalized_query,
            "ranking": "rrf",
            "projects": [],
            "results": [],
            "count": 0,
        }
    per_project = [
        _search_project(e, normalized_query, limit, include_superseded=include_superseded, include_stale=include_stale)
        for e in entries
    ]
    merged = fuse_by_rank(per_project)
    return {
        "schemaVersion": SEARCH_SCHEMA_VERSION,
        "query": normalized_query,
        "ranking": "rrf",
        "rrfK": RRF_K,
        "projects": [
            {
                "slug": p["slug"], "name": p["name"], "reason": p["reason"],
                "count": len(p["results"]), "hiddenStale": p.get("hiddenStale", 0),
                "hiddenOther": p.get("hiddenOther", []),
                "hiddenUnattributed": p.get("hiddenUnattributed", 0),
                "sharedWith": p.get("sharedWith", []),
                "owner": p.get("owner", ""),
            }
            for p in per_project
        ],
        "results": merged,
        "count": len(merged),
    }
