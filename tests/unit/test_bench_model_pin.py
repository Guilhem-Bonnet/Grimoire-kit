"""B-01 : modèle épinglé, appariement refusé entre modèles, suite non saturée.

Données figées ou synthétiques : aucun appel ``claude`` payant (``claude --version``
et ``Popen`` sont remplacés).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"
FIXTURE = ROOT / "tests" / "fixtures" / "bench" / "lot-l-nu-baseline.json"


def _load(name: str, filename: str) -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ta = _load("three_arms_model_pin_test", "three_arms.py")
import model_pin as mp  # noqa: E402 - `_load` a placé scripts/bench sur sys.path


@pytest.fixture(autouse=True)
def _no_real_claude_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ta, "detect_claude_code_version", lambda **k: "9.9.9 (Claude Code)", raising=False)


def _record(
    task_id: str = "python/a",
    arm: str = "nu",
    run_index: int = 0,
    success: bool = True,
    *,
    model_id: str | None = "claude-a",
    transcript_check: str | None = None,
    version: str | None = None,
) -> Any:
    return ta.RunRecord(
        task_id, task_id.split("/")[0], arm, run_index, success, 0.1, 10, 10, 1, 5.0, "completed",
        model_id=model_id, claude_code_version=version, transcript_check=transcript_check,
    )  # fmt: skip


def _report(records: list[Any]) -> dict[str, Any]:
    return ta.build_report(records, total_tasks=20, expected_cost=None, seed=551)


# ── 1. ``--model`` : transmis à claude, obligatoire pour un run payant ─────


class _CapturePopen:
    commands: ClassVar[list[list[str]]] = []

    def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
        del cwd, env, stderr
        type(self).commands.append(list(cmd))
        stdout.write((json.dumps({"type": "result", "total_cost_usd": 0.01, "num_turns": 1}) + "\n").encode("utf-8"))
        stdout.flush()

    def poll(self) -> int | None:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def _launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[str]:
    _CapturePopen.commands = []
    monkeypatch.setattr(ta.subprocess, "Popen", _CapturePopen)
    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    ta.run_claude_headless(task_dir, "prompt", home=tmp_path, timeout_s=5, poll_interval=0.0, **kwargs)
    return _CapturePopen.commands[0]


def test_run_claude_headless_passes_the_pinned_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _launch(tmp_path, monkeypatch, model="claude-pinned-1")
    assert cmd[cmd.index("--model") + 1] == "claude-pinned-1"


def test_run_claude_headless_without_model_adds_no_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "--model" not in _launch(tmp_path, monkeypatch)


@pytest.mark.parametrize("mode", ["--full", "--pilot"])
def test_paid_campaign_without_model_is_refused_before_anything(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(ta, "_run_one", lambda *a, **k: pytest.fail("un run est parti sans modèle épinglé"))
    with pytest.raises(SystemExit) as exc:
        ta.main([mode, "--workspace", str(workspace)])
    assert exc.value.code == 2
    assert "--model" in capsys.readouterr().err
    assert not workspace.exists()  # rien n'a été préparé, rien dépensé


def test_blank_model_counts_as_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ta.main(["--full", "--model", "   ", "--workspace", str(tmp_path / "ws")])


# ── 2. ``selection.json`` et lignes portent le modèle et la version ────────


def _patch_main(monkeypatch: pytest.MonkeyPatch, bench_root: Path, tmp_path: Path) -> None:
    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: bench_root)
    monkeypatch.setattr(ta, "ensure_ecc_repo", lambda ws: (tmp_path / "ecc-repo", "deadbeef"))
    monkeypatch.setattr(ta, "ensure_go_toolchain", lambda ws: None)
    monkeypatch.setattr(ta, "disk_guard_ok", lambda workspace, min_free_gb=0.0: True)
    monkeypatch.setattr(ta, "resolve_grimoire_bin", lambda explicit: "grimoire")
    monkeypatch.setattr(ta, "verify_grimoire_binary_matches_template", lambda *a, **k: None)
    monkeypatch.setattr(ta, "ensure_system_toolchains_present", lambda languages: None)
    monkeypatch.setattr(ta, "verify_agent_toolchain_environment", lambda *a, **k: {})
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    monkeypatch.setattr(ta, "find_leftover_credentials", lambda ws: [])
    monkeypatch.setattr(ta, "find_leaked_api_keys", lambda ws, api_key=None: [])
    # B-06 : api-key ajoute --bare, refusé pour les bras gouvernés ; ces tests jouent tous les bras
    monkeypatch.setattr(ta, "resolve_auth_mode", lambda explicit: "oauth-copy")


@pytest.fixture
def bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot-benchmark"
    for language in ("python", "javascript", "go", "rust"):
        for i in range(6):
            ex = root / language / "exercises" / "practice" / f"{language}-ex-{i}"
            (ex / ".meta").mkdir(parents=True)
            (ex / ".docs").mkdir(parents=True)
            name = f"{language}_ex_{i}"
            (ex / ".meta" / "config.json").write_text(
                json.dumps({"files": {"solution": [f"{name}.py"], "test": [f"{name}_test.py"]}}), encoding="utf-8"
            )
            (ex / ".docs" / "instructions.md").write_text("consigne\n", encoding="utf-8")
            (ex / f"{name}.py").write_text("pass\n", encoding="utf-8")
            (ex / f"{name}_test.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    return root


def test_selection_json_records_model_and_claude_code_version(
    bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    _patch_main(monkeypatch, bench_root, tmp_path)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _record(task.task_id, arm, run_index))
    assert ta.main(["--full", "--model", "claude-pinned-1", "--workspace", str(workspace)]) == 0
    selection = json.loads((workspace / "state" / "selection.json").read_text(encoding="utf-8"))
    assert selection["model_id"] == "claude-pinned-1"
    assert selection["claude_code_version"] == "9.9.9 (Claude Code)"
    assert selection["tasks"]  # le reste du contenu est conservé


def test_dry_run_does_not_erase_the_pin_of_a_launched_campaign(
    bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    _patch_main(monkeypatch, bench_root, tmp_path)
    monkeypatch.setattr(ta, "_do_dry_run", lambda *a, **k: 0)
    state = workspace / "state"
    state.mkdir(parents=True)
    (state / "selection.json").write_text(
        json.dumps({"model_id": "claude-pinned-1", "claude_code_version": "1.2.3"}), encoding="utf-8"
    )
    assert ta.main(["--dry-run", "--workspace", str(workspace)]) == 0
    selection = json.loads((state / "selection.json").read_text(encoding="utf-8"))
    assert (selection["model_id"], selection["claude_code_version"]) == ("claude-pinned-1", "1.2.3")


def test_run_one_stamps_the_pin_on_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bench_root: Path
) -> None:
    task = next(t for t in ta.discover_catalog(bench_root) if t.slug == "python-ex-0")
    homes = {arm: tmp_path / "home" / arm for arm in ta.ARMS}
    for home in homes.values():
        home.mkdir(parents=True)
    seen: dict[str, Any] = {}

    def _fake_claude(task_dir: Path, *a: Any, **k: Any) -> Any:
        seen.update(k)
        return ta.RunOutcome()

    monkeypatch.setattr(ta, "setup_arm_nu", lambda *a, **k: None)
    monkeypatch.setattr(ta, "run_environment", lambda *a, **k: {})
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(ta, "run_claude_headless", _fake_claude)

    record = ta._run_one(
        task, "nu", 0, workspace=tmp_path / "ws", ecc_repo=tmp_path / "ecc", homes=homes, go_bin=None,
        run_timeout_s=5, grimoire_bin="grimoire", auth_mode="api-key",
        model="claude-pinned-1", claude_code_version="9.9.9",
    )  # fmt: skip

    assert seen["model"] == "claude-pinned-1"  # le modèle part bien vers l'appel claude
    assert (record.model_id, record.claude_code_version) == ("claude-pinned-1", "9.9.9")


# ── 3. Appariement refusé entre modèles différents ─────────────────────────


def test_report_refuses_two_models() -> None:
    with pytest.raises(mp.ModelMismatchError) as exc:
        _report([_record(model_id="claude-a"), _record(arm="kit-gov", model_id="claude-b")])
    assert "claude-a" in str(exc.value) and "claude-b" in str(exc.value)


def test_report_refuses_a_pinned_row_mixed_with_an_unrecorded_one() -> None:
    """Une ligne dont le modèle n'est pas enregistré est inconnue, pas égale."""
    with pytest.raises(mp.ModelMismatchError) as exc:
        _report([_record(model_id="claude-a"), _record(arm="kit-gov", model_id=None)])
    assert "non enregistré" in str(exc.value)


