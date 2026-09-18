"""Tests du harnais du banc à trois bras (Grimoire-kit#551), sans LLM.

Ces tests verrouillent les mécanismes qui doivent rester corrects sans jamais
appeler ``claude -p`` : tirage reproductible, préparation d'un dépôt de tâche
(tests cachés), détection de succès sur tests verts/rouges, calcul de pass^k
et de son intervalle de confiance, détection d'un agent qui « tourne en
rond », et — lot A du plan de correction du surcoût (#551/#552) — la garde
disque et le report des tokens de cache dans ``results.jsonl``. Aucun
réseau, aucune clé API.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

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


def test_extract_go_archive_rejects_a_path_traversal_member(tmp_path: Path) -> None:
    """Régression CodeQL py/tarslip : ``tarfile.extractall`` sans ``filter``
    fait confiance aveuglément aux membres de l'archive. Même une archive
    « officielle » pinnée peut, en cas de compromission de la source ou de
    MITM, contenir un membre ``../`` — le filtre ``data`` doit le refuser
    plutôt que de l'écrire hors de la destination."""
    import tarfile

    archive = tmp_path / "evil.tar.gz"
    escapee = tmp_path / "evil-payload.txt"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name="../evil-payload.txt")
        payload = b"pwned"
        info.size = len(payload)
        import io

        tar.addfile(info, io.BytesIO(payload))

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(tarfile.FilterError):
        ta._extract_go_archive(archive, dest)
    assert not escapee.exists()


# ── Lot A (#551/#552) : garde disque, nettoyage, tokens de cache ───────────


class _FakePopen:
    """Remplace ``subprocess.Popen`` pour ``run_claude_headless`` : écrit un
    flux ``stream-json`` déterministe dans le fichier de log au lieu de
    lancer réellement ``claude -p``, puis se déclare terminé au premier
    ``poll()``."""

    def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
        del cmd, cwd, env, stderr
        lines = [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.07,
                    "num_turns": 3,
                    "is_error": False,
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 80,
                        "cache_read_input_tokens": 900,
                        "cache_creation_input_tokens": 200,
                    },
                    "modelUsage": {"claude-sonnet-5": {"cost_usd": 0.07}},
                }
            ),
        ]
        stdout.write(("\n".join(lines) + "\n").encode("utf-8"))
        stdout.flush()
        self._polled = False

    def poll(self) -> int | None:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def test_run_claude_headless_extracts_cache_tokens_and_model_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le flux ``result`` porte des tokens de cache et une répartition par
    modèle que le harnais ignorait avant #551/#552 lot A — la mesure de coût
    du banc à trois bras était donc incomplète sans que rien ne le signale."""
    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    monkeypatch.setattr(ta.subprocess, "Popen", _FakePopen)

    outcome = ta.run_claude_headless(task_dir, "peu importe le prompt", home=tmp_path, timeout_s=5, poll_interval=0.0)

    assert outcome.cache_read_input_tokens == 900
    assert outcome.cache_creation_input_tokens == 200
    assert outcome.model_usage == {"claude-sonnet-5": {"cost_usd": 0.07}}
    assert outcome.terminated_reason == "completed"


def test_run_claude_headless_defaults_cache_tokens_to_zero_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un flux plus ancien sans ces clés ne doit jamais faire planter l'extraction."""

    class _FakePopenNoCache(_FakePopen):
        def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
            del cmd, cwd, env, stderr
            line = json.dumps({"type": "result", "total_cost_usd": 0.01, "num_turns": 1, "usage": {"input_tokens": 5, "output_tokens": 5}})
            stdout.write((line + "\n").encode("utf-8"))
            stdout.flush()

    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    monkeypatch.setattr(ta.subprocess, "Popen", _FakePopenNoCache)

    outcome = ta.run_claude_headless(task_dir, "peu importe", home=tmp_path, timeout_s=5, poll_interval=0.0)

    assert outcome.cache_read_input_tokens == 0
    assert outcome.cache_creation_input_tokens == 0


