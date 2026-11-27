"""Magasin léger de jetons API : ``~/.env-store/`` + un index (issue #310, suite).

Les clés ne vivent jamais dans ``llm-provider-registry.yaml`` lui-même — sa
``data_policy.forbidden_data_classes`` interdit déjà ``secrets``/``credentials``
dans ce qui *transite par* un fournisseur ; ce module applique la même règle
à ce qui *décrit* un fournisseur. Une valeur se pose en saisie masquée
(``grimoire providers set-key``, ``getpass``), jamais en argument de ligne de
commande ni en log — c'est le seul chemin d'écriture exposé par la CLI.

Format du magasin :
  ``~/.env-store/index.json``     — ``{"entries": [{"provider", "env_var", "file", "added_at"}]}``
  ``~/.env-store/<provider>.env`` — lignes ``VAR=valeur``, une par variable, ``chmod 600``

``env_for_provider`` est lu par ``missions.dispatch._run_provider_call`` pour
fusionner ces variables dans l'environnement du sous-processus avant
d'invoquer le fournisseur — un script d'invocation (``deepseek-cli.py`` et
analogues) n'a besoin de rien connaître du magasin, il reçoit déjà la
variable dans son ``os.environ``.

Une clé est liée au point d'accès pour lequel elle a été saisie : l'index retient, avec elle, l'empreinte des points
d'accès normalisés (schéma, hôte, port, chemin de base) que le fournisseur déclarait à la saisie. À chaque usage,
l'appelant donne le point d'accès visé ; si l'empreinte diffère, la clé n'est pas rendue
(:class:`KeyEndpointError`). Une clé enregistrée avant cette liaison (sans empreinte) ne vaut que vers l'adresse par
défaut officielle de son fournisseur. Ni l'adresse ni la clé ne figurent dans un message d'erreur.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from grimoire.providers.capabilities import canonical_provider


def store_dir() -> Path:
    """``~/.env-store`` — jamais dans le dépôt du projet, jamais versionné.

    Sous Windows, ``chmod`` ne restreint rien : seuls les droits du profil utilisateur protègent ce dossier.
    """
    return Path.home() / ".env-store"


def _index_file() -> Path:
    return store_dir() / "index.json"


#: Identifiant de fournisseur admis pour nommer un fichier du magasin ou dériver un nom de
#: variable : ASCII minuscule strict. Un id hors de cette forme pourrait sortir du magasin
#: (``../``) ou, après ``upper()``, tomber sur la variable d'un autre fournisseur.
_PROVIDER_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}", re.ASCII)


def is_valid_provider_id(provider_id: str) -> bool:
    """Vrai si *provider_id* est un identifiant ASCII strict (``[a-z0-9][a-z0-9-]{0,63}``)."""
    return isinstance(provider_id, str) and _PROVIDER_ID_RE.fullmatch(provider_id) is not None


def provider_key_var(provider_id: str) -> str:
    """``<ID>_API_KEY`` dérivé d'un id valide ; vide pour un id invalide (rien n'est dérivé)."""
    if not is_valid_provider_id(provider_id):
        return ""
    return f"{provider_id.upper().replace('-', '_')}_API_KEY"


def _provider_file(provider_id: str) -> Path:
    """Fichier du magasin pour *provider_id* ; ``ValueError`` si l'id n'est pas admis."""
    if not is_valid_provider_id(provider_id):
        msg = f"identifiant de fournisseur invalide {provider_id!r} (attendu : [a-z0-9][a-z0-9-]{{0,63}})."
        raise ValueError(msg)
    return store_dir() / f"{provider_id}.env"


#: Adresse par défaut officielle de chaque fournisseur documenté (clé : ``canonical_provider``). Un point d'accès
#: explicite de la saisie : une clé ancienne, sans empreinte, ne vaut que vers elle.
OFFICIAL_ENDPOINTS: Mapping[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "google-gemini": "https://generativelanguage.googleapis.com",
    "deepseek": "https://api.deepseek.com",
    "moonshot": "https://api.moonshot.ai",
}
#: Hôtes d'une boucle locale : seule adresse admise en ``http://``.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_DEFAULT_PORTS = {"https": 443, "http": 80}

_CHANGED = (
    "l'adresse de ce fournisseur a changé depuis la saisie de la clé ; saisis-la de nouveau pour l'approuver "
    "(`grimoire providers set-key {provider} {var}`)."
)
_LEGACY = (
    "cette clé a été saisie avant sa liaison à une adresse : elle ne vaut que vers l'adresse officielle du "
    "fournisseur ; saisis-la de nouveau pour l'approuver (`grimoire providers set-key {provider} {var}`)."
)
_NOT_ADMITTED = (
    "adresse du fournisseur non admise pour une clé (https requis hors boucle locale, ni identifiants, ni requête, "
    "ni fragment) ; corrige la déclaration du fournisseur."
)


class KeyEndpointError(ValueError):
    """La clé n'est pas rendue pour ce point d'accès. Le message ne porte ni la clé ni l'adresse."""


