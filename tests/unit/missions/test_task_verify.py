"""V0 réservé aux commandes déclarées, et ``task verify`` producteur de preuve (issue #772)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.evidence.schemas import VerdictResult
from grimoire.evidence.service import EvidenceService
from grimoire.missions.dispatch_advice import dispatch_advice
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import MissionTask, TaskCheck
from grimoire.missions.verifiability import Verifiability, as_dict, classify
from grimoire.missions.verify import NoDeclaredChecksError, verify_task

runner = CliRunner()
LEDGER = Path("_grimoire-runtime-output/ledger")
EVIDENCE = Path("_grimoire-runtime-output/evidence")


def _task(root: Path, acceptance: tuple[str, ...], checks: tuple[TaskCheck, ...] = ()) -> MissionTask:
    ledger = MissionLedger(root / LEDGER)
    mission = ledger.create_mission("m", origin="user")
    return ledger.create_task(mission.id, "t", acceptance=acceptance, checks=checks)


def _py(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


# ── #708 : V0 exige une commande déclarée ────────────────────────────────────


def test_tests_verts_sans_commande_declaree_n_est_pas_v0(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",))
    assert classify(task) is Verifiability.V1
    advice = dispatch_advice(task)
    assert advice["recommended_model"] == "sonnet"
    data = as_dict(task)
    assert data["raised_by"] == "no_declared_command"
    assert "checks" in data["reason"]


def test_meme_critere_avec_commande_declaree_reste_v0(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",), (TaskCheck(cmd="true"),))
    assert classify(task) is Verifiability.V0
    assert dispatch_advice(task)["recommended_model"] == "haiku"
    assert "raised_by" not in as_dict(task)


def test_sure_tests_ne_donne_jamais_haiku(tmp_path: Path) -> None:
    task = _task(tmp_path, ("sûre (tests)",))
    assert classify(task) is not Verifiability.V0
    assert dispatch_advice(task)["recommended_model"] != "haiku"


def test_checks_survivent_au_rejeu_du_ledger(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",), (TaskCheck(cmd="pytest -q", expect_exit=1),))
    replayed = MissionLedger(tmp_path / LEDGER).get_task(task.id)
    assert replayed is not None
    assert replayed.checks == (TaskCheck(cmd="pytest -q", expect_exit=1),)


@pytest.mark.parametrize("bad", [{"cmd": ""}, {"cmd": "x", "expect_exit": True}, {"cmd": "x", "expect_exit": "0"}])
def test_check_invalide_est_refuse(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TaskCheck.from_dict(bad)


# ── task verify : pack et verdict ────────────────────────────────────────────


def test_verify_vert_ecrit_pack_et_verdict(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("pass")),))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert outcome.passed
    service = EvidenceService(tmp_path / EVIDENCE)
    assert service.get_pack(outcome.pack.id) is not None
    latest = service.get_latest_verdict(task.id)
    assert latest is not None and latest.verdict is VerdictResult.PASSED
    assert latest.evidence_pack_id == outcome.pack.id


def test_verify_code_de_sortie_inattendu_rend_failed(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("raise SystemExit(3)")),))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed
    assert outcome.verdict.decision.reopen_task is True
    assert any("code de sortie 3, attendu 0" in c.reason for c in outcome.verdict.checks)


def test_verify_respecte_expect_exit(tmp_path: Path) -> None:
    task = _task(tmp_path, ("échoue comme prévu",), (TaskCheck(cmd=_py("raise SystemExit(3)"), expect_exit=3),))
    assert verify_task(task, tmp_path, tmp_path / EVIDENCE).passed


def test_verify_commande_inexecutable_est_failed_jamais_passed(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd="commande-qui-n-existe-pas-772"),))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed


def test_verify_une_commande_rouge_suffit_a_echouer(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("pass")), TaskCheck(cmd=_py("raise SystemExit(1)"))))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed
    assert len(outcome.pack.items) == 2


def test_verify_sans_commande_declaree_est_refuse_et_n_ecrit_rien(tmp_path: Path) -> None:
    task = _task(tmp_path, ("les tests passent",))
    with pytest.raises(NoDeclaredChecksError):
        verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not (tmp_path / EVIDENCE / "verdicts.jsonl").exists()


def test_cli_task_verify_codes_de_sortie(tmp_path: Path) -> None:
    ok = _task(tmp_path, ("ça passe",), (TaskCheck(cmd=_py("pass")),))
    res = runner.invoke(app, ["--output", "json", "task", "verify", ok.id, "--project-root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["verdict"]["verdict"] == "passed"
    assert payload["pack"]["id"] == payload["verdict"]["evidence_pack_id"]

    ko = MissionLedger(tmp_path / LEDGER).create_task(
        ok.mission_id, "rouge", acceptance=("x",), checks=(TaskCheck(cmd=_py("raise SystemExit(1)")),)
    )
    assert runner.invoke(app, ["task", "verify", ko.id, "--project-root", str(tmp_path)]).exit_code == 1

    nu = MissionLedger(tmp_path / LEDGER).create_task(ok.mission_id, "nu", acceptance=("les tests passent",))
    assert runner.invoke(app, ["task", "verify", nu.id, "--project-root", str(tmp_path)]).exit_code == 2


def test_cli_task_add_check_declare_la_commande(tmp_path: Path) -> None:
    res = runner.invoke(
        app,
        ["--output", "json", "task", "add", "t", "-a", "les tests passent", "--check", "true", "--project-root", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    task_id = json.loads(res.output)["id"]
    task = MissionLedger(tmp_path / LEDGER).get_task(task_id)
    assert task is not None and task.checks == (TaskCheck(cmd="true"),)
    assert classify(task) is Verifiability.V0


# ── revue K7 : le verdict ne ferme pas ce que les commandes ne couvrent pas ──

_GATES_TEMPLATE = Path(__file__).resolve().parents[3] / "framework/agentic-standard/templates/evidence-gates.yaml"


def _gated_root(tmp_path: Path) -> Path:
    standard = tmp_path / "_grimoire/standard"
    standard.mkdir(parents=True)
    (standard / "evidence-gates.yaml").write_text(_GATES_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_verify_critere_humain_reste_non_couvert_et_evidence_gate_refuse(tmp_path: Path) -> None:
    """K7-S1-01 : `true` ne couvre pas « relue par un humain » ; la gate evidence doit le dire."""
    from grimoire.missions.gates import check_transition

    root = _gated_root(tmp_path)
    human = "la documentation utilisateur est relue par un humain"
    task = _task(root, (human, "aucune régression de sécurité"), (TaskCheck(cmd="true"),))
    outcome = verify_task(task, root, root / EVIDENCE)
    assert outcome.passed
    assert outcome.pack.coverage is not None
    assert human in outcome.pack.coverage.acceptance_missing
    assert outcome.uncovered == outcome.pack.coverage.acceptance_missing
    refusals = check_transition(root, task, "review", "accepted").refusals
    assert any(r.evidence == "evidence_gate" and human in r.reason for r in refusals), [str(r) for r in refusals]


def test_verify_tache_v0_les_commandes_valent_couverture(tmp_path: Path) -> None:
    """Pour une V0, les commandes déclarées SONT le verdict : aucun critère n'est laissé en suspens."""
    from grimoire.missions.gates import check_transition

    root = _gated_root(tmp_path)
    task = _task(root, ("les tests passent",), (TaskCheck(cmd="true"),))
    assert classify(task) is Verifiability.V0
    outcome = verify_task(task, root, root / EVIDENCE)
    assert outcome.passed and outcome.uncovered == ()
    refusals = check_transition(root, task, "review", "accepted").refusals
    assert not any(r.evidence == "evidence_gate" for r in refusals), [str(r) for r in refusals]


