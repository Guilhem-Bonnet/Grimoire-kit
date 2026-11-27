"""Ce qui est branché et disponible, en lecture seule (épique #1477, lot B1).

Un seul inventaire normalisé de tout ce que Grimoire pourrait utiliser en direct : les fournisseurs du registre
(activés ou non), les modèles locaux d'Ollama et les hôtes d'agent installés. Chaque ligne dit, dans les mêmes mots :

- l'état : ``disponible``, ou ``indisponible`` avec sa raison (``hors-ligne``, ``limite-atteinte``, ``non-installé``,
  ``clé-absente``, ``désactivé``) ;
- ce qui sort de la machine : ``rien`` (modèle local) ou ``texte`` (le texte envoyé au modèle) ;
- le coût : ``gratuit-local``, ``abonnement`` (forfait) ou ``payant`` (à l'usage) ; un coût inconnu compte comme payant ;
- les capacités connues avec leur provenance (déclarée, sondée, documentée), jamais devinées.

Garanties :

- **Lecture seule, sans génération.** Aucun appel de modèle, aucune écriture. Les sondes sont celles qui existent déjà
  (PATH, ``GET /api/tags`` d'Ollama, ``initialize`` ACP) et sont toutes bornées en temps.
- **Aucun réseau non local au chargement.** La seule requête réseau d'une détection simple est ``GET /api/tags`` sur une
  adresse de la machine (``127.0.0.1``, ``localhost``, ``::1``) : une adresse d'Ollama non locale n'est pas sondée.
  La sonde de capacités d'un service distant (:func:`grimoire.providers.capability_probe.probe_capabilities`) n'est
  jamais lancée ici ; elle n'est lancée que par le geste « Vérifier » de l'utilisateur.
- **Clés : présence seulement.** Jamais la valeur, jamais sa longueur : l'index du magasin ou l'environnement disent
  « présente » ou « absente ».
- **Réponses hostiles bornées.** Une réponse d'Ollama trop grosse, mal formée ou aux noms étranges est refusée ou nettoyée.
- **Cache court** : une détection est gardée :data:`CACHE_TTL_SECONDS` secondes ; « Vérifier » la refait.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from grimoire.providers.audit import ollama_base_url
from grimoire.providers.capabilities import MODEL_CAPABILITIES
from grimoire.providers.capability_probe import capabilities_for, load_probes
from grimoire.providers.registry import LOCAL_PROVIDER_ID, ProviderRegistryError, ProviderSpec, read_registry
from grimoire.providers.secrets import is_valid_provider_id, list_secrets, provider_key_var
from grimoire.providers.state import ProviderRuntimeState, load_state

__all__ = [
    "CACHE_TTL_SECONDS",
    "COSTS",
    "EGRESSES",
    "REASONS",
    "Inventory",
    "ModelAvailability",
    "OllamaScan",
    "clear_cache",
    "detect",
    "scan_ollama",
    "tier_from_parameter_size",
]

#: Combien de temps une détection est gardée avant d'être refaite (le bouton « Vérifier » la contourne).
CACHE_TTL_SECONDS = 20.0
#: Délai de ``GET /api/tags`` : un service local répond tout de suite, sinon il est tenu pour hors-ligne.
OLLAMA_TIMEOUT_S = 1.5
#: Une réponse d'Ollama plus grosse que cela est hostile ou cassée : refusée.
MAX_RESPONSE_BYTES = 262_144
MAX_OLLAMA_MODELS = 200
#: Un identifiant de modèle d'Ollama : mêmes caractères que la sonde de capacités.
_MODEL_ID_RE = re.compile(r"[A-Za-z0-9._:/@+-]{1,128}", re.ASCII)
_SIZE_RE = re.compile(r"(\d{1,4}(?:\.\d{1,2})?)\s*([MBT])", re.ASCII | re.IGNORECASE)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

REASONS: tuple[str, ...] = ("hors-ligne", "limite-atteinte", "non-installé", "clé-absente", "désactivé")
EGRESSES: tuple[str, ...] = ("rien", "texte")
COSTS: tuple[str, ...] = ("gratuit-local", "abonnement", "payant")

SOURCE_REGISTRY = "registre"
SOURCE_OLLAMA = "ollama"
SOURCE_HOST = "hote"


@dataclass(frozen=True, slots=True)
class ModelAvailability:
    """Une ligne de l'inventaire : un modèle (ou un hôte) que Grimoire pourrait utiliser, et son état maintenant."""

    key: str
    label: str
    source: str
    provider_id: str
    model_id: str
    origin: str
    tier: str
    tier_provenance: str
    provider_type: str
    currency: str
    configured: bool
    available: bool
    reason: str
    detail: str
    egress: str
    cost: str
    key_present: bool | None
    key_command: str
    cooldown_until: str | None
    capabilities: tuple[tuple[str, int | bool, str], ...]
    connected: bool | None = None
    install_hint: str = ""
    size_label: str = ""

    def capability(self, name: str) -> tuple[int | bool, str] | None:
        """``(valeur, provenance)`` d'une capacité connue, ``None`` si elle est inconnue."""
        for cap_name, value, provenance in self.capabilities:
            if cap_name == name:
                return value, provenance
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "origin": self.origin,
            "tier": self.tier or None,
            "tier_provenance": self.tier_provenance or None,
            "provider_type": self.provider_type,
            "currency": self.currency,
            "configured": self.configured,
            "state": "disponible" if self.available else "indisponible",
            "reason": self.reason or None,
            "detail": self.detail,
            "egress": self.egress,
            "cost": self.cost,
            "key_present": self.key_present,
            "key_command": self.key_command or None,
            "cooldown_until": self.cooldown_until,
            "connected": self.connected,
            "install_hint": self.install_hint or None,
            "size": self.size_label or None,
            "capabilities": [
                {"name": name, "value": value, "provenance": provenance} for name, value, provenance in self.capabilities
            ],
        }


