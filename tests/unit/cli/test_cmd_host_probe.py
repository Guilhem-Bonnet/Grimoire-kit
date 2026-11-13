"""``grimoire host matrix`` et ``grimoire host probe`` (D-07) : codes de sortie et JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.hosts.capabilities import GOVERNANCE_FUNCTIONS
from grimoire.hosts.sync import sync_host_surfaces

pytestmark = pytest.mark.covers("cli:host matrix", "cli:host probe")

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo Sonde"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude"]\n',
        encoding="utf-8",
    )
    return tmp_path


def _invoke(args: list[str]) -> tuple[int, str]:
    result = runner.invoke(app, args)
    return result.exit_code, result.stdout


def test_probe_sort_0_sur_un_projet_synchronise(project: Path) -> None:
    sync_host_surfaces(project)
    code, out = _invoke(["-o", "json", "host", "probe", "--project-root", str(project)])
    payload = json.loads(out)
    assert code == 0 and payload["ok"] is True
    assert [h["host"] for h in payload["hosts"]] == ["host-claude-code-cli"]


def test_probe_sort_1_et_nomme_le_fichier_manquant(project: Path) -> None:
    sync_host_surfaces(project)
    (project / ".claude" / "settings.json").unlink()
    code, out = _invoke(["-o", "json", "host", "probe", "--project-root", str(project)])
    payload = json.loads(out)
    assert code == 1 and payload["ok"] is False
    assert {"path": ".claude/settings.json", "state": "missing", "cause": ""} in payload["hosts"][0]["files"]


def test_probe_texte_dit_ce_qui_manque(project: Path) -> None:
    code, out = _invoke(["host", "probe", "--project-root", str(project)])
    assert code == 1
    assert "settings.json" in out and "missing" in out


def test_probe_hote_explicite_sans_emetteur_est_refuse(project: Path) -> None:
    code, _ = _invoke(["host", "probe", "--project-root", str(project), "--host", "inconnu"])
    assert code == 1


def test_matrix_json_liste_une_cellule_par_regle_et_par_hote(project: Path) -> None:
    code, out = _invoke(["-o", "json", "host", "matrix", "--project-root", str(project), "--host", "all"])
    payload = json.loads(out)
    assert code == 0 and payload["ok"] is True
    assert {c["subject"] for c in payload["cells"]} >= {"hook:pre_tool_use", "hook:session_start"}
    assert {c["host"] for c in payload["cells"]} == {"host-claude-code-cli"}


def test_matrix_texte_affiche_les_statuts(project: Path) -> None:
    code, out = _invoke(["host", "matrix", "--project-root", str(project)])
    assert code == 0
    assert "enforced" in out


def test_matrix_json_declare_chaque_fonction_par_hote(project: Path) -> None:
    """GAO-parit-d-h-te-001 : la matrice porte la parité, hôte par hôte."""
    code, out = _invoke(["-o", "json", "host", "matrix", "--project-root", str(project), "--host", "gemini"])
    payload = json.loads(out)
    assert code == 0
    assert len(payload["functions"]) == len(payload["hosts"]) * len(GOVERNANCE_FUNCTIONS)
    assert {f["function"] for f in payload["functions"]} == {f.value for f in GOVERNANCE_FUNCTIONS}
    for cell in payload["functions"]:
        if cell["state"] != "rich":
            assert cell["reason"].strip(), cell


def test_matrix_texte_dit_les_etats_et_leurs_raisons(project: Path) -> None:
    code, out = _invoke(["host", "matrix", "--project-root", str(project), "--host", "gemini"])
    assert code == 0
    assert "Fonctions de gouvernance" in out
    assert "garde avant outil" in out
    assert "degraded" in out
    # La raison est écrite sous la table, pas seulement un état colorié.
    assert "BeforeTool" in out