def normalize_endpoint(url: str | None) -> str:
    """Le point d'accès normalisé de *url* ; vide si l'adresse n'est pas admise pour recevoir une clé.

    Schéma et hôte en minuscules, port par défaut omis, ``/`` final et ``/v1`` final retirés. Admis : ``https://``, et
    ``http://`` vers une boucle locale seulement ; jamais d'identifiants, de requête ni de fragment.
    """
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if not host or port == 0 or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return ""
    if scheme != "https" and not (scheme == "http" and host in _LOOPBACK_HOSTS):
        return ""
    shown_host = f"[{host}]" if ":" in host else host
    shown_port = "" if port in (None, _DEFAULT_PORTS[scheme]) else f":{port}"
    path = parts.path.rstrip("/").removesuffix("/v1").rstrip("/")
    return f"{scheme}://{shown_host}{shown_port}{path}"


def _fingerprint(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def official_endpoint(provider_id: str, provider_type: str = "") -> str:
    """L'adresse par défaut officielle de *provider_id* ; vide s'il n'en a pas (passerelle, local, inconnu)."""
    return OFFICIAL_ENDPOINTS.get(canonical_provider(provider_id, provider_type), "")


@dataclass(frozen=True, slots=True)
class SecretEntry:
    """Une ligne de l'index — jamais la valeur, seulement sa provenance."""

    provider: str
    env_var: str
    file: str
    added_at: str
    #: Vrai si la clé porte l'empreinte du point d'accès de sa saisie ; faux : saisie d'avant la liaison.
    bound: bool = False


def _ensure_store() -> None:
    store_dir().mkdir(parents=True, exist_ok=True)
    store_dir().chmod(0o700)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    body = "\n".join(f"{k}={v}" for k, v in values.items())
    path.write_text(f"{body}\n" if values else "", encoding="utf-8")
    path.chmod(0o600)


_Index = dict[str, list[dict[str, Any]]]


def _load_index() -> _Index:
    path = _index_file()
    if not path.is_file():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"entries": []}
    return data if isinstance(data, dict) else {"entries": []}


def _save_index(data: _Index) -> None:
    path = _index_file()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def set_secret(provider_id: str, env_var: str, value: str, *, endpoints: Iterable[str] | None = None) -> Path:
    """Pose *value* pour *env_var* du fournisseur *provider_id* ; rend le fichier écrit.

    *endpoints* : les points d'accès que le fournisseur déclare à cet instant (geste de saisie de l'humain) ; la clé
    y est liée. ``None`` pose une clé non liée (valable seulement vers l'adresse officielle du fournisseur) ; une liste
    vide lie la clé à « aucune adresse déclarée ».

    Lève ``ValueError`` sur une valeur vide — un magasin qui accepterait une
    clé vide masquerait l'absence réelle de configuration —, sur un id invalide, ou
    (:class:`KeyEndpointError`) sur une adresse non admise.
    """
    if not value.strip():
        msg = "valeur vide refusée."
        raise ValueError(msg)
    fingerprints: list[str] | None = None
    if endpoints is not None:
        normalized = [normalize_endpoint(url) for url in endpoints]
        if "" in normalized:
            raise KeyEndpointError(_NOT_ADMITTED)
        fingerprints = sorted({_fingerprint(url) for url in normalized})
    path = _provider_file(provider_id)
    _ensure_store()
    values = _read_env_file(path)
    values[env_var] = value.strip()
    _write_env_file(path, values)

    index = _load_index()
    entries = [e for e in index["entries"] if not (e["provider"] == provider_id and e["env_var"] == env_var)]
    entry: dict[str, Any] = {
        "provider": provider_id,
        "env_var": env_var,
        "file": path.name,
        "added_at": datetime.now(UTC).isoformat(),
    }
    if fingerprints is not None:
        entry["endpoints"] = fingerprints
    entries.append(entry)
    index["entries"] = entries
    _save_index(index)
    return path


