"""Entretien de cadrage (#1342 M2) : sept angles, profondeur par l'intake, réponses, gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.cadrage import check, scaffold
from grimoire.core.cadrage_interview import ANGLES, answer, check_interview, classify_depth, interview_plan

pytestmark = pytest.mark.covers("cli:cadrage interview answer")
runner = CliRunner()


def _plan(root: Path, *args: str) -> dict:
    res = runner.invoke(app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root), "-o", "json", *args])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    res = runner.invoke(app, ["cadrage", "new", "Lister les tâches", "--slug", "demo", str(tmp_path)])
    assert res.exit_code == 0, res.output
    return tmp_path


def test_cadrage_interview_seven_angles(root: Path) -> None:
    plan = _plan(root)
    assert [a["id"] for a in plan["angles"]] == [a.id for a in ANGLES]
    assert len(plan["angles"]) == 7
    assert {a["state"] for a in plan["angles"]} == {"ouvert"}
    assert all(a["intent"] and a["questions"] for a in plan["angles"])


def test_cadrage_interview_text_output(root: Path) -> None:
    res = runner.invoke(app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root)])
    assert res.exit_code == 0
    assert "utilisateurs" in res.output and "profondeur" in res.output


def test_cadrage_interview_depth_from_intake() -> None:
    assert classify_depth({"risk_profile": "light", "mission_type": "analysis", "scope_hints": ["repo"]}) == "express"
    assert classify_depth({"risk_profile": "standard", "mission_type": "implementation", "scope_hints": ["repo"]}) == "standard"
    assert classify_depth({"risk_profile": "strict", "mission_type": "implementation", "scope_hints": []}) == "approfondi"
    assert classify_depth({"risk_profile": "standard", "mission_type": "migration", "scope_hints": []}) == "approfondi"


def test_cadrage_interview_depth_bounds_questions(root: Path) -> None:
    express = _plan(root, "--depth", "express")
    assert express["depth_source"] == "forcée"
    assert sum(a["required"] for a in express["angles"]) == 3
    assert {len(a["questions"]) for a in express["angles"]} == {1}
    deep = _plan(root, "--depth", "approfondi")
    assert all(a["required"] for a in deep["angles"])
    assert {len(a["questions"]) for a in deep["angles"]} == {2}


def test_cadrage_interview_bad_depth(root: Path) -> None:
    res = runner.invoke(app, ["cadrage", "interview", "--chantier", "demo", "--path", str(root), "--depth", "zzz"])
    assert res.exit_code == 1


def test_cadrage_interview_depth_is_frozen(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="x", chantier="demo")
    first = interview_plan(tmp_path, "demo", text="Delete all prod database")
    assert first["depth"] == "approfondi"
    assert interview_plan(tmp_path, "demo", text="list files")["depth"] == "approfondi"


def test_cadrage_interview_check_cli_refuses(root: Path) -> None:
    _plan(root, "--depth", "express")
    res = runner.invoke(app, ["cadrage", "check", "--chantier", "demo", str(root)])
    assert res.exit_code == 1 and "angle(s) obligatoire(s)" in res.output


def test_cadrage_interview_answer_writes_files(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="x", chantier="demo")
    interview_plan(tmp_path, "demo", depth="express")
    answer(tmp_path, "demo", "utilisateurs", "Les mainteneurs du kit")
    answer(tmp_path, "demo", "criteres", "`pytest -k x` passe")
    view = answer(tmp_path, "demo", "hors-perimetre", "", unknown=True)
    d = tmp_path / "_grimoire" / "cadrage" / "demo"
    brief = (d / "01-brief.md").read_text()
    assert "- Les mainteneurs du kit" in brief and "- `pytest -k x` passe" in brief
    assert "À compléter" not in brief.split("## L'utilisateur")[1].split("##")[0]
    assert "À valider (je ne sais pas)" in (d / "03-comprehension.md").read_text().split("## Hypothèses")[1]
    assert view["complete"] is True and check_interview(tmp_path, "demo") == []


def test_cadrage_interview_skip_needs_reason(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="x", chantier="demo")
    interview_plan(tmp_path, "demo", depth="express")
    with pytest.raises(ValueError, match="raison"):
        answer(tmp_path, "demo", "utilisateurs", "", skip=True)
    view = answer(tmp_path, "demo", "utilisateurs", "outil interne sans utilisateur", skip=True)
    angle = next(a for a in view["angles"] if a["id"] == "utilisateurs")
    assert angle["state"] == "ecarte" and angle["reason"]
    assert "utilisateurs" not in view["open_required"]


def test_cadrage_interview_answer_errors(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="x", chantier="demo")
    with pytest.raises(ValueError, match="angle inconnu"):
        answer(tmp_path, "demo", "nope", "x")
    with pytest.raises(ValueError, match="vide"):
        answer(tmp_path, "demo", "utilisateurs", " ")
    with pytest.raises(ValueError, match="s'excluent"):
        answer(tmp_path, "demo", "utilisateurs", "x", unknown=True, skip=True)


def test_cadrage_interview_gate_refuses_open_angle(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="x", chantier="demo")
    assert not any("Entretien" in e for e in check(tmp_path, "demo")[0])
    interview_plan(tmp_path, "demo", depth="express")
    assert any("Entretien" in e for e in check(tmp_path, "demo")[0])


def test_cadrage_interview_answer_cli(root: Path) -> None:
    _plan(root, "--depth", "express")
    res = runner.invoke(app, ["cadrage", "interview", "answer", "criteres", "ok", "--chantier", "demo", "--path", str(root)])
    assert res.exit_code == 0, res.output
    assert "encore ouverts" in res.output
    res = runner.invoke(app, ["cadrage", "interview", "answer", "bad", "x", "--chantier", "demo", "--path", str(root)])
    assert res.exit_code == 1
