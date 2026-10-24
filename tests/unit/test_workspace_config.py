"""Lectures de l'espace Configuration (P66) : hooks et fournisseurs, sans écriture.

Les fichiers sont de vrais fichiers dans un dossier jetable ; les branches
d'erreur (absent, illisible, mal formé) sont celles que l'écran doit dire au
lieu de les rendre en liste vide.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grimoire.providers.state import record_failure, record_suspension
from grimoire.tools import workspace_config as wc
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, workspace_get

REGISTRY = Path("_grimoire-runtime/_config/hook-safety-registry.json")
PROVIDERS = Path("_grimoire/standard/llm-provider-registry.yaml")


def _registry(root: Path, hooks: object) -> None:
    path = root / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "hooks": hooks}), encoding="utf-8")


def test_les_deux_routes_sont_declarees_en_lecture() -> None:
    assert f"{PREFIX}hooks" in GET_ROUTES
    assert f"{PREFIX}providers" in GET_ROUTES


def test_registre_absent_n_est_pas_une_panne(tmp_path: Path) -> None:
    view = wc.hooks_view(tmp_path)
    assert view["registry"] is False
    assert view["error"] is None
    assert view["hooks"] == []
    assert view["total"] == 0


def test_modes_comptes_et_hotes_viennent_des_fichiers(tmp_path: Path) -> None:
    _registry(
        tmp_path,
        {
            "grimoire-session-start": {"event": "SessionStart", "mode": "enforced"},
            "grimoire-doc-drift": {"event": "PostToolUse", "mode": "canary"},
            "grimoire-stop-trace": {"event": "Stop", "mode": "shadow"},
            "grimoire-bizarre": {"event": "Stop", "mode": "autre"},
            "grimoire-sans-mode": "pas une table",
        },
    )
    hooks_dir = tmp_path / ".github" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "grimoire-session-start.json").write_text("{}", encoding="utf-8")
    (hooks_dir / "x.json").write_text(
        '{"command": "gateway.sh --hook-id grimoire-doc-drift --event PostToolUse"}', encoding="utf-8"
    )
    (hooks_dir / "illisible.json").write_bytes(b"\xff\xfe\x00bad")
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "hooks.json").write_text("--hook-id grimoire-stop-trace-bis", encoding="utf-8")

    view = wc.hooks_view(tmp_path)
    by_id = {h["id"]: h for h in view["hooks"]}
    assert by_id["grimoire-session-start"]["label"] == "appliqué"
    assert by_id["grimoire-session-start"]["tone"] == "ok"
    assert by_id["grimoire-doc-drift"]["label"] == "essai"
    assert by_id["grimoire-stop-trace"]["label"] == "observation"
    assert by_id["grimoire-bizarre"]["label"] == "autre"
    assert by_id["grimoire-sans-mode"]["label"] == "inconnu"
    assert view["counts"]["appliqué"] == 1
    assert view["total"] == 5
    # Hôtes : seulement sur un nom explicite ; le préfixe d'un autre identifiant n'en est pas un.
    assert by_id["grimoire-session-start"]["hosts"] == ["copilot"]
    assert by_id["grimoire-doc-drift"]["hosts"] == ["copilot"]
    assert by_id["grimoire-stop-trace"]["hosts"] == []
    assert [h["id"] for h in view["hosts"]] == ["copilot"]


def test_registre_illisible_dit_son_motif(tmp_path: Path) -> None:
    path = tmp_path / REGISTRY
    path.parent.mkdir(parents=True)
    path.write_text("{pas du json", encoding="utf-8")
    view = wc.hooks_view(tmp_path)
    assert view["registry"] is True
    assert "illisible" in view["error"]
    assert view["hooks"] == []

    path.write_bytes(b"\xff\xfe\x00")
    assert "illisible" in wc.hooks_view(tmp_path)["error"]


def test_registre_sans_table_hooks(tmp_path: Path) -> None:
    path = tmp_path / REGISTRY
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    assert "sans table" in wc.hooks_view(tmp_path)["error"]
    path.write_text("[]", encoding="utf-8")
    assert "sans table" in wc.hooks_view(tmp_path)["error"]


def test_providers_sans_registre(tmp_path: Path) -> None:
    view = wc.providers_view(tmp_path)
    assert view["providers"] == []
    assert view["error"] is None
    assert view["registry"] is False


def test_providers_registre_mal_forme_dit_l_erreur(tmp_path: Path) -> None:
    path = tmp_path / PROVIDERS
    path.parent.mkdir(parents=True)
    path.write_text("providers: [this is not: valid: yaml", encoding="utf-8")
    view = wc.providers_view(tmp_path)
    assert view["providers"] == []
    assert "illisible" in view["error"]


def test_providers_etat_palier_et_refroidissement(tmp_path: Path) -> None:
    path = tmp_path / PROVIDERS
    path.parent.mkdir(parents=True)
    path.write_text(
        """\
providers:
  - id: anthropic
    enabled: true
    currency: quota
    models:
      - {id: haiku, tier: cheap}
      - {id: opus, tier: premium}
  - id: local
    enabled: true
    currency: local
    models:
      - {id: qwen3-coder, tier: cheap}
  - id: openai
    enabled: false
routing:
  default_provider: anthropic
""",
        encoding="utf-8",
    )
    record_failure(tmp_path, "local", "rate_limit")
    view = wc.providers_view(tmp_path)
    by_id = {p["id"]: p for p in view["providers"]}
    assert by_id["anthropic"]["status"] == "disponible"
    assert by_id["anthropic"]["tiers"] == ["cheap", "premium"]
    assert by_id["local"]["status"] == "refroidi"
    assert by_id["local"]["tone"] == "warn"
    assert by_id["openai"]["status"] == "désactivé"
    assert view["registry"] is True
    assert view["next_choice"]["cheap"] == "anthropic"


def test_les_routes_repondent_par_workspace_get(tmp_path: Path) -> None:
    _registry(tmp_path, {"h": {"event": "Stop", "mode": "enforced"}})
    hooks = workspace_get(tmp_path, f"{PREFIX}hooks", {})
    assert hooks["total"] == 1
    providers = workspace_get(tmp_path, f"{PREFIX}providers", {})
    assert providers["providers"] == []


def test_providers_suspendu_n_a_aucun_prochain_choix(tmp_path: Path) -> None:
    path = tmp_path / PROVIDERS
    path.parent.mkdir(parents=True)
    path.write_text(
        "providers:\n  - id: seul\n    enabled: true\n    models:\n      - {id: m, tier: cheap}\n",
        encoding="utf-8",
    )
    record_suspension(tmp_path, "seul", until=datetime.now(UTC) + timedelta(hours=1), source="test")
    view = wc.providers_view(tmp_path)
    assert view["providers"][0]["status"] == "suspendu"
    assert view["providers"][0]["tone"] == "bad"
    # Aucun fournisseur disponible : la clé existe, sa valeur est None (jamais un identifiant inventé).
    assert view["next_choice"]["cheap"] is None
