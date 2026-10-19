"""B-06 : bras ``aidlc`` du banc, dry-run sans modèle et garde-fou de budget fermé par défaut.

Aucun appel ``claude`` : le pilote reçoit un faux invocateur, l'installeur et
``aidlc config`` un faux exécuteur, et ``subprocess.Popen`` lève s'il voit
``claude``. Le clone de référence est un dépôt git jetable dont le commit du tag
``v2.11.0`` remplace l'empreinte épinglée le temps du test.
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"


def _load() -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location("bench_aidlc_arm", BENCH / "aidlc_arm.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


aa = _load()
ta = aa.ta


# ── Collaborateurs factices ──────────────────────────────────────────────────


_REAL_POPEN = subprocess.Popen


def _no_claude(cmd: Any, *args: Any, **kwargs: Any) -> Any:
    """``subprocess.Popen`` de remplacement : tout lancement de ``claude`` est une faute du test, ``git`` passe."""
    argv = [cmd] if isinstance(cmd, str) else list(cmd)
    if any(Path(str(a)).name.startswith("claude") for a in argv[:1]):
        raise AssertionError(f"un modèle a été lancé : {argv[:3]}")
    return _REAL_POPEN(cmd, *args, **kwargs)


@pytest.fixture(autouse=True)
def _never_launch_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", _no_claude)
    monkeypatch.setattr(aa, "detect_claude_code_version", lambda **k: "9.9.9 (Claude Code)")


class FakeInvoker:
    """Rend des ``RunOutcome`` scriptés et note chaque appel (prompt, reprise, environnement)."""

    def __init__(self, outcomes: list[ta.RunOutcome], *, on_call: Any = None) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.on_call = on_call

    def __call__(
        self,
        task_dir: Path,
        prompt: str,
        *,
        env: dict[str, str],
        timeout_s: int,
        model: str | None,
        resume: str | None,
        log_path: Path,
    ) -> ta.RunOutcome:
        self.calls.append(
            {
                "prompt": prompt,
                "resume": resume,
                "unattended": env.get("AIDLC_UNATTENDED"),
                "timeout_s": timeout_s,
                "model": model,
            }
        )
        if self.on_call is not None:
            self.on_call(len(self.calls), task_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "system", "subtype": "init"}) + "\n")
        return self.outcomes.pop(0)


def _outcome(cost: float | None, *, session: str | None = "sess-1", reason: str = "completed") -> ta.RunOutcome:
    raw: dict[str, Any] = {"type": "result", "session_id": session} if session else {"type": "result"}
    return ta.RunOutcome(
        total_cost_usd=cost or 0.0,
        cost_known=cost is not None,
        num_turns=4,
        wall_seconds=1.0,
        terminated_reason=reason,
        raw_result=raw,
    )


def _drive(tmp_path: Path, invoker: FakeInvoker, guard: Any, *, driver: str, replies: int = 5) -> Any:
    task_dir = tmp_path / "run0"
    task_dir.mkdir(exist_ok=True)
    return aa.drive_run(
        task_dir,
        "Lis TASK.md",
        scope="express",
        driver=driver,
        env={"HOME": str(tmp_path), "AIDLC_UNATTENDED": "1"},
        model="claude-test",
        guard=guard,
        log_path=tmp_path / "run0.stream.jsonl",
        call_prefix="python/x/run0",
        invoke=invoker,
        max_person_replies=replies,
    )


# ── Plafond : refus sans plafond explicite ───────────────────────────────────


@pytest.mark.parametrize("raw", [None, "", "  ", "0", "-1", "nan", "inf", "-inf", "abc", True])
def test_parse_cap_refuses_missing_or_non_positive_or_non_finite(raw: object) -> None:
    with pytest.raises(aa.BudgetError):
        aa.parse_cap(raw)


def test_parse_cap_accepts_a_positive_amount() -> None:
    assert aa.parse_cap("12.5") == 12.5


def test_run_without_cap_is_refused_before_any_work(tmp_path: Path) -> None:
    invoker = FakeInvoker([])
    workspace = tmp_path / "ws"
    with pytest.raises(SystemExit) as exc:
        aa.main(
            [
                "--run",
                "--driver",
                "unattended",
                "--model",
                "claude-test",
                "--aidlc-clone",
                str(tmp_path),
                "--workspace",
                str(workspace),
            ],
            invoke=invoker,
        )
    assert exc.value.code == 2
    assert invoker.calls == []
    assert not workspace.exists()


def test_run_without_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        aa.main(
            ["--run", "--driver", "unattended", "--max-total-usd", "5", "--aidlc-clone", str(tmp_path)],
            invoke=FakeInvoker([]),
        )
    assert exc.value.code == 2


# ── Garde-fou : arrêt au plafond, coût inconnu bloquant ──────────────────────


def test_guard_stops_the_run_once_known_spend_reaches_the_cap(tmp_path: Path) -> None:
    guard = aa.BudgetGuard.open("1.0", tmp_path / "budget.jsonl")
    invoker = FakeInvoker([_outcome(0.6), _outcome(0.6), _outcome(0.6)])

    drive = _drive(tmp_path, invoker, guard, driver=aa.DRIVER_SCRIPTED)

    assert len(invoker.calls) == 2  # le troisième appel n'est jamais lancé
    assert drive.terminated_reason == "budget"
    assert "plafond atteint" in (drive.stopped_by_budget or "")
    assert math.isclose(guard.spent_usd, 1.2)
    with pytest.raises(aa.BudgetError):
        guard.check_before_call()


def test_unknown_cost_never_counts_as_zero_and_blocks_the_next_call(tmp_path: Path) -> None:
    guard = aa.BudgetGuard.open("100", tmp_path / "budget.jsonl")
    invoker = FakeInvoker([_outcome(None), _outcome(0.1)])

    drive = _drive(tmp_path, invoker, guard, driver=aa.DRIVER_SCRIPTED)

    assert len(invoker.calls) == 1
    assert drive.terminated_reason == "budget"
    assert "coût inconnu" in (drive.stopped_by_budget or "")
    assert drive.cost_known is False
    assert guard.spent_usd == 0.0


def test_ledger_replay_resumes_the_spend_instead_of_restarting_from_zero(tmp_path: Path) -> None:
    ledger = tmp_path / "budget.jsonl"
    first = aa.BudgetGuard.open("3", ledger)
    for i, cost in enumerate((1.0, 2.0)):
        first.begin(f"c{i}")
        first.record(f"c{i}", cost, True)

    reopened = aa.BudgetGuard.open("3", ledger)
    assert reopened.spent_usd == 3.0
    with pytest.raises(aa.BudgetError, match="plafond atteint"):
        reopened.check_before_call()
    assert aa.BudgetGuard.open("10", ledger).snapshot()["blocked"] is False


def test_a_call_begun_but_never_ended_blocks_on_replay(tmp_path: Path) -> None:
    ledger = tmp_path / "budget.jsonl"
    crashed = aa.BudgetGuard.open("10", ledger)
    crashed.begin("python/x/run0#0")  # processus tué pendant l'appel : aucune ligne « end »

    reopened = aa.BudgetGuard.open("10", ledger)
    with pytest.raises(aa.BudgetError, match="jamais terminé"):
        reopened.check_before_call()


def test_an_unreadable_ledger_line_blocks(tmp_path: Path) -> None:
    ledger = tmp_path / "budget.jsonl"
    ledger.write_text(
        '{"event": "end", "call_id": "a", "cost_usd": 0.5, "cost_known": true}\n{tronqué\n', encoding="utf-8"
    )
    with pytest.raises(aa.BudgetError, match="coût inconnu"):
        aa.BudgetGuard.open("10", ledger).check_before_call()


# ── Pilotes ──────────────────────────────────────────────────────────────────


def test_unattended_driver_declares_aidlc_unattended_and_makes_one_call(tmp_path: Path) -> None:
    guard = aa.BudgetGuard.open("10", None)
    invoker = FakeInvoker([_outcome(0.2), _outcome(0.2)])

    drive = _drive(tmp_path, invoker, guard, driver=aa.DRIVER_UNATTENDED)

    assert [c["unattended"] for c in invoker.calls] == ["1"]
    assert invoker.calls[0]["prompt"].startswith("/aidlc express ")
    assert drive.person_replies == 0


def test_scripted_driver_resumes_the_session_with_the_fixed_reply_until_workflow_completed(tmp_path: Path) -> None:
    def complete_on_third_call(n: int, task_dir: Path) -> None:
        if n == 3:
            audit = task_dir / "aidlc" / "audit"
            audit.mkdir(parents=True)
            (audit / "shard.md").write_text("## Workflow Completed\n**Event**: WORKFLOW_COMPLETED\n", encoding="utf-8")

    guard = aa.BudgetGuard.open("10", None)
    invoker = FakeInvoker([_outcome(0.2, session="s-42")] * 5, on_call=complete_on_third_call)

    drive = _drive(tmp_path, invoker, guard, driver=aa.DRIVER_SCRIPTED)

    assert [c["unattended"] for c in invoker.calls] == [None, None, None]  # AIDLC_UNATTENDED retiré
    assert [c["prompt"] for c in invoker.calls[1:]] == [aa.SCRIPTED_REPLY, aa.SCRIPTED_REPLY]
    assert [c["resume"] for c in invoker.calls] == [None, "s-42", "s-42"]
    assert drive.workflow_completed is True
    assert drive.person_replies == 2
    assert math.isclose(drive.total_cost_usd, 0.6)


def test_scripted_driver_stops_at_the_reply_cap(tmp_path: Path) -> None:
    invoker = FakeInvoker([_outcome(0.1)] * 10)
    drive = _drive(tmp_path, invoker, aa.BudgetGuard.open("10", None), driver=aa.DRIVER_SCRIPTED, replies=2)
    assert len(invoker.calls) == 3
    assert drive.terminated_reason == "reply_cap"


def test_planned_command_is_never_bare() -> None:
    cmd = aa.planned_claude_command("Approved.", model="m", resume="s")
    assert "--bare" not in cmd
    assert cmd[-2:] == ["--resume", "s"]


# ── Dry-run de bout en bout : aucun modèle, pré-enregistrement écrit ─────────


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def reference_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clone = tmp_path / "aidlc-workflows"
    (clone / "core" / "scopes").mkdir(parents=True)
    (clone / "scripts").mkdir()
    (clone / "core" / "scopes" / "aidlc-express.md").write_text(
        "---\nname: express\nplan_approval: off\nreview_cap: none\n---\n", encoding="utf-8"
    )
    (clone / "scripts" / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (clone / "scripts" / "install.ps1").write_text("exit 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(clone)], check=True, capture_output=True)
    for args in (
        ("add", "."),
        ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ref"),
        ("tag", "v2.11.0"),
    ):
        _git(clone, *args)
    monkeypatch.setattr(aa, "AIDLC_TAG_COMMIT", _git(clone, "rev-parse", "HEAD"))
    return clone


@pytest.fixture
def bench_root(tmp_path: Path) -> Path:
    """Catalogue minimal au format Exercism : 5 exercices par langue (le tirage en exige 5)."""
    root = tmp_path / "polyglot-benchmark"
    for language in ta.LANGUAGES:
        for i in range(ta.PER_LANGUAGE):
            ex = root / language / "exercises" / "practice" / f"ex-{i}"
            (ex / ".meta").mkdir(parents=True)
            (ex / ".docs").mkdir()
            (ex / ".meta" / "config.json").write_text(
                json.dumps({"files": {"solution": ["ex.py"], "test": ["ex_test.py"]}}), encoding="utf-8"
            )
            (ex / ".docs" / "instructions.md").write_text(f"# ex-{i}\n", encoding="utf-8")
            (ex / "ex.py").write_text("def solve():\n    pass\n", encoding="utf-8")
            (ex / "ex_test.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return root


class FakeRunner:
    """Joue l'installeur, ``aidlc version`` et ``aidlc config`` ; délègue ``git`` au vrai sous-processus."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(
        self, cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        cmd = list(cmd)
        assert "claude" not in Path(cmd[0]).name
        self.commands.append(cmd)
        assert env is not None
        if cmd[0] in ("sh", "powershell"):
            bin_dir = Path(env["AIDLC_BIN_DIR"])
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "aidlc").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:] == ["version"]:
            return subprocess.CompletedProcess(cmd, 0, "aidlc 2.11.0\n", "")
        if cmd[1] == "config":
            assert cwd is not None
            (cwd / ".claude").mkdir(exist_ok=True)
            (cwd / ".claude" / "settings.json").write_text(
                json.dumps({"hooks": {"UserPromptSubmit": [], "Stop": []}}), encoding="utf-8"
            )
            return subprocess.CompletedProcess(cmd, 0, "configured\n", "")
        raise AssertionError(f"commande inattendue : {cmd}")


