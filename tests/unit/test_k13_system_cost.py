"""K13 (épopée #705) — le PreToolUse ne rejoue pas le ledger ; un rejeu n'est payé qu'une fois.

Constat sur la base (``origin/main``, K2 + K3) : le frein du bail lit le fichier de bail et
jamais le journal, mais le reste du PreToolUse, lui, rejoue le ledger en entier — la
résolution de la tâche active (``active_task_id``) deux fois dans ``decide_tool_policy``,
une troisième dans ``_record_decision`` (trace), une de plus dans ``_record_hold`` sur un
refus. Chaque résolution relance ``list_tasks`` deux fois (``_held_claims``). Sur un ledger
de 10 000 événements, un ``Bash`` mutant coûtait environ 750 ms au lieu des 50 ms que D21
accorde au p95 du PreToolUse. Ces tests comptent les rejeux (un espion sur
``MissionLedger._replay_events``), pas des millisecondes : ils tiennent partout. Le banc
chronométré est ``test_k13_latency_budgets.py`` (marqué ``perf``, hors CI).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import Outcome
from grimoire.missions.lease import lease_path, write_lease
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.service import TaskService

from .test_pretooluse_brake import (  # noqa: F401
    SESSION,
    _claimed,
    _pre,
    _prompt,
    _real_pretooluse,
    _verdict,
)

if TYPE_CHECKING:
    from pathlib import Path



@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le projet ``governed`` de ``test_pretooluse_brake`` : environnement d'hôte purgé, registre activé."""
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_HOST_VERSION"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    return tmp_path


#: Une commande que le profil ``governed`` ne laisse pas passer sans question (``ask`` ou ``deny``).
DESTRUCTIVE = "rm -rf build"


@pytest.fixture
def replays(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Un espion sur le rejeu du journal : une entrée par ``_replay_events`` réellement lancé."""
    seen: list[str] = []
    original = MissionLedger._replay_events

    def spy(self: MissionLedger) -> None:
        seen.append(str(self._events_path))
        original(self)

    monkeypatch.setattr(MissionLedger, "_replay_events", spy)
    return seen


def _trace_task_ids(root: Path) -> set[str]:
    """Les ``task_id`` que les traces du projet portent (fichiers JSON ou JSONL sous ``traces/``)."""
    found: set[str] = set()
    for path in (root / "_grimoire-output").rglob("*.json*"):
        if "trace" not in path.as_posix():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if isinstance(raw, dict) and raw.get("task_id"):
                found.add(str(raw["task_id"]))
    return found


def test_pretooluse_reads_lease_file_not_ledger(governed: Path, replays: list[str]) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    replays.clear()  # seul le PreToolUse est jugé : le claim et le premier prompt ont le droit de lire

    rendered, decision = _pre(governed, _real_pretooluse(governed))

    assert _verdict(rendered) == "allow"
    assert replays == [], f"le PreToolUse a rejoué le ledger {len(replays)} fois : {replays}"
    # La tâche n'est pas perdue pour autant : la décision et la trace la portent.
    assert decision.detail["task_id"] == tid
    assert tid in _trace_task_ids(governed)


def test_pretooluse_hold_does_not_replay_the_ledger(governed: Path, replays: list[str]) -> None:
    tid = _claimed(governed)
    _prompt(governed)
    replays.clear()

    rendered, decision = _pre(governed, _real_pretooluse(governed, command=DESTRUCTIVE))

    assert decision.outcome is not Outcome.ALLOW, "la commande de contrôle doit être retenue par la politique"
    assert _verdict(rendered) in ("ask", "deny")
    assert replays == [], f"le refus a rejoué le ledger {len(replays)} fois : {replays}"
    assert decision.detail["task_id"] == tid


def test_session_without_a_lease_replays_the_ledger_once(governed: Path, replays: list[str]) -> None:
    """Sans bail noté, le ledger tranche (le fichier absent renvoie au ledger) — mais une seule fois."""
    _claimed(governed)  # claim non rattaché à la session qui parle
    replays.clear()

    rendered, _ = _pre(governed, _real_pretooluse(governed, session="une-autre-session"))

    assert _verdict(rendered) == "allow"
    assert len(replays) == 1, f"{len(replays)} rejeux du ledger pour un seul appel d'outil"


def test_lease_cache_miss_still_decides_on_the_ledger(governed: Path, replays: list[str]) -> None:
    """Le fichier de bail absent : le chemin rapide se tait, le ledger fait foi (inchangé)."""
    tid = _claimed(governed)
    _prompt(governed)
    lease_path(TaskService(governed).ledger_root, tid).unlink()
    replays.clear()

    rendered, decision = _pre(governed, _real_pretooluse(governed))

    assert _verdict(rendered) == "allow"
    assert replays, "sans fichier de bail, le ledger doit être relu"
    assert decision.detail["task_id"] == tid


def test_a_barrier_in_the_lease_file_is_not_a_held_task(governed: Path) -> None:
    """Une barrière (``holder=False``) n'est pas un titulaire : le chemin rapide ne nomme pas sa tâche."""
    tid = _claimed(governed)
    _prompt(governed)
    path = lease_path(TaskService(governed).ledger_root, tid)
    write_lease(path, _barrier(tid))

    rendered, _ = _pre(governed, _real_pretooluse(governed))

    assert _verdict(rendered) == "deny"  # le frein du bail refuse toujours, avant tout chemin rapide


def test_task_env_override_still_wins(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GRIMOIRE_TASK_ID`` désigne la tâche, comme dans ``resolve_active_task`` : le chemin rapide ne le masque pas."""
    _claimed(governed)
    _prompt(governed)
    monkeypatch.setenv("GRIMOIRE_TASK_ID", "TASK-FORCEE")

    _, decision = _pre(governed, _real_pretooluse(governed))

    assert decision.detail["task_id"] == "TASK-FORCEE"


def _barrier(task_id: str) -> Any:
    from datetime import UTC, datetime

    from grimoire.missions.lease import LeaseFile

    return LeaseFile(task_id, 2, datetime.now(UTC).isoformat(), holder=False, origin="reap")
