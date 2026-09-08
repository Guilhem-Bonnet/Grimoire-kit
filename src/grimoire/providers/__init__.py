"""Routage par palier de coût entre fournisseurs LLM (issue #310, lot 2).

Trois responsabilités, trois modules : ``registry`` lit le déclaratif
(``llm-provider-registry.yaml``, étendu par ce lot avec ``currency``,
``invocation`` et ``models``/``tier``) ; ``state`` porte le refroidissement
après échec (``_grimoire-output/providers-state.json``) ; ``routing``
combine les deux pour répondre à une seule question : quel fournisseur
appeler maintenant pour ce palier ?
"""

from __future__ import annotations

from grimoire.providers.registry import (
    REGISTRY_FILE,
    SUPPORTED_CURRENCIES,
    SUPPORTED_MODEL_TIERS,
    ModelSpec,
    ProviderRegistryError,
    ProviderSpec,
    read_default_fallback_chain,
    read_registry,
)
from grimoire.providers.routing import choose
from grimoire.providers.state import (
    PROVIDERS_STATE_FILE,
    ProviderRuntimeState,
    load_state,
    record_failure,
    record_success,
    save_state,
)

__all__ = [
    "PROVIDERS_STATE_FILE",
    "REGISTRY_FILE",
    "SUPPORTED_CURRENCIES",
    "SUPPORTED_MODEL_TIERS",
    "ModelSpec",
    "ProviderRegistryError",
    "ProviderRuntimeState",
    "ProviderSpec",
    "choose",
    "load_state",
    "read_default_fallback_chain",
    "read_registry",
    "record_failure",
    "record_success",
    "save_state",
]
