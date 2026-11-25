"""Routage par palier de coût entre fournisseurs LLM (issue #310, lot 2).

Quatre responsabilités, quatre modules : ``registry`` lit le déclaratif
(``llm-provider-registry.yaml``, étendu par ce lot avec ``currency``,
``invocation`` et ``models``/``tier``) ; ``state`` porte le refroidissement
après échec et le résultat d'audit (``_grimoire-output/providers-state.json``) ;
``routing`` combine les deux pour répondre à une seule question : quel
fournisseur appeler maintenant pour ce palier ? ``audit`` (issue #330) sonde
sans dépenser — PATH, ``--version``, modèles Ollama — pour nourrir cet état.
``capabilities`` et ``capability_probe`` (#1281, lot L3) décrivent ce que chaque
fournisseur et modèle sait faire, avec la provenance de chaque valeur.
"""

from __future__ import annotations

from grimoire.providers.audit import ProbeResult, audit_providers, probe_provider
from grimoire.providers.capabilities import Capability, CapabilityProbe, ModelCapabilities, ProviderCapabilities
from grimoire.providers.capability_probe import probe_capabilities, read_capabilities
from grimoire.providers.quota import QuotaObservation, QuotaSignal, classify_failure, observe_window
from grimoire.providers.registry import (
    REGISTRY_FILE,
    SUPPORTED_CURRENCIES,
    SUPPORTED_MODEL_TIERS,
    ModelPricing,
    ModelSpec,
    ProviderRegistryError,
    ProviderSpec,
    read_default_fallback_chain,
    read_model_pricing,
    read_registry,
)
from grimoire.providers.routing import choose
from grimoire.providers.state import (
    PROVIDERS_STATE_FILE,
    ProviderRuntimeState,
    Suspension,
    available_models,
    clear_unknown_suspensions,
    cooling_down_entry,
    load_state,
    record_failure,
    record_quota_observation,
    record_success,
    record_suspension,
    save_state,
)

__all__ = [
    "PROVIDERS_STATE_FILE",
    "REGISTRY_FILE",
    "SUPPORTED_CURRENCIES",
    "SUPPORTED_MODEL_TIERS",
    "Capability",
    "CapabilityProbe",
    "ModelCapabilities",
    "ModelPricing",
    "ModelSpec",
    "ProbeResult",
    "ProviderCapabilities",
    "ProviderRegistryError",
    "ProviderRuntimeState",
    "ProviderSpec",
    "QuotaObservation",
    "QuotaSignal",
    "Suspension",
    "audit_providers",
    "available_models",
    "choose",
    "classify_failure",
    "clear_unknown_suspensions",
    "cooling_down_entry",
    "load_state",
    "observe_window",
    "probe_capabilities",
    "probe_provider",
    "read_capabilities",
    "read_default_fallback_chain",
    "read_model_pricing",
    "read_registry",
    "record_failure",
    "record_quota_observation",
    "record_success",
    "record_suspension",
    "save_state",
]
