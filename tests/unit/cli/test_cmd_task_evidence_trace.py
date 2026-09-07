"""``grimoire task pack`` / ``task trace-export`` (#275) — un appelant réel.

`EvidenceService.get_pack` et `TraceLedger.export_langfuse` n'avaient ni
appelant ni test avant cette passe (#275). Ces tests échouent si la commande
qui les appelle disparaît ou cesse d'appeler l'accesseur.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_task import task_app
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService

runner = CliRunner()

EVIDENCE = Path("_grimoire-runtime-output/evidence")


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return tmp_path


def _cree_pack(projet: Path, task_id: str = "GAO-demo-001") -> str:
    svc = EvidenceService(projet / EVIDENCE)
    item = EvidenceItem(id="ev-1", kind=EvidenceKind.TEST, uri="tests/test_x.py", digest="sha256:abc", summary="tests passent")
    pack = svc.create_pack(task_id, EvidenceProfile.LIGHT, [item], acceptance=("tests passent",))
    return pack.id


def test_task_pack_lit_le_pack_par_id(projet: Path) -> None:
    task_id = "GAO-demo-001"
    pack_id = _cree_pack(projet, task_id)

    res = runner.invoke(task_app, ["pack", task_id, pack_id, "--project-root", str(projet)])

    assert res.exit_code == 0, res.output
    assert pack_id in res.output
    assert "tests passent" in res.output or "TEST" in res.output.upper()


def test_task_pack_json_expose_la_couverture(projet: Path) -> None:
    task_id = "GAO-demo-002"
    pack_id = _cree_pack(projet, task_id)

    res = runner.invoke(app, ["--output", "json", "task", "pack", task_id, pack_id, "--project-root", str(projet)])

    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["id"] == pack_id
    assert data["coverage"]["acceptance_covered"] == ["tests passent"]


def test_task_pack_id_inconnu_echoue(projet: Path) -> None:
    res = runner.invoke(task_app, ["pack", "GAO-demo-003", "EVD-does-not-exist", "--project-root", str(projet)])

    assert res.exit_code == 1
    assert "introuvable" in res.output


def test_task_trace_export_otel_et_langfuse(projet: Path) -> None:
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.schemas import TraceOutcome

    ledger = TraceLedger(projet / TRACES_DIR)
    ledger.record(
        run_id="RUN-abc123",
        workflow_instance_id="WFI-1",
        mission_id="MIS-1",
        task_id="GAO-demo-004",
        recipe_id="recipe.pack.demo",
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-09-07T10:00:00+00:00",
    )

    otel_dest = projet / "traces-otel.jsonl"
    res_otel = runner.invoke(
        task_app,
        ["trace-export", str(otel_dest), "--format", "otel", "--project-root", str(projet)],
    )
    assert res_otel.exit_code == 0, res_otel.output
    assert otel_dest.exists()
    assert json.loads(otel_dest.read_text(encoding="utf-8").splitlines()[0])["name"]

    langfuse_dest = projet / "traces-langfuse.jsonl"
    res_langfuse = runner.invoke(
        task_app,
        ["trace-export", str(langfuse_dest), "--format", "langfuse", "--project-root", str(projet)],
    )
    assert res_langfuse.exit_code == 0, res_langfuse.output
    assert langfuse_dest.exists()
    first = json.loads(langfuse_dest.read_text(encoding="utf-8").splitlines()[0])
    assert first["name"] == "grimoire.recipe.pack.demo"


def test_task_trace_export_format_inconnu(projet: Path) -> None:
    res = runner.invoke(
        task_app,
        ["trace-export", str(projet / "out.jsonl"), "--format", "bogus", "--project-root", str(projet)],
    )
    assert res.exit_code == 2
