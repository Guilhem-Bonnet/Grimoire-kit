"""Écriture du registre de fournisseurs LLM depuis le cockpit (issue #310 suite).

Extrait de ``workspace_routes`` (ratchet R2, fichier source < 1500 lignes) pour
la même raison que ``workspace_notify``/``workspace_ideas`` vivent déjà à part :
une surface d'écriture isolée, mergée dans ``POST_ROUTES`` du module central.

Les deux routes exigent la clé de contrôle (:mod:`grimoire.missions.mandate`),
même geste de gouvernance que l'autopilote : éditer le registre change ce que
la cascade de dispatch peut dépenser, le jeton de session servi sur la
loopback n'y suffit pas — l'humain lit la clé depuis son terminal. La clé API
elle-même ne transite jamais par le registre du projet (``~/.env-store/``
seulement, hors du dépôt — voir :mod:`grimoire.providers.secrets`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PREFIX = "/api/workspace/"


def providers_write_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/providers`` — add/enable/disable/remove un fournisseur."""
    from grimoire.missions.mandate import verify_control_key
    from grimoire.providers.registry import (
        ModelSpec,
        ProviderRegistryError,
        add_provider,
        remove_provider,
        set_provider_enabled,
    )
    from grimoire.tools import workspace_config
    from grimoire.tools.project_registry import body_project_refusal

    refusal = body_project_refusal(project_root, body)
    if refusal is not None:
        raise ValueError(refusal)
    if not verify_control_key(body.get("control_key")):
        raise PermissionError("clé de contrôle absente ou invalide : modifier les fournisseurs est un geste d'humain")
    action = str(body.get("action") or "")
    provider_id = str(body.get("provider_id") or "")
    if not provider_id:
        raise ValueError("`provider_id` requis")
    try:
        if action == "add":
            models = tuple(
                ModelSpec(id=str(m["id"]), tier=str(m["tier"]))
                for m in (body.get("models") or [])
                if isinstance(m, dict) and m.get("id") and m.get("tier")
            )
            add_provider(
                project_root,
                provider_id,
                invocation=str(body.get("invocation") or ""),
                models=models,
                currency=str(body.get("currency") or "api"),
                enabled=bool(body.get("enabled", True)),
            )
        elif action == "enable":
            set_provider_enabled(project_root, provider_id, enabled=True)
        elif action == "disable":
            set_provider_enabled(project_root, provider_id, enabled=False)
        elif action == "remove":
            remove_provider(project_root, provider_id)
        else:
            raise ValueError(f"action {action!r} inconnue (add, enable, disable, remove)")
    except ProviderRegistryError as exc:
        raise ValueError(str(exc)) from exc
    return {"provider_id": provider_id, "action": action, "providers": workspace_config.providers_view(project_root)}


def providers_set_key_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/providers/set-key`` — pose une clé API en magasin, jamais dans le registre."""
    from grimoire.missions.mandate import verify_control_key
    from grimoire.providers.secrets import set_secret
    from grimoire.tools.project_registry import body_project_refusal

    refusal = body_project_refusal(project_root, body)
    if refusal is not None:
        raise ValueError(refusal)
    if not verify_control_key(body.get("control_key")):
        raise PermissionError("clé de contrôle absente ou invalide : poser une clé API est un geste d'humain")
    provider_id = str(body.get("provider_id") or "")
    env_var = str(body.get("env_var") or "")
    value = str(body.get("value") or "")
    if not provider_id or not env_var:
        raise ValueError("`provider_id` et `env_var` requis")
    try:
        path = set_secret(provider_id, env_var, value)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return {"provider_id": provider_id, "env_var": env_var, "file": str(path)}


POST_ROUTES = {
    f"{PREFIX}providers": providers_write_route,
    f"{PREFIX}providers/set-key": providers_set_key_route,
}
