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

from grimoire.providers.registry import ProviderRegistryError, ProviderSpec, read_registry
from grimoire.providers.secrets import official_endpoint


def _declared(spec: ProviderSpec) -> tuple[str, ...]:
    return tuple(url.strip() for url in (spec.anthropic_base_url, spec.base_url) if url and url.strip())


def key_targets(spec: ProviderSpec) -> tuple[str, ...]:
    """Les adresses où la clé de *spec* partirait aujourd'hui : celles déclarées, sinon l'officielle (``()`` sinon)."""
    declared = _declared(spec)
    if declared:
        return declared
    official = official_endpoint(spec.id, spec.provider_type)
    return (official,) if official else ()


def binding_endpoints(root: Path, provider_id: str) -> tuple[str, ...]:
    """Ce qu'approuve la clé saisie maintenant pour *provider_id* d'après le registre de *root*.

    Un registre illisible ou sans ce fournisseur ne déclare rien : seule l'adresse officielle (si elle existe) est liée.
    """
    try:
        spec = next((p for p in read_registry(root) if p.id == provider_id), None)
    except ProviderRegistryError:
        spec = None
    declared = _declared(spec) if spec is not None else ()
    official = official_endpoint(provider_id, spec.provider_type if spec is not None else "")
    return (*declared, *((official,) if official else ()))
