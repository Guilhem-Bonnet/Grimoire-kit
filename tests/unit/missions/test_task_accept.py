"""``grimoire task accept`` — le verdict humain d'une tâche à preuve déclarative (issue #865).

Constat du 2026-10-23 (Forge, kit 3.64.0) : cinq tâches en revue, pack déclaratif rangé,
critères remplis, et aucune voie pour les fermer — ``review_gate`` refuse le verdict
``skipped`` et aucun geste ne produisait le verdict humain qu'il réclame.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli import cmd_task
from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.evidence.schemas import VerdictResult
from grimoire.evidence.service import EvidenceService
from grimoire.missions.human_acceptance import accept_task
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService

ACC = ("la PR est fusionnée", "la CI est verte sous Windows")
STANDARD = Path("_grimoire/standard")
EVIDENCE = Path("_grimoire-runtime-output/evidence")
runner = CliRunner()


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / STANDARD / "llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _in_review(root: Path) -> str:
    """Une tâche sans commande, pack déclaratif rangé, passée en revue — l'état des cinq tâches de la Forge."""
    service = TaskService(root)
    tid = service.add("Correctif fusionné", ACC, owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude")
    service.transition(tid, TaskState.RUNNING, "claude")
    (root / "preuve.md").write_text("# PR fusionnée, CI verte\n", encoding="utf-8")
    trace = root / "_grimoire-output" / "decisions" / tid / "decision-trace.yaml"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("decisions: []\n", encoding="utf-8")
    result = runner.invoke(app, ["task", "verify", tid, "--project-root", str(root), "--evidence", "preuve.md"])
    assert result.exit_code == 4, result.output
    service.transition(tid, TaskState.NEEDS_VERIFICATION, "claude")
    return tid


def test_without_acceptance_the_declarative_task_cannot_close(governed: Path) -> None:
    """Le défaut : rien d'autre que le verdict humain ne lève ce refus."""
    tid = _in_review(governed)
    with pytest.raises(GrimoireMissionError, match="skipped"):
        TaskService(governed).transition(tid, TaskState.CLOSED, "guilhem")


def test_human_acceptance_records_a_passed_verdict_and_lets_the_task_close(governed: Path) -> None:
    tid = _in_review(governed)
    service = TaskService(governed)
    outcome = accept_task(
        service.require(tid), governed, governed / EVIDENCE, "PR #763 relue, CI Windows verte", "guilhem", origin=ORIGIN_HUMAN
    )
    assert outcome.verdict.verdict is VerdictResult.PASSED
    assert outcome.verdict.created_by == "human:guilhem"
    assert outcome.pack.coverage is not None and not outcome.pack.coverage.acceptance_missing
    # Le pack d'attestation garde la preuve relue et y ajoute l'acceptation, distincte d'un vert mécanique.
    assert [item.uri for item in outcome.pack.items] == ["file:preuve.md", "human:guilhem"]
    assert "PR #763 relue" in outcome.pack.items[-1].summary
    assert outcome.receipt.verdict == "READY" and outcome.receipt.reviewer_id == "human:guilhem"
    assert service.transition(tid, TaskState.CLOSED, "guilhem").task.status is TaskState.CLOSED


@pytest.mark.parametrize("origin", [ORIGIN_AGENT, "unverified"])
def test_an_agent_cannot_accept_its_own_work(governed: Path, origin: str) -> None:
    tid = _in_review(governed)
    service = TaskService(governed)
    evidence = EvidenceService(governed / EVIDENCE)
    before = len(evidence.list_verdicts(tid))
    with pytest.raises(GrimoireMissionError, match="canal humain"):
        accept_task(service.require(tid), governed, governed / EVIDENCE, "c'est bon", "claude", origin=origin)
    assert len(evidence.list_verdicts(tid)) == before


def test_acceptance_needs_a_task_in_review_with_a_pack(governed: Path) -> None:
    service = TaskService(governed)
    tid = service.add("Pas encore en revue", ACC, owner="guilhem", actor="claude", ready=True).task.id
    with pytest.raises(GrimoireMissionError, match="en revue"):
        accept_task(service.require(tid), governed, governed / EVIDENCE, "relu", "guilhem", origin=ORIGIN_HUMAN)


def test_acceptance_needs_a_reason(governed: Path) -> None:
    tid = _in_review(governed)
    with pytest.raises(ValueError, match="--reason"):
        accept_task(TaskService(governed).require(tid), governed, governed / EVIDENCE, "  ", "guilhem", origin=ORIGIN_HUMAN)


def test_cli_accept_from_the_human_channel_closes_the_task(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _in_review(governed)
    monkeypatch.setattr(cmd_task, "_comment_origin", lambda: ORIGIN_HUMAN)
    result = runner.invoke(
        app, ["task", "accept", tid, "--reason", "relu", "--project-root", str(governed), "--actor", "guilhem"]
    )
    assert result.exit_code == 0, result.output
    assert TaskService(governed).require(tid).status is TaskState.CLOSED


def test_cli_accept_from_an_agent_session_is_refused(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tid = _in_review(governed)
    monkeypatch.setattr(cmd_task, "_comment_origin", lambda: ORIGIN_AGENT)
    result = runner.invoke(app, ["task", "accept", tid, "--reason", "relu", "--project-root", str(governed)])
    assert result.exit_code == 1
    assert "canal humain" in result.output
    assert TaskService(governed).require(tid).status is TaskState.NEEDS_VERIFICATION
