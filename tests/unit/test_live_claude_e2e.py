"""D-12 : scénario e2e live Claude Code — tout ce qui se vérifie sans modèle.

Aucun ``claude`` n'est lancé : ``subprocess.Popen`` lève s'il le voit, et l'invocateur est un faux.
Le test de bout en bout sans modèle utilise le vrai ``grimoire`` (relais vers ``src/``) pour préparer
le projet gouverné ; il vérifie qu'une attestation absente arrête le scénario avant tout appel de plus.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"


def _load() -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location("bench_live_claude_e2e", BENCH / "live_claude_e2e.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


le = _load()
ta = le.ta

_REAL_POPEN = subprocess.Popen


def _no_claude(cmd: Any, *args: Any, **kwargs: Any) -> Any:
    argv = [cmd] if isinstance(cmd, str) else list(cmd)
    if any(Path(str(a)).name.startswith("claude") for a in argv[:1]):
        raise AssertionError(f"un modèle a été lancé : {argv[:3]}")
    return _REAL_POPEN(cmd, *args, **kwargs)


@pytest.fixture(autouse=True)
def _never_launch_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", _no_claude)
    monkeypatch.setattr(le, "detect_claude_code_version", lambda **k: "9.9.9 (Claude Code)")


def _outcome(total: float | None, *, session: str = "S") -> Any:
    return ta.RunOutcome(
        total_cost_usd=total or 0.0,
        cost_known=total is not None,
        num_turns=2,
        wall_seconds=1.0,
        raw_result={"type": "result", "session_id": session, "result": "OK"},
    )


class FakeInvoker:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        project: Path,
        prompt: str,
        *,
        env: dict[str, str],
        model: str | None,
        extra_args: list[str],
        log_path: Path,
        timeout_s: int,
    ) -> Any:
        self.calls.append({"prompt": prompt, "env": dict(env), "args": list(extra_args)})
        return self.outcomes.pop(0)


def _caller(tmp_path: Path, invoker: FakeInvoker, cap: float = 5.0, ledger: Path | None = None) -> Any:
    guard = le.BudgetGuard.open(cap, ledger or tmp_path / "budget.jsonl")
    (tmp_path / "home" / ".claude").mkdir(parents=True, exist_ok=True)
    return le.Caller(guard, tmp_path, tmp_path / "home", tmp_path, "m", "api-key", invoke=invoker)


# ── Environnement et commande ────────────────────────────────────────────────


def test_clean_env_never_inherits_the_parent_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Une session parente ne doit jamais devenir celle du scénario (claims, tours, attestations)."""
    for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "GRIMOIRE_SESSION_ID", "ANTHROPIC_API_KEY", "GRIMOIRE_UNATTENDED"):
        monkeypatch.setenv(name, "parent")
    env = le.clean_env(home=tmp_path, path_dirs=[tmp_path / "bin"], tmpdir=tmp_path)
    assert not any(k.startswith("CLAUDE") for k in env)
    assert "GRIMOIRE_SESSION_ID" not in env and "ANTHROPIC_API_KEY" not in env and "GRIMOIRE_UNATTENDED" not in env
    assert env["HOME"] == str(tmp_path)
    run = le.clean_env(home=tmp_path, path_dirs=[], tmpdir=tmp_path, unattended=True, session_id="S2")
    assert run["GRIMOIRE_UNATTENDED"] == "1" and run["GRIMOIRE_SESSION_ID"] == "S2"


def test_call_args_never_bare_and_pass_the_rest_of_the_cap() -> None:
    first = le.call_args(session_id="S1", resume=None, max_budget_usd=4.25, attend_only=True)
    assert first[:5] == ["--max-budget-usd", "4.2500", "--strict-mcp-config", "--session-id", "S1"]
    assert first[-2:] == ["--disallowedTools", le.ATTEND_DISALLOWED_TOOLS]  # variadique, en dernier
    resumed = le.call_args(session_id=None, resume="S2", max_budget_usd=1.0, attend_only=False)
    assert resumed == ["--max-budget-usd", "1.0000", "--strict-mcp-config", "--resume", "S2"]
    assert "--bare" not in first + resumed


def test_only_the_project_mcp_servers_are_loaded_and_none_for_a_human_turn(tmp_path: Path) -> None:
    config = tmp_path / ".mcp.json"
    work = le.call_args(session_id="S2", resume=None, max_budget_usd=1.0, attend_only=False, mcp_config=config)
    i = work.index("--strict-mcp-config")
    assert work[i + 1 : i + 3] == ["--mcp-config", config.as_posix()]
    turn = le.call_args(session_id="S1", resume=None, max_budget_usd=1.0, attend_only=True, mcp_config=config)
    assert "--strict-mcp-config" in turn and "--mcp-config" not in turn
    assert turn[-2] == "--disallowedTools"


