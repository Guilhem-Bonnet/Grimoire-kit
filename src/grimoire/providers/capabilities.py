"""Capacités d'un fournisseur et de ses modèles, avec leur provenance (épique #1281, lot L3).

Huit capacités utiles au dispatch et aux hôtes : deux au niveau de l'API du fournisseur
(``openai_compatible``, ``anthropic_compatible``) et six par modèle (``tools``,
``structured_output``, ``vision``, ``context_window``, ``thinking``, ``prompt_cache``).

Chaque valeur porte sa provenance, de la plus forte à la plus faible :

- ``declared`` : écrite par l'utilisateur sous ``capabilities:`` dans le registre, au niveau du
  fournisseur ou d'un modèle (le modèle l'emporte) ;
- ``probed`` : mesurée par ``grimoire providers probe --capabilities`` (liste des modèles du
  fournisseur, sans appel de génération) ;
- ``documented`` : valeur par défaut du kit, tirée de la doc du fournisseur dont l'URL est citée
  dans :data:`DOCUMENTED_DEFAULTS` ; elle décrit l'API, pas chaque modèle ;
- ``unknown`` : rien de ce qui précède. Une capacité absente vaut « inconnue », jamais « oui ».

Ce module est pur : il ne lit ni fichier ni réseau. La lecture du registre et de la dernière
sonde est dans :mod:`grimoire.providers.capability_probe` (:func:`read_capabilities`). Le routage
ne s'en sert pas encore (lot L5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Capacités de l'API du fournisseur, communes à ses modèles.
API_CAPABILITIES: tuple[str, ...] = ("openai_compatible", "anthropic_compatible")
#: Capacités par modèle.
MODEL_CAPABILITIES: tuple[str, ...] = (
    "tools",
    "structured_output",
    "vision",
    "context_window",
    "thinking",
    "prompt_cache",
)
ALL_CAPABILITIES: tuple[str, ...] = API_CAPABILITIES + MODEL_CAPABILITIES
#: Seule capacité numérique (tokens) ; les autres sont des booléens.
_INT_CAPABILITIES = frozenset({"context_window"})

DECLARED = "declared"
PROBED = "probed"
DOCUMENTED = "documented"
UNKNOWN = "unknown"
PROVENANCES: tuple[str, ...] = (DECLARED, PROBED, DOCUMENTED, UNKNOWN)

CapabilityValue = bool | int
CapabilityPairs = tuple[tuple[str, CapabilityValue], ...]

#: Identifiants du registre qui désignent le même fournisseur que ceux de :data:`DOCUMENTED_DEFAULTS`.
PROVIDER_ALIASES: Mapping[str, str] = {
    "claude": "anthropic",
    "codex": "openai",
    "gemini": "google-gemini",
    "google": "google-gemini",
    "kimi": "moonshot",
    "ollama": "local",
}

_DEEPSEEK_ANTHROPIC = "https://api-docs.deepseek.com/guides/anthropic_api"
_KIMI_AGENT = "https://platform.kimi.ai/docs/guide/agent-support"
_OLLAMA_ANTHROPIC = "https://docs.ollama.com/api/anthropic-compatibility"
#: Type de fournisseur d'une passerelle LiteLLM (#1281, L8) : ``provider_type: litellm`` + ``base_url``.
LITELLM = "litellm"

#: Valeurs documentées par fournisseur : ``{capacité: (valeur, URL de la doc)}``, lues le 2026-11-22.
#: Seules y figurent les capacités que la doc affirme pour l'API entière ; ce qui dépend du modèle
#: (vision, raisonnement, contexte) reste inconnu jusqu'à la sonde ou une déclaration.
DOCUMENTED_DEFAULTS: Mapping[str, Mapping[str, tuple[CapabilityValue, str]]] = {
    "anthropic": {
        "anthropic_compatible": (True, "https://platform.claude.com/docs/en/api/messages"),
        "openai_compatible": (True, "https://platform.claude.com/docs/en/api/openai-sdk"),
        "tools": (True, "https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview"),
        "prompt_cache": (True, "https://platform.claude.com/docs/en/build-with-claude/prompt-caching"),
    },
    "openai": {
        "openai_compatible": (True, "https://platform.openai.com/docs/api-reference/models/list"),
        "tools": (True, "https://platform.openai.com/docs/guides/function-calling"),
        "structured_output": (True, "https://platform.openai.com/docs/guides/structured-outputs"),
        "prompt_cache": (True, "https://platform.openai.com/docs/guides/prompt-caching"),
    },
    "google-gemini": {
        "openai_compatible": (True, "https://ai.google.dev/gemini-api/docs/openai"),
        "tools": (True, "https://ai.google.dev/gemini-api/docs/function-calling"),
        "structured_output": (True, "https://ai.google.dev/gemini-api/docs/structured-output"),
        "prompt_cache": (True, "https://ai.google.dev/gemini-api/docs/caching"),
    },
    "deepseek": {
        "openai_compatible": (True, "https://api-docs.deepseek.com/"),
        "anthropic_compatible": (True, _DEEPSEEK_ANTHROPIC),
        "tools": (True, "https://api-docs.deepseek.com/guides/function_calling"),
        "structured_output": (True, "https://api-docs.deepseek.com/guides/json_mode"),
        "thinking": (True, _DEEPSEEK_ANTHROPIC),
        "prompt_cache": (True, "https://api-docs.deepseek.com/guides/kv_cache"),
    },
    "moonshot": {
        "openai_compatible": (True, "https://platform.kimi.ai/docs/api/list-models"),
        "anthropic_compatible": (True, _KIMI_AGENT),
        "tools": (True, "https://platform.kimi.ai/docs/api/tool-use"),
        "structured_output": (True, _KIMI_AGENT),
    },
    "local": {
        "openai_compatible": (True, "https://docs.ollama.com/api/openai-compatibility"),
        "anthropic_compatible": (True, _OLLAMA_ANTHROPIC),
        "prompt_cache": (False, _OLLAMA_ANTHROPIC),
    },
    # Le proxy sert `/chat/completions` et `/models` au format OpenAI, et `/v1/messages` au format
    # Anthropic pour tous ses modèles (lu le 2026-11-22). Le reste dépend du modèle derrière le proxy.
    LITELLM: {
        "openai_compatible": (True, "https://docs.litellm.ai/docs/proxy/quick_start"),
        "anthropic_compatible": (True, "https://docs.litellm.ai/docs/proxy/client_setup/claude_code"),
    },
}


def canonical_provider(provider_id: str, provider_type: str = "") -> str:
    """L'identifiant de :data:`DOCUMENTED_DEFAULTS` qui décrit *provider_id* ; ``local`` pour tout fournisseur local."""
    if provider_type == "local":
        return "local"
    if provider_type == LITELLM:
        return LITELLM
    lowered = provider_id.strip().lower()
    return PROVIDER_ALIASES.get(lowered, lowered)


