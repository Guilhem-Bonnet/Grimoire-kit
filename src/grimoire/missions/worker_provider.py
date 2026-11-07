"""Router un worker du tick vers un fournisseur compatible avec l'API d'Anthropic.

``claude --bg`` garde alors ses outils, ses hooks et ses gardes ; seul le modèle change. La route passe par
``--settings`` et non par l'environnement : celui d'un ``--bg`` n'atteint que le démon qu'il démarre, il serait
ignoré par un démon déjà lancé et s'imposerait à toutes les sessions suivantes s'il le démarrait.

Une condition manquante (fournisseur fermé, modèle suspendu, clé absente) rend ``None`` : le worker part sur le
modèle habituel. Une tâche V2 n'est jamais routée : rien ne pourrait juger seul le travail d'un modèle moins cher.
Une tâche dont la classe de données n'est pas admise par le fournisseur non plus (#1133) : voir :func:`route_refusal`.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from grimoire.core.fsio import atomic_write
from grimoire.missions.schemas import MissionTask
from grimoire.missions.verifiability import Verifiability, classify
from grimoire.providers.data_policy import data_policy_refusal
from grimoire.providers.registry import read_registry
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


def _write_settings(provider: str, base_url: str, model: str, key_var: str) -> Path:
    """Les réglages de la session : la clé reste dans le magasin, `apiKeyHelper` l'y relit à chaque besoin."""
    env = {"ANTHROPIC_BASE_URL": base_url}
    env.update(dict.fromkeys(_SUBAGENT_MODEL_VARS, model))
    helper = f"sed -n 's/^{key_var}=//p' {shlex.quote(str(store_dir() / f'{provider}.env'))}"
    path = settings_path(provider)
    atomic_write(path, json.dumps({"apiKeyHelper": helper, "env": env, "permissions": {"deny": list(_DENIED_READS)}}))
    return path


def route_refusal(root: Path, provider_id: str, task: MissionTask) -> str:
    """Pourquoi la politique de données ferme *provider_id* à *task* ; vide si elle ne s'y oppose pas."""
    provider = next((p for p in read_registry(root) if p.id == provider_id and p.enabled), None)
    return data_policy_refusal(root, provider, task.data_class) if provider is not None else ""


def route_worker(
    root: Path, provider_id: str, tier: str, task: MissionTask, *, now: datetime | None = None
) -> WorkerRoute | None:
    """La route du worker de *task* vers *provider_id*, ou ``None`` s'il garde son modèle habituel."""
    if not provider_id or classify(task) is Verifiability.V2:
        return None
    provider = next((p for p in read_registry(root) if p.id == provider_id and p.enabled), None)
    if provider is None or not (provider.anthropic_base_url or "").startswith("https://"):
        return None
    if data_policy_refusal(root, provider, task.data_class):
        return None
    moment = now or datetime.now().astimezone()
    state = load_state(root)
    models = available_models(provider, tier, state, now=moment) or available_models(provider, "cheap", state, now=moment)
    key_var = _key_var(provider.id)
    if not models or not key_var:
        return None
    settings = _write_settings(provider.id, str(provider.anthropic_base_url), models[0].id, key_var)
    return WorkerRoute(provider.id, models[0].id, settings)
