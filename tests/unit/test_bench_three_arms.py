"""Tests du harnais du banc à trois bras (Grimoire-kit#551), sans LLM.

Ces tests verrouillent les cinq mécanismes qui doivent rester corrects sans
jamais appeler ``claude -p`` : tirage reproductible, préparation d'un dépôt de
tâche (tests cachés), détection de succès sur tests verts/rouges, calcul de
pass^k et de son intervalle de confiance, et détection d'un agent qui « tourne
en rond ». Aucun réseau, aucune clé API.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bench" / "three_arms.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("three_arms", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # requis par `dataclasses` (résolution de __module__)
    spec.loader.exec_module(module)
    return module


ta = _load_module()


# ── Fixtures : un catalogue synthétique, format Exercism minimal ───────────


def _write_exercise(root: Path, language: str, slug: str, *, solution_body: str = "pass\n") -> None:
    # Comme sur Exercism : le dossier de l'exercice utilise des tirets, le
    # module Python des underscores (affine-cipher/ -> affine_cipher.py).
    module_name = slug.replace("-", "_")
    ex_dir = root / language / "exercises" / "practice" / slug
    (ex_dir / ".meta").mkdir(parents=True)
    (ex_dir / ".docs").mkdir(parents=True)
    (ex_dir / ".meta" / "config.json").write_text(
        json.dumps({"files": {"solution": [f"{module_name}.py"], "test": [f"{module_name}_test.py"]}}),
        encoding="utf-8",
    )
    (ex_dir / ".docs" / "instructions.md").write_text(f"# {slug}\n\nConsigne de test.\n", encoding="utf-8")
    (ex_dir / f"{module_name}.py").write_text(solution_body, encoding="utf-8")
    (ex_dir / f"{module_name}_test.py").write_text(
        f"from {module_name} import solve\n\n\ndef test_solve():\n    assert solve() == 42\n",
        encoding="utf-8",
    )
    # fichier support, ni solution ni test : doit survivre à la préparation
    (ex_dir / "README.support").write_text("support\n", encoding="utf-8")


@pytest.fixture
def synthetic_bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot-benchmark"
    for language in ("python", "javascript", "go", "rust"):
        for i in range(6):  # plus que PER_LANGUAGE pour exercer le tirage
            _write_exercise(root, language, f"{language}-ex-{i}")
    return root


# ── 1. Tirage reproductible ─────────────────────────────────────────────────


def test_sample_tasks_is_reproducible(synthetic_bench_root: Path) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    assert len(catalog) == 4 * 6

    first = ta.sample_tasks(catalog, seed=551)
    second = ta.sample_tasks(catalog, seed=551)
    assert [t.task_id for t in first] == [t.task_id for t in second]
    assert len(first) == 4 * ta.PER_LANGUAGE

    # une graine différente change (au moins partiellement) le tirage
    third = ta.sample_tasks(catalog, seed=42)
    assert [t.task_id for t in first] != [t.task_id for t in third]


def test_sample_tasks_balances_languages(synthetic_bench_root: Path) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    tasks = ta.sample_tasks(catalog, seed=551)
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.language] = counts.get(task.language, 0) + 1
    assert counts == dict.fromkeys(ta.LANGUAGES, ta.PER_LANGUAGE)


def test_sample_tasks_raises_when_catalog_too_small(synthetic_bench_root: Path) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    small_catalog = [t for t in catalog if t.language != "python"][:1] + [t for t in catalog if t.language == "python"]
    with pytest.raises(ValueError):
        ta.sample_tasks(small_catalog, seed=1, languages=("javascript",), per_language=5)


# ── 2. Préparation d'un dépôt de tâche ──────────────────────────────────────


def test_prepare_task_repo_hides_tests_by_default(synthetic_bench_root: Path, tmp_path: Path) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    task = next(t for t in catalog if t.slug == "python-ex-0")
    dest = tmp_path / "task-repo"

    ta.prepare_task_repo(task, dest)

    assert (dest / "TASK.md").is_file()
    assert "Consigne de test" in (dest / "TASK.md").read_text(encoding="utf-8")
    assert (dest / "python_ex_0.py").is_file()
    assert (dest / "README.support").is_file()
    assert not (dest / "python_ex_0_test.py").exists()
    assert (dest / ".git").is_dir()


def test_hidden_tests_dir_carries_the_excluded_files(synthetic_bench_root: Path, tmp_path: Path) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    task = next(t for t in catalog if t.slug == "python-ex-0")
    hidden = ta.hidden_tests_dir(task, tmp_path / "hidden")
    assert (hidden / "python_ex_0_test.py").is_file()


def test_prepare_task_repo_can_include_tests_for_verification(synthetic_bench_root: Path, tmp_path: Path) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    task = next(t for t in catalog if t.slug == "python-ex-0")
    dest = tmp_path / "task-repo-with-tests"
    ta.prepare_task_repo(task, dest, include_tests=True)
    assert (dest / "python_ex_0_test.py").is_file()


# ── 3. Détection de succès sur tests verts/rouges ──────────────────────────


def test_detect_success_true_only_on_zero_without_timeout() -> None:
    assert ta.detect_success(0) is True
    assert ta.detect_success(1) is False
    assert ta.detect_success(0, timed_out=True) is False


def test_run_hidden_tests_green_and_red(synthetic_bench_root: Path, tmp_path: Path) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    task = next(t for t in catalog if t.slug == "python-ex-0")

    # cas vert : la solution est correcte
    green_dir = tmp_path / "green"
    ta.prepare_task_repo(task, green_dir)
    (green_dir / "python_ex_0.py").write_text("def solve():\n    return 42\n", encoding="utf-8")
    hidden = ta.hidden_tests_dir(task, tmp_path / "hidden")
    success, _ = ta.run_hidden_tests(task, green_dir, hidden)
    assert success is True

    # cas rouge : la solution reste un stub
    red_dir = tmp_path / "red"
    ta.prepare_task_repo(task, red_dir)
    success, output = ta.run_hidden_tests(task, red_dir, hidden)
    assert success is False
    assert output  # un message d'échec est bien remonté


# ── Identifiants : jamais laissés à demeure dans un HOME isolé ──────────────


@pytest.fixture
def fake_real_home(tmp_path: Path) -> Path:
    """Un faux ``HOME`` réel avec un faux fichier d'identifiants (pas une vraie clé)."""
    real_home = tmp_path / "real-home"
    (real_home / ".claude").mkdir(parents=True)
    (real_home / ".claude" / ".credentials.json").write_text('{"fake": "not-a-real-token"}', encoding="utf-8")
    return real_home