def test_cli_task_verify_texte_nomme_les_criteres_non_couverts(tmp_path: Path) -> None:
    ok = _task(tmp_path, ("relue par un humain",), (TaskCheck(cmd=_py("pass")),))
    res = runner.invoke(app, ["task", "verify", ok.id, "--project-root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "non couvert" in res.output and "relue par un humain" in res.output


# ── revue K7 : l'aiguille de stdout et le délai déclarés ne se perdent plus ──


def _stdout_check(needle: str | None = "ALL OK", timeout_s: float | None = None) -> TaskCheck:
    return TaskCheck(
        cmd=_py("print('3 FAILED')"), expect_stdout_contains=needle, timeout_s=timeout_s
    )


def test_verify_aiguille_absente_de_stdout_rend_failed(tmp_path: Path) -> None:
    """K7-S2-02 : exit 0 mais « 3 FAILED » sans « ALL OK » ne peut pas être `passed`."""
    task = _task(tmp_path, ("ça passe",), (_stdout_check(),))
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed
    assert any("ALL OK" in c.reason for c in outcome.verdict.checks), [c.reason for c in outcome.verdict.checks]


def test_verify_aiguille_presente_rend_passed(tmp_path: Path) -> None:
    task = _task(tmp_path, ("ça passe",), (_stdout_check("FAILED"),))
    assert verify_task(task, tmp_path, tmp_path / EVIDENCE).passed


def test_verify_delai_declare_est_applique(tmp_path: Path) -> None:
    task = _task(
        tmp_path,
        ("ça passe",),
        (TaskCheck(cmd=_py("import time; time.sleep(10)"), timeout_s=0.3),),
    )
    outcome = verify_task(task, tmp_path, tmp_path / EVIDENCE)
    assert not outcome.passed
    assert outcome.results[0].exit_code is None
    assert any("délai" in c.reason for c in outcome.verdict.checks)


def test_taskcheck_aller_retour_et_retrocompatibilite() -> None:
    plain = TaskCheck(cmd="true", expect_exit=2)
    assert plain.to_dict() == {"cmd": "true", "expect_exit": 2}  # ledger existant : forme inchangée
    assert TaskCheck.from_dict({"cmd": "true", "expect_exit": 2}) == plain
    rich = TaskCheck(cmd="./c.sh", expect_stdout_contains="ALL OK", timeout_s=30.0)
    assert TaskCheck.from_dict(rich.to_dict()) == rich


@pytest.mark.parametrize(
    "bad",
    [
        {"cmd": "x", "expect_stdout_contains": 3},
        {"cmd": "x", "timeout_s": "10"},
        {"cmd": "x", "timeout_s": 0},
        {"cmd": "x", "timeout_s": -1},
        {"cmd": "x", "timeout_s": True},
    ],
)
def test_taskcheck_champs_optionnels_invalides_refuses(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TaskCheck.from_dict(bad)


def test_checks_avec_aiguille_survivent_au_rejeu_du_ledger(tmp_path: Path) -> None:
    check = TaskCheck(cmd="./c.sh", expect_stdout_contains="ALL OK", timeout_s=5.0)
    task = _task(tmp_path, ("x",), (check,))
    replayed = MissionLedger(tmp_path / LEDGER).get_task(task.id)
    assert replayed is not None and replayed.checks == (check,)


def test_acceptance_run_de_flow_garde_aiguille_et_delai_dans_la_tache(tmp_path: Path) -> None:
    """K7-S2-02 de bout en bout : `_find_or_create_task` ne jette plus `_stdout`/`_timeouts`."""
    from grimoire.flows.dispatch_executor import DispatchExecutor
    from grimoire.flows.schemas import AcceptanceRun, NodeContract

    script = tmp_path / "check.sh"
    script.write_text("#!/bin/sh\necho '3 FAILED'\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    contract = NodeContract(
        node_id="n1", kind="task", label="n1", description="d", inputs=(), outputs=(), tool_boundary=(),
        acceptance=("./check.sh",),
        acceptance_runs=(
            AcceptanceRun(argv=("./check.sh",), raw="./check.sh", expect_stdout_contains="ALL OK", timeout_s=30.0),
        ),
    )
    executor = DispatchExecutor(project_root=tmp_path, blueprint_path=tmp_path / "bp.yaml")
    task = executor._find_or_create_task(
        mission_id="MIS-x", task_id="GAO-x-001", contract=contract, result_path=tmp_path / "r.json"
    )
    assert task.checks[0].expect_stdout_contains == "ALL OK"
    assert task.checks[0].timeout_s == 30.0
    assert not verify_task(task, tmp_path, tmp_path / EVIDENCE).passed
