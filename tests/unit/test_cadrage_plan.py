"""W6-07b (ADP-9, ADP-8 critère 2) — du cadrage aux tâches du ledger, et le gate des questions du cadrage.

Constat sur la base (origin/main, après W6-07 tranche 1) : les exigences du cadrage sont de la prose libre ;
rien ne les relie aux tâches du ledger (``grimoire cadrage plan`` est inconnue), et ``standard verify`` ne
sait rien d'une question bloquante du cadrage restée sans réponse.

Convention testée : ``REQ-NNN`` en tête de ligne (liste, titre) ou en colonne d'un tableau ; les sous-éléments
sont les critères. Un cadrage sans identifiant reste valide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import verify_standard_profile
from grimoire.core.cadrage import CADRAGE_DIR, scaffold
from grimoire.core.cadrage_requirements import parse_requirements, read_requirements
from grimoire.missions.cadrage_plan import ANCHOR, apply_plan, build_plan, covers_of, is_anchor
from grimoire.missions.decisions import NOTIFY_ENV
from grimoire.missions.questions import question_rows
from grimoire.missions.schemas import ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage plan")

runner = CliRunner()
S = TaskState

EXIGENCES = """---
phase: exigences
status: draft
---

# Exigences

## Fonctionnelles

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - `pytest tests/test_login.py`
- REQ-002 (Should) : export CSV des commandes
  - le code est propre
- REQ-003 : journal d'audit

## Non-fonctionnelles

| ID | Exigence | Priorité | Critère d'acceptation |
|----|----------|----------|------------------------|
| REQ-004 | Temps de réponse < 200 ms | Could | la revue de performance valide le rapport |

## Priorisation (MoSCoW)

- REQ-003 : Must

## Critères d'acceptation

