"""ADR-007 point 1 — `grimoire standard init` ouvre le Mission Ledger.

Avant ce correctif, `standard init` copiait tel quel le template YAML statique
`framework/agentic-standard/templates/task-board.yaml` sans jamais ouvrir de
`MissionLedger` : tout projet nouvellement enrôlé avait un board sans source
(issue #521, constat repris par ADR-007). Le premier test ci-dessous prouve
que l'init ouvre bien le ledger ; le second prouve que le board produit reste
équivalent pour `grimoire standard gate check`, comme l'exige l'invariant I4.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from grimoire.core.agentic_standard import (
    _render_template,
    check_evidence_gates,
    get_profile,
    setup_standard_profile,
)
from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH

_LEGACY_TEMPLATE = Path("framework/agentic-standard/templates/task-board.yaml")


def _read_board(path: Path) -> dict[str, object]:
    return YAML(typ="safe").load(path)  # type: ignore[no-any-return]


def test_init_opens_the_mission_ledger_for_the_bootstrap_task(tmp_path: Path) -> None:
    """Ce qu'ADR-007 corrige : l'init crée désormais une vraie tâche de ledger."""
    setup_standard_profile(tmp_path, profile_id="governed", project_name="demo")

    ledger = MissionLedger(tmp_path / DEFAULT_LEDGER_RELPATH)
    assert (tmp_path / DEFAULT_LEDGER_RELPATH / "events.jsonl").is_file()

    task = ledger.get_task("bootstrap")
    assert task is not None
    assert task.status is TaskState.PROPOSED
    assert task.owner == "project-maintainer"
    assert "Standard artifacts are generated and verified." in task.acceptance

    board = _read_board(tmp_path / STANDARD_DIR / "task-board.yaml")
    board_tasks = board["tasks"]
    assert isinstance(board_tasks, list)
    assert board_tasks[0]["task_id"] == "bootstrap"
    assert board_tasks[0]["status"] == "proposed"
    # La projection se déclare elle-même issue du ledger (ADR-005/007), plus du
    # template statique — signal vérifiable que le chemin a bien changé.
    assert board["metadata"]["source"] == "mission-ledger"


def test_init_is_idempotent_on_the_ledger(tmp_path: Path) -> None:
    """Rejouer `init` (refresh, comme `grimoire up` le fait) ne duplique rien."""
    setup_standard_profile(tmp_path, profile_id="governed", project_name="demo")
    setup_standard_profile(tmp_path, profile_id="governed", project_name="demo", refresh=True)

    ledger = MissionLedger(tmp_path / DEFAULT_LEDGER_RELPATH)
    bootstrap_tasks = [t for t in ledger.list_tasks() if t.id == "bootstrap"]
    assert len(bootstrap_tasks) == 1


def test_init_task_board_stays_equivalent_for_gate_check(tmp_path: Path) -> None:
    """Le board projeté depuis le ledger doit satisfaire `gate check` comme
    l'ancien template statique le faisait — même verdict, même état, mêmes
    manques (invariant I4 : les suites existantes restent vertes sans
    modification).
    """
    legacy_root = tmp_path / "legacy"
    legacy_board_path = legacy_root / STANDARD_DIR / "task-board.yaml"
    legacy_board_path.parent.mkdir(parents=True)
    rendered = _render_template(
        _LEGACY_TEMPLATE.read_text(encoding="utf-8"),
        project_name="legacy",
        profile=get_profile("governed"),
        generated_at="2026-01-01",
    )
    legacy_board_path.write_text(rendered, encoding="utf-8")
    legacy_result = check_evidence_gates(legacy_root, task_id="bootstrap")

    ledger_root = tmp_path / "ledger-project"
    setup_standard_profile(ledger_root, profile_id="governed", project_name="ledger-project")
    ledger_result = check_evidence_gates(ledger_root, task_id="bootstrap")

    assert ledger_result.ok == legacy_result.ok
    assert ledger_result.state == legacy_result.state
    assert ledger_result.missing == legacy_result.missing
    assert [c.id for c in ledger_result.checks] == [c.id for c in legacy_result.checks]