def coerce_capability(name: str, value: Any) -> CapabilityValue | None:
    """*value* validée pour *name*, ou ``None`` si elle est absente, inconnue ou du mauvais type.

    Un booléen n'est jamais un nombre de tokens ni l'inverse : ``context_window: true`` ou
    ``tools: 1`` ne disent rien, ils restent inconnus plutôt que d'être devinés.
    """
    if name in _INT_CAPABILITIES:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value
    return value if isinstance(value, bool) else None


def parse_capabilities(value: Any) -> CapabilityPairs:
    """Bloc ``capabilities:`` du registre en paires validées ; une clé ou une valeur invalide est ignorée."""
    if not isinstance(value, Mapping):
        return ()
    pairs: list[tuple[str, CapabilityValue]] = []
    for name in ALL_CAPABILITIES:
        coerced = coerce_capability(name, value.get(name))
        if coerced is not None:
            pairs.append((name, coerced))
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class Capability:
    """Une capacité résolue : valeur (``None`` = inconnue), provenance et source citée."""

    name: str
    value: CapabilityValue | None
    provenance: str
    source: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "provenance": self.provenance, "source": self.source or None}


@dataclass(frozen=True, slots=True)
class ProbedModel:
    """Ce que la sonde a lu pour un modèle (sans génération)."""

    model_id: str
    values: CapabilityPairs = ()


