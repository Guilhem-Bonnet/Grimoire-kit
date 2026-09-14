"""``grimoire upgrade-flow run`` — reporting a refused ``apply`` (issue #510, point 3).

Before this fix, a refused ``apply`` node raised ``GrimoireRuntimeError`` from
inside ``_node_handlers()._apply``, caught by the loop's generic exception
handler (``cmd_upgrade_flow.upgrade_flow_run``) which discarded every node
already done, the backup path, and the failing check's name — the JSON
payload came back ``{"ok": false, "error": "..."}"`` with no ``done``,
``stopped_at`` or ``state``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.tools.project_upgrade import ApplyResult, ProbeResult

runner = CliRunner()


def _fake_failing_apply(_root: Path) -> ApplyResult:
    return ApplyResult(
        ok=False,
        up_ok=True,
        doctor_failures=("1 agent(s) référencé(s) mais absent(s) (manifeste ou projection hôte) : ghost",),
        hook=ProbeResult(ok=True, detail="hook rejoué sans erreur"),
        failing_checks=("agents_referenced",),
    )


def test_run_reports_done_state_and_backup_path_when_apply_is_refused(
    cli_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("grimoire.tools.project_upgrade.apply_upgrade", _fake_failing_apply)

    result = runner.invoke(app, ["upgrade-flow", "run", "--project-root", str(cli_project), "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["done"] == ["backup", "preview", "orphans"]
    assert payload["stopped_at"] == "apply"
    assert payload["state"] == "upgraded-but-failed"
    assert payload["failing_checks"] == ["agents_referenced"]
    assert payload["backup_path"]
    assert Path(payload["backup_path"]).is_file()
    assert payload["repairs_proposed"] == 0
    assert "référencé" in payload["error"]

    from grimoire.tools.project_upgrade import run_output_dir

    report_path = Path(payload["report_path"])
    assert report_path == run_output_dir(cli_project) / "report.md"
    report_text = report_path.read_text(encoding="utf-8")
    assert report_text.startswith("Mis à niveau, flow en échec sur agents_referenced")


def test_run_text_output_names_the_failing_control(
    cli_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("grimoire.tools.project_upgrade.apply_upgrade", _fake_failing_apply)

    result = runner.invoke(app, ["upgrade-flow", "run", "--project-root", str(cli_project)])

    assert result.exit_code == 1
    assert "agents_referenced" in result.output
