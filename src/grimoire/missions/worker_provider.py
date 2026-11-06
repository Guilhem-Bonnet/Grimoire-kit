"""Router un worker du tick vers un fournisseur compatible avec l'API d'Anthropic.

``claude --bg`` garde alors ses outils, ses hooks et ses gardes ; seul le modèle change. La route passe par
``--settings`` et non par l'environnement : celui d'un ``--bg`` n'atteint que le démon qu'il démarre, il serait
ignoré par un démon déjà lancé et s'imposerait à toutes les sessions suivantes s'il le démarrait.

Une condition manquante (fournisseur fermé, modèle suspendu, clé absente) rend ``None`` : le worker part sur le
modèle habituel. Une tâche V2 n'est jamais routée : rien ne pourrait juger seul le travail d'un modèle moins cher.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from grimoire.missions.schemas import MissionTask
from grimoire.missions.verifiability import Verifiability, classify
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


def _token(provider: str) -> str:
    secrets = env_for_provider(provider)
    return secrets.get(f"{provider.upper().replace('-', '_')}_API_KEY", "")


def _write_settings(provider: str, base_url: str, model: str, token: str) -> Path:
    env = {"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": token}
    env.update(dict.fromkeys(_SUBAGENT_MODEL_VARS, model))
    path = settings_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"env": env, "permissions": {"deny": list(_DENIED_READS)}}, stream)
    temp.replace(path)
    return path


def route_worker(
    root: Path, provider_id: str, tier: str, task: MissionTask, *, now: datetime | None = None
) -> WorkerRoute | None:
    """La route du worker de *task* vers *provider_id*, ou ``None`` s'il garde son modèle habituel."""
    if not provider_id or classify(task) is Verifiability.V2:
        return None
    provider = next((p for p in read_registry(root) if p.id == provider_id and p.enabled), None)
    if provider is None or not (provider.anthropic_base_url or "").startswith("https://"):
        return None
    moment = now or datetime.now().astimezone()
    state = load_state(root)
    models = available_models(provider, tier, state, now=moment) or available_models(provider, "cheap", state, now=moment)
    token = _token(provider.id)
    if not models or not token:
        return None
    settings = _write_settings(provider.id, str(provider.anthropic_base_url), models[0].id, token)
    return WorkerRoute(provider.id, models[0].id, settings)
