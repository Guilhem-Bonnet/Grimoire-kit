"""Exigence validée par la MOA, tâche rattachée, gate ready (#1342, lot M3) : `pytest -k moa_requirement`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.config import GovernanceConfig, GrimoireConfig
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import mandate as md
from grimoire.missions import requirement_gate as rg
from grimoire.missions.cadrage_plan import covers_of
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, TaskState
from grimoire.missions.service import TaskRefusedError, TaskService

runner = CliRunner()
REQS = "# Exigences\n\n- REQ-001 [Must] : export CSV\n  - `pytest tests/test_export.py`\n"


def _project(root: Path, governance: dict | None = ..., reqs: str = REQS) -> Path:  # type: ignore[assignment]
    data: dict = {"project": {"name": "demo"}}
    if governance is not ...:
        data["governance"] = governance
    (root / "project-context.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    cadrage = root / "_grimoire" / "cadrage"
    cadrage.mkdir(parents=True, exist_ok=True)
    (cadrage / "04-exigences.md").write_text(reqs, encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un journal d'autorité neuf par test : la racine de l'utilisateur est celle du test."""
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        md.PRINCIPAL_ENV,
        md.PROVENANCE_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def blocking(tmp_path: Path) -> Path:
    return _project(tmp_path, {"roles": {"moa": "Ada Lovelace"}})


def _validate(root: Path, key: str = "REQ-001") -> dict:
    return rg.validate_requirement(root, key, origin=ORIGIN_HUMAN, actor="ada")


def _add(root: Path, **kw: object) -> str:
    return TaskService(root).add("Exporter", ("le CSV est produit",), owner="amelia", **kw).task.id  # type: ignore[arg-type]


# ── la validation : seulement le canal humain ───────────────────────────────


@pytest.mark.parametrize("origin", [ORIGIN_AGENT, ORIGIN_ORCHESTRATOR, "unverified", "flow", ""])
def test_moa_requirement_refuses_every_non_human_origin(blocking: Path, origin: str) -> None:
    with pytest.raises(rg.RequirementRefusedError) as exc:
        rg.validate_requirement(blocking, "REQ-001", origin=origin, actor="x")
    assert exc.value.code == "human_required"
    assert rg.validation_of(blocking, "REQ-001")[0] is None


def test_moa_requirement_human_validation_is_sealed_in_the_authority_journal(blocking: Path) -> None:
    entry = _validate(blocking)
    assert (entry["type"], entry["requirement"], entry["moa"]) == (rg.VALIDATED_ENTRY, "REQ-001", "Ada Lovelace")
    assert md.AuthorityJournal().read()[-1]["hash"] == entry["hash"]
    found, _ = rg.validation_of(blocking, "REQ-001")
    assert found is not None


def test_moa_requirement_a_forged_ledger_event_does_not_count(blocking: Path) -> None:
    service = TaskService(blocking)
    tid = _add(blocking, requirement="REQ-001")
    for kind in ("requirement.validated", "task.requirement_validated", rg.VALIDATED_ENTRY):
        service.ledger.append_event(kind, tid, "task", "worker", {"requirement": "REQ-001", "origin": "human"})
    with pytest.raises(TaskRefusedError):
        service.transition(tid, TaskState.READY, "worker")


def test_moa_requirement_edited_requirement_loses_its_validation(blocking: Path) -> None:
    _validate(blocking)
    path = blocking / "_grimoire" / "cadrage" / "04-exigences.md"
    path.write_text(REQS.replace("export CSV", "export CSV et XML"), encoding="utf-8")
    found, reason = rg.validation_of(blocking, "REQ-001")
    assert found is None
    assert "revalider" in reason


def test_moa_requirement_refuses_without_moa_unknown_or_conflicting(tmp_path: Path) -> None:
    _project(tmp_path, ...)
    with pytest.raises(rg.RequirementRefusedError) as exc:
        _validate(tmp_path)
    assert exc.value.code == "no_moa"
    _project(tmp_path, {"roles": {"moa": "Ada"}})
    with pytest.raises(rg.RequirementRefusedError) as exc:
        _validate(tmp_path, "REQ-042")
    assert exc.value.code == "unknown_requirement"
    _project(tmp_path, {"roles": {"moa": "Ada"}}, "- REQ-001 : un\n- REQ-001 : deux\n")
    with pytest.raises(rg.RequirementRefusedError) as exc:
        _validate(tmp_path)
    assert exc.value.code == "conflict"


