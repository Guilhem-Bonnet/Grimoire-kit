"""``grimoire doctor`` : la ligne d'information sur les skills servis par MCP.

Ne dépend pas du SDK ``mcp`` : le catalogue est un module pur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.cli.cmd_up import run_env_checks
from grimoire.cli.doctor_mcp import check_mcp_skills


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    framework = tmp_path / "framework"
    (framework / "hosts" / "skills").mkdir(parents=True)
    monkeypatch.setattr("grimoire.data.framework_path", lambda: framework)
    monkeypatch.setattr("grimoire.hosts.collect.framework_path", lambda: framework)
    root = tmp_path / "projet"
    directory = root / "_grimoire" / "overrides" / "skills"
    directory.mkdir(parents=True)
    (directory / "triage.md").write_text("---\ndescription: Quand trier.\n---\n\nCorps\n", encoding="utf-8")
    return root


def test_ligne_d_information_avec_le_nombre_de_skills(project: Path) -> None:
    check = check_mcp_skills(project)
    assert check.name == "mcp_skills"
    assert check.passed is True and check.level == "info"
    assert check.detail == "skills servis par MCP : 1 (extension io.modelcontextprotocol/skills)"


def test_les_exclus_sont_nommes_avec_leur_raison_sans_bloquer(project: Path) -> None:
    (project / "_grimoire" / "overrides" / "skills" / "Mauvais.md").write_text(
        "---\ndescription: x\n---\n\nCorps\n", encoding="utf-8"
    )
    check = check_mcp_skills(project)
    assert check.passed is True and check.level == "info"
    assert check.detail.startswith("skills servis par MCP : 1 (extension io.modelcontextprotocol/skills) ; non servis : Mauvais (")
    assert "majuscule" in check.detail


def test_une_collecte_en_echec_n_est_pas_un_echec_du_doctor(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path, *, log: bool = False) -> None:
        raise OSError("disque")

    monkeypatch.setattr("grimoire.mcp.skills_catalogue.build_catalogue", boom)
    check = check_mcp_skills(project)
    assert check.passed is True and check.level == "info" and "non mesuré (OSError)" in check.detail


def test_la_sonde_fait_partie_des_controles_d_environnement(project: Path) -> None:
    assert "mcp_skills" in {c.name for c in run_env_checks(project)}