def test_run_one_carries_cache_tokens_and_model_usage_into_the_record(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'oubli documenté par le diagnostic : ``RunOutcome.model_usage`` était
    rempli mais jamais recopié dans le ``RunRecord`` que ``results.jsonl``
    persiste. Ce test construit un ``_run_one`` avec de faux collaborateurs
    (aucun appel réseau ni ``claude -p``) et vérifie le trajet complet."""
    task = ta.discover_catalog(synthetic_bench_root)[0]
    workspace = tmp_path / "workspace"
    homes = {arm: workspace / "homes" / arm for arm in ta.ARMS}
    for home in homes.values():
        ta.ensure_isolated_home(home)

    fake_outcome = ta.RunOutcome(
        success=None,
        total_cost_usd=0.07,
        input_tokens=120,
        output_tokens=80,
        cache_read_input_tokens=900,
        cache_creation_input_tokens=200,
        num_turns=3,
        wall_seconds=12.0,
        terminated_reason="completed",
        model_usage={"claude-sonnet-5": {"cost_usd": 0.07}},
    )

    @contextlib.contextmanager
    def fake_credentials(home: Path, real_home: Path | None = None):
        del home, real_home
        yield "fake-token"

    monkeypatch.setattr(ta, "credentials_provisioned", fake_credentials)
    monkeypatch.setattr(ta, "run_claude_headless", lambda *a, **k: fake_outcome)
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    cleaned: list[Path] = []
    monkeypatch.setattr(ta, "cleanup_build_artifacts", cleaned.append)

    record = ta._run_one(
        task,
        "nu",
        0,
        workspace=workspace,
        ecc_repo=tmp_path / "ecc-repo-unused",
        homes=homes,
        go_bin=None,
        run_timeout_s=5,
    )

    assert record.cache_read_input_tokens == 900
    assert record.cache_creation_input_tokens == 200
    assert record.model_usage == {"claude-sonnet-5": {"cost_usd": 0.07}}
    assert cleaned, "le nettoyage des artefacts de build doit tourner après chaque run"


def test_cleanup_build_artifacts_removes_target_and_node_modules(tmp_path: Path) -> None:
    task_dir = tmp_path / "run"
    (task_dir / "target" / "debug").mkdir(parents=True)
    (task_dir / "target" / "debug" / "binary").write_text("bin", encoding="utf-8")
    (task_dir / "node_modules" / "some-pkg").mkdir(parents=True)
    (task_dir / "node_modules" / "some-pkg" / "index.js").write_text("//", encoding="utf-8")
    (task_dir / "src").mkdir(parents=True)
    (task_dir / "src" / "keep.py").write_text("kept\n", encoding="utf-8")

    ta.cleanup_build_artifacts(task_dir)

    assert not (task_dir / "target").exists()
    assert not (task_dir / "node_modules").exists()
    assert (task_dir / "src" / "keep.py").is_file()


def test_cleanup_build_artifacts_is_a_silent_no_op_without_any(tmp_path: Path) -> None:
    task_dir = tmp_path / "run"
    (task_dir / "src").mkdir(parents=True)
    ta.cleanup_build_artifacts(task_dir)  # ne doit pas lever
    assert (task_dir / "src").is_dir()


def test_disk_guard_ok_reports_false_under_the_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ta.shutil, "disk_usage", lambda path: SimpleNamespace(total=0, used=0, free=4 * (1024**3)))
    assert ta.disk_guard_ok(tmp_path) is False


def test_disk_guard_ok_reports_true_above_the_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ta.shutil, "disk_usage", lambda path: SimpleNamespace(total=0, used=0, free=10 * (1024**3)))
    assert ta.disk_guard_ok(tmp_path) is True


def test_main_full_stops_cleanly_and_writes_a_partial_report_on_low_disk(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le coeur de la garde disque : jamais un crash en plein run, un rapport
    partiel avec ce qui a déjà tourné — ici zéro run, le disque étant déjà
    sous le seuil avant même le premier appel à ``claude -p``."""
    workspace = tmp_path / "workspace"

    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: synthetic_bench_root)
    monkeypatch.setattr(ta, "ensure_ecc_repo", lambda ws: (tmp_path / "ecc-repo", "deadbeef"))
    monkeypatch.setattr(ta, "ensure_go_toolchain", lambda ws: None)
    monkeypatch.setattr(ta, "disk_guard_ok", lambda workspace, min_free_gb=ta.DISK_GUARD_MIN_FREE_GB: False)

    def _fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("un run a été lancé malgré le disque sous le seuil")

    monkeypatch.setattr(ta, "_run_one", _fail_if_called)

    written: dict[str, Any] = {}

    def _fake_write_report(
        records: Any,
        out_dir: Path,
        *,
        total_tasks: int,
        expected_cost: Any,
        seed: int,
        rerun_arms: Any = None,
        label: Any = None,
    ) -> None:
        del total_tasks, expected_cost, seed, rerun_arms, label
        written["records"] = list(records)
        written["out_dir"] = out_dir

    monkeypatch.setattr(ta, "write_report", _fake_write_report)

    rc = ta.main(["--full", "--workspace", str(workspace)])

    assert rc == 0
    assert written, "un rapport (même vide) doit être écrit à l'arrêt disque"
    assert written["records"] == []