def _dry_run_args(tmp_path: Path, clone: Path, root: Path, *, driver: str) -> list[str]:
    return [
        "--dry-run",
        "--driver",
        driver,
        "--aidlc-clone",
        str(clone),
        "--bench-root",
        str(root),
        "--workspace",
        str(tmp_path / "ws"),
        "--model",
        "claude-test",
        "--max-total-usd",
        "20",
        "--tasks",
        "1",
    ]


def test_dry_run_prepares_installs_and_preregisters_without_any_model_call(
    tmp_path: Path, reference_clone: Path, bench_root: Path
) -> None:
    invoker, runner = FakeInvoker([]), FakeRunner()

    rc = aa.main(
        _dry_run_args(tmp_path, reference_clone, bench_root, driver=aa.DRIVER_SCRIPTED), invoke=invoker, run=runner
    )

    assert rc == 0
    assert invoker.calls == []
    state = tmp_path / "ws" / "state"
    prereg = json.loads((state / aa.PREREGISTRATION).read_text(encoding="utf-8"))
    assert prereg["config"]["aidlc_version"] == "2.11.0"
    assert prereg["config"]["special_configuration"] is True
    assert prereg["config"]["scope_frontmatter"]["plan_approval"] == "off"
    planned = json.loads((state / aa.PLANNED_COMMANDS).read_text(encoding="utf-8"))
    assert planned["runs"][0]["reply_call"][-2:] == ["--resume", "<session_id>"]
    assert not (state / "results.jsonl").exists()
    assert any(c[1:3] == ["config", "--harness"] for c in runner.commands)
    home = tmp_path / "ws" / "homes" / "aidlc"
    assert all(Path(c[0]).is_relative_to(home) for c in runner.commands if Path(c[0]).name == "aidlc")


