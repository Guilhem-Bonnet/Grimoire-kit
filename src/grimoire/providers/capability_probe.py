"""Sonder les capacités d'un fournisseur sans dépenser, et les relire (épique #1281, lot L3).

``grimoire providers probe --capabilities`` interroge, pour chaque fournisseur activé, ce que son
API donne sans génération : la liste des modèles (``GET /models``) et, quand elle les porte, les
métadonnées de contexte, de vision, de raisonnement ou de sortie structurée. Pour un fournisseur
local (Ollama), ``GET /api/tags`` puis ``POST /api/show`` par modèle déclaré. Aucun appel de
génération n'existe ici : une capacité que seule une génération prouverait reste documentée ou
inconnue.

Sans clé, sans réseau, ou sur une réponse illisible, la sonde rend « non mesuré » avec son motif,
jamais une erreur fatale. La clé est lue dans le magasin de secrets
(:func:`grimoire.providers.secrets.env_for_provider`) puis dans l'environnement, envoyée en en-tête
seulement : elle n'apparaît ni dans l'URL, ni dans les notes, ni dans le fichier écrit.

Le résultat est écrit dans ``_grimoire-output/providers-capabilities.json`` (état runtime, jamais
le registre). :func:`read_capabilities` fusionne registre, dernière sonde et doc : c'est la
lecture que les lots L4/L5 consommeront.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.fsio import atomic_write
from grimoire.providers.audit import ollama_base_url
from grimoire.providers.capabilities import (
    CapabilityPairs,
    CapabilityProbe,
    ProbedModel,
    ProviderCapabilities,
    canonical_provider,
    coerce_capability,
    resolve_capabilities,
)
from grimoire.providers.registry import ProviderSpec, read_registry
from grimoire.providers.secrets import env_for_provider

PROVIDERS_CAPABILITIES_FILE = Path("_grimoire-output/providers-capabilities.json")
_SCHEMA = "grimoire-provider-capabilities/v1"
_HTTP_TIMEOUT_S = 5.0
_ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True, slots=True)
class _Endpoint:
    """Où lister les modèles d'un fournisseur hébergé, et comment s'y authentifier."""

    url: str
    key_var: str
    auth: str  # "bearer" | "anthropic" | "google"


#: Points d'entrée documentés de la liste des modèles (sources : DOCUMENTED_DEFAULTS dans ``capabilities``,
#: plus https://platform.claude.com/docs/en/api/models/list,
#: https://api-docs.deepseek.com/api/list-models et https://aistudio.google.com/api-reference/models).
_ENDPOINTS: Mapping[str, _Endpoint] = {
    "anthropic": _Endpoint("https://api.anthropic.com/v1/models?limit=1000", "ANTHROPIC_API_KEY", "anthropic"),
    "openai": _Endpoint("https://api.openai.com/v1/models", "OPENAI_API_KEY", "bearer"),
    "google-gemini": _Endpoint(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", "GEMINI_API_KEY", "google"
    ),
    "deepseek": _Endpoint("https://api.deepseek.com/models", "DEEPSEEK_API_KEY", "bearer"),
    "moonshot": _Endpoint("https://api.moonshot.ai/v1/models", "MOONSHOT_API_KEY", "bearer"),
}

#: Champs de contexte connus des listes de modèles (Anthropic, DeepSeek, Moonshot, Gemini, vLLM).
_CONTEXT_KEYS = ("max_input_tokens", "context_window", "context_length", "inputTokenLimit", "max_model_len")


class _ProbeError(Exception):
    """Motif lisible d'une sonde non mesurée — jamais le texte brut d'une exception réseau."""


def _key_value(provider_id: str, key_var: str) -> str:
    """La clé de *provider_id* : magasin de secrets d'abord, puis environnement ; vide si absente."""
    return (env_for_provider(provider_id).get(key_var) or os.environ.get(key_var, "")).strip()


