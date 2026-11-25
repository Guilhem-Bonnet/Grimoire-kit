"""Sonder les capacités d'un fournisseur sans dépenser, et les relire (épique #1281, lot L3).

``grimoire providers probe --capabilities`` interroge, pour chaque fournisseur activé, ce que son
API donne sans génération : la liste des modèles (``GET /models``) et, quand elle les porte, les
métadonnées de contexte, de vision, de raisonnement ou de sortie structurée. Pour un fournisseur
local (Ollama), ``GET /api/tags`` puis ``POST /api/show`` par modèle déclaré ; pour une passerelle
LiteLLM (``provider_type: litellm``), ``GET <base_url>/v1/models`` puis, s'il répond,
``GET <base_url>/v1/model/info``. Aucun appel de
génération n'existe ici : une capacité que seule une génération prouverait reste documentée ou
inconnue.

Sans clé, sans réseau, ou sur une réponse illisible, la sonde rend « non mesuré » avec son motif,
jamais une erreur fatale. Pour un fournisseur documenté, la clé est lue dans le magasin de secrets
(:func:`grimoire.providers.secrets.env_for_provider`) puis dans l'environnement ; pour tout autre
fournisseur, dont l'hôte vient du registre, dans le magasin seulement, sous un nom qui ne peut pas
être celui d'un fournisseur documenté. Elle part en en-tête seulement, jamais après une
redirection : elle n'apparaît ni dans l'URL, ni dans les notes, ni dans le fichier écrit. Les
réponses sont bornées (taille, nombre de modèles, forme des identifiants).

Le résultat est écrit dans ``_grimoire-output/providers-capabilities.json`` (état runtime, jamais
le registre). :func:`read_capabilities` fusionne registre, dernière sonde et doc : c'est la
lecture que les lots L4/L5 consommeront.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlsplit

from grimoire.core.fsio import atomic_write
from grimoire.providers.audit import ollama_base_url
from grimoire.providers.capabilities import (
    DOCUMENTED_DEFAULTS,
    LITELLM,
    PROVIDER_ALIASES,
    CapabilityPairs,
    CapabilityProbe,
    ProbedModel,
    ProviderCapabilities,
    canonical_provider,
    coerce_capability,
    resolve_capabilities,
)
from grimoire.providers.registry import ProviderSpec, read_registry
from grimoire.providers.secrets import env_for_provider, is_valid_provider_id, provider_key_var

PROVIDERS_CAPABILITIES_FILE = Path("_grimoire-output/providers-capabilities.json")
_SCHEMA = "grimoire-provider-capabilities/v1"
_HTTP_TIMEOUT_S = 5.0
_ANTHROPIC_VERSION = "2023-06-01"
#: Bornes du contenu sondé : au-delà, la réponse est refusée ou tronquée, jamais lue en entier.
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_MODELS = 2000
#: Forme admise d'un identifiant de modèle lu sur le réseau ou relu du fichier : ni séquence de
#: contrôle, ni balisage, rien qui s'afficherait autrement qu'écrit.
_MODEL_ID_RE = re.compile(r"[A-Za-z0-9._:/@+-]{1,128}", re.ASCII)


@dataclass(frozen=True, slots=True)
class _Endpoint:
    """Où lister les modèles d'un fournisseur hébergé, et comment s'y authentifier."""

    url: str
    key_var: str
    auth: str  # "bearer" | "anthropic" | "google"
    #: Hôte lu dans le registre : la clé vient du magasin seulement, jamais de l'environnement.
    store_only: bool = False


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
#: Hôtes d'une boucle locale : seule adresse où une passerelle peut être jointe en ``http://``.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: ``model_info`` de ``/model/info`` (docs.litellm.ai/docs/proxy/model_discovery, providers/ollama,
#: completion/vision, lues le 2026-11-22) : seuls ces champs y sont documentés. La doc prévient qu'un
#: champ non renseigné vaut ``null`` : seul ``true`` est lu, jamais un ``false`` qui ne dirait rien.
_LITELLM_FLAGS = (("supports_function_calling", "tools"), ("supports_vision", "vision"))


class _ProbeError(Exception):
    """Motif lisible d'une sonde non mesurée — jamais le texte brut d'une exception réseau."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Aucune redirection suivie : urllib y recopierait les en-têtes d'authentification."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # la 3xx remonte en HTTPError, refusée par _http_json


