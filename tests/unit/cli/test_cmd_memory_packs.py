"""Surface CLI ``grimoire memory rules pack`` et ``standard score`` (W6-08).

Un vrai projet temporaire, la vraie CLI : l'installation passe par la
frontière de confiance de W6-03 et la vérification lit le vrai code du projet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project-context.yaml").write_text(
        'project:\n  name: "Demo Packs"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    return root


def _clean(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nrequires-python = '>=3.11'\n", encoding="utf-8")
    (root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (root / "app.py").write_text("def f(a=None):\n    return a\n", encoding="utf-8")


def test_list_donne_la_part_verifiee_et_advisory(project: Path) -> None:
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "pack", "list"])
    assert result.exit_code == 0, result.output
    python = next(p for p in json.loads(result.output) if p["id"] == "python")
    assert python["verified"] >= 3
    assert python["rules"] >= 5
    assert python["verified"] + python["advisory"] == python["rules"]


def test_show_dit_pour_chaque_regle_comment_elle_est_verifiee(project: Path) -> None:
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "pack", "show", "python"])
    assert result.exit_code == 0, result.output
    rules = json.loads(result.output)["rules"]
    assert all(r["advisory"] != (r["verifier"] is not None) for r in rules)
    assert {r["verifier"]["kind"] for r in rules if r["verifier"]} >= {"python_ast", "require_regex"}


def test_pack_inconnu_sort_en_erreur(project: Path) -> None:
    assert runner.invoke(app, ["memory", "rules", "pack", "show", "cobol"]).exit_code == 2
    assert runner.invoke(app, ["memory", "rules", "pack", "install", "cobol"]).exit_code == 2


def test_install_ecrit_les_regles_dans_la_couche(project: Path) -> None:
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "pack", "install", "python", "--layer", "team"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["written"]) == 7
    assert payload["refused"] == []
    lines = (project / "_grimoire/standard/rules/team.rules").read_text(encoding="utf-8").splitlines()
    assert "NEVER use bare-except" in lines
    again = json.loads(runner.invoke(app, ["-o", "json", "memory", "rules", "pack", "install", "python", "--layer", "team"]).output)
    assert again["written"] == []
    assert len(again["present"]) == 7


def test_install_dans_une_couche_inconnue_est_refuse_et_n_ecrit_rien(project: Path) -> None:
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "pack", "install", "python", "--layer", "squad"])
    assert result.exit_code == 1
    assert {r["code"] for r in json.loads(result.output)["refused"]} == {"rules.layer_unknown"}
    assert not (project / "_grimoire").exists()


def test_verify_sort_en_erreur_sur_une_regle_violee(project: Path) -> None:
    (project / "bad.py").write_text("try:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
    runner.invoke(app, ["memory", "rules", "pack", "install", "python"])
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "pack", "verify"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    bare = next(r for r in payload["rules"] if r["rule"] == "NEVER use bare-except")
    assert bare["status"] == "fail"
    assert bare["findings"] == ["bad.py:3 `except:` nu"]


def test_verify_passe_sur_un_projet_conforme(project: Path) -> None:
    _clean(project)
    runner.invoke(app, ["memory", "rules", "pack", "install", "python"])
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "pack", "verify"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert (payload["rules_enforced"], payload["rules_advisory"], payload["rules_failing"]) == (5, 2, 0)


def test_standard_score_json_rapporte_separement_controlees_et_advisory(project: Path) -> None:
    _clean(project)
    runner.invoke(app, ["memory", "rules", "pack", "install", "python"])
    result = runner.invoke(app, ["-o", "json", "standard", "score", str(project), "--profile", "starter"])
    payload = json.loads(result.output)
    assert payload["rules_enforced"] == 5
    assert payload["rules_advisory"] == 2
    assert payload["rules_failing"] == 0


def test_standard_verify_echoue_sur_une_regle_de_pack_violee(project: Path) -> None:
    (project / "bad.py").write_text("x = eval('1')\n", encoding="utf-8")
    runner.invoke(app, ["memory", "rules", "pack", "install", "python"])
    result = runner.invoke(app, ["-o", "json", "standard", "verify", str(project), "--profile", "starter"])
    assert result.exit_code == 1
    assert "rules.pack_violation" in result.output