def test_ensure_isolated_home_never_copies_credentials(fake_real_home: Path, tmp_path: Path) -> None:
    home = tmp_path / "isolated-home"
    ta.ensure_isolated_home(home)
    assert not (home / ta.CREDENTIALS_REL_PATH).exists()


def test_credentials_provisioned_copies_then_removes_on_success(fake_real_home: Path, tmp_path: Path) -> None:
    home = tmp_path / "isolated-home"
    ta.ensure_isolated_home(home)
    creds_path = home / ta.CREDENTIALS_REL_PATH

    with ta.credentials_provisioned(home, real_home=fake_real_home) as provided:
        assert provided == creds_path
        assert creds_path.is_file()  # présent PENDANT le bloc

    assert not creds_path.exists()  # absent après, même en sortie normale


def test_credentials_provisioned_removes_even_on_exception(fake_real_home: Path, tmp_path: Path) -> None:
    home = tmp_path / "isolated-home"
    ta.ensure_isolated_home(home)
    creds_path = home / ta.CREDENTIALS_REL_PATH

    with pytest.raises(RuntimeError), ta.credentials_provisioned(home, real_home=fake_real_home):
        assert creds_path.is_file()
        raise RuntimeError("run tué en cours (timeout/erreur simulés)")

    assert not creds_path.exists()  # le `finally` a tourné malgré l'exception


