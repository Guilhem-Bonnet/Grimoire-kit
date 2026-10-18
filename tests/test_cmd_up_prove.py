"""``grimoire up --prove`` (B-03) — le hook tire pour de bon, et un premier gate passe au vert.

Avant ce chantier, ``up`` puis ``doctor`` répondaient « le projet est en état »
sans jamais lancer la commande de hook que l'hôte exécutera : un hook désactivé,
absent du ``PATH`` ou sans effet laissait tous les checks verts. Ces tests
posent la contre-épreuve : chaque panne ci-dessous laisse ``doctor`` vert et
doit faire échouer ``--prove``.

Le ``grimoire-hook`` du ``PATH`` est un script d'une ligne qui lance le module
du kit en cours de test (``python -m grimoire.hosts.runtime``) : c'est le même
code que la console script installée, sans exiger une installation.
"""

from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import grimoire
from grimoire.cli.cmd_up_prove import discover_session_start_hooks, run_prove

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="le script grimoire-hook de test est un script sh")


@pytest.fixture
def runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def cli_app():
    from grimoire.cli.app import app

    return app


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


def _install_hook_binary(bin_dir: Path, monkeypatch: pytest.MonkeyPatch, *, body: str | None = None) -> Path:
    """Pose un ``grimoire-hook`` en tête du ``PATH`` ; *body* le remplace (hook sans effet, par exemple)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    src = Path(grimoire.__file__).resolve().parent.parent
    script = bin_dir / "grimoire-hook"
    script.write_text(
        body
        if body is not None
        else f'#!/bin/sh\nexport PYTHONPATH="{src}"\nexec "{sys.executable}" -m grimoire.hosts.runtime "$@"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return script


@pytest.fixture
def hook_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _install_hook_binary(tmp_path / "bin", monkeypatch)


@pytest.fixture
def project(tmp_path: Path, runner, cli_app, hook_binary: Path) -> Path:
    target = tmp_path / "proj"
    result = runner.invoke(cli_app, ["up", str(target), "--backend", "local"])
    assert result.exit_code == 0, result.output
    return target


@pytest.fixture
def governed_project(project: Path) -> Path:
    """Un projet qui a déjà un ledger et un board : une vraie tâche y est ouverte."""
    from grimoire.missions.service import TaskService

    TaskService(project).add("vraie tâche", ("elle existait avant la preuve",), owner="humain", actor="humain", ready=True)
    return project


def _prove_json(runner, cli_app, target: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(cli_app, ["-o", "json", "up", str(target), "--backend", "local", "--prove", *extra])
    return result.exit_code, json.loads(result.stdout)


BOARD = Path("_grimoire") / "standard" / "task-board.yaml"
LEDGER = Path("_grimoire-runtime-output") / "ledger"


def _session_start_output(target: Path) -> str:
    """Ce que le hook SessionStart de Claude Code répond ici, tel que l'hôte le recevrait."""
    import shutil
    import subprocess

    payload = json.dumps({"session_id": "s-test", "hook_event_name": "SessionStart", "cwd": str(target)})
    command = next(h.command for h in discover_session_start_hooks(target)[0] if h.source == ".claude/settings.json")
    resolved = shutil.which("grimoire-hook")
    assert resolved is not None
    proc = subprocess.run(
        [resolved, *command.split()[1:]], input=payload, cwd=target, capture_output=True, text=True,
        encoding="utf-8", check=False, timeout=60, env={**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(target)},
    )
    return proc.stdout + proc.stderr


def _ledger_tasks(target: Path) -> list[dict[str, Any]]:
    from grimoire.missions.service import TaskService

    return [t.to_dict() for t in TaskService(target).list_tasks()]