#: Ouvreur de la sonde : les gestionnaires par défaut, sauf le suivi des redirections.
_OPENER = urllib.request.build_opener(_NoRedirect())


def _key_value(provider_id: str, endpoint: _Endpoint) -> str:
    """La clé de *provider_id* ; vide si absente.

    Fournisseur documenté : magasin de secrets, puis environnement. Hôte venu du registre :
    magasin seulement — l'environnement porte les clés d'autres fournisseurs.
    """
    stored = env_for_provider(provider_id).get(endpoint.key_var, "")
    if endpoint.store_only:
        return stored.strip()
    return (stored or os.environ.get(endpoint.key_var, "")).strip()


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
        with _OPENER.open(request, timeout=_HTTP_TIMEOUT_S) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise _ProbeError(f"redirection refusée (HTTP {exc.code})") from None
        reason = "clé refusée" if exc.code in {401, 403} else "réponse en erreur"
        raise _ProbeError(f"{reason} (HTTP {exc.code})") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise _ProbeError("injoignable (réseau ou délai dépassé)") from None
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise _ProbeError("réponse trop volumineuse (plus de 2 Mio)")
    try:
        return json.loads(raw.decode("utf-8"))
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
    if not _valid_model_id(raw_id):
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


def _valid_model_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and _MODEL_ID_RE.fullmatch(value) is not None


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
    parsed = (_parse_model_entry(entry) for entry in entries[:_MAX_MODELS] if isinstance(entry, Mapping))
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


