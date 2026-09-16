"""``grimoire doctor`` doit dire quand l'outil qui l'exécute est en retard sur
le kit aligné du projet (issue #510 point 4).

Cas réel : un homelab piloté par un pipx 3.50.1 alors que le cockpit (un
process séparé) avait mis le projet à niveau en 3.50.2. Ni l'ancien
``doctor`` ni la fiche projet du cockpit ne le disaient — le badge Kit ne
comparait la version alignée qu'à celle du serveur cockpit, jamais à l'outil
qui gouverne vraiment le projet. Ce test couvre le nouveau check
``tool_version`` : WARN (jamais FAIL) quand l'outil est plus vieux, OK à
l'égalité.

Utilise ``real_project`` (``tests/conftest.py``) — un vrai
``_grimoire/kit`` scaffoldé par ``grimoire init``, seule façon d'obtenir une
version alignée réelle à comparer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.tools import project_health


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _env_ok():
    """Neutralise les sondes d'environnement pour un résultat déterministe."""
    from unittest.mock import MagicMock, patch

    proc = MagicMock(returncode=0, stdout="1.0\n")
    return (
        patch("grimoire.cli.cmd_up.shutil.which", return_value="/usr/bin/tool"),
        patch("grimoire.cli.cmd_up.subprocess.run", return_value=proc),
        patch("grimoire.cli.cmd_up.socket.create_connection", side_effect=OSError("down")),
    )


class TestDoctorToolVersion:
    def test_warns_when_the_running_tool_is_older_than_the_project_kit(
        self, runner: CliRunner, real_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Issue #519 : la référence est désormais `upVersion` (le marqueur
        # écrit par le `grimoire init` réel de cette fixture), pas `aligned`
        # (le catalogue de digests, qui date un contenu à sa première
        # introduction plutôt qu'au dernier passage réel de l'outil).
        aligned = project_health.kit_alignment(real_project)["upVersion"]
        assert aligned, "le projet réel doit avoir une version de dernier `up` à comparer"
        monkeypatch.setattr(project_health, "_installed_kit_version", lambda: "0.0.1")

        p_which, p_run, p_sock = _env_ok()
        with p_which, p_run, p_sock:
            result = runner.invoke(app, ["-o", "json", "doctor", str(real_project)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        check = next(c for c in data["checks"] if c["name"] == "tool_version")
        assert check["passed"] is True, "un écart d'outil ne doit jamais faire échouer doctor"
        assert check["level"] == "warn"
        assert "0.0.1" in check["detail"]
        assert aligned in check["detail"]
        assert "pipx upgrade grimoire-kit" in check["detail"]

    def test_ok_when_the_running_tool_matches_the_project_kit(
        self, runner: CliRunner, real_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        aligned = project_health.kit_alignment(real_project)["upVersion"]
        assert aligned, "le projet réel doit avoir une version de dernier `up` à comparer"
        # `upVersion` (marqueur du dernier `up` réel) et l'outil qui a lancé
        # cette suite peuvent légitimement différer (dev non publié) : on fixe
        # l'outil dessus pour isoler le cas « à jour » sans dépendre de
        # l'état de release de la machine qui exécute les tests.
        monkeypatch.setattr(project_health, "_installed_kit_version", lambda: aligned)

        p_which, p_run, p_sock = _env_ok()
        with p_which, p_run, p_sock:
            result = runner.invoke(app, ["-o", "json", "doctor", str(real_project)])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        check = next(c for c in data["checks"] if c["name"] == "tool_version")
        assert check["passed"] is True
        assert check.get("level") not in {"warn", "fail"}
        assert aligned in check["detail"]
