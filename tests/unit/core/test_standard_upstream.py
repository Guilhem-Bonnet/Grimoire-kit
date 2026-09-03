"""Le bridge sait dire si le standard qu'il trace a bougé.

Sans révision épinglée, « aligné sur le standard » n'était vérifiable qu'en
relisant les deux dépôts à la main. Ces tests injectent la tête distante : le
réseau n'est pas la chose testée.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import load_profile_map
from grimoire.core.standard_upstream import upstream_status

runner = CliRunner()


def _pinned() -> str:
    return str(load_profile_map()["metadata"]["upstream_standard"]["commit"])


def test_the_map_pins_a_full_commit() -> None:
    meta = load_profile_map()["metadata"]["upstream_standard"]
    assert len(meta["commit"]) == 40 and meta["remote"].endswith(".git") and meta["pinned_on"]


def test_identical_head_is_pinned() -> None:
    status = upstream_status(ls_remote=lambda remote, branch: _pinned())
    assert status.state == "pinned" and status.exit_code == 0


def test_a_moved_standard_is_a_drift_not_a_detail() -> None:
    status = upstream_status(ls_remote=lambda remote, branch: "f" * 40)
    assert status.state == "ahead" and status.exit_code == 2
    assert status.remote_head == "f" * 40


def test_an_unreachable_remote_is_unverified_not_fine() -> None:
    status = upstream_status(ls_remote=lambda remote, branch: None)
    assert status.state == "unreachable" and status.exit_code == 3


def test_cli_reports_json_and_exit_code(monkeypatch) -> None:
    import grimoire.core.standard_upstream as mod

    monkeypatch.setattr(mod, "git_ls_remote", lambda remote, branch, timeout=15.0: "f" * 40)
    result = runner.invoke(app, ["-o", "json", "standard", "upstream"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["state"] == "ahead"