def test_credentials_provisioned_yields_none_without_source_credentials(tmp_path: Path) -> None:
    home = tmp_path / "isolated-home"
    ta.ensure_isolated_home(home)
    empty_real_home = tmp_path / "real-home-without-creds"
    empty_real_home.mkdir()

    with ta.credentials_provisioned(home, real_home=empty_real_home) as provided:
        assert provided is None
    assert not (home / ta.CREDENTIALS_REL_PATH).exists()


def test_find_leftover_credentials_detects_and_is_clean_after_normal_use(fake_real_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    homes_dir = workspace / "homes"
    for arm in ta.ARMS:
        ta.ensure_isolated_home(homes_dir / arm)

    # rien à signaler tant qu'aucun run n'a eu lieu
    assert ta.find_leftover_credentials(workspace) == []

    # simule un nettoyage qui n'aurait pas tourné (process tué avant le finally)
    leaked = homes_dir / "kit" / ta.CREDENTIALS_REL_PATH
    leaked.parent.mkdir(parents=True, exist_ok=True)
    leaked.write_text('{"fake": "leaked"}', encoding="utf-8")
    assert ta.find_leftover_credentials(workspace) == [leaked]

    # un run normal (via le context manager) ne laisse rien derrière lui
    leaked.unlink()
    with ta.credentials_provisioned(homes_dir / "kit", real_home=fake_real_home):
        pass
    assert ta.find_leftover_credentials(workspace) == []


# ── 4. pass^k et intervalle de confiance ────────────────────────────────────


def test_pass_hat_k_all_green_vs_one_red() -> None:
    assert ta.pass_hat_k([True, True, True]) == 1
    assert ta.pass_hat_k([True, False, True]) == 0
    assert ta.pass_hat_k([]) == 0


def test_bootstrap_ci_shrinks_around_constant_values() -> None:
    lo, hi = ta.bootstrap_ci([1.0, 1.0, 1.0, 1.0], seed=1)
    assert lo == pytest.approx(1.0)
    assert hi == pytest.approx(1.0)


def test_bootstrap_ci_is_deterministic_given_seed() -> None:
    values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]
    first = ta.bootstrap_ci(values, seed=7)
    second = ta.bootstrap_ci(values, seed=7)
    assert first == second


def test_cis_disjoint() -> None:
    assert ta.cis_disjoint((0.0, 0.2), (0.6, 1.0)) is True
    assert ta.cis_disjoint((0.0, 0.5), (0.4, 1.0)) is False


def test_should_stop_early_requires_minimum_tasks() -> None:
    records = [
        ta.RunRecord("python/a", "python", "kit", 0, True, 0.1, 10, 10, 1, 5.0, "completed"),
    ]
    stop, reason = ta.should_stop_early(records, total_tasks=20)
    assert stop is False
    assert "minimum" in reason


def test_should_stop_early_stops_on_disjoint_success_cis() -> None:
    records = []
    task_ids = [f"python/task-{i}" for i in range(6)]
    for task_id in task_ids:
        for run_index in range(3):
            records.append(ta.RunRecord(task_id, "python", "kit", run_index, True, 0.1, 10, 10, 1, 5.0, "completed"))
            records.append(ta.RunRecord(task_id, "python", "nu", run_index, False, 0.1, 10, 10, 1, 5.0, "completed"))
            records.append(ta.RunRecord(task_id, "python", "ecc", run_index, False, 0.1, 10, 10, 1, 5.0, "completed"))
    stop, reason = ta.should_stop_early(records, total_tasks=20, seed=0)
    assert stop is True
    assert "disjoints" in reason


# ── 5. Détection « tourne en rond » ─────────────────────────────────────────