# ── 9. ``--arms`` (lot E, #582) : filtre et repli ──────────────────────────


def test_parse_arms_default_is_all_three_arms() -> None:
    assert ta.parse_arms(None) == ta.ARMS


def test_parse_arms_empty_or_blank_string_falls_back_to_all_three() -> None:
    assert ta.parse_arms("") == ta.ARMS
    assert ta.parse_arms("   ") == ta.ARMS


def test_parse_arms_single_arm() -> None:
    assert ta.parse_arms("kit") == ("kit",)


def test_parse_arms_is_order_independent_and_canonical() -> None:
    assert ta.parse_arms("kit,nu") == ta.parse_arms("nu,kit") == ("nu", "kit")


def test_parse_arms_tolerates_whitespace_and_duplicates() -> None:
    assert ta.parse_arms(" nu , nu ,kit ") == ("nu", "kit")


def test_parse_arms_rejects_unknown_arm() -> None:
    with pytest.raises(ValueError, match="bras inconnu"):
        ta.parse_arms("kit,rust")


def test_main_rejects_unknown_arm_via_argparse_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ta.main(["--dry-run", "--workspace", str(tmp_path), "--arms", "kit,rust"])


# ── 10. Preuve d'exécution des tests réels du bras kit (lot B/E, #582) ─────


def test_has_test_run_evidence_true_when_test_run_json_present(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "_grimoire-output" / "evidence" / "bootstrap"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "test-run.json").write_text("{}", encoding="utf-8")
    assert ta.has_test_run_evidence(tmp_path) is True


def test_has_test_run_evidence_false_when_evidence_dir_absent(tmp_path: Path) -> None:
    assert ta.has_test_run_evidence(tmp_path) is False


def test_has_test_run_evidence_false_when_dir_present_but_no_test_run_json(tmp_path: Path) -> None:
    (tmp_path / "_grimoire-output" / "evidence" / "bootstrap").mkdir(parents=True)
    assert ta.has_test_run_evidence(tmp_path) is False


def test_has_test_run_evidence_finds_it_under_any_task_id(tmp_path: Path) -> None:
    # L'agent choisit son propre ``--task-id`` (défaut "bootstrap", ou autre) :
    # la détection ne doit pas dépendre d'un identifiant figé.
    evidence_dir = tmp_path / "_grimoire-output" / "evidence" / "une-tache-quelconque"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "test-run.json").write_text("{}", encoding="utf-8")
    assert ta.has_test_run_evidence(tmp_path) is True


# ── 11. Repli « bras repris » du rapport (lot E, #582) ──────────────────────


def test_carried_over_label_uses_earliest_recorded_at() -> None:
    records = [
        ta.RunRecord("python/a", "python", "nu", 0, True, 0.1, 10, 10, 1, 5.0, "completed", recorded_at="2026-09-17T06:00:00+00:00"),
        ta.RunRecord("python/b", "python", "nu", 0, True, 0.1, 10, 10, 1, 5.0, "completed", recorded_at="2026-09-16T06:00:00+00:00"),
    ]
    assert ta.carried_over_label(records, "nu") == "2026-09-16"


def test_carried_over_label_falls_back_when_recorded_at_is_missing() -> None:
    records = [ta.RunRecord("python/a", "python", "nu", 0, True, 0.1, 10, 10, 1, 5.0, "completed")]
    assert "date inconnue" in ta.carried_over_label(records, "nu")


def test_build_report_notes_arms_not_rerun_this_execution() -> None:
    records = [
        ta.RunRecord("python/a", "python", "nu", 0, True, 0.1, 10, 10, 1, 5.0, "completed", recorded_at="2026-09-17T06:00:00+00:00"),
        ta.RunRecord("python/a", "python", "kit", 0, True, 0.1, 10, 10, 1, 5.0, "completed", recorded_at="2026-09-18T06:00:00+00:00"),
    ]
    report = ta.build_report(records, total_tasks=1, expected_cost=None, seed=0, rerun_arms=("kit",), label="lot E")
    assert report["label"] == "lot E"
    assert report["carried_over_notes"] == {"nu": "2026-09-17"}
    rendered = ta.render_report_markdown(report)
    assert "repris de la campagne du 2026-09-17" in rendered
    assert "lot E" in rendered


def test_build_report_has_no_carried_over_notes_when_rerun_arms_is_none() -> None:
    records = [ta.RunRecord("python/a", "python", "nu", 0, True, 0.1, 10, 10, 1, 5.0, "completed")]
    report = ta.build_report(records, total_tasks=1, expected_cost=None, seed=0)
    assert report["carried_over_notes"] == {}


