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

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from grimoire.providers.audit import ollama_base_url
from grimoire.providers.capabilities import MODEL_CAPABILITIES
from grimoire.providers.capability_probe import capabilities_for, load_probes
from grimoire.providers.registry import (
    LOCAL_INVOCATION,
    LOCAL_PROVIDER_ID,
    ProviderRegistryError,
    ProviderSpec,
    read_registry,
)
from grimoire.providers.secrets import KeyEndpointError, is_valid_provider_id, list_secrets, provider_key_var
from grimoire.providers.state import ProviderRuntimeState, load_state

__all__ = [
    "CACHE_TTL_SECONDS",
    "COSTS",
    "EGRESSES",
    "ENDPOINTS_CHANGED_MESSAGE",
    "REASONS",
    "EndpointsChangedError",
    "Inventory",
    "ModelAvailability",
    "OllamaScan",
    "clear_cache",
    "detect",
    "fingerprint",
    "is_local_ollama",
    "key_endpoints",
    "local_ollama_entry",
    "probe_remote",
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
    #: Nom lisible et stable (``origine:fournisseur/modèle``) ; ``key`` en est dérivé et ne s'en distingue que si deux
    #: entrées portent le même nom avec des empreintes différentes (``ref#empreinte``).
    ref: str = ""
    #: Empreinte de ce que la ligne désigne (voir :func:`fingerprint`) : une autorisation n'est valable que pour elle.
    fingerprint: str = ""

    def capability(self, name: str) -> tuple[int | bool, str] | None:
        """``(valeur, provenance)`` d'une capacité connue, ``None`` si elle est inconnue."""
        for cap_name, value, provenance in self.capabilities:
            if cap_name == name:
                return value, provenance
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "ref": self.ref or self.key,
            "fingerprint": self.fingerprint[:12] or None,
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
    #: Noms listés par Ollama mais exécutés hors de la machine (ou dont on le soupçonne) : jamais « rien ne sort ».
    remote: frozenset[str] = frozenset()


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


def _is_cloud_name(name: str) -> bool:
    return name.lower().endswith(("-cloud", ":cloud"))


def _remote_names(payload: Any) -> frozenset[str]:
    """Les modèles que ``/api/tags`` liste mais qu'Ollama exécute ailleurs : au doute, ils sont exposés.

    Une entrée portant ``remote_host`` ou ``remote_model`` (même vides), ou dont le nom se termine par ``-cloud`` ou
    ``:cloud``, est tenue pour hébergée : jamais « rien ne sort de la machine », jamais gratuite-locale.
    """
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return frozenset()
    found: set[str] = set()
    for entry in models[:MAX_OLLAMA_MODELS]:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str):
            continue
        alias = entry.get("model")
        if (
            "remote_host" in entry
            or "remote_model" in entry
            or _is_cloud_name(name)
            or (isinstance(alias, str) and _is_cloud_name(alias))
        ):
            found.add(name)
    return frozenset(found)


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
    return OllamaScan(base, True, _clean_models(payload), "", _remote_names(payload))


_REAL_WHICH = shutil.which
_CACHE: dict[tuple[str, str], tuple[float, Inventory]] = {}
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    """Oublie les détections gardées (tests, geste « Vérifier »)."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ── Normalisation ───────────────────────────────────────────────────────────


def _cost_of(currency: str | None, *, verified_local: bool) -> str:
    """``gratuit-local`` seulement si le point d'accès est vérifiablement sur la machine ; inconnu = payant."""
    if verified_local:
        return "gratuit-local"
    return "abonnement" if currency == "quota" else "payant"


def _egress_of(*, verified_local: bool) -> str:
    return "rien" if verified_local else "texte"


def _is_loopback_url(value: str | None) -> bool:
    return bool(value) and _is_loopback(str(value))


def _ollama_binary(which: Callable[[str], str | None]) -> str:
    """Le chemin réel (liens résolus) du binaire ``ollama`` que le PATH trouve ; vide s'il n'y en a pas."""
    found = which("ollama")
    return os.path.realpath(found) if found else ""


