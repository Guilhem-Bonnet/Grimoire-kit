"""Le journal d'actions observées par les hooks, projeté dans le pack de preuve (issue #582 lot G2).

Avant ce lot, ``evidence-pack.md`` n'avait qu'une section « Evidence
inventory » éditée à la main, et un projet gouverné sans cette recopie
manuelle n'avait aucun autre moyen de prouver son inventaire à
``gate check``/``verify``. Le test
``test_governed_gate_refuses_empty_inventory_then_accepts_the_observed_log``
ci-dessous est le rouge-avant/vert-après cité dans la PR : avant ce lot, son
seul import (``grimoire.core.standard_checks.evidence_journal`` n'existe pas)
fait échouer la collecte ; après, la première évaluation reste rouge (aucune
preuve, ni manuelle ni observée) et la seconde, après un seul événement
observé, est verte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import check_evidence_gates, setup_standard_profile
from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_bash_event,
    build_file_write_event,
    has_observed_inventory,
    read_evidence_log,
    regenerate_observed_inventory_section,
)
from grimoire.core.standard_task_scaffold import scaffold_task_artifacts
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService


def _open_task(root: Path) -> str:
    service = TaskService(root)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(
        mission.id, "Ajouter la fonction somme", acceptance=("les tests passent",), owner="dev"
    )
    service.ledger.transition_task(task.id, TaskState.READY, actor_id="dev")
    service.ledger.claim_task(task.id, "dev", "local")
    service.project_board()
    return task.id


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed")
    return tmp_path


# ── Garde fermée : journal absent ou malformé ────────────────────────────────


def test_absent_journal_reads_as_nothing_observed(governed: Path) -> None:
    assert read_evidence_log(governed, "T-1") == []
    assert has_observed_inventory(governed, "T-1") is False


def test_malformed_lines_are_skipped_a_totally_corrupt_file_reads_as_nothing(governed: Path) -> None:
    log_path = governed / "_grimoire-output/evidence/T-1/evidence-log.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("ceci n'est pas du JSON\n{ encore cassé\n", encoding="utf-8")
    assert read_evidence_log(governed, "T-1") == []
    assert has_observed_inventory(governed, "T-1") is False


def test_one_corrupt_line_does_not_lose_its_valid_neighbours(governed: Path) -> None:
    log_path = governed / "_grimoire-output/evidence/T-1/evidence-log.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        '{"type": "bash", "command": "pytest -q"}\n'
        "coupure d'écriture au milieu\n"
        '{"no_type_field": true}\n'
        '{"type": "file_write", "path": "src/foo.py"}\n',
        encoding="utf-8",
    )
    entries = read_evidence_log(governed, "T-1")
    assert [e["type"] for e in entries] == ["bash", "file_write"]


# ── Écriture et classification ───────────────────────────────────────────────


def test_bash_event_recognises_known_test_runner_shapes() -> None:
    assert build_bash_event("pytest -q", exit_code=0)["type"] == "test_run"
    assert build_bash_event("npm test", exit_code=1)["type"] == "test_run"
    assert build_bash_event("cargo test --quiet", exit_code=None)["type"] == "test_run"
    assert build_bash_event("git status", exit_code=0)["type"] == "bash"


def test_long_command_is_truncated_not_dropped() -> None:
    event = build_bash_event("echo " + "x" * 1000, exit_code=0)
    assert len(event["command"]) < 300
    assert event["command"].endswith("(tronqué)")


def test_append_and_read_round_trip(governed: Path) -> None:
    append_evidence_event(governed, "T-1", build_bash_event("git status", exit_code=0))
    append_evidence_event(governed, "T-1", build_file_write_event("src/foo.py"))
    entries = read_evidence_log(governed, "T-1")
    assert len(entries) == 2
    assert entries[0]["type"] == "bash" and entries[1]["type"] == "file_write"


# ── Projection dans evidence-pack.md ─────────────────────────────────────────


def test_regenerate_is_idempotent_and_leaves_the_manual_section_untouched(governed: Path) -> None:
    task_id = _open_task(governed)
    scaffold_task_artifacts(governed, task_id=task_id)
    pack_path = governed / "_grimoire-output/evidence" / task_id / "evidence-pack.md"
    manual_before = pack_path.read_text(encoding="utf-8")

    append_evidence_event(governed, task_id, build_bash_event("pytest -q", exit_code=0))
    observed_first = regenerate_observed_inventory_section(governed, task_id)
    once = pack_path.read_text(encoding="utf-8")

    assert observed_first is True
    assert once.startswith(manual_before.split("<!-- grimoire:observed-inventory:start -->")[0][:40])
    assert once.count("<!-- grimoire:observed-inventory:start -->") == 1
    assert "## Inventaire observé" in once
    assert "`pytest -q`" in once
    assert f"- Task id: {task_id}" in once, "la section manuelle est conservée au-dessus"

    append_evidence_event(governed, task_id, build_bash_event("git status", exit_code=0))
    regenerate_observed_inventory_section(governed, task_id)
    twice = pack_path.read_text(encoding="utf-8")

    assert twice.count("<!-- grimoire:observed-inventory:start -->") == 1, "régénéré en place, jamais dupliqué"
    assert "`git status`" in twice


def test_regenerate_on_a_pack_without_the_scaffold_is_a_no_op(governed: Path) -> None:
    assert regenerate_observed_inventory_section(governed, "no-such-task") is False


# ── Le rouge-avant / vert-après cité dans la PR ──────────────────────────────


def test_governed_gate_flags_empty_inventory_then_accepts_the_observed_log(governed: Path) -> None:
    """Preuve du lot G2 : ``gate check`` (profil governed) signale un
    inventaire vide (ni manuel, ni observé) et cesse de le signaler dès
    qu'une action a été observée par le hook — sans qu'aucune ligne n'ait été
    recopiée à la main dans ``evidence-pack.md``. Reste un avertissement (pas
    une erreur qui ferait échouer ``result.ok``) : transition douce, comme le
    reste des constats de contenu ajoutés à ``check_evidence_gates`` (voir
    ``test_gate_check_surfaces_an_unproven_passed_criterion_without_failing``
    dans ``tests/test_agentic_standard.py``, qui fixe cette règle).
    """
    task_id = _open_task(governed)
    scaffold_task_artifacts(governed, task_id=task_id)
    # Pas de dépôt git dans cette fixture : la ligne placeholder de la
    # section manuelle « Evidence inventory » n'est donc jamais remplacée par
    # le scaffold — c'est le cas nu que ce lot doit fermer autrement.

    red = check_evidence_gates(governed, task_id=task_id, target_state="review")
    assert any(c.id == "evidence.inventory_placeholder" for c in red.checks), [c.message for c in red.checks]

    append_evidence_event(governed, task_id, build_bash_event("pytest -q", exit_code=0))

    green = check_evidence_gates(governed, task_id=task_id, target_state="review")
    assert not any(c.id == "evidence.inventory_placeholder" for c in green.checks), [c.message for c in green.checks]


def test_the_remedy_message_names_the_task_id_and_the_gate_command(governed: Path) -> None:
    task_id = _open_task(governed)
    scaffold_task_artifacts(governed, task_id=task_id)
    red = check_evidence_gates(governed, task_id=task_id, target_state="review")
    (check,) = [c for c in red.checks if c.id == "evidence.inventory_placeholder"]
    assert f"--task-id {task_id}" in check.message
