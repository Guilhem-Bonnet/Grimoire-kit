"""Les points d'accès qu'une déclaration du registre désigne pour une clé d'API.

Deux lectures de la même déclaration (``anthropic_base_url``, ``base_url``, sinon l'adresse par défaut officielle) :

- :func:`binding_endpoints` : à la saisie de la clé (CLI, cockpit), ce que la clé approuve — les adresses déclarées
  et, pour un fournisseur documenté, son adresse officielle ;
- :func:`key_targets` : à l'usage, où l'appelant enverrait la clé d'après la déclaration d'aujourd'hui.

Les valeurs de clé ne passent jamais ici : seulement des adresses, comparées par le magasin
(:mod:`grimoire.providers.secrets`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.providers.registry import ProviderRegistryError, ProviderSpec, read_registry
from grimoire.providers.secrets import normalize_endpoint, official_endpoint

#: Ce que la vue montre à la place d'une adresse non admise (jamais l'adresse : elle peut porter des identifiants).
UNADMITTED = "(adresse non admise)"


def _declared(spec: ProviderSpec) -> tuple[str, ...]:
    return tuple(url.strip() for url in (spec.anthropic_base_url, spec.base_url) if url and url.strip())


def key_targets(spec: ProviderSpec) -> tuple[str, ...]:
    """Les adresses où la clé de *spec* partirait aujourd'hui : celles déclarées, sinon l'officielle (``()`` sinon)."""
    declared = _declared(spec)
    if declared:
        return declared
    official = official_endpoint(spec.id, spec.provider_type)
    return (official,) if official else ()


def _spec(root: Path, provider_id: str) -> ProviderSpec | None:
    try:
        return next((p for p in read_registry(root) if p.id == provider_id), None)
    except ProviderRegistryError:
        return None


def binding_endpoints(root: Path, provider_id: str) -> tuple[str, ...]:
    """Ce qu'approuve la clé saisie maintenant pour *provider_id* d'après le registre de *root*.

    Un registre illisible ou sans ce fournisseur ne déclare rien : seule l'adresse officielle (si elle existe) est liée.
    """
    spec = _spec(root, provider_id)
    declared = _declared(spec) if spec is not None else ()
    official = official_endpoint(provider_id, spec.provider_type if spec is not None else "")
    return (*declared, *((official,) if official else ()))


def binding_invocation(root: Path, provider_id: str) -> str:
    """Le gabarit de commande que la saisie approuve (``""`` : le registre n'en déclare pas)."""
    spec = _spec(root, provider_id)
    return (spec.invocation or "").strip() if spec is not None else ""


def binding_view(endpoints: tuple[str, ...], invocation: str) -> dict[str, Any]:
    """Ce qui serait (ou a été) lié, pour l'humain : adresses normalisées, jamais une adresse non admise telle quelle."""
    shown = dict.fromkeys(normalize_endpoint(url) or UNADMITTED for url in endpoints)
    return {"endpoints": list(shown), "invocation": invocation}


def spec_binding_view(spec: ProviderSpec) -> dict[str, Any]:
    """:func:`binding_view` de ce que la saisie lierait pour *spec* (lecture seule, sans clé)."""
    official = official_endpoint(spec.id, spec.provider_type)
    return binding_view((*_declared(spec), *((official,) if official else ())), (spec.invocation or "").strip())
