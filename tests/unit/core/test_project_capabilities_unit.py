"""`grimoire.core.project_capabilities` depuis tests/unit (#617/#619, lot onboarding) :
échelle `memory_upgrade_target`, sondes qui échouent sans jamais lever."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from grimoire.core import project_capabilities as pc


class TestMemoryUpgradeTarget:
    def test_no_embedding_means_nothing_above_lexical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("grimoire.tools.memory_setup.local_embedding_available", lambda *a, **k: False)
        assert pc.memory_upgrade_target("lexical") is None
        assert pc.memory_upgrade_target("standard") is None

    def test_embedding_without_docker_points_at_standard(self) -> None:
        assert pc.memory_upgrade_target("lexical") == "standard"
        assert pc.memory_upgrade_target("standard") is None

    def test_docker_points_at_complet_even_from_standard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("grimoire.tools.memory_setup.docker_daemon_reachable", lambda *a, **k: True)
        assert pc.memory_upgrade_target("standard") == "complet"
        assert pc.memory_upgrade_target("complet") is None

    def test_unknown_profile_reads_as_lexical(self) -> None:
        assert pc.memory_upgrade_target("n-importe-quoi") == "standard"


class TestProbesNeverRaise:
    def test_registry_read_failure_reads_as_not_registered(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.slug_for_path", side_effect=OSError("registre illisible")):
            assert pc.cockpit_registered(tmp_path) is False

    def test_unreadable_profile_marker_reads_as_not_installed(self, tmp_path: Path) -> None:
        with patch("grimoire.core.project_capabilities.read_profile", side_effect=ValueError("corrompu")):
            assert pc.standard_profile(tmp_path) is None


class TestUnexploitedHints:
    def test_starter_profile_on_a_team_scale_archetype_is_nudged(self, tmp_path: Path) -> None:
        archetype = next(iter(pc._TEAM_SCALE_ARCHETYPES))
        with (
            patch("grimoire.core.project_capabilities.slug_for_path", return_value="slug"),
            patch("grimoire.core.project_capabilities.read_profile", return_value="starter"),
        ):
            hints = pc.unexploited_hints(
                tmp_path, archetype=archetype, backend="qdrant-local", layer_profile="standard"
            )
        assert "grimoire standard init --profile governed" in [h.command for h in hints]

    def test_layer_profile_drives_the_memory_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("grimoire.tools.memory_setup.docker_daemon_reachable", lambda *a, **k: True)
        with (
            patch("grimoire.core.project_capabilities.slug_for_path", return_value="slug"),
            patch("grimoire.core.project_capabilities.read_profile", return_value="governed"),
        ):
            hints = pc.unexploited_hints(
                tmp_path, archetype="web-app", backend="qdrant-local", layer_profile="standard"
            )
        assert [h.command for h in hints] == ["grimoire memory up --profile complet --apply"]
        assert "'standard'" in hints[0].label

    def test_footer_line_is_empty_without_hints(self) -> None:
        assert pc.discover_footer_line([]) is None
