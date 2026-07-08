"""Tests for grimoire.tools.features — canaux de features beta/stable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.tools import features as feat

runner = CliRunner()


class TestFeatureState:
    def test_defaults_by_channel(self, tmp_path: Path) -> None:
        assert feat.is_enabled(tmp_path, "stigmergy") is True
        assert feat.is_enabled(tmp_path, "stigmergy-hooks") is False
        assert feat.is_enabled(tmp_path, "inconnue") is False

    def test_set_and_persist(self, tmp_path: Path) -> None:
        feat.set_enabled(tmp_path, "stigmergy-hooks", True)
        assert feat.is_enabled(tmp_path, "stigmergy-hooks") is True
        state = json.loads((tmp_path / "_grimoire" / "features.json").read_text(encoding="utf-8"))
        assert state["stigmergy-hooks"]["enabled"] is True
        feat.set_enabled(tmp_path, "stigmergy-hooks", False)
        assert feat.is_enabled(tmp_path, "stigmergy-hooks") is False

    def test_non_toggleable_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non togglable"):
            feat.set_enabled(tmp_path, "stigmergy", False)

    def test_unknown_feature_raises(self, tmp_path: Path) -> None:
        with pytest.raises(KeyError):
            feat.set_enabled(tmp_path, "nope", True)

    def test_list_features_shape(self, tmp_path: Path) -> None:
        entries = feat.list_features(tmp_path)
        assert {e["id"] for e in entries} >= {"stigmergy", "stigmergy-hooks"}
        for e in entries:
            assert e["channel"] in ("stable", "beta", "experimental")
            assert isinstance(e["enabled"], bool)


class TestFeaturesCli:
    def test_list(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["features", "list", "--project-root", str(tmp_path)])
        assert result.exit_code == 0
        assert "stigmergy" in result.output

    def test_enable_stigmergy_hooks_installs(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["features", "enable", "stigmergy-hooks",
                                     "--project-root", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".github" / "hooks" / "stigmergy-sense.json").is_file()
        assert feat.is_enabled(tmp_path, "stigmergy-hooks") is True

        result2 = runner.invoke(app, ["features", "disable", "stigmergy-hooks",
                                      "--project-root", str(tmp_path)])
        assert result2.exit_code == 0
        assert not (tmp_path / ".github" / "hooks" / "stigmergy-sense.json").exists()

    def test_unknown_feature_exit_code(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["features", "enable", "nope",
                                     "--project-root", str(tmp_path)])
        assert result.exit_code == 2


class TestFeaturesApi:
    def test_view_and_toggle(self, tmp_path: Path) -> None:
        from grimoire.tools.forge_server import ForgeAPI

        kit = tmp_path / "kit"
        (kit / "extensions").mkdir(parents=True)
        project = tmp_path / "proj"
        project.mkdir()
        api = ForgeAPI(project, kit, ui_dir=None)

        entries = api.features_view()
        hooks_entry = next(e for e in entries if e["id"] == "stigmergy-hooks")
        assert hooks_entry["enabled"] is False
        assert hooks_entry["installed"] is False

        result = api.feature_toggle("stigmergy-hooks", True)
        assert result["enabled"] is True
        assert (project / ".github" / "hooks" / "stigmergy-emit.json").is_file()

        result2 = api.feature_toggle("stigmergy-hooks", False)
        assert result2["enabled"] is False
        assert not (project / ".github" / "hooks" / "stigmergy-emit.json").exists()
