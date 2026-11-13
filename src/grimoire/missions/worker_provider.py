"""Router un worker du tick vers un fournisseur compatible avec l'API d'Anthropic.

``claude --bg`` garde alors ses outils, ses hooks et ses gardes ; seul le modèle change. La route passe par
``--settings`` et non par l'environnement : celui d'un ``--bg`` n'atteint que le démon qu'il démarre, il serait
ignoré par un démon déjà lancé et s'imposerait à toutes les sessions suivantes s'il le démarrait.

Une condition manquante (fournisseur fermé, modèle suspendu, clé absente) rend ``None`` : le worker part sur le
modèle habituel. Une tâche V2 n'est jamais routée : rien ne pourrait juger seul le travail d'un modèle moins cher.
Une tâche dont la classe de données n'est pas admise par le fournisseur non plus (#1133) : voir :func:`route_refusal`.

Un fournisseur ``provider_type: local`` sur une boucle locale (``127.0.0.1``, ``localhost``, ``::1``) est admis en
``http://`` et sans clé (#1154) : rien ne sort du poste. Toute autre URL ``http://`` reste refusée — une adresse
distante servie en clair ne reçoit jamais les données d'une tâche. Un seul worker à la fois par fournisseur local :
le deuxième attend (``busy_providers``), il ne retombe pas en silence sur le modèle de l'hôte.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from grimoire.core.fsio import atomic_write
from grimoire.missions.schemas import MissionTask
from grimoire.missions.verifiability import Verifiability, classify
from grimoire.providers.data_policy import data_policy_refusal
from grimoire.providers.registry import ProviderSpec, read_registry
from grimoire.providers.secrets import env_for_provider, store_dir
from grimoire.providers.state import available_models, load_state

_SUBAGENT_MODEL_VARS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
#: Le modèle externe voit ce que ses outils lisent : ces dossiers lui restent fermés.
_DENIED_READS = ("Read(~/.env-store/**)", "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.config/gh/**)")
#: Hôtes d'une boucle locale : les seules adresses qu'un fournisseur local peut servir en clair.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: Jeton factice d'un serveur local : il n'authentifie rien, mais le client Claude Code en exige un.
_LOCAL_AUTH_PLACEHOLDER = "local"


@dataclass(frozen=True, slots=True)
class WorkerRoute:
    provider: str
    model: str
    settings: Path


def settings_path(provider: str) -> Path:
    return store_dir() / f"{provider}.claude-settings.json"


def _key_var(provider: str) -> str:
    """La variable de la clé dans le magasin, vide si elle n'y est pas."""
    name = f"{provider.upper().replace('-', '_')}_API_KEY"
    return name if env_for_provider(provider).get(name) else ""


def _enabled_provider(root: Path, provider_id: str) -> ProviderSpec | None:
    return next((p for p in read_registry(root) if p.id == provider_id and p.enabled), None)


def _loopback_url(url: str) -> bool:
    """Vrai si *url* vise une boucle locale — jamais vrai pour une autre adresse."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in {"http", "https"} and (parts.hostname or "").lower() in _LOOPBACK_HOSTS


def _admitted_base_url(provider: ProviderSpec) -> str:
    """L'URL que ce fournisseur peut servir à un worker ; vide si elle n'est pas admise.

    Un fournisseur ``local`` dit que rien ne sort du poste : seule une boucle locale le vérifie, en ``http``
    comme en ``https``. Un hébergé n'est admis qu'en ``https://`` ; une adresse locale ne le rend pas local.
    """
    url = (provider.anthropic_base_url or "").strip()
    if provider.provider_type == "local":
        return url if _loopback_url(url) else ""
    return url if url.startswith("https://") else ""


def _write_settings(provider: str, base_url: str, model: str, key_var: str) -> Path:
    """Les réglages de la session : la clé reste dans le magasin, `apiKeyHelper` l'y relit à chaque besoin.

    Un fournisseur local sans clé n'a pas d'``apiKeyHelper`` : un jeton factice satisfait le client, et le
    serveur de boucle locale ne l'authentifie pas — aucune valeur secrète n'est écrite dans les réglages.
    """
    env = {"ANTHROPIC_BASE_URL": base_url}
    env.update(dict.fromkeys(_SUBAGENT_MODEL_VARS, model))
    settings: dict[str, object] = {"env": env, "permissions": {"deny": list(_DENIED_READS)}}
    if key_var:
        helper = f"sed -n 's/^{key_var}=//p' {shlex.quote(str(store_dir() / f'{provider}.env'))}"
        settings["apiKeyHelper"] = helper
    else:
        env["ANTHROPIC_AUTH_TOKEN"] = _LOCAL_AUTH_PLACEHOLDER
    path = settings_path(provider)
    atomic_write(path, json.dumps(settings))
    return path


def route_refusal(root: Path, provider_id: str, task: MissionTask) -> str:
    """Pourquoi la politique de données ferme *provider_id* à *task* ; vide si elle ne s'y oppose pas."""
    provider = _enabled_provider(root, provider_id)
    return data_policy_refusal(root, provider, task.data_class) if provider is not None else ""


