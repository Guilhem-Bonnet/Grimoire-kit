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
import os
import sys
from collections.abc import Iterator
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
    def fake_credentials(home: Path, real_home: Path | None = None) -> Iterator[str]:
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
        grimoire_bin="grimoire",
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
    monkeypatch.setattr(ta, "resolve_grimoire_bin", lambda explicit: "grimoire")
    monkeypatch.setattr(ta, "verify_grimoire_binary_matches_template", lambda *a, **k: None)
    # Lot I (#582) : ces deux garde-fous tourneraient pour de vrai sinon
    # (``shutil.which`` réel, ``_run(["grimoire", "needs", "resolve", ...])``
    # sur un binaire factice) — hors sujet pour ce test, qui vérifie la garde
    # disque, pas la toolchain.
    monkeypatch.setattr(ta, "ensure_system_toolchains_present", lambda languages: None)
    monkeypatch.setattr(ta, "verify_agent_toolchain_environment", lambda *a, **k: {})

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
        toolchain_check: Any = None,
    ) -> None:
        del total_tasks, expected_cost, seed, rerun_arms, label, toolchain_check
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
            test_deps_install_ok=True,
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
            "test_deps_install_ok": True,
            "toolchain_friction_bash_calls": 0,
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
        lambda task_dir, *, kit_home, grimoire_bin, timeout=180, npm_cache_dir=None: {
            "arm": "kit",
            "added": ["x"],
            "init_rc": 0,
            "sync_rc": 0,
        },
    )

    task_dir = tmp_path / "run"
    task_dir.mkdir()
    result = ta.setup_arm_kit_gov(
        task_dir, kit_home=tmp_path / "home", task_id="go/palindrome-products", grimoire_bin="grimoire"
    )

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


# ── 13. Dépendances de test installées avant l'agent (lot H, #582) ─────────
#
# Les lots E/F ont mesuré que l'agent, DANS sa propre session, ne pouvait
# jamais faire aboutir `npm test`/`npx jest` sur une tâche JavaScript :
# `node_modules/` n'existait pas encore à ce moment-là, seule la
# vérification finale du harnais (après la fin du run) installait les
# dépendances — `exit 127` systématique côté agent, sur tous les bras
# également (un confondu de méthode, pas une différence entre bras).


def test_install_test_dependencies_returns_none_without_package_json(tmp_path: Path) -> None:
    task_dir = tmp_path / "run"
    task_dir.mkdir()
    assert ta.install_test_dependencies(task_dir, npm_cache_dir=tmp_path / "npm-cache") is None


def test_install_test_dependencies_runs_npm_install_with_isolated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_dir = tmp_path / "run"
    task_dir.mkdir()
    (task_dir / "package.json").write_text("{}\n", encoding="utf-8")
    npm_cache_dir = tmp_path / "npm-cache"

    calls: list[dict[str, Any]] = []

    def fake_run(cmd: Any, *, cwd: Path | None = None, env: Any = None, timeout: int | None = None) -> Any:
        calls.append({"cmd": list(cmd), "cwd": cwd, "env": env, "timeout": timeout})
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ta, "_run", fake_run)
    result = ta.install_test_dependencies(task_dir, npm_cache_dir=npm_cache_dir, timeout=42)

    assert result == {"attempted": True, "ok": True, "returncode": 0, "stdout": "ok", "stderr": ""}
    assert calls[0]["cmd"][:2] == ["npm", "install"]
    assert calls[0]["cwd"] == task_dir
    # Cache npm forcé sous le workspace du banc — jamais le HOME réel de
    # l'opérateur ni l'un des HOME isolés par bras.
    assert calls[0]["env"]["npm_config_cache"] == str(npm_cache_dir)
    assert calls[0]["timeout"] == 42
    assert npm_cache_dir.is_dir()


