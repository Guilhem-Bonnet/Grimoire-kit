"""Workflow Recipe — reusable procedure templates for WorkflowInstance execution.

A Recipe is the immutable specification; a WorkflowInstance is one concrete run of it.
Recipes are loaded from JSONL or JSON files in a registry directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.evidence.schemas import EvidenceProfile

__all__ = [
    "Recipe",
    "RecipeRegistry",
    "RecipeRetryProfile",
    "RecipeStep",
    "VerificationGate",
]


@dataclass(frozen=True, slots=True)
class RecipeRetryProfile:
    max_retries: int = 3
    backoff_seconds: float = 2.0
    retry_on: tuple[str, ...] = ("tool_blocked", "step_failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "backoff_seconds": self.backoff_seconds,
            "retry_on": list(self.retry_on),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RecipeRetryProfile:
        return cls(
            max_retries=int(d.get("max_retries", 3)),
            backoff_seconds=float(d.get("backoff_seconds", 2.0)),
            retry_on=tuple(d.get("retry_on", ["tool_blocked", "step_failed"])),
        )


@dataclass(frozen=True, slots=True)
class VerificationGate:
    step_id: str
    required_evidence_kinds: tuple[str, ...]
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "required_evidence_kinds": list(self.required_evidence_kinds),
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerificationGate:
        return cls(
            step_id=d["step_id"],
            required_evidence_kinds=tuple(d.get("required_evidence_kinds", [])),
            blocking=bool(d.get("blocking", True)),
        )


@dataclass(frozen=True, slots=True)
class RecipeStep:
    id: str
    name: str
    description: str = ""
    roles: tuple[str, ...] = ()
    tools_allowed: tuple[str, ...] = ()
    policy_profile: str = "standard"
    evidence_required: bool = False
    outputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "roles": list(self.roles),
            "tools_allowed": list(self.tools_allowed),
            "policy_profile": self.policy_profile,
            "evidence_required": self.evidence_required,
            "outputs": list(self.outputs),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RecipeStep:
        return cls(
            id=d["id"],
            name=d["name"],
            description=d.get("description", ""),
            roles=tuple(d.get("roles", [])),
            tools_allowed=tuple(d.get("tools_allowed", [])),
            policy_profile=d.get("policy_profile", "standard"),
            evidence_required=bool(d.get("evidence_required", False)),
            outputs=tuple(d.get("outputs", [])),
        )


@dataclass(frozen=True, slots=True)
class Recipe:
    """Immutable specification for a reusable workflow procedure."""

    id: str
    name: str
    version: str
    steps: tuple[RecipeStep, ...]
    schema_version: str = "grimoire.recipe.v1"
    description: str = ""
    roles: tuple[str, ...] = ()
    tools_allowed: tuple[str, ...] = ()
    memory_scopes: tuple[str, ...] = ("session",)
    policy_profile: str = "standard"
    evidence_profile: EvidenceProfile = EvidenceProfile.STANDARD
    retry_profile: RecipeRetryProfile = field(default_factory=RecipeRetryProfile)
    verification_gates: tuple[VerificationGate, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "roles": list(self.roles),
            "tools_allowed": list(self.tools_allowed),
            "memory_scopes": list(self.memory_scopes),
            "policy_profile": self.policy_profile,
            "evidence_profile": self.evidence_profile.value,
            "retry_profile": self.retry_profile.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "verification_gates": [g.to_dict() for g in self.verification_gates],
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "tags": list(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Recipe:
        return cls(
            id=d["id"],
            name=d["name"],
            version=d["version"],
            steps=tuple(RecipeStep.from_dict(s) for s in d.get("steps", [])),
            schema_version=d.get("schema_version", "grimoire.recipe.v1"),
            description=d.get("description", ""),
            roles=tuple(d.get("roles", [])),
            tools_allowed=tuple(d.get("tools_allowed", [])),
            memory_scopes=tuple(d.get("memory_scopes", ["session"])),
            policy_profile=d.get("policy_profile", "standard"),
            evidence_profile=EvidenceProfile(d.get("evidence_profile", "standard")),
            retry_profile=RecipeRetryProfile.from_dict(d.get("retry_profile", {})),
            verification_gates=tuple(VerificationGate.from_dict(g) for g in d.get("verification_gates", [])),
            input_schema=d.get("input_schema", {}),
            output_schema=d.get("output_schema", {}),
            tags=tuple(d.get("tags", [])),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


class RecipeRegistry:
    """Load, register, and look up Recipes from a directory of JSON files.

    Usage::

        reg = RecipeRegistry(Path("_grimoire-runtime/recipes"))
        reg.load_directory()
        recipe = reg.get("recipe.pack.convert-gascity")
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._recipes: dict[str, dict[str, Recipe]] = {}  # id -> version -> Recipe

    def load_directory(self) -> int:
        """Load all .json recipe files from the registry directory. Returns count loaded."""
        count = 0
        for path in sorted(self._root.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    for item in raw:
                        self._ingest(Recipe.from_dict(item))
                        count += 1
                else:
                    self._ingest(Recipe.from_dict(raw))
                    count += 1
            except (json.JSONDecodeError, KeyError):
                pass
        return count

    def _ingest(self, recipe: Recipe) -> None:
        if recipe.id not in self._recipes:
            self._recipes[recipe.id] = {}
        self._recipes[recipe.id][recipe.version] = recipe

    def register(self, recipe: Recipe, *, persist: bool = True) -> None:
        """Register a recipe in memory and optionally persist it to disk."""
        self._ingest(recipe)
        if persist:
            path = self._root / f"{recipe.id.replace('.', '-')}-{recipe.version}.json"
            path.write_text(json.dumps(recipe.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, recipe_id: str, version: str | None = None) -> Recipe | None:
        """Return a recipe by id. If version is None, returns the latest version."""
        versions = self._recipes.get(recipe_id)
        if not versions:
            return None
        if version is not None:
            return versions.get(version)
        return sorted(versions.values(), key=lambda r: r.version)[-1]

    def get_or_raise(self, recipe_id: str, version: str | None = None) -> Recipe:
        recipe = self.get(recipe_id, version)
        if recipe is None:
            raise GrimoireRuntimeError(f"Recipe not found: {recipe_id}" + (f"@{version}" if version else ""))
        return recipe

    def list_recipes(self) -> list[Recipe]:
        """Return all latest-version recipes, sorted by id."""
        result = []
        for versions in self._recipes.values():
            result.append(sorted(versions.values(), key=lambda r: r.version)[-1])
        return sorted(result, key=lambda r: r.id)
