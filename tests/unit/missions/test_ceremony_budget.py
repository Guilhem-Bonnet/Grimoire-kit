"""Le budget de cérémonie du kit tient, et chaque dérive le fait échouer (W6-01)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from grimoire.__version__ import __version__
from grimoire.cli.app import app
from grimoire.missions.ceremony_budget import MIN_CORPUS, check_budget

_BUDGET = Path(__file__).resolve().parents[3] / "evals" / "ceremony-budget.yaml"

pytestmark = pytest.mark.skipif(not _BUDGET.is_file(), reason="dépôt source uniquement")


@pytest.fixture
def doc() -> dict[str, Any]:
    data = YAML(typ="safe").load(_BUDGET.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _version_au_plus_tard() -> str:
    cal = YAML(typ="safe").load(_BUDGET.read_text(encoding="utf-8"))["calibration"]["kit_version"]
    major, minor = (int(p) for p in str(cal).split(".")[:2])
    return f"{major}.{minor + 2}.9"


def test_le_budget_du_kit_tient(doc: dict[str, Any]) -> None:
    """Le contrôle que la CI rejoue : il échoue dès que le code ou le corpus dérive."""
    assert check_budget(doc, kit_version=__version__) == []


def test_le_corpus_fige_douze_taches_types(doc: dict[str, Any]) -> None:
    assert len(doc["corpus"]) >= MIN_CORPUS == 12


def test_calibration_perimee_au_dela_de_deux_mineures(doc: dict[str, Any]) -> None:
    assert check_budget(doc, kit_version=_version_au_plus_tard()) == []
    major, minor = (int(p) for p in str(doc["calibration"]["kit_version"]).split(".")[:2])
    trop_tard = check_budget(doc, kit_version=f"{major}.{minor + 3}.0")
    assert any("calibration périmée" in v for v in trop_tard)
    assert any("calibration périmée" in v for v in check_budget(doc, kit_version=f"{major + 1}.0.0"))


def _mute(doc: dict[str, Any], chemin: tuple[Any, ...], valeur: Any) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    cible = out
    for cle in chemin[:-1]:
        cible = cible[cle]
    if valeur is _SUPPRIMER:
        del cible[chemin[-1]]
    else:
        cible[chemin[-1]] = valeur
    return out


_SUPPRIMER = object()


@pytest.mark.parametrize(
    ("chemin", "valeur", "attendu"),
    [
        (("$schema",), "autre/v0", "$schema"),
        (("calibration", "calibrated"), "non", "calibration.calibrated"),
        (("calibration", "as_of"), "hier", "calibration.as_of"),
        (("calibration", "kit_version"), "x", "calibration.kit_version"),
        (("indices", "files_wide", "value"), 99, "indices.files_wide.value"),
        (("indices", "files_wide", "calibrated"), True, "indices.files_wide.calibrated"),
        (("indices", "impact_wide", "as_of"), "2020-01-01", "indices.impact_wide.as_of"),
        (("indices", "impact_wide", "calibrated"), _SUPPRIMER, "indices.impact_wide.calibrated"),
        (("indices", "impact_wide"), _SUPPRIMER, "indices.impact_wide"),
        (("indices", "fantome"), {"value": 1, "calibrated": False, "as_of": "2026-10-18"}, "indices.fantome"),
        (("routes", "guarded", "max_steps"), 3, "routes.guarded"),
        (("routes", "session", "max_human_turns"), 2, "routes.session"),
        (("routes", "express", "test_depth"), "standard", "routes.express.test_depth"),
        (("routes", "express"), _SUPPRIMER, "routes.express"),
        (("routes", "bonus"), {"max_steps": 1, "max_human_turns": 0, "test_depth": "floor"}, "routes.bonus"),
        (("corpus_budget", "max_human_turns"), 9, "corpus_budget.max_human_turns"),
        (("corpus_budget", "max_per_route", "guarded"), 2, "corpus_budget.max_per_route.guarded"),
        (("corpus_budget", "max_per_route", "inconnue"), 1, "corpus_budget.max_per_route.inconnue"),
        (("corpus", 0, "expect", "route"), "session", "corpus.T01-doc-typo"),
        (("corpus", 1, "files"), None, "corpus.T02-small-fix-with-test"),
        (("corpus", 2, "risk_profile"), "bof", "corpus.T03-fix-without-command"),
    ],
)
def test_chaque_derive_fait_echouer(doc: dict[str, Any], chemin: tuple[Any, ...], valeur: Any, attendu: str) -> None:
    violations = check_budget(_mute(doc, chemin, valeur), kit_version=__version__)
    assert any(attendu in v for v in violations), violations


def test_corpus_trop_court_ou_en_double(doc: dict[str, Any]) -> None:
    court = copy.deepcopy(doc)
    court["corpus"] = court["corpus"][:MIN_CORPUS - 1]
    assert any("au moins" in v for v in check_budget(court, kit_version=__version__))
    double = copy.deepcopy(doc)
    double["corpus"].append(copy.deepcopy(double["corpus"][0]))
    assert any("en double" in v for v in check_budget(double, kit_version=__version__))


def test_budget_illisible() -> None:
    assert check_budget([], kit_version=__version__) == ["budget : table YAML attendue"]
    vide = check_budget({"$schema": "grimoire-ceremony-budget/v1"}, kit_version=__version__)
    assert {"calibration : bloc absent", "indices : table absente", "routes : table absente",
            "corpus : liste absente"} <= set(vide)


def test_cli_budget(tmp_path: Path, doc: dict[str, Any]) -> None:
    runner = CliRunner()
    ok = runner.invoke(app, ["ceremony", "budget", "--file", str(_BUDGET)])
    assert ok.exit_code == 0, ok.output
    derive = tmp_path / "budget.yaml"
    yaml = YAML(typ="safe")
    with derive.open("w", encoding="utf-8") as fh:
        yaml.dump(_mute(doc, ("corpus_budget", "max_human_turns"), 0), fh)
    ko = runner.invoke(app, ["ceremony", "budget", "--file", str(derive), "--json"])
    assert ko.exit_code == 1 and "max_human_turns" in ko.stdout
    (tmp_path / "casse.yaml").write_text("a: [", encoding="utf-8")
    assert runner.invoke(app, ["ceremony", "budget", "--file", str(tmp_path / "casse.yaml")]).exit_code == 2
