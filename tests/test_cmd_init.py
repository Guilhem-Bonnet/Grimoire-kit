"""Tests for cli/cmd_init.py — enhanced init command."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from grimoire.cli.cmd_init import _git_user_name, _maybe_register_cockpit, detect_memory_backend


class TestMaybeRegisterCockpitLogging:
    """Régression CodeQL py/log-injection : `target` (un nom de dossier choisi
    par l'appelant) était interpolé avec `%s` dans `logger.debug`. Un dossier
    dont le nom contient un retour à la ligne pouvait alors forger une fausse
    entrée de log. `%r` (repr) échappe `\\n`/`\\r` au lieu de les émettre."""

    def test_no_cockpit_flag_does_not_leak_a_raw_newline(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = tmp_path / "projet\nFAUX-LOG : accès accordé"
        with caplog.at_level(logging.DEBUG, logger="grimoire.cli.cmd_init"):
            _maybe_register_cockpit(target, "demo", "python", no_cockpit=True)
        assert len(caplog.records) == 1
        rendered = caplog.records[0].getMessage()
        assert "\n" not in rendered
        assert "FAUX-LOG" in rendered  # toujours visible, juste échappé

    def test_scratch_path_does_not_leak_a_raw_newline(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import tempfile

        monkeypatch.delenv("GRIMOIRE_NO_COCKPIT", raising=False)
        scratch_root = Path(tempfile.gettempdir()) / "projet\nFAUX-LOG : accès accordé"
        with caplog.at_level(logging.DEBUG, logger="grimoire.cli.cmd_init"):
            _maybe_register_cockpit(scratch_root, "demo", "python")
        assert len(caplog.records) == 1
        rendered = caplog.records[0].getMessage()
        assert "\n" not in rendered


class TestDetectMemoryBackend:
    def test_returns_local_when_nothing_available(self) -> None:
        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", side_effect=OSError("no server")):
            result = detect_memory_backend()
        assert result == "local"

    def test_returns_qdrant_local_when_qdrant_up(self) -> None:
        with (
            patch("grimoire.cli.cmd_init._is_weaviate_reachable", return_value=False),
            patch("grimoire.cli.cmd_init._is_qdrant_reachable", return_value=True),
        ):
            result = detect_memory_backend()
        assert result == "qdrant-local"

    def test_returns_weaviate_when_weaviate_up(self) -> None:
        with patch("grimoire.cli.cmd_init._is_weaviate_reachable", return_value=True):
            result = detect_memory_backend()
        assert result == "weaviate-server"

    def test_returns_local_on_timeout(self) -> None:
        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result = detect_memory_backend()
        assert result == "local"


class TestMemoryServiceSuggestion:
    """Issue #496 — `detect_memory_backend()` no longer decides anything; it
    only feeds a suggestion line, never applied silently."""

    def test_no_suggestion_when_nothing_detected(self) -> None:
        from grimoire.cli.cmd_init import memory_service_suggestion

        assert memory_service_suggestion("local") is None

    def test_suggests_the_activation_command_for_a_detected_service(self) -> None:
        from grimoire.cli.cmd_init import memory_service_suggestion

        suggestion = memory_service_suggestion("weaviate-server")
        assert suggestion is not None
        assert "Weaviate" in suggestion
        assert "grimoire memory up --profile standard --apply" in suggestion


class TestCollectionHasContent:
    """Issue #496 (b) — probing an existing collection must never block on a
    probe failure, only on confirmed content."""

    def test_unreachable_backend_reads_as_empty(self) -> None:
        from grimoire.cli.cmd_init import collection_has_content

        with patch("grimoire.cli.cmd_init.urllib.request.urlopen", side_effect=OSError("no server")):
            assert collection_has_content("weaviate-server", "some-project") is False

    def test_weaviate_collection_with_objects_is_not_empty(self) -> None:
        from grimoire.cli.cmd_init import collection_has_content

        with patch(
            "grimoire.cli.cmd_init._http_get_json",
            return_value={"objects": [{"id": "1"}]},
        ):
            assert collection_has_content("weaviate-server", "shared") is True

    def test_qdrant_collection_with_points_is_not_empty(self) -> None:
        from grimoire.cli.cmd_init import collection_has_content

        with patch(
            "grimoire.cli.cmd_init._http_get_json",
            return_value={"result": {"points_count": 42}},
        ):
            assert collection_has_content("qdrant-server", "shared") is True

    def test_local_and_lexical_never_probe(self) -> None:
        from grimoire.cli.cmd_init import collection_has_content

        assert collection_has_content("local", "anything") is False
        assert collection_has_content("lexical", "anything") is False


class TestChooseMemoryProfileDetectedService:
    """Issue #496 — the wizard may offer a detected service, but only through
    an explicit question; declining it must never leave the project attached."""

    def test_declining_the_detected_service_keeps_the_project_isolated(self) -> None:
        from grimoire.cli.cmd_init import _choose_memory_profile

        with (
            patch("grimoire.cli.cmd_init.Confirm.ask", return_value=False),
            patch("grimoire.cli.cmd_init.Prompt.ask", return_value="2"),
        ):
            _profile_id, backend, _offline, _qdrant_docker = _choose_memory_profile(
                "lexical", offer_qdrant_docker=False, detected_service="weaviate-server",
            )
        assert backend == "lexical"

    def test_accepting_the_detected_service_uses_it(self) -> None:
        from grimoire.cli.cmd_init import _choose_memory_profile

        with (
            patch("grimoire.cli.cmd_init.Confirm.ask", return_value=True),
            patch("grimoire.cli.cmd_init.Prompt.ask", return_value="2"),
        ):
            _profile_id, backend, _offline, _qdrant_docker = _choose_memory_profile(
                "lexical", offer_qdrant_docker=False, detected_service="weaviate-server",
            )
        assert backend == "weaviate-server"

    def test_an_already_explicit_backend_is_never_asked_about(self) -> None:
        """`detected_service == backend` means the caller already claimed it
        explicitly (e.g. `--backend weaviate-server`) — no question needed."""
        from grimoire.cli.cmd_init import _choose_memory_profile

        with (
            patch("grimoire.cli.cmd_init.Confirm.ask") as mock_confirm,
            patch("grimoire.cli.cmd_init.Prompt.ask", return_value="2"),
        ):
            _choose_memory_profile(
                "weaviate-server", offer_qdrant_docker=False, detected_service="weaviate-server",
            )
        mock_confirm.assert_not_called()


class TestGitUserName:
    def test_returns_name_on_success(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Test User\n"
            result = _git_user_name()
        assert result == "Test User"

    def test_returns_empty_on_failure(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            result = _git_user_name()
        assert result == ""

    def test_returns_empty_on_exception(self) -> None:
        with patch("grimoire.cli.cmd_init.subprocess.run", side_effect=FileNotFoundError("no git")):
            result = _git_user_name()
        assert result == ""


class TestInitCLI:
    """CLI integration tests using typer CliRunner."""

    @pytest.fixture
    def runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def app(self):
        from grimoire.cli.app import app
        return app

    def test_init_dry_run(self, runner, app, tmp_path: Path) -> None:
        result = runner.invoke(app, ["-y", "init", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower() or "mkdir" in result.output.lower()

    def test_init_express_creates_project(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "my-project"
        result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 0
        assert (target / "project-context.yaml").is_file()
        assert (target / "_grimoire" / "kit" / "agents").is_dir()

    def test_init_express_sets_project_name(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "cool-app"
        runner.invoke(app, ["-y", "init", str(target)])
        content = (target / "project-context.yaml").read_text()
        assert "cool-app" in content

    def test_init_with_explicit_name(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "dir"
        runner.invoke(app, ["-y", "init", str(target), "--name", "MyApp"])
        content = (target / "project-context.yaml").read_text()
        assert "MyApp" in content

    def test_init_with_explicit_archetype(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "infra"
        runner.invoke(app, ["-y", "init", str(target), "--archetype", "infra-ops"])
        content = (target / "project-context.yaml").read_text()
        assert "infra-ops" in content
        # Should have infra-ops agents
        agents_dir = target / "_grimoire" / "kit" / "agents"
        agent_names = {f.stem for f in agents_dir.glob("*.md")}
        assert "ops-engineer" in agent_names

    def test_init_with_agentic_standard_archetype(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "standard"
        result = runner.invoke(app, ["-y", "init", str(target), "--archetype", "agentic-standard"])
        assert result.exit_code == 0
        content = (target / "project-context.yaml").read_text()
        assert "agentic-standard" in content
        assert (target / "_grimoire" / "kit" / "archetype.dna.agentic-standard.yaml").is_file()

    def test_init_refuses_existing_without_force(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "existing"
        target.mkdir()
        (target / "project-context.yaml").write_text("existing")
        result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 1

    def test_init_force_overwrites(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "overwrite"
        target.mkdir()
        (target / "project-context.yaml").write_text("old")
        result = runner.invoke(app, ["-y", "init", str(target), "--force"])
        assert result.exit_code == 0
        content = (target / "project-context.yaml").read_text()
        assert "old" not in content

    def test_init_json_output(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "json-proj"
        result = runner.invoke(app, ["-y", "-o", "json", "init", str(target)])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["project"] == "json-proj"
        assert "agents" in data

    def test_init_invalid_archetype(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "bad-arch"
        result = runner.invoke(app, ["-y", "init", str(target), "--archetype", "nonexistent"])
        assert result.exit_code == 1

    def test_init_invalid_backend(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "bad-backend"
        result = runner.invoke(app, ["-y", "init", str(target), "--backend", "redis"])
        assert result.exit_code == 1

    def test_init_deploys_meta_agents(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "meta-test"
        runner.invoke(app, ["-y", "init", str(target)])
        agents_dir = target / "_grimoire" / "kit" / "agents"
        agent_names = {f.stem for f in agents_dir.glob("*.md")}
        # project-navigator, memory-keeper, art-director, creative-toolsmith
        # became skills attached to agent-optimizer (issue #375) — the meta
        # roster now keeps only its 3 agents at a distinct faisceau.
        assert "concierge" in agent_names
        assert "agent-optimizer" in agent_names
        assert "security-auditor" in agent_names
        skills_dir = target / "_grimoire" / "kit" / "skills"
        skill_names = {f.stem for f in skills_dir.glob("*.md")}
        assert "meta-project-navigation" in skill_names
        assert "meta-memory-quality" in skill_names

    def test_init_creates_session_branch(self, runner, app, tmp_path: Path) -> None:
        target = tmp_path / "session-test"
        runner.invoke(app, ["-y", "init", str(target)])
        branch_file = target / "_grimoire-output" / ".runs" / "main" / "branch.json"
        assert branch_file.is_file()

    # ── Batch 3 — Enriched dry-run and JSON output ──────────────

    def test_dry_run_shows_agent_categories(self, runner, app, tmp_path: Path) -> None:
        """Dry-run should display agents grouped by category."""
        target = tmp_path / "dry-cats"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run"])
        assert result.exit_code == 0
        assert "meta" in result.output.lower()

    def test_dry_run_shows_gitignore_patterns(self, runner, app, tmp_path: Path) -> None:
        """Dry-run should preview .gitignore patterns."""
        target = tmp_path / "dry-gi"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run"])
        assert result.exit_code == 0
        assert "_grimoire-output/.runs/" in result.output

    def test_dry_run_infra_shows_dna(self, runner, app, tmp_path: Path) -> None:
        """Dry-run for infra-ops should show DNA traits."""
        target = tmp_path / "dry-dna"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run", "--archetype", "infra-ops"])
        assert result.exit_code == 0
        assert "Archetype DNA" in result.output

    def test_json_output_agents_categorized(self, runner, app, tmp_path: Path) -> None:
        """JSON output should have agents.by_category breakdown."""
        import json
        target = tmp_path / "json-cats"
        result = runner.invoke(app, ["-y", "-o", "json", "init", str(target)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "by_category" in data["agents"]
        assert "meta" in data["agents"]["by_category"]

    def test_init_deploys_archetype_dna(self, runner, app, tmp_path: Path) -> None:
        """Init with infra-ops should deploy archetype.dna.yaml."""
        target = tmp_path / "dna-test"
        runner.invoke(app, ["-y", "init", str(target), "--archetype", "infra-ops"])
        dna = target / "_grimoire" / "kit" / "archetype.dna.yaml"
        assert dna.is_file()

    def test_init_creates_gitignore(self, runner, app, tmp_path: Path) -> None:
        """Init should generate .gitignore with grimoire patterns."""
        target = tmp_path / "gi-test"
        runner.invoke(app, ["-y", "init", str(target)])
        gi = target / ".gitignore"
        assert gi.is_file()
        content = gi.read_text()
        assert "Grimoire Kit" in content

    # ── Batch 4 — Multi-archetype ──────────────────────────────

    def test_init_multi_archetype_comma_separated(self, runner, app, tmp_path: Path) -> None:
        """CLI --archetype supports comma-separated values."""
        target = tmp_path / "multi-arch"
        result = runner.invoke(app, ["-y", "init", str(target), "--archetype", "web-app,infra-ops"])
        assert result.exit_code == 0
        # Should have DNA for primary archetype at least
        dna = target / "_grimoire" / "kit" / "archetype.dna.yaml"
        assert dna.is_file()

    def test_init_multi_archetype_json_output(self, runner, app, tmp_path: Path) -> None:
        """JSON output should contain archetypes list."""
        import json
        target = tmp_path / "multi-json"
        result = runner.invoke(app, ["-y", "-o", "json", "init", str(target), "--archetype", "web-app,fix-loop"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "archetypes" in data
        assert "web-app" in data["archetypes"]
        assert "fix-loop" in data["archetypes"]

    def test_init_multi_archetype_dry_run(self, runner, app, tmp_path: Path) -> None:
        """Dry-run with multiple archetypes shows composite info."""
        target = tmp_path / "multi-dry"
        result = runner.invoke(app, ["-y", "init", str(target), "--dry-run", "--archetype", "infra-ops,fix-loop"])
        assert result.exit_code == 0


class TestInitNeverSilentlyAttaches:
    """Issue #496 — a real service found on the host (Weaviate on :8080 in
    the reported incident) must never get wired into a fresh project just
    because `-y`/`auto` (the default) ran on a machine that happens to run
    one."""

    @pytest.fixture
    def runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def app(self):
        from grimoire.cli.app import app
        return app

    def test_auto_backend_falls_back_to_lexical_despite_a_detected_service(
        self, runner, app, tmp_path: Path,
    ) -> None:
        target = tmp_path / "throwaway"
        with patch("grimoire.cli.cmd_init._is_weaviate_reachable", return_value=True):
            result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 0, result.output
        content = (target / "project-context.yaml").read_text(encoding="utf-8")
        assert 'backend: "lexical"' in content
        assert "weaviate" not in content.lower()

    def test_report_suggests_the_detected_service_without_attaching(
        self, runner, app, tmp_path: Path,
    ) -> None:
        target = tmp_path / "throwaway2"
        with patch("grimoire.cli.cmd_init._is_weaviate_reachable", return_value=True):
            result = runner.invoke(app, ["-y", "init", str(target)])
        assert result.exit_code == 0, result.output
        assert "grimoire memory up --profile standard --apply" in result.output

    def test_json_report_carries_the_suggestion_not_the_attachment(
        self, runner, app, tmp_path: Path,
    ) -> None:
        import json

        target = tmp_path / "throwaway3"
        with (
            patch("grimoire.cli.cmd_init._is_weaviate_reachable", return_value=False),
            patch("grimoire.cli.cmd_init._is_qdrant_reachable", return_value=True),
        ):
            result = runner.invoke(app, ["-y", "-o", "json", "init", str(target)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["backend"] == "lexical"
        assert data["memory_detected"] == "qdrant-local"
        assert "grimoire memory up" in data["memory_suggestion"]

    def test_explicit_backend_is_still_honored_over_detection(
        self, runner, app, tmp_path: Path,
    ) -> None:
        """A caller who names a backend outright still gets it — detection
        only ever fills in for an *unset* choice."""
        target = tmp_path / "explicit"
        with patch("grimoire.cli.cmd_init.collection_has_content", return_value=False):
            result = runner.invoke(app, ["-y", "init", str(target), "--backend", "local"])
        assert result.exit_code == 0, result.output
        content = (target / "project-context.yaml").read_text(encoding="utf-8")
        assert 'backend: "local"' in content


class TestInitMemoryCollectionNaming:
    """Issue #496 (b) — a shared backend gets a per-project collection name;
    attaching to a pre-existing, non-empty one needs an explicit flag."""

    @pytest.fixture
    def runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def app(self):
        from grimoire.cli.app import app
        return app

    def test_backend_weaviate_server_names_the_collection_after_the_project_slug(
        self, runner, app, tmp_path: Path,
    ) -> None:
        target = tmp_path / "My Cool Project"
        with patch("grimoire.cli.cmd_init.collection_has_content", return_value=False):
            result = runner.invoke(app, ["-y", "init", str(target), "--backend", "weaviate-server"])
        assert result.exit_code == 0, result.output
        content = (target / "project-context.yaml").read_text(encoding="utf-8")
        assert 'collection_prefix: "my-cool-project"' in content
        # Never the fixed name every project used to share (issue #493/#496).
        assert "GrimoireMemory" not in content

    def test_memory_collection_flag_overrides_the_slug(
        self, runner, app, tmp_path: Path,
    ) -> None:
        target = tmp_path / "proj"
        with patch("grimoire.cli.cmd_init.collection_has_content", return_value=False):
            result = runner.invoke(
                app,
                ["-y", "init", str(target), "--backend", "weaviate-server", "--memory-collection", "team-shared"],
            )
        assert result.exit_code == 0, result.output
        content = (target / "project-context.yaml").read_text(encoding="utf-8")
        assert 'collection_prefix: "team-shared"' in content

    def test_refuses_to_attach_to_a_nonempty_collection_without_the_flag(
        self, runner, app, tmp_path: Path,
    ) -> None:
        target = tmp_path / "proj2"
        with patch("grimoire.cli.cmd_init.collection_has_content", return_value=True):
            result = runner.invoke(app, ["-y", "init", str(target), "--backend", "weaviate-server"])
        assert result.exit_code == 1
        assert "--memory-collection" in result.output
        assert not (target / "project-context.yaml").exists()

    def test_explicit_memory_collection_allows_attaching_to_a_nonempty_one(
        self, runner, app, tmp_path: Path,
    ) -> None:
        target = tmp_path / "proj3"
        with patch("grimoire.cli.cmd_init.collection_has_content", return_value=True):
            result = runner.invoke(
                app,
                ["-y", "init", str(target), "--backend", "weaviate-server", "--memory-collection", "existing"],
            )
        assert result.exit_code == 0, result.output
        content = (target / "project-context.yaml").read_text(encoding="utf-8")
        assert 'collection_prefix: "existing"' in content

    def test_qdrant_server_also_gets_a_per_project_collection(
        self, runner, app, tmp_path: Path,
    ) -> None:
        target = tmp_path / "qproj"
        with patch("grimoire.cli.cmd_init.collection_has_content", return_value=False):
            result = runner.invoke(app, ["-y", "init", str(target), "--backend", "qdrant-server"])
        assert result.exit_code == 0, result.output
        content = (target / "project-context.yaml").read_text(encoding="utf-8")
        assert 'collection_prefix: "qproj"' in content

    def test_local_backend_is_unaffected(self, runner, app, tmp_path: Path) -> None:
        """No shared collection to namespace for a file-local backend."""
        target = tmp_path / "localproj"
        result = runner.invoke(app, ["-y", "init", str(target), "--backend", "local"])
        assert result.exit_code == 0, result.output
        content = (target / "project-context.yaml").read_text(encoding="utf-8")
        assert "collection_prefix" not in content


class TestParseArchetypeSelection:
    """Tests for _parse_archetype_selection helper."""

    def test_all_returns_all_keys(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("all")
        assert result == list(_ARCHETYPE_KEYS)

    def test_none_returns_minimal(self) -> None:
        from grimoire.cli.cmd_init import _parse_archetype_selection
        assert _parse_archetype_selection("none") == ["minimal"]

    def test_empty_returns_minimal(self) -> None:
        from grimoire.cli.cmd_init import _parse_archetype_selection
        assert _parse_archetype_selection("") == ["minimal"]

    def test_single_number(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1")
        assert result == [_ARCHETYPE_KEYS[0]]

    def test_multiple_numbers(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1,3,5")
        assert result == [_ARCHETYPE_KEYS[0], _ARCHETYPE_KEYS[2], _ARCHETYPE_KEYS[4]]

    def test_numbers_with_spaces(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1 3 5")
        assert result == [_ARCHETYPE_KEYS[0], _ARCHETYPE_KEYS[2], _ARCHETYPE_KEYS[4]]

    def test_deduplication(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1,1,1")
        assert result == [_ARCHETYPE_KEYS[0]]

    def test_archetype_names_directly(self) -> None:
        from grimoire.cli.cmd_init import _parse_archetype_selection
        result = _parse_archetype_selection("web-app")
        assert result == ["web-app"]

    def test_out_of_range_ignored(self) -> None:
        from grimoire.cli.cmd_init import _ARCHETYPE_KEYS, _parse_archetype_selection
        result = _parse_archetype_selection("1,99")
        assert result == [_ARCHETYPE_KEYS[0]]

    def test_zero_triggers_guided(self) -> None:
        # "0" calls _guided_discovery which needs user input — for unit test, we mock
        from unittest.mock import patch

        from grimoire.cli.cmd_init import _parse_archetype_selection

        with patch("grimoire.cli.cmd_init._guided_discovery", return_value=["web-app"]):
            result = _parse_archetype_selection("0")
        assert result == ["web-app"]


class TestInitNoCockpit:
    """#305 — `init` enrôlait chaque projet dans le registre cockpit réel, même
    les jetables, sans option pour l'éviter (seule la variable d'environnement
    non documentée `GRIMOIRE_NO_COCKPIT` le pouvait)."""

    @pytest.fixture
    def runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    @pytest.fixture
    def app(self):
        from grimoire.cli.app import app
        return app

    @pytest.fixture
    def simulated_real_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """A stand-in for a real `$HOME` with a pre-existing, empty cockpit
        registry — `GRIMOIRE_COCKPIT_HOME` is what `registry_home()` reads
        first (see `tools/project_registry.py`), so pointing it here is
        exactly what a real, already-used machine looks like before this
        project gets created."""
        cockpit_home = tmp_path / "simulated-home" / ".grimoire" / "cockpit"
        cockpit_home.mkdir(parents=True)
        registry = cockpit_home / "registry.json"
        registry.write_text("[]", encoding="utf-8")
        monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(cockpit_home))
        monkeypatch.delenv("GRIMOIRE_NO_COCKPIT", raising=False)
        return registry

    def test_init_no_cockpit_leaves_the_registry_untouched(
        self, runner, app, tmp_path: Path, simulated_real_home: Path,
    ) -> None:
        target = tmp_path / "throwaway"
        before = simulated_real_home.read_bytes()

        result = runner.invoke(app, ["-y", "init", str(target), "--no-cockpit"])

        assert result.exit_code == 0, result.output
        assert simulated_real_home.read_bytes() == before
        assert "Cockpit local" not in result.output

    def test_init_without_the_flag_still_enrols(
        self, runner, app, tmp_path: Path, simulated_real_home: Path,
    ) -> None:
        """Control: the flag is what changes the outcome, not the fixture."""
        target = tmp_path / "real-project"

        result = runner.invoke(app, ["-y", "init", str(target)])

        assert result.exit_code == 0, result.output
        import json
        registered = json.loads(simulated_real_home.read_text(encoding="utf-8"))
        assert any(p.get("path") == str(target) for p in registered)

    def test_init_under_the_temp_root_is_never_auto_enrolled(
        self, runner, app, simulated_real_home: Path,
    ) -> None:
        """Régression #492 / 2026-09-14 : un `init` jeté à même la racine
        temporaire (le motif exact des entrées `probe` et `x`/`x-2`…`x-7`
        trouvées au registre réel) ne doit jamais s'auto-enrôler, même sans
        `--no-cockpit` ni `GRIMOIRE_NO_COCKPIT` — contrairement à
        `test_init_without_the_flag_still_enrols`, qui prouve que l'enrôlement
        marche toujours pour un projet ordinaire."""
        import shutil
        import tempfile

        before = simulated_real_home.read_bytes()
        # `mkdtemp()` direct, comme le smoke test manuel qui a produit
        # l'incident — pas `tmp_path` (isolé, plusieurs niveaux plus bas).
        scratch_root = Path(tempfile.mkdtemp())
        target = scratch_root / "x"
        try:
            result = runner.invoke(app, ["-y", "init", str(target)])

            assert result.exit_code == 0, result.output
            assert simulated_real_home.read_bytes() == before
            assert "Cockpit local" not in result.output
        finally:
            shutil.rmtree(scratch_root, ignore_errors=True)

    def test_init_no_cockpit_documented_in_help(self, app) -> None:
        """The option exists on the command, and the env var is documented
        alongside it — checked on the declared parameters and the raw
        docstring rather than the Rich-rendered `--help` text: under a
        narrow terminal width, Rich can wrap or hyphenate a long option name
        across a line break, making a substring search on the rendered
        output test the runner's terminal width instead of the contract
        (see `TestUpAlias.test_up_help_shows_new_flags` in test_cmd_up.py for
        the same lesson, and CI turning this test red on windows-latest)."""
        from typer.main import get_command

        group = get_command(app)
        init_cmd = group.get_command(None, "init")
        assert init_cmd is not None
        declared = {opt for param in init_cmd.params for opt in getattr(param, "opts", [])}
        assert "--no-cockpit" in declared
        assert "GRIMOIRE_NO_COCKPIT" in (init_cmd.help or "")