def test_moa_requirement_unreadable_journal_validates_nothing(blocking: Path) -> None:
    _validate(blocking)
    journal = md.AuthorityJournal()
    journal.path.write_text(journal.path.read_text().replace("Ada Lovelace", "Mallory"), encoding="utf-8")
    found, reason = rg.validation_of(blocking, "REQ-001")
    assert found is None
    assert "journal" in reason


def test_moa_requirement_cli_refuses_an_agent_session_then_accepts_a_human_turn(
    blocking: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = runner.invoke(app, ["cadrage", "approve", "REQ-001", str(blocking)])  # pas un terminal : origine agent
    assert refused.exit_code == 1
    assert "Seul l'humain MOA" in refused.output
    monkeypatch.setenv(md.PRINCIPAL_ENV, "orchestrator")
    monkeypatch.setenv(md.PROVENANCE_ENV, "rule:regle-test")
    refused = runner.invoke(app, ["cadrage", "approve", "REQ-001", str(blocking)])
    assert refused.exit_code == 1
    assert rg.validation_of(blocking, "REQ-001")[0] is None
    monkeypatch.delenv(md.PRINCIPAL_ENV)
    monkeypatch.delenv(md.PROVENANCE_ENV)
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: ORIGIN_HUMAN)
    ok = runner.invoke(app, ["cadrage", "approve", "REQ-001", str(blocking)])
    assert ok.exit_code == 0, ok.output
    assert rg.validation_of(blocking, "REQ-001")[0] is not None


# ── la tâche porte son exigence ──────────────────────────────────────────────


def test_moa_requirement_task_add_carries_the_reference_and_normalizes_it(blocking: Path) -> None:
    tid = _add(blocking, requirement="req-001")
    task = TaskService(blocking).require(tid)
    assert (task.extra or {})["requirement"] == "REQ-001"
    with pytest.raises(GrimoireMissionError):
        _add(blocking, requirement="n'importe quoi")


def test_moa_requirement_cadrage_plan_hands_the_reference_to_its_tasks(blocking: Path) -> None:
    result = runner.invoke(app, ["cadrage", "plan", str(blocking), "--yes", "-o", "json"])
    assert result.exit_code in (0, 1), result.output
    tasks = [t for t in TaskService(blocking).list_tasks() if covers_of(t)]
    assert tasks
    assert all((t.extra or {}).get("requirement") == covers_of(t)[0] for t in tasks)


def test_moa_requirement_task_add_cli_option(blocking: Path) -> None:
    result = runner.invoke(
        app, ["task", "add", "Exporter", "-a", "le CSV", "--requirement", "REQ-001", "--project-root", str(blocking)]
    )
    assert result.exit_code == 0, result.output
    assert any((t.extra or {}).get("requirement") == "REQ-001" for t in TaskService(blocking).list_tasks())


# ── le gate proposed → ready ─────────────────────────────────────────────────


def test_moa_requirement_blocking_refuses_a_task_without_validated_requirement(blocking: Path) -> None:
    service = TaskService(blocking)
    for tid in (_add(blocking), _add(blocking, requirement="REQ-001")):
        with pytest.raises(TaskRefusedError) as exc:
            service.transition(tid, TaskState.READY, "worker")
        refusal = exc.value.verdict.refusals[0]
        assert refusal.evidence == "requirement_validated"
        assert "grimoire cadrage interview" in refusal.remedy
        assert "MOA" in refusal.remedy
        assert service.require(tid).status is TaskState.PROPOSED


def test_moa_requirement_blocking_lets_a_validated_requirement_through(blocking: Path) -> None:
    tid = _add(blocking, requirement="REQ-001")
    _validate(blocking)
    moved = TaskService(blocking).transition(tid, TaskState.READY, "worker")
    assert moved.task.status is TaskState.READY
    assert TaskService(blocking).add("Autre", ("ok",), requirement="REQ-001", ready=True).task.status is TaskState.READY


def test_moa_requirement_blocking_refusal_through_add_ready(blocking: Path) -> None:
    with pytest.raises(TaskRefusedError):
        TaskService(blocking).add("Exporter", ("ok",), owner="amelia", ready=True)


@pytest.mark.parametrize("nature", ["question", "incident", "Incident"])
def test_moa_requirement_question_and_incident_are_exempt(blocking: Path, nature: str) -> None:
    service = TaskService(blocking)
    tid = _add(blocking, nature=nature)
    assert service.transition(tid, TaskState.READY, "worker").task.status is TaskState.READY
    assert [e.event_type for e in service.ledger.list_events(tid)].count(rg.EXEMPTED_EVENT) == 1


def test_moa_requirement_other_natures_are_not_exempt(blocking: Path) -> None:
    with pytest.raises(TaskRefusedError):
        TaskService(blocking).transition(_add(blocking, nature="feature"), TaskState.READY, "worker")


