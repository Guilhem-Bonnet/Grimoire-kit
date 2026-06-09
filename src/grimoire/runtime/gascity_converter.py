"""D2 — Gas City Formula → Grimoire Recipe converter.

Converts Gas City formula definitions (molecules/steps) into experimental
Grimoire Recipes with provenance tracking.

Guardrails:
- Generated recipes are marked status=experimental and never auto-activated.
- Provenance field records upstream source (gas-city).
- Conversion is deterministic: same formula input → same recipe output.

Gas City formula format (YAML/JSON dict)::

    {
      "name": "formula-name",
      "version": "1.0.0",
      "description": "...",
      "molecules": [
        {
          "id": "step-1",
          "name": "Research",
          "role": "analyst",
          "tools": ["read", "search"],
          "outputs": ["findings"],
          "evidence_required": false
        }
      ],
      "output_schema": {"findings": {"type": "string"}}
    }
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from grimoire.evidence.schemas import EvidenceProfile
from grimoire.runtime.recipes import Recipe, RecipeStep, VerificationGate

__all__ = [
    "GasCityConverter",
    "GasCityConverterReport",
    "GasCityFormula",
    "GasCityMolecule",
]

_SCHEMA_VERSION = "grimoire.recipe.v1"
_PROVENANCE_TAG = "gas-city"
_EXPERIMENTAL_TAG = "experimental"


@dataclass(frozen=True, slots=True)
class GasCityMolecule:
    id: str
    name: str
    role: str = ""
    tools: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    evidence_required: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.role:
            d["role"] = self.role
        if self.tools:
            d["tools"] = list(self.tools)
        if self.inputs:
            d["inputs"] = list(self.inputs)
        if self.outputs:
            d["outputs"] = list(self.outputs)
        if self.evidence_required:
            d["evidence_required"] = True
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GasCityMolecule:
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            role=str(d.get("role", "")),
            tools=tuple(str(t) for t in d.get("tools", [])),
            inputs=tuple(str(i) for i in d.get("inputs", [])),
            outputs=tuple(str(o) for o in d.get("outputs", [])),
            evidence_required=bool(d.get("evidence_required", False)),
            description=str(d.get("description", "")),
        )


@dataclass(frozen=True, slots=True)
class GasCityFormula:
    name: str
    version: str
    molecules: tuple[GasCityMolecule, ...]
    description: str = ""
    output_schema: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GasCityFormula:
        return cls(
            name=str(d["name"]),
            version=str(d.get("version", "1.0.0")),
            molecules=tuple(GasCityMolecule.from_dict(m) for m in d.get("molecules", [])),
            description=str(d.get("description", "")),
            output_schema=dict(d.get("output_schema") or {}),
            tags=tuple(str(t) for t in d.get("tags", [])),
        )


@dataclass
class GasCityConverterReport:
    formula_name: str
    recipe_id: str
    steps_converted: int = 0
    verification_gates: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_name": self.formula_name,
            "recipe_id": self.recipe_id,
            "steps_converted": self.steps_converted,
            "verification_gates": self.verification_gates,
            "errors": self.errors,
        }


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64]


class GasCityConverter:
    """Convert Gas City formula dicts into Grimoire Recipes (experimental).

    Usage::

        converter = GasCityConverter()
        recipe, report = converter.convert(formula_dict)
        registry.register(recipe)
    """

    def convert(
        self,
        formula: dict[str, Any] | GasCityFormula,
        *,
        recipe_id_prefix: str = "gc",
    ) -> tuple[Recipe, GasCityConverterReport]:
        """Convert a Gas City formula to a Grimoire Recipe.

        Returns (Recipe, GasCityConverterReport). The recipe is tagged
        experimental and gas-city, and is never auto-activated.
        """
        if isinstance(formula, dict):
            formula = GasCityFormula.from_dict(formula)

        recipe_id = f"{recipe_id_prefix}.{_slugify(formula.name)}"
        report = GasCityConverterReport(formula_name=formula.name, recipe_id=recipe_id)

        steps: list[RecipeStep] = []
        gates: list[VerificationGate] = []

        for mol in formula.molecules:
            step = RecipeStep(
                id=mol.id,
                name=mol.name,
                description=mol.description or (
                    f"Inputs: {', '.join(mol.inputs)}" if mol.inputs else ""
                ),
                roles=(mol.role,) if mol.role else (),
                tools_allowed=mol.tools,
                evidence_required=mol.evidence_required,
                outputs=mol.outputs,
            )
            steps.append(step)
            report.steps_converted += 1

            if mol.evidence_required:
                gates.append(VerificationGate(
                    step_id=mol.id,
                    required_evidence_kinds=("test",),
                    blocking=True,
                ))
                report.verification_gates += 1

        now = datetime.now(tz=UTC).isoformat()
        recipe = Recipe(
            id=recipe_id,
            name=formula.name,
            version=formula.version,
            description=formula.description,
            steps=tuple(steps),
            verification_gates=tuple(gates),
            output_schema=formula.output_schema,
            evidence_profile=EvidenceProfile.LIGHT,
            tags=(*(_EXPERIMENTAL_TAG, _PROVENANCE_TAG), *formula.tags),
            created_at=now,
            updated_at=now,
        )
        return recipe, report