def test_build_report_kit_runs_detail_includes_turns_cost_time_and_evidence() -> None:
    records = [
        ta.RunRecord(
            "go/palindrome-products",
            "go",
            "kit",
            0,
            True,
            0.42,
            10,
            10,
            7,
            123.0,
            "completed",
            kit_test_run_evidence=True,
        ),
    ]
    report = ta.build_report(records, total_tasks=1, expected_cost=None, seed=0, rerun_arms=("kit",))
    assert report["kit_runs"] == [
        {
            "task_id": "go/palindrome-products",
            "run_index": 0,
            "num_turns": 7,
            "total_cost_usd": 0.42,
            "wall_seconds": 123.0,
            "test_run_evidence": True,
        }
    ]
    rendered = ta.render_report_markdown(report)
    assert "Détail par run — bras kit" in rendered
    assert "go/palindrome-products" in rendered


# ── 12. Bras ``kit-gov`` (lot F, #582) : projet réellement enrôlé ──────────


def test_governed_task_id_replaces_slash_with_double_underscore() -> None:
    # Grimoire refuse '/' dans un task_id (TASK_ID_PATTERN) : même convention
    # que le harnais utilise déjà pour les chemins de dépôt de tâche.
    assert ta._governed_task_id("go/palindrome-products") == "go__palindrome-products"
    assert ta._governed_task_id("python/word-count") == "python__word-count"


def test_setup_arm_kit_gov_runs_standard_init_then_migrates_a_synthetic_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provisionnement simulé, sans réseau ni CLI Grimoire réelle.

    Verrouille l'ordre (init du standard puis migration) et le contenu du
    board synthétique écrit entre les deux : id gouverné exact, statut
    ``in_progress`` — c'est ce qui fait passer ``_is_governed()`` à ``True``
    et résout ``active_task_id()`` sur cet id plutôt que sur ``bootstrap``
    (vérifié en isolation, voir la docstring de ``setup_arm_kit_gov``).
    """
    calls: list[list[str]] = []

    def fake_run(cmd: Any, *, cwd: Path | None = None, env: Any = None, timeout: int | None = None) -> Any:
        del cwd, env, timeout
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ta, "_run", fake_run)
    monkeypatch.setattr(
        ta,
        "setup_arm_kit",
        lambda task_dir, *, kit_home, timeout=180: {"arm": "kit", "added": ["x"], "init_rc": 0, "sync_rc": 0},
    )

    task_dir = tmp_path / "run"
    task_dir.mkdir()
    result = ta.setup_arm_kit_gov(task_dir, kit_home=tmp_path / "home", task_id="go/palindrome-products")

    assert result["arm"] == "kit-gov"
    assert result["governed_task_id"] == "go__palindrome-products"
    assert result["standard_init_rc"] == 0
    assert result["standard_init_profile"] == "starter"
    assert result["migrate_rc"] == 0
    assert result["added"] == ["x"]
    assert calls[0][:3] == ["grimoire", "standard", "init"]
    assert calls[1][:3] == ["grimoire", "task", "migrate-standard"]

    board = (task_dir / "_grimoire" / "standard" / "task-board.yaml").read_text(encoding="utf-8")
    assert "task_id: go__palindrome-products" in board
    assert "status: in_progress" in board


def test_run_one_wires_kit_gov_setup_and_collects_governed_evidence(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run_one`` doit provisionner ``kit-gov`` avec le task_id du banc et
    relire la preuve ``test-run.json``/les stats de dispatch, comme pour
    ``kit`` — sans quoi le rapport n'aurait jamais de quoi remplir la
    colonne « lot B » de ce bras."""
    task = ta.discover_catalog(synthetic_bench_root)[0]
    workspace = tmp_path / "workspace"
    homes = {arm: workspace / "homes" / arm for arm in ta.ARMS}
    for home in homes.values():
        ta.ensure_isolated_home(home)

    setup_calls: list[tuple[Path, str]] = []

    def fake_setup_arm_kit_gov(task_dir: Path, *, kit_home: Path, task_id: str, timeout: int = 180) -> dict[str, Any]:
        del kit_home, timeout
        setup_calls.append((task_dir, task_id))
        return {"arm": "kit-gov", "added": []}

    @contextlib.contextmanager
    def fake_credentials(home: Path, real_home: Path | None = None):
        del home, real_home
        yield "fake-token"

    monkeypatch.setattr(ta, "setup_arm_kit_gov", fake_setup_arm_kit_gov)
    monkeypatch.setattr(ta, "credentials_provisioned", fake_credentials)
    monkeypatch.setattr(ta, "run_claude_headless", lambda *a, **k: ta.RunOutcome(terminated_reason="completed"))
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda *_: None)
    monkeypatch.setattr(ta, "_collect_dispatch_stats", lambda run_dir, home: {"overall": {"total": 0}})
    monkeypatch.setattr(ta, "has_test_run_evidence", lambda run_dir: True)

    record = ta._run_one(
        task,
        "kit-gov",
        0,
        workspace=workspace,
        ecc_repo=tmp_path / "ecc-repo-unused",
        homes=homes,
        go_bin=None,
        run_timeout_s=5,
    )

    assert setup_calls and setup_calls[0][1] == task.task_id
    assert record.arm == "kit-gov"
    assert record.kit_test_run_evidence is True
    assert record.dispatch_stats == {"overall": {"total": 0}}


