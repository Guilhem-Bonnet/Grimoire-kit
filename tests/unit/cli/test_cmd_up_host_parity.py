"""``grimoire up`` dit, pour l'hôte détecté, ce qui est dégradé ou absent.

GAO-parit-d-h-te-001 : la table de parité de
:mod:`grimoire.hosts.capabilities` doit atteindre l'utilisateur là où il lance
le kit, pas seulement dans ``grimoire host matrix``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import parity_line
from grimoire.hosts.parity import detected_host_parity


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucune sonde d'environnement, aucune inscription au cockpit réel."""
    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    monkeypatch.setattr("grimoire.cli.cmd_up.run_env_checks", lambda target: [])


def test_the_first_resolved_alias_names_the_detected_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "grimoire.hosts.parity.resolve_enabled_hosts",
        lambda root, cfg=None: ["pas-un-hote", "gemini"],
    )
    report = detected_host_parity(tmp_path)
    assert report["host_id"] == HostId.GEMINI_CLI.value
    assert report["line"] == parity_line(HostId.GEMINI_CLI)


def test_no_resolved_alias_means_no_parity_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("grimoire.hosts.parity.resolve_enabled_hosts", lambda root, cfg=None: [])
    assert detected_host_parity(tmp_path) == {}


def test_up_prints_the_governance_line_for_the_detected_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.app import app

    monkeypatch.setattr(
        "grimoire.hosts.parity.resolve_enabled_hosts",
        lambda root, cfg=None: ["gemini"],
    )
    result = CliRunner().invoke(app, ["up", str(tmp_path / "proj"), "--backend", "local"])
    assert result.exit_code == 0, result.output
    assert "Governance (detected host):" in result.output
    assert "absent" in result.output