def _inside_project(binary: str, root: Path | None) -> bool:
    if not binary or root is None:
        return False
    try:
        return Path(binary).is_relative_to(Path(os.path.realpath(root)))
    except (OSError, ValueError):
        return True  # au doute, le binaire est tenu pour celui du projet


def is_local_ollama(
    spec: ProviderSpec, which: Callable[[str], str | None] = shutil.which, root: Path | None = None
) -> tuple[bool, str]:
    """Cette entrée du registre est-elle réellement l'Ollama local ? ``(oui, motif du refus)``.

    Même définition de « local » que la politique de données (``provider_type == "local"``), et en plus : l'invocation
    a pour nom de base ``ollama``, son chemin (s'il en a un) est le binaire que le PATH du cockpit trouve, aucune
    ``base_url`` n'envoie ailleurs que sur une boucle locale, et (si *root* est donné) ce binaire ne se trouve pas
    sous la racine du projet : un exécutable que le projet apporte lui-même n'est pas « l'Ollama de la machine ».
    """
    words = (spec.invocation or "").split()
    if not words or PurePosixPath(words[0]).name != "ollama":
        return False, "l'invocation n'est pas la commande « ollama »"
    if spec.provider_type != "local":
        return False, f"le type du fournisseur est « {spec.provider_type or 'inconnu'} », pas « local »"
    for url in (spec.base_url, spec.anthropic_base_url):
        if url and not _is_loopback_url(url):
            return False, "une adresse distante (base_url) est déclarée"
    if "/" in words[0] or "\\" in words[0]:
        found = which("ollama")
        if found is None or os.path.realpath(words[0]) != os.path.realpath(found):
            return False, "le chemin de la commande n'est pas l'exécutable « ollama » du PATH"
    if _inside_project(_ollama_binary(which), root):
        return False, "l'exécutable « ollama » trouvé est sous la racine du projet, pas celui de la machine"
    return True, ""


def _verified_local(spec: ProviderSpec, genuine_ollama: bool) -> bool:
    """Le point d'accès est-il vérifiablement sur la machine ? Ollama local reconnu, ou ``local`` + boucle locale.

    ``currency: local`` seul ne suffit pas, et un binaire local quelconque non plus : ce qu'il fait du texte ne se vérifie pas.
    """
    if genuine_ollama:
        return True
    return spec.provider_type == "local" and _is_loopback_url(spec.base_url) and not (
        spec.anthropic_base_url and not _is_loopback_url(spec.anthropic_base_url)
    )


def fingerprint(*parts: str) -> str:
    """Empreinte SHA-256 (hex) d'une liste ordonnée de champs : ce qu'une autorisation désigne.

    Registre : ``("registre", id du fournisseur, type, monnaie, invocation, base_url, anthropic_base_url, modèle)``.
    Ollama local reconnu : ``("ollama-local", modèle sans ":latest", chemin réel du binaire, invocation sans son
    premier mot, base_url, anthropic_base_url[, "distant"])`` (la même, configuré ou non, quand l'entrée est la
    canonique, pour qu'ajouter le modèle au registre n'invalide rien). Hôte : ``("hote", identifiant, commande ACP)``.
    """
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _ollama_fp(
    model: str, binary: str, invocation: str, base_url: str = "", anthropic_base_url: str = "", *, remote: bool = False
) -> str:
    """L'empreinte d'un modèle d'Ollama local : le nom, le binaire réel, le reste de la commande et les adresses."""
    rest = " ".join((invocation or "").split()[1:])
    parts = ["ollama-local", model.removesuffix(":latest"), binary, rest, base_url, anthropic_base_url]
    return fingerprint(*parts, *(["distant"] if remote else []))