def _decide(
    root: Path,
    provider_id: str,
    tier: str,
    task: MissionTask,
    *,
    busy_providers: frozenset[str],
    now: datetime | None,
    write_settings: bool,
) -> tuple[WorkerRoute | None, str]:
    """``(route, motif)`` : la route du worker et, quand il n'y en a pas, pourquoi.

    Une seule décision pour :func:`route_worker` et :func:`route_denial` : le motif rendu dans ``task show``
    ne peut pas diverger des conditions qui ont réellement fermé la route.
    """
    if not provider_id or classify(task) is Verifiability.V2:
        return None, ""  # pas de route demandée, ou tâche V2 : ce n'est pas un refus
    provider = _enabled_provider(root, provider_id)
    if provider is None:
        return None, ""
    base_url = _admitted_base_url(provider)
    if not base_url:
        url = (provider.anthropic_base_url or "").strip()
        if not url:
            return None, f"{provider.id} : aucune anthropic_base_url dans le registre"
        if provider.provider_type == "local" and url.lower().startswith("http://"):
            return None, f"{provider.id} : http:// n'est admis que vers une boucle locale (127.0.0.1, localhost, ::1)"
        return None, f"{provider.id} : URL non admise pour un worker ({url})"
    if provider.provider_type == "local" and provider.id in busy_providers:
        return None, f"{provider.id} : un seul worker à la fois par fournisseur local, déjà occupé"
    refusal = data_policy_refusal(root, provider, task.data_class)
    if refusal:
        return None, refusal
    moment = now or datetime.now().astimezone()
    state = load_state(root)
    models = available_models(provider, tier, state, now=moment) or available_models(provider, "cheap", state, now=moment)
    if not models:
        return None, f"{provider.id} : aucun modèle disponible pour le palier {tier or 'cheap'}"
    key_var = _key_var(provider.id)
    if provider.provider_type != "local" and not key_var:
        name = f"{provider.id.upper().replace('-', '_')}_API_KEY"
        return None, f"{provider.id} : clé absente du magasin ({name})"
    if write_settings:
        settings = _write_settings(provider.id, base_url, models[0].id, key_var)
    else:
        settings = settings_path(provider.id)
    return WorkerRoute(provider.id, models[0].id, settings), ""


def route_denial(
    root: Path,
    provider_id: str,
    tier: str,
    task: MissionTask,
    *,
    busy_providers: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> str:
    """Pourquoi le worker de *task* n'est pas routé vers *provider_id* ; vide si rien ne l'en empêche.

    :func:`route_refusal` ne dit que la politique de données ; ce motif-ci couvre aussi l'URL, la clé, le
    palier disponible et l'occupation d'un fournisseur local — c'est lui que ``task show`` rend sous
    « repli sur l'hôte », pour qu'un refus ne soit jamais silencieux (#1154).
    """
    return _decide(
        root, provider_id, tier, task, busy_providers=busy_providers, now=now, write_settings=False
    )[1]


def is_local_provider(root: Path, provider_id: str) -> bool:
    """Vrai si *provider_id* est un fournisseur activé de type ``local``."""
    provider = _enabled_provider(root, provider_id)
    return provider is not None and provider.provider_type == "local"


def route_worker(
    root: Path,
    provider_id: str,
    tier: str,
    task: MissionTask,
    *,
    now: datetime | None = None,
    busy_providers: frozenset[str] = frozenset(),
) -> WorkerRoute | None:
    """La route du worker de *task* vers *provider_id*, ou ``None`` s'il garde son modèle habituel."""
    return _decide(root, provider_id, tier, task, busy_providers=busy_providers, now=now, write_settings=True)[0]