class TestProveHealthyProject:
    def test_reports_the_three_fields_and_exits_zero(self, runner, cli_app, project: Path) -> None:
        code, data = _prove_json(runner, cli_app, project)
        assert code == 0, data
        assert data["hooks_fired"] is True
        assert data["first_gate"] == "green"
        assert isinstance(data["time_to_first_proof_s"], float)
        assert data["time_to_first_proof_s"] > 0
        assert data["ok"] is True
        steps = {s["step"]: s["status"] for s in data["steps"]}
        assert steps["prove"] == "done"
        # Chaque hôte dont la configuration déclare un SessionStart a été prouvé, pas seulement le premier.
        sources = {h["source"] for h in data["prove"]["hooks"]}
        assert ".claude/settings.json" in sources
        assert ".github/hooks/grimoire-session-start.json" in sources

    def test_the_proof_is_the_hooks_doing_not_the_scaffold_default(self, runner, cli_app, project: Path) -> None:
        """Le gate était rouge avant, vert après : ce sont les artefacts créés par le hook qui le font passer."""
        _, data = _prove_json(runner, cli_app, project)
        for hook in data["prove"]["hooks"]:
            assert hook["fired"] is True
            assert hook["gate"] == "green"
            assert any(rel.endswith("task-envelope.md") for rel in hook["created"])

    def test_without_prove_the_json_has_no_proof_fields(self, runner, cli_app, project: Path) -> None:
        result = runner.invoke(cli_app, ["-o", "json", "up", str(project), "--backend", "local"])
        data = json.loads(result.stdout)
        assert "hooks_fired" not in data
        assert "first_gate" not in data
        assert "time_to_first_proof_s" not in data

    def test_the_throwaway_task_is_cancelled_and_its_artifacts_removed(
        self, runner, cli_app, governed_project: Path
    ) -> None:
        project = governed_project
        _, data = _prove_json(runner, cli_app, project)
        for hook in data["prove"]["hooks"]:
            task = next(t for t in _ledger_tasks(project) if t["id"] == hook["task_id"])
            assert task["status"] == "cancelled"
            for rel in hook["created"]:
                assert not (project / rel).exists()
            assert not (project / "_grimoire-output" / "evidence" / hook["task_id"]).exists()
        assert data["prove"]["problems"] == []

    def test_text_report_names_the_step(self, runner, cli_app, project: Path) -> None:
        result = runner.invoke(cli_app, ["up", str(project), "--backend", "local", "--prove"])
        assert result.exit_code == 0, result.output
        assert "prove" in result.output
        assert "gate vert" in result.output