def key_command(provider_id: str) -> str:
    """La commande à lancer au terminal pour poser la clé ; vide si l'identifiant n'admet pas de clé."""
    var = provider_key_var(provider_id)
    return f"grimoire providers set-key {provider_id} {var}" if var else ""


def _key_present(provider_id: str, stored: frozenset[str]) -> bool:
    """La clé est-elle là ? Présence seulement : l'index du magasin, ou la variable d'environnement posée."""
    var = provider_key_var(provider_id)
    return provider_id in stored or (bool(var) and bool(os.environ.get(var)))


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


def _runs_remotely(model_id: str, scan: OllamaScan) -> bool:
    """Ce modèle d'Ollama s'exécute-t-il hors de la machine (ou est-ce à craindre) ? Au doute, oui."""
    return _is_cloud_name(model_id) or any(_same_model(model_id, name) for name in scan.remote)


def _registry_rows(spec: ProviderSpec, env: _Env) -> list[ModelAvailability]:
    runtime = env.state.get(spec.id)
    ollama_backed = is_local_ollama(spec, env.which, env.root)[0]
    verified_local = _verified_local(spec, ollama_backed)
    needs_key = spec.currency == "api" and not verified_local
    present = _key_present(spec.id, env.stored) if needs_key else None
    declared = [(m.id, m.tier) for m in spec.models] or [(mid, "") for mid in spec.default_models] or [("", "")]
    binary = _ollama_binary(env.which) if ollama_backed else ""
    rows: list[ModelAvailability] = []
    for model_id, tier in declared:
        # Un modèle que l'Ollama de la machine liste mais exécute ailleurs est exposé, jamais « rien ne sort ».
        remote = ollama_backed and bool(model_id) and _runs_remotely(model_id, env.ollama)
        local_row = verified_local and not remote
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
                origin=(
                    "Ollama (modèle exécuté hors de la machine)"
                    if remote
                    else "Ollama sur cette machine" if ollama_backed else f"Fournisseur « {spec.id} »"
                ),
                tier=tier,
                tier_provenance="registre" if tier else "",
                provider_type=spec.provider_type,
                currency=spec.currency or "",
                configured=True,
                available=available,
                reason=reason,
                detail=detail,
                egress=_egress_of(verified_local=local_row),
                cost=_cost_of(spec.currency, verified_local=local_row),
                ref=key,
                fingerprint=(
                    _ollama_fp(
                        model_id, binary, spec.invocation or "", spec.base_url or "", spec.anthropic_base_url or "",
                        remote=remote,
                    )
                    if ollama_backed and model_id
                    else fingerprint(
                        "registre", spec.id, spec.provider_type, spec.currency or "", spec.invocation or "",
                        spec.base_url or "", spec.anthropic_base_url or "", model_id,
                    )
                ),
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
    binary = _ollama_binary(env.which)
    project_bound = _inside_project(binary, env.root)
    for name, size in env.ollama.models:
        if f"{SOURCE_OLLAMA}:{name}" in configured or any(
            _same_model(name, k.split(":", 1)[1]) for k in configured if k.startswith(f"{SOURCE_OLLAMA}:")
        ):
            continue
        # Exécuté ailleurs, ou binaire apporté par le projet : exposé, jamais « rien ne sort » ni gratuit-local.
        exposed = project_bound or _runs_remotely(name, env.ollama)
        tier = tier_from_parameter_size(size)
        rows.append(
            ModelAvailability(
                key=f"{SOURCE_OLLAMA}:{name}",
                label=name,
                source=SOURCE_OLLAMA,
                provider_id=LOCAL_PROVIDER_ID,
                model_id=name,
                origin="Ollama (modèle exécuté hors de la machine)" if exposed else "Ollama sur cette machine",
                tier=tier,
                tier_provenance="déduit de la taille" if tier else "",
                provider_type="hosted" if exposed else "local",
                currency="api" if exposed else "local",
                configured=False,
                available=True,
                reason="",
                detail="Détecté, non configuré dans le registre.",
                egress=_egress_of(verified_local=not exposed),
                cost=_cost_of("api" if exposed else "local", verified_local=not exposed),
                key_present=None,
                key_command="",
                cooldown_until=None,
                capabilities=(),
                size_label=size,
                ref=f"{SOURCE_OLLAMA}:{name}",
                fingerprint=_ollama_fp(name, binary, LOCAL_INVOCATION, remote=exposed),
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
                ref=f"{SOURCE_HOST}:{profile.host_id}",
                fingerprint=fingerprint("hote", str(profile.host_id), " ".join(profile.acp_command)),
            )
        )
    return rows


