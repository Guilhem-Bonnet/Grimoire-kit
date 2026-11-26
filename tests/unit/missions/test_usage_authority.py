"""Chemin d'autorité : aucun agent ne relève un plafond, ne désactive une règle, n'efface une consommation.

Le plafond et les règles vivent dans ``capacity.yaml`` (un humain l'édite) ; la consommation est un journal
append-only que seul le tick écrit d'après les journaux de session. Ces tests le prouvent de trois façons :
aucune surface d'agent (outils MCP, routes et commandes du cockpit) n'y touche ; les lectures n'écrivent rien ;
la pause posée par une règle ne se lève pas par un agent.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.admission import QUOTA_CONSUMED_EVENT
from grimoire.missions.orchestrate import run_tick
from grimoire.missions.pause import CONTROL_HOME_ENV, DRAIN, PauseError, clear_pause, read_pause, write_pause
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.usage_rules import PAUSE_ORIGIN

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


def test_only_the_tick_step_writes_consumption_and_nothing_writes_the_capacity_file() -> None:
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

    capacity_view(projet)
    assert _digest(projet) == before


def test_the_tick_never_edits_the_file_and_the_consumption_only_grows(projet: Path) -> None:
    file_before = hashlib.sha256((projet / STANDARD / "capacity.yaml").read_bytes()).hexdigest()
    consumed = lambda: [  # noqa: E731
        e.id for e in TaskService(projet).ledger.list_events() if e.event_type == QUOTA_CONSUMED_EVENT
    ]
    before = consumed()
    run_tick(projet, now=datetime.now(UTC))
    run_tick(projet, now=datetime.now(UTC))
    assert hashlib.sha256((projet / STANDARD / "capacity.yaml").read_bytes()).hexdigest() == file_before
    assert set(before) <= set(consumed())


def test_a_pause_posed_by_a_rule_is_not_lifted_by_an_agent(projet: Path) -> None:
    run_tick(projet)
    (flag,) = read_pause(projet).flags
    assert flag.actor.startswith("usage-rule:") and flag.origin == PAUSE_ORIGIN
    with pytest.raises(PauseError):
        clear_pause(projet, origin=ORIGIN_AGENT, session="une-session-d-agent")
    assert read_pause(projet).flags


def _flag(projet: Path):  # type: ignore[no-untyped-def]
    (flag,) = read_pause(projet).flags
    return flag


def test_an_agent_cannot_replace_a_rule_pause_then_lift_it(projet: Path) -> None:
    """Remplacer puis lever contournait le contrôle de la levée : poser par-dessus est refusé, la pause reste."""
    run_tick(projet)
    before = _flag(projet)
    with pytest.raises(PauseError, match="ne la remplace pas"):
        write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="s-agent-1")
    with pytest.raises(PauseError):
        write_pause(projet, mode="hold", actor="cli", origin=ORIGIN_AGENT, session="s-agent-1", tasks=("T",))
    assert _flag(projet) == before
    with pytest.raises(PauseError):
        clear_pause(projet, origin=ORIGIN_AGENT, session="s-agent-1")


def test_an_agent_cannot_replace_a_human_or_another_sessions_pause(projet: Path) -> None:
    write_pause(projet, mode=DRAIN, actor="guilhem", reason="je tiens la main", origin=ORIGIN_HUMAN)
    with pytest.raises(PauseError, match="un humain"):
        write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="s-agent-1")
    assert _flag(projet).reason == "je tiens la main" and _flag(projet).origin == ORIGIN_HUMAN
    clear_pause(projet, origin=ORIGIN_HUMAN)
    write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="s-agent-1")
    with pytest.raises(PauseError, match="autre session"):
        write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="s-agent-2")
    with pytest.raises(PauseError):
        write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="")  # une session sans identité non plus
    assert _flag(projet).session == "s-agent-1"


def test_legitimate_pause_behaviours_are_unchanged(projet: Path) -> None:
    # Un agent pose sa propre pause, la renforce, la lève.
    write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="s-agent-1")
    write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="s-agent-1", reason="encore")
    assert _flag(projet).reason == "encore"
    clear_pause(projet, origin=ORIGIN_AGENT, session="s-agent-1")
    assert read_pause(projet).flags == ()
    # Un humain remplace n'importe quelle pause (agent, règle) et lève tout.
    write_pause(projet, mode=DRAIN, actor="cli", origin=ORIGIN_AGENT, session="s-agent-1")
    write_pause(projet, mode="hold", actor="guilhem", origin=ORIGIN_HUMAN, reason="à moi")
    assert _flag(projet).origin == ORIGIN_HUMAN and _flag(projet).mode == "hold"
    write_pause(projet, mode=DRAIN, actor="usage-rule:day:x", origin=PAUSE_ORIGIN)
    write_pause(projet, mode=DRAIN, actor="guilhem", origin=ORIGIN_HUMAN)
    clear_pause(projet, origin=ORIGIN_HUMAN)
    assert read_pause(projet).flags == ()
    # Le code du kit (sans origine) pose et remplace comme avant.
    write_pause(projet, mode=DRAIN, actor="kit")
    write_pause(projet, mode="hold", actor="kit")
    assert _flag(projet).mode == "hold"


def test_the_orchestrate_cli_applies_the_same_rule_for_pause_and_resume(
    projet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_tick(projet)
    before = _flag(projet)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-agent-cli-1")
    paused = runner.invoke(app, ["orchestrate", "pause", "--project-root", str(projet), "--actor", "usage-rule:day:gros"])
    assert paused.exit_code == 1 and "ne la remplace pas" in paused.output
    resumed = runner.invoke(app, ["orchestrate", "resume", "--project-root", str(projet)])
    assert resumed.exit_code == 1
    assert _flag(projet) == before  # ni remplacée par l'option --actor, ni levée


def test_a_partial_lift_keeps_the_owner_origin_actor_and_reason_of_the_flag(projet: Path) -> None:
    write_pause(
        projet, mode=DRAIN, tasks=("A", "B"), actor="cli", reason="deux tâches", origin=ORIGIN_AGENT, session="s-agent-1"
    )
    clear_pause(projet, tasks=("A",), origin=ORIGIN_AGENT, session="s-agent-1")
    flag = _flag(projet)
    assert flag.tasks == ("B",) and (flag.origin, flag.session, flag.actor, flag.reason) == (
        ORIGIN_AGENT, "s-agent-1", "cli", "deux tâches"
    )
    clear_pause(projet, origin=ORIGIN_AGENT, session="s-agent-1")  # la session lève encore le reste de SA pause
    assert read_pause(projet).flags == ()


def test_a_partial_lift_does_not_strip_a_rule_pause_of_its_origin(projet: Path) -> None:
    write_pause(projet, mode=DRAIN, tasks=("A", "B"), actor="usage-rule:task:t", reason="règle", origin=PAUSE_ORIGIN)
    clear_pause(projet, tasks=("A",), origin=ORIGIN_HUMAN)
    flag = _flag(projet)
    assert flag.tasks == ("B",) and flag.origin == PAUSE_ORIGIN and flag.actor == "usage-rule:task:t" and flag.reason == "règle"
    with pytest.raises(PauseError):
        clear_pause(projet, origin=ORIGIN_AGENT, session="s-agent-1")
