"""Lot M exécutable (B-08) : --k, plafond de campagne, bloc K (tâches du kit). Aucun appel modèle (fakes)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"


def _load(name: str, filename: str) -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bg = _load("budget_guard", "budget_guard.py")
kl = _load("kit_lot", "kit_lot.py")
ta = _load("three_arms_lot_m_test", "three_arms.py")
aa = _load("aidlc_arm_lot_m_test", "aidlc_arm.py")

STATEMENT = "# Défaut\n\n" + "La fonction `double` rend une valeur fausse pour tout entier positif. " * 5


# ── Dépôt git synthétique : un parent fautif, un correctif ──────────────────


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return done.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def kit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """(dépôt, parent, correctif) : ``double`` est faux au parent, le test de régression arrive au correctif."""
    repo = tmp_path / "kit-src"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    _write(repo / "src" / "mod" / "__init__.py", "def double(x):\n    return x + 1\n")
    _write(repo / "tests" / "test_double.py", "def test_old():\n    assert True\n")
    _write(repo / "pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "parent")
    parent = _git(repo, "rev-parse", "HEAD")
    _write(repo / "src" / "mod" / "__init__.py", "def double(x):\n    return 2 * x\n")
    _write(
        repo / "tests" / "test_double.py",
        "from mod import double\n\n\ndef test_double():\n    assert double(3) == 6\n",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix")
    return repo, parent, _git(repo, "rev-parse", "HEAD")


def _tasks_file(tmp_path: Path, parent: str, fix: str, **overrides: Any) -> Path:
    entry = {
        "id": "K01",
        "slug": "k01-double",
        "issue": 1,
        "fix_pr": 2,
        "fix_commit": fix,
        "parent_commit": parent,
        "test_files": ["tests/test_double.py"],
        "statement": STATEMENT,
        **overrides,
    }
    path = tmp_path / "kit-tasks.json"
    path.write_text(
        json.dumps({"schema": kl.SCHEMA, "test_requirements": ["pytest"], "tasks": [entry]}), encoding="utf-8"
    )
    return path


def _k_catalog(tmp_path: Path, kit_repo: tuple[Path, str, str]) -> tuple[Any, Path]:
    repo, parent, fix = kit_repo
    tasks_file = _tasks_file(tmp_path, parent, fix)
    root = kl.materialize(kl.load_kit_tasks(tasks_file), repo, tmp_path / "ws" / "kit-lot")
    env_file = kl.write_offline_env_file(root)
    # Le Python du lanceur sert de venv de test ici (verify_test_python est testé à part).
    (root / "test-env.json").write_text(
        json.dumps({"python": sys.executable, "env_file": str(env_file), "tasks_file": str(tasks_file)}),
        encoding="utf-8",
    )
    (task,) = ta.discover_catalog(root, (kl.LANGUAGE,))
    return task, tasks_file


# ── Fichier des tâches K ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"slug": "double"}, "slug"),
        ({"fix_commit": "abc"}, "sha complets"),
        ({"test_files": ["../x.py"]}, "chemin de test invalide"),
        ({"statement": "trop court"}, "trop court"),
        ({"pytest_k": "  "}, "pytest_k"),
    ],
)
def test_load_kit_tasks_refuses_malformed_entries(
    tmp_path: Path, kit_repo: tuple[Path, str, str], override: dict[str, Any], match: str
) -> None:
    _repo, parent, fix = kit_repo
    with pytest.raises(kl.KitLotError, match=match):
        kl.load_kit_tasks(_tasks_file(tmp_path, parent, fix, **override))


def test_statement_that_names_the_fix_is_refused(tmp_path: Path, kit_repo: tuple[Path, str, str]) -> None:
    _repo, parent, fix = kit_repo
    for leak in (f"corrigé par {fix[:9]}", "voir la PR #2", "voir https://github.com/o/r/pull/9"):
        with pytest.raises(kl.KitLotError, match="révèle le correctif"):
            kl.load_kit_tasks(_tasks_file(tmp_path, parent, fix, statement=STATEMENT + leak))


def test_versioned_kit_tasks_file_is_valid_and_complete() -> None:
    tasks = kl.load_kit_tasks(kl.DEFAULT_TASKS_FILE)
    assert [t.id for t in tasks] == [f"K{i:02d}" for i in (*range(1, 16), 17)]
    named = {t.id: t.statement for t in tasks}
    assert "GatesFileError" in named["K05"]
    assert "tool_pattern_matches" in named["K09"]
    assert "is_read_only_command" in named["K10"]
    assert kl.load_test_requirements(kl.DEFAULT_TASKS_FILE)


def test_reviewed_statements_keep_k08_narrow_and_k09_discriminating() -> None:
    """Relecture du 2026-10-20 : K08 ne dicte plus l'algorithme du correctif, K09 sélectionne les tests de l'interface nommée."""
    tasks = {t.id: t for t in kl.load_kit_tasks(kl.DEFAULT_TASKS_FILE)}
    for leaked in ("scan_claim_rows", "_cells", "silent-ok", "claims.summary", "status_invalid"):
        assert leaked not in tasks["K08"].statement
    assert tasks["K08"].pytest_k and "malformed_row_is" in tasks["K08"].pytest_k
    assert tasks["K09"].pytest_k and "tool_pattern_matches" in tasks["K09"].pytest_k


def test_versioned_lot_m_names_the_sieve_and_the_k_block() -> None:
    lot = json.loads((ROOT / "docs" / "bench" / "lot-m.json").read_text(encoding="utf-8"))
    sieve = json.loads((ROOT / "docs" / "bench" / "lot-m-polyglot-sieve.json").read_text(encoding="utf-8"))
    k_ids = [t.task_id for t in kl.load_kit_tasks(kl.DEFAULT_TASKS_FILE)]
    assert lot["tasks"] == sieve["tasks"] + k_ids
    assert lot["polyglot_commit"] == sieve["polyglot_commit"]


# ── Matérialisation, dépôt de l'agent, tests cachés ──────────────────────────


def test_materialize_puts_the_parent_tree_and_the_fix_tests_in_polyglot_form(
    tmp_path: Path, kit_repo: tuple[Path, str, str]
) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    assert task.task_id == "kit/k01-double"
    assert "x + 1" in (task.exercise_dir / "src" / "mod" / "__init__.py").read_text(encoding="utf-8")
    assert "double(3) == 6" in (task.exercise_dir / "tests" / "test_double.py").read_text(encoding="utf-8")
    assert task.test_files == ("tests/test_double.py",)
    meta = kl.task_meta(task)
    assert kit_repo[2] not in json.dumps(meta)  # le sha complet du correctif n'est pas écrit à portée d'agent

    repo_dir = tmp_path / "run0"
    ta.prepare_task_repo(task, repo_dir)
    ta.assert_task_repo_clean(task, repo_dir)  # un commit, test caché absent
    assert not (repo_dir / "tests" / "test_double.py").exists()
    assert (repo_dir / "TASK.md").read_text(encoding="utf-8").startswith("# Défaut")


def test_materialize_refuses_a_parent_that_is_not_the_parent_of_the_fix(
    tmp_path: Path, kit_repo: tuple[Path, str, str]
) -> None:
    repo, _parent, fix = kit_repo
    tasks = kl.load_kit_tasks(_tasks_file(tmp_path, fix, fix))
    with pytest.raises(kl.KitLotError, match="parent"):
        kl.materialize(tasks, repo, tmp_path / "root")


def test_hidden_tests_are_red_at_parent_and_green_once_fixed(tmp_path: Path, kit_repo: tuple[Path, str, str]) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    hidden = ta.hidden_tests_dir(task, tmp_path / "hidden-tests")
    run_dir = tmp_path / "run0"
    ta.prepare_task_repo(task, run_dir)
    env = kl.run_environment_for(task, run_dir, dict(__import__("os").environ))
    assert ta.run_hidden_tests(task, run_dir, hidden, env=env)[0] is False
    _write(run_dir / "src" / "mod" / "__init__.py", "def double(x):\n    return x * 2\n")
    assert ta.run_hidden_tests(task, run_dir, hidden, env=env)[0] is True


def test_verify_tasks_accepts_a_discriminating_task_and_leaves_no_fix_tree(
    tmp_path: Path, kit_repo: tuple[Path, str, str]
) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    report = kl.verify_tasks([task], kit_repo[0])
    assert report == [{"task_id": task.task_id, "red_at_parent": True, "green_at_fix": True, "cached": False}]
    assert not any((kl.lot_root(task) / "_verify").glob("*/fix"))
    assert kl.verify_tasks([task], kit_repo[0])[0]["cached"] is True


def test_verify_tasks_refuses_a_task_already_green_at_parent(tmp_path: Path, kit_repo: tuple[Path, str, str]) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    _write(task.exercise_dir / "tests" / "test_double.py", "def test_double():\n    assert True\n")
    with pytest.raises(kl.KitLotError, match="ne discrimine pas"):
        kl.verify_tasks([task], kit_repo[0])


def test_test_python_that_imports_grimoire_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _fake(rc: int) -> Any:
        return lambda *a, **k: subprocess.CompletedProcess(a[0], rc, "", "")

    monkeypatch.setattr(kl.subprocess, "run", _fake(3))
    with pytest.raises(kl.KitLotError, match="contient le kit"):
        kl.verify_test_python(tmp_path / "python")
    monkeypatch.setattr(kl.subprocess, "run", _fake(4))
    with pytest.raises(kl.KitLotError, match="pytest absent"):
        kl.verify_test_python(tmp_path / "python")
    monkeypatch.setattr(kl.subprocess, "run", _fake(0))
    kl.verify_test_python(tmp_path / "python")


# ── Réseau de l'agent ────────────────────────────────────────────────────────


def test_k_environment_closes_pip_and_points_bash_proxies_at_a_closed_port(tmp_path: Path) -> None:
    env_file = kl.write_offline_env_file(tmp_path)
    python = tmp_path / "venv" / "bin" / "python"
    env = kl.k_environment({"PATH": "/usr/bin", "PYTHONHOME": "x"}, tmp_path / "run0", python, env_file)
    assert env["PATH"].split(__import__("os").pathsep)[0] == str(python.parent)
    assert env["PYTHONPATH"] == str(tmp_path / "run0" / "src")
    assert env["GRIMOIRE_POLICIES_BACKEND"] == "python"
    assert env["PYTHONNOUSERSITE"] == "1" and env["PIP_NO_INDEX"] == "1"
    assert "PYTHONHOME" not in env
    assert env["CLAUDE_ENV_FILE"] == str(env_file)
    text = env_file.read_bytes().decode("utf-8")
    assert "\r" not in text and "HTTPS_PROXY=http://127.0.0.1:9" in text
    assert "HTTP_PROXY" not in env  # le processus de Claude Code garde son accès à l'API


def test_claude_extra_args_remove_web_tools_on_k_tasks_only(tmp_path: Path, kit_repo: tuple[Path, str, str]) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    assert kl.claude_extra_args(task) == ["--disallowedTools", "WebFetch", "WebSearch"]
    assert kl.claude_extra_args(ta.TaskMeta("python/a", "python", "a", tmp_path, ("a.py",), ("a_test.py",))) == []


def _tool_use(name: str, tool_input: dict[str, Any]) -> str:
    content = [{"type": "tool_use", "id": "t", "name": name, "input": tool_input}]
    return json.dumps({"type": "assistant", "message": {"content": content}})


def test_transcript_that_reaches_the_published_fix_invalidates_a_k_run(
    tmp_path: Path, kit_repo: tuple[Path, str, str]
) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    run_dir = tmp_path / "ws" / "tasks" / "kit__k01-double" / "nu" / "run0"
    run_dir.mkdir(parents=True)
    hidden = run_dir.parent.parent / "hidden-tests"
    hidden.mkdir()
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(run_dir)})
    log = tmp_path / "run0.stream.jsonl"
    for line, expected in [
        (_tool_use("Bash", {"command": "python -m pytest -q"}), "clean"),
        (_tool_use("Bash", {"command": "pip download grimoire-kit==3.64.0"}), "invalid"),
        (_tool_use("WebFetch", {"url": "https://example.org"}), "invalid"),
        (_tool_use("Bash", {"command": f"git -C {kit_repo[0].as_posix()} log"}), "invalid"),
        (_tool_use("Read", {"file_path": "/v/lib/python3.12/site-packages/grimoire/policies/temporal.py"}), "invalid"),
    ]:
        log.write_text(init + "\n" + line + "\n", encoding="utf-8")
        verdict, reads = kl.check_k_transcript(log, task, run_dir=run_dir, hidden_dir=hidden)
        assert verdict == expected, line
        if expected == "invalid":
            assert reads and reads[-1]["kind"] == "solution_leak"


def test_governed_arms_are_refused_on_the_k_block(tmp_path: Path, kit_repo: tuple[Path, str, str]) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    polyglot = ta.TaskMeta("python/a", "python", "a", tmp_path, ("a.py",), ("a_test.py",))
    for arms in (("kit-gov",), ("nu", "kit")):
        with pytest.raises(SystemExit, match=r"17\.7"):
            kl.refuse_governed_arms([polyglot, task], arms)
    kl.refuse_governed_arms([task], ("nu", "ecc"))
    kl.refuse_governed_arms([polyglot], ("kit-gov",))


# ── Estimation et couverture du plafond ──────────────────────────────────────


def test_sieve_estimate_matches_section_17_and_fits_the_90_dollar_cap() -> None:
    plan = bg.estimate_plan(["python"] * 132 + ["kit"] * 16, ["nu"], 1)
    assert plan["total_runs"] == 148
    assert plan["central_usd"] == pytest.approx(41.53, abs=0.05)
    assert plan["high_usd"] == pytest.approx(83.77, abs=0.01)
    assert bg.cap_coverage(90.0, plan) == {
        "cap_usd": 90.0,
        "covers_central": True,
        "covers_high": True,
        "margin_over_high_usd": 6.23,
    }


def test_estimate_of_an_arm_without_a_cost_basis_is_unknown_not_zero() -> None:
    plan = bg.estimate_plan(["python"], ["nu", "ecc"], 3)
    assert plan["central_usd"] is None and plan["unknown"] == ["ecc/P"]
    assert bg.cap_coverage(10.0, plan)["covers_high"] is None


def test_aidlc_arm_uses_the_shared_budget_guard() -> None:
    assert aa.BudgetGuard is bg.BudgetGuard
    assert aa.session_call_cost is bg.session_call_cost
    assert aa._extra_args(resume="s", max_budget_usd=1.5) == ["--max-budget-usd", "1.5000", "--resume", "s"]


# ── three_arms : --k, plafond de campagne, journal partagé ───────────────────


def _bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot-benchmark"
    for i in range(5):
        ex = root / "python" / "exercises" / "practice" / f"ex-{i}"
        _write(ex / ".meta" / "config.json", json.dumps({"files": {"solution": ["a.py"], "test": ["a_test.py"]}}))
        _write(ex / ".docs" / "instructions.md", "# ex\n")
        _write(ex / "a.py", "pass\n")
        _write(ex / "a_test.py", "def test_a():\n    assert False\n")
    return root


def _patch_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _bench_root(tmp_path)
    monkeypatch.setattr(ta, "LANGUAGES", ("python",))
    monkeypatch.setattr(ta, "PER_LANGUAGE", 2)
    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: root)
    monkeypatch.setattr(
        ta, "discover_catalog", lambda r, languages=("python",), _real=ta.discover_catalog: _real(r, languages)
    )
    monkeypatch.setattr(ta, "sample_tasks", lambda catalog, seed=551: list(catalog)[:2])
    monkeypatch.setattr(ta, "ensure_ecc_repo", lambda ws: pytest.fail("ecc cloné alors qu'aucun bras ecc n'est joué"))
    monkeypatch.setattr(ta, "ensure_go_toolchain", lambda ws: None)
    monkeypatch.setattr(ta, "disk_guard_ok", lambda workspace, min_free_gb=0: True)
    monkeypatch.setattr(ta, "resolve_grimoire_bin", lambda explicit: "grimoire")
    monkeypatch.setattr(ta, "verify_grimoire_binary_matches_template", lambda *a, **k: None)
    monkeypatch.setattr(ta, "ensure_system_toolchains_present", lambda languages: None)
    monkeypatch.setattr(ta, "verify_agent_toolchain_environment", lambda *a, **k: {})
    monkeypatch.setattr(ta, "detect_claude_code_version", lambda: "test")
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)


def _fake_runs(monkeypatch: pytest.MonkeyPatch, costs: list[tuple[float, bool]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _run_one(task: Any, arm: str, run_index: int, **kwargs: Any) -> Any:
        cost, known = costs[len(calls)]
        calls.append({"key": (task.task_id, arm, run_index), "max_budget_usd": kwargs.get("max_budget_usd")})
        record = ta.RunRecord(task.task_id, task.language, arm, run_index, True, cost, 1, 1, 1, 1.0, "completed")
        record.cost_known = known
        return record

    monkeypatch.setattr(ta, "_run_one", _run_one)
    return calls


def _main(workspace: Path, *extra: str) -> int:
    return int(ta.main(["--full", "--model", "m", "--arms", "nu", "--workspace", str(workspace), *extra]))


def test_full_campaign_refuses_to_start_without_a_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_main(monkeypatch, tmp_path)
    calls = _fake_runs(monkeypatch, [(0.1, True)] * 10)
    for cap in ([], ["--max-total-usd", "0"], ["--max-total-usd", "abc"]):
        with pytest.raises(SystemExit):
            _main(tmp_path / "ws", *cap)
    assert calls == []


def test_k_sets_the_number_of_runs_per_task_and_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_main(monkeypatch, tmp_path)
    calls = _fake_runs(monkeypatch, [(0.1, True)] * 10)
    assert _main(tmp_path / "ws", "--k", "1", "--max-total-usd", "5") == 0
    assert [c["key"][2] for c in calls] == [0, 0]
    selection = json.loads((tmp_path / "ws" / "state" / "selection.json").read_text(encoding="utf-8"))
    assert selection["k"] == 1
    with pytest.raises(SystemExit):
        _main(tmp_path / "ws2", "--k", "0", "--max-total-usd", "5")


def test_campaign_stops_at_the_cap_and_passes_the_remainder_to_each_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch, tmp_path)
    calls = _fake_runs(monkeypatch, [(0.6, True)] * 10)
    assert _main(tmp_path / "ws", "--k", "3", "--max-total-usd", "1.0") == 3
    assert len(calls) == 2  # 0,6 puis 1,2 $ : le 3e run n'est pas lancé
    assert [round(c["max_budget_usd"], 4) for c in calls] == [1.0, 0.4]
    assert "plafond atteint" in capsys.readouterr().err
    ledger = (tmp_path / "ws" / "state" / "three-arms-budget.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in ledger] == ["begin", "end", "begin", "end"]


def test_unknown_cost_blocks_the_next_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_main(monkeypatch, tmp_path)
    calls = _fake_runs(monkeypatch, [(0.0, False)] + [(0.1, True)] * 5)
    assert _main(tmp_path / "ws", "--k", "3", "--max-total-usd", "50") == 3
    assert len(calls) == 1


def test_shared_ledger_carries_the_spend_of_a_previous_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_main(monkeypatch, tmp_path)
    ledger = tmp_path / "shared-budget.jsonl"
    previous = bg.BudgetGuard.open("100", ledger)
    previous.begin("aidlc/x/run0")
    previous.record("aidlc/x/run0", 4.9, True)
    calls = _fake_runs(monkeypatch, [(0.2, True)] * 10)
    assert _main(tmp_path / "ws", "--k", "3", "--max-total-usd", "5", "--budget-ledger", str(ledger)) == 3
    assert len(calls) == 1  # 4,9 $ déjà dépensés par l'autre lanceur : un run, puis le plafond


def test_run_one_passes_the_remainder_and_the_k_restrictions_to_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kit_repo: tuple[Path, str, str]
) -> None:
    task, _ = _k_catalog(tmp_path, kit_repo)
    seen: dict[str, Any] = {}

    def _headless(task_dir: Path, prompt: str, **kwargs: Any) -> Any:
        seen.update(kwargs, prompt=prompt)
        return ta.RunOutcome(total_cost_usd=0.5, cost_known=True)

    monkeypatch.setattr(ta, "run_claude_headless", _headless)
    monkeypatch.setattr(ta, "setup_arm_nu", lambda *a, **k: {"arm": "nu"})
    workspace = tmp_path / "ws"
    homes = {arm: workspace / "homes" / arm for arm in ta.ARMS}
    record = ta._run_one(
        task, "nu", 0, workspace=workspace, ecc_repo=workspace / "ecc", homes=homes, go_bin=None,
        run_timeout_s=5, grimoire_bin="grimoire", auth_mode="api-key", model="m", max_budget_usd=2.5,
    )  # fmt: skip
    assert seen["extra_args"] == ["--max-budget-usd", "2.5000", "--disallowedTools", "WebFetch", "WebSearch"]
    assert seen["env"]["GRIMOIRE_POLICIES_BACKEND"] == "python"
    assert seen["prompt"] == kl.K_PROMPT
    assert record.success is False  # rien corrigé : le test caché reste rouge


def test_dry_run_prepares_only_the_selected_arms_and_prints_the_cap_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_main(monkeypatch, tmp_path)
    prepared: list[str] = []
    monkeypatch.setattr(ta, "setup_arm_nu", lambda d, **k: prepared.append("nu") or {"arm": "nu"})
    rc = ta.main(
        ["--dry-run", "--arms", "nu", "--k", "1", "--max-total-usd", "90", "--workspace", str(tmp_path / "ws")]
    )
    assert rc == 0 and prepared == ["nu", "nu"]
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") : out.index("\n}\n") + 2])
    assert payload["estimate"]["total_runs"] == 2
    assert payload["cap_coverage"]["covers_high"] is True


@pytest.mark.parametrize(
    ("tail", "ok"),
    [
        ("3 passed in 0.10s", True),
        ("3 passed, 40 deselected in 0.10s", True),
        ("2 passed, 1 skipped in 0.10s", False),
        ("2 skipped in 0.10s", False),
        ("1 passed, 1 xfailed in 0.1s", False),
        ("no tests ran in 0.01s", False),
    ],
)
def test_a_skipped_hidden_test_never_counts_as_a_pass(tail: str, ok: bool) -> None:
    assert kl.pytest_all_passed(f"...\n{tail}\n") is ok