def test_install_test_dependencies_is_best_effort_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_dir = tmp_path / "run"
    task_dir.mkdir()
    (task_dir / "package.json").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(ta, "_run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="network down"))
    result = ta.install_test_dependencies(task_dir, npm_cache_dir=tmp_path / "npm-cache")

    assert result is not None
    assert result["ok"] is False
    assert result["returncode"] == 1


def test_setup_arm_nu_installs_test_dependencies_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Équité de méthode (lot H, #582) : le bras `nu` reçoit désormais la
    même installation de dépendances JS que les bras gouvernés, quand on lui
    fournit un `npm_cache_dir` — avant ce lot, `setup_arm_nu` n'était même
    jamais appelée par le harnais (aucun branchement `if arm == "nu"` dans
    `_run_one`/`_do_dry_run`)."""
    task_dir = tmp_path / "run"
    task_dir.mkdir()
    (task_dir / "package.json").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        ta,
        "install_test_dependencies",
        lambda task_dir, *, npm_cache_dir, timeout=120: {"attempted": True, "ok": True, "returncode": 0},
    )
    result = ta.setup_arm_nu(task_dir, npm_cache_dir=tmp_path / "npm-cache")

    assert result == {
        "arm": "nu",
        "added": [],
        "test_deps_install": {"attempted": True, "ok": True, "returncode": 0},
    }


def test_setup_arm_nu_skips_install_without_cache_dir(tmp_path: Path) -> None:
    task_dir = tmp_path / "run"
    task_dir.mkdir()
    (task_dir / "package.json").write_text("{}\n", encoding="utf-8")

    assert ta.setup_arm_nu(task_dir) == {"arm": "nu", "added": []}


def test_run_one_records_test_deps_install_outcome_from_setup_result(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = ta.discover_catalog(synthetic_bench_root)[0]
    workspace = tmp_path / "workspace"
    homes = {arm: workspace / "homes" / arm for arm in ta.ARMS}
    for home in homes.values():
        ta.ensure_isolated_home(home)

    monkeypatch.setattr(
        ta,
        "setup_arm_nu",
        lambda task_dir, *, npm_cache_dir=None: {
            "arm": "nu",
            "added": [],
            "test_deps_install": {"attempted": True, "ok": False, "returncode": 1},
        },
    )

    @contextlib.contextmanager
    def fake_credentials(home: Path, real_home: Path | None = None) -> Iterator[str]:
        del home, real_home
        yield "fake-token"

    monkeypatch.setattr(ta, "credentials_provisioned", fake_credentials)
    monkeypatch.setattr(ta, "run_claude_headless", lambda *a, **k: ta.RunOutcome(terminated_reason="completed"))
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda *_: None)

    record = ta._run_one(
        task,
        "nu",
        0,
        workspace=workspace,
        ecc_repo=tmp_path / "ecc-repo-unused",
        homes=homes,
        go_bin=None,
        run_timeout_s=5,
        grimoire_bin="grimoire",
    )

    assert record.test_deps_install_ok is False


def test_build_report_counts_test_deps_install_per_arm() -> None:
    records = [
        ta.RunRecord(
            "javascript/a", "javascript", "nu", 0, True, 0.1, 10, 10, 1, 5.0, "completed", test_deps_install_ok=True
        ),
        ta.RunRecord(
            "javascript/a", "javascript", "nu", 1, True, 0.1, 10, 10, 1, 5.0, "completed", test_deps_install_ok=False
        ),
        ta.RunRecord("python/b", "python", "nu", 0, True, 0.1, 10, 10, 1, 5.0, "completed"),
    ]
    report = ta.build_report(records, total_tasks=2, expected_cost=None, seed=0)
    assert report["per_arm"]["nu"]["test_deps_install_attempted"] == 2
    assert report["per_arm"]["nu"]["test_deps_install_ok"] == 1
    rendered = ta.render_report_markdown(report)
    assert "Dépendances de test installées avant l'agent" in rendered


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

    def fake_setup_arm_kit_gov(
        task_dir: Path,
        *,
        kit_home: Path,
        task_id: str,
        grimoire_bin: str,
        timeout: int = 180,
        npm_cache_dir: Path | None = None,
    ) -> dict[str, Any]:
        del kit_home, timeout, npm_cache_dir, grimoire_bin
        setup_calls.append((task_dir, task_id))
        return {"arm": "kit-gov", "added": []}

    @contextlib.contextmanager
    def fake_credentials(home: Path, real_home: Path | None = None) -> Iterator[str]:
        del home, real_home
        yield "fake-token"

    monkeypatch.setattr(ta, "setup_arm_kit_gov", fake_setup_arm_kit_gov)
    monkeypatch.setattr(ta, "credentials_provisioned", fake_credentials)
    monkeypatch.setattr(ta, "run_claude_headless", lambda *a, **k: ta.RunOutcome(terminated_reason="completed"))
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda *_: None)
    monkeypatch.setattr(ta, "_collect_dispatch_stats", lambda run_dir, home, *, grimoire_bin: {"overall": {"total": 0}})
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
        grimoire_bin="grimoire",
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
            test_deps_install_ok=None,
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
            "test_deps_install_ok": None,
            "toolchain_friction_bash_calls": 0,
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
    monkeypatch.setattr(ta, "resolve_grimoire_bin", lambda explicit: "grimoire")
    monkeypatch.setattr(ta, "verify_grimoire_binary_matches_template", lambda *a, **k: None)
    # Lot I (#582) : voir la même note dans le test de la garde disque.
    monkeypatch.setattr(ta, "ensure_system_toolchains_present", lambda languages: None)
    monkeypatch.setattr(ta, "verify_agent_toolchain_environment", lambda *a, **k: {})

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


# ── 14. Binaire grimoire résolu en absolu (incident lot H, #582) ───────────
#
# Le 2026-09-18, un lancement avec `PATH=".venv/bin:$PATH"` (entrée
# RELATIVE) a fait retomber chaque appel `grimoire` d'un sous-processus
# (`cwd` = dépôt de tâche jetable) sur le `grimoire` suivant du PATH — celui
# de la Forge, une version antérieure aux lots G — parce qu'une entrée PATH
# relative se résout par rapport au `cwd` du sous-processus, jamais au
# répertoire de lancement du harnais. 27 runs (44 $) ont rejoué l'ancien
# gabarit de directive (847 caractères) au lieu du nouveau (396) sans le
# moindre message d'erreur.


def test_resolve_grimoire_bin_uses_explicit_absolute_path(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin" / "grimoire"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    assert ta.resolve_grimoire_bin(str(fake_bin)) == str(fake_bin.resolve())


def test_resolve_grimoire_bin_rejects_a_relative_or_missing_explicit_path(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="introuvable"):
        ta.resolve_grimoire_bin(str(tmp_path / "does-not-exist"))


def test_resolve_grimoire_bin_falls_back_to_which_resolved_to_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin" / "grimoire"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    monkeypatch.setattr(ta.shutil, "which", lambda name: str(fake_bin))
    assert ta.resolve_grimoire_bin(None) == str(fake_bin.resolve())


def test_resolve_grimoire_bin_raises_when_not_found_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ta.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="introuvable"):
        ta.resolve_grimoire_bin(None)


def test_expected_activation_directive_template_reads_the_worktree_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lit `_DIRECTIVE_TEMPLATE` depuis le fichier source RÉEL de ce
    worktree, sans jamais importer `grimoire` — reproduit la disposition
    `<repo>/scripts/bench/three_arms.py` avec un `<repo>/src/grimoire/...`
    synthétique pour ne pas dépendre du contenu réel, qui peut changer."""
    repo_root = tmp_path / "repo"
    script_path = repo_root / "scripts" / "bench" / "three_arms.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("# placeholder\n", encoding="utf-8")
    source_dir = repo_root / "src" / "grimoire" / "core"
    source_dir.mkdir(parents=True)
    (source_dir / "claude_activation.py").write_text(
        'TASK_ID_PLACEHOLDER = "{task_id}"\n'
        '_DIRECTIVE_TEMPLATE = """[Test] Tâche {task_id} — gabarit synthétique.\n"""\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(ta, "__file__", str(script_path))
    assert ta._expected_activation_directive_template() == "[Test] Tâche {task_id} — gabarit synthétique.\n"


def test_expected_activation_directive_template_raises_when_source_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    script_path = repo_root / "scripts" / "bench" / "three_arms.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("# placeholder\n", encoding="utf-8")

    monkeypatch.setattr(ta, "__file__", str(script_path))
    with pytest.raises(RuntimeError, match="introuvable"):
        ta._expected_activation_directive_template()


def test_verify_grimoire_binary_matches_template_passes_when_served_equals_expected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cas sain : le binaire résolu sert exactement le gabarit attendu — pas
    d'exception, aucun appel modèle n'est donc bloqué."""
    grimoire_bin = str(tmp_path / "venv" / "bin" / "grimoire")
    expected_template = "[Grimoire Standard] gabarit attendu\n"
    monkeypatch.setattr(ta, "_expected_activation_directive_template", lambda: expected_template)

    def fake_run(cmd: Any, *, cwd: Path | None = None, env: Any = None, timeout: int | None = None) -> Any:
        if cmd[0] == grimoire_bin and cmd[1] == "--version":
            return SimpleNamespace(returncode=0, stdout="grimoire 3.56.0", stderr="")
        if cmd[:2] == ["git", "init"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1] == "init":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1:3] == ["host", "sync"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1:3] == ["standard", "init"]:
            # C'est `standard init`, pas `init`/`host sync`, qui écrit
            # `.claude/activation-context.md` — vérifié en isolation.
            (cwd / ".claude").mkdir(parents=True, exist_ok=True)
            (cwd / ".claude" / "activation-context.md").write_text(expected_template, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"commande inattendue : {cmd}")

    monkeypatch.setattr(ta, "_run", fake_run)
    ta.verify_grimoire_binary_matches_template(grimoire_bin, check_dir=tmp_path / "check")


def test_verify_grimoire_binary_matches_template_raises_on_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le cas de l'incident lot H : le binaire résolu sert un gabarit
    différent (ancien kit, autre environnement) — refus avant tout appel
    modèle, message actionnable.

    Reproduit le défaut trouvé dans la première version de ce garde-fou :
    ``_expected_activation_directive_template`` renvoie ici le gabarit du
    CODE SOURCE de CE worktree (« ATTENDU »), totalement indépendant de ce
    que le binaire lui-même croit servir — contrairement à une comparaison
    contre le propre code du binaire, qui ne peut jamais détecter un
    binaire cohérent avec lui-même mais installé ailleurs.
    """
    grimoire_bin = str(tmp_path / "venv" / "bin" / "grimoire")
    monkeypatch.setattr(
        ta, "_expected_activation_directive_template", lambda: "[Grimoire Standard] gabarit ATTENDU (396)\n"
    )

    def fake_run(cmd: Any, *, cwd: Path | None = None, env: Any = None, timeout: int | None = None) -> Any:
        if cmd[0] == grimoire_bin and cmd[1] == "--version":
            return SimpleNamespace(returncode=0, stdout="grimoire 3.55.0", stderr="")
        if cmd[:2] == ["git", "init"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1] == "init":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1:3] == ["host", "sync"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1:3] == ["standard", "init"]:
            (cwd / ".claude").mkdir(parents=True, exist_ok=True)
            (cwd / ".claude" / "activation-context.md").write_text(
                "[Grimoire Standard — activation]\nAncien gabarit servi (847)\n", encoding="utf-8"
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"commande inattendue : {cmd}")

    monkeypatch.setattr(ta, "_run", fake_run)
    with pytest.raises(RuntimeError, match="GARDE-FOU LOT H"):
        ta.verify_grimoire_binary_matches_template(grimoire_bin, check_dir=tmp_path / "check")


def test_verify_grimoire_binary_matches_template_raises_when_version_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ta, "_run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="command not found")
    )
    with pytest.raises(RuntimeError, match="--version"):
        ta.verify_grimoire_binary_matches_template("grimoire", check_dir=tmp_path / "check")


def test_setup_arm_kit_invokes_the_resolved_absolute_binary_not_a_path_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le coeur de l'incident lot H : `setup_arm_kit` doit invoquer
    `grimoire_bin` tel quel (argv[0]), jamais la chaîne littérale
    `"grimoire"` qu'une résolution PATH pourrait faire retomber sur un autre
    environnement."""
    grimoire_bin = str(tmp_path / "venv" / "bin" / "grimoire")
    calls: list[list[str]] = []

    def fake_run(cmd: Any, *, cwd: Path | None = None, env: Any = None, timeout: int | None = None) -> Any:
        calls.append(list(cmd))
        assert env is not None
        # PATH absolu en tête, jamais une entrée relative.
        assert env["PATH"].split(os.pathsep)[0] == str(Path(grimoire_bin).parent)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ta, "_run", fake_run)
    task_dir = tmp_path / "run"
    task_dir.mkdir()
    ta.setup_arm_kit(task_dir, kit_home=tmp_path / "home", grimoire_bin=grimoire_bin)

    assert calls[0][0] == grimoire_bin
    assert calls[1][0] == grimoire_bin
    assert all(call[0] != "grimoire" for call in calls)


# ── 15. Environnement unique agent/harnais (lot I, #582) ────────────────────
#
# Le rejeu du lot H (docs/bench/rejeu-lot-h-2026-09-18.md §3) a mesuré que
# `grimoire standard gate check --strict` exécute la commande de test DANS la
# session de l'agent, mais que le harnais ne provisionnait la toolchain Go
# que pour SA PROPRE vérification finale : jusqu'à 28 tours perdus à
# chercher le binaire `go`. Ces tests verrouillent que `run_environment()`
# calcule un environnement partagé, jamais recalculé séparément, et que la
# vérification de toolchain refuse de dépenser un $ si la commande de test
# ne peut pas s'exécuter dans cet environnement.


def test_run_environment_puts_go_bin_ahead_of_path_for_every_arm(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    go_bin = workspace / "tools" / "go" / "go" / "bin" / "go"
    go_bin.parent.mkdir(parents=True)
    go_bin.touch()
    home = tmp_path / "home"

    for arm in ta.ARMS:
        env = ta.run_environment(
            tmp_path / "task",
            arm,
            home=home,
            grimoire_bin=str(tmp_path / "venv" / "bin" / "grimoire"),
            workspace=workspace,
            go_bin=go_bin,
        )
        assert env["PATH"].split(os.pathsep)[0] == str(go_bin.parent)
        # Go est provisionné SOUS le workspace de ce harnais : GOROOT en
        # découle directement, jamais un `go` système dont la disposition
        # des répertoires n'est pas garantie.
        assert env["GOROOT"] == str(go_bin.parent.parent)
        assert env["GOPATH"] == str(workspace / "tools" / "go-path")
        assert env["GOCACHE"] == str(workspace / "tools" / "go-build-cache")
        assert env["HOME"] == str(home)


def test_run_environment_only_exposes_grimoire_bin_to_governed_arms(tmp_path: Path) -> None:
    """`nu`/`ecc` ne doivent jamais découvrir `grimoire` par un effet de bord
    de ce harnais — seuls `kit`/`kit-gov` en ont besoin (`gate check`)."""
    workspace = tmp_path / "workspace"
    grimoire_dir = tmp_path / "venv" / "bin"
    grimoire_bin = str(grimoire_dir / "grimoire")
    home = tmp_path / "home"

    for arm in ("nu", "ecc"):
        env = ta.run_environment(tmp_path / "task", arm, home=home, grimoire_bin=grimoire_bin, workspace=workspace)
        assert str(grimoire_dir) not in env["PATH"].split(os.pathsep)
        assert "GRIMOIRE_NO_COCKPIT" not in env

    for arm in ("kit", "kit-gov"):
        env = ta.run_environment(tmp_path / "task", arm, home=home, grimoire_bin=grimoire_bin, workspace=workspace)
        assert str(grimoire_dir) in env["PATH"].split(os.pathsep)
        assert env["GRIMOIRE_NO_COCKPIT"] == "1"


def test_run_environment_redirects_cargo_and_rust_homes_to_the_real_home_by_default(
    tmp_path: Path,
) -> None:
    """Sans CARGO_HOME/RUSTUP_HOME déjà redirigés par l'opérateur, le `HOME`
    isolé (utilisé pour l'authentification Claude Code) ferait chercher à
    `cargo`/`rustup` une toolchain sous un `$HOME/.cargo` qui n'existe pas
    pour cet utilisateur isolé — même si Rust est bien installé sur le
    poste. Défaut : celui de *real_home*, jamais celui de `home`."""
    workspace = tmp_path / "workspace"
    real_home = tmp_path / "real-home"
    home = tmp_path / "isolated-home"

    env = ta.run_environment(
        tmp_path / "task",
        "kit-gov",
        home=home,
        grimoire_bin=str(tmp_path / "grimoire"),
        workspace=workspace,
        real_home=real_home,
        base_env={"PATH": "/usr/bin"},
    )

    assert env["CARGO_HOME"] == str(real_home / ".cargo")
    assert env["RUSTUP_HOME"] == str(real_home / ".rustup")
    assert env["CARGO_TARGET_DIR"] == str(workspace / "tools" / "cargo-target")


def test_run_environment_respects_an_already_redirected_cargo_home(tmp_path: Path) -> None:
    """Un opérateur qui redirige déjà CARGO_HOME/RUSTUP_HOME (plusieurs
    toolchains Rust sur le même poste) doit voir sa redirection respectée —
    jamais écrasée silencieusement par le défaut de *real_home*."""
    workspace = tmp_path / "workspace"
    custom_cargo_home = tmp_path / "custom-cargo"

    env = ta.run_environment(
        tmp_path / "task",
        "kit-gov",
        home=tmp_path / "home",
        grimoire_bin=str(tmp_path / "grimoire"),
        workspace=workspace,
        base_env={"PATH": "/usr/bin", "CARGO_HOME": str(custom_cargo_home)},
    )

    assert env["CARGO_HOME"] == str(custom_cargo_home)


def test_run_environment_sets_npm_cache_when_provided(tmp_path: Path) -> None:
    npm_cache_dir = tmp_path / "npm-cache"
    env = ta.run_environment(
        tmp_path / "task",
        "nu",
        home=tmp_path / "home",
        grimoire_bin=str(tmp_path / "grimoire"),
        workspace=tmp_path / "workspace",
        npm_cache_dir=npm_cache_dir,
    )
    assert env["npm_config_cache"] == str(npm_cache_dir)


def test_run_one_builds_the_environment_once_and_shares_it_with_both_calls(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le coeur du lot I : `_run_one` ne doit calculer `run_environment()`
    qu'UNE fois, et transmettre le MÊME dict à `run_claude_headless` et
    `run_hidden_tests` — jamais deux calculs qui pourraient diverger."""
    task = ta.discover_catalog(synthetic_bench_root)[0]
    workspace = tmp_path / "workspace"
    homes = {arm: workspace / "homes" / arm for arm in ta.ARMS}
    for home in homes.values():
        ta.ensure_isolated_home(home)

    seen_envs: list[dict[str, str]] = []

    @contextlib.contextmanager
    def fake_credentials(home: Path, real_home: Path | None = None) -> Iterator[str]:
        del home, real_home
        yield "fake-token"

    def fake_run_claude_headless(task_dir: Path, prompt: str, *, home: Path, env: Any = None, **kwargs: Any) -> Any:
        del task_dir, prompt, home, kwargs
        seen_envs.append(env)
        return ta.RunOutcome(terminated_reason="completed")

    def fake_run_hidden_tests(
        task: Any, task_dir: Path, hidden_dir: Path, *, go_bin: Any = None, env: Any = None
    ) -> Any:
        del task, task_dir, hidden_dir, go_bin
        seen_envs.append(env)
        return (True, "")

    monkeypatch.setattr(ta, "credentials_provisioned", fake_credentials)
    monkeypatch.setattr(ta, "run_claude_headless", fake_run_claude_headless)
    monkeypatch.setattr(ta, "run_hidden_tests", fake_run_hidden_tests)
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda run_dir: None)

    ta._run_one(
        task,
        "nu",
        0,
        workspace=workspace,
        ecc_repo=tmp_path / "ecc-repo-unused",
        homes=homes,
        go_bin=None,
        run_timeout_s=5,
        grimoire_bin="grimoire",
    )

    assert len(seen_envs) == 2
    assert seen_envs[0] is seen_envs[1]


def test_is_toolchain_friction_command_matches_the_documented_patterns() -> None:
    friction_examples = [
        "command -v go",
        "which -a go",
        "find / -maxdepth 6 -name gofmt",
        "rustup toolchain list",
        "rustup default stable",
        "npm install --save-dev jest",
    ]
    for command in friction_examples:
        assert ta.is_toolchain_friction_command(command), command

    non_friction_examples = [
        "go test ./...",
        "cargo test --quiet",
        "npm test",
        "python -m pytest -q",
        "git status",
    ]
    for command in non_friction_examples:
        assert not ta.is_toolchain_friction_command(command), command


def test_count_toolchain_friction_bash_calls_counts_only_matching_commands() -> None:
    commands = ["git status", "command -v cargo", "cargo build", "rustup show"]
    assert ta.count_toolchain_friction_bash_calls(commands) == 2


def test_run_claude_headless_records_toolchain_friction_from_the_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _bash_event(command: str) -> str:
        content = [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]
        return json.dumps({"type": "assistant", "message": {"content": content}})

    class _FakePopenWithFriction(_FakePopen):
        def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
            del cmd, cwd, env, stderr
            lines = [
                _bash_event("command -v go"),
                _bash_event("go test ./..."),
                json.dumps({"type": "result", "total_cost_usd": 0.01, "num_turns": 2, "usage": {}}),
            ]
            stdout.write(("\n".join(lines) + "\n").encode("utf-8"))
            stdout.flush()

    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    monkeypatch.setattr(ta.subprocess, "Popen", _FakePopenWithFriction)

    outcome = ta.run_claude_headless(task_dir, "peu importe", home=tmp_path, timeout_s=5, poll_interval=0.0)

    assert outcome.toolchain_friction_bash_calls == 1


def test_ensure_system_toolchains_present_passes_when_binaries_are_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ta.shutil, "which", lambda binary: f"/usr/bin/{binary}")
    ta.ensure_system_toolchains_present(["rust", "javascript", "python", "go"])


