"""Tests for D2 — Gas City Formula → Grimoire Recipe converter."""

from __future__ import annotations

from grimoire.runtime.gascity_converter import (
    GasCityConverter,
    GasCityConverterReport,
    GasCityFormula,
    GasCityMolecule,
)
from grimoire.runtime.recipes import Recipe


def _formula(name: str = "my-formula", molecules: list[dict] | None = None) -> dict:
    if molecules is None:
        molecules = [
            {"id": "step-1", "name": "Research", "role": "analyst", "tools": ["read", "search"],
             "outputs": ["findings"]},
            {"id": "step-2", "name": "Implement", "role": "dev", "tools": ["edit"],
             "inputs": ["findings"], "outputs": ["code"], "evidence_required": True},
        ]
    return {"name": name, "version": "1.0.0", "molecules": molecules,
            "description": "test formula", "output_schema": {"code": {"type": "string"}}}


class TestGasCityMolecule:
    def test_from_dict_roundtrip(self) -> None:
        d = {"id": "s1", "name": "Step 1", "role": "dev", "tools": ["read"],
             "inputs": ["a"], "outputs": ["b"], "evidence_required": True}
        mol = GasCityMolecule.from_dict(d)
        assert mol.id == "s1"
        assert mol.role == "dev"
        assert mol.evidence_required is True
        assert mol.to_dict()["tools"] == ["read"]

    def test_minimal_molecule(self) -> None:
        mol = GasCityMolecule.from_dict({"id": "x", "name": "X"})
        assert mol.role == ""
        assert mol.tools == ()
        assert mol.evidence_required is False


class TestGasCityFormula:
    def test_from_dict(self) -> None:
        f = GasCityFormula.from_dict(_formula())
        assert f.name == "my-formula"
        assert f.version == "1.0.0"
        assert len(f.molecules) == 2


class TestGasCityConverter:
    def test_convert_produces_recipe(self) -> None:
        converter = GasCityConverter()
        recipe, report = converter.convert(_formula())
        assert isinstance(recipe, Recipe)
        assert isinstance(report, GasCityConverterReport)

    def test_recipe_id_slugified(self) -> None:
        converter = GasCityConverter()
        recipe, _ = converter.convert(_formula("My Formula Name"))
        assert recipe.id == "gc.my-formula-name"

    def test_recipe_id_prefix(self) -> None:
        converter = GasCityConverter()
        recipe, _ = converter.convert(_formula("test"), recipe_id_prefix="custom")
        assert recipe.id.startswith("custom.")

    def test_steps_count_matches_molecules(self) -> None:
        converter = GasCityConverter()
        recipe, report = converter.convert(_formula())
        assert len(recipe.steps) == 2
        assert report.steps_converted == 2

    def test_role_mapped_to_step_roles(self) -> None:
        converter = GasCityConverter()
        recipe, _ = converter.convert(_formula())
        step1 = next(s for s in recipe.steps if s.id == "step-1")
        assert "analyst" in step1.roles

    def test_tools_mapped(self) -> None:
        converter = GasCityConverter()
        recipe, _ = converter.convert(_formula())
        step1 = next(s for s in recipe.steps if s.id == "step-1")
        assert "read" in step1.tools_allowed

    def test_evidence_required_creates_gate(self) -> None:
        converter = GasCityConverter()
        recipe, report = converter.convert(_formula())
        # step-2 has evidence_required=True
        gate_ids = {g.step_id for g in recipe.verification_gates}
        assert "step-2" in gate_ids
        assert report.verification_gates == 1

    def test_no_evidence_no_gate(self) -> None:
        converter = GasCityConverter()
        formula = _formula(molecules=[
            {"id": "s1", "name": "S1", "role": "dev", "tools": [], "outputs": [], "evidence_required": False},
        ])
        recipe, report = converter.convert(formula)
        assert len(recipe.verification_gates) == 0
        assert report.verification_gates == 0

    def test_experimental_tag(self) -> None:
        converter = GasCityConverter()
        recipe, _ = converter.convert(_formula())
        assert "experimental" in recipe.tags

    def test_gas_city_provenance_tag(self) -> None:
        converter = GasCityConverter()
        recipe, _ = converter.convert(_formula())
        assert "gas-city" in recipe.tags

    def test_output_schema_preserved(self) -> None:
        converter = GasCityConverter()
        recipe, _ = converter.convert(_formula())
        assert "code" in recipe.output_schema

    def test_deterministic(self) -> None:
        converter = GasCityConverter()
        formula = _formula()
        recipe1, _ = converter.convert(formula)
        recipe2, _ = converter.convert(formula)
        assert recipe1.id == recipe2.id
        assert len(recipe1.steps) == len(recipe2.steps)
        assert [s.id for s in recipe1.steps] == [s.id for s in recipe2.steps]

    def test_accepts_formula_object(self) -> None:
        converter = GasCityConverter()
        formula_obj = GasCityFormula.from_dict(_formula())
        _recipe, report = converter.convert(formula_obj)
        assert report.ok

    def test_report_to_dict(self) -> None:
        converter = GasCityConverter()
        _, report = converter.convert(_formula())
        d = report.to_dict()
        assert "formula_name" in d
        assert "recipe_id" in d
        assert "steps_converted" in d

    def test_empty_molecules(self) -> None:
        converter = GasCityConverter()
        recipe, report = converter.convert(_formula(molecules=[]))
        assert len(recipe.steps) == 0
        assert report.steps_converted == 0
        assert report.ok