def _is_bound(entry: Mapping[str, Any]) -> bool:
    return isinstance(entry.get("endpoints"), list)


def list_secrets() -> tuple[SecretEntry, ...]:
    """Entrées déclarées (fournisseur, variable, fichier, date, liée ou non) — jamais les valeurs."""
    index = _load_index()
    return tuple(
        SecretEntry(
            provider=e["provider"], env_var=e["env_var"], file=e["file"], added_at=e.get("added_at", ""), bound=_is_bound(e)
        )
        for e in index.get("entries", [])
        if isinstance(e, dict) and e.get("provider") and e.get("env_var")
    )


def unbound_secrets() -> tuple[SecretEntry, ...]:
    """Les clés saisies avant leur liaison à un point d'accès : à saisir de nouveau pour tout autre usage que l'officiel."""
    return tuple(entry for entry in list_secrets() if not entry.bound)


def remove_secret(provider_id: str, env_var: str | None = None) -> None:
    """Retire une variable, ou tout le fichier de *provider_id* si *env_var* est ``None``.

    Lève ``ValueError`` sur un id invalide.
    """
    path = _provider_file(provider_id)
    values = _read_env_file(path)
    if env_var is None:
        path.unlink(missing_ok=True)
    elif env_var in values:
        del values[env_var]
        _write_env_file(path, values)
    index = _load_index()
    index["entries"] = [
        e
        for e in index["entries"]
        if not (e["provider"] == provider_id and (env_var is None or e["env_var"] == env_var))
    ]
    _save_index(index)


def _check_endpoints(provider_id: str, var: str, entry: Mapping[str, Any] | None, targets: list[str]) -> None:
    """Lève :class:`KeyEndpointError` si la clé *var* ne peut pas partir vers *targets* (déjà normalisés)."""
    if "" in targets:
        raise KeyEndpointError(_NOT_ADMITTED)
    names = {"provider": provider_id, "var": var}
    if entry is not None and _is_bound(entry):
        approved = set(entry["endpoints"])
        if any(_fingerprint(url) not in approved for url in targets):
            raise KeyEndpointError(_CHANGED.format(**names))
        return
    official = normalize_endpoint(official_endpoint(provider_id))
    if not official or not targets or any(url != official for url in targets):
        raise KeyEndpointError(_LEGACY.format(**names))


def env_for_provider(provider_id: str, *, endpoints: Iterable[str]) -> dict[str, str]:
    """Variables déjà posées pour *provider_id*, rendues seulement pour les points d'accès *endpoints*.

    *endpoints* : les adresses où l'appelant enverra la clé (au moins celle qu'il vise). ``{}`` si rien n'est
    enregistré ou si l'id est invalide ; :class:`KeyEndpointError` si la clé n'a pas été saisie pour l'une d'elles
    — aucune valeur n'est alors rendue.
    """
    if not is_valid_provider_id(provider_id):
        return {}
    values = _read_env_file(_provider_file(provider_id))
    if not values:
        return {}
    targets = [normalize_endpoint(url) for url in endpoints]
    index = {
        e.get("env_var"): e
        for e in _load_index().get("entries", [])
        if isinstance(e, dict) and e.get("provider") == provider_id
    }
    for var in values:
        _check_endpoints(provider_id, var, index.get(var), targets)
    return values