@dataclass(frozen=True, slots=True)
class OllamaScan:
    """Ce que ``GET /api/tags`` a dit : joignable ou non, et les modèles lus (nettoyés, bornés)."""

    url: str
    reachable: bool
    models: tuple[tuple[str, str], ...] = ()  # (nom, taille en paramètres lue, ou "")
    note: str = ""


@dataclass(frozen=True, slots=True)
class Inventory:
    """Tout ce qui a été détecté, à un instant donné."""

    models: tuple[ModelAvailability, ...]
    ollama: OllamaScan
    registry_error: str = ""
    verified: bool = False
    at: str = ""

    def by_key(self) -> dict[str, ModelAvailability]:
        return {m.key: m for m in self.models}

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": [m.to_dict() for m in self.models],
            "ollama": {"url": self.ollama.url, "reachable": self.ollama.reachable, "note": self.ollama.note or None},
            "registry_error": self.registry_error or None,
            "verified": self.verified,
            "at": self.at,
        }


# ── Ollama (adresse locale seulement) ───────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


#: Aucun proxy d'environnement ni redirection : la requête ne part que vers l'adresse locale vérifiée.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

Fetch = Callable[[str, float, int], bytes]


def _is_loopback(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        _ = parts.port  # un port illisible lève ValueError : l'adresse est alors refusée
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and host in _LOOPBACK_HOSTS


def _fetch(url: str, timeout: float, limit: int) -> bytes:
    """``GET`` borné : au plus *limit* + 1 octets lus, sans redirection ni proxy."""
    request = urllib.request.Request(url, method="GET")  # noqa: S310 — adresse locale vérifiée par l'appelant
    with _OPENER.open(request, timeout=timeout) as response:
        return bytes(response.read(limit + 1))


def tier_from_parameter_size(value: str) -> str:
    """Le palier déduit de la taille en paramètres d'un modèle local (``3.2B`` -> ``cheap``) ; vide si elle est inconnue.

    Moins de 5 milliards : ``cheap`` ; moins de 20 milliards : ``mid`` ; au-delà : ``strong``. C'est une déduction
    (dite telle sur la page), jamais une mesure : un registre qui déclare le palier d'un modèle prime.
    """
    match = _SIZE_RE.fullmatch(value.strip())
    if match is None:
        return ""
    number = float(match.group(1))
    unit = match.group(2).upper()
    billions = number / 1000 if unit == "M" else number * 1000 if unit == "T" else number
    if billions < 5:
        return "cheap"
    return "mid" if billions < 20 else "strong"


def _clean_models(payload: Any) -> tuple[tuple[str, str], ...]:
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return ()
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in models[:MAX_OLLAMA_MODELS]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or _MODEL_ID_RE.fullmatch(name) is None or name in seen:
            continue
        details = entry.get("details")
        size = details.get("parameter_size") if isinstance(details, dict) else None
        seen.add(name)
        found.append((name, size.strip()[:16] if isinstance(size, str) and _SIZE_RE.fullmatch(size.strip()) else ""))
    return tuple(found)


def scan_ollama(base_url: str | None = None, *, fetch: Fetch | None = None) -> OllamaScan:
    """``GET /api/tags`` sur l'adresse locale d'Ollama ; jamais de génération, jamais d'adresse non locale.

    *fetch* (``url, délai, limite -> octets``) est injectable : les tests n'ouvrent aucune connexion.
    """
    base = base_url if base_url is not None else ollama_base_url()
    if not _is_loopback(base):
        return OllamaScan(base, False, (), "adresse non locale : non sondée")
    url = f"{base}/api/tags"
    try:
        raw = (fetch or _fetch)(url, OLLAMA_TIMEOUT_S, MAX_RESPONSE_BYTES)
    except (OSError, TimeoutError, urllib.error.URLError, ValueError):
        return OllamaScan(base, False, (), "service injoignable")
    if len(raw) > MAX_RESPONSE_BYTES:
        return OllamaScan(base, False, (), "réponse trop grosse : ignorée")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return OllamaScan(base, False, (), "réponse illisible : ignorée")
    return OllamaScan(base, True, _clean_models(payload), "")


_REAL_WHICH = shutil.which
_CACHE: dict[tuple[str, str], tuple[float, Inventory]] = {}
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    """Oublie les détections gardées (tests, geste « Vérifier »)."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ── Normalisation ───────────────────────────────────────────────────────────


def _cost_of(currency: str | None, provider_type: str) -> str:
    if currency == "local" or provider_type == "local":
        return "gratuit-local"
    return "abonnement" if currency == "quota" else "payant"


def _egress_of(currency: str | None, provider_type: str) -> str:
    return "rien" if currency == "local" or provider_type == "local" else "texte"


def key_command(provider_id: str) -> str:
    """La commande à lancer au terminal pour poser la clé ; vide si l'identifiant n'admet pas de clé."""
    var = provider_key_var(provider_id)
    return f"grimoire providers set-key {provider_id} {var}" if var else ""


def _key_present(provider_id: str, stored: frozenset[str]) -> bool:
    """La clé est-elle là ? Présence seulement : l'index du magasin, ou la variable d'environnement posée."""
    var = provider_key_var(provider_id)
    return provider_id in stored or (bool(var) and bool(os.environ.get(var)))


def _is_ollama_provider(spec: ProviderSpec) -> bool:
    words = (spec.invocation or "").split()
    return bool(words) and PurePosixPath(words[0]).name == "ollama"


def _same_model(declared: str, seen: str) -> bool:
    return declared == seen or f"{declared}:latest" == seen or declared == f"{seen}:latest"


def _caps(spec: ProviderSpec, model_id: str, probes: Mapping[str, Any]) -> tuple[tuple[str, int | bool, str], ...]:
    resolved = capabilities_for(spec, probes.get(spec.id))
    model = resolved.model(model_id)
    out: list[tuple[str, int | bool, str]] = []
    for name in MODEL_CAPABILITIES:
        cap = model.get(name)
        if cap.value is not None:
            out.append((name, cap.value, cap.provenance))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class _Env:
    root: Path
    now: datetime
    state: dict[str, ProviderRuntimeState]
    probes: dict[str, Any]
    stored: frozenset[str]
    which: Callable[[str], str | None]
    ollama: OllamaScan


def _registry_rows(spec: ProviderSpec, env: _Env) -> list[ModelAvailability]:
    runtime = env.state.get(spec.id)
    ollama_backed = _is_ollama_provider(spec)
    local = spec.provider_type == "local" or spec.currency == "local"
    needs_key = spec.currency == "api" and not local
    present = _key_present(spec.id, env.stored) if needs_key else None
    declared = [(m.id, m.tier) for m in spec.models] or [(mid, "") for mid in spec.default_models] or [("", "")]
    rows: list[ModelAvailability] = []
    for model_id, tier in declared:
        available, reason, detail = True, "", ""
        executable = (spec.invocation or "").split()[:1]
        suspended_model = runtime is not None and model_id != "" and runtime.is_model_suspended(model_id, now=env.now)
        if not spec.enabled:
            available, reason, detail = False, "désactivé", "Désactivé dans le registre."
        elif runtime is not None and (runtime.is_blocked(now=env.now) or suspended_model):
            until = runtime.suspension.until if runtime.suspension and runtime.suspension.until else runtime.cooldown_until
            when = f" jusqu'à {until.astimezone().strftime('%H:%M')}" if until else ""
            available, reason, detail = False, "limite-atteinte", f"Limite atteinte ou refroidissement{when}."
        elif not executable:
            available, reason, detail = False, "non-installé", "Aucune commande d'invocation déclarée."
        elif env.which(executable[0]) is None:
            available, reason, detail = False, "non-installé", f"La commande « {executable[0]} » est introuvable."
        elif runtime is not None and runtime.probed_at is not None and not runtime.available and not ollama_backed:
            available, reason, detail = False, "hors-ligne", "Le dernier audit ne l'a pas trouvé."
        elif ollama_backed and not env.ollama.reachable:
            available, reason, detail = False, "hors-ligne", "Ollama ne répond pas sur cette machine."
        elif ollama_backed and model_id and not any(_same_model(model_id, name) for name, _ in env.ollama.models):
            available, reason, detail = False, "non-installé", f"Modèle non téléchargé : ollama pull {model_id}"
        elif needs_key and not present:
            available, reason, detail = False, "clé-absente", "Aucune clé posée pour ce fournisseur."
        key = f"{SOURCE_OLLAMA}:{model_id}" if ollama_backed and model_id else f"{SOURCE_REGISTRY}:{spec.id}/{model_id}"
        label = model_id or spec.id
        rows.append(
            ModelAvailability(
                key=key,
                label=label,
                source=SOURCE_OLLAMA if ollama_backed else SOURCE_REGISTRY,
                provider_id=spec.id,
                model_id=model_id,
                origin=("Ollama sur cette machine" if ollama_backed else f"Fournisseur « {spec.id} »"),
                tier=tier,
                tier_provenance="registre" if tier else "",
                provider_type=spec.provider_type,
                currency=spec.currency or "",
                configured=True,
                available=available,
                reason=reason,
                detail=detail,
                egress=_egress_of(spec.currency, spec.provider_type),
                cost=_cost_of(spec.currency, spec.provider_type),
                key_present=present,
                key_command=key_command(spec.id) if needs_key else "",
                cooldown_until=(
                    runtime.cooldown_until.isoformat() if runtime is not None and runtime.cooldown_until else None
                ),
                capabilities=_caps(spec, model_id, env.probes),
            )
        )
    return rows


def _ollama_rows(env: _Env, configured: set[str]) -> list[ModelAvailability]:
    rows: list[ModelAvailability] = []
    for name, size in env.ollama.models:
        if f"{SOURCE_OLLAMA}:{name}" in configured or any(
            _same_model(name, k.split(":", 1)[1]) for k in configured if k.startswith(f"{SOURCE_OLLAMA}:")
        ):
            continue
        tier = tier_from_parameter_size(size)
        rows.append(
            ModelAvailability(
                key=f"{SOURCE_OLLAMA}:{name}",
                label=name,
                source=SOURCE_OLLAMA,
                provider_id=LOCAL_PROVIDER_ID,
                model_id=name,
                origin="Ollama sur cette machine",
                tier=tier,
                tier_provenance="déduit de la taille" if tier else "",
                provider_type="local",
                currency="local",
                configured=False,
                available=True,
                reason="",
                detail="Détecté, non configuré dans le registre.",
                egress="rien",
                cost="gratuit-local",
                key_present=None,
                key_command="",
                cooldown_until=None,
                capabilities=(),
                size_label=size,
            )
        )
    return rows


def _host_rows(env: _Env, *, verify: bool, acp: Callable[[Any, Path], tuple[bool, str]] | None) -> list[ModelAvailability]:
    from grimoire.hosts.acp_hosts import acp_availability, acp_usable, install_hint
    from grimoire.hosts.capabilities import all_profiles

    rows: list[ModelAvailability] = []
    for profile in all_profiles():
        if not profile.acp_command:
            continue
        installed, why = acp_availability(profile)
        connected: bool | None = None
        available, reason, detail = installed, "" if installed else "non-installé", why
        if installed and verify:
            ok, why_not = (acp or (lambda p, r: acp_usable(p, r)))(profile, env.root)
            connected = ok
            if not ok:
                available, reason, detail = False, "hors-ligne", why_not
        elif installed:
            detail = "Installé ; connexion non vérifiée (bouton « Vérifier maintenant »)."
        rows.append(
            ModelAvailability(
                key=f"{SOURCE_HOST}:{profile.host_id}",
                label=profile.display_name,
                source=SOURCE_HOST,
                provider_id=str(profile.host_id),
                model_id="",
                origin="Agent installé sur cette machine",
                tier="",
                tier_provenance="",
                provider_type="host",
                currency="quota",
                configured=True,
                available=available,
                reason=reason,
                detail=detail,
                egress="texte",
                cost="abonnement",
                key_present=None,
                key_command="",
                cooldown_until=None,
                capabilities=(),
                connected=connected,
                install_hint=install_hint(profile),
            )
        )
    return rows


def detect(
    root: Path,
    *,
    verify: bool = False,
    use_cache: bool = True,
    now: datetime | None = None,
    which: Callable[[str], str | None] = _REAL_WHICH,
    fetch: Fetch | None = None,
    ollama_url: str | None = None,
    acp: Callable[[Any, Path], tuple[bool, str]] | None = None,
) -> Inventory:
    """L'inventaire de ce qui est branché ici, en lecture seule (voir le docstring du module).

    *verify* : le geste « Vérifier » : ignore le cache et sonde en plus la connexion de chaque hôte installé
    (``initialize`` ACP, sans modèle). Sans lui, un hôte est « installé » et rien de plus. *fetch*, *which* et *acp*
    sont injectables.
    """
    base = ollama_url if ollama_url is not None else ollama_base_url()
    cache_key = (str(root.resolve()), base)
    injected = fetch is not None or acp is not None or which is not _REAL_WHICH
    if use_cache and not verify and not injected:
        with _CACHE_LOCK:
            hit = _CACHE.get(cache_key)
        if hit is not None and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    moment = now if now is not None else datetime.now(UTC)
    registry_error = ""
    try:
        specs = read_registry(root)
    except ProviderRegistryError as exc:
        specs, registry_error = (), str(exc)
    try:
        stored = frozenset(entry.provider for entry in list_secrets() if is_valid_provider_id(entry.provider))
    except (OSError, KeyError, TypeError):
        stored = frozenset()
    env = _Env(
        root=root,
        now=moment,
        state=load_state(root),
        probes=dict(load_probes(root)),
        stored=stored,
        which=which,
        ollama=scan_ollama(base, fetch=fetch),
    )
    rows: list[ModelAvailability] = []
    for spec in specs:
        rows.extend(_registry_rows(spec, env))
    seen_keys: set[str] = set()
    unique: list[ModelAvailability] = []
    for row in rows:
        if row.key not in seen_keys:  # un même modèle déclaré deux fois : la première déclaration fait foi
            seen_keys.add(row.key)
            unique.append(row)
    unique.extend(_ollama_rows(env, {r.key for r in unique if r.source == SOURCE_OLLAMA}))
    unique.extend(_host_rows(env, verify=verify, acp=acp))
    inventory = Inventory(
        models=tuple(unique),
        ollama=env.ollama,
        registry_error=registry_error,
        verified=verify,
        at=moment.isoformat(timespec="seconds"),
    )
    if not injected:
        with _CACHE_LOCK:
            _CACHE[cache_key] = (time.monotonic(), inventory)
    return inventory