Voir plus haut.
"""


@pytest.fixture(autouse=True)
def _no_host_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
        "AI_AGENT", "CLAUDECODE", "CURSOR_AGENT", "GEMINI_CLI", "CODEX_SANDBOX", NOTIFY_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def _without_req_003() -> str:
    return EXIGENCES.replace("- REQ-003 : journal d'audit\n", "").replace("- REQ-003 : Must\n", "")


def _project(root: Path, exigences: str = EXIGENCES, cdc: str | None = None) -> Path:
    scaffold(root, project_name="demo")
    (root / CADRAGE_DIR / "04-exigences.md").write_text(exigences, encoding="utf-8", newline="\n")
    if cdc is not None:
        (root / CADRAGE_DIR / "05-cahier-des-charges.md").write_text(cdc, encoding="utf-8", newline="\n")
    return root


def _apply(root: Path) -> Any:
    service = TaskService(root)
    return apply_plan(service, build_plan(root, service), "cli")


def _checks(root: Path, check_id: str, profile: str = "starter") -> list[Any]:
    return [c for c in verify_standard_profile(root, profile_id=profile).checks if c.id == check_id]


def _anchor(root: Path) -> Any:
    return next(t for t in TaskService(root).list_tasks() if is_anchor(t))


# ── la convention ────────────────────────────────────────────────────────────


def test_the_convention_reads_lists_headings_tables_and_sections() -> None:
    text = EXIGENCES + "\n## Autres\n\n### REQ-006 — import (Must)\n\n- une commande `ruff check src/` rend 0\n"
    text += "\n## Hors périmètre\n\n- REQ-009 : application mobile\n\n```\n- REQ-099 : exemple\n```\n"
    reqs = parse_requirements({"04-exigences.md": text}).by_id()
    assert reqs["REQ-001"].priority == "must" and reqs["REQ-001"].criteria == ["`pytest tests/test_login.py`"]
    assert reqs["REQ-003"].priority == "must" and reqs["REQ-003"].criteria == []
    assert reqs["REQ-004"].title == "Temps de réponse < 200 ms" and reqs["REQ-004"].priority == "could"
    assert reqs["REQ-004"].criteria == ["la revue de performance valide le rapport"]
    assert reqs["REQ-006"].title == "import" and reqs["REQ-006"].criteria
    assert reqs["REQ-009"].out_of_scope and not reqs["REQ-009"].planned
    assert "REQ-099" not in reqs  # un bloc de code n'est pas une exigence


def test_a_prose_cadrage_stays_valid_and_plan_says_it_sees_nothing(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="demo")
    assert not read_requirements(tmp_path).identified
    result = runner.invoke(app, ["cadrage", "plan", str(tmp_path)])
    assert result.exit_code == 1
    assert "aucune exigence identifiée" in result.output and "REQ-001" in result.output
    # Rien ne fait échouer le standard pour autant.
    assert _checks(tmp_path, "cadrage.question_open") == []
    assert _checks(tmp_path, "cadrage.requirement_uncovered") == []


def test_the_cahier_des_charges_wins_on_wording_and_a_same_file_redefinition_is_a_conflict() -> None:
    reqs = parse_requirements({
        "04-exigences.md": "## Fonctionnelles\n\n- REQ-001 : export\n  - `pytest tests/x.py`\n- REQ-001 : import\n",
        "05-cahier-des-charges.md": "## Exigences priorisées\n\n- REQ-002 : B, reformulée\n",
    }).by_id()
    assert reqs["REQ-001"].conflicts == ["export", "import"]
    reqs = parse_requirements({
        "04-exigences.md": "## F\n\n- REQ-002 : B\n  - `pytest tests/b.py`\n",
        "05-cahier-des-charges.md": "## Exigences priorisées\n\n- REQ-002 : B, reformulée\n",
    }).by_id()
    assert reqs["REQ-002"].title == "B, reformulée" and not reqs["REQ-002"].conflicts


# ── le plan ──────────────────────────────────────────────────────────────────


def test_dry_run_proposes_one_task_per_requirement_and_writes_nothing(tmp_path: Path) -> None:
    _project(tmp_path)
    result = runner.invoke(app, ["cadrage", "plan", str(tmp_path), "--dry-run", "-o", "json"])
    data = json.loads(result.output)
    assert result.exit_code == 1  # REQ-002 (critère vague) et REQ-003 (sans critère) : listées manquantes
    assert data["missing"] == ["REQ-002", "REQ-003"]
    tasks = {t["covers"][0]: t for t in data["tasks"]}
    assert set(tasks) == {"REQ-001", "REQ-002", "REQ-004"}
    assert tasks["REQ-001"]["verifiability"] == "V0" and tasks["REQ-001"]["checks"] == ["pytest tests/test_login.py"]
    assert tasks["REQ-002"]["verifiability"] == "V2"
    assert tasks["REQ-004"]["verifiability"] == "V1" and tasks["REQ-004"]["priority"] == "low"
    kinds = {q["key"].split(":")[2]: q["blocking"] for q in data["questions"]}
    assert kinds == {"critere-vague": False, "sans-critere": True}
    assert not (tmp_path / "_grimoire-runtime-output" / "ledger" / "events.jsonl").exists()


def test_apply_writes_through_the_ledger_and_is_idempotent(tmp_path: Path) -> None:
    _project(tmp_path)
    plan = _apply(tmp_path)
    tasks = TaskService(tmp_path).list_tasks()
    planned = [t for t in tasks if covers_of(t)]
    assert sorted(covers_of(t)[0] for t in planned) == ["REQ-001", "REQ-002", "REQ-004"]
    login = next(t for t in planned if covers_of(t) == ["REQ-001"])
    assert login.acceptance == ("`pytest tests/test_login.py`",) and login.priority == "high"
    assert [c.cmd for c in login.checks] == ["pytest tests/test_login.py"]
    # Une bloquante : l'ancre est garée, un seul lot, une seule interruption.
    anchor = _anchor(tmp_path)
    assert anchor.status is S.WAITING and anchor.extra["cadrage"] == ANCHOR
    assert len(question_rows(anchor)) == 2 and plan.anchor == anchor.id
    # Rejouer ne recrée ni tâche ni question.
    events = len(TaskService(tmp_path).ledger.list_events())
    again = _apply(tmp_path)
    assert again.proposals == [] and len(TaskService(tmp_path).ledger.list_events()) == events


# ── le gate (ADP-8 critère 2) ────────────────────────────────────────────────


def test_an_open_blocking_question_fails_verify_and_names_how_to_answer(tmp_path: Path) -> None:
    _project(tmp_path)
    _apply(tmp_path)
    anchor = _anchor(tmp_path)
    blocking = next(r for r in question_rows(anchor) if r["blocking"])
    [check] = _checks(tmp_path, "cadrage.question_open")
    assert check.severity == "error" and check.is_error
    assert "journal d'audit" in check.message and blocking["id"] in check.message
    assert f"grimoire task answer {anchor.id}" in check.message and "cockpit" in check.message


def test_a_non_blocking_question_never_fails_verify(tmp_path: Path) -> None:
    _project(tmp_path, _without_req_003())
    _apply(tmp_path)
    assert any(not r["blocking"] for r in question_rows(_anchor(tmp_path)))
    assert _anchor(tmp_path).status is not S.WAITING
    assert _checks(tmp_path, "cadrage.question_open") == []


def test_a_human_answer_clears_the_gate_and_becomes_the_criterion(tmp_path: Path) -> None:
    _project(tmp_path)
    _apply(tmp_path)
    service = TaskService(tmp_path)
    anchor = _anchor(tmp_path)
    blocking = next(r for r in question_rows(anchor) if r["blocking"])
    move = service.answer(anchor.id, "`pytest tests/test_audit.py`", "guilhem", origin=ORIGIN_HUMAN,
                          question_id=blocking["id"])
    assert move.task.status is S.READY
    assert _checks(tmp_path, "cadrage.question_open") == []
    plan = _apply(tmp_path)
    [audit] = [p for p in plan.proposals if p.requirement == "REQ-003"]
    assert audit.acceptance == ("`pytest tests/test_audit.py`",) and audit.verifiability == "V0"
    assert audit.criterion_origin.startswith("réponse humaine")
    assert "REQ-003" not in plan.missing


def test_an_agent_cannot_clear_the_gate(tmp_path: Path) -> None:
    from grimoire.core.exceptions import GrimoireMissionError

    _project(tmp_path)
    _apply(tmp_path)
    service = TaskService(tmp_path)
    anchor = _anchor(tmp_path)
    blocking = next(r for r in question_rows(anchor) if r["blocking"])
    with pytest.raises(GrimoireMissionError):
        service.answer(anchor.id, "deviné", "agent", origin="agent", question_id=blocking["id"])
    with pytest.raises(GrimoireMissionError):
        service.resolve_question(anchor.id, blocking["id"], "deviné", "intuition", "agent")
    assert len(_checks(tmp_path, "cadrage.question_open")) == 1


def test_fixing_the_cadrage_makes_the_question_moot_without_a_fabricated_answer(tmp_path: Path) -> None:
    _project(tmp_path)
    _apply(tmp_path)
    anchor_id = _anchor(tmp_path).id
    fixed = EXIGENCES.replace("- REQ-003 : journal d'audit\n", "- REQ-003 : journal d'audit\n  - `pytest tests/test_audit.py`\n")
    (tmp_path / CADRAGE_DIR / "04-exigences.md").write_text(fixed, encoding="utf-8", newline="\n")
    assert _checks(tmp_path, "cadrage.question_open") == []
    _apply(tmp_path)
    anchor = TaskService(tmp_path).require(anchor_id)
    assert anchor.status is S.CANCELLED
    assert not any(r.get("answer_origin") for r in question_rows(anchor) if r["blocking"])


# ── la couverture ────────────────────────────────────────────────────────────


def test_coverage_warns_and_is_an_error_only_for_an_accepted_cdc_in_governed(tmp_path: Path) -> None:
    _project(tmp_path, cdc="---\nphase: cahier-des-charges\nstatus: accepted\n---\n")
    TaskService(tmp_path).add("Ancienne tâche", ("`pytest`",), actor="t", extra={"covers": ["REQ-042"]})
    [uncovered] = _checks(tmp_path, "cadrage.requirement_uncovered")
    assert uncovered.severity == "warning" and "REQ-001" in uncovered.message
    [orphan] = _checks(tmp_path, "cadrage.task_orphan")
    assert orphan.severity == "warning" and "REQ-042" in orphan.message
    assert all(c.severity == "error" for c in _checks(tmp_path, "cadrage.requirement_uncovered", "governed"))
    draft = tmp_path / CADRAGE_DIR / "05-cahier-des-charges.md"
    draft.write_text("---\nstatus: draft\n---\n", encoding="utf-8", newline="\n")
    assert all(c.severity == "warning" for c in _checks(tmp_path, "cadrage.requirement_uncovered", "governed"))


def test_cli_yes_writes_and_check_reports_coverage(tmp_path: Path) -> None:
    _project(tmp_path, EXIGENCES.replace("  - le code est propre\n", "  - `pytest tests/test_export.py`\n")
             .replace("- REQ-003 : journal d'audit\n", "").replace("- REQ-003 : Must\n", ""))
    check = runner.invoke(app, ["cadrage", "plan", str(tmp_path), "--check"])
    assert check.exit_code == 1 and "sans tâche" in check.output
    written = runner.invoke(app, ["cadrage", "plan", str(tmp_path), "--yes", "-o", "json"])
    data = json.loads(written.output)
    assert written.exit_code == 0 and data["applied"] and data["coverage"]["uncovered"] == []
    assert runner.invoke(app, ["cadrage", "plan", str(tmp_path), "--check"]).exit_code == 0
