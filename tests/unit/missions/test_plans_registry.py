"""Tests for A2 — PlansRegistry loader and validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.missions.plans_registry import (
    PlanEntry,
    PlansRegistry,
    load_plans_registry,
)


def _make_registry_dict(plans: list[dict] | None = None) -> dict:
    if plans is None:
        plans = [
            {"id": "plan-a", "path": "plan-a.md", "title": "Plan A", "status": "active"},
            {"id": "plan-b", "path": "plan-b.md", "title": "Plan B", "status": "absorbed",
             "absorbed_by": "plan-a"},
        ]
    return {
        "schema_version": "grimoire.plans-registry.v1",
        "target_plan": "plan-a",
        "unified_backlog": "plan-a/BACKLOG.md",
        "plans": plans,
    }


def _write_registry(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestPlanEntry:
    def test_from_dict_roundtrip(self) -> None:
        entry = PlanEntry.from_dict({
            "id": "foo", "path": "foo.md", "title": "Foo",
            "status": "absorbed", "absorbed_by": "bar",
            "absorbed_lots": ["LOT-B", "LOT-C"], "note": "old plan",
        })
        d = entry.to_dict()
        assert d["id"] == "foo"
        assert d["absorbed_lots"] == ["LOT-B", "LOT-C"]
        assert d["note"] == "old plan"

    def test_from_dict_minimal(self) -> None:
        entry = PlanEntry.from_dict({"id": "x", "path": "x.md", "title": "X", "status": "archive"})
        assert entry.absorbed_by == ""
        assert entry.absorbed_lots == ()


class TestPlansRegistry:
    def _registry(self) -> PlansRegistry:
        data = _make_registry_dict()
        plans = tuple(PlanEntry.from_dict(p) for p in data["plans"])
        return PlansRegistry(
            schema_version=data["schema_version"],
            target_plan=data["target_plan"],
            unified_backlog=data["unified_backlog"],
            plans=plans,
        )

    def test_by_status(self) -> None:
        r = self._registry()
        assert len(r.by_status("active")) == 1
        assert len(r.by_status("absorbed")) == 1
        assert len(r.by_status("incubator")) == 0

    def test_by_id(self) -> None:
        r = self._registry()
        assert r.by_id("plan-a") is not None
        assert r.by_id("does-not-exist") is None

    def test_active_concurrent(self) -> None:
        r = self._registry()
        active = r.active_concurrent()
        assert len(active) == 1
        assert active[0].id == "plan-a"

    def test_validate_ok(self) -> None:
        r = self._registry()
        result = r.validate()
        assert result.ok is True
        assert result.issues == []

    def test_validate_stats(self) -> None:
        r = self._registry()
        result = r.validate()
        assert result.stats["active"] == 1
        assert result.stats["absorbed"] == 1
        assert result.stats["total"] == 2

    def test_validate_unknown_status(self) -> None:
        plans = [{"id": "x", "path": "x.md", "title": "X", "status": "unknown"}]
        data = _make_registry_dict(plans)
        entries = tuple(PlanEntry.from_dict(p) for p in data["plans"])
        r = PlansRegistry(
            schema_version="v1", target_plan="x", unified_backlog="",
            plans=entries,
        )
        result = r.validate()
        assert result.ok is False
        assert any("unknown status" in issue for issue in result.issues)

    def test_validate_absorbed_missing_absorbed_by(self) -> None:
        plans = [
            {"id": "a", "path": "a.md", "title": "A", "status": "active"},
            {"id": "b", "path": "b.md", "title": "B", "status": "absorbed"},
        ]
        data = _make_registry_dict(plans)
        entries = tuple(PlanEntry.from_dict(p) for p in data["plans"])
        r = PlansRegistry(schema_version="v1", target_plan="a", unified_backlog="", plans=entries)
        result = r.validate()
        assert result.ok is False
        assert any("absorbed_by" in issue for issue in result.issues)

    def test_validate_duplicate_id(self) -> None:
        plans = [
            {"id": "dup", "path": "a.md", "title": "A", "status": "active"},
            {"id": "dup", "path": "b.md", "title": "B", "status": "archive"},
        ]
        data = _make_registry_dict(plans)
        entries = tuple(PlanEntry.from_dict(p) for p in data["plans"])
        r = PlansRegistry(schema_version="v1", target_plan="dup", unified_backlog="", plans=entries)
        result = r.validate()
        assert result.ok is False
        assert any("Duplicate" in issue for issue in result.issues)

    def test_validate_no_active_plan(self) -> None:
        plans = [{"id": "a", "path": "a.md", "title": "A", "status": "archive"}]
        data = _make_registry_dict(plans)
        entries = tuple(PlanEntry.from_dict(p) for p in data["plans"])
        r = PlansRegistry(schema_version="v1", target_plan="", unified_backlog="", plans=entries)
        result = r.validate()
        assert result.ok is False
        assert any("No active plan" in issue for issue in result.issues)

    def test_validate_path_exists_check(self, tmp_path: Path) -> None:
        (tmp_path / "plan-a.md").write_text("# Plan A", encoding="utf-8")
        plans = [
            {"id": "plan-a", "path": "plan-a.md", "title": "A", "status": "active"},
            {"id": "missing", "path": "missing.md", "title": "M", "status": "archive"},
        ]
        data = _make_registry_dict(plans)
        entries = tuple(PlanEntry.from_dict(p) for p in data["plans"])
        r = PlansRegistry(schema_version="v1", target_plan="plan-a", unified_backlog="", plans=entries)
        result = r.validate(base_dir=tmp_path)
        assert result.ok is False
        assert any("missing.md" in issue for issue in result.issues)

    def test_to_dict(self) -> None:
        r = self._registry()
        d = r.to_dict()
        assert d["schema_version"] == "grimoire.plans-registry.v1"
        assert len(d["plans"]) == 2


class TestLoadPlansRegistry:
    def test_load_json_fallback(self, tmp_path: Path) -> None:
        path = _write_registry(tmp_path, _make_registry_dict())
        r = load_plans_registry(path)
        assert r.target_plan == "plan-a"
        assert len(r.plans) == 2

    def test_load_validates_schema(self, tmp_path: Path) -> None:
        r = load_plans_registry(_write_registry(tmp_path, _make_registry_dict()))
        result = r.validate()
        assert result.ok is True

    def test_load_real_registry(self) -> None:
        registry_path = Path(__file__).parents[4] / (
            "_grimoire-runtime-output/planning-artifacts/deprecated-plans-registry.yaml"
        )
        if not registry_path.exists():
            pytest.skip("deprecated-plans-registry.yaml not present")
        r = load_plans_registry(registry_path)
        result = r.validate()
        assert result.ok is True, f"Registry issues: {result.issues}"
        assert result.stats["active"] >= 1
        assert result.stats.get("absorbed", 0) >= 1
