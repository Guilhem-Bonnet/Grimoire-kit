"""Aucun geste à clé de contrôle par dictée : un point de passage commun refuse un texte dicté sur toutes les routes
qui exigent la clé (mandat, pilote automatique, fournisseurs, clés de fournisseurs, acceptation en lot)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.missions import mandate as md
from grimoire.providers.registry import read_registry
from grimoire.tools.human_gesture import AUTHORITY_DICTATION_REFUSAL, require_human_key
from grimoire.tools.workspace_routes import POST_ROUTES, PREFIX

STANDARD = Path("_grimoire/standard")
REGISTRY = """\
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

ROUTES = {
    "mandate": f"{PREFIX}autonomy/mandate/grant",
    "autopilot": f"{PREFIX}autonomy/autopilot",
    "providers": f"{PREFIX}providers",
    "set-key": f"{PREFIX}providers/set-key",
    "accept-merged": f"{PREFIX}tasks/accept-merged",
}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
    return root


def _key() -> str:
    return md.ensure_control_key().read_text(encoding="utf-8").strip()


def _body(route: str) -> dict[str, Any]:
    return {
        "mandate": {"gestures": ["merge"], "task_types": ["feat"], "max_sessions_per_day": 2, "until": "2099-01-01"},
        "autopilot": {"on": True},
        "providers": {"action": "disable", "provider_id": "anthropic"},
        "set-key": {"provider_id": "anthropic", "env_var": "ANTHROPIC_API_KEY", "value": "x"},
        "accept-merged": {},
    }[route]


@pytest.mark.parametrize("route", list(ROUTES))
def test_une_dictee_avec_la_cle_valide_ne_declenche_aucun_geste_d_autorite(
    project: Path, tmp_path: Path, route: str
) -> None:
    handler = POST_ROUTES[ROUTES[route]]
    body = {**_body(route), "control_key": _key(), "dictation": True}
    with pytest.raises(ValueError, match="ne se dicte pas"):
        handler(project, body)
    assert md.AuthorityJournal().read() == []  # rien au journal d'autorité
    assert [p.id for p in read_registry(project) if not p.enabled] == []  # registre inchangé
    assert not (tmp_path / "home" / ".env-store").exists()  # aucune clé posée


@pytest.mark.parametrize("route", list(ROUTES))
def test_sans_cle_une_dictee_est_refusee_comme_dictee_et_une_saisie_comme_non_autorisee(
    project: Path, route: str
) -> None:
    handler = POST_ROUTES[ROUTES[route]]
    with pytest.raises(ValueError, match="ne se dicte pas"):
        handler(project, {**_body(route), "dictation": True})
    with pytest.raises(PermissionError, match="clé de contrôle"):
        handler(project, _body(route))


def test_cas_nominal_inchange_cle_valide_sans_dictee(project: Path) -> None:
    require_human_key(project, {"control_key": _key()}, "refus")  # ne lève rien
    with pytest.raises(PermissionError, match="refus"):
        require_human_key(project, {"control_key": "faux"}, "refus")
    disabled = POST_ROUTES[ROUTES["providers"]](project, {**_body("providers"), "control_key": _key()})
    assert disabled["action"] == "disable"
    assert POST_ROUTES[ROUTES["accept-merged"]](project, {"control_key": _key()}) is not None
    assert AUTHORITY_DICTATION_REFUSAL not in str(disabled)
