"""ADR-007 point 4 — `grimoire doctor` signale la divergence board/ledger.

Un projet enrôlé au standard dont le board n'a pas (ou plus) de Mission Ledger
complet (issue #559, constat #521) doit être nommé par `doctor`, avec un
remède explicite (`grimoire task migrate-standard`), jamais en silence et
jamais en FAIL — ADR-007 : « jamais une correction automatique ».
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _no_env_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les sondes réseau/sous-processus de `doctor` n'ont rien à voir avec ce
    check ; les désactiver rend le test rapide et hermétique."""
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


def _write_board_without_ledger(root: Path) -> None:
    board_dir = root / "_grimoire" / "standard"
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "task-board.yaml").write_text(
        "$schema: grimoire-agentic-standard-task-board/v1\n"
        "metadata:\n  project: test-project\n"
        "states: [proposed, ready, in_progress, blocked, review, accepted, released, archived]\n"
        "transitions: {}\n"
        "tasks:\n"
        "  - task_id: bootstrap\n"
        "    title: Bootstrap agentic standard runtime\n"
        "    status: proposed\n"
        "    acceptance_criteria: ['Standard artifacts are generated and verified.']\n",
        encoding="utf-8",
    )


class TestDoctorTaskUnification:
    def test_board_without_ledger_is_a_named_warning_not_a_failure(
        self, runner: CliRunner, init_project: Path,
    ) -> None:
        """Rouge avant le correctif : ce check n'existait pas — `doctor` ne
        disait jamais qu'un board sans ledger empêche l'espace Exécuter et le
        panneau Preuves du cockpit de voir la moindre tâche (#521)."""
        _write_board_without_ledger(init_project)

        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        data = json.loads(result.output)

        check = next(c for c in data["checks"] if c["name"] == "task_unification")
        assert check["passed"] is True  # jamais un FAIL — ADR-007 point 4
        assert check["level"] == "warn"
        assert "migrate-standard" in check["detail"]
        # Jamais de correction automatique : la commande le dit, ne l'exécute pas.
        from grimoire.missions.service import DEFAULT_LEDGER_RELPATH

        assert not (init_project / DEFAULT_LEDGER_RELPATH / "events.jsonl").is_file()

    def test_migrated_project_is_silent_on_this_check(
        self, runner: CliRunner, init_project: Path,
    ) -> None:
        """Contre-épreuve : une fois migré, plus de WARN — sinon le check crierait
        toujours, quoi que fasse le projet."""
        _write_board_without_ledger(init_project)
        migrate = runner.invoke(app, ["task", "migrate-standard", str(init_project)])
        assert migrate.exit_code == 0, migrate.output

        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        data = json.loads(result.output)
        check = next(c for c in data["checks"] if c["name"] == "task_unification")
        assert check.get("level") != "warn"
        assert "en phase" in check["detail"]

    def test_project_without_the_standard_is_silent(
        self, runner: CliRunner, init_project: Path,
    ) -> None:
        """Un projet qui n'a jamais adopté le standard n'a rien à diverger."""
        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        data = json.loads(result.output)
        names = {c["name"] for c in data["checks"]}
        assert "task_unification" not in names
