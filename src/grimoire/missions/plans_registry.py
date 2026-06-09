"""A2 — Plans registry loader and validator.

Validates the `deprecated-plans-registry.yaml` produced by A2 and provides
query helpers used by the CLI and cockpit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "PlanEntry",
    "PlansRegistry",
    "PlansValidationResult",
    "load_plans_registry",
]

_VALID_STATUSES = frozenset({"active", "absorbed", "archive", "incubator"})


@dataclass(frozen=True, slots=True)
class PlanEntry:
    id: str
    path: str
    title: str
    status: str
    absorbed_by: str = ""
    absorbed_lots: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "path": self.path,
            "title": self.title,
            "status": self.status,
        }
        if self.absorbed_by:
            d["absorbed_by"] = self.absorbed_by
        if self.absorbed_lots:
            d["absorbed_lots"] = list(self.absorbed_lots)
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanEntry:
        return cls(
            id=str(d["id"]),
            path=str(d["path"]),
            title=str(d["title"]),
            status=str(d["status"]),
            absorbed_by=str(d.get("absorbed_by", "")),
            absorbed_lots=tuple(str(x) for x in d.get("absorbed_lots", [])),
            note=str(d.get("note", "")),
        )


@dataclass
class PlansValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


@dataclass
class PlansRegistry:
    schema_version: str
    target_plan: str
    unified_backlog: str
    plans: tuple[PlanEntry, ...]

    # ── Query ──────────────────────────────────────────────────────────────────

    def by_status(self, status: str) -> list[PlanEntry]:
        return [p for p in self.plans if p.status == status]

    def by_id(self, plan_id: str) -> PlanEntry | None:
        for p in self.plans:
            if p.id == plan_id:
                return p
        return None

    def active_concurrent(self) -> list[PlanEntry]:
        return self.by_status("active")

    def validate(self, base_dir: Path | None = None) -> PlansValidationResult:
        """Validate registry consistency.

        If base_dir is provided, also checks that each path exists on disk.
        """
        issues: list[str] = []
        seen_ids: set[str] = set()

        for entry in self.plans:
            if entry.id in seen_ids:
                issues.append(f"Duplicate plan id: {entry.id}")
            seen_ids.add(entry.id)

            if entry.status not in _VALID_STATUSES:
                issues.append(f"{entry.id}: unknown status '{entry.status}'")

            if entry.status == "absorbed" and not entry.absorbed_by:
                issues.append(f"{entry.id}: absorbed but missing absorbed_by")

            if base_dir is not None:
                p = base_dir / entry.path
                if not p.exists():
                    issues.append(f"{entry.id}: path not found: {entry.path}")

        stats = {
            status: sum(1 for p in self.plans if p.status == status)
            for status in _VALID_STATUSES
        }
        stats["total"] = len(self.plans)

        active = [p for p in self.plans if p.status == "active"]
        if len(active) == 0:
            issues.append("No active plan declared — at least one active plan is required")

        return PlansValidationResult(ok=len(issues) == 0, issues=issues, stats=stats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_plan": self.target_plan,
            "unified_backlog": self.unified_backlog,
            "plans": [p.to_dict() for p in self.plans],
        }


def load_plans_registry(path: Path) -> PlansRegistry:
    """Load and parse a deprecated-plans-registry.yaml file."""
    try:
        import yaml  # type: ignore[import-untyped]
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        import json
        # Fallback: attempt JSON (not standard, but allows testing without pyyaml)
        raw = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        msg = "Plans registry must be a YAML mapping"
        raise ValueError(msg)

    plans = tuple(PlanEntry.from_dict(p) for p in raw.get("plans", []))
    return PlansRegistry(
        schema_version=str(raw.get("schema_version", "grimoire.plans-registry.v1")),
        target_plan=str(raw.get("target_plan", "")),
        unified_backlog=str(raw.get("unified_backlog", "")),
        plans=plans,
    )
