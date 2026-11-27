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

import contextlib
from pathlib import Path
from typing import Any

PREFIX = "/api/workspace/"


def providers_write_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/providers`` — add/enable/disable/remove un fournisseur."""
    from grimoire.providers.registry import (
        ModelSpec,
        ProviderRegistryError,
        add_provider,
        remove_provider,
        set_provider_enabled,
    )
    from grimoire.tools import workspace_config
    from grimoire.tools.human_gesture import require_human_key
    from grimoire.tools.project_registry import body_project_refusal

    refusal = body_project_refusal(project_root, body)
    if refusal is not None:
        raise ValueError(refusal)
    require_human_key(project_root, body, "clé de contrôle absente ou invalide : modifier les fournisseurs est un geste d'humain")
    action = str(body.get("action") or "")
    if action == "add-local":
        return _add_local_models(project_root, body)
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
    from grimoire.providers.secrets import set_secret
    from grimoire.tools.human_gesture import require_human_key
    from grimoire.tools.project_registry import body_project_refusal

    refusal = body_project_refusal(project_root, body)
    if refusal is not None:
        raise ValueError(refusal)
    require_human_key(project_root, body, "clé de contrôle absente ou invalide : poser une clé API est un geste d'humain")
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


def _add_local_models(project_root: Path, body: dict[str, Any]) -> Any:
    """``action: add-local`` — déclare au registre des modèles qu'Ollama propose déjà (clé de contrôle déjà vérifiée).

    Seuls les noms que la détection locale voit sont acceptés : on n'inscrit pas un modèle qu'on ne voit pas. Le palier
    est déduit de la taille lue (``cheap`` à défaut : jamais plus fort qu'on ne le sait).
    """
    from grimoire.providers.availability import clear_cache, detect, tier_from_parameter_size
    from grimoire.providers.registry import ModelSpec, ProviderRegistryError, add_local_models
    from grimoire.tools import workspace_config

    wanted = body.get("models")
    if not isinstance(wanted, list) or not wanted or not all(isinstance(n, str) for n in wanted):
        raise ValueError("`models` : la liste des noms de modèles Ollama à ajouter est requise")
    from grimoire.providers.availability import local_ollama_entry

    target = local_ollama_entry(project_root)
    if not target["genuine"]:  # une entrée « ollama » qui n'est pas l'Ollama local ne reçoit rien : elle enverrait ailleurs
        raise ValueError(
            f"l'entrée « {target['provider_id']} » du registre n'est pas l'Ollama local ({target['reason']}) : "
            "ajout refusé, corrige ou retire cette entrée à la main"
        )
    seen = dict(detect(project_root, use_cache=False).ollama.models)
    unknown = [name for name in wanted if name not in seen]
    if unknown:
        raise ValueError(f"modèle absent d'Ollama (non détecté) : {unknown[0][:80]!r}")
    specs = tuple(
        ModelSpec(id=name, tier=tier_from_parameter_size(seen[name]) or "cheap") for name in dict.fromkeys(wanted)
    )
    try:
        added = add_local_models(project_root, specs)
    except ProviderRegistryError as exc:
        raise ValueError(str(exc)) from exc
    clear_cache()
    return {"action": "add-local", "added": list(added), "providers": workspace_config.providers_view(project_root)}


def direct_use_get(project_root: Path, _query: dict[str, list[str]]) -> Any:
    """``GET /api/workspace/providers/direct-use`` — ce qui est détecté, ce qui est autorisé, la décision de maintenant.

    Lecture seule : aucune requête réseau non locale, aucun appel de modèle (voir :mod:`grimoire.providers.availability`).
    """
    from grimoire.providers import direct_use_settings

    return direct_use_settings.view(project_root)


def direct_use_write_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/providers/direct-use`` — remplace la liste ordonnée des modèles autorisés d'UN usage.

    Même garde que ``POST /api/workspace/providers`` : garde de requête (origine, type de contenu, jeton de page,
    appliquée par l'hôte) PUIS clé de contrôle. Autoriser, réordonner, plafonner ou lever un plafond est un geste
    d'autorité de l'humain : le jeton de page seul n'y suffit pas, et un texte dicté est refusé.
    """
    from grimoire.providers import direct_use_settings
    from grimoire.providers.availability import detect
    from grimoire.tools.human_gesture import require_human_key
    from grimoire.tools.project_registry import body_project_refusal

    refusal = body_project_refusal(project_root, body)
    if refusal is not None:
        raise ValueError(refusal)
    require_human_key(
        project_root, body, "clé de contrôle absente ou invalide : autoriser un modèle est un geste d'humain"
    )
    models = body.get("models")
    if not isinstance(models, list):
        raise ValueError("`models` : la liste ordonnée des modèles autorisés est requise (vide pour tout retirer)")
    inventory = detect(project_root)
    try:
        direct_use_settings.write_usage(project_root, str(body.get("usage") or ""), models, inventory.by_key())
    except direct_use_settings.DirectUseError as exc:
        raise ValueError(str(exc)) from exc
    return direct_use_settings.view(project_root, inventory=inventory)


def direct_use_verify_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/providers/direct-use/verify`` — le geste « Vérifier maintenant » (ni écriture d'autorité).

    Relit seulement ce qui est local, sans cache : Ollama sur une adresse de la machine, commandes présentes dans le
    PATH, présence des clés. Aucune clé n'est transmise, aucun service distant n'est joint, rien n'est écrit dans le
    projet. Garde de requête de l'hôte ; pas de clé de contrôle. La vérification qui parle à des services est
    ``verify-remote``, sous la clé de contrôle.
    """
    from grimoire.providers import direct_use_settings
    from grimoire.providers.availability import clear_cache, detect
    from grimoire.tools.project_registry import body_project_refusal

    refusal = body_project_refusal(project_root, body)
    if refusal is not None:
        raise ValueError(refusal)
    clear_cache()
    return direct_use_settings.view(project_root, inventory=detect(project_root, use_cache=False))


def direct_use_verify_remote_route(project_root: Path, body: dict[str, Any]) -> Any:
    """``POST /api/workspace/providers/direct-use/verify-remote`` — la vérification qui parle à des services.

    Geste d'humain, sous la clé de contrôle (mêmes gardes que l'écriture des autorisations) : il envoie la clé d'API de
    chaque fournisseur listé par ``remote_endpoints`` à SON point d'accès (la page le dit avant de demander la clé) et
    lance la connexion des agents installés (``initialize``, sans modèle). Il ne change aucun réglage.
    """
    from grimoire.providers import direct_use_settings
    from grimoire.providers.availability import clear_cache, detect, probe_remote
    from grimoire.tools.human_gesture import require_human_key
    from grimoire.tools.project_registry import body_project_refusal

    refusal = body_project_refusal(project_root, body)
    if refusal is not None:
        raise ValueError(refusal)
    require_human_key(
        project_root, body, "clé de contrôle absente ou invalide : envoyer une clé à un service est un geste d'humain"
    )
    clear_cache()
    with contextlib.suppress(Exception):  # une sonde qui échoue se dit dans « non mesuré » ; la vérification continue
        probe_remote(project_root)
    return direct_use_settings.view(project_root, inventory=detect(project_root, verify=True))


GET_ROUTES = {
    f"{PREFIX}providers/direct-use": direct_use_get,
}

POST_ROUTES = {
    f"{PREFIX}providers": providers_write_route,
    f"{PREFIX}providers/set-key": providers_set_key_route,
    f"{PREFIX}providers/direct-use": direct_use_write_route,
    f"{PREFIX}providers/direct-use/verify": direct_use_verify_route,
    f"{PREFIX}providers/direct-use/verify-remote": direct_use_verify_remote_route,
}