def test_report_of_a_single_model_carries_it_with_the_claude_code_versions() -> None:
    report = _report([_record(version="1.0.0"), _record(arm="kit-gov", version="1.0.1")])
    assert report["model_id"] == "claude-a"
    assert report["claude_code_versions"] == ["1.0.0", "1.0.1"]
    markdown = ta.render_report_markdown(report)
    assert "`claude-a`" in markdown
    assert "Plusieurs versions de Claude Code" in markdown


def test_report_of_a_legacy_batch_says_the_model_is_not_recorded() -> None:
    report = _report([_record(model_id=None), _record(arm="kit-gov", model_id=None)])
    assert report["model_id"] is None
    assert "non enregistré" in ta.render_report_markdown(report)


def _write_rows(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r.to_dict()) + "\n" for r in rows), encoding="utf-8")


def test_report_only_refuses_a_results_file_mixing_models(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_rows(
        tmp_path / "state" / "results.jsonl",
        [_record(model_id="claude-a"), _record(arm="kit-gov", model_id="claude-b")],
    )
    rc = ta.main(["--report-only", "--workspace", str(tmp_path), "--report-dir", str(tmp_path / "out")])
    assert rc == 1
    assert "appariement refusé" in capsys.readouterr().err
    assert not (tmp_path / "out" / "report.json").exists()


@pytest.mark.parametrize("previous_model", ["claude-a", None])
def test_campaign_on_another_model_is_refused_before_any_run_or_write(
    previous_model: str | None,
    bench_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "ws"
    _patch_main(monkeypatch, bench_root, tmp_path)
    monkeypatch.setattr(ta, "_run_one", lambda *a, **k: pytest.fail("un run payant est parti sur un autre modèle"))
    _write_rows(workspace / "state" / "results.jsonl", [_record(model_id=previous_model)])
    selection = workspace / "state" / "selection.json"
    selection.write_text(json.dumps({"model_id": previous_model}), encoding="utf-8")

    rc = ta.main(["--full", "--resume", "--model", "claude-b", "--workspace", str(workspace)])

    assert rc == 1
    assert "appariement refusé" in capsys.readouterr().err
    assert json.loads(selection.read_text(encoding="utf-8")) == {"model_id": previous_model}  # pas écrasé


def test_resume_on_the_same_model_is_accepted(
    bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    _patch_main(monkeypatch, bench_root, tmp_path)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _record(task.task_id, arm, run_index))
    _write_rows(workspace / "state" / "results.jsonl", [_record(model_id="claude-a")])
    assert ta.main(["--full", "--resume", "--model", "claude-a", "--workspace", str(workspace)]) == 0


# ── 4. ``nu_baseline_pass_rate`` par tâche et mention SATURÉ ───────────────


def test_report_gives_the_nu_baseline_pass_rate_per_task() -> None:
    records = [
        _record("python/a", "nu", 0, True), _record("python/a", "nu", 1, False), _record("python/a", "kit-gov", 0, True),
        _record("python/b", "nu", 0, True), _record("python/b", "nu", 1, True),
        _record("python/c", "kit-gov", 0, True),
    ]  # fmt: skip
    by_task = _report(records)["by_task"]
    assert by_task["python/a"]["nu_baseline_pass_rate"] == 0.5
    assert by_task["python/b"]["nu_baseline_pass_rate"] == 1.0
    assert by_task["python/c"]["nu_baseline_pass_rate"] is None  # aucun run nu : inconnu, pas 0.0


def test_invalid_nu_runs_do_not_count_in_the_baseline() -> None:
    records = [_record("python/a", "nu", 0, True), _record("python/a", "nu", 1, False, transcript_check="invalid")]
    assert _report(records)["by_task"]["python/a"]["nu_baseline_pass_rate"] == 1.0


def test_lot_l_is_saturated_on_frozen_data() -> None:
    """Les 20 tâches du lot L sont à 1.0 pour le nu (60/60) : la suite est saturée, le rapport l'écrit."""
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))["nu_successes_by_task"]
    assert len(frozen) == 20
    records = [_record(task, "nu", i, ok) for task, runs in frozen.items() for i, ok in enumerate(runs)]
    report = _report(records)
    saturation = report["suite_saturation"]
    assert (saturation["verdict"], saturation["n_tasks_at_1"], saturation["n_tasks_measured"]) == ("SATURÉ", 20, 20)
    markdown = ta.render_report_markdown(report)
    assert "SATURÉ" in markdown and "20/20" in markdown