def _auth_headers(endpoint: _Endpoint, key: str) -> dict[str, str]:
    if endpoint.auth == "anthropic":
        return {"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION}
    if endpoint.auth == "google":
        return {"x-goog-api-key": key}
    return {"Authorization": f"Bearer {key}"}


def _http_json(url: str, headers: Mapping[str, str], body: Mapping[str, Any] | None = None) -> Any:
    """``GET`` (ou ``POST`` JSON) borné à 5 s ; lève :class:`_ProbeError` avec un motif sans secret."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json", **headers}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST" if data else "GET")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:  # noqa: S310 — https ou boucle locale
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        reason = "clé refusée" if exc.code in {401, 403} else "réponse en erreur"
        raise _ProbeError(f"{reason} (HTTP {exc.code})") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise _ProbeError("injoignable (réseau ou délai dépassé)") from None
    except (ValueError, UnicodeDecodeError):
        raise _ProbeError("réponse illisible (JSON attendu)") from None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _supported(value: Any) -> Any:
    """``{"supported": bool}`` (Models API d'Anthropic) ou la valeur telle quelle."""
    return value.get("supported") if isinstance(value, Mapping) else value


def _parse_model_entry(entry: Mapping[str, Any]) -> ProbedModel | None:
    """Un modèle d'une liste ``/models`` : identifiant et capacités lisibles, rien de deviné."""
    raw_id = entry.get("id") or entry.get("name")
    if not isinstance(raw_id, str) or not raw_id:
        return None
    model_id = raw_id.removeprefix("models/")
    found: dict[str, Any] = {}
    for key in _CONTEXT_KEYS:
        if key in entry:
            found["context_window"] = entry[key]
            break
    caps = _mapping(entry.get("capabilities"))
    found["vision"] = _supported(caps.get("image_input")) if "image_input" in caps else entry.get("supports_image_in")
    modalities = entry.get("input_modalities")
    if found["vision"] is None and isinstance(modalities, list):
        found["vision"] = "image" in modalities
    found["thinking"] = next(
        (
            value
            for value in (_supported(caps.get("thinking")), entry.get("supports_reasoning"), entry.get("thinking"))
            if isinstance(value, bool)
        ),
        None,
    )
    found["structured_output"] = _supported(caps.get("structured_outputs"))
    return ProbedModel(model_id, _pairs(found))


def _pairs(found: Mapping[str, Any]) -> CapabilityPairs:
    pairs: list[tuple[str, bool | int]] = []
    for name, raw in found.items():
        value = coerce_capability(name, raw)
        if value is not None:
            pairs.append((name, value))
    return tuple(pairs)


def _parse_models_payload(payload: Any) -> tuple[ProbedModel, ...]:
    """``{"data": [...]}`` (OpenAI, Anthropic, DeepSeek, Moonshot) ou ``{"models": [...]}`` (Gemini)."""
    if not isinstance(payload, Mapping):
        raise _ProbeError("réponse illisible (liste de modèles attendue)")
    entries = payload.get("data", payload.get("models"))
    if not isinstance(entries, list):
        raise _ProbeError("réponse illisible (liste de modèles attendue)")
    parsed = (_parse_model_entry(entry) for entry in entries if isinstance(entry, Mapping))
    return tuple(model for model in parsed if model is not None)


def _ollama_show(base_url: str, model_id: str) -> ProbedModel:
    """``POST /api/show`` : ``capabilities`` (tools, vision, thinking) et ``<arch>.context_length``."""
    payload = _http_json(f"{base_url}/api/show", {}, {"model": model_id})
    if not isinstance(payload, Mapping):
        return ProbedModel(model_id)
    found: dict[str, Any] = {}
    capabilities = payload.get("capabilities")
    if isinstance(capabilities, list):
        found.update(
            {
                "tools": "tools" in capabilities,
                "vision": "vision" in capabilities,
                "thinking": "thinking" in capabilities,
            }
        )
    info = payload.get("model_info")
    if isinstance(info, Mapping):
        found["context_window"] = next(
            (value for key, value in info.items() if str(key).endswith(".context_length")), None
        )
    return ProbedModel(model_id, _pairs(found))


def _probe_local(provider: ProviderSpec) -> tuple[str, tuple[ProbedModel, ...]]:
    base_url = ollama_base_url()
    listed = _parse_models_payload(_http_json(f"{base_url}/api/tags", {}))
    names = {model.model_id for model in listed}
    shown: list[ProbedModel] = []
    for model in provider.models:
        name = model.id if model.id in names else f"{model.id}:latest"
        if name in names:
            try:
                shown.append(_ollama_show(base_url, name))
            except _ProbeError:
                continue  # le modèle reste listé, sans détail : rien n'est inventé
    extra = tuple(model for model in listed if model.model_id not in {entry.model_id for entry in shown})
    return f"{base_url}/api/tags", (*shown, *extra)


def _hosted_endpoint(provider: ProviderSpec) -> _Endpoint | None:
    endpoint = _ENDPOINTS.get(canonical_provider(provider.id, provider.provider_type))
    if endpoint is not None:
        return endpoint
    base = (provider.anthropic_base_url or "").strip().rstrip("/")
    if base.startswith("https://"):
        return _Endpoint(f"{base}/v1/models", f"{provider.id.upper().replace('-', '_')}_API_KEY", "anthropic")
    return None


def probe_provider_capabilities(provider: ProviderSpec, *, now: datetime | None = None) -> CapabilityProbe:
    """Sonde *provider* sans génération ; « non mesuré » (``measured=False``) plutôt qu'une exception."""
    probed_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    if canonical_provider(provider.id, provider.provider_type) == "local":
        try:
            url, models = _probe_local(provider)
        except _ProbeError as exc:
            return CapabilityProbe(provider.id, False, f"non mesuré : Ollama {exc}", probed_at, ollama_base_url())
        return CapabilityProbe(provider.id, True, f"{len(models)} modèle(s) listé(s)", probed_at, url, models)
    endpoint = _hosted_endpoint(provider)
    if endpoint is None:
        return CapabilityProbe(provider.id, False, "non mesuré : aucun point d'entrée connu", probed_at)
    key = _key_value(provider.id, endpoint.key_var)
    if not key:
        note = f"non mesuré : clé absente ({endpoint.key_var}, voir `grimoire providers set-key`)"
        return CapabilityProbe(provider.id, False, note, probed_at, endpoint.url)
    try:
        models = _parse_models_payload(_http_json(endpoint.url, _auth_headers(endpoint, key)))
    except _ProbeError as exc:
        return CapabilityProbe(provider.id, False, f"non mesuré : {exc}", probed_at, endpoint.url)
    return CapabilityProbe(provider.id, True, f"{len(models)} modèle(s) listé(s)", probed_at, endpoint.url, models)


def _probe_from_dict(provider_id: str, raw: Any) -> CapabilityProbe | None:
    if not isinstance(raw, Mapping):
        return None
    models_raw = _mapping(raw.get("models"))
    models = tuple(
        ProbedModel(str(model_id), _pairs(values))
        for model_id, values in models_raw.items()
        if isinstance(values, Mapping)
    )
    return CapabilityProbe(
        provider_id,
        raw.get("measured") is True,
        str(raw.get("note") or ""),
        str(raw.get("probed_at") or ""),
        str(raw.get("endpoint") or ""),
        models,
    )


def load_probes(root: Path) -> dict[str, CapabilityProbe]:
    """Dernières sondes par fournisseur ; ``{}`` si le fichier est absent ou illisible (rien de mesuré)."""
    path = root / PROVIDERS_CAPABILITIES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    providers = data.get("providers") if isinstance(data, Mapping) else None
    if not isinstance(providers, Mapping):
        return {}
    probes = {str(pid): _probe_from_dict(str(pid), raw) for pid, raw in providers.items()}
    return {pid: probe for pid, probe in probes.items() if probe is not None}


def save_probes(root: Path, probes: Mapping[str, CapabilityProbe]) -> None:
    path = root / PROVIDERS_CAPABILITIES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"$schema": _SCHEMA, "providers": {pid: probe.to_dict() for pid, probe in probes.items()}}
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def probe_capabilities(
    root: Path, provider_id: str | None = None, *, now: datetime | None = None
) -> tuple[CapabilityProbe, ...]:
    """Sonde les fournisseurs activés (ou le seul *provider_id*) et enregistre le résultat.

    Une sonde remplace la précédente du même fournisseur : une valeur qu'on ne mesure plus
    n'est pas gardée comme « sondée ». Les autres fournisseurs gardent leur dernière sonde.
    """
    targets = tuple(spec for spec in read_registry(root) if spec.enabled and provider_id in (None, spec.id))
    results = tuple(probe_provider_capabilities(spec, now=now) for spec in targets)
    probes = load_probes(root)
    probes.update({probe.provider_id: probe for probe in results})
    save_probes(root, probes)
    return results


def capabilities_for(provider: ProviderSpec, probe: CapabilityProbe | None) -> ProviderCapabilities:
    """Capacités résolues d'un fournisseur déjà lu, avec sa dernière sonde."""
    return resolve_capabilities(
        provider.id,
        provider_type=provider.provider_type,
        declared=provider.capabilities,
        models=tuple((model.id, model.capabilities) for model in provider.models),
        probe=probe,
    )


def read_capabilities(root: Path, provider_id: str | None = None) -> tuple[ProviderCapabilities, ...]:
    """Capacités typées de chaque fournisseur du registre (ou du seul *provider_id*).

    Lecture pure : registre + dernière sonde + doc, sans réseau. Lève
    :class:`~grimoire.providers.registry.ProviderRegistryError` sur un registre corrompu, comme
    :func:`~grimoire.providers.registry.read_registry`.
    """
    probes = load_probes(root)
    return tuple(
        capabilities_for(spec, probes.get(spec.id)) for spec in read_registry(root) if provider_id in (None, spec.id)
    )