def admitted_gateway_url(raw: str | None) -> str:
    """La racine admise d'une passerelle, sans ``/`` final ni ``/v1`` ; vide si elle n'est pas admise.

    ``https://`` partout, ``http://`` vers une boucle locale seulement ; ni identifiants, ni requête, ni
    fragment dans l'URL : la clé part en en-tête, et rien d'autre ne l'accompagne.
    """
    url = (raw or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    scheme = parts.scheme.lower()
    if not host or not port_ok or parts.username or parts.password or parts.query or parts.fragment:
        return ""
    if scheme != "https" and not (scheme == "http" and host in _LOOPBACK_HOSTS):
        return ""
    return url.removesuffix("/v1")


def _litellm_info(base: str, headers: Mapping[str, str]) -> dict[str, CapabilityPairs]:
    """Capacités par modèle lues sur ``/v1/model/info`` ; ``{}`` si le proxy la refuse (clé virtuelle).

    La doc ne garantit cette route qu'avec la clé maître : un refus ou une réponse illisible n'empêche pas
    la sonde, il prive seulement les modèles de leur détail.
    """
    try:
        payload = _http_json(f"{base}/v1/model/info", headers)
    except _ProbeError:
        return {}
    entries = payload.get("data") if isinstance(payload, Mapping) else None
    found: dict[str, CapabilityPairs] = {}
    for entry in entries[:_MAX_MODELS] if isinstance(entries, list) else ():
        name = entry.get("model_name") if isinstance(entry, Mapping) else None
        info = _mapping(entry.get("model_info")) if isinstance(entry, Mapping) else {}
        if not _valid_model_id(name) or name in found:
            continue
        values: dict[str, Any] = {"context_window": info.get("max_input_tokens")}
        values.update({cap: True for key, cap in _LITELLM_FLAGS if info.get(key) is True})
        found[name] = _pairs(values)
    return found


def _names_documented_provider(provider_id: str) -> bool:
    """*provider_id* désigne-t-il, ou est-il l'alias d'un fournisseur documenté par le kit ?

    Calculé sans ``provider_type`` : déclarer ``litellm`` ne change pas ce que l'id nomme, et la clé
    rangée sous ce nom est celle du fournisseur documenté, pas celle d'une passerelle.
    """
    lowered = provider_id.strip().lower()
    canonical = canonical_provider(provider_id)
    if canonical == LITELLM:
        return False
    return lowered in PROVIDER_ALIASES or canonical in DOCUMENTED_DEFAULTS or canonical in _ENDPOINTS


def _merged(listed: CapabilityPairs, detail: CapabilityPairs) -> CapabilityPairs:
    """Les valeurs de ``/models``, complétées par celles de ``/model/info`` pour ce qu'elle seule porte."""
    known = dict(listed)
    return (*listed, *((name, value) for name, value in detail if name not in known))


def _probe_litellm(provider: ProviderSpec, probed_at: str) -> CapabilityProbe:
    """Sonde d'une passerelle LiteLLM : ``/v1/models`` (liste), puis ``/v1/model/info`` (détail)."""
    if _names_documented_provider(provider.id):
        note = "non mesuré : id d'un fournisseur documenté, refusé pour une passerelle (choisir un autre id)"
        return CapabilityProbe(provider.id, False, note, probed_at)
    base = admitted_gateway_url(provider.base_url)
    if not base:
        note = "non mesuré : base_url absente ou non admise (https://, ou http:// vers une boucle locale)"
        return CapabilityProbe(provider.id, False, note, probed_at)
    # Les variables réservées (`_reserved_key_vars`) sont toutes celles d'un id refusé ci-dessus.
    endpoint = _Endpoint(f"{base}/v1/models", provider_key_var(provider.id), "bearer", store_only=True)
    key = _key_value(provider.id, endpoint)
    if not key:
        note = f"non mesuré : clé absente ({endpoint.key_var}, voir `grimoire providers set-key`)"
        return CapabilityProbe(provider.id, False, note, probed_at, endpoint.url)
    headers = _auth_headers(endpoint, key)
    try:
        listed = _parse_models_payload(_http_json(endpoint.url, headers))
    except _ProbeError as exc:
        return CapabilityProbe(provider.id, False, f"non mesuré : LiteLLM {exc}", probed_at, endpoint.url)
    info = _litellm_info(base, headers)
    models = tuple(ProbedModel(m.model_id, _merged(m.values, info.get(m.model_id, ()))) for m in listed)
    detail = f", détail de {sum(m.model_id in info for m in listed)} par /model/info" if info else ""
    return CapabilityProbe(
        provider.id, True, f"{len(models)} modèle(s) listé(s){detail}", probed_at, endpoint.url, models
    )


def _hosted_endpoint(provider: ProviderSpec) -> _Endpoint | None:
    endpoint = _ENDPOINTS.get(canonical_provider(provider.id, provider.provider_type))
    if endpoint is not None:
        return endpoint
    base = (provider.anthropic_base_url or "").strip().rstrip("/")
    if base.startswith("https://"):
        return _Endpoint(f"{base}/v1/models", provider_key_var(provider.id), "anthropic", store_only=True)
    return None


def _reserved_key_vars() -> frozenset[str]:
    """Variables des fournisseurs documentés : un hôte venu du registre n'en reçoit jamais une."""
    return frozenset(endpoint.key_var for endpoint in _ENDPOINTS.values())


def probe_provider_capabilities(provider: ProviderSpec, *, now: datetime | None = None) -> CapabilityProbe:
    """Sonde *provider* sans génération ; « non mesuré » (``measured=False``) plutôt qu'une exception."""
    probed_at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    if not is_valid_provider_id(provider.id):
        return CapabilityProbe(provider.id, False, "non mesuré : id invalide ([a-z0-9][a-z0-9-]{0,63})", probed_at)
    if canonical_provider(provider.id, provider.provider_type) == "local":
        try:
            url, models = _probe_local(provider)
        except _ProbeError as exc:
            return CapabilityProbe(provider.id, False, f"non mesuré : Ollama {exc}", probed_at, ollama_base_url())
        return CapabilityProbe(provider.id, True, f"{len(models)} modèle(s) listé(s)", probed_at, url, models)
    if canonical_provider(provider.id, provider.provider_type) == LITELLM:
        return _probe_litellm(provider, probed_at)
    endpoint = _hosted_endpoint(provider)
    if endpoint is None:
        return CapabilityProbe(provider.id, False, "non mesuré : aucun point d'entrée connu", probed_at)
    if endpoint.store_only and endpoint.key_var in _reserved_key_vars():
        note = f"non mesuré : variable de clé réservée à un autre fournisseur ({endpoint.key_var})"
        return CapabilityProbe(provider.id, False, note, probed_at, endpoint.url)
    key = _key_value(provider.id, endpoint)
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
        for model_id, values in list(models_raw.items())[:_MAX_MODELS]
        if isinstance(values, Mapping) and _valid_model_id(model_id)
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
    probes = {str(pid): _probe_from_dict(str(pid), raw) for pid, raw in providers.items() if is_valid_provider_id(pid)}
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
