"""Tests for core/project_capabilities.py — shared 'unexploited capability' hints.

One source of truth for two surfaces (onboarding audit 2026-09-18): the
dynamic Next Steps panel (`grimoire init`/`up`) and the 'Découvrir' footer
(`grimoire doctor`/`status`). Written against a plain filesystem fixture
(no project-context.yaml parsing needed) so both call sites can reuse it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from grimoire.core.project_capabilities import (
    cockpit_registered,
    standard_profile,
    unexploited_hints,
)


class TestCockpitRegistered:
    def test_not_registered_by_default(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value=None):
            assert cockpit_registered(tmp_path) is False

    def test_registered_when_slug_found(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value="demo"):
            assert cockpit_registered(tmp_path) is True

    def test_never_raises_on_registry_error(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", side_effect=OSError("boom")):
            assert cockpit_registered(tmp_path) is False


class TestStandardProfile:
    def test_none_when_no_manifest(self, tmp_path: Path) -> None:
        assert standard_profile(tmp_path) is None

    def test_reads_installed_profile(self, tmp_path: Path) -> None:
        manifest = tmp_path / "_grimoire" / "standard" / "standard-profile.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("profile: starter\n", encoding="utf-8")
        assert standard_profile(tmp_path) == "starter"


class TestUnexploitedHints:
    """Never more than what applies; never crashes; always explains *why*."""

    def test_fresh_minimal_project_has_several_hints(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value=None):
            hints = unexploited_hints(tmp_path, archetype="minimal", backend="lexical")
        labels = " ".join(h.label for h in hints)
        assert any("minimal" in h.label for h in hints)
        assert any("mémoire" in h.label or "lexical" in h.label for h in hints)
        assert "grimoire standard needs" in [h.command for h in hints]
        assert labels  # sanity: something was said

    def test_specialized_archetype_drops_the_minimal_hint(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value=None):
            hints = unexploited_hints(tmp_path, archetype="web-app", backend="lexical")
        assert not any("minimal" in h.label for h in hints)

    def test_registered_cockpit_drops_the_cockpit_hint(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value="demo"):
            hints = unexploited_hints(tmp_path, archetype="minimal", backend="lexical")
        assert "grimoire cockpit" not in [h.command for h in hints]

    def test_no_cockpit_flag_drops_the_cockpit_hint(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value=None):
            hints = unexploited_hints(tmp_path, archetype="minimal", backend="lexical", no_cockpit=True)
        assert "grimoire cockpit" not in [h.command for h in hints]

    def test_non_lexical_backend_drops_the_memory_hint(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value=None):
            hints = unexploited_hints(tmp_path, archetype="minimal", backend="qdrant-local")
        assert not any("mémoire" in h.label for h in hints)

    def test_starter_under_team_scale_archetype_flags_profile_gap(self, tmp_path: Path) -> None:
        manifest = tmp_path / "_grimoire" / "standard" / "standard-profile.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("profile: starter\n", encoding="utf-8")
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value="demo"):
            hints = unexploited_hints(tmp_path, archetype="infra-ops", backend="lexical", no_cockpit=True)
        assert any("target" in h.label for h in hints)

    def test_everything_exploited_returns_empty(self, tmp_path: Path) -> None:
        manifest = tmp_path / "_grimoire" / "standard" / "standard-profile.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("profile: governed\n", encoding="utf-8")
        with patch("grimoire.core.project_capabilities.slug_for_path", return_value="demo"):
            hints = unexploited_hints(tmp_path, archetype="infra-ops", backend="qdrant-local")
        assert hints == []