def test_dry_run_refuses_a_clone_whose_tag_is_not_the_pinned_commit(
    tmp_path: Path, reference_clone: Path, bench_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(aa, "AIDLC_TAG_COMMIT", "0" * 40)
    assert (
        aa.main(
            _dry_run_args(tmp_path, reference_clone, bench_root, driver=aa.DRIVER_UNATTENDED),
            invoke=FakeInvoker([]),
            run=FakeRunner(),
        )
        == 1
    )


def test_run_refuses_without_preregistration_or_with_a_different_configuration(
    tmp_path: Path, reference_clone: Path, bench_root: Path
) -> None:
    run_args = [
        "--run",
        "--driver",
        aa.DRIVER_SCRIPTED,
        "--aidlc-clone",
        str(reference_clone),
        "--bench-root",
        str(bench_root),
        "--workspace",
        str(tmp_path / "ws"),
        "--model",
        "claude-test",
        "--max-total-usd",
        "20",
        "--tasks",
        "1",
    ]
    invoker = FakeInvoker([])

    assert aa.main(run_args, invoke=invoker, run=FakeRunner()) == 2  # pas de pré-enregistrement

    assert (
        aa.main(
            _dry_run_args(tmp_path, reference_clone, bench_root, driver=aa.DRIVER_UNATTENDED),
            invoke=invoker,
            run=FakeRunner(),
        )
        == 0
    )
    assert aa.main(run_args, invoke=invoker, run=FakeRunner()) == 2  # pilote différent du pré-enregistrement
    assert invoker.calls == []


def test_run_refuses_to_start_when_the_ledger_already_reaches_the_cap(
    tmp_path: Path, reference_clone: Path, bench_root: Path
) -> None:
    assert (
        aa.main(
            _dry_run_args(tmp_path, reference_clone, bench_root, driver=aa.DRIVER_SCRIPTED),
            invoke=FakeInvoker([]),
            run=FakeRunner(),
        )
        == 0
    )
    ledger = tmp_path / "ws" / "state" / aa.BUDGET_LEDGER
    ledger.write_text(
        json.dumps({"event": "end", "call_id": "old", "cost_usd": 20.0, "cost_known": True}) + "\n", encoding="utf-8"
    )
    invoker = FakeInvoker([])
    args = [
        "--run",
        "--driver",
        aa.DRIVER_SCRIPTED,
        "--aidlc-clone",
        str(reference_clone),
        "--bench-root",
        str(bench_root),
        "--workspace",
        str(tmp_path / "ws"),
        "--model",
        "claude-test",
        "--max-total-usd",
        "20",
        "--tasks",
        "1",
    ]

    assert aa.main(args, invoke=invoker, run=FakeRunner()) == 2
    assert invoker.calls == []


# ── Estimation ────────────────────────────────────────────────────────────────


def test_lot_l_prices_reproduce_a_measured_run_cost() -> None:
    """Ligne figée du lot L (``modelUsage`` claude-opus-5-5 d'un run) : le tarif ajusté la retrouve à 1e-9 près."""
    usage = {
        "inputTokens": 6,
        "outputTokens": 1804,
        "cacheReadInputTokens": 65620,
        "cacheCreationInputTokens": 34942,
        "costUSD": 0.328764,
    }
    p = aa.LOT_L_PRICES_PER_MTOK
    cost = (
        usage["inputTokens"] * p["input"]
        + usage["outputTokens"] * p["output"]
        + usage["cacheReadInputTokens"] * p["cache_read"]
        + usage["cacheCreationInputTokens"] * p["cache_write"]
    ) / 1e6
    assert math.isclose(cost, usage["costUSD"], abs_tol=1e-9)


def test_campaign_estimate_multiplies_runs_and_adds_the_measured_arms() -> None:
    est = aa.campaign_estimate(20, k=3, driver=aa.DRIVER_SCRIPTED)
    low, high = est["aidlc_per_run_usd"]
    assert low < high
    assert est["runs_per_arm"] == 60
    assert est["nu_usd"] == 10.15 and est["kit_gov_usd"] == 12.69  # lot L, 60 runs par bras
    assert math.isclose(est["total_usd"][0], round(60 * low + 10.15 + 12.69, 2), abs_tol=0.02)


def test_scope_stage_counts_match_the_pinned_scope_files() -> None:
    assert len(aa.SCOPE_STAGES["express"]) == 10
    assert len(aa.SCOPE_STAGES["bugfix"]) == 9


# ── three_arms.run_claude_headless : reprise et journal ajouté ───────────────


class _OneResultPopen:
    def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
        type(self).last_cmd = list(cmd)
        line = json.dumps({"type": "result", "total_cost_usd": 0.05, "num_turns": 2, "session_id": "s-1", "usage": {}})
        stdout.write((line + "\n").encode("utf-8"))
        stdout.flush()

    last_cmd: ClassVar[list[str]] = []

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


class _NoResultPopen(_OneResultPopen):
    def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
        type(self).last_cmd = list(cmd)
        stdout.write(b'{"type": "system", "subtype": "init"}\n')
        stdout.flush()


def test_run_claude_headless_appends_and_reads_only_its_own_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    log = tmp_path / "run0.stream.jsonl"
    log.write_text(json.dumps({"type": "result", "total_cost_usd": 9.0}) + "\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "Popen", _NoResultPopen)

    outcome = ta.run_claude_headless(
        task_dir,
        "Approved.",
        home=tmp_path,
        timeout_s=5,
        poll_interval=0.0,
        log_path=log,
        append_log=True,
        extra_args=["--resume", "s-1"],
    )

    assert not outcome.cost_known  # le 9.0 d'un appel précédent n'est jamais repris
    assert outcome.total_cost_usd == 0.0
    assert outcome.terminated_reason == "error"
    assert "9.0" in log.read_text(encoding="utf-8")  # journal conservé, pas écrasé
    assert _NoResultPopen.last_cmd[-2:] == ["--resume", "s-1"]


def test_run_claude_headless_bare_false_overrides_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    monkeypatch.setattr(subprocess, "Popen", _OneResultPopen)

    ta.run_claude_headless(
        task_dir, "x", home=tmp_path, timeout_s=5, poll_interval=0.0, auth_mode="api-key", bare=False
    )
    assert "--bare" not in _OneResultPopen.last_cmd
    ta.run_claude_headless(task_dir, "x", home=tmp_path, timeout_s=5, poll_interval=0.0, auth_mode="api-key")
    assert "--bare" in _OneResultPopen.last_cmd  # comportement inchangé pour les autres bras