def test_ensure_system_toolchains_present_raises_before_any_spend_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ta.shutil, "which", lambda binary: None)
    with pytest.raises(SystemExit, match="cargo"):
        ta.ensure_system_toolchains_present(["rust"])


def test_toolchain_failure_reason_is_none_for_a_legitimate_test_failure() -> None:
    """Un test qui échoue faute de solution (assertion classique) n'est PAS
    une friction de toolchain — le code de sortie seul n'est jamais le
    signal (lot I, #582 : « code de sortie non pertinent »)."""
    assert ta._toolchain_failure_reason(1, "", "AssertionError: assert 1 == 42") is None


def test_toolchain_failure_reason_flags_exit_127() -> None:
    reason = ta._toolchain_failure_reason(127, "", "bash: go: command not found")
    assert reason is not None
    assert "127" in reason


def test_toolchain_failure_reason_flags_a_not_found_marker_even_off_127() -> None:
    reason = ta._toolchain_failure_reason(1, "", "sh: 1: jest: not found")
    assert reason is not None


def test_verify_agent_toolchain_environment_passes_when_the_command_runs(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    tasks = [next(t for t in catalog if t.language == "python")]
    workspace = tmp_path / "workspace"

    def fake_run(cmd: Any, *, cwd: Any = None, env: Any = None, timeout: Any = None) -> Any:
        if cmd[1:3] == ["needs", "resolve"]:
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"test-runner": {"command": "python -m pytest -q"}}), stderr=""
            )
        # commande de test elle-même : échoue faute de solution, pas de toolchain.
        return SimpleNamespace(returncode=1, stdout="", stderr="AssertionError")

    monkeypatch.setattr(ta, "_run", fake_run)

    report = ta.verify_agent_toolchain_environment(
        tasks, workspace=workspace, grimoire_bin="grimoire", go_bin=None, npm_cache_dir=workspace / "npm-cache"
    )

    assert report["python"]["ok"] is True
    assert report["python"]["command"] == "python -m pytest -q"