class TestProveFailsWhereDoctorStaysGreen:
    def test_disable_all_hooks_fails_the_proof_but_not_doctor(self, runner, cli_app, project: Path) -> None:
        """Le cas de la fiche : hook désactivé, ``doctor`` vert, ``--prove`` rouge."""
        (project / ".claude" / "settings.local.json").write_text('{"disableAllHooks": true}\n', encoding="utf-8")
        doctor = runner.invoke(cli_app, ["doctor", str(project)])
        assert doctor.exit_code == 0, doctor.output

        code, data = _prove_json(runner, cli_app, project)
        assert code == 1
        assert data["ok"] is False
        assert data["hooks_fired"] is False
        disabled = next(h for h in data["prove"]["hooks"] if h["source"] == ".claude/settings.local.json")
        assert "disableAllHooks" in disabled["detail"]

    def test_grimoire_hook_missing_from_the_path_fails_the_proof(
        self, runner, cli_app, project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        doctor = runner.invoke(cli_app, ["doctor", str(project)])
        assert doctor.exit_code == 0, doctor.output

        code, data = _prove_json(runner, cli_app, project)
        assert code == 1
        assert data["hooks_fired"] is False
        assert data["first_gate"] == "red"
        assert all("introuvable" in h["detail"] for h in data["prove"]["hooks"])

    def test_a_hook_that_exits_zero_without_effect_fails_the_proof(
        self, runner, cli_app, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un hook qui sort 0 et ne fait rien est le pire cas : tout paraît tiré."""
        _install_hook_binary(tmp_path / "noop-bin", monkeypatch, body="#!/bin/sh\necho '{}'\nexit 0\n")
        code, data = _prove_json(runner, cli_app, project)
        assert code == 1
        assert data["hooks_fired"] is False
        assert data["first_gate"] == "red"
        assert all("sans effet" in h["detail"] for h in data["prove"]["hooks"])

    def test_a_hook_that_fails_fails_the_proof_with_its_exit_code(
        self, runner, cli_app, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_hook_binary(tmp_path / "bad-bin", monkeypatch, body="#!/bin/sh\necho boom >&2\nexit 3\n")
        code, data = _prove_json(runner, cli_app, project)
        assert code == 1
        assert all("code de sortie 3" in h["detail"] for h in data["prove"]["hooks"])

    def test_no_configured_hook_at_all_fails_the_proof(self, project: Path) -> None:
        import shutil

        (project / ".claude" / "settings.json").unlink()
        shutil.rmtree(project / ".github" / "hooks")
        report = run_prove(project, started=time.monotonic())
        assert report.hooks_fired is False
        assert report.first_gate == "unevaluated"
        assert any("aucune commande SessionStart" in p for p in report.problems)


class TestProveNeverRunsWhatTheProjectDeclares:
    def test_a_foreign_session_start_command_is_not_executed_nor_counted(
        self, runner, cli_app, project: Path, tmp_path: Path
    ) -> None:
        """Un dépôt cloné ne fait pas exécuter une commande arbitraire à ``up --prove``, et elle ne fait pas échouer le hook du kit."""
        sentinel = tmp_path / "executed"
        (project / ".claude" / "settings.local.json").write_text(
            json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"touch {sentinel}"}]}]}}),
            encoding="utf-8",
        )
        code, data = _prove_json(runner, cli_app, project)
        assert not sentinel.exists()
        assert code == 0, data
        assert data["hooks_fired"] is True
        assert data["first_gate"] == "green"
        assert {h["source"] for h in data["prove"]["hooks"]} == {
            ".claude/settings.json",
            ".github/hooks/grimoire-session-start.json",
        }
        (foreign,) = data["prove"]["skipped"]
        assert foreign["source"] == ".claude/settings.local.json"
        assert foreign["fired"] is False
        assert "non reconnue" in foreign["detail"]
        assert "non prouvée" in foreign["detail"]

    def test_a_foreign_command_costs_no_throwaway_task(self, runner, cli_app, governed_project: Path) -> None:
        project = governed_project
        (project / ".github" / "hooks" / "custom.json").write_text(
            json.dumps({"hooks": {"SessionStart": [{"type": "command", "command": "echo hi"}]}}), encoding="utf-8"
        )
        before = len(_ledger_tasks(project))
        code, data = _prove_json(runner, cli_app, project)
        assert code == 0, data
        assert [h["source"] for h in data["prove"]["skipped"]] == [".github/hooks/custom.json"]
        # Deux configurations du kit, deux tâches jetables : la commande étrangère n'en ouvre aucune.
        assert len(_ledger_tasks(project)) == before + len(data["prove"]["hooks"]) == before + 2

    def test_only_foreign_commands_still_fail_the_proof(self, project: Path) -> None:
        import shutil

        # `up` resynchronise les configurations d'hôte : l'état est posé après lui, comme pour « aucune commande ».
        (project / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}),
            encoding="utf-8",
        )
        shutil.rmtree(project / ".github" / "hooks")
        report = run_prove(project, started=time.monotonic())
        assert report.hooks_fired is False
        assert report.first_gate == "unevaluated"
        assert report.ok is False
        assert len(report.skipped) == 1
        assert any("aucune commande SessionStart" in p and "étrangère" in p for p in report.problems)
        assert not (project / BOARD).exists()

    def test_discovery_reads_both_the_nested_and_the_flat_formats(self, project: Path) -> None:
        hooks, unfired = discover_session_start_hooks(project)
        assert unfired == []
        assert {h.source for h in hooks} == {
            ".claude/settings.json",
            ".github/hooks/grimoire-session-start.json",
        }
        assert all(h.command.startswith("grimoire-hook ") for h in hooks)


class TestProveRefusesWhatItCannotProve:
    def test_dry_run_is_refused_and_runs_nothing(self, runner, cli_app, project: Path) -> None:
        before = _ledger_tasks(project)
        code, data = _prove_json(runner, cli_app, project, "--dry-run")
        assert code == 1
        assert data["hooks_fired"] is False
        assert data["first_gate"] == "unevaluated"
        assert "--dry-run" in next(s for s in data["steps"] if s["step"] == "prove")["detail"]
        assert _ledger_tasks(project) == before

    def test_no_standard_is_refused(self, runner, cli_app, project: Path) -> None:
        code, data = _prove_json(runner, cli_app, project, "--no-standard")
        assert code == 1
        assert "--no-standard" in next(s for s in data["steps"] if s["step"] == "prove")["detail"]

    def test_a_gate_already_green_before_the_hook_attributes_nothing_to_it(
        self, runner, cli_app, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Green:
            ok = True
            missing: tuple[str, ...] = ()

        monkeypatch.setattr("grimoire.core.agentic_standard.check_evidence_gates", lambda *a, **k: _Green())
        code, data = _prove_json(runner, cli_app, project)
        assert code == 1
        assert data["first_gate"] == "unevaluated"
        assert all("déjà vert" in h["detail"] for h in data["prove"]["hooks"])


class TestProveLeavesTheGovernanceAsItFoundIt:
    """B03-S1-01 : l'expérience ouvre le ledger et projette le board, ce qui fait « gouverné » pour le hook."""

    def test_an_ungoverned_project_is_still_ungoverned_afterwards(self, runner, cli_app, project: Path) -> None:
        assert not (project / BOARD).exists()
        assert not (project / LEDGER).exists()
        assert "non gouverné" in _session_start_output(project)

        code, data = _prove_json(runner, cli_app, project)
        assert code == 0, data
        assert not (project / BOARD).exists()
        assert not (project / LEDGER).exists()
        assert "non gouverné" in _session_start_output(project)
        assert any("non gouverné" in note for note in data["prove"]["notes"])

    def test_two_runs_add_nothing_to_a_board_that_did_not_exist(self, runner, cli_app, project: Path) -> None:
        for _ in range(2):
            code, _data = _prove_json(runner, cli_app, project)
            assert code == 0
        assert not (project / BOARD).exists()
        assert not (project / LEDGER).exists()

    def test_the_text_report_carries_the_note(self, runner, cli_app, project: Path) -> None:
        result = runner.invoke(cli_app, ["up", str(project), "--backend", "local", "--prove"])
        assert result.exit_code == 0, result.output
        assert "non gouverné" in result.output

    def test_a_governed_project_keeps_its_ledger_and_its_board(self, runner, cli_app, governed_project: Path) -> None:
        project = governed_project
        (before,) = (t["id"] for t in _ledger_tasks(project))
        code, data = _prove_json(runner, cli_app, project)
        assert code == 0, data
        assert (project / BOARD).is_file()
        assert (project / LEDGER / "events.jsonl").is_file()
        assert any(t["id"] == before and t["status"] == "ready" for t in _ledger_tasks(project))
        assert data["prove"]["notes"] == []

    def test_a_failed_proof_also_gives_the_governance_back(
        self, runner, cli_app, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_hook_binary(tmp_path / "bad-bin", monkeypatch, body="#!/bin/sh\nexit 3\n")
        code, _data = _prove_json(runner, cli_app, project)
        assert code == 1
        assert not (project / BOARD).exists()
        assert not (project / LEDGER).exists()
