"""`grimoire host status` dit quelle persona d'entrée le projet retient.

Une désignation qu'aucune surface ne montre est une étiquette : ce test lit le
JSON de `host status` sur trois projets — sans clé, avec une clé vide, avec une
clé qui nomme un agent absent — et vérifie que chaque cas est dit, pas déduit.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app

runner = CliRunner()
AGENT_DIR = Path("_grimoire/_config/custom/agents")


def _project(root: Path, entry: str | None) -> Path:
    (root / AGENT_DIR).mkdir(parents=True)
    (root / AGENT_DIR / "concierge.md").write_text(
        '---\nname: "concierge"\ndescription: "Concierge — triage"\ntools: [read]\n---\nTu tries.\n',
        encoding="utf-8",
    )
    lines = ['project:', '  name: "entry-test"', '  type: "library"', 'agents:', '  archetype: "minimal"']
    if entry is not None:
        lines.append(f'  entry: "{entry}"')
    (root / "project-context.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _status(root: Path) -> dict:
    result = runner.invoke(app, ["-o", "json", "host", "status", "--host", "claude", "--project-root", str(root)])
    payload = json.loads(result.stdout)
    return payload[0] if isinstance(payload, list) else payload


def test_default_entry_is_concierge(tmp_path: Path) -> None:
    item = _status(_project(tmp_path, None))
    assert item["entry_agent"] == "concierge"
    assert item["entry_agent_declared"] == "concierge"


def test_an_empty_entry_is_reported_as_none(tmp_path: Path) -> None:
    item = _status(_project(tmp_path, ""))
    assert item["entry_agent"] == ""
    assert item["entry_agent_declared"] == ""


def test_an_entry_naming_no_agent_shows_the_gap(tmp_path: Path) -> None:
    """La clé est gardée telle quelle : l'écart entre déclaré et retenu doit se voir."""
    item = _status(_project(tmp_path, "fantome"))
    assert item["entry_agent"] == ""
    assert item["entry_agent_declared"] == "fantome"
