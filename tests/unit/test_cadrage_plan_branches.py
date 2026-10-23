"""Branches de refus et de bord de ``grimoire cadrage`` (plan, exigences, gate du ledger, CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import verify_standard_profile
from grimoire.core.cadrage import CADRAGE_DIR, scaffold
from grimoire.core.cadrage_requirements import parse_requirements
from grimoire.missions.cadrage_plan import apply_plan, build_plan
from grimoire.missions.decisions import NOTIFY_ENV
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage plan")

runner = CliRunner()

DOC = """## Fonctionnelles

- REQ-001 [Must] : connexion
  - `pytest tests/test_login.py`
- REQ-002 : doublon de titre
  - `pytest tests/a.py`
- REQ-003 : Doublon de   titre
  - `pytest tests/b.py`
- REQ-004 : vague
  - le code est propre
- REQ-005 : sans critère

## Hors périmètre

- REQ-006 : exclue

## Priorisation

| ID | Priorité |
|----|----------|
| REQ-005 | Should |
| REQ-007 | Won't |
"""


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX", NOTIFY_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def _project(root: Path, text: str = DOC) -> Path:
    scaffold(root, project_name="demo")
    (root / CADRAGE_DIR / "04-exigences.md").write_text(text, encoding="utf-8", newline="\n")
    return root


def test_parser_edge_cases_tables_sections_and_conflicts() -> None:
    reqs = parse_requirements({
        "04-exigences.md": (
            "## Fonctionnelles\n\n"
            "| ID | Exigence | Priorité | Critère d'acceptation |\n|---|---|---|---|\n"
            "| **REQ-001** | export | Must | `pytest tests/x.py` ; la revue passe |\n"
            "| REQ-002 | | | |\n"
            "| sans id | ligne ignorée | | |\n\n"
            "## Hors périmètre\n\n| ID | Exigence |\n|---|---|\n| REQ-003 | exclue |\n\n"
            "## Priorisation\n\n| ID | Exigence | Priorité |\n|---|---|---|\n| REQ-001 | export | Should |\n"
            "| REQ-004 | x | |\n\n"
            "## Critères d'acceptation\n\n| ID | Critère |\n|---|---|\n| REQ-002 | `pytest tests/y.py` |\n"
            "- REQ-002 : `pytest tests/z.py`\n"
            "- REQ-004 : Must\n"
            "- REQ-001 : rien à voir\n"
        ),
    }).by_id()
    assert reqs["REQ-001"].priority in ("must", "should")
    assert reqs["REQ-003"].out_of_scope and not reqs["REQ-003"].planned
    assert reqs["REQ-003"].to_dict()["out_of_scope"] is True
    assert reqs["REQ-002"].criteria
    dup = parse_requirements({"04-exigences.md": "- REQ-001 : a\n- REQ-001 : b\n"}).by_id()["REQ-001"]
    assert dup.to_dict()["conflicts"] == ["a", "b"]
    # critère inline après un séparateur, priorité entre crochets, titre vide, placeholder ignoré
    inline = parse_requirements({"04-exigences.md": "- REQ-009 [Could] : titre : `pytest t.py`\n- REQ-010\n"})
    assert inline.by_id()["REQ-009"].priority == "could" and inline.by_id()["REQ-010"].title == ""


def test_plan_flags_conflicts_duplicate_titles_and_excluded_requirements(tmp_path: Path) -> None:
    _project(tmp_path)
    service = TaskService(tmp_path)
    plan = build_plan(tmp_path, service)
    kinds = {a.kind for q in plan.questions for a in [q.ambiguity]}
    assert {"sans-critere", "critere-vague"} <= kinds
    assert any(k not in ("sans-critere", "critere-vague") for k in kinds)
    data = plan.to_dict()
    statuses = {row["id"]: row["status"] for row in data["requirements"]}
    assert statuses["REQ-006"] == "out_of_scope" and statuses["REQ-007"] == "out_of_scope"
    # écrit puis relit : questions ouvertes, réponses d'état, ancre présente
    applied = apply_plan(service, plan, "cli")
    assert applied.applied
    again = build_plan(tmp_path, service).to_dict()
    assert any(q["status"] == "open" and q.get("answer_with") for q in again["questions"])
    assert any(row["status"] == "covered" for row in again["requirements"])


def test_a_conflicting_id_is_reported_as_conflict(tmp_path: Path) -> None:
    _project(tmp_path, "- REQ-001 : export\n  - `pytest a.py`\n- REQ-001 : import\n")
    data = build_plan(tmp_path, TaskService(tmp_path)).to_dict()
    assert data["requirements"][0]["status"] == "conflict" and data["conflicts"] == ["REQ-001"]


def test_task_on_unknown_requirement_is_orphan_and_other_open_task_is_unlinked(tmp_path: Path) -> None:
    _project(tmp_path, "- REQ-001 : a\n  - `pytest a.py`\n")
    service = TaskService(tmp_path)
    plan = apply_plan(service, build_plan(tmp_path, service), "cli")
    assert plan.applied
    service.add("Hors cadrage", ("`pytest z.py`",))
    cov = build_plan(tmp_path, service).to_dict()["coverage"]
    assert len(cov["unlinked"]) == 1


def test_cli_without_cadrage_fails_and_status_reports_it(tmp_path: Path) -> None:
    res = runner.invoke(app, ["cadrage", "plan", str(tmp_path)])
    assert res.exit_code == 1 and "pas de cadrage" in res.output
    res = runner.invoke(app, ["cadrage", "status", str(tmp_path)])
    assert res.exit_code == 1 and "non initialisé" in res.output


def test_cli_init_status_check_cycle(tmp_path: Path) -> None:
    first = runner.invoke(app, ["cadrage", "init", str(tmp_path), "--name", "demo"])
    assert first.exit_code == 0 and "initialisé" in first.output
    again = runner.invoke(app, ["cadrage", "init", str(tmp_path)])
    assert "déjà initialisé" in again.output
    assert runner.invoke(app, ["cadrage", "init", str(tmp_path), "--force"]).exit_code == 0
    status = runner.invoke(app, ["cadrage", "status", str(tmp_path)])
    assert status.exit_code == 0 and "Cadrage" in status.output
    check = runner.invoke(app, ["cadrage", "check", str(tmp_path)])
    assert check.exit_code == 1 and "incomplet" in check.output


def test_cli_prose_cadrage_says_it_sees_nothing(tmp_path: Path) -> None:
    _project(tmp_path, "Du texte libre sans identifiant.\n")
    res = runner.invoke(app, ["cadrage", "plan", str(tmp_path)])
    assert res.exit_code == 1 and "aucune exigence identifiée" in res.output


def test_cli_dry_run_text_preview_then_yes_text_then_replay(tmp_path: Path) -> None:
    _project(tmp_path)
    dry = runner.invoke(app, ["cadrage", "plan", str(tmp_path), "--dry-run"])
    assert dry.exit_code in (0, 1) and "Aperçu : rien n'est écrit" in dry.output
    assert "bloquante" in dry.output and "REQ-006" in dry.output
    assert not (tmp_path / "_grimoire" / "tasks").exists() or not TaskService(tmp_path).list_tasks()
    yes = runner.invoke(app, ["cadrage", "plan", str(tmp_path), "--yes"])
    assert "écrit" in yes.output and "répondez dans le cockpit" in yes.output
    replay = runner.invoke(app, ["cadrage", "plan", str(tmp_path)])
    assert "posée sur" in replay.output


def test_cli_json_dry_run_does_not_write_and_exit_code_matches(tmp_path: Path) -> None:
    _project(tmp_path)
    res = runner.invoke(app, ["cadrage", "plan", str(tmp_path), "--dry-run", "-o", "json"])
    data = json.loads(res.output)
    assert res.exit_code == data["exit_code"] and not data["applied"]
    assert data["missing"] and not TaskService(tmp_path).list_tasks()
    # sans --yes ni terminal interactif : rien n'est écrit non plus
    res = runner.invoke(app, ["cadrage", "plan", str(tmp_path), "-o", "json"])
    assert not json.loads(res.output)["applied"] and not TaskService(tmp_path).list_tasks()


def test_cli_interactive_confirmation_declined_and_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, "- REQ-001 : a\n  - `pytest a.py`\n")
    from typer.testing import _NamedTextIOWrapper  # type: ignore[attr-defined]

    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    no = runner.invoke(app, ["cadrage", "plan", str(tmp_path)], input="n\n")
    assert "rien n'est écrit" in no.output and "Créer 1 tâche" in no.output and not TaskService(tmp_path).list_tasks()
    ok = runner.invoke(app, ["cadrage", "plan", str(tmp_path)], input="y\n")
    assert "écrit" in ok.output and TaskService(tmp_path).list_tasks()


def test_cli_global_output_json_is_honoured(tmp_path: Path) -> None:
    _project(tmp_path, "- REQ-001 : a\n  - `pytest a.py`\n")
    res = runner.invoke(app, ["--output", "json", "cadrage", "plan", str(tmp_path), "--dry-run"])
    assert json.loads(res.output)["requirements"][0]["id"] == "REQ-001"


def _checks(root: Path, check_id: str, profile: str) -> list:
    return [c for c in verify_standard_profile(root, profile_id=profile).checks if c.id == check_id]


def test_ledger_check_silent_without_cadrage_or_requirements(tmp_path: Path) -> None:
    assert _checks(tmp_path, "cadrage.question_open", "starter") == []
    _project(tmp_path, "texte libre\n")
    assert _checks(tmp_path, "cadrage.requirement_uncovered", "governed") == []


def test_ledger_check_not_yet_asked_blocking_question_does_not_fail(tmp_path: Path) -> None:
    _project(tmp_path)
    assert _checks(tmp_path, "cadrage.question_open", "starter") == []
    assert _checks(tmp_path, "cadrage.requirement_uncovered", "starter")


def test_ledger_check_orphans_and_unanswered_question(tmp_path: Path) -> None:
    _project(tmp_path, "- REQ-001 : a\n  - `pytest a.py`\n- REQ-002 : b\n")
    service = TaskService(tmp_path)
    apply_plan(service, build_plan(tmp_path, service), "cli")
    (tmp_path / CADRAGE_DIR / "04-exigences.md").write_text("- REQ-002 : b\n", encoding="utf-8", newline="\n")
    orphans = _checks(tmp_path, "cadrage.task_orphan", "starter")
    assert orphans and "REQ-001" in orphans[0].message