def _rates(n_at_one: int, n_below: int) -> dict[str, float | None]:
    return {f"t{i}": (1.0 if i < n_at_one else 2 / 3) for i in range(n_at_one + n_below)}


@pytest.mark.parametrize(
    ("n_at_one", "n_below", "verdict"),
    [(11, 9, "SATURÉ"), (10, 10, "NON SATURÉ"), (0, 20, "NON SATURÉ"), (1, 0, "SATURÉ")],
)
def test_saturation_threshold_is_strictly_more_than_half(n_at_one: int, n_below: int, verdict: str) -> None:
    assert mp.suite_saturation(_rates(n_at_one, n_below))["verdict"] == verdict


def test_saturation_is_unevaluated_without_any_nu_measure() -> None:
    assert mp.suite_saturation({})["verdict"] == "NON ÉVALUÉ"
    assert mp.suite_saturation({"t": None})["verdict"] == "NON ÉVALUÉ"
    report = _report([_record(arm="kit-gov")])
    assert report["suite_saturation"]["saturated"] is None
    assert "NON ÉVALUÉ" in ta.render_report_markdown(report)


def test_a_non_saturated_suite_is_not_flagged() -> None:
    records = [_record(f"python/t{i}", "nu", j, ok) for i in range(4) for j, ok in enumerate((i < 1, True, True))]
    report = _report(records)  # 3 tâches sur 4 ont un échec : 1/4 à 1.0
    assert report["suite_saturation"]["verdict"] == "NON SATURÉ"
    assert "**SATURÉ**" not in ta.render_report_markdown(report)


