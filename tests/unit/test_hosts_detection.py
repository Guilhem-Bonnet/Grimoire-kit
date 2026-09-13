"""Tests for grimoire.hosts.detection — issue #177 (petite version).

`hosts.enabled` is either declared in ``project-context.yaml`` or, when the
key is absent, detected from files already present in the project. These
tests cover the detection heuristic alone (:func:`detect_enabled_hosts`) and
its combination with a declared config (:func:`resolve_enabled_hosts`,
:func:`enabled_host_ids`).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.bridges.schemas import HostId
from grimoire.core.config import GrimoireConfig
from grimoire.hosts.detection import (
    KNOWN_HOST_ALIASES,
    alias_for_host,
    detect_enabled_hosts,
    enabled_host_ids,
    resolve_enabled_hosts,
)


class TestDetectEnabledHosts:
    def test_empty_project_defaults_to_claude_only(self, tmp_path: Path) -> None:
        assert detect_enabled_hosts(tmp_path) == ("claude",)

    def test_claude_dir_is_detected(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        assert detect_enabled_hosts(tmp_path) == ("claude",)

    def test_copilot_instructions_file_is_detected(self, tmp_path: Path) -> None:
        gh = tmp_path / ".github"
        gh.mkdir()
        (gh / "copilot-instructions.md").write_text("x", encoding="utf-8")
        assert detect_enabled_hosts(tmp_path) == ("copilot",)

    def test_copilot_agents_dir_is_detected(self, tmp_path: Path) -> None:
        (tmp_path / ".github" / "agents").mkdir(parents=True)
        assert detect_enabled_hosts(tmp_path) == ("copilot",)

    def test_gemini_md_is_detected(self, tmp_path: Path) -> None:
        (tmp_path / "GEMINI.md").write_text("x", encoding="utf-8")
        assert detect_enabled_hosts(tmp_path) == ("gemini",)

    def test_cursor_dir_is_detected(self, tmp_path: Path) -> None:
        (tmp_path / ".cursor").mkdir()
        assert detect_enabled_hosts(tmp_path) == ("cursor",)

    def test_agents_md_alone_is_not_codex(self, tmp_path: Path) -> None:
        """AGENTS.md seul (le pont générique) ne suffit pas : il faut aussi `.codex`."""
        (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
        assert "codex" not in detect_enabled_hosts(tmp_path)

    def test_agents_md_and_codex_dir_is_codex(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
        (tmp_path / ".codex").mkdir()
        assert "codex" in detect_enabled_hosts(tmp_path)

    def test_stop_criterion_three_hosts_detected_nothing_else(self, tmp_path: Path) -> None:
        """Critère d'arrêt de l'issue #177 : un projet avec `.claude/`,
        `.github/agents/` et `GEMINI.md` détecte exactement ces trois hôtes."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".github" / "agents").mkdir(parents=True)
        (tmp_path / "GEMINI.md").write_text("x", encoding="utf-8")
        detected = set(detect_enabled_hosts(tmp_path))
        assert detected == {"claude", "copilot", "gemini"}


class TestResolveEnabledHosts:
    def test_declared_key_wins_over_detection(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()  # would detect "claude" alone
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "p"\nhosts:\n  enabled: ["copilot"]\n',
            encoding="utf-8",
        )
        assert resolve_enabled_hosts(tmp_path) == ("copilot",)

    def test_absent_key_falls_back_to_detection(self, tmp_path: Path) -> None:
        (tmp_path / ".cursor").mkdir()
        (tmp_path / "project-context.yaml").write_text('project:\n  name: "p"\n', encoding="utf-8")
        assert resolve_enabled_hosts(tmp_path) == ("cursor",)

    def test_no_config_file_falls_back_to_detection(self, tmp_path: Path) -> None:
        assert resolve_enabled_hosts(tmp_path) == ("claude",)

    def test_explicit_empty_list_emits_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "p"\nhosts:\n  enabled: []\n', encoding="utf-8",
        )
        assert resolve_enabled_hosts(tmp_path) == ()

    def test_passed_in_cfg_skips_reload(self, tmp_path: Path) -> None:
        cfg = GrimoireConfig.from_dict({"project": {"name": "p"}, "hosts": {"enabled": ["gemini"]}})
        # No project-context.yaml on disk at all — proves `cfg` short-circuits the load.
        assert resolve_enabled_hosts(tmp_path, cfg) == ("gemini",)


class TestEnabledHostIds:
    def test_translates_aliases_to_host_ids(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "p"\nhosts:\n  enabled: ["claude", "cursor"]\n',
            encoding="utf-8",
        )
        ids = enabled_host_ids(tmp_path)
        assert ids == {HostId.CLAUDE_CODE_CLI, HostId.CURSOR}


class TestAliasForHost:
    def test_round_trips_every_known_alias(self) -> None:
        from grimoire.hosts.capabilities import resolve_host

        for alias in KNOWN_HOST_ALIASES:
            host_id = resolve_host(alias)
            assert host_id is not None
            assert alias_for_host(host_id) == alias

    def test_unknown_host_falls_back_to_its_value(self) -> None:
        assert alias_for_host(HostId.UNKNOWN) == HostId.UNKNOWN.value
