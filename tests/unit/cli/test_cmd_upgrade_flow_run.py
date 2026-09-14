"""`grimoire upgrade-flow run --json` porte assez de détail pour une UI (issue #506, PR B).

Avant cette PR, la réponse JSON de succès ne portait jamais où la
sauvegarde du nœud `backup` avait atterri (`BackupResult.to_dict()` était
jeté après avoir servi de `detail` au contrat), et un échec en cours de
route (`_fail` générique) perdait `done` et le nœud fautif — un consommateur
JSON ne pouvait alors jamais distinguer « rien n'a tourné » de « huit nœuds
sur neuf ont réussi ».

Projet dédié par test (jamais le `upgrade_project` module-scoped partagé de
``test_project_upgrade.py``) : le second test force `apply` à lever pour de
vrai via ``monkeypatch``, ce qui doit rester sans effet sur les autres tests
du module qui exercent le même nœud pour de vrai.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.slow


def _grimoire(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=180,
    )


@pytest.fixture
def fresh_project(tmp_path: Path) -> Path:
    """Un projet réellement initialisé, dédié à ce test — jamais partagé."""
    root = tmp_path / "projet"
    root.mkdir(parents=True)
    created = _grimoire(["init", ".", "-y", "--name", "cmd-upgrade-flow-run-test"], root)
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")
    return root


def _last_json_line(output: str) -> dict[str, Any]:
    return json.loads(output.strip().splitlines()[-1])


def test_a_dry_run_reports_the_backup_path(fresh_project: Path) -> None:
    """Le nœud `backup` tourne même sous `--dry-run` — son dossier doit
    atteindre la réponse JSON, pas rester enterré dans le `detail` que la
    boucle jette après l'avoir soumis au contrat."""
    from grimoire.cli.app import app

    result = CliRunner().invoke(
        app, ["upgrade-flow", "run", "--project-root", str(fresh_project), "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = _last_json_line(result.output)
    assert payload["ok"] is True
    backup_path = payload.get("backup_path")
    assert backup_path, "le nœud backup a tourné sous --dry-run, son dossier doit être rapporté"
    backup_dir = Path(backup_path)
    assert backup_dir.is_dir()
    assert backup_dir.parent.name == "_archive"
    assert any(backup_dir.glob("grimoire-state*.tar.gz")), "le tarball doit être dans ce dossier"


def test_a_node_failure_reports_done_and_the_failed_node(
    fresh_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_fail_run` remplace le `_fail` générique pour cette boucle : `done`
    (les nœuds réussis avant l'échec) et le nœud fautif doivent atteindre la
    réponse JSON, pas seulement un message d'erreur en texte libre."""
    import grimoire.tools.project_upgrade as project_upgrade
    from grimoire.cli.app import app
    from grimoire.core.exceptions import GrimoireRuntimeError

    def _boom(_root: Path) -> Any:
        raise GrimoireRuntimeError("doctor cassé exprès (test)")

    monkeypatch.setattr(project_upgrade, "apply_upgrade", _boom)

    result = CliRunner().invoke(
        app, ["upgrade-flow", "run", "--project-root", str(fresh_project), "--json"]
    )
    assert result.exit_code != 0
    payload = _last_json_line(result.output)
    assert payload["ok"] is False
    assert payload["failed_node"] == "apply"
    assert payload["done"] == ["backup", "preview", "orphans"]
    assert "doctor cassé exprès" in payload["error"]
