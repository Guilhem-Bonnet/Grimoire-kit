"""Entretien du registre des projets depuis le cockpit (P35) : ajouter, scanner, retirer, purger.

Le registre est l'état de la *machine* (``~/.grimoire/cockpit/registry.json``),
pas un dépôt : le peupler ou le nettoyer n'est pas une écriture dans un projet,
c'est ce que font déjà ``grimoire cockpit add|scan|remove|prune``. Les gardes
de transport (jeton de session, ``Origin``, ``Sec-Fetch-Site``, JSON) sont
posées par l'hôte avant l'appel (``cmd_cockpit._guard_mutation``).

Deux règles propres à ces routes :

- retirer et purger ne touchent **jamais** au dossier d'un projet : seule
  l'entrée du registre disparaît. Sans ``confirm: true``, la route rend un
  aperçu et n'écrit rien ;
- une valeur venue de la requête ne devient jamais un chemin pour retirer :
  le slug sert de clé de recherche dans le registre. Le projet que ce cockpit
  sert ne se retire pas d'ici, sinon toutes ses écritures tomberaient en 404.

Le module rend ``(statut HTTP, corps JSON)`` et ne connaît pas le serveur.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.tools.project_registry import (
    DEFAULT_SCAN_DEPTH,
    classify_registry,
    forget_selection_if_gone,
    load_registry,
    looks_grimoire,
    register_project,
    resolve_within_allowed,
    save_registry,
    scan_payload,
    slug_for_path,
)

__all__ = ["FOLDER_KEPT", "REGISTRY_ROUTES", "registry_post"]

#: Les routes d'entretien du registre, toutes en ``POST``.
REGISTRY_ROUTES = ("/api/projects/add", "/api/projects/scan", "/api/projects/remove", "/api/projects/prune")

#: Ce que l'écran répète à chaque retrait : le dossier reste sur le disque.
FOLDER_KEPT = "Le dossier du projet reste sur le disque : seule l'entrée du registre du cockpit est retirée."

_Reply = tuple[int, dict[str, Any]]


def _entry_view(entry: dict[str, str]) -> dict[str, Any]:
    raw = str(entry.get("path", ""))
    return {
        "slug": str(entry.get("slug", "")),
        "name": str(entry.get("name", "")),
        "path": raw,
        "exists": bool(raw) and Path(raw).is_dir(),
    }


def _is_served(entry: dict[str, str], served: Path | None) -> bool:
    raw = str(entry.get("path", "")).strip()
    if served is None or not raw:
        return False
    try:
        return Path(raw).samefile(served)
    except OSError:
        return False


def _add(body: dict[str, Any]) -> _Reply:
    added_root = resolve_within_allowed(str(body.get("path", "")))
    if not added_root.is_dir():
        return 404, {"ok": False, "error": f"pas un dossier : {added_root}"}
    fresh = register_project(added_root)
    return 200, {
        "slug": fresh or slug_for_path(added_root), "path": str(added_root), "added": True,
        "already": fresh is None, "is_grimoire": looks_grimoire(added_root),
    }


def _scan(body: dict[str, Any]) -> _Reply:
    try:
        depth = int(body.get("depth", DEFAULT_SCAN_DEPTH))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "profondeur invalide"}
    return 200, scan_payload(str(body.get("root", "")), depth)


def _remove(body: dict[str, Any], served: Path | None) -> _Reply:
    slug = str(body.get("slug", "")).strip()
    projects = load_registry()
    entry = next((p for p in projects if slug and p.get("slug") == slug), None)
    if entry is None:
        return 404, {"ok": False, "error": f"projet inconnu au registre : {slug or '(vide)'}"}
    if _is_served(entry, served):
        return 409, {
            "ok": False,
            "error": "c'est le projet que ce cockpit sert : retire-le depuis la CLI (grimoire cockpit remove).",
        }
    if body.get("confirm") is not True:
        return 200, {"preview": True, "entry": _entry_view(entry), "note": FOLDER_KEPT}
    kept = [p for p in projects if p is not entry]
    save_registry(kept)
    forget_selection_if_gone(kept)
    return 200, {"removed": _entry_view(entry), "remaining": len(kept), "note": FOLDER_KEPT}


def _prune(body: dict[str, Any], served: Path | None) -> _Reply:
    projects = load_registry()
    keep, drop = classify_registry(projects, stale=body.get("stale") is True)
    keep += [p for p in drop if _is_served(p, served)]
    drop = [p for p in drop if not _is_served(p, served)]
    if body.get("confirm") is not True:
        return 200, {
            "preview": True, "total": len(projects),
            "candidates": [_entry_view(p) for p in drop], "note": FOLDER_KEPT,
        }
    wanted = body.get("slugs")
    if isinstance(wanted, list):
        # Ce que l'aperçu a montré, et rien de plus : une entrée devenue morte
        # entre l'aperçu et la confirmation attend le prochain aperçu.
        chosen = {str(s) for s in wanted}
        keep += [p for p in drop if str(p.get("slug", "")) not in chosen]
        drop = [p for p in drop if str(p.get("slug", "")) in chosen]
    if not drop:
        return 200, {"removed": [], "remaining": len(projects), "note": FOLDER_KEPT}
    gone = {id(p) for p in drop}
    kept = [p for p in projects if id(p) not in gone]
    save_registry(kept)
    forget_selection_if_gone(kept)
    return 200, {"removed": [_entry_view(p) for p in drop], "remaining": len(kept), "note": FOLDER_KEPT}


def registry_post(path: str, body: dict[str, Any], *, served: Path | None) -> _Reply:
    """Exécute une route de :data:`REGISTRY_ROUTES` ; ``served`` est le projet servi (garde P02)."""
    try:
        if path.endswith("/add"):
            return _add(body)
        if path.endswith("/scan"):
            return _scan(body)
        if path.endswith("/remove"):
            return _remove(body, served)
        if path.endswith("/prune"):
            return _prune(body, served)
    except FileNotFoundError as exc:
        return 404, {"ok": False, "error": str(exc)}
    except OSError as exc:  # PermissionError comprise : chemin hors des racines permises
        return 403, {"ok": False, "error": str(exc)}
    return 404, {"ok": False, "error": "route inconnue"}