def test_build_report_kit_gov_runs_detail_and_dynamic_per_task_header() -> None:
    records = [
        ta.RunRecord(
            "go/palindrome-products",
            "go",
            "kit-gov",
            0,
            True,
            0.5,
            10,
            10,
            5,
            100.0,
            "completed",
            kit_test_run_evidence=True,
        ),
    ]
    report = ta.build_report(records, total_tasks=1, expected_cost=None, seed=0, rerun_arms=("kit-gov",))
    assert report["kit_gov_runs"] == [
        {
            "task_id": "go/palindrome-products",
            "run_index": 0,
            "num_turns": 5,
            "total_cost_usd": 0.5,
            "wall_seconds": 100.0,
            "test_run_evidence": True,
        }
    ]
    rendered = ta.render_report_markdown(report)
    assert "Détail par run — bras kit-gov" in rendered
    assert "| Tâche | Langue | nu | ecc | kit | kit-gov |" in rendered


def test_main_full_only_replays_the_selected_arms(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--arms kit`` ne doit jamais appeler ``_run_one`` pour nu/ecc, et les
    lignes déjà présentes dans ``results.jsonl`` pour ces bras doivent quand
    même être comptées dans le rapport final (compatibilité ``--resume``)."""
    workspace = tmp_path / "workspace"
    state_dir = workspace / "state"
    state_dir.mkdir(parents=True)

    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: synthetic_bench_root)
    monkeypatch.setattr(ta, "ensure_ecc_repo", lambda ws: (tmp_path / "ecc-repo", "deadbeef"))
    monkeypatch.setattr(ta, "ensure_go_toolchain", lambda ws: None)
    monkeypatch.setattr(ta, "ensure_isolated_home", lambda home: None)

    catalog = ta.discover_catalog(synthetic_bench_root)
    tasks = ta.sample_tasks(catalog, seed=551)
    (state_dir / "selection.json").write_text(
        json.dumps({"seed": 551, "ecc_commit": "deadbeef", "tasks": [t.to_dict() for t in tasks]}),
        encoding="utf-8",
    )

    carried_over_lines = []
    for task in tasks:
        for arm in ("nu", "ecc"):
            for run_index in range(ta.K_REPLAY):
                record = ta.RunRecord(
                    task.task_id, task.language, arm, run_index, True, 0.1, 10, 10, 2, 5.0, "completed",
                    recorded_at="2026-09-17T06:00:00+00:00",
                )
                carried_over_lines.append(json.dumps(record.to_dict()))
    (state_dir / "results.jsonl").write_text("\n".join(carried_over_lines) + "\n", encoding="utf-8")

    called_arms: list[str] = []

    def _fake_run_one(task: Any, arm: str, run_index: int, **kwargs: Any) -> Any:
        del kwargs
        called_arms.append(arm)
        return ta.RunRecord(task.task_id, task.language, arm, run_index, True, 0.05, 5, 5, 1, 2.0, "completed")

    monkeypatch.setattr(ta, "_run_one", _fake_run_one)

    written: dict[str, Any] = {}

    def _fake_write_report(records: Any, out_dir: Path, **kwargs: Any) -> None:
        written["records"] = list(records)
        written["kwargs"] = kwargs

    monkeypatch.setattr(ta, "write_report", _fake_write_report)

    rc = ta.main(["--full", "--resume", "--arms", "kit", "--label", "lot E", "--workspace", str(workspace)])

    assert rc == 0
    assert called_arms and set(called_arms) == {"kit"}
    # Les 20*3 lignes nu/ecc reprises + les nouvelles lignes kit doivent
    # toutes se retrouver dans le rapport final.
    assert len(written["records"]) == len(carried_over_lines) + len(called_arms)
    assert written["kwargs"]["rerun_arms"] == ("kit",)
    assert written["kwargs"]["label"] == "lot E"
