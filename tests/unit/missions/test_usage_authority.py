"""Chemin d'autorité : aucun agent ne relève un plafond, ne désactive une règle, n'efface une consommation.

Le plafond et les règles vivent dans ``capacity.yaml`` (un humain l'édite) ; la consommation est un journal
append-only que seul le tick écrit d'après les journaux de session. Ces tests le prouvent : aucune surface d'agent
(outils MCP, routes et commandes du cockpit) n'y touche, et les lectures n'écrivent rien.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.pause import CONTROL_HOME_ENV
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.usage_rules import QUOTA_CONSUMED_EVENT

SRC = Path(__file__).resolve().parents[3] / "src" / "grimoire"
STANDARD = Path("_grimoire/standard")
runner = CliRunner()
#: Ce qui désigne le plafond, les règles ou la consommation dans le code.
_MARKERS = ("capacity.yaml", "CAPACITY_FILE", "QUOTA_CONSUMED_EVENT", "quota.consumed", "usage_rules", "HOLD_EVENT")
_WRITES = re.compile(r"write_text\(|write_bytes\(|atomic_write\(|\.unlink\(|shutil\.|open\([^)]*['\"][wa]")


def _files(*patterns: str) -> list[Path]:
    return sorted({p for pattern in patterns for p in SRC.glob(pattern)})


def test_no_agent_surface_names_the_ceiling_the_rules_or_the_consumption() -> None:
    """MCP, routes et console du cockpit : aucune ne mentionne ces objets, hors la lecture ``workspace_capacity``."""
    surfaces = [p for p in _files("mcp/*.py", "tools/workspace_*.py") if p.name != "workspace_capacity.py"]
    assert surfaces, "les surfaces d'agent n'ont pas été trouvées"
    offenders = {p.name: [m for m in _MARKERS if m in p.read_text(encoding="utf-8")] for p in surfaces}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_the_cockpit_capacity_sheet_is_read_only() -> None:
    source = (SRC / "tools" / "workspace_capacity.py").read_text(encoding="utf-8")
    assert not _WRITES.search(source) and "append_event" not in source and "write_pause" not in source


def test_no_mcp_tool_or_cockpit_command_can_change_capacity_quota_or_rules() -> None:
    server = (SRC / "mcp" / "server.py").read_text(encoding="utf-8")
    names = re.findall(r"^def ((?:grimoire|task|idea)_\w+)\(", server, flags=re.MULTILINE)
    assert names, "les outils MCP n'ont pas été trouvés"
    assert [n for n in names if re.search(r"capacity|quota|rule|usage|pause", n)] == []
    from grimoire.tools.workspace_exec import ALLOWED

    assert [c for c in ALLOWED if re.search(r"capacity|quota|rules|pause|resume", c)] == []


def test_only_the_consumption_step_writes_it_and_nothing_writes_the_capacity_file() -> None:
    writers = [p.name for p in _files("**/*.py") if re.search(r"append_event\(\s*QUOTA_CONSUMED_EVENT", p.read_text(encoding="utf-8"))]
    assert writers == ["quota_usage.py"]
    mentioning = [p for p in _files("**/*.py") if any(m in p.read_text(encoding="utf-8") for m in ("capacity.yaml", "CAPACITY_FILE"))]
    assert mentioning, "aucun lecteur de capacity.yaml trouvé"
    risky: list[str] = []
    for path in mentioning:
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            near = " ".join(lines[max(number - 2, 0) : number + 1]).lower()
            if _WRITES.search(line) and ("capacity" in near or "standard_dir" in near):
                risky.append(f"{path.name}:{number + 1}")
    assert risky == []


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(CONTROL_HOME_ENV, str(tmp_path / "control-home"))
    root = tmp_path / "projet"
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "capacity.yaml").write_text(
        "version: 1\nquota:\n  tokens_per_day: 100\nusage_rules:\n"
        "  - {id: gros, scope: day, metric: tokens, threshold: 50, action: pause}\n",
        encoding="utf-8",
    )
    service = TaskService(root)
    mission = service.ledger.create_mission(title="Autorité", origin="test").id
    task = service.ledger.create_task(mission, "t", acceptance=("ok",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    service.ledger.append_event(
        QUOTA_CONSUMED_EVENT, "project", "task", "tick", {"session": "s", "task_id": "", "tokens": 90, "status": "lower_bound"}
    )
    return root


def _digest(root: Path) -> tuple[str, int]:
    return (
        hashlib.sha256((root / STANDARD / "capacity.yaml").read_bytes()).hexdigest(),
        len(TaskService(root).ledger.list_events()),
    )


def test_reading_commands_write_neither_the_ceiling_nor_the_rules_nor_the_ledger(projet: Path) -> None:
    before = _digest(projet)
    for argv in (["queue", "rules"], ["queue", "rules", "--json"], ["queue", "plan", "--json"]):
        runner.invoke(app, [*argv, "--project-root", str(projet)])
    assert _digest(projet) == before
    from grimoire.tools.workspace_capacity import capacity_view

    capacity_view(projet, now=datetime.now(UTC))
    assert _digest(projet) == before