def test_isolate_host_trusts_the_project_and_excludes_parent_instructions(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "a" / "project"
    home.mkdir()
    project.mkdir(parents=True)
    (home / ".claude.json").write_text('{"userID": "x"}', encoding="utf-8")
    le.isolate_host(home, project)
    config = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert config["userID"] == "x" and config["projects"][project.as_posix()]["hasTrustDialogAccepted"] is True
    excludes = json.loads((project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))["claudeMdExcludes"]
    assert (tmp_path / "CLAUDE.md").as_posix() in excludes and (tmp_path / "a" / "CLAUDE.md").as_posix() in excludes
    assert (project / "CLAUDE.md").as_posix() not in excludes  # les instructions du kit restent chargées


def test_a_failed_call_stops_the_scenario_after_counting_it(tmp_path: Path) -> None:
    """Premier passage réel : « Not logged in », coût 0 connu ; la suite n'a pas à tourner sur ce vide."""
    failed = _outcome(0.0, session="S1")
    failed.raw_result = {"type": "result", "session_id": "S1", "is_error": True, "result": "Not logged in"}
    invoker = FakeInvoker([failed])
    caller = _caller(tmp_path, invoker)
    with pytest.raises(le.ScenarioError, match="en erreur"):
        caller.call("A1", "p", env={"HOME": str(tmp_path)}, session_id="S1")
    assert caller.records[0].is_error and caller.guard.calls == 1


def test_credentials_left_after_the_run_are_reported_and_removed(tmp_path: Path) -> None:
    creds = tmp_path / ".claude" / ".credentials.json"
    creds.parent.mkdir(parents=True)
    creds.write_text("{}", encoding="utf-8")
    assert le.purge_credentials(tmp_path) == [creds.as_posix()]
    assert not creds.exists() and le.purge_credentials(tmp_path) == []


def test_invoker_refuses_bare_for_the_governed_run(tmp_path: Path) -> None:
    """``run_claude_headless`` reçoit ``bare=False`` et le bras ``kit-gov`` : le garde refuserait ``--bare``."""
    with pytest.raises(SystemExit, match="ne serait pas gouverné"):
        ta.run_claude_headless(tmp_path, "x", home=tmp_path, bare=True, arm="kit-gov")


def test_shims_run_the_source_tree(tmp_path: Path) -> None:
    shims = le.write_shims(tmp_path / "bin", src_dir=tmp_path / "src", python="/py/bin/python")
    assert set(shims) == {"grimoire", "grimoire-hook", "grimoire-mcp"}
    body = shims["grimoire-hook"].read_text(encoding="utf-8")
    assert f'PYTHONPATH="{(tmp_path / "src").as_posix()}"' in body
    assert "from grimoire.hosts.runtime import main" in body
    assert "\r" not in body


# ── Garde-fou de budget, par appel ───────────────────────────────────────────


def test_resumed_call_costs_the_session_delta_and_gets_the_remaining_cap(tmp_path: Path) -> None:
    invoker = FakeInvoker([_outcome(0.30, session="S2"), _outcome(0.75, session="S2")])
    caller = _caller(tmp_path, invoker, cap=2.0)
    env = {"HOME": str(tmp_path / "home")}
    first = caller.call("U1", "p", env=env, session_id="S2")
    second = caller.call("U2", "p", env=env, session_id="S2", resume=True)
    assert first.cost_usd == pytest.approx(0.30)
    assert second.cost_usd == pytest.approx(0.45)  # 0,75 cumulé - 0,30 déjà compté
    assert invoker.calls[0]["args"][:2] == ["--max-budget-usd", "2.0000"]
    assert invoker.calls[1]["args"][:2] == ["--max-budget-usd", "1.7000"]
    assert "--resume" in invoker.calls[1]["args"]
    assert caller.guard.spent_usd == pytest.approx(0.75)


def test_unknown_cost_stops_and_blocks_every_later_call(tmp_path: Path) -> None:
    invoker = FakeInvoker([_outcome(None)])
    caller = _caller(tmp_path, invoker)
    with pytest.raises(le.BudgetError, match="inconnu"):
        caller.call("A1", "p", env={"HOME": str(tmp_path)}, session_id="S1")
    assert caller.records[0].cost_known is False and caller.records[0].cost_usd is None
    with pytest.raises(le.BudgetError):
        caller.call("A2", "p", env={"HOME": str(tmp_path)}, session_id="S1b")
    assert len(invoker.calls) == 1


def test_a_shared_ledger_at_the_cap_launches_nothing(tmp_path: Path) -> None:
    ledger = tmp_path / "budget.jsonl"
    rows = [{"event": "begin", "call_id": "x"}, {"event": "end", "call_id": "x", "cost_usd": 5.0, "cost_known": True}]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
    invoker = FakeInvoker([_outcome(0.1)])
    caller = _caller(tmp_path, invoker, cap=5.0, ledger=ledger)
    with pytest.raises(le.BudgetError, match="plafond atteint"):
        caller.call("A1", "p", env={"HOME": str(tmp_path)}, session_id="S1")
    assert invoker.calls == []


# ── Verdicts, lus dans les journaux ──────────────────────────────────────────


def _record(phase: str, start: str, end: str, session: str = "S2") -> Any:
    return le.CallRecord(
        phase=phase, session_id=session, attended=False, started_at=start, ended_at=end, max_budget_usd=1.0,
        cost_usd=0.1, cost_known=True, session_total_usd=0.1, num_turns=1, input_tokens=0, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0, wall_seconds=1.0, terminated_reason="completed",
        result_session_id=session, is_error=False,
    )


def _stop(verdict: str, at: str, session: str = "S2", refusal: str = "task_unparked") -> dict[str, Any]:
    tags = ["stop", verdict, *([f"refusal:{refusal}"] if verdict == "block" else [])]
    return {"event": "stop", "verdict": verdict, "session_id": session, "at": at, "tags": tags}


_GREEN = le.Step("final-gate-check", [], 0, "OK evidence gates for task T (state: review)")
_RED = le.Step("final-gate-check", [], 1, "FAIL evidence gates for task T")
_CALLS = [
    _record("U1-close-without-proof", "2026-10-19T10:00:00+00:00", "2026-10-19T10:01:00+00:00"),
    _record("U2-close-with-proof", "2026-10-19T10:02:00+00:00", "2026-10-19T10:05:00+00:00"),
]


_UNPROVEN = {"task_state": "claimed", "ledger_evidence_packs": 0, "verdicts": [], "test_run_recorded": False}


def _verdicts(
    decisions: list[dict[str, Any]], gate: Any = _GREEN, state: str = "needs_verification", after_u1: Any = _UNPROVEN
) -> Any:
    return le.verdicts(
        calls=_CALLS, decisions=decisions, gate_check=gate, final_state=state, grant_active=True,
        approval_attested=True, grant_attested=True, hooks_ok=True, proof_after_u1=after_u1,
    )


def test_block_then_release_with_green_gate_passes() -> None:
    v = _verdicts(
        [
            _stop("block", "2026-10-19T10:00:30+00:00"),
            _stop("allow", "2026-10-19T10:00:50+00:00"),
            _stop("allow", "2026-10-19T10:04:00+00:00"),
        ]
    )
    assert v["proof_hook_blocked_closure_without_proof"] and v["gate_closed_with_proof"] and v["passed"]
    assert v["u1_stop_verdicts"] == ["block", "allow"] and v["u2_first_stop_allowed"]
    assert v["u1_first_refusal"] == "task_unparked" and v["u2_refusals"] == []


@pytest.mark.parametrize(
    ("decisions", "gate", "state", "failed"),
    [
        # Aucun refus pendant U1 : le hook n'a rien bloqué.
        ([_stop("allow", "2026-10-19T10:00:30+00:00"), _stop("allow", "2026-10-19T10:04:00+00:00")], _GREEN,
         "needs_verification", "proof_hook_blocked_closure_without_proof"),
        # Le refus vient d'une autre session : il ne compte pas.
        ([_stop("block", "2026-10-19T10:00:30+00:00", session="other"), _stop("allow", "2026-10-19T10:04:00+00:00")],
         _GREEN, "needs_verification", "proof_hook_blocked_closure_without_proof"),
        # Gate final rouge.
        ([_stop("block", "2026-10-19T10:00:30+00:00"), _stop("allow", "2026-10-19T10:04:00+00:00")], _RED,
         "needs_verification", "gate_closed_with_proof"),
        # Tâche jamais rendue.
        ([_stop("block", "2026-10-19T10:00:30+00:00"), _stop("allow", "2026-10-19T10:04:00+00:00")], _GREEN,
         "running", "gate_closed_with_proof"),
        # Dernier Stop de U2 encore refusé.
        ([_stop("block", "2026-10-19T10:00:30+00:00"), _stop("block", "2026-10-19T10:04:00+00:00")], _GREEN,
         "needs_verification", "gate_closed_with_proof"),
        # Aucune décision Stop enregistrée : absent = faux.
        ([], None, None, "gate_closed_with_proof"),
    ],
)
def test_each_missing_proof_fails_the_scenario(decisions: Any, gate: Any, state: Any, failed: str) -> None:
    v = _verdicts(decisions, gate, state)
    assert v[failed] is False and v["passed"] is False


@pytest.mark.parametrize(
    "after_u1",
    [
        None,  # instantané absent : rien ne dit que la clôture était sans preuve
        {**_UNPROVEN, "ledger_evidence_packs": 1},  # un pack existait déjà : le refus ne prouve rien
        {**_UNPROVEN, "task_state": "needs_verification"},  # tâche déjà rendue
    ],
)
def test_a_refusal_counts_only_if_the_closure_was_really_unproven(after_u1: Any) -> None:
    v = _verdicts(
        [_stop("block", "2026-10-19T10:00:30+00:00"), _stop("allow", "2026-10-19T10:04:00+00:00")], after_u1=after_u1
    )
    failed = "proof_hook_blocked_closure_without_proof"
    assert v[failed] is False and v["passed"] is False


def test_hooks_emitted_requires_the_kit_hooks_for_claude(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)

    def write(events: list[str]) -> None:
        hooks = {e: [{"hooks": [{"type": "command", "command": f"grimoire-hook --host claude --event {e}"}]}] for e in events}
        settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")

    write(["SessionStart", "PreToolUse", "UserPromptSubmit", "Stop"])
    assert le.hooks_emitted(tmp_path)["ok"] is True
    write(["SessionStart", "PreToolUse", "UserPromptSubmit"])
    assert le.hooks_emitted(tmp_path)["ok"] is False


def test_hook_decisions_and_ledger_events_are_read_from_the_kit_journals(tmp_path: Path) -> None:
    traces = tmp_path / "_grimoire-output" / "traces" / "traces.jsonl"
    traces.parent.mkdir(parents=True)
    row = {"run_id": "S2", "task_id": "T", "recipe_id": "grimoire.evidence-gate", "outcome": "failure",
           "tags": ["stop", "block"], "started_at": "2026-10-19T10:00:30+00:00"}
    traces.write_text(json.dumps(row) + "\n{broken\n", encoding="utf-8", newline="\n")
    decisions = le.hook_decisions(tmp_path)
    assert decisions[0]["event"] == "stop" and decisions[0]["verdict"] == "block"
    assert len(decisions) == 2  # la ligne illisible reste visible, jamais ignorée
    events = tmp_path / "_grimoire-runtime-output" / "ledger" / "events.jsonl"
    events.parent.mkdir(parents=True)
    rows = [
        {"event_type": "task.transitioned", "entity_id": "T", "payload": {"from_state": "ready", "to_state": "claimed"}},
        {"event_type": "task.transitioned", "entity_id": "T", "payload": {"from_state": "running", "to_state": "needs_verification"}},
        {"event_type": "task.transitioned", "entity_id": "U", "payload": {"from_state": "x", "to_state": "closed"}},
    ]
    events.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
    assert le.task_state(le.ledger_events(tmp_path, "T")) == "needs_verification"


# ── Secrets ──────────────────────────────────────────────────────────────────


def test_secret_scan_reports_paths_never_contents(tmp_path: Path) -> None:
    (tmp_path / "a.jsonl").write_text('{"x": "sk-ant-oat01-ABCDEFGHIJKLMNOP"}\n', encoding="utf-8")
    (tmp_path / "b.txt").write_text("rien de secret, sk-ant- trop court\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("cle-litterale-XYZ\n", encoding="utf-8")
    hits = le.secret_hits([tmp_path], extra=[b"cle-litterale-XYZ"])
    assert hits == [(tmp_path / "a.jsonl").as_posix(), (tmp_path / "c.txt").as_posix()]
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    assert le.leftover_credentials(home) == []
    (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert le.leftover_credentials(home) == [(home / ".claude" / ".credentials.json").as_posix()]


def test_no_credentials_to_copy_stops_before_any_call(tmp_path: Path) -> None:
    """Le ``HOME`` vu par la suite est isolé : sans identifiant à copier, aucun appel n'est lancé."""
    invoker = FakeInvoker([_outcome(0.1)])
    cfg = le.Config(workspace=tmp_path / "ws", max_total_usd=1.0, auth="oauth-copy", real_home=tmp_path / "vide")
    report = le.run_scenario(cfg, invoke=invoker)
    assert invoker.calls == [] and "aucun identifiant à copier" in report["stopped"]


def test_main_refuses_to_start_without_a_cap(tmp_path: Path) -> None:
    assert le.main(["--workspace", str(tmp_path / "ws"), "--max-total-usd", "0"]) == 2
    assert not (tmp_path / "ws").exists()


# ── Le live ne tourne jamais par défaut ──────────────────────────────────────


def test_live_test_is_deselected_by_default_and_skips_without_a_cap(tmp_path: Path) -> None:
    """Collecte réelle avec l'``addopts`` du projet : le test live est désélectionné ; sélectionné sans
    plafond, il est ignoré en le disant ; avec ``GRIMOIRE_REQUIRE_LIVE=1``, il échoue."""
    live = ROOT / "tests" / "live" / "test_claude_code_live.py"
    base = [sys.executable, "-m", "pytest", str(live), "-p", "no:cacheprovider", "--rootdir", str(ROOT)]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GRIMOIRE_LIVE", "GRIMOIRE_REQUIRE"))}
    env["PYTHONPATH"] = str(ROOT / "src")

    runs: list[str] = []

    def run(*extra: str, **more: str) -> subprocess.CompletedProcess[str]:
        runs.append("x")
        return subprocess.run(
            [*base, f"--basetemp={tmp_path / f'run{len(runs)}'}", *extra], cwd=ROOT, env={**env, **more},
            capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
        )

    default = run()
    assert "1 deselected" in default.stdout, default.stdout + default.stderr
    skipped = run("-m", "live_host", "-rs")
    assert skipped.returncode == 0 and "GRIMOIRE_LIVE_CLAUDE_MAX_USD absent" in skipped.stdout, skipped.stdout
    required = run("-m", "live_host", GRIMOIRE_REQUIRE_LIVE="1")
    assert required.returncode == 1 and "live exigé" in required.stdout, required.stdout


# ── Bout en bout sans modèle ─────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="relais POSIX (#!/bin/sh) : le scénario live tourne sous Linux/macOS")
def test_without_a_real_human_turn_the_grant_is_refused_and_nothing_else_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le vrai ``grimoire`` prépare un projet governed ; un faux appel n'écrit aucun tour humain.

    L'octroi reste donc non attesté (le hook ``UserPromptSubmit`` n'a jamais tourné) : le scénario
    s'arrête après le premier appel, le coût de cet appel est compté, rien n'est déclaré réussi.
    Aucun identifiant n'est copié : la copie OAuth est remplacée le temps du test.
    """
    copies: list[Path] = []

    @contextlib.contextmanager
    def _no_copy(home: Path, **_: Any) -> Iterator[None]:
        copies.append(home)
        yield None

    monkeypatch.setattr(ta, "credentials_provisioned", _no_copy)
    invoker = FakeInvoker([_outcome(0.12, session="S1")])
    source = tmp_path / "operator"
    (source / ".claude").mkdir(parents=True)
    (source / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    cfg = le.Config(workspace=tmp_path / "ws", max_total_usd=1.0, auth="oauth-copy", timeout_s=30, real_home=source)
    report = le.run_scenario(cfg, invoke=invoker)
    assert report["hooks_emitted"]["ok"] is True
    steps = {s["name"]: s for s in report["steps"]}
    assert steps["grant-challenge"]["returncode"] == 3
    assert steps["grant-attest"]["returncode"] == 3
    assert report["stopped"].startswith("scenario: octroi non attesté")
    assert len(invoker.calls) == 1
    env = invoker.calls[0]["env"]
    assert "GRIMOIRE_UNATTENDED" not in env and "GRIMOIRE_SESSION_ID" not in env
    assert "--session-id" in invoker.calls[0]["args"] and "--bare" not in invoker.calls[0]["args"]
    assert report["cost"]["spent_this_run_usd"] == pytest.approx(0.12)
    assert report["verdicts"]["passed"] is False
    assert report["verdicts"]["final_task_state"] == "ready"
    assert report["secrets"] == {"leftover_credentials": [], "token_hits": []}
    assert copies == [tmp_path / "ws" / "home"]  # une copie par appel, dans le HOME isolé seulement
