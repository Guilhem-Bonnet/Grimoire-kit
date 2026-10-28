"""Lot 4.4 : deux tentatives rouges au même diff → ``pilot.signal=stuck`` et palier suivant."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from grimoire.missions.dispatch import run_dispatch
from grimoire.missions.service import TaskService

STANDARD = Path("_grimoire/standard")
LEDGER = Path("_grimoire-runtime-output/ledger")


@pytest.fixture(autouse=True)
def _no_unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_UNATTENDED", raising=False)


def _git_init(root: Path) -> None:
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)


def _invocation(root: Path, name: str, body: str) -> str:
    path = root / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body), encoding="utf-8")
    return f"{Path(sys.executable).as_posix()} {path.as_posix()} {{prompt}} --model {{model}}"


def _registry(root: Path, cheap: str, mid: str) -> None:
    def provider(pid: str, tier: str, invocation: str) -> str:
        return f"""\
  - id: "{pid}"
    enabled: true
    provider_type: "local"
    allowed_capabilities: ["chat", "code"]
    default_models: ["{pid}-m"]
    currency: "local"
    invocation: "{invocation}"
    models:
      - id: "{pid}-m"
        tier: "{tier}"
    fallback_order: []
"""

    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text(
        'schema_version: "grimoire-llm-provider-registry/v1"\n'.replace("schema_version", "$schema")
        + 'metadata:\n  project: "demo"\n  owner: ""\n  policy: "x"\nproviders:\n'
        + provider("cheapo", "cheap", cheap)
        + provider("midi", "mid", mid)
        + 'routing:\n  default_provider: ""\n  default_fallback_chain: []\n  require_capability_match: true\n  require_data_policy_match: true\n',
        encoding="utf-8",
    )


def _run(tmp_path: Path, cheap_body: str, mid_body: str):
    _git_init(tmp_path)
    _registry(tmp_path, _invocation(tmp_path, "cheap.py", cheap_body), _invocation(tmp_path, "mid.py", mid_body))
    service = TaskService(tmp_path, LEDGER)
    mission = service.ledger.create_mission("Démo pilote", origin="test")
    task = service.ledger.create_task(mission.id, "Tâche", acceptance=("la suite de tests passe",), owner="amelia")
    report = run_dispatch(service, task.id, checks=("test -f never.txt",), start_tier="cheap")
    events = [e for e in service.ledger.list_events(task.id) if e.event_type == "pilot.signal"]
    return report, events


_NOOP = "import sys\n"


def test_same_diff_twice_emits_stuck_and_changes_tier(tmp_path: Path) -> None:
    report, events = _run(tmp_path, _NOOP, _NOOP)

    assert [a.tier for a in report.attempts] == ["cheap", "mid"]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["signal"] == "stuck"
    assert payload["from_tier"] == "mid"


def test_different_diff_emits_no_signal(tmp_path: Path) -> None:
    writer = 'from pathlib import Path\nPath("a.txt").write_text("x")\n'
    _, events = _run(tmp_path, _NOOP, writer)

    assert events == []
