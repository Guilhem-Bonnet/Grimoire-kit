"""Route de cérémonie déterministe et profondeur de test mesurée (W6-01)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.evidence.schemas import VerdictResult, VerificationCheck
from grimoire.missions.ceremony import (
    INDICES,
    MAX_ROUTES,
    ROUTES,
    DepthLevel,
    RouteInputs,
    check_kinds,
    depth_shortfall,
    inputs_for,
    reached_depth,
    render_json,
    route_named,
    suggest,
    verified_commands,
)
from grimoire.missions.schemas import RiskProfile
from grimoire.missions.verifiability import Verifiability


def _inputs(**kw: object) -> RouteInputs:
    base: dict[str, object] = {
        "verifiability": Verifiability.V0, "risk_profile": RiskProfile.STANDARD,
        "files": ("src/app/a.py",), "test_runner": "pytest", "impact_files": 2,
    }
    base.update(kw)
    return RouteInputs.build(**base)  # type: ignore[arg-type]


def _route(**kw: object) -> str:
    return suggest(_inputs(**kw)).route.name


# ── Les routes ──────────────────────────────────────────────────────────────

def test_au_plus_cinq_routes_ordonnees_par_cout() -> None:
    assert len(ROUTES) <= MAX_ROUTES
    assert [r.rank for r in ROUTES] == list(range(len(ROUTES)))
    turns = [r.human_turns for r in ROUTES]
    assert turns == sorted(turns)
    assert route_named("session").human_turns == 3
    with pytest.raises(KeyError):
        route_named("inconnue")


def test_v0_petit_changement_route_express_a_profondeur_floor() -> None:
    s = suggest(_inputs())
    assert (s.route.name, s.route.test_depth) == ("express", DepthLevel.FLOOR)
    assert s.fired == ()


@pytest.mark.parametrize(
    ("kw", "route"),
    [
        ({"verifiability": Verifiability.V1}, "checked"),
        ({"verifiability": Verifiability.V2}, "session"),
        ({"risk_profile": RiskProfile.STRICT}, "reviewed"),
        ({"risk_profile": RiskProfile.SECURITY_CRITICAL}, "guarded"),
        ({"risk_profile": RiskProfile.RELEASE}, "guarded"),
        ({"files": None}, "checked"),
        ({"files": tuple(f"src/m{i}.py" for i in range(11))}, "checked"),
        ({"impact_files": 21}, "checked"),
        ({"files": (".env.production",)}, "guarded"),
        ({"files": (".github/workflows/ci.yml",)}, "guarded"),
        ({"files": ("app/migrations/0001_init.py",)}, "guarded"),
        ({"files": ("config/id_rsa.pub",)}, "guarded"),
    ],
)
def test_chaque_regle_monte_au_moins_a_son_plancher(kw: dict[str, object], route: str) -> None:
    assert _route(**kw) == route


def test_les_seuils_sont_des_bornes_strictes() -> None:
    files_wide = next(i.value for i in INDICES if i.name == "files_wide")
    impact_wide = next(i.value for i in INDICES if i.name == "impact_wide")
    assert _route(files=tuple(f"src/m{i}.py" for i in range(files_wide))) == "express"
    assert _route(impact_files=impact_wide) == "express"


def test_la_route_est_le_maximum_des_planchers_quel_que_soit_leur_nombre() -> None:
    lourde = _inputs(verifiability=Verifiability.V2, risk_profile=RiskProfile.STRICT, files=None, impact_files=99)
    assert suggest(lourde).route.name == "session"
    regles = {f.rule for f in suggest(lourde).fired}
    assert {"verifiability", "risk_profile", "files_unknown", "impact_wide"} <= regles


def test_une_entree_inconnue_ne_descend_jamais_la_route() -> None:
    """Fichiers inconnus : on ne sait pas borner le changement, la route monte."""
    assert _route(files=None) == "checked"
    # Un impact non mesuré ne monte pas la route, mais la limite est écrite.
    s = suggest(_inputs(impact_files=None))
    assert s.route.name == "express"
    assert any("impact non mesuré" in limit for limit in s.limits)


def test_sans_commande_de_test_la_limite_est_affichee() -> None:
    s = suggest(_inputs(verifiability=Verifiability.V1, test_runner=None))
    assert s.route.test_depth is DepthLevel.STANDARD
    assert any("aucune commande de test résolue" in limit for limit in s.limits)
    assert not any("aucune commande de test" in limit for limit in suggest(_inputs(test_runner=None)).limits)


def test_chaque_seuil_porte_calibrated_et_sa_date() -> None:
    data = suggest(_inputs()).to_dict()
    assert data["indices"], "aucun seuil exposé"
    for index in data["indices"]:
        assert isinstance(index["calibrated"], bool)
        assert len(index["as_of"]) == 10
    # Aucun n'est calibré aujourd'hui, et la sortie le dit.
    assert any("non calibrés" in limit for limit in data["limits"])


# ── Déterminisme ────────────────────────────────────────────────────────────

def test_deux_appels_rendent_les_memes_octets() -> None:
    a = render_json(suggest(_inputs(files=("b.py", "a.py", "./a.py"))))
    b = render_json(suggest(_inputs(files=("a.py", "b.py"))))
    assert a.encode("utf-8") == b.encode("utf-8")
    assert json.loads(a)["inputs"]["files"] == ["a.py", "b.py"]


def test_l_empreinte_couvre_les_entrees() -> None:
    assert suggest(_inputs()).inputs_digest != suggest(_inputs(impact_files=3)).inputs_digest
    assert suggest(_inputs()).inputs_digest == suggest(_inputs()).inputs_digest


def test_impact_negatif_refuse() -> None:
    with pytest.raises(ValueError, match="impact_files"):
        _inputs(impact_files=-1)


def test_inputs_for_reprend_la_remontee_sans_commande() -> None:
    sans = inputs_for(title="Fix the parser", acceptance=("les tests passent",), has_checks=False,
                      files=("a.py",), test_runner="pytest", impact_files=1)
    avec = inputs_for(title="Fix the parser", acceptance=("les tests passent",), has_checks=True,
                      files=("a.py",), test_runner="pytest", impact_files=1)
    assert (sans.verifiability, avec.verifiability) == (Verifiability.V1, Verifiability.V0)


def test_inputs_for_le_risque_ne_peut_que_monter() -> None:
    bas = inputs_for(title="Rotate the API key", acceptance=("le lint passe",), has_checks=True,
                     declared_risk=RiskProfile.LIGHT, files=("a.py",), test_runner=None, impact_files=None)
    assert bas.risk_profile is RiskProfile.SECURITY_CRITICAL
    haut = inputs_for(title="Fix a typo", acceptance=("le lint passe",), has_checks=True,
                      declared_risk=RiskProfile.RELEASE, files=("a.py",), test_runner=None, impact_files=None)
    assert haut.risk_profile is RiskProfile.RELEASE


# ── Nature des commandes et profondeur ──────────────────────────────────────

@pytest.mark.parametrize(
    ("cmd", "kinds"),
    [
        ("pytest -q", {"test"}),
        ("python -m pytest tests/unit", {"test"}),
        ("uv run pytest", {"test"}),
        (".venv/bin/pytest -x", {"test"}),
        ("PYTHONPATH=src python3 -m pytest", {"test"}),
        ("go test ./...", {"test"}),
        ("npm test", {"test"}),
        ("ruff check src", {"static"}),
        ("mypy --strict src/", {"static"}),
        ("ruff check src && pytest -q", {"test", "static"}),
        ("echo ok", set()),
        ("go build ./...", set()),
        ("cat 'unterminated", set()),
    ],
)
def test_nature_des_commandes(cmd: str, kinds: set[str]) -> None:
    assert set(check_kinds(cmd)) == kinds


def test_la_commande_de_test_declaree_par_le_projet_compte() -> None:
    assert check_kinds("tox -e py311 -- -q") == {"test"}
    assert check_kinds("./run-suite.sh --fast") == frozenset()
    assert check_kinds("./run-suite.sh --fast", {"test-runner": "./run-suite.sh"}) == {"test"}
    assert check_kinds("just check", {"lint": "just check"}) == {"static"}


def test_profondeur_atteinte_seules_les_commandes_vertes_comptent() -> None:
    assert reached_depth([]) is None
    assert reached_depth([("pytest", False)]) is None
    assert reached_depth([("echo ok", True)]) is DepthLevel.FLOOR
    assert reached_depth([("pytest", True)]) is DepthLevel.STANDARD
    assert reached_depth([("pytest", True), ("ruff check", False)]) is DepthLevel.STANDARD
    assert reached_depth([("pytest", True), ("ruff check", True)]) is DepthLevel.STRICT


def test_depth_shortfall() -> None:
    assert depth_shortfall("floor", [("echo ok", True)]) is None
    manque = depth_shortfall("standard", [("echo ok", True)])
    assert manque is not None and "standard" in manque and "floor" in manque
    assert depth_shortfall("strict", [("pytest", True)]) is not None
    assert depth_shortfall("strict", [("pytest", True), ("mypy src", True)]) is None
    assert "aucune" in (depth_shortfall("floor", []) or "")


def test_profondeur_inconnue_refusee() -> None:
    refus = depth_shortfall("paranoid", [("pytest", True), ("mypy", True)])
    assert refus is not None and "inconnue" in refus


def test_verified_commands_relit_le_pack_et_le_verdict(tmp_path: Path) -> None:
    svc = EvidenceService(tmp_path)
    items = [
        EvidenceItem.from_text("check-01", EvidenceKind.TEST, "ok", uri="check:pytest -q", summary="pytest"),
        EvidenceItem.from_text("check-02", EvidenceKind.TEST, "ko", uri="check:mypy src", summary="mypy"),
        EvidenceItem.from_text("log-01", EvidenceKind.LOG, "x", uri="artefact://log", summary="log"),
    ]
    pack = svc.create_pack("T-1", EvidenceProfile.LIGHT, items)
    verdict = svc.verify(pack, extra_checks=(
        VerificationCheck(id="command-01", result=VerdictResult.PASSED),
        VerificationCheck(id="command-02", result=VerdictResult.FAILED, reason="exit 1"),
    ))
    assert verified_commands(pack, verdict) == (("pytest -q", True), ("mypy src", False))
    assert verified_commands(None, verdict) == ()
    assert verified_commands(pack, None) == ()


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_cli_suggest_sortie_identique_octet_pour_octet(tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["ceremony", "suggest", "--title", "Fix the pagination helper", "-a", "les tests passent",
            "--check", "pytest -q", "-f", "src/b.py", "-f", "src/a.py", "--impact", "2",
            "--project-root", str(tmp_path), "--json"]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert first.stdout_bytes == second.stdout_bytes
    data = json.loads(first.stdout)
    assert data["route"] == "express" and data["inputs"]["files"] == ["src/a.py", "src/b.py"]


def test_cli_suggest_sans_fichiers_ne_descend_pas_sous_checked(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["ceremony", "suggest", "--title", "Fix it", "-a", "les tests passent",
                                      "--check", "pytest", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["route"] == "checked"


def test_cli_suggest_texte_et_entrees_invalides(tmp_path: Path) -> None:
    runner = CliRunner()
    texte = runner.invoke(app, ["ceremony", "suggest", "--title", "Concevoir le cache", "-a", "l'API est élégante",
                                "--project-root", str(tmp_path)])
    assert texte.exit_code == 0, texte.output
    assert "route       : session" in texte.stdout and "non calibré" in texte.stdout
    assert runner.invoke(app, ["ceremony", "suggest", "--project-root", str(tmp_path)]).exit_code == 2
    assert runner.invoke(app, ["ceremony", "suggest", "--title", "x", "--risk", "bof",
                               "--project-root", str(tmp_path)]).exit_code == 2
    assert runner.invoke(app, ["ceremony", "suggest", "x-inconnue", "--project-root", str(tmp_path)]).exit_code == 2


def test_cli_suggest_diff_base_refuse_sans_git(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["ceremony", "suggest", "--title", "x", "--diff-base", "HEAD",
                                      "--project-root", str(tmp_path)])
    assert result.exit_code == 2


def test_cli_suggest_depuis_une_tache_du_ledger(tmp_path: Path) -> None:
    from grimoire.missions.ledger import MissionLedger
    from grimoire.missions.schemas import TaskCheck

    ledger = MissionLedger(tmp_path / "_grimoire-runtime-output" / "ledger")
    mission = ledger.create_mission("M", origin="cli")
    task = ledger.create_task(mission.id, "Rotate the API key", acceptance=("les tests passent",),
                              checks=(TaskCheck(cmd="pytest"),), risk_profile=RiskProfile.SECURITY_CRITICAL)
    result = CliRunner().invoke(app, ["ceremony", "suggest", task.id, "-f", "src/a.py", "--impact", "0",
                                      "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["route"] == "guarded"


# ── Impact : importeurs directs ─────────────────────────────────────────────

def _paquet(root: Path) -> None:
    sources = {
        "src/pkg/__init__.py": "",
        "src/pkg/a.py": "X = 1\n",
        "src/pkg/b.py": "from pkg.a import X\n",
        "src/pkg/c.py": "from . import a\n",
        "src/pkg/sub/__init__.py": "",
        "src/pkg/sub/d.py": "from ..a import X\n",
        "src/pkg/e.py": "import pkg.a as m\n",
        "src/pkg/f.py": "import os\n",
        "src/pkg/g.py": "def (:\n",
        "src/.venv/h.py": "from pkg.a import X\n",
    }
    for rel, text in sources.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_import_fan_in_compte_les_importeurs_directs(tmp_path: Path) -> None:
    from grimoire.missions.ceremony import import_fan_in

    _paquet(tmp_path)
    assert import_fan_in(tmp_path, ["src/pkg/a.py"]) == 4
    assert import_fan_in(tmp_path, ["src/pkg/f.py"]) == 0
    assert import_fan_in(tmp_path, ["README.md"]) is None
    assert import_fan_in(tmp_path, ["../ailleurs.py"]) is None


def test_cli_measure_impact(tmp_path: Path) -> None:
    _paquet(tmp_path)
    result = CliRunner().invoke(app, ["ceremony", "suggest", "--title", "Fix a", "-a", "les tests passent",
                                      "--check", "pytest", "-f", "src/pkg/a.py", "--measure-impact",
                                      "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["inputs"]["impact_files"] == 4
