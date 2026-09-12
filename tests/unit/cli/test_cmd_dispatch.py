"""``grimoire dispatch stats`` — coût par tâche résolue et pass^k (issue #442).

Sœur de ``grimoire providers history`` : ces tests vérifient que la commande
lit le journal de traces (pas le Mission Ledger), rend un JSON fidèle à
``TraceLedger.dispatch_outcome_stats``, filtre correctement avec ``--since``,
et ne plante jamais en l'absence de journal.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger
from grimoire.traces.schemas import TraceOutcome

runner = CliRunner()


def _write_outcome(
    root: Path,
    *,
    started_at: str,
    class_: str = "V0",
    resolved: bool = True,
    cost: float = 0.01,
    provider: str = "openai",
) -> None:
    tags = [DISPATCH_OUTCOME_TAG, f"class:{class_}", "tier:cheap", "acceptance:judged", f"resolved:{'true' if resolved else 'false'}", f"replay:GAO-{started_at}"]
    if provider:
        tags.append(f"provider:{provider}")
    TraceLedger(root / TRACES_DIR).record(
        run_id=f"dispatch-{started_at}",
        workflow_instance_id="",
        mission_id="",
        task_id=f"GAO-{started_at}",
        recipe_id="grimoire.dispatch",
        outcome=TraceOutcome.SUCCESS if resolved else TraceOutcome.FAILURE,
        started_at=started_at,
        agent_id=provider,
        token_usage={"estimated_cost_usd": cost},
        tags=tags,
    )


def test_sans_journal_ne_plante_pas(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.stdout


def test_sans_journal_json_rend_overall_null(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == {"overall": None}


def test_json_reflete_fidelement_dispatch_outcome_stats(tmp_path: Path) -> None:
    _write_outcome(tmp_path, started_at="2026-01-01T00:00:00+00:00", resolved=True, cost=0.01)
    _write_outcome(tmp_path, started_at="2026-01-02T00:00:00+00:00", resolved=False, cost=0.02)

    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    expected = TraceLedger(tmp_path / TRACES_DIR).dispatch_outcome_stats().to_dict()
    assert payload == expected
    assert payload["overall"]["total"] == 2
    assert payload["overall"]["resolved"] == 1


def test_texte_liste_l_ensemble_et_les_classes(tmp_path: Path) -> None:
    _write_outcome(tmp_path, started_at="2026-01-01T00:00:00+00:00", class_="V0")
    _write_outcome(tmp_path, started_at="2026-01-02T00:00:00+00:00", class_="V1")

    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "ensemble" in result.stdout
    assert "V0" in result.stdout
    assert "V1" in result.stdout


def test_since_exclut_les_dispatchs_trop_anciens(tmp_path: Path) -> None:
    now = datetime.now(tz=UTC)
    old = (now - timedelta(days=60)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    _write_outcome(tmp_path, started_at=old, cost=0.5)
    _write_outcome(tmp_path, started_at=recent, cost=0.1)

    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path), "--since", "30d", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["overall"]["total"] == 1
    assert payload["overall"]["total_cost_usd"] == 0.1


def test_since_mal_forme_est_un_parametre_invalide(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dispatch", "stats", "--project-root", str(tmp_path), "--since", "trois-jours"])
    assert result.exit_code != 0
