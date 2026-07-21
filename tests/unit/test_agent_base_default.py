"""Compact base protocol by default (issue #39, C3).

Every shipped agent sheet loads agent-base-compact.md (~1.2k tokens)
instead of the full agent-base.md (~10k tokens) — an ~88 % cut of the
per-agent session overhead. The full protocol remains the on-demand
reference. These tests pin the fleet invariant and the measurement
semantics of context_guard / context_router for both new and legacy
projects.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.tools.context_guard import resolve_agent_loads
from grimoire.tools.context_router import Priority, discover_context_files

REPO = Path(__file__).resolve().parents[2]

COMPACT = "protocole compact\n"
FULL = "protocole complet " * 200 + "\n"


def _project(tmp_path: Path, *, sheet_references: str) -> tuple[Path, Path]:
    custom = tmp_path / "_grimoire" / "_config" / "custom"
    agents = custom / "agents"
    agents.mkdir(parents=True)
    (custom / "agent-base-compact.md").write_text(COMPACT, encoding="utf-8")
    (custom / "agent-base.md").write_text(FULL, encoding="utf-8")
    sheet = agents / "dev.md"
    sheet.write_text(
        f"Load and apply {{project-root}}/_grimoire/_config/custom/{sheet_references} with:\n",
        encoding="utf-8",
    )
    return tmp_path, sheet


def _base_loads(root: Path, sheet: Path) -> list[str]:
    return [f.path for f in resolve_agent_loads(sheet, root) if f.role == "base-protocol"]


def test_guard_measures_compact_when_sheet_references_it(tmp_path: Path) -> None:
    root, sheet = _project(tmp_path, sheet_references="agent-base-compact.md")
    assert _base_loads(root, sheet) == ["_grimoire/_config/custom/agent-base-compact.md"]


def test_guard_keeps_full_protocol_for_legacy_sheets(tmp_path: Path) -> None:
    root, sheet = _project(tmp_path, sheet_references="agent-base.md")
    assert _base_loads(root, sheet) == ["_grimoire/_config/custom/agent-base.md"]


def test_router_plans_compact_p0_and_full_on_demand(tmp_path: Path) -> None:
    root, _ = _project(tmp_path, sheet_references="agent-base-compact.md")
    entries = {e.path: e for e in discover_context_files(root, "dev")}
    compact = entries["_grimoire/_config/custom/agent-base-compact.md"]
    full = entries["_grimoire/_config/custom/agent-base.md"]
    assert compact.priority == Priority.P0_ALWAYS
    assert full.priority == Priority.P4_ON_REQUEST


def test_router_falls_back_to_full_when_no_compact(tmp_path: Path) -> None:
    custom = tmp_path / "_grimoire" / "_config" / "custom"
    custom.mkdir(parents=True)
    (custom / "agent-base.md").write_text(FULL, encoding="utf-8")
    entries = {e.path: e for e in discover_context_files(tmp_path, "dev")}
    assert entries["_grimoire/_config/custom/agent-base.md"].priority == Priority.P0_ALWAYS


def test_fleet_invariant_all_shipped_sheets_load_compact() -> None:
    offenders = []
    for area in ("archetypes", "_grimoire", "features"):
        base = REPO / area
        if not base.is_dir():
            continue
        for sheet in base.rglob("*.md"):
            for line in sheet.read_text(encoding="utf-8", errors="replace").splitlines():
                if "Load and apply" in line and "custom/agent-base.md" in line:
                    offenders.append(str(sheet.relative_to(REPO)))
    assert not offenders, (
        "these agent sheets still load the full protocol by default "
        f"(use agent-base-compact.md): {offenders}"
    )


def test_compact_protocol_exists_and_links_to_full_reference() -> None:
    compact = REPO / "framework" / "agent-base-compact.md"
    text = compact.read_text(encoding="utf-8")
    assert "agent-base.md" in text, "compact protocol must reference the full one"
    assert len(text.splitlines()) < 100
