"""Issue #874 — `grimoire doctor` signale un skill de projet hors spec, sans échouer.

Un skill de projet non conforme est une dette à nommer avec sa règle et sa
valeur, jamais un FAIL : le projet reste utilisable, et le remède est dit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core import layout


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _no_env_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les sondes d'environnement n'ont rien à voir avec ce check ; les couper rend le test hermétique."""
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


def _write_project_skill(root: Path, slug: str, text: str) -> None:
    directory = layout.overrides_dir(root) / layout.SKILLS_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{slug}.md").write_text(text, encoding="utf-8")


def _skill_spec_check(output: str) -> dict:
    data = json.loads(output)
    return next(c for c in data["checks"] if c["name"] == "skill_spec")


class TestDoctorSkillSpec:
    def test_non_conforming_project_skill_is_a_warning_not_a_failure(
        self,
        runner: CliRunner,
        init_project: Path,
    ) -> None:
        """Rouge avant le correctif : aucun contrôle ne nommait le skill hors spec."""
        _write_project_skill(
            init_project,
            "mauvais--skill",
            "---\ndescription: 'Une description.'\n---\n# Corps\n",
        )

        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        check = _skill_spec_check(result.output)

        assert check["passed"] is True  # jamais un FAIL
        assert check["level"] == "warn"
        assert "tiret doublé" in check["detail"]
        assert "mauvais--skill" in check["detail"]

    def test_conforming_project_skill_is_silent_on_this_check(
        self,
        runner: CliRunner,
        init_project: Path,
    ) -> None:
        """Contre-épreuve : un skill conforme ne fait pas crier le check."""
        _write_project_skill(
            init_project,
            "bon-skill",
            "---\ndescription: 'Une description utile.'\n---\n# Corps\n",
        )

        result = runner.invoke(app, ["-o", "json", "doctor", str(init_project)])
        check = _skill_spec_check(result.output)

        assert check["passed"] is True
        assert check["level"] == "ok"
