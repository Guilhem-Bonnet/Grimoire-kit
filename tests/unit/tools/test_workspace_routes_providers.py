"""``POST /api/workspace/providers`` et ``.../providers/set-key`` (issue #310 suite).

Même garde que l'autopilote (:mod:`grimoire.missions.mandate`) : la clé de
contrôle est requise, le jeton de session seul ne suffit jamais. Ces tests
appellent les handlers directement (pas le serveur HTTP complet, déjà couvert
pour le reste de la coque par ``test_workspace_routes.py``) — ils isolent
``HOME`` puisque ``providers/set-key`` écrit dans ``~/.env-store/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.missions import mandate as md
from grimoire.providers.registry import read_registry
from grimoire.providers.secrets import env_for_provider
from grimoire.tools.workspace_routes import POST_ROUTES, PREFIX

STANDARD = Path("_grimoire/standard")
REGISTRY_PATH = STANDARD / "llm-provider-registry.yaml"

_REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["claude-sonnet-4.6"]
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "anthropic"
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
"""


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / STANDARD).mkdir(parents=True)
    (root / REGISTRY_PATH).write_text(_REGISTRY, encoding="utf-8")
    return root


def _control_key() -> str:
    return md.ensure_control_key().read_text(encoding="utf-8").strip()


def test_providers_write_is_registered() -> None:
    assert f"{PREFIX}providers" in POST_ROUTES
    assert f"{PREFIX}providers/set-key" in POST_ROUTES


def test_add_without_control_key_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path)
    handler = POST_ROUTES[f"{PREFIX}providers"]

    with pytest.raises(PermissionError):
        handler(root, {"action": "add", "provider_id": "mistral", "invocation": "x {model} {prompt}"})

    assert [p.id for p in read_registry(root)] == ["anthropic"]  # rien écrit


def test_add_with_control_key_writes_the_provider(tmp_path: Path) -> None:
    root = _project(tmp_path)
    handler = POST_ROUTES[f"{PREFIX}providers"]

    result = handler(
        root,
        {
            "action": "add",
            "provider_id": "mistral",
            "invocation": "mistral-cli --model {model} {prompt}",
            "models": [{"id": "mistral-small", "tier": "cheap"}],
            "control_key": _control_key(),
        },
    )

    assert result["action"] == "add"
    assert "mistral" in {p.id for p in read_registry(root)}


def test_disable_then_enable_via_route(tmp_path: Path) -> None:
    root = _project(tmp_path)
    handler = POST_ROUTES[f"{PREFIX}providers"]
    key = _control_key()

    handler(root, {"action": "disable", "provider_id": "anthropic", "control_key": key})
    assert next(p for p in read_registry(root) if p.id == "anthropic").enabled is False

    handler(root, {"action": "enable", "provider_id": "anthropic", "control_key": key})
    assert next(p for p in read_registry(root) if p.id == "anthropic").enabled is True


def test_unknown_action_is_a_value_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    handler = POST_ROUTES[f"{PREFIX}providers"]

    with pytest.raises(ValueError, match="inconnue"):
        handler(root, {"action": "nope", "provider_id": "anthropic", "control_key": _control_key()})


def test_set_key_without_control_key_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path)
    handler = POST_ROUTES[f"{PREFIX}providers/set-key"]

    with pytest.raises(PermissionError):
        handler(root, {"provider_id": "deepseek", "env_var": "DEEPSEEK_API_KEY", "value": "sk-test"})

    assert env_for_provider("deepseek") == {}


def test_set_key_with_control_key_writes_to_the_store_not_the_registry(tmp_path: Path) -> None:
    root = _project(tmp_path)
    handler = POST_ROUTES[f"{PREFIX}providers/set-key"]

    handler(
        root,
        {
            "provider_id": "deepseek",
            "env_var": "DEEPSEEK_API_KEY",
            "value": "sk-test-abc",
            "control_key": _control_key(),
        },
    )

    assert env_for_provider("deepseek") == {"DEEPSEEK_API_KEY": "sk-test-abc"}
    registry_text = (root / REGISTRY_PATH).read_text(encoding="utf-8")
    assert "sk-test-abc" not in registry_text  # jamais dans le registre du projet
