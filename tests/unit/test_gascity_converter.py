"""Tests for grimoire.runtime.gascity_converter — Gas City formula → Recipe conversion."""

from __future__ import annotations

from typing import Any

from grimoire.evidence.schemas import EvidenceProfile
from grimoire.runtime.gascity_converter import (
    GasCityConverter,
    GasCityConverterReport,
    GasCityFormula,
    GasCityMolecule,
)


class TestGasCityMolecule:
    def test_to_dict_minimal(self) -> None:
        mol = GasCityMolecule(id="s1", name="Step 1")
        assert mol.to_dict() == {"id": "s1", "name": "Step 1"}

    def test_to_dict_full(self) -> None:
        mol = GasCityMolecule(
            id="s1",
            name="Research",
            role="analyst",
            tools=("read", "search"),
            inputs=("brief",),
            outputs=("findings",),
            evidence_required=True,
            description="do research",
        )
        d = mol.to_dict()
        assert d["role"] == "analyst"
        assert d["tools"] == ["read", "search"]
        assert d["inputs"] == ["brief"]
        assert d["outputs"] == ["findings"]
        assert d["evidence_required"] is True
        assert d["description"] == "do research"

    def test_from_dict_defaults_name_to_id(self) -> None:
        mol = GasCityMolecule.from_dict({"id": "x"})
        assert mol.name == "x"
        assert mol.tools == ()
        assert mol.evidence_required is False


class TestGasCityFormula:
    def test_from_dict_defaults(self) -> None:
        formula = GasCityFormula.from_dict({"name": "f"})
        assert formula.version == "1.0.0"
        assert formula.molecules == ()
        assert formula.output_schema == {}
        assert formula.tags == ()

    def test_from_dict_full(self) -> None:
        formula = GasCityFormula.from_dict(
            {
                "name": "pipeline",
                "version": "2.1.0",
                "description": "demo",
                "molecules": [{"id": "a"}, {"id": "b"}],
                "output_schema": {"findings": {"type": "string"}},
                "tags": ["demo"],
            }
        )
        assert formula.version == "2.1.0"
        assert len(formula.molecules) == 2
        assert formula.output_schema == {"findings": {"type": "string"}}
        assert formula.tags == ("demo",)


class TestGasCityConverterReport:
    def test_ok_true_without_errors(self) -> None:
        report = GasCityConverterReport(formula_name="f", recipe_id="gc.f")
        assert report.ok is True

    def test_ok_false_with_errors_and_to_dict(self) -> None:
        report = GasCityConverterReport(
            formula_name="f", recipe_id="gc.f", steps_converted=2, verification_gates=1
        )
        report.errors.append("boom")
        assert report.ok is False
        assert report.to_dict() == {
            "formula_name": "f",
            "recipe_id": "gc.f",
            "steps_converted": 2,
            "verification_gates": 1,
            "errors": ["boom"],
        }


class TestGasCityConverter:
    def _formula(self) -> dict[str, Any]:
        return {
            "name": "Research Pipeline!",
            "version": "1.2.0",
            "description": "a pipeline",
            "molecules": [
                {
                    "id": "research",
                    "name": "Research",
                    "role": "analyst",
                    "tools": ["read", "search"],
                    "inputs": ["brief"],
                    "outputs": ["findings"],
                    "evidence_required": True,
                },
                {"id": "summarize", "name": "Summarize"},
            ],
            "tags": ["custom"],
        }

    def test_convert_from_dict_builds_experimental_recipe(self) -> None:
        recipe, report = GasCityConverter().convert(self._formula())

        # Slugified, prefixed recipe id (deterministic).
        assert recipe.id == "gc.research-pipeline"
        assert recipe.name == "Research Pipeline!"
        assert recipe.version == "1.2.0"

        # Experimental + provenance tags always present, user tags appended.
        assert "experimental" in recipe.tags
        assert "gas-city" in recipe.tags
        assert "custom" in recipe.tags

        # Never auto-activated → light evidence profile.
        assert recipe.evidence_profile == EvidenceProfile.LIGHT

        # Two molecules → two steps; one evidence_required → one gate.
        assert len(recipe.steps) == 2
        assert report.steps_converted == 2
        assert report.verification_gates == 1
        assert len(recipe.verification_gates) == 1
        assert recipe.verification_gates[0].step_id == "research"
        assert recipe.verification_gates[0].blocking is True
        assert report.ok is True

    def test_convert_accepts_formula_object_and_custom_prefix(self) -> None:
        formula = GasCityFormula.from_dict(self._formula())
        recipe, report = GasCityConverter().convert(formula, recipe_id_prefix="exp")
        assert recipe.id == "exp.research-pipeline"
        assert report.recipe_id == "exp.research-pipeline"

    def test_step_description_falls_back_to_inputs(self) -> None:
        recipe, _ = GasCityConverter().convert(
            {
                "name": "f",
                "molecules": [
                    {"id": "s", "name": "S", "inputs": ["alpha", "beta"]},
                ],
            }
        )
        assert recipe.steps[0].description == "Inputs: alpha, beta"

    def test_convert_is_deterministic(self) -> None:
        formula = self._formula()
        r1, _ = GasCityConverter().convert(formula)
        r2, _ = GasCityConverter().convert(formula)
        assert r1.id == r2.id
        assert [s.id for s in r1.steps] == [s.id for s in r2.steps]

    def test_empty_molecules_yields_no_steps(self) -> None:
        recipe, report = GasCityConverter().convert({"name": "empty"})
        assert recipe.steps == ()
        assert report.steps_converted == 0
        assert report.verification_gates == 0
        assert report.ok is True
