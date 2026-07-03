from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
REGISTRY = ROOT / "framework" / "registry" / "compiled-flow-recipes.json"

sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("compiled_flow", TOOLS / "compiled-flow.py")
compiled_flow = importlib.util.module_from_spec(_spec)
sys.modules["compiled_flow"] = compiled_flow
_spec.loader.exec_module(compiled_flow)


def _seed_registry(project_root: Path) -> None:
    registry_dir = project_root / "framework" / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.joinpath("compiled-flow-recipes.json").write_text(
        REGISTRY.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_loads_base_registry() -> None:
    bundle = compiled_flow.load_registry_bundle(ROOT)
    recipe_ids = {recipe.id for recipe in bundle.recipes}
    assert "ci-diagnosis" in recipe_ids
    assert "quality-loop" in recipe_ids


def test_matches_ci_recipe_first() -> None:
    bundle = compiled_flow.load_registry_bundle(ROOT)
    matches = compiled_flow.match_recipes(
        "La CI GitHub Actions casse sur les status checks du workflow",
        bundle.recipes,
    )
    assert matches
    assert matches[0].recipe_id == "ci-diagnosis"


def test_render_quality_loop_commands_contains_tasks() -> None:
    bundle = compiled_flow.load_registry_bundle(ROOT)
    rendered = compiled_flow.render_surface(bundle, "quality-loop", "commands", ROOT)
    assert "task: grimoire: lint" in rendered
    assert "task: grimoire: preflight" in rendered


def test_hook_context_mentions_dynamic_governance() -> None:
    bundle = compiled_flow.load_registry_bundle(ROOT)
    context = compiled_flow.build_hook_context(
        "Simplifier le flow agentique avec des templates de rapport et des hooks fins",
        bundle,
        ROOT,
    )
    assert "COMPILED_FLOW_MATCHES" in context
    assert "dynamic recipe" in context


def test_extract_prompt_text_from_json_payload() -> None:
    payload = json.dumps({"prompt": "bonjour", "promptPreview": "salut"})
    assert compiled_flow.extract_prompt_text(payload) == "bonjour salut"


def test_scaffold_writes_dynamic_overlay_and_reloads(tmp_path: Path) -> None:
    _seed_registry(tmp_path)
    payload = compiled_flow.scaffold_recipe_payload(
        recipe_id="local-ci-fast-path",
        title="Local CI fast path",
        intent="diagnostic local rapide",
    )
    output_path = compiled_flow.write_scaffold_recipe(tmp_path, payload)
    assert output_path.exists()

    bundle = compiled_flow.load_registry_bundle(tmp_path)
    recipe_ids = {recipe.id for recipe in bundle.recipes}
    assert "local-ci-fast-path" in recipe_ids


def test_validate_detects_forbidden_token_in_universal_recipe(tmp_path: Path) -> None:
    _seed_registry(tmp_path)
    dynamic_dir = tmp_path / "_grimoire-runtime-output" / "implementation-artifacts" / "compiled-flow" / "recipes"
    dynamic_dir.mkdir(parents=True, exist_ok=True)
    dynamic_dir.joinpath("bad-universal.json").write_text(
        json.dumps(
            {
                "id": "bad-universal",
                "title": "Bad universal recipe",
                "summary": "Should fail validation",
                "scope": "universal",
                "kind": "execution",
                "risk_class": "read_only",
                "keywords": ["bad"],
                "intent_patterns": ["bad"],
                "chat_template": "implementation_summary_chat",
                "report_template": "diagnostic_report",
                "hook_context": "Do not use.",
                "commands": [
                    {
                        "id": "bad-shell",
                        "label": "Bad shell",
                        "mode": "shell",
                        "template": "/home/test/run-me.sh"
                    }
                ],
                "governance": {
                    "mutate_universal_only_for": ["portability"],
                    "create_dynamic_when": ["one-off use case"],
                    "forbid": ["home paths"]
                }
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    issues = compiled_flow.validate_registry(tmp_path)
    assert any("forbidden token '/home/'" in issue.message for issue in issues)


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("chat", "Response contract:"),
        ("report", "## Objective"),
    ],
)
def test_render_templates(surface: str, expected: str) -> None:
    bundle = compiled_flow.load_registry_bundle(ROOT)
    rendered = compiled_flow.render_surface(
        bundle,
        "ci-diagnosis",
        surface,
        ROOT,
        {"OBJECTIVE": "Diagnostiquer la CI"},
    )
    assert expected in rendered