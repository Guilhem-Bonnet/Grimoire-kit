"""Compact base protocol by default (issue #39, C3).

Every shipped agent sheet loads agent-base-compact.md (~1.2k tokens)
instead of the full agent-base.md (~10k tokens) — an ~88 % cut of the
per-agent session overhead. The full protocol remains the on-demand
reference. This test pins the fleet invariant.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