# ── 4bis. garde opt-in ``--fail-on-saturated`` : la saturation devient un code de sortie ──


def _lot_l_records() -> list[Any]:
    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))["nu_successes_by_task"]
    return [_record(task, "nu", i, ok) for task, runs in frozen.items() for i, ok in enumerate(runs)]


def test_report_only_fails_on_the_saturated_lot_l_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fiche B-01 : une suite saturée doit rougir. Sur le lot L figé (20/20 à 1.0), le rapport est écrit ET la sortie est 1."""
    _write_rows(tmp_path / "state" / "results.jsonl", _lot_l_records())
    argv = ["--report-only", "--workspace", str(tmp_path), "--report-dir", str(tmp_path / "out")]

    rc = ta.main([*argv, "--fail-on-saturated"])

    assert rc == 1
    assert "SATURÉE" in capsys.readouterr().err
    saturation = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))["suite_saturation"]
    assert saturation["verdict"] == "SATURÉ"  # le rapport reste écrit
    assert ta.main(argv) == 0  # opt-in : sans le drapeau, comportement inchangé


@pytest.mark.parametrize("kind", ["not-saturated", "unevaluated"])
def test_fail_on_saturated_does_not_fail_without_a_saturation_verdict(kind: str, tmp_path: Path) -> None:
    if kind == "not-saturated":
        records = [_record(f"python/t{i}", "nu", j, ok) for i in range(4) for j, ok in enumerate((i < 1, True, True))]
    else:
        records = [_record(arm="kit-gov")]  # aucun run nu : on ne rougit pas sur une donnée absente
    _write_rows(tmp_path / "state" / "results.jsonl", records)
    rc = ta.main(
        ["--report-only", "--workspace", str(tmp_path), "--report-dir", str(tmp_path / "out"), "--fail-on-saturated"]
    )
    assert rc == 0


def test_full_campaign_fails_on_saturated_when_asked(
    bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_main(monkeypatch, bench_root, tmp_path)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _record(task.task_id, arm, run_index))
    base = ["--full", "--model", "claude-a"]
    assert ta.main([*base, "--workspace", str(tmp_path / "ws-off")]) == 0
    assert ta.main([*base, "--fail-on-saturated", "--workspace", str(tmp_path / "ws-on")]) == 1


def test_saturation_exit_code_is_zero_when_the_guard_is_off() -> None:
    records = [_record(f"python/t{i}", "nu", 0, True) for i in range(3)]
    assert mp.saturation_exit_code(records, fail_on_saturated=False) == 0
    assert mp.saturation_exit_code(records, fail_on_saturated=True) == 1


# ── 5. ``claude --version`` ────────────────────────────────────────────────


def test_detect_claude_code_version_parses_the_first_line(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(cmd: list[str], **kwargs: Any) -> Any:
        assert cmd == ["claude", "--version"]
        return subprocess.CompletedProcess(cmd, 0, stdout="2.1.0 (Claude Code)\nautre\n", stderr="")

    monkeypatch.setattr(mp.subprocess, "run", _run)
    assert mp.detect_claude_code_version() == "2.1.0 (Claude Code)"


@pytest.mark.parametrize("failure", ["missing", "exit", "empty"])
def test_detect_claude_code_version_is_unknown_not_guessed(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(cmd: list[str], **kwargs: Any) -> Any:
        if failure == "missing":
            raise FileNotFoundError("claude")
        return subprocess.CompletedProcess(
            cmd, 1 if failure == "exit" else 0, stdout="" if failure == "empty" else "x", stderr=""
        )

    monkeypatch.setattr(mp.subprocess, "run", _run)
    assert mp.detect_claude_code_version() is None
