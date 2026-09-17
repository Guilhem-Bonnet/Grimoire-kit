"""``grimoire init --lite`` — un profil léger pour un dépôt sans CI ni tests.

Issue Grimoire-kit#552 (phase 2 du plan produit 2026-Q4, lot 2.6). Un preset
nommé, pas un nouveau mécanisme : chaque réglage qu'il pose existe déjà comme
son propre flag (``--backend``, ``--memory-profile``, ``--no-cockpit``,
``--archetype``) — ``--lite``/``--profile lite`` ne fait que les regrouper
sous un nom mémorisable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.cli import cmd_init
from grimoire.cli.app import app
from grimoire.memory import profiles

runner = CliRunner()


# ── _looks_like_a_playground ────────────────────────────────────────────────


class TestLooksLikeAPlayground:
    def test_empty_directory_looks_like_a_playground(self, tmp_path: Path) -> None:
        assert cmd_init._looks_like_a_playground(tmp_path) is True

    def test_github_workflows_disqualifies_it(self, tmp_path: Path) -> None:
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
        assert cmd_init._looks_like_a_playground(tmp_path) is False

    def test_a_non_empty_tests_directory_disqualifies_it(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
        assert cmd_init._looks_like_a_playground(tmp_path) is False

    def test_an_empty_tests_directory_does_not_disqualify_it(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        assert cmd_init._looks_like_a_playground(tmp_path) is True


# ── _choose_memory_profile(suggest_lite=...) ────────────────────────────────


@pytest.fixture()
def answers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"egress": True, "choice": None, "docker": False}

    def fake_confirm(prompt: str, **kwargs: Any) -> bool:
        return bool(state["egress"]) if "réseau sortant" in prompt else bool(state["docker"])

    def fake_prompt(prompt: str, **kwargs: Any) -> str:
        return str(state["choice"] or kwargs.get("default"))

    monkeypatch.setattr(cmd_init.Confirm, "ask", staticmethod(fake_confirm))
    monkeypatch.setattr(cmd_init.Prompt, "ask", staticmethod(fake_prompt))
    return state


def _with_capabilities(monkeypatch: pytest.MonkeyPatch, *tokens: str) -> None:
    monkeypatch.setattr(
        cmd_init, "machine_capabilities", lambda *, has_egress: frozenset(tokens if has_egress else ())
    )


class TestSuggestLite:
    def test_lexical_becomes_the_recommendation_when_suggested(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A machine that *could* serve the normally-recommended composition —
        # the suggestion still wins when suggest_lite is set.
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS)

        profile_id, _backend, offline, _qdrant = cmd_init._choose_memory_profile(
            "qdrant-local", offer_qdrant_docker=False, suggest_lite=True,
        )

        assert profile_id == "lexical"
        assert offline is False  # has_egress stayed True; offline only flips without it

    def test_default_recommendation_is_unchanged_without_the_suggestion(
        self, answers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _with_capabilities(monkeypatch, profiles.REQ_EGRESS)

        profile_id, _backend, _offline, _qdrant = cmd_init._choose_memory_profile(
            "qdrant-local", offer_qdrant_docker=False, suggest_lite=False,
        )

        assert profile_id == profiles.DEFAULT_PROFILE


# ── grimoire init --lite / --profile lite (end to end, CliRunner) ──────────


def _memory_backend(project: Path) -> str:
    cfg = yaml.safe_load((project / "project-context.yaml").read_text(encoding="utf-8"))
    return str(cfg["memory"]["backend"])


class TestInitLiteFlag:
    def test_lite_writes_a_lexical_project_with_the_minimal_archetype(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        cfg = yaml.safe_load((target / "project-context.yaml").read_text(encoding="utf-8"))
        assert cfg["memory"]["backend"] == "lexical"
        assert cfg["memory"]["redis_url"] == ""
        assert cfg["agents"]["archetype"] == "minimal"

    def test_lite_never_registers_the_project_in_the_cockpit(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        # HOME is session-isolated by the suite's own autouse fixture
        # (conftest._isolate_user_state) — read it back rather than assume a
        # path, and assert nothing landed under it, not even the fake one.
        home = Path(os.environ["HOME"])
        registry = home / ".grimoire" / "cockpit" / "registry.json"
        existed_before = registry.exists()

        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert registry.exists() == existed_before

    def test_lite_never_writes_a_standard_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert not (target / "_grimoire" / "standard").exists()

    def test_lite_report_names_the_three_things_left_out_and_how_to_turn_them_on(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert "grimoire memory up --profile standard" in result.output
        assert "grimoire cockpit add ." in result.output
        assert "grimoire standard init ." in result.output

    def test_lite_json_output_reports_the_profile_and_activation_commands(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["--output", "json", "init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["profile"] == "lite"
        assert data["skipped"]["cockpit"] is False
        assert data["skipped"]["standard"] is False
        assert "memory" in data["activate_later"]
        assert "cockpit" in data["activate_later"]
        assert "standard" in data["activate_later"]

    def test_profile_lite_is_an_alias_for_the_lite_flag(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--profile", "lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert _memory_backend(target) == "lexical"

    def test_unknown_profile_name_is_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--profile", "bogus", "--name", "x"])

        assert result.exit_code != 0
        assert "lite" in result.output
        assert not target.exists() or not (target / "project-context.yaml").exists()

    def test_lite_doctor_is_green(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        init_result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])
        assert init_result.exit_code == 0, init_result.output

        doctor_result = runner.invoke(app, ["doctor", str(target)])
        assert doctor_result.exit_code == 0, doctor_result.output

    def test_lite_session_start_hook_is_healthy(self, tmp_path: Path) -> None:
        """The generated project answers a real SessionStart hook call cleanly."""
        target = tmp_path / "proj"
        init_result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])
        assert init_result.exit_code == 0, init_result.output

        from grimoire.hosts.capabilities import resolve_host
        from grimoire.hosts.runtime import run_hook

        host_id = resolve_host("claude")
        assert host_id is not None
        rendered, _decision, _hook = run_hook({}, host_id=host_id, project_root=target)
        context = rendered.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert isinstance(context, str)
        assert context.strip() != ""

    def test_lite_configuration_names_no_external_service(self, tmp_path: Path) -> None:
        """No qdrant/weaviate/ollama/redis reference anywhere in the written config."""
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])
        assert result.exit_code == 0, result.output

        raw = (target / "project-context.yaml").read_text(encoding="utf-8")
        for service in ("qdrant", "weaviate", "ollama"):
            assert service not in raw.lower()