def test_detect_loop_needs_consecutive_repeats() -> None:
    assert ta.detect_loop(["ls", "ls", "ls", "ls"], repeat_threshold=4) is True
    assert ta.detect_loop(["ls", "pwd", "ls", "ls"], repeat_threshold=4) is False
    assert ta.detect_loop(["ls", "ls"], repeat_threshold=4) is False


def test_detect_loop_ignores_older_history_once_it_moves_on() -> None:
    commands = ["ls", "ls", "ls", "ls", "pwd", "cat file", "cat file"]
    assert ta.detect_loop(commands, repeat_threshold=4) is False


def test_extract_bash_command_from_stream_json_line() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]},
        }
    )
    assert ta.extract_bash_command(line) == "pytest -q"


def test_extract_bash_command_ignores_non_bash_events() -> None:
    assert ta.extract_bash_command(json.dumps({"type": "system", "subtype": "init"})) is None
    assert ta.extract_bash_command(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
    ) is None
    assert ta.extract_bash_command("not json") is None


def test_parse_result_event_keeps_the_last_one() -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "result", "total_cost_usd": 0.1, "num_turns": 1}),
        json.dumps({"type": "assistant", "message": {"content": []}}),
        json.dumps({"type": "result", "total_cost_usd": 0.42, "num_turns": 3}),
    ]
    result = ta.parse_result_event(lines)
    assert result is not None
    assert result["total_cost_usd"] == pytest.approx(0.42)
    assert result["num_turns"] == 3


# ── Divers : ordre des bras, coût attendu, rapport ──────────────────────────


def test_select_run_order_is_reproducible_per_task() -> None:
    order_a1 = ta.select_run_order(ta.ARMS, task_id="python/foo", seed=551)
    order_a2 = ta.select_run_order(ta.ARMS, task_id="python/foo", seed=551)
    assert order_a1 == order_a2
    assert sorted(order_a1) == sorted(ta.ARMS)


def test_select_run_order_varies_with_task_id() -> None:
    orders = {tuple(ta.select_run_order(ta.ARMS, task_id=f"python/task-{i}", seed=551)) for i in range(10)}
    # avec 3 bras il n'y a que 6 permutations possibles ; sur 10 tâches on
    # doit en voir plus d'une, sinon le tirage par tâche ne fait rien.
    assert len(orders) > 1


def test_expected_cost_report_extrapolates_from_pilot() -> None:
    records = [
        ta.RunRecord("python/a", "python", "nu", 0, True, 1.0, 10, 10, 1, 5.0, "completed"),
        ta.RunRecord("python/a", "python", "kit", 0, True, 0.5, 10, 10, 1, 5.0, "completed"),
    ]
    estimate = ta.expected_cost_report(records, planned_runs_per_arm={"nu": 60, "ecc": 60, "kit": 60})
    assert estimate["per_arm"]["nu"]["expected_cost_usd"] == pytest.approx(60.0)
    assert estimate["per_arm"]["kit"]["expected_cost_usd"] == pytest.approx(30.0)
    assert estimate["per_arm"]["ecc"]["pilot_runs"] == 0


def test_should_alert_cost_overrun() -> None:
    assert ta.should_alert_cost_overrun(10.0, 31.0) is True
    assert ta.should_alert_cost_overrun(10.0, 20.0) is False
    assert ta.should_alert_cost_overrun(0.0, 5.0) is False


def test_build_report_and_markdown_smoke() -> None:
    records = [
        ta.RunRecord("python/a", "python", "kit", 0, True, 0.2, 10, 10, 2, 4.0, "completed"),
        ta.RunRecord("python/a", "python", "nu", 0, False, 0.3, 10, 10, 2, 4.0, "completed"),
    ]
    report = ta.build_report(records, total_tasks=20, expected_cost=None, seed=0)
    assert report["per_arm"]["kit"]["success_rate"] == pytest.approx(1.0)
    assert report["per_arm"]["nu"]["success_rate"] == pytest.approx(0.0)
    md = ta.render_report_markdown(report)
    assert "Par bras" in md
    assert "python/a" in md