def test_moa_requirement_flagged_mode_lets_through_and_records_an_event(tmp_path: Path) -> None:
    root = _project(tmp_path, {"roles": {"moa": "Ada"}, "requirement_gate": "flagged"})
    service = TaskService(root)
    tid = _add(root)
    assert service.transition(tid, TaskState.READY, "worker").task.status is TaskState.READY
    assert tid in rg.flagged_task_ids(service.ledger.list_events())


def test_moa_requirement_existing_project_without_governance_is_never_blocked(tmp_path: Path) -> None:
    root = _project(tmp_path, ...)
    assert GrimoireConfig.from_yaml(root / "project-context.yaml").governance.requirement_mode == "flagged"
    service = TaskService(root)
    tid = _add(root)
    assert service.transition(tid, TaskState.READY, "worker").task.status is TaskState.READY
    assert service.add("Direct", ("ok",), ready=True).task.status is TaskState.READY


def test_moa_requirement_project_without_context_file_is_never_blocked(tmp_path: Path) -> None:
    service = TaskService(tmp_path)
    assert service.add("Direct", ("ok",), ready=True).task.status is TaskState.READY


def test_moa_requirement_explicit_switch_overrides_the_default() -> None:
    assert GovernanceConfig(moa="Ada", declared=True).requirement_mode == "blocking"
    assert GovernanceConfig(moa="Ada", declared=True, requirement_gate="flagged").requirement_mode == "flagged"
    assert GovernanceConfig(requirement_gate="blocking").requirement_mode == "blocking"
    with pytest.raises(Exception, match="requirement_gate"):
        GrimoireConfig.from_dict({"project": {"name": "d"}, "governance": {"requirement_gate": "maybe"}})


def test_moa_requirement_worker_cannot_unblock_by_editing_its_worktree_config(blocking: Path, tmp_path: Path) -> None:
    """Le mode se lit à la racine d'origine : la copie que le worker écrit dans son worktree n'oppose rien."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "project-context.yaml").write_text("project: {name: demo}\n", encoding="utf-8")
    tid = _add(blocking)
    task = TaskService(blocking).require(tid)
    refusal, check, mode = rg.refusal_of(blocking, task)
    assert refusal is not None and mode == "blocking" and check.gap
    assert rg.refusal_of(worktree, task)[0] is None  # ce que lirait un gate naïf : la preuve que la racine compte


def test_moa_requirement_cockpit_lists_a_flagged_task_until_validated(tmp_path: Path) -> None:
    from grimoire.tools import workspace_attention as wa

    root = _project(tmp_path, {"roles": {"moa": "Ada"}, "requirement_gate": "flagged"})
    service = TaskService(root)
    tid = _add(root, requirement="REQ-001")
    service.transition(tid, TaskState.READY, "worker")

    def kinds() -> list[str]:
        view = wa.attention_view(root, service_probe=lambda *_: "online", watch=wa.ServiceWatch())
        return [
            i["kind"]
            for section in view["sections"]
            for i in section["items"]
            if tid in i.get("ref", {}).get("task_ids", [])
        ]

    assert kinds() == [wa.REQUIREMENT_UNVALIDATED]
    _validate(root)
    assert kinds() == []


def test_moa_requirement_json_refusal_names_the_remedy(blocking: Path) -> None:
    result = runner.invoke(
        app,
        [
            "-o",
            "json",
            "task",
            "add",
            "Exporter",
            "-a",
            "ok",
            "--owner",
            "amelia",
            "--ready",
            "--project-root",
            str(blocking),
        ],
    )
    assert result.exit_code != 0
    assert "interview" in result.output


@pytest.mark.parametrize("name", ["github-actions[bot]", "Dependabot[BOT]", "Ada\nLovelace"])
def test_moa_requirement_refuses_bot_and_multiline_moa(name: str) -> None:
    from grimoire.core.config import GovernanceConfig
    from grimoire.core.exceptions import GrimoireConfigError

    with pytest.raises(GrimoireConfigError, match="MOA"):
        GovernanceConfig.from_dict({"roles": {"moa": name}})


def test_moa_requirement_doctor_shows_malformed_governance(tmp_path: Path) -> None:
    from grimoire.cli.doctor_moa_moe import moa_moe_verdict

    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: x\ngovernance:\n  roles:\n    moa: 'bot[bot]'\n", encoding="utf-8"
    )
    verdict = moa_moe_verdict(tmp_path)
    assert verdict is not None
    assert verdict["level"] == "warn"
    assert "mal formée" in verdict["detail"]
