"""Tests for grimoire.missions.plans_registry — registry loader, validator, queries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.missions.plans_registry import (
    PlanEntry,
    PlansRegistry,
    PlansValidationResult,
    load_plans_registry,
)


class TestPlanEntry:
    def test_to_dict_minimal_omits_optionals(self) -> None:
        entry = PlanEntry(id="p1", path="plans/p1.md", title="One", status="active")
        assert entry.to_dict() == {
            "id": "p1",
            "path": "plans/p1.md",
            "title": "One",
            "status": "active",
        }

    def test_to_dict_includes_optionals_when_set(self) -> None:
        entry = PlanEntry(
            id="p2",
            path="plans/p2.md",
            title="Two",
            status="absorbed",
            absorbed_by="p1",
            absorbed_lots=("L1", "L2"),
            note="merged",
        )
        d = entry.to_dict()
        assert d["absorbed_by"] == "p1"
        assert d["absorbed_lots"] == ["L1", "L2"]
        assert d["note"] == "merged"

    def test_from_dict_coerces_types(self) -> None:
        entry = PlanEntry.from_dict(
            {
                "id": 7,
                "path": "plans/p7.md",
                "title": "Seven",
                "status": "active",
                "absorbed_lots": [1, 2],
            }
        )
        assert entry.id == "7"
        assert entry.absorbed_lots == ("1", "2")
        assert entry.absorbed_by == ""
        assert entry.note == ""


def _registry(*entries: PlanEntry) -> PlansRegistry:
    return PlansRegistry(
        schema_version="grimoire.plans-registry.v1",
        target_plan="plans/target.md",
        unified_backlog="plans/backlog.md",
        plans=tuple(entries),
    )


class TestPlansRegistryQueries:
    def test_by_status_and_active_concurrent(self) -> None:
        reg = _registry(
            PlanEntry("a", "a.md", "A", "active"),
            PlanEntry("b", "b.md", "B", "active"),
            PlanEntry("c", "c.md", "C", "archive"),
        )
        assert {p.id for p in reg.by_status("active")} == {"a", "b"}
        assert {p.id for p in reg.active_concurrent()} == {"a", "b"}
        assert [p.id for p in reg.by_status("archive")] == ["c"]

    def test_by_id_found_and_missing(self) -> None:
        reg = _registry(PlanEntry("a", "a.md", "A", "active"))
        assert reg.by_id("a").id == "a"  # type: ignore[union-attr]
        assert reg.by_id("zzz") is None

    def test_to_dict_structure(self) -> None:
        reg = _registry(PlanEntry("a", "a.md", "A", "active"))
        d = reg.to_dict()
        assert d["schema_version"] == "grimoire.plans-registry.v1"
        assert d["target_plan"] == "plans/target.md"
        assert d["unified_backlog"] == "plans/backlog.md"
        assert d["plans"] == [
            {"id": "a", "path": "a.md", "title": "A", "status": "active"}
        ]


class TestPlansRegistryValidate:
    def test_valid_registry(self) -> None:
        reg = _registry(
            PlanEntry("a", "a.md", "A", "active"),
            PlanEntry("b", "b.md", "B", "absorbed", absorbed_by="a"),
        )
        result = reg.validate()
        assert isinstance(result, PlansValidationResult)
        assert result.ok is True
        assert result.issues == []
        assert result.stats["total"] == 2
        assert result.stats["active"] == 1
        assert result.stats["absorbed"] == 1

    def test_duplicate_id_flagged(self) -> None:
        reg = _registry(
            PlanEntry("a", "a.md", "A", "active"),
            PlanEntry("a", "a2.md", "A2", "archive"),
        )
        result = reg.validate()
        assert result.ok is False
        assert any("Duplicate plan id: a" in i for i in result.issues)

    def test_unknown_status_flagged(self) -> None:
        reg = _registry(PlanEntry("a", "a.md", "A", "active"), PlanEntry("b", "b.md", "B", "weird"))
        result = reg.validate()
        assert any("unknown status 'weird'" in i for i in result.issues)

    def test_absorbed_without_absorbed_by_flagged(self) -> None:
        reg = _registry(
            PlanEntry("a", "a.md", "A", "active"),
            PlanEntry("b", "b.md", "B", "absorbed"),
        )
        result = reg.validate()
        assert any("absorbed but missing absorbed_by" in i for i in result.issues)

    def test_no_active_plan_flagged(self) -> None:
        reg = _registry(PlanEntry("a", "a.md", "A", "archive"))
        result = reg.validate()
        assert result.ok is False
        assert any("No active plan declared" in i for i in result.issues)

    def test_base_dir_path_existence(self, tmp_path: Path) -> None:
        (tmp_path / "exists.md").write_text("x", encoding="utf-8")
        reg = _registry(
            PlanEntry("a", "exists.md", "A", "active"),
            PlanEntry("b", "missing.md", "B", "archive"),
        )
        result = reg.validate(base_dir=tmp_path)
        assert any("path not found: missing.md" in i for i in result.issues)
        assert not any("exists.md" in i for i in result.issues)


class TestLoadPlansRegistry:
    def test_load_from_yaml_or_json_fallback(self, tmp_path: Path) -> None:
        payload = {
            "schema_version": "grimoire.plans-registry.v1",
            "target_plan": "plans/target.md",
            "unified_backlog": "plans/backlog.md",
            "plans": [
                {"id": "a", "path": "a.md", "title": "A", "status": "active"},
                {"id": "b", "path": "b.md", "title": "B", "status": "absorbed", "absorbed_by": "a"},
            ],
        }
        # JSON is valid YAML, so this works whether or not pyyaml is installed.
        path = tmp_path / "registry.yaml"
        path.write_text(json.dumps(payload), encoding="utf-8")

        reg = load_plans_registry(path)
        assert reg.schema_version == "grimoire.plans-registry.v1"
        assert reg.target_plan == "plans/target.md"
        assert len(reg.plans) == 2
        assert reg.by_id("b").absorbed_by == "a"  # type: ignore[union-attr]
        assert reg.validate().ok is True

    def test_load_defaults_when_keys_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "minimal.yaml"
        path.write_text(json.dumps({"plans": []}), encoding="utf-8")
        reg = load_plans_registry(path)
        assert reg.schema_version == "grimoire.plans-registry.v1"
        assert reg.target_plan == ""
        assert reg.plans == ()

    def test_load_rejects_non_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_plans_registry(path)
