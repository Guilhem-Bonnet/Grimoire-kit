"""K13 (épopée #705) — banc synthétique de 10 000 événements : les budgets de D21, mesurés.

Marqué ``perf`` : hors CI par défaut (``addopts = -m 'not perf'``), à lancer à la main ::

    pytest tests/unit/test_k13_latency_budgets.py -m perf -s

Le rapport (p50, p95, budget, verdict par hook) s'imprime avec ``-s`` et s'écrit dans le fichier que
nomme ``GRIMOIRE_BENCH_REPORT`` (par défaut ``k13-latency-report.json`` sous le répertoire temporaire du test).

Ce que le banc **tient** (assertions) : PreToolUse p95 ≤ 50 ms pour une session qui tient son bail, rejeu
complet du ledger p95 ≤ 500 ms. Ce qu'il **rapporte sans l'exiger** : les autres hooks (budget 100 ms) — ils
lisent aussi des fichiers YAML et relancent des ``MissionLedger`` à eux, travail hors du périmètre de K13 ;
le rapport dit lesquels dépassent, sans les cacher derrière une assertion qui échouerait.

Les durées sont celles de la décision *dans le processus* ; le démarrage de l'interpréteur (≈ 125 ms d'imports,
indépendants du ledger) n'y est pas compté.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from typing import TYPE_CHECKING, Any

import pytest

from grimoire.hosts.events import HookEvent
from grimoire.missions import ledger_snapshot
from grimoire.missions.ledger import MissionLedger

from .test_k13_system_cost import governed  # noqa: F401
from .test_pretooluse_brake import LEDGER, _claimed, _hook, _prompt, _real_pretooluse, _transcript

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.perf

EVENTS = 10_000
PRETOOLUSE_BUDGET_MS = 50.0
OTHER_HOOK_BUDGET_MS = 100.0
REPLAY_BUDGET_MS = 500.0
SESSION = "00000000-0000-4000-8000-000000000003"


def _inflate(ledger_dir: Path, task_id: str, target: int) -> int:
    """Gonfle le journal de *task_id* jusqu'à *target* événements en clonant son cycle de vie sous d'autres identifiants."""
    events = ledger_dir / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    clones: list[str] = []
    copy = 0
    while len(lines) + len(clones) < target:
        for line in lines:
            record = json.loads(line.replace(task_id, f"{task_id}-c{copy}"))
            record["id"] = f"{record['id']}-c{copy}"
            clones.append(json.dumps(record, ensure_ascii=False))
        copy += 1
    events.write_text("\n".join([*clones[: target - len(lines)], *lines]) + "\n", encoding="utf-8")
    return len(events.read_text(encoding="utf-8").splitlines())


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    return {"p50": round(statistics.median(ordered), 2), "p95": round(p95, 2), "max": round(ordered[-1], 2)}


def _time(runs: int, call: Callable[[], Any]) -> list[float]:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def test_hook_and_replay_budgets_at_10000_events(request: pytest.FixtureRequest, tmp_path: Path) -> None:
    root: Path = request.getfixturevalue("governed")
    task_id = _claimed(root)
    _prompt(root)
    ledger_dir = root / LEDGER
    total = _inflate(ledger_dir, task_id, EVENTS)
    size = (ledger_dir / "events.jsonl").stat().st_size

    # Rejeu complet : sans instantané (le premier rejeu en écrit un, on le retire à chaque mesure).
    def full_replay() -> None:
        (ledger_dir / ledger_snapshot.SNAPSHOT_FILENAME).unlink(missing_ok=True)
        MissionLedger(ledger_dir).list_tasks()

    replay_full = _percentiles(_time(7, full_replay))
    MissionLedger(ledger_dir).list_tasks()  # laisse un instantané
    replay_resumed = _percentiles(_time(7, lambda: MissionLedger(ledger_dir).list_tasks()))

    def payload(event: str, **extra: Any) -> dict[str, Any]:
        return {
            "session_id": SESSION,
            "hook_event_name": event,
            "cwd": str(root),
            "transcript_path": str(_transcript(root)),
            **extra,
        }

    cases: dict[str, tuple[HookEvent, Callable[[], dict[str, Any]], float]] = {
        "PreToolUse": (HookEvent.PRE_TOOL_USE, lambda: _real_pretooluse(root), PRETOOLUSE_BUDGET_MS),
        "UserPromptSubmit": (HookEvent.USER_PROMPT_SUBMIT, lambda: payload("UserPromptSubmit", prompt="go"), OTHER_HOOK_BUDGET_MS),
        "PostToolUse": (
            HookEvent.POST_TOOL_USE,
            lambda: payload(
                "PostToolUse",
                tool_name="Bash",
                tool_input={"command": "touch x"},
                tool_response={"stdout": "", "stderr": "", "interrupted": False},
                tool_use_id="toolu_bench",
            ),
            OTHER_HOOK_BUDGET_MS,
        ),
        "Stop": (HookEvent.STOP, lambda: payload("Stop", stop_hook_active=False), OTHER_HOOK_BUDGET_MS),
        "SessionStart": (HookEvent.SESSION_START, lambda: payload("SessionStart", source="startup"), OTHER_HOOK_BUDGET_MS),
        "SubagentStop": (HookEvent.SUBAGENT_STOP, lambda: payload("SubagentStop", agent_type="bench"), OTHER_HOOK_BUDGET_MS),
        "PreCompact": (HookEvent.PRE_COMPACT, lambda: payload("PreCompact", trigger="auto"), OTHER_HOOK_BUDGET_MS),
    }
    hooks: dict[str, dict[str, Any]] = {}
    for name, (event, build, budget) in cases.items():
        runs = 100 if name == "PreToolUse" else 15
        stats = _percentiles(_time(runs, lambda event=event, build=build: _hook(root, event, build())))
        hooks[name] = {**stats, "budget_ms": budget, "within_budget": stats["p95"] <= budget, "runs": runs}

    report = {
        "events": total,
        "journal_bytes": size,
        "scope": "décision dans le processus, hors démarrage de l'interpréteur",
        "replay_full_ms": {**replay_full, "budget_ms": REPLAY_BUDGET_MS, "within_budget": replay_full["p95"] <= REPLAY_BUDGET_MS},
        "replay_resumed_from_snapshot_ms": replay_resumed,
        "hooks": hooks,
    }
    target = os.environ.get("GRIMOIRE_BENCH_REPORT") or str(tmp_path / "k13-latency-report.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("\n" + json.dumps(report, ensure_ascii=False, indent=2))

    assert total >= EVENTS
    assert report["replay_full_ms"]["within_budget"], f"rejeu complet : {replay_full} > {REPLAY_BUDGET_MS} ms"
    assert hooks["PreToolUse"]["within_budget"], f"PreToolUse : {hooks['PreToolUse']}"