def test_verify_agent_toolchain_environment_raises_before_any_spend_on_exit_127(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le coeur de la garde : une commande de test résolue mais introuvable
    dans l'environnement de l'agent (toolchain absente) doit arrêter la
    campagne avant le premier appel `claude -p` — jamais un run payant qui
    découvre l'absence en pleine session."""
    catalog = ta.discover_catalog(synthetic_bench_root)
    tasks = [next(t for t in catalog if t.language == "go")]
    workspace = tmp_path / "workspace"

    def fake_run(cmd: Any, *, cwd: Any = None, env: Any = None, timeout: Any = None) -> Any:
        if cmd[1:3] == ["needs", "resolve"]:
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"test-runner": {"command": "go test ./..."}}), stderr=""
            )
        return SimpleNamespace(returncode=127, stdout="", stderr="bash: go: command not found")

    monkeypatch.setattr(ta, "_run", fake_run)

    with pytest.raises(RuntimeError, match="GARDE-FOU LOT I"):
        ta.verify_agent_toolchain_environment(
            tasks, workspace=workspace, grimoire_bin="grimoire", go_bin=None, npm_cache_dir=workspace / "npm-cache"
        )


def test_verify_agent_toolchain_environment_skips_when_test_runner_is_unresolved(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    tasks = [next(t for t in catalog if t.language == "python")]
    workspace = tmp_path / "workspace"

    def fake_run(cmd: Any, *, cwd: Any = None, env: Any = None, timeout: Any = None) -> Any:
        assert cmd[1:3] == ["needs", "resolve"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"test-runner": {"command": None}}), stderr="")

    monkeypatch.setattr(ta, "_run", fake_run)

    report = ta.verify_agent_toolchain_environment(
        tasks, workspace=workspace, grimoire_bin="grimoire", go_bin=None, npm_cache_dir=workspace / "npm-cache"
    )

    assert report["python"]["ok"] is True
    assert report["python"]["command"] is None


def test_verify_agent_toolchain_environment_raises_when_npm_install_fails(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = ta.discover_catalog(synthetic_bench_root)
    tasks = [next(t for t in catalog if t.language == "javascript")]
    workspace = tmp_path / "workspace"

    def fake_install_test_dependencies(task_dir: Path, *, npm_cache_dir: Path, timeout: int = 120) -> Any:
        del task_dir, npm_cache_dir, timeout
        return {"attempted": True, "ok": False, "returncode": 1, "stderr": "registre injoignable"}

    def _fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("`grimoire needs resolve` ne doit jamais tourner après un npm install en échec")

    monkeypatch.setattr(ta, "install_test_dependencies", fake_install_test_dependencies)
    monkeypatch.setattr(ta, "_run", _fail_if_called)

    with pytest.raises(RuntimeError, match="GARDE-FOU LOT I"):
        ta.verify_agent_toolchain_environment(
            tasks, workspace=workspace, grimoire_bin="grimoire", go_bin=None, npm_cache_dir=workspace / "npm-cache"
        )
