"""``grimoire init --lite``/``--profile lite`` — deprecated, behaves like the default.

2026-09-18 onboarding decision (issue Grimoire-kit#616, PR2): the light
profile no longer exists — the installed experience is complete, the core
adapts to each task by class of work. ``--lite``/``--profile lite`` are kept
as accepted-but-inert flags for backward compatibility: they print a
deprecation notice and change nothing else (no forced ``lexical`` backend, no
``--no-cockpit``, no forced ``minimal`` archetype).

Supersedes the Grimoire-kit#552 preset this file used to test: that mechanism
(``suggest_lite``, the "looks like a playground" heuristic feeding the
wizard's Memory-step recommendation) is retired along with the flag's special
behaviour. ``_looks_like_a_playground`` itself survives — it is still used by
``grimoire.hosts.decisions.activation`` for an unrelated purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from grimoire.cli import cmd_init
from grimoire.cli.app import app

runner = CliRunner()


# ── _looks_like_a_playground (retained for grimoire.hosts.decisions.activation) ──


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


# ── grimoire init --lite / --profile lite (end to end, CliRunner) ──────────


def _memory_backend(project: Path) -> str:
    cfg = yaml.safe_load((project / "project-context.yaml").read_text(encoding="utf-8"))
    return str(cfg["memory"]["backend"])


class TestLiteIsDeprecatedAndInert:
    def test_lite_prints_a_deprecation_notice(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output.lower()
        assert "the light profile no longer exists" in result.output.lower()

    def test_profile_lite_also_prints_the_deprecation_notice(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--profile", "lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output.lower()

    def test_lite_behaves_exactly_like_the_default(self, tmp_path: Path) -> None:
        """Same memory backend, same cockpit enrolment, same archetype as a
        plain ``init`` on an equivalent (empty) project — no special casing
        survives the deprecation."""
        plain = tmp_path / "plain"
        lite = tmp_path / "lite"
        runner.invoke(app, ["init", str(plain), "--name", "plain-demo"])
        lite_result = runner.invoke(app, ["init", str(lite), "--lite", "--name", "lite-demo"])

        assert lite_result.exit_code == 0, lite_result.output
        assert _memory_backend(plain) == _memory_backend(lite)

        plain_cfg = yaml.safe_load((plain / "project-context.yaml").read_text(encoding="utf-8"))
        lite_cfg = yaml.safe_load((lite / "project-context.yaml").read_text(encoding="utf-8"))
        assert plain_cfg["agents"]["archetype"] == lite_cfg["agents"]["archetype"]

    def test_lite_still_registers_the_project_in_the_cockpit(self, tmp_path: Path) -> None:
        """The deprecated flag used to force `--no-cockpit`; it no longer does."""
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert "Cockpit local" in result.output

    def test_lite_report_carries_no_special_panel(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        assert "Profil lite" not in result.output
        assert "Profil léger" not in result.output

    def test_lite_json_output_carries_no_special_keys(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["--output", "json", "init", str(target), "--lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "profile" not in data
        assert "skipped" not in data
        assert "activate_later" not in data

    def test_profile_lite_is_still_the_only_accepted_profile_name(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--profile", "lite", "--name", "lite-demo"])

        assert result.exit_code == 0, result.output

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


# ── --memory-stack (unrelated to --lite, exercised here for the CLI wiring) ──


class TestMemoryStackFlag:
    def test_unknown_value_is_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--memory-stack", "bogus"])

        assert result.exit_code != 0
        assert "up" in result.output
        assert not (target / "project-context.yaml").exists()

    def test_up_is_accepted(self, tmp_path: Path, monkeypatch) -> None:
        # No real Docker Compose in this suite, per project rule — Docker is
        # reported unreachable so `start_memory_stack` short-circuits without
        # spawning anything, while still exercising the flag end to end.
        monkeypatch.setattr(
            "grimoire.tools.memory_setup.docker_daemon_reachable", lambda: False,
        )
        target = tmp_path / "proj"
        result = runner.invoke(app, ["init", str(target), "--memory-stack", "up"])

        assert result.exit_code == 0, result.output

    def test_explicit_backend_without_profile_derives_one_and_honors_up(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """#619 review: `-b weaviate-server --memory-stack up` without
        `--memory-profile` used to leave `memory_profile` empty — the old
        `elif memory_profile == "complet":` consent branch never matched, so
        the explicit `--memory-stack up` consent was silently dropped, and
        even if it had matched, `start_memory_stack("")` resolves to the
        default `standard` profile and no-ops (never touches Docker). Both
        must now hold: the profile is derived from the explicit backend, and
        the helper is actually invoked with a profile that needs Docker —
        never called with an empty string."""
        from unittest.mock import patch

        calls: list[str] = []

        def _record(profile: str, _root: Path) -> list[str]:
            calls.append(profile)
            return []

        monkeypatch.setattr("grimoire.tools.memory_setup.start_memory_stack", _record)
        target = tmp_path / "proj"
        with patch("grimoire.cli.cmd_init.collection_has_content", return_value=False):
            result = runner.invoke(
                app,
                ["init", str(target), "--backend", "weaviate-server", "--memory-stack", "up"],
            )

        assert result.exit_code == 0, result.output
        assert calls, "start_memory_stack was never called — --memory-stack up was ignored"
        assert calls[0] in ("graphe", "complet")
        assert calls[0] != ""
