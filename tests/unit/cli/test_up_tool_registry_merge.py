"""#702 — `grimoire up` ne perd pas les réglages projet de `tool-registry.yaml`.

Le registre est un gabarit du kit que le projet complète (MCP `out_of_scope`,
`error_capture`, outils ajoutés). Un `up` doit apporter les nouveautés du kit
sans écraser ces choix, et ne jamais réécrire un fichier qu'il ne sait pas lire.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

NEEDS = "multi-agent-orchestration,hooks-skills-governance,observability-cockpit"
REGISTRY = Path("_grimoire/standard/tool-registry.yaml")
BASE = Path("_grimoire/standard/.generated-base/tool-registry.yaml")


@pytest.fixture
def runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def cli_app():
    from grimoire.cli.app import app

    return app


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GRIMOIRE_TASK_ID", raising=False)
    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


def _load(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: dict) -> None:
    yaml = YAML()
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


def _up(runner, cli_app, target: Path, *extra: str):
    result = runner.invoke(cli_app, ["up", str(target), "--backend", "local", *extra])
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def project(runner, cli_app, tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    _up(runner, cli_app, target, "--needs", NEEDS)
    assert (target / REGISTRY).is_file()
    return target


def _customise(target: Path) -> None:
    data = _load(target / REGISTRY)
    data["mcp_servers"] = [{
        "id": "MCP-001", "server": "grimoire", "out_of_scope": True,
        "out_of_scope_reason": "outil de l'atelier",
    }]
    data["error_capture"]["policy"] = "incident"
    data["error_capture"]["note_projet"] = "à garder"
    data["tools"][0]["risk"] = "critique"
    data["tools"].append({
        "id": "TOOL-900", "name": "deploy", "kind": "shell", "risk": "élevé",
        "permissions": ["execute"], "owner": "ops",
    })
    data["champ_projet"] = {"x": 1}
    _dump(target / REGISTRY, data)


class TestUpKeepsProjectRegistry:
    def test_generation_records_the_merge_base(self, project: Path) -> None:
        assert _load(project / BASE) == _load(project / REGISTRY)

    def test_project_choices_survive_up(self, runner, cli_app, project: Path) -> None:
        _customise(project)
        _up(runner, cli_app, project)
        data = _load(project / REGISTRY)
        assert data["mcp_servers"][0]["out_of_scope"] is True
        assert data["error_capture"]["policy"] == "incident"
        assert data["error_capture"]["note_projet"] == "à garder"
        assert data["tools"][0]["risk"] == "critique"
        assert any(t["id"] == "TOOL-900" for t in data["tools"])
        assert data["champ_projet"] == {"x": 1}

    def test_kit_news_arrive_and_unchanged_defaults_follow(self, runner, cli_app, project: Path) -> None:
        # Simule un projet resté sur un ancien kit : base et fichier portent
        # l'ancien défaut de TOOL-002, et ignorent un outil et un champ nouveaux.
        old = _load(project / BASE)
        old["tools"][1]["risk"] = "faible"
        old["tools"] = [t for t in old["tools"] if t["id"] != "TOOL-002"] + [old["tools"][1]]
        old["error_capture"].pop("incident_from_severity")
        old["tools"].append({"id": "TOOL-OLD", "name": "legacy", "kind": "shell", "risk": "faible",
                             "permissions": ["read"], "owner": ""})
        _dump(project / BASE, old)
        mine = _load(project / REGISTRY)
        mine["tools"] = [dict(t) for t in old["tools"] if t["id"] != "TOOL-001"] + [mine["tools"][0]]
        mine["tools"] = [t for t in mine["tools"] if t["id"] != "TOOL-002"] + [dict(old["tools"][1])]
        mine["error_capture"].pop("incident_from_severity")
        mine["mcp_servers"] = [{"id": "MCP-001", "server": "g", "out_of_scope": True,
                                "out_of_scope_reason": "x"}]
        _dump(project / REGISTRY, mine)
        # TOOL-001 est « nouveau » pour ce projet : retiré de la base et du fichier.
        base = _load(project / BASE)
        base["tools"] = [t for t in base["tools"] if t["id"] != "TOOL-001"]
        _dump(project / BASE, base)
        mine = _load(project / REGISTRY)
        mine["tools"] = [t for t in mine["tools"] if t["id"] != "TOOL-001"]
        _dump(project / REGISTRY, mine)

        result = _up(runner, cli_app, project)
        data = _load(project / REGISTRY)
        ids = [t["id"] for t in data["tools"]]
        assert "TOOL-001" in ids  # nouveauté du kit
        assert "TOOL-OLD" in ids  # retiré par le kit : conservé, jamais supprimé
        assert "TOOL-OLD" in result.output
        assert next(t for t in data["tools"] if t["id"] == "TOOL-002")["risk"] == "moyen"
        assert data["error_capture"]["incident_from_severity"] == "élevée"
        assert data["mcp_servers"][0]["out_of_scope"] is True

    def test_a_default_removed_by_the_project_stays_removed(self, runner, cli_app, project: Path) -> None:
        data = _load(project / REGISTRY)
        data["tools"] = [t for t in data["tools"] if t["id"] != "TOOL-002"]
        _dump(project / REGISTRY, data)
        _up(runner, cli_app, project)
        assert "TOOL-002" not in [t["id"] for t in _load(project / REGISTRY)["tools"]]

    def test_up_is_idempotent(self, runner, cli_app, project: Path) -> None:
        _customise(project)
        _up(runner, cli_app, project)
        first = (project / REGISTRY).read_bytes()
        _up(runner, cli_app, project)
        assert (project / REGISTRY).read_bytes() == first

    def test_doctor_stays_green(self, runner, cli_app, project: Path) -> None:
        _customise(project)
        _up(runner, cli_app, project)
        before = runner.invoke(cli_app, ["doctor", str(project)])
        assert "FAIL" not in before.output, before.output


class TestUnreadableRegistry:
    @pytest.mark.parametrize("content", ["tools: [\n  - nope: : :\n", "- une\n- liste\n", '$schema: "autre/v9"\ntools: []\n'])
    def test_never_overwritten_and_saved_aside(self, runner, cli_app, project: Path, content: str) -> None:
        (project / REGISTRY).write_text(content, encoding="utf-8")
        result = _up(runner, cli_app, project)
        assert (project / REGISTRY).read_text(encoding="utf-8") == content
        backups = list((project / REGISTRY).parent.glob("tool-registry.yaml.unreadable-*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == content
        assert "laissé tel quel" in result.output
        _up(runner, cli_app, project)
        assert len(list((project / REGISTRY).parent.glob("tool-registry.yaml.unreadable-*"))) == 1


class TestPristineRegistryStillRefreshes:
    def test_untouched_file_follows_the_template(self, runner, cli_app, project: Path) -> None:
        target = project / REGISTRY
        stale = target.read_text(encoding="utf-8").replace("risk: moyen", "risk: faible")
        target.write_text(stale, encoding="utf-8")
        from grimoire.core.standard_generation import digest, save_generation_manifest

        save_generation_manifest(project, {str(REGISTRY): digest(target)})
        _up(runner, cli_app, project)
        assert target.read_text(encoding="utf-8") != stale


class TestMergeEdgeCases:
    """Branches de `standard_merge` que `up` n'atteint pas dans un projet sain."""

    TEMPLATE = (
        '$schema: "s/v1"\n'
        "tools:\n  - id: A\n    risk: moyen\n  - id: B\n    risk: faible\n"
        "mcp_servers: []\nextra: {k: 1}\nlegacy_free: 1\n"
    )

    def _setup(self, tmp_path: Path, project_text: str, base_text: str | None) -> Path:
        dest = Path("_grimoire/standard/tool-registry.yaml")
        (tmp_path / dest).parent.mkdir(parents=True)
        (tmp_path / dest).write_text(project_text, encoding="utf-8")
        if base_text is not None:
            from grimoire.core.standard_merge import base_path

            base_path(tmp_path, dest).parent.mkdir(parents=True)
            base_path(tmp_path, dest).write_text(base_text, encoding="utf-8")
        return dest

    def test_without_base_only_missing_keys_are_added(self, tmp_path: Path) -> None:
        from grimoire.core.standard_merge import merge_project_registry

        dest = self._setup(tmp_path, '$schema: "s/v1"\ntools:\n  - id: A\n    risk: critique\n', None)
        out = merge_project_registry(tmp_path, dest, self.TEMPLATE)
        data = _load(tmp_path / dest)
        assert out.changed
        assert [t["id"] for t in data["tools"]] == ["A"]  # liste jamais touchée sans base
        assert data["tools"][0]["risk"] == "critique"
        assert data["extra"] == {"k": 1}

    def test_key_retired_by_the_kit_is_kept_and_reported(self, tmp_path: Path) -> None:
        from grimoire.core.standard_merge import merge_project_registry

        base = self.TEMPLATE + "gone: 1\n"
        proj = base.replace("extra: {k: 1}", "extra: {k: 2}")
        dest = self._setup(tmp_path, proj, base)
        out = merge_project_registry(tmp_path, dest, self.TEMPLATE)
        data = _load(tmp_path / dest)
        assert data["gone"] == 1
        assert data["extra"] == {"k": 2}
        assert any("gone" in n and "conservé" in n for n in out.notices)

    def test_unreadable_base_is_ignored(self, tmp_path: Path) -> None:
        from grimoire.core.standard_merge import merge_project_registry

        dest = self._setup(tmp_path, '$schema: "s/v1"\ntools: []\n', "tools: [\n  - : :\n")
        out = merge_project_registry(tmp_path, dest, self.TEMPLATE)
        assert out.changed
        assert "extra" in _load(tmp_path / dest)

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        from grimoire.core.standard_merge import merge_project_registry

        text = '$schema: "s/v1"\ntools: []\n'
        dest = self._setup(tmp_path, text, None)
        out = merge_project_registry(tmp_path, dest, self.TEMPLATE, dry_run=True)
        assert out.changed
        assert (tmp_path / dest).read_text(encoding="utf-8") == text
        bad = merge_project_registry(tmp_path, dest, "- pas un registre\n", dry_run=True)
        assert "laissé tel quel" in bad.notices[0]
        assert not list((tmp_path / dest).parent.glob("*.unreadable-*"))

    def test_missing_file_is_reported_not_raised(self, tmp_path: Path) -> None:
        from grimoire.core.standard_merge import merge_project_registry

        out = merge_project_registry(tmp_path, Path("_grimoire/standard/absent.yaml"), self.TEMPLATE)
        assert not out.changed
        assert "laissé tel quel" in out.notices[0]

    def test_record_base_failure_is_silent(self, tmp_path: Path) -> None:
        from grimoire.core.standard_merge import record_base

        (tmp_path / "_grimoire").write_text("fichier, pas un dossier", encoding="utf-8")
        record_base(tmp_path, "_grimoire/standard/tool-registry.yaml", "x: 1\n")

    def test_backup_failure_is_reported(self, tmp_path: Path) -> None:
        from grimoire.core.standard_merge import _backup_unreadable

        assert _backup_unreadable(tmp_path / "absent.yaml") == "(sauvegarde impossible)"