@dataclass(frozen=True, slots=True)
class CapabilityProbe:
    """Résultat d'une sonde de capacités ; ``measured`` faux = « non mesuré », avec son motif."""

    provider_id: str
    measured: bool
    note: str
    probed_at: str = ""
    endpoint: str = ""
    models: tuple[ProbedModel, ...] = ()

    def model(self, model_id: str) -> ProbedModel | None:
        """Le modèle sondé *model_id* (``nom`` et ``nom:latest`` d'Ollama se valent)."""
        for candidate in (model_id, f"{model_id}:latest"):
            for entry in self.models:
                if entry.model_id == candidate:
                    return entry
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "measured": self.measured,
            "note": self.note,
            "probed_at": self.probed_at or None,
            "endpoint": self.endpoint or None,
            "models": {entry.model_id: dict(entry.values) for entry in self.models},
        }


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Les six capacités d'un modèle, chacune avec sa provenance."""

    model_id: str
    tools: Capability
    structured_output: Capability
    vision: Capability
    context_window: Capability
    thinking: Capability
    prompt_cache: Capability

    def get(self, name: str) -> Capability:
        if name not in MODEL_CAPABILITIES:
            raise KeyError(name)
        capability: Capability = getattr(self, name)
        return capability

    def to_dict(self) -> dict[str, Any]:
        return {name: self.get(name).to_dict() for name in MODEL_CAPABILITIES}


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Capacités d'un fournisseur : API, défaut pour tout modèle, modèles déclarés et dernière sonde."""

    provider_id: str
    openai_compatible: Capability
    anthropic_compatible: Capability
    defaults: ModelCapabilities
    models: tuple[ModelCapabilities, ...] = ()
    probe: CapabilityProbe | None = None

    def model(self, model_id: str) -> ModelCapabilities:
        """Capacités de *model_id* ; un modèle non déclaré reçoit celles du fournisseur."""
        for entry in self.models:
            if entry.model_id == model_id:
                return entry
        return _with_model_id(self.defaults, model_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api": {
                "openai_compatible": self.openai_compatible.to_dict(),
                "anthropic_compatible": self.anthropic_compatible.to_dict(),
            },
            "defaults": self.defaults.to_dict(),
            "models": [{"id": entry.model_id, "capabilities": entry.to_dict()} for entry in self.models],
            "probe": self.probe.to_dict() if self.probe is not None else None,
        }


def _with_model_id(capabilities: ModelCapabilities, model_id: str) -> ModelCapabilities:
    return ModelCapabilities(
        model_id,
        *(capabilities.get(name) for name in MODEL_CAPABILITIES),
    )


def _resolve_one(
    name: str,
    declared_layers: tuple[CapabilityPairs, ...],
    probed: CapabilityPairs,
    probe_source: str,
    documented: Mapping[str, tuple[CapabilityValue, str]],
) -> Capability:
    for layer in declared_layers:
        value = dict(layer).get(name)
        if value is not None:
            return Capability(name, value, DECLARED, "llm-provider-registry.yaml")
    value = dict(probed).get(name)
    if value is not None:
        return Capability(name, value, PROBED, probe_source)
    if name in documented:
        doc_value, url = documented[name]
        return Capability(name, doc_value, DOCUMENTED, url)
    return Capability(name, None, UNKNOWN)


def _probe_source(probe: CapabilityProbe | None) -> str:
    if probe is None:
        return ""
    return f"{probe.endpoint} ({probe.probed_at})" if probe.probed_at else probe.endpoint


def _probed_values(probe: CapabilityProbe | None, model_id: str) -> CapabilityPairs:
    entry = probe.model(model_id) if probe is not None else None
    return entry.values if entry is not None else ()


def _model_capabilities(
    model_id: str,
    declared_layers: tuple[CapabilityPairs, ...],
    probed: CapabilityPairs,
    probe: CapabilityProbe | None,
    documented: Mapping[str, tuple[CapabilityValue, str]],
) -> ModelCapabilities:
    source = _probe_source(probe)
    return ModelCapabilities(
        model_id,
        *(_resolve_one(name, declared_layers, probed, source, documented) for name in MODEL_CAPABILITIES),
    )


def resolve_capabilities(
    provider_id: str,
    *,
    provider_type: str = "",
    declared: CapabilityPairs = (),
    models: tuple[tuple[str, CapabilityPairs], ...] = (),
    probe: CapabilityProbe | None = None,
) -> ProviderCapabilities:
    """Fusionne déclaration (modèle, puis fournisseur), sonde et doc, dans cet ordre de force.

    *declared* est le bloc ``capabilities:`` du fournisseur, *models* les couples
    ``(id, capabilities:)`` de ses modèles. Une sonde « non mesurée » n'apporte aucune valeur.
    """
    documented = DOCUMENTED_DEFAULTS.get(canonical_provider(provider_id, provider_type), {})
    usable_probe = probe if probe is not None and probe.measured else None
    source = _probe_source(usable_probe)
    api = {name: _resolve_one(name, (declared,), (), source, documented) for name in API_CAPABILITIES}
    resolved_models = tuple(
        _model_capabilities(
            model_id,
            (model_declared, declared),
            _probed_values(usable_probe, model_id),
            usable_probe,
            documented,
        )
        for model_id, model_declared in models
    )
    return ProviderCapabilities(
        provider_id=provider_id,
        openai_compatible=api["openai_compatible"],
        anthropic_compatible=api["anthropic_compatible"],
        defaults=_model_capabilities("", (declared,), (), None, documented),
        models=resolved_models,
        probe=probe,
    )
