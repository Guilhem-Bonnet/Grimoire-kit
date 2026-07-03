"""Tests for runtime/recipes.py — Recipe schema and RecipeRegistry."""

from __future__ import annotations

import json

import pytest

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.evidence.schemas import EvidenceProfile
from grimoire.runtime.recipes import (
    Recipe,
    RecipeRegistry,
    RecipeRetryProfile,
    RecipeStep,
    VerificationGate,
)


def _make_recipe(recipe_id: str = "recipe.test.basic", version: str = "1.0.0") -> Recipe:
    return Recipe(
        id=recipe_id,
        name="Basic Test Recipe",
        version=version,
        steps=(
            RecipeStep(id="step-parse", name="Parse", roles=("dev",), tools_allowed=("read",)),
            RecipeStep(id="step-validate", name="Validate", evidence_required=True),
        ),
        roles=("dev", "qa"),
        tools_allowed=("read", "edit"),
        policy_profile="standard",
        evidence_profile=EvidenceProfile.STANDARD,
        verification_gates=(
            VerificationGate(step_id="step-validate", required_evidence_kinds=("test",)),
        ),
        tags=("test", "basic"),
        created_at="2026-01-01T00:00:00+00:00",
    )


class TestRecipeSchema:
    def test_roundtrip(self) -> None:
        r = _make_recipe()
        assert Recipe.from_dict(r.to_dict()) == r

    def test_step_roundtrip(self) -> None:
        step = RecipeStep(id="s1", name="S1", roles=("dev",), tools_allowed=("read",), evidence_required=True)
        assert RecipeStep.from_dict(step.to_dict()) == step

    def test_retry_profile_defaults(self) -> None:
        rp = RecipeRetryProfile()
        assert rp.max_retries == 3
        assert RecipeRetryProfile.from_dict(rp.to_dict()) == rp

    def test_verification_gate_roundtrip(self) -> None:
        gate = VerificationGate(step_id="step-x", required_evidence_kinds=("test", "log"), blocking=False)
        assert VerificationGate.from_dict(gate.to_dict()) == gate

    def test_evidence_profile_preserved(self) -> None:
        r = _make_recipe()
        d = r.to_dict()
        assert d["evidence_profile"] == "standard"
        restored = Recipe.from_dict(d)
        assert restored.evidence_profile == EvidenceProfile.STANDARD

    def test_steps_ordered(self) -> None:
        r = _make_recipe()
        assert r.steps[0].id == "step-parse"
        assert r.steps[1].id == "step-validate"


class TestRecipeRegistry:
    def test_register_and_get(self, tmp_path) -> None:
        reg = RecipeRegistry(tmp_path)
        r = _make_recipe()
        reg.register(r, persist=False)
        assert reg.get(r.id) == r

    def test_get_unknown_returns_none(self, tmp_path) -> None:
        reg = RecipeRegistry(tmp_path)
        assert reg.get("does.not.exist") is None

    def test_get_or_raise(self, tmp_path) -> None:
        reg = RecipeRegistry(tmp_path)
        with pytest.raises(GrimoireRuntimeError):
            reg.get_or_raise("does.not.exist")

    def test_persist_and_reload(self, tmp_path) -> None:
        reg = RecipeRegistry(tmp_path)
        r = _make_recipe()
        reg.register(r, persist=True)
        reg2 = RecipeRegistry(tmp_path)
        reg2.load_directory()
        assert reg2.get(r.id) == r

    def test_list_recipes(self, tmp_path) -> None:
        reg = RecipeRegistry(tmp_path)
        reg.register(_make_recipe("a.b.c"), persist=False)
        reg.register(_make_recipe("a.b.d"), persist=False)
        recipes = reg.list_recipes()
        assert len(recipes) == 2
        assert recipes[0].id == "a.b.c"

    def test_multi_version_returns_latest(self, tmp_path) -> None:
        reg = RecipeRegistry(tmp_path)
        reg.register(_make_recipe(version="1.0.0"), persist=False)
        reg.register(_make_recipe(version="2.0.0"), persist=False)
        latest = reg.get("recipe.test.basic")
        assert latest is not None
        assert latest.version == "2.0.0"

    def test_load_directory_json_list(self, tmp_path) -> None:
        r = _make_recipe("bulk.recipe")
        (tmp_path / "bulk.json").write_text(json.dumps([r.to_dict()]), encoding="utf-8")
        reg = RecipeRegistry(tmp_path)
        count = reg.load_directory()
        assert count == 1
        assert reg.get("bulk.recipe") is not None

    def test_load_directory_skips_invalid(self, tmp_path) -> None:
        (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
        reg = RecipeRegistry(tmp_path)
        count = reg.load_directory()
        assert count == 0