def local_ollama_entry(root: Path, which: Callable[[str], str | None] = shutil.which) -> dict[str, Any]:
    """Ce qu'« ajouter un modèle Ollama détecté » écrirait : l'entrée ``ollama`` du registre, créée ou existante.

    ``genuine`` est faux si l'entrée existante n'est pas l'Ollama local (``reason`` dit pourquoi) : l'ajout est alors refusé.
    """
    try:
        spec = next((s for s in read_registry(root) if s.id == LOCAL_PROVIDER_ID), None)
    except ProviderRegistryError:
        spec = None
    if spec is None:
        return {"exists": False, "genuine": True, "reason": "", "provider_id": LOCAL_PROVIDER_ID, "invocation": LOCAL_INVOCATION}
    genuine, reason = is_local_ollama(spec, which)
    return {
        "exists": True, "genuine": genuine, "reason": reason, "provider_id": spec.id, "invocation": spec.invocation or "",
    }


class EndpointsChangedError(ValueError):
    """La liste des points d'accès n'est plus celle que l'humain a confirmée : rien n'a été envoyé."""


#: Le motif que la page reconnaît pour relire la vue et réafficher le dialogue (ne pas le changer sans elle).
ENDPOINTS_CHANGED_MESSAGE = "la liste a changé, relis-la : aucune clé n'a été envoyée"


def _spec_key_endpoint(spec: ProviderSpec) -> tuple[str, str] | None:
    """``(provider_id, url)`` où la sonde enverrait la clé de *spec*, ou ``None`` ; sans réseau, sans valeur de clé."""
    from grimoire.providers import capability_probe as cp
    from grimoire.providers.capabilities import LITELLM, canonical_provider

    if not (spec.enabled and is_valid_provider_id(spec.id)):
        return None
    kind = canonical_provider(spec.id, spec.provider_type)
    endpoint: Any
    if kind == "local":
        return None
    if kind == LITELLM:
        base = cp.admitted_gateway_url(spec.base_url)
        if not base or cp._names_documented_provider(spec.id):
            return None
        endpoint = cp._Endpoint(
            f"{base}/v1/models", provider_key_var(spec.id), "bearer", store_only=True, target=spec.base_url or ""
        )
    else:
        endpoint = cp._hosted_endpoint(spec)
        if endpoint is None or (endpoint.store_only and endpoint.key_var in cp._reserved_key_vars()):
            return None
    try:
        present = bool(cp._key_value(spec.id, endpoint))  # présence seulement : la valeur n'en sort pas
    except KeyEndpointError:  # clé saisie pour une autre adresse : la sonde ne l'enverrait pas, la page ne la propose pas
        return None
    if not present:
        return None
    return spec.id, str(endpoint.url).split("?", 1)[0]


def _endpoints_of(specs: Sequence[ProviderSpec]) -> list[dict[str, str]]:
    found = (_spec_key_endpoint(spec) for spec in specs)
    return [{"provider_id": entry[0], "url": entry[1]} for entry in found if entry is not None]


def key_endpoints(root: Path) -> list[dict[str, str]]:
    """Les points d'accès où la sonde de capacités enverrait une clé d'API si on la lançait : sans réseau, sans valeur de clé.

    Un fournisseur activé figure ici seulement si sa clé est posée ET si la sonde saurait où l'envoyer (adresse documentée,
    ``anthropic_base_url`` en https sans identifiants, ou passerelle admise). Les fournisseurs locaux n'en font pas partie.
    """
    try:
        specs = read_registry(root)
    except ProviderRegistryError:
        return []
    return _endpoints_of(specs)


def _confirmed_pairs(confirmed: Any) -> list[tuple[str, str]]:
    if not isinstance(confirmed, list):
        raise EndpointsChangedError("`confirmed_endpoints` : la liste affichée à l'humain est requise (relis-la)")
    pairs: list[tuple[str, str]] = []
    for item in confirmed:
        pid = item.get("provider_id") if isinstance(item, dict) else None
        url = item.get("url") if isinstance(item, dict) else None
        if not isinstance(pid, str) or not isinstance(url, str):
            raise EndpointsChangedError("`confirmed_endpoints` : liste illisible (relis-la)")
        pairs.append((pid, url))
    return sorted(pairs)


def probe_remote(root: Path, confirmed_endpoints: Any) -> list[str]:
    """La sonde de capacités sur les seuls fournisseurs que l'humain a vus listés ; rend leurs identifiants.

    À n'appeler que derrière la clé de contrôle : elle envoie la clé d'API de chacun à son point d'accès. Le registre est
    lu UNE fois : la liste des points d'accès, sa comparaison à *confirmed_endpoints* (ce que la page a montré, liste vide
    comprise ; absente = refus) et la sonde partent de cette seule lecture. Si la liste diffère, :class:`EndpointsChanged`
    (une :class:`EndpointsChangedError`) est levée et rien n'est envoyé. Les fournisseurs locaux (Ollama) ne passent jamais par ici.
    """
    from grimoire.providers.capability_probe import load_probes, probe_provider_capabilities, save_probes

    confirmed = _confirmed_pairs(confirmed_endpoints)
    try:
        specs = read_registry(root)
    except ProviderRegistryError:
        specs = ()
    entries = _endpoints_of(specs)
    if sorted((e["provider_id"], e["url"]) for e in entries) != confirmed:
        raise EndpointsChangedError(ENDPOINTS_CHANGED_MESSAGE)
    by_id = {e["provider_id"]: e["url"] for e in entries}
    targets = [s for s in specs if _spec_key_endpoint(s) == (s.id, by_id.get(s.id))]
    if not targets:
        return []
    probes = load_probes(root)
    for spec in targets:
        probes[spec.id] = probe_provider_capabilities(spec)
    save_probes(root, probes)
    return [s.id for s in targets]


def _disambiguate(rows: list[ModelAvailability]) -> tuple[ModelAvailability, ...]:
    """Une ligne par (nom, empreinte) : deux entrées de même nom mais d'empreintes différentes ne se confondent jamais.

    Un doublon exact (même nom, même empreinte) n'est gardé qu'une fois ; une collision de nom garde les deux lignes,
    chacune sous ``nom#empreinte`` (douze premiers caractères).
    """
    by_ref: dict[str, list[ModelAvailability]] = {}
    for row in rows:
        group = by_ref.setdefault(row.ref or row.key, [])
        if all(other.fingerprint != row.fingerprint for other in group):
            group.append(row)
    out: list[ModelAvailability] = []
    for group in by_ref.values():
        for row in group:
            out.append(row if len(group) == 1 else replace(row, key=f"{row.ref}#{row.fingerprint[:12]}"))
    return tuple(out)


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
    rows.extend(_ollama_rows(env, {r.key for r in rows if r.source == SOURCE_OLLAMA}))
    rows.extend(_host_rows(env, verify=verify, acp=acp))
    inventory = Inventory(
        models=_disambiguate(rows),
        ollama=env.ollama,
        registry_error=registry_error,
        verified=verify,
        at=moment.isoformat(timespec="seconds"),
    )
    if not injected:
        with _CACHE_LOCK:
            _CACHE[cache_key] = (time.monotonic(), inventory)
    return inventory
