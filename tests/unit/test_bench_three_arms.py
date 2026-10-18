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
import dataclasses
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


@pytest.fixture(autouse=True)
def _no_real_claude_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """B-01 : ``claude --version`` ne part jamais en sous-processus réel depuis ces tests."""
    monkeypatch.setattr(ta, "detect_claude_code_version", lambda **k: "9.9.9 (Claude Code)")


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


def test_prepare_task_repo_refuses_non_empty_directory(synthetic_bench_root: Path, tmp_path: Path) -> None:
    """Lot J : prepare_task_repo ne doit pas réutiliser un dossier existant.

    Reproduit le défaut #694 : appeler prepare_task_repo deux fois avec la
    même destination reléve les tests cachés de la première tâche dans la
    seconde préparation.
    """
    catalog = ta.discover_catalog(synthetic_bench_root)
    task1 = next(t for t in catalog if t.slug == "python-ex-0")
    task2 = next(t for t in catalog if t.slug == "python-ex-1")
    dest = tmp_path / "task-repo"

    # Premier appel : prépare la tâche 1
    ta.prepare_task_repo(task1, dest)
    assert (dest / "TASK.md").is_file()
    assert (dest / "python_ex_0.py").is_file()
    assert not (dest / "python_ex_0_test.py").exists()  # tests cachés

    # Deuxième appel : doit refuser de réutiliser le dossier existant
    with pytest.raises(FileExistsError):
        ta.prepare_task_repo(task2, dest)
    # En l'absence de refus, les tests cachés de task1 seraient visibles
    # dans le dépôt de task2 (contamination).


def _git(repo: Path, *args: str) -> None:
    ta.subprocess.run(
        ["git", "-c", "user.email=t@t.local", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        encoding="utf-8",
    )


def _task(root: Path, slug: str = "python-ex-0") -> Any:
    return next(t for t in ta.discover_catalog(root) if t.slug == slug)


def test_assert_task_repo_clean_accepts_fresh_repo(synthetic_bench_root: Path, tmp_path: Path) -> None:
    task = _task(synthetic_bench_root)
    dest = tmp_path / "repo"
    ta.prepare_task_repo(task, dest)
    ta.assert_task_repo_clean(task, dest)  # ne lève pas


def test_assert_task_repo_clean_rejects_two_commits(synthetic_bench_root: Path, tmp_path: Path) -> None:
    task = _task(synthetic_bench_root)
    dest = tmp_path / "repo"
    ta.prepare_task_repo(task, dest)
    (dest / "extra.txt").write_text("x\n", encoding="utf-8")
    _git(dest, "add", "extra.txt")
    _git(dest, "commit", "-q", "-m", "second")
    with pytest.raises(ta.ContaminatedTaskRepoError, match="1 commit"):
        ta.assert_task_repo_clean(task, dest)


def test_assert_task_repo_clean_rejects_tracked_hidden_test(synthetic_bench_root: Path, tmp_path: Path) -> None:
    task = _task(synthetic_bench_root)
    dest = tmp_path / "repo"
    ta.prepare_task_repo(task, dest, include_tests=True)  # un seul commit, mais le test est suivi
    with pytest.raises(ta.ContaminatedTaskRepoError, match=r"python_ex_0_test\.py"):
        ta.assert_task_repo_clean(task, dest)


def _patch_run_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, calls: list[Path]) -> dict[str, Path]:
    """Neutralise tout ce qui n'est pas la préparation ; ``claude`` est tracé."""
    homes = {arm: tmp_path / "home" / arm for arm in ta.ARMS}
    for h in homes.values():
        h.mkdir(parents=True)
    monkeypatch.setattr(ta, "setup_arm_nu", lambda *a, **k: None)
    monkeypatch.setattr(ta, "run_environment", lambda *a, **k: {})
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda *a, **k: None)

    def _fake_claude(task_dir: Path, *a: Any, **k: Any) -> Any:
        calls.append(task_dir)
        return ta.RunOutcome()

    monkeypatch.setattr(ta, "run_claude_headless", _fake_claude)
    return homes


def _call_run_one(task: Any, workspace: Path, homes: dict[str, Path]) -> Any:
    return ta._run_one(
        task,
        "nu",
        0,
        workspace=workspace,
        ecc_repo=workspace / "ecc",
        homes=homes,
        go_bin=None,
        run_timeout_s=5,
        grimoire_bin="grimoire",
        auth_mode="api-key",
    )


def test_run_one_raises_before_any_model_call_when_repo_contaminated(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W1-08a : un dépôt contaminé n'est PAS un résultat (pas de RunRecord) :
    l'erreur remonte, ``claude`` n'est jamais appelé."""
    task = _task(synthetic_bench_root)
    calls: list[Path] = []
    homes = _patch_run_one(monkeypatch, tmp_path, calls)
    real_prepare = ta.prepare_task_repo
    # préparation fautive : le test caché est commité (état de la contamination du lot J)
    monkeypatch.setattr(ta, "prepare_task_repo", lambda t, d, **k: real_prepare(t, d, include_tests=True))
    with pytest.raises(ta.ContaminatedTaskRepoError):
        _call_run_one(task, tmp_path / "ws", homes)
    assert calls == []  # claude n'a jamais été appelé


def _patch_main_environment(monkeypatch: pytest.MonkeyPatch, synthetic_bench_root: Path, tmp_path: Path) -> None:
    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: synthetic_bench_root)
    monkeypatch.setattr(ta, "ensure_ecc_repo", lambda ws: (tmp_path / "ecc-repo", "deadbeef"))
    monkeypatch.setattr(ta, "ensure_go_toolchain", lambda ws: None)
    monkeypatch.setattr(ta, "disk_guard_ok", lambda workspace, min_free_gb=ta.DISK_GUARD_MIN_FREE_GB: True)
    monkeypatch.setattr(ta, "resolve_grimoire_bin", lambda explicit: "grimoire")
    monkeypatch.setattr(ta, "verify_grimoire_binary_matches_template", lambda *a, **k: None)
    monkeypatch.setattr(ta, "ensure_system_toolchains_present", lambda languages: None)
    monkeypatch.setattr(ta, "verify_agent_toolchain_environment", lambda *a, **k: {})


def test_main_stops_without_recording_when_repo_contaminated(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W1-08a, « arrêt à 0 dollar » : la campagne s'arrête (code non nul), rien
    n'est écrit dans results.jsonl (sinon ``--resume`` sauterait la clé et le
    run compterait comme un échec), aucun rapport n'est produit."""
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    attempts: list[tuple[str, str, int]] = []

    def _contaminated(task: Any, arm: str, run_index: int, **kwargs: Any) -> Any:
        attempts.append((task.task_id, arm, run_index))
        raise ta.ContaminatedTaskRepoError(f"{task.task_id} contaminé")

    def _no_report(*a: Any, **k: Any) -> None:
        raise AssertionError("aucun rapport ne doit être écrit après une contamination")

    monkeypatch.setattr(ta, "_run_one", _contaminated)
    monkeypatch.setattr(ta, "write_report", _no_report)

    rc = ta.main(["--full", "--model", "claude-test", "--workspace", str(workspace)])

    assert rc != 0
    assert len(attempts) == 1  # la campagne s'arrête au premier dépôt contaminé
    results = workspace / "state" / "results.jsonl"
    assert not results.exists() or results.read_text(encoding="utf-8") == ""
    assert "contamin" in capsys.readouterr().err


def test_run_one_replays_from_clean_dir_after_interrupted_run(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(synthetic_bench_root)
    calls: list[Path] = []
    homes = _patch_run_one(monkeypatch, tmp_path, calls)
    workspace = tmp_path / "ws"
    run_dir = workspace / "tasks" / task.task_id.replace("/", "__") / "nu" / "run0"
    run_dir.mkdir(parents=True)
    (run_dir / "TASK.md").write_text("résidu\n", encoding="utf-8")
    (run_dir / "python_ex_0_test.py").write_text("résidu\n", encoding="utf-8")
    record = _call_run_one(task, workspace, homes)
    assert calls == [run_dir]
    assert record.terminated_reason == "completed"
    assert not (run_dir / "python_ex_0_test.py").exists()
    ta.assert_task_repo_clean(task, run_dir)


def test_do_dry_run_can_run_twice_on_same_workspace(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(synthetic_bench_root)
    for name in ("setup_arm_nu", "setup_arm_ecc", "setup_arm_kit", "setup_arm_kit_gov"):
        monkeypatch.setattr(ta, name, lambda *a, **k: None)
    homes = {arm: tmp_path / "home" / arm for arm in ta.ARMS}
    kwargs: dict[str, Any] = {"workspace": tmp_path / "ws", "ecc_repo": tmp_path / "ecc", "homes": homes, "grimoire_bin": "g"}
    assert ta._do_dry_run([task], **kwargs) == 0
    assert ta._do_dry_run([task], **kwargs) == 0


def test_do_dry_run_returns_error_when_repo_contaminated(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _task(synthetic_bench_root)
    real_prepare = ta.prepare_task_repo
    monkeypatch.setattr(ta, "prepare_task_repo", lambda t, d, **k: real_prepare(t, d, include_tests=True))
    homes = {arm: tmp_path / "home" / arm for arm in ta.ARMS}
    kwargs: dict[str, Any] = {"workspace": tmp_path / "ws", "ecc_repo": tmp_path / "ecc", "homes": homes, "grimoire_bin": "g"}
    assert ta._do_dry_run([task], **kwargs) == 1


# ── W1-08a : tests cachés présents dans le dépôt (suivis ou non) ───────────


def test_assert_task_repo_clean_rejects_untracked_hidden_test(synthetic_bench_root: Path, tmp_path: Path) -> None:
    task = _task(synthetic_bench_root)
    dest = tmp_path / "repo"
    ta.prepare_task_repo(task, dest)
    (dest / "python_ex_0_test.py").write_text("def test_x(): pass\n", encoding="utf-8")  # jamais `git add`
    with pytest.raises(ta.ContaminatedTaskRepoError, match=r"python_ex_0_test\.py"):
        ta.assert_task_repo_clean(task, dest)


def test_assert_task_repo_clean_rejects_git_ignored_hidden_test(synthetic_bench_root: Path, tmp_path: Path) -> None:
    task = _task(synthetic_bench_root)
    dest = tmp_path / "repo"
    ta.prepare_task_repo(task, dest)
    with (dest / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
        fh.write("python_ex_0_test.py\n")
    (dest / "python_ex_0_test.py").write_text("def test_x(): pass\n", encoding="utf-8")
    with pytest.raises(ta.ContaminatedTaskRepoError, match=r"python_ex_0_test\.py"):
        ta.assert_task_repo_clean(task, dest)


def test_assert_task_repo_clean_names_tracked_non_ascii_hidden_test(synthetic_bench_root: Path, tmp_path: Path) -> None:
    """``git ls-files`` met entre guillemets les noms non ASCII : sans ``-z``,
    un test suivi mais absent du disque échapperait à la comparaison."""
    task = dataclasses.replace(_task(synthetic_bench_root), test_files=("tést.py",))
    dest = tmp_path / "repo"
    ta.prepare_task_repo(task, dest)
    (dest / "tést.py").write_text("def test_x(): pass\n", encoding="utf-8")
    _git(dest, "add", "tést.py")
    _git(dest, "commit", "-q", "--amend", "--no-edit")
    (dest / "tést.py").unlink()  # suivi par git, absent du disque
    with pytest.raises(ta.ContaminatedTaskRepoError, match="suivi"):
        ta.assert_task_repo_clean(task, dest)


# ── W1-08a : reset_run_dir tolère les fichiers en lecture seule ────────────


def test_reset_run_dir_removes_read_only_tree(tmp_path: Path) -> None:
    """Les objets git sont en 0444 (échec de ``rmtree`` sous Windows) ; un
    dossier en lecture seule reproduit le même ``PermissionError`` sous Linux."""
    run_dir = tmp_path / "run0"
    objects = run_dir / ".git" / "objects" / "ab"
    objects.mkdir(parents=True)
    obj = objects / "cdef"
    obj.write_text("x\n", encoding="utf-8")
    obj.chmod(0o444)
    objects.chmod(0o555)
    try:
        ta.reset_run_dir(run_dir)
    finally:
        if objects.exists():
            objects.chmod(0o755)
    assert not run_dir.exists()


def test_reset_run_dir_fails_loudly_when_dir_survives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run0"
    run_dir.mkdir()
    monkeypatch.setattr(ta.shutil, "rmtree", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="run0"):
        ta.reset_run_dir(run_dir)


def test_errata_lot_j_contamination_is_present() -> None:
    """L'errata existe, date correctement le lot J et ne sur-affirme pas."""
    errata_path = ROOT / "docs" / "bench" / "ERRATA-lot-j-contamination.md"
    assert errata_path.is_file(), f"L'errata doit exister à {errata_path}"
    content = errata_path.read_text(encoding="utf-8")
    assert "Lot J" in content
    assert "#694" in content
    assert "W1-08a" in content
    assert "2026-09-18" in content  # date du lot J (rejeu-lot-j-2026-09-18.md)
    assert "30/60 runs" in content or "30 des 60 runs" in content
    assert "_scratch/" not in content  # chemin hors dépôt
    assert "Fermé" not in content  # la lecture de tests pendant le run reste ouverte (W1-08b)
    assert "W1-08b" in content
    # seul kit-gov a été rejoué au lot J : nu/ecc/kit viennent du lot F
    assert "repris du lot F" in content
    assert "dossiers frais pour" not in content
    # un dépôt contaminé arrête la campagne, il n'est pas enregistré comme un échec
    assert "terminated_reason" not in content
    # T3-01 : les lots K et L ne sont PAS sains (le lot K a lu la suite masquée)
    assert "sont sains" not in content
    assert "python/bowling" in content
    assert "2026-09-29T22:26:27" in content
    # la disposition expose deux choses à l'agent : hidden-tests/ et les tests copiés dans les run<i>
    assert "hidden-tests/" in content
    assert "run<i>" in content


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

    rc = ta.main(["--full", "--model", "claude-test", "--workspace", str(workspace)])

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
                    recorded_at="2026-09-17T06:00:00+00:00", model_id="claude-test",
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

    rc = ta.main(["--full", "--model", "claude-test", "--resume", "--arms", "kit", "--label", "lot E", "--workspace", str(workspace)])

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


# ── Mode d'authentification (lot J, #582) ───────────────────────────────────


def test_resolve_auth_mode_picks_api_key_when_present_in_environment() -> None:
    assert ta.resolve_auth_mode(None, env={"ANTHROPIC_API_KEY": "sk-ant-fake"}) == "api-key"


def test_resolve_auth_mode_falls_back_to_oauth_copy_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    assert ta.resolve_auth_mode(None, env={}) == "oauth-copy"
    err = capsys.readouterr().err
    assert "rotation" in err
    assert "oauth-copy" in err


def test_resolve_auth_mode_explicit_choice_overrides_the_environment() -> None:
    # --auth oauth-copy explicite, même avec une clé API présente : le choix
    # de l'opérateur n'est jamais silencieusement contredit.
    assert ta.resolve_auth_mode("oauth-copy", env={"ANTHROPIC_API_KEY": "sk-ant-fake"}) == "oauth-copy"
    assert ta.resolve_auth_mode("api-key", env={"ANTHROPIC_API_KEY": "sk-ant-fake"}) == "api-key"


def test_resolve_auth_mode_raises_when_api_key_explicit_but_missing() -> None:
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        ta.resolve_auth_mode("api-key", env={})


def test_resolve_auth_mode_raises_on_unknown_mode() -> None:
    with pytest.raises(SystemExit, match="inconnu"):
        ta.resolve_auth_mode("bearer-token", env={})


def test_run_claude_headless_adds_bare_flag_in_api_key_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doc Claude Code (``code.claude.com/docs/en/headless.md``, « Start
    faster with bare mode ») : ``--bare`` ignore explicitement le trousseau
    OAuth et le système de keychain — c'est ce qui rend le mode ``api-key``
    étanche à la copie d'identifiant."""
    seen_cmds: list[list[str]] = []

    class _RecordingFakePopen(_FakePopen):
        def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
            seen_cmds.append(cmd)
            super().__init__(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr)

    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    monkeypatch.setattr(ta.subprocess, "Popen", _RecordingFakePopen)

    ta.run_claude_headless(
        task_dir, "peu importe", home=tmp_path, timeout_s=5, poll_interval=0.0, auth_mode="api-key"
    )

    assert "--bare" in seen_cmds[0]


def test_run_claude_headless_omits_bare_flag_in_oauth_copy_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_cmds: list[list[str]] = []

    class _RecordingFakePopen(_FakePopen):
        def __init__(self, cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any, stderr: Any) -> None:
            seen_cmds.append(cmd)
            super().__init__(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr)

    task_dir = tmp_path / "run0"
    task_dir.mkdir()
    monkeypatch.setattr(ta.subprocess, "Popen", _RecordingFakePopen)

    ta.run_claude_headless(task_dir, "peu importe", home=tmp_path, timeout_s=5, poll_interval=0.0)

    assert "--bare" not in seen_cmds[0]


def test_run_one_api_key_mode_never_calls_credentials_provisioned_nor_writes_any_credentials(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coeur du lot J : en mode ``api-key``, aucun identifiant OAuth n'est
    jamais copié — la fonction qui le ferait ne doit même pas être appelée,
    et aucun ``.credentials.json`` ne doit exister sous le `HOME` isolé du
    run, à aucun moment."""
    task = ta.discover_catalog(synthetic_bench_root)[0]
    workspace = tmp_path / "workspace"
    homes = {arm: workspace / "homes" / arm for arm in ta.ARMS}
    for home in homes.values():
        ta.ensure_isolated_home(home)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("credentials_provisioned ne doit jamais être appelé en mode api-key")

    monkeypatch.setattr(ta, "credentials_provisioned", boom)
    seen_auth_modes: list[str | None] = []

    def fake_run_claude_headless(*args: Any, **kwargs: Any) -> Any:
        seen_auth_modes.append(kwargs.get("auth_mode"))
        assert not (homes["nu"] / ta.CREDENTIALS_REL_PATH).exists()
        return ta.RunOutcome(terminated_reason="completed")

    monkeypatch.setattr(ta, "run_claude_headless", fake_run_claude_headless)
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda run_dir: None)

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
        auth_mode="api-key",
    )

    assert seen_auth_modes == ["api-key"]
    assert record.auth_mode == "api-key"
    assert not (homes["nu"] / ta.CREDENTIALS_REL_PATH).exists()


def test_run_one_oauth_copy_mode_is_unchanged(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le mode par défaut (pas de ``auth_mode`` explicite) doit continuer à
    passer par ``credentials_provisioned`` exactement comme avant ce lot."""
    task = ta.discover_catalog(synthetic_bench_root)[0]
    workspace = tmp_path / "workspace"
    homes = {arm: workspace / "homes" / arm for arm in ta.ARMS}
    for home in homes.values():
        ta.ensure_isolated_home(home)

    calls: list[Path] = []

    @contextlib.contextmanager
    def fake_credentials(home: Path, real_home: Path | None = None) -> Iterator[str]:
        del real_home
        calls.append(home)
        yield "fake-token"

    monkeypatch.setattr(ta, "credentials_provisioned", fake_credentials)
    monkeypatch.setattr(ta, "run_claude_headless", lambda *a, **k: ta.RunOutcome(terminated_reason="completed"))
    monkeypatch.setattr(ta, "run_hidden_tests", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ta, "cleanup_build_artifacts", lambda run_dir: None)

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

    assert calls == [homes["nu"]]
    assert record.auth_mode == "oauth-copy"


def test_find_leaked_api_keys_returns_empty_without_an_api_key(tmp_path: Path) -> None:
    assert ta.find_leaked_api_keys(tmp_path, api_key=None) == []


def test_find_leaked_api_keys_detects_key_in_isolated_home_and_in_run_log(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    homes_dir = workspace / "homes"
    tasks_dir = workspace / "tasks"

    leaked_home_file = homes_dir / "kit" / ".claude" / "settings.local.json"
    leaked_home_file.parent.mkdir(parents=True)
    leaked_home_file.write_text('{"token": "sk-ant-super-secret"}', encoding="utf-8")

    clean_home_file = homes_dir / "nu" / ".claude" / "settings.local.json"
    clean_home_file.parent.mkdir(parents=True)
    clean_home_file.write_text("{}", encoding="utf-8")

    leaked_log = tasks_dir / "python__ex-0" / "kit" / "run0.stream.jsonl"
    leaked_log.parent.mkdir(parents=True)
    leaked_log.write_text('{"line": "sk-ant-super-secret"}\n', encoding="utf-8")

    # un fichier de dépôt de tâche (pas un journal) contenant la clé ne doit
    # jamais être scanné — hors périmètre (voir docstring).
    (tasks_dir / "python__ex-0" / "kit" / "solution.py").write_text("sk-ant-super-secret\n", encoding="utf-8")

    found = ta.find_leaked_api_keys(workspace, api_key="sk-ant-super-secret")

    assert found == sorted([leaked_home_file, leaked_log])


def test_find_leaked_api_keys_returns_empty_when_nothing_leaked(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    home = workspace / "homes" / "kit"
    ta.ensure_isolated_home(home)
    assert ta.find_leaked_api_keys(workspace, api_key="sk-ant-super-secret") == []


def test_build_report_lists_the_auth_modes_actually_used() -> None:
    records = [
        ta.RunRecord(
            task_id="python/ex-0",
            language="python",
            arm="nu",
            run_index=0,
            success=True,
            total_cost_usd=0.01,
            input_tokens=1,
            output_tokens=1,
            num_turns=1,
            wall_seconds=1.0,
            terminated_reason="completed",
            auth_mode="api-key",
        )
    ]
    report = ta.build_report(records, total_tasks=1, expected_cost=None, seed=1)
    assert report["auth_modes"] == ["api-key"]
    markdown = ta.render_report_markdown(report)
    assert "api-key" in markdown


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


def test_verify_grimoire_binary_matches_template_is_replayable_on_the_same_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lot J (#582) : deux appels consécutifs de la garde sur le même
    workspace (ex. un ``--resume`` après un incident) ne doivent jamais se
    marcher dessus.

    Avant correction, le dépôt de vérification vivait à un chemin FIXE
    (``check_dir`` lui-même) : au second appel, ``grimoire init`` retombait
    sur un dépôt déjà initialisé et refusait (« Use --force to overwrite »),
    simulé ici par ``initialized_dirs`` — un ``cwd`` déjà vu redonne un
    ``returncode`` non nul. Rouge avant correction (le second appel lève),
    vert après (chaque appel provisionne son propre dépôt jetable, supprimé
    ensuite)."""
    grimoire_bin = str(tmp_path / "venv" / "bin" / "grimoire")
    expected_template = "[Grimoire Standard] gabarit attendu\n"
    monkeypatch.setattr(ta, "_expected_activation_directive_template", lambda: expected_template)

    initialized_dirs: set[Path] = set()

    def fake_run(cmd: Any, *, cwd: Path | None = None, env: Any = None, timeout: int | None = None) -> Any:
        if cmd[0] == grimoire_bin and cmd[1] == "--version":
            return SimpleNamespace(returncode=0, stdout="grimoire 3.56.0", stderr="")
        if cmd[:2] == ["git", "init"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1] == "init":
            resolved = Path(cwd).resolve()  # type: ignore[arg-type]
            if resolved in initialized_dirs:
                return SimpleNamespace(returncode=1, stdout="", stderr="Use --force to overwrite")
            initialized_dirs.add(resolved)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1:3] == ["host", "sync"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == grimoire_bin and cmd[1:3] == ["standard", "init"]:
            (cwd / ".claude").mkdir(parents=True, exist_ok=True)
            (cwd / ".claude" / "activation-context.md").write_text(expected_template, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"commande inattendue : {cmd}")

    monkeypatch.setattr(ta, "_run", fake_run)
    check_dir = tmp_path / "_grimoire_bin_check"

    ta.verify_grimoire_binary_matches_template(grimoire_bin, check_dir=check_dir)
    # Le second appel — ex. un `--resume` après un incident — ne doit PAS
    # échouer.
    ta.verify_grimoire_binary_matches_template(grimoire_bin, check_dir=check_dir)

    # Chaque dépôt jetable est nettoyé après coup : rien ne s'accumule sous
    # `check_dir` d'un appel à l'autre.
    assert list(check_dir.iterdir()) == []


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


# ── W1-08a, 3e revue : gardes de fin de campagne, effacement signalé, taille ─


def _ok_record(task: Any, arm: str, run_index: int, cost: float = 0.5) -> Any:
    return ta.RunRecord(
        task_id=task.task_id, language=task.language, arm=arm, run_index=run_index, success=True,
        total_cost_usd=cost, input_tokens=1, output_tokens=1, num_turns=1, wall_seconds=1.0,
        terminated_reason="completed", model_id="claude-test",
    )


def _spy_security_guards(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    spies = {"left": 0, "leak": 0}

    def _left(*a: Any, **k: Any) -> list[Path]:
        spies["left"] += 1
        return []

    def _leak(*a: Any, **k: Any) -> list[Path]:
        spies["leak"] += 1
        return []

    monkeypatch.setattr(ta, "find_leftover_credentials", _left)
    monkeypatch.setattr(ta, "find_leaked_api_keys", _leak)
    return spies


def test_main_runs_security_guards_when_contamination_follows_a_real_run(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W1-08a (T2-01) : une contamination au 2e run ne doit pas sauter les deux
    gardes de sécurité de fin de campagne (identifiants oubliés, clé API
    fuitée) : le 1er run a bien tourné avec des identifiants."""
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    attempts: list[int] = []

    def _one(task: Any, arm: str, run_index: int, **kwargs: Any) -> Any:
        attempts.append(run_index)
        if len(attempts) == 1:
            return _ok_record(task, arm, run_index)
        raise ta.ContaminatedTaskRepoError("contaminé")

    def _no_report(*a: Any, **k: Any) -> None:
        raise AssertionError("aucun rapport ne doit être écrit après une contamination")

    spies = _spy_security_guards(monkeypatch)
    monkeypatch.setattr(ta, "_run_one", _one)
    monkeypatch.setattr(ta, "write_report", _no_report)

    rc = ta.main(["--full", "--model", "claude-test", "--workspace", str(workspace)])

    assert rc == 1
    assert len(attempts) == 2  # la campagne s'arrête au premier dépôt contaminé
    assert spies == {"left": 1, "leak": 1}
    lines = (workspace / "state" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # le run réel est enregistré, le contaminé non


def test_main_warns_before_erasing_a_completed_run_dir_without_resume(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W1-08a (T2-02) : sans ``--resume``, le dossier d'un run DÉJÀ enregistré
    dans results.jsonl est effacé puis rejoué ; ce n'est pas le « résidu d'un
    run interrompu » : l'opérateur doit en être prévenu."""
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    _spy_security_guards(monkeypatch)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _ok_record(task, arm, run_index))
    task = _task(synthetic_bench_root)
    run_dir = ta.run_dir_for(workspace, task, "nu", 0)
    run_dir.mkdir(parents=True)
    (run_dir / "SOLUTION_DU_RUN_PRECEDENT.txt").write_text("preuve\n", encoding="utf-8")
    (workspace / "state").mkdir(parents=True)
    (workspace / "state" / "results.jsonl").write_text(
        json.dumps(_ok_record(task, "nu", 0).to_dict()) + "\n", encoding="utf-8"
    )

    ta.main(["--full", "--model", "claude-test", "--workspace", str(workspace)])

    err = capsys.readouterr().err
    assert str(run_dir) in err
    assert "effacé" in err  # message nommant le dossier effacé et la clé déjà enregistrée
    assert "--resume" in err


def test_main_full_tolerates_blank_lines_in_results_without_resume(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W1-08a (T3-02) : ``main`` lit désormais results.jsonl sans ``--resume`` ;
    une ligne vide (comme dans ``_do_report_only``) ne doit pas le faire planter."""
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    _spy_security_guards(monkeypatch)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _ok_record(task, arm, run_index))
    task = _task(synthetic_bench_root)
    (workspace / "state").mkdir(parents=True)
    (workspace / "state" / "results.jsonl").write_text(
        json.dumps(_ok_record(task, "nu", 0).to_dict()) + "\n\n", encoding="utf-8"
    )

    assert ta.main(["--full", "--model", "claude-test", "--workspace", str(workspace)]) == 0


def test_main_full_tolerates_blank_lines_in_results_with_resume(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    _spy_security_guards(monkeypatch)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _ok_record(task, arm, run_index))
    task = _task(synthetic_bench_root)
    (workspace / "state").mkdir(parents=True)
    (workspace / "state" / "results.jsonl").write_text(
        "\n" + json.dumps(_ok_record(task, "nu", 0).to_dict()) + "\n  \n", encoding="utf-8"
    )

    assert ta.main(["--full", "--model", "claude-test", "--resume", "--workspace", str(workspace)]) == 0


def test_main_names_the_truncated_results_line(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Une ligne tronquée n'est pas ignorée en silence : échec net qui nomme la ligne."""
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    monkeypatch.setattr(ta, "_run_one", lambda *a, **k: pytest.fail("aucun run ne doit partir"))
    task = _task(synthetic_bench_root)
    (workspace / "state").mkdir(parents=True)
    good = json.dumps(_ok_record(task, "nu", 0).to_dict())
    (workspace / "state" / "results.jsonl").write_text(good + '\n{"task_id": "pyth', encoding="utf-8")

    assert ta.main(["--full", "--model", "claude-test", "--workspace", str(workspace)]) == 1
    err = capsys.readouterr().err
    assert "results.jsonl" in err
    assert "ligne 2" in err


def test_main_does_not_warn_for_interrupted_run_residue(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un dossier sans ligne dans results.jsonl est un vrai résidu : pas d'alerte."""
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    _spy_security_guards(monkeypatch)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _ok_record(task, arm, run_index))
    task = _task(synthetic_bench_root)
    run_dir = ta.run_dir_for(workspace, task, "nu", 0)
    run_dir.mkdir(parents=True)
    (run_dir / "TASK.md").write_text("résidu\n", encoding="utf-8")

    ta.main(["--full", "--model", "claude-test", "--workspace", str(workspace)])

    assert "effacé" not in capsys.readouterr().err


def test_task_repo_guards_live_in_their_own_module_and_script_does_not_grow() -> None:
    """W1-08a (T2-03) : ``three_arms.py`` dépassait déjà le seuil du ratchet
    (non couvert pour ``scripts/``) ; les gardes vivent dans ``task_repo.py``
    et le script ne grossit pas au-delà de ``origin/main`` (2584 lignes) + le
    câblage de W1-08b (36 lignes : champs de ``RunRecord``, exclusion des taux,
    appel de ``hidden_reads`` avec le dossier de l'exercice canonique) + 12 lignes
    pour signaler les transcriptions non évaluées (W1-08b-T1-03 : import, clé
    ``unevaluated_runs`` du rapport, section Markdown, alerte stderr) + 6 lignes pour le coût
    connu ou inconnu (B-00 : import, champ ``cost_known`` de ``RunOutcome`` et de ``RunRecord``, câblage) + 4 lignes pour ``--subset`` + 61 lignes pour B-01 (champs de ``RunRecord``, option ``--model`` et son refus, pré-contrôle de ``results.jsonl``, ``selection.json``, câblage du rapport) ; le détecteur et le rendu vivent dans ``hidden_reads.py``, la logique de modèle et de saturation dans ``model_pin.py``."""
    for name in ("ContaminatedTaskRepoError", "assert_task_repo_clean", "reset_run_dir"):
        assert getattr(ta, name).__module__ == "task_repo"
    assert (SCRIPT.parent / "task_repo.py").is_file()
    assert len(SCRIPT.read_text(encoding="utf-8").splitlines()) <= 2584 + 36 + 12 + 6 + 4 + 61


# ── W1-08b (#694) : run invalidé si la transcription lit un test caché ─────


def _hidden_reads_module() -> Any:
    """Import tardif : un module absent ne doit rougir que ces tests."""
    return importlib.import_module("hidden_reads")


def _tool_line(name: str, **tool_input: Any) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": tool_input}]}})


class _Layout:
    """La disposition du banc : ``tasks/<slug>/{hidden-tests,<arm>/run<i>}``."""

    def __init__(self, workspace: Path) -> None:
        self.task_root = workspace / "tasks" / "python__ex"
        self.hidden = self.task_root / "hidden-tests"
        self.run_dir = self.task_root / "kit-gov" / "run1"
        self.previous = self.task_root / "kit-gov" / "run0"
        # la source canonique : le dossier de l'exercice dans le clone polyglot-benchmark
        self.clone = workspace / "polyglot-benchmark"
        self.canonical = self.clone / "python" / "exercises" / "practice" / "ex"
        for directory in (self.hidden, self.run_dir, self.previous, self.canonical / ".meta"):
            directory.mkdir(parents=True)
        (self.canonical / "ex_test.py").write_text("def test_x(): ...\n", encoding="utf-8")
        (self.canonical / ".meta" / "example.py").write_text("def solve(): ...\n", encoding="utf-8")
        (self.hidden / "ex_test.py").write_text("def test_x(): ...\n", encoding="utf-8")
        (self.previous / "ex_test.py").write_text("def test_x(): ...\n", encoding="utf-8")  # copié par run_hidden_tests
        (self.run_dir / "ex.py").write_text("pass\n", encoding="utf-8")

    def find(self, *lines: str) -> list[Any]:
        return _hidden_reads_module().find_hidden_test_reads(
            lines,
            test_files=("ex_test.py",),
            run_dir=self.run_dir,
            hidden_dir=self.hidden,
            canonical_dirs=(self.canonical,),
            other_run_dirs=(self.previous,),
        )


@pytest.mark.parametrize(
    ("kind", "line"),
    [
        ("direct", _tool_line("Read", file_path="{hidden}/ex_test.py")),
        ("direct", _tool_line("Bash", command="cat ../../hidden-tests/ex_test.py")),
        ("direct", _tool_line("Bash", command="cd ../.. && cat hidden-tests/ex_test.py")),
        ("direct", _tool_line("Bash", command="cat ../run0/ex_test.py")),
        ("direct", _tool_line("Bash", command="python -c \"print(open('../../hidden-tests/ex_test.py').read())\"")),
        ("direct", _tool_line("Bash", command="cat {hidden}/ex_test.py | head -50")),
        ("glob", _tool_line("Glob", pattern="**/*_test.py", path="{task_root}")),
        ("glob", _tool_line("Bash", command="cat ../../hidden-tests/*.py")),
        ("glob", _tool_line("Bash", command="cat ../../hidden*/ex_*")),
        ("glob", _tool_line("Bash", command="cat ../run?/ex_test.py")),
        ("directory", _tool_line("Bash", command="ls ../../hidden-tests")),
        ("directory", _tool_line("Grep", pattern="def test", path="{task_root}")),
        ("directory", _tool_line("Bash", command="grep -rn 'def test' ../..")),
        ("directory", _tool_line("Bash", command="find ../.. -name '*.py'")),
        ("directory", _tool_line("Bash", command="find / -name 'ex_test.py'")),
        ("direct", _tool_line("Bash", command="python3 - <<'EOF'\nprint(open('../../hidden-tests/ex_test.py').read())\nEOF")),
        ("glob", _tool_line("Bash", command="cat ../../hidden-tests/ex_[t]est.py")),
        # W1-08b-S1-01 : le test canonique du clone polyglot-benchmark est la même fuite
        ("direct", _tool_line("Read", file_path="{canonical}/ex_test.py")),
        ("direct", _tool_line("Bash", command="cat ../../../../polyglot-benchmark/python/exercises/practice/ex/ex_test.py")),
        ("direct", _tool_line("Bash", command="cd {canonical} && sed -n 1,20p ex_test.py")),
        ("direct", _tool_line("Bash", command="grep -n test {canonical}/ex_test.py")),
        ("glob", _tool_line("Glob", pattern="*_test.py", path="{canonical}")),
        ("directory", _tool_line("Read", file_path="{canonical}/.meta/example.py")),  # la solution de référence
        ("directory", _tool_line("Bash", command="ls {canonical}")),
        ("directory", _tool_line("Bash", command="find {clone} -name '*_test.py'")),
        ("directory", _tool_line("Grep", pattern="def test", path="{clone}")),
    ],
)
def test_hidden_test_read_makes_the_transcript_invalid(tmp_path: Path, kind: str, line: str) -> None:
    layout = _Layout(tmp_path)
    line = (
        line.replace("{hidden}", layout.hidden.as_posix())
        .replace("{task_root}", layout.task_root.as_posix())
        .replace("{canonical}", layout.canonical.as_posix())
        .replace("{clone}", layout.clone.as_posix())
    )
    found = layout.find(line)
    assert [read.kind for read in found] == [kind], line
    assert found[0].line == 1


@pytest.mark.parametrize(
    "line",
    [
        _tool_line("Read", file_path="{run_dir}/ex.py"),
        _tool_line("Bash", command="cat ex.py"),
        _tool_line("Bash", command="python -m pytest ex_test.py"),  # son propre test, écrit dans le dépôt
        _tool_line("Bash", command="ls .. && git status"),
        _tool_line("Bash", command="grep -rn solve ."),
        # le code d'une heredoc n'est pas une racine : `//`, `///`, `/**` et la division
        _tool_line("Bash", command="grep -rn x . && cat > ex.py <<'EOF'\n// note\n/// doc\n/** c */\nx = a / b\nEOF"),
        # `"$SP"/` est un chemin sous une variable, pas la racine, même derrière `rm -rf`
        _tool_line("Bash", command='SP=/tmp/x\nrm -rf "$SP" && cp ex.py "$SP"/'),
        _tool_line("Write", file_path="{run_dir}/ex_test.py", content="# voir ../../hidden-tests/ex_test.py\n"),
        _tool_line("Edit", file_path="{run_dir}/ex.py", old_string="pass", new_string="return 1"),
        _tool_line("Glob", pattern="**/*.py"),
        _tool_line("Grep", pattern="solve"),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "je ne lirai pas hidden-tests/ex_test.py"}]}}),
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "../../hidden-tests/ex_test.py"}]}}),
        "pas du json",
    ],
)
def test_clean_transcript_stays_valid(tmp_path: Path, line: str) -> None:
    layout = _Layout(tmp_path)
    assert layout.find(line.replace("{run_dir}", str(layout.run_dir))) == []


def test_cd_inside_the_repo_persists_between_tool_calls_and_report_names_the_triggering_line(tmp_path: Path) -> None:
    """``cd src`` (dans le dépôt du run) tient d'un appel ``Bash`` au suivant."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    lines = [
        _tool_line("Bash", command="cat ex.py"),
        _tool_line("Bash", command="cd src"),
        _tool_line("Bash", command="cat ../../run0/ex_test.py"),  # src/../../run0 = le run0 frère
    ]
    found = layout.find(*lines)
    assert [(read.line, read.tool, read.kind) for read in found] == [(3, "Bash", "direct")]
    assert "cat ../../run0/ex_test.py" in found[0].excerpt
    assert found[0].to_dict()["line"] == 3


def _bash_result(tool_id: str, text: str) -> str:
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}})


def _bash_call(tool_id: str, command: str) -> str:
    event = json.loads(_tool_line("Bash", command=command))
    event["message"]["content"][0]["id"] = tool_id
    return json.dumps(event)


def test_cd_to_the_hidden_dir_stays_candidate_until_the_result_settles_it(tmp_path: Path) -> None:
    """Sans résultat, ``cd`` hors du dépôt garde les deux répertoires candidats (fail-closed)."""
    layout = _Layout(tmp_path)
    cd, cat = _tool_line("Bash", command="cd ../../hidden-tests"), _tool_line("Bash", command="cat ex_test.py")
    unsettled = layout.find(cd, cat)
    assert [(read.line, read.kind) for read in unsettled] == [(1, "directory"), (2, "direct")]
    reset = f"Shell cwd was reset to {layout.run_dir.as_posix()}"
    settled = layout.find(_bash_call("t1", "cd ../../hidden-tests"), _bash_result("t1", reset), cat)
    assert [(read.line, read.kind) for read in settled] == [(1, "directory")]  # le shell est revenu au dépôt
    kept = layout.find(_bash_call("t1", "cd ../../hidden-tests"), _bash_result("t1", "ok"), cat)
    assert [(read.line, read.kind) for read in kept] == [(1, "directory"), (3, "direct")]  # pas de reset : il y est resté


def test_cd_outside_the_repo_does_not_carry_over_to_the_next_call(tmp_path: Path) -> None:
    """W1-08b-S1-02 : Claude Code ramène le shell au dépôt (« Shell cwd was reset to ... »)."""
    layout = _Layout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})
    cat = _tool_line("Bash", command="cat ../run0/ex_test.py")
    witness = layout.find(init, cat)
    assert [read.kind for read in witness] == ["direct"]
    cd = f"cd {elsewhere.as_posix()} && ls"
    # résultat absent : les deux candidats sont jugés, le dépôt trouve la copie
    assert [(r.line, r.kind) for r in layout.find(init, _tool_line("Bash", command=cd), cat)] == [(3, "direct")]
    # résultat qui confirme la réinitialisation
    reset = _bash_result("t1", f"x\nShell cwd was reset to {layout.run_dir.as_posix()}")
    assert [(r.line, r.kind) for r in layout.find(init, _bash_call("t1", cd), reset, cat)] == [(4, "direct")]
    # résultat sans message : ce n'est pas un signal que le shell soit resté dehors (W1-08b-S2-01)
    assert [(r.line, r.kind) for r in layout.find(init, _bash_call("t1", cd), _bash_result("t1", "ok"), cat)] == [(4, "direct")]
    # dans la MÊME commande, le cwd de `cd` vaut encore pour les segments suivants
    assert layout.find(init, _tool_line("Bash", command=f"cd {elsewhere.as_posix()} && cat ../run0/ex_test.py")) == []


def _bash_error(tool_id: str, text: str) -> str:
    return json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": True}]}}
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows : l'analyseur de transcripts du banc lit des chemins POSIX absolus (/...) : un chemin à lettre de lecteur (C:/...) y est relatif, donc jamais « hors du dépôt »"
    ),
)
def test_failed_command_outside_the_repo_leaves_the_repo_candidate(tmp_path: Path) -> None:
    """W1-08b-S2-01 : un résultat en erreur n'a pas le message de réinitialisation, et le shell est revenu au dépôt."""
    layout = _Layout(tmp_path)
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})
    cd = f"cd {layout.task_root}; false"
    cat = _tool_line("Bash", command="cat ../run0/ex_test.py")
    failed = layout.find(init, _bash_call("t1", cd), _bash_error("t1", "Exit code 1"), cat)
    assert [(r.line, r.kind) for r in failed] == [(4, "direct")]
    # avec le message, le shell est au dépôt : même verdict
    reset = _bash_result("t1", f"Exit code 1\nShell cwd was reset to {layout.run_dir.as_posix()}")
    assert [(r.line, r.kind) for r in layout.find(init, _bash_call("t1", cd), reset, cat)] == [(4, "direct")]
    # le répertoire où la commande a fini reste candidat : la lecture relative qui n'a de sens que là est vue aussi
    inside_task = _tool_line("Bash", command="cat hidden-tests/ex_test.py")
    after = layout.find(init, _bash_call("t1", cd), _bash_error("t1", "Exit code 1"), inside_task)
    assert [(r.line, r.kind) for r in after] == [(4, "direct")]
    # le message, lui, écarte le répertoire de la tâche
    assert layout.find(init, _bash_call("t1", cd), reset, inside_task) == []


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows : l'analyseur de transcripts du banc lit des chemins POSIX absolus (/...) : un chemin à lettre de lecteur (C:/...) y est relatif, donc jamais « hors du dépôt »"
    ),
)
def test_cd_dash_returns_to_the_previous_directory(tmp_path: Path) -> None:
    """W1-08b-S2-01 : ``cd -`` n'était pas modélisé, le shell restait dehors alors qu'il revenait au dépôt."""
    layout = _Layout(tmp_path)
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})
    cat = "cat ../run0/ex_test.py"
    # dans la même commande : retour au répertoire d'avant le cd
    back = _tool_line("Bash", command=f"cd {layout.task_root} && ls; cd - >/dev/null; {cat}")
    assert [(r.line, r.kind) for r in layout.find(init, back)] == [(2, "direct")]
    # d'un appel à l'autre : le précédent est inconnu, les deux issues sont jouées
    leave = _bash_call("t1", f"cd {tmp_path} && ls; cd -")
    ok = _bash_result("t1", "ok")
    assert [(r.line, r.kind) for r in layout.find(init, leave, ok, _tool_line("Bash", command=cat))] == [(4, "direct")]
    # `cd -` sans rien avant : le dépôt reste candidat
    assert [(r.line, r.kind) for r in layout.find(init, _tool_line("Bash", command=f"cd - && {cat}"))] == [(2, "direct")]
    # les options de cd ne changent pas la cible
    assert [r.kind for r in layout.find(init, _tool_line("Bash", command=f"cd -P {layout.task_root} && cat hidden-tests/ex_test.py"))] == ["direct"]



# Chaque commande lit `../run0/ex_test.py` (la copie du test dans le run précédent) ; seul le répertoire
# où elle s'exécute dépend de la portée du `cd`. Depuis le dépôt du run (`kit-gov/run1`) : `direct`.
_SCOPE_READ = "cat ../run0/ex_test.py"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(_SCOPE_READ, id="temoin"),
        pytest.param(f"(cd /opt && ls); {_SCOPE_READ}", id="sous-shell"),
        pytest.param(f"(cd src && ls) && {_SCOPE_READ}", id="sous-shell-et"),
        pytest.param(f"pushd /opt && ls && popd && {_SCOPE_READ}", id="pushd-popd"),
        pytest.param(f"cd /opt | true; {_SCOPE_READ}", id="cd-dans-un-tube"),
        pytest.param(f"echo $(cd /opt && pwd); {_SCOPE_READ}", id="substitution"),
        pytest.param(f"bash -c 'cd /opt && ls'; {_SCOPE_READ}", id="bash-c"),
        pytest.param(f"echo '(' ; (cd src); {_SCOPE_READ}", id="parenthese-entre-guillemets"),
    ],
)
def test_cd_in_a_subshell_pipe_or_after_popd_does_not_move_the_rest_of_the_command(tmp_path: Path, command: str) -> None:
    """W1-08b-S3-01 : un ``cd`` dans ``( ... )``, un élément de tube ou défait par ``popd`` ne déplace pas la suite."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    assert [(r.line, r.kind) for r in layout.find(_tool_line("Bash", command=command))] == [(1, "direct")]


@pytest.mark.parametrize(
    "first",
    [
        pytest.param("(cd src && ls)", id="sous-shell"),
        pytest.param("(cd /opt && ls) | cat", id="sous-shell-dans-un-tube"),
        pytest.param("pushd src && popd", id="pushd-popd"),
        pytest.param("cd src | true", id="cd-dans-un-tube"),
    ],
)
def test_scoped_cd_does_not_carry_over_to_the_next_call(tmp_path: Path, first: str) -> None:
    """W1-08b-S3-01 : le répertoire final d'un appel ne garde pas un ``cd`` dont la portée est close."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})
    found = layout.find(init, _bash_call("t1", first), _bash_result("t1", "ok"), _tool_line("Bash", command=_SCOPE_READ))
    assert [(r.line, r.kind) for r in found] == [(4, "direct")]


def test_cd_that_leaves_the_scope_still_moves_the_rest_of_the_command(tmp_path: Path) -> None:
    """Contre-épreuve : ce qui n'est PAS dans un sous-shell déplace bien la suite (``{ ...; }`` et ``cd`` d'un tube terminal)."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    deep = "cat ../../run0/ex_test.py"  # src/../../run0 : le run0 frère
    for command in (f"cd src && {deep}", f"{{ cd src; }}; {deep}", f"(cd /opt); cd src && {deep}"):
        assert [r.kind for r in layout.find(_tool_line("Bash", command=command))] == ["direct"], command
    # un cd dans un sous-shell ne déplace pas la suite : `../../run0` y sort de l'arborescence du banc
    assert layout.find(_tool_line("Bash", command=f"(cd src); {deep}")) == []


def test_cd_in_the_last_element_of_a_pipe_keeps_both_directories_candidate(tmp_path: Path) -> None:
    """zsh (et ``shopt -s lastpipe``) exécutent le dernier élément d'un tube dans le shell courant : fail-closed."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    deep = "cat ../../run0/ex_test.py"
    found = layout.find(_tool_line("Bash", command=f"echo x | cd src; {_SCOPE_READ}; {deep}"))
    assert [(r.line, r.kind) for r in found] == [(1, "direct")]  # `../run0` depuis le dépôt, `../../run0` depuis src
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})
    nxt = layout.find(init, _bash_call("t1", "echo x | cd src"), _bash_result("t1", "ok"), _tool_line("Bash", command=deep))
    assert [(r.line, r.kind) for r in nxt] == [(4, "direct")]
    nxt = layout.find(init, _bash_call("t1", "echo x | cd src"), _bash_result("t1", "ok"), _tool_line("Bash", command=_SCOPE_READ))
    assert [(r.line, r.kind) for r in nxt] == [(4, "direct")]


def test_unbalanced_parenthesis_keeps_the_entry_directory_candidate(tmp_path: Path) -> None:
    """Une parenthèse jamais refermée ou un guillemet mal lu ne doit pas masquer le dépôt : fail-closed."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})
    unclosed = _bash_call("t1", "(cd src; ls")
    found = layout.find(init, unclosed, _bash_result("t1", "ok"), _tool_line("Bash", command=_SCOPE_READ))
    assert [(r.line, r.kind) for r in found] == [(4, "direct")]
    # un guillemet jamais fermé cache les parenthèses : on les lit quand même, et le dépôt reste candidat
    stray = layout.find(_tool_line("Bash", command=f"echo it's; (cd src); {_SCOPE_READ}"))
    assert [r.kind for r in stray] == ["direct"]


def test_popd_with_an_unknown_stack_plays_both_outcomes(tmp_path: Path) -> None:
    """``popd`` sans ``pushd`` dans la commande : la pile vient d'un autre appel, comme ``cd -`` on joue rester ou revenir au dépôt."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    init = json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})
    cd = _tool_line("Bash", command="cd src")
    popd = _tool_line("Bash", command=f"popd; {_SCOPE_READ}")
    assert [(r.line, r.kind) for r in layout.find(init, cd, popd)] == [(3, "direct")]
    # les deux issues restent candidates : la lecture qui n'a de sens que depuis `src` est vue aussi
    deep = _tool_line("Bash", command="popd; cat ../../run0/ex_test.py")
    assert [(r.line, r.kind) for r in layout.find(init, cd, deep)] == [(3, "direct")]


def test_reset_message_of_another_call_does_not_settle_the_shell(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cat = _tool_line("Bash", command="cat ../run0/ex_test.py")
    found = layout.find(_bash_call("t1", f"cd {elsewhere.as_posix()}"), _bash_result("autre", "ok"), cat)
    assert [(read.line, read.kind) for read in found] == [(3, "direct")]


# ── W1-08b-T1-01 : la fin de la ligne d'ouverture d'un heredoc est une commande ─────


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        pytest.param("cat > s.py <<'EOF' && cat ../../hidden-tests/ex_test.py\nprint(1)\nEOF", "direct", id="hidden-tests"),
        pytest.param("cat > s.py <<EOF; cat ../run0/ex_test.py\nprint(1)\nEOF", "direct", id="run0-sans-guillemets"),
        pytest.param("cat > s.py <<'EOF' && cd ../.. && python -m pytest hidden-tests/ex_test.py\nx\nEOF", "direct", id="cd-puis-pytest"),
        pytest.param("cat > s.py <<'EOF'; cp ../run0/ex_test.py .\nprint(1)\nEOF", "direct", id="cp"),
        pytest.param("cat <<-EOF | tee ../run0/ex_test.py\n\tx\n\tEOF", "direct", id="heredoc-tabule-puis-tube"),
        pytest.param("python3 - <<'EOF' | grep -r x ../..\nprint(1)\nEOF", "directory", id="parcours-recursif-en-fin-de-ligne"),
        pytest.param("cat <<< x && cat ../run0/ex_test.py\nls", "direct", id="here-string"),
        pytest.param("cat <<< x\ncat ../run0/ex_test.py", "direct", id="here-string-ligne-suivante"),
        pytest.param("cat > s.py <<'EOF'\nprint(1)\nEOF\ncat ../run0/ex_test.py", "direct", id="temoin-apres-le-delimiteur"),
    ],
)
def test_heredoc_opening_line_tail_and_here_string_are_still_commands(tmp_path: Path, command: str, kind: str) -> None:
    """W1-08b-T1-01 : seuls le corps et le délimiteur d'un heredoc sont du texte ; ``<<<`` n'en est pas un."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command=command))
    assert [(r.line, r.kind) for r in found] == [(1, kind)]


def test_heredoc_without_a_read_in_its_opening_line_stays_clean(tmp_path: Path) -> None:
    """Contre-épreuve : la fin de ligne n'est pas lue comme un accès quand elle n'en fait pas (corps et division restent du texte)."""
    layout = _Layout(tmp_path)
    for command in (
        "cat > ex.py <<'EOF' && python -m pytest\nx = a / b\n// note\nEOF",
        "cat <<< x && ls\nls",
        "echo $((1 << 3))\nls",
    ):
        assert layout.find(_tool_line("Bash", command=command)) == [], command


# ── W1-08b-T1-02 : un cd peut échouer, et le shell reste où il était ─────


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("cd /nonexistent/x 2>/dev/null || cd ../run0 && cat ex_test.py", id="ou-puis-cd-et-cat"),
        pytest.param("cd /nonexistent/x; cat ../run0/ex_test.py", id="point-virgule"),
        pytest.param("cd /nonexistent/x || true; cat ../run0/ex_test.py", id="ou-true"),
        pytest.param("cd /nonexistent/x\ncat ../run0/ex_test.py", id="saut-de-ligne"),
        pytest.param("cd /nonexistent/x && ls; cat ../run0/ex_test.py", id="et-puis-point-virgule"),
        pytest.param("cd /nonexistent/x 2>/dev/null || cd /tmp; cat ../run0/ex_test.py", id="idiome-du-corpus"),
        pytest.param("cd /opt || cat ../run0/ex_test.py", id="cote-droit-du-ou"),
        pytest.param("cd /opt && ls || cat ../run0/ex_test.py", id="cote-droit-apres-une-chaine"),
        pytest.param("pushd /nonexistent/x; cat ../run0/ex_test.py", id="pushd"),
        pytest.param("false || cat ../run0/ex_test.py", id="temoin-sans-cd"),
    ],
)
def test_a_failed_cd_leaves_the_shell_where_it_was(tmp_path: Path, command: str) -> None:
    """W1-08b-T1-02 : ``;``, saut de ligne et ``||`` jugent la suite aussi depuis la position d'avant le ``cd``."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command=command))
    assert [(r.line, r.kind) for r in found] == [(1, "direct")]


def test_cd_followed_by_and_still_moves_only_on_success(tmp_path: Path) -> None:
    """Contre-épreuve : ``cd X && ...`` n'exécute la suite que si le ``cd`` a réussi ; le groupe garde le shell en place."""
    layout = _Layout(tmp_path)
    (layout.run_dir / "src").mkdir()
    deep = "cat ../../run0/ex_test.py"  # src/../../run0 = le run0 frère
    assert layout.find(_tool_line("Bash", command="cd /opt && cat ../run0/ex_test.py")) == []
    assert [r.kind for r in layout.find(_tool_line("Bash", command=f"cd src && {deep}"))] == ["direct"]
    assert [r.kind for r in layout.find(_tool_line("Bash", command=f"(cd /nonexistent || true); {_SCOPE_READ}"))] == ["direct"]
    assert layout.find(_tool_line("Bash", command=f"(cd src); {deep}")) == []


def test_symlinked_run_dir_is_still_the_agents_own_repo(tmp_path: Path) -> None:
    """La transcription peut donner le chemin réel (lien symbolique résolu)."""
    layout = _Layout(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(layout.task_root)
    own = _tool_line("Read", file_path=f"{alias}/kit-gov/run1/ex.py")
    hidden = _tool_line("Read", file_path=f"{alias}/hidden-tests/ex_test.py")
    assert layout.find(own) == []
    assert [read.kind for read in layout.find(hidden)] == ["direct"]


def _invalid_record(task: Any, arm: str = "kit-gov", run_index: int = 2, *, success: bool = True) -> Any:
    reads = [{"line": 7, "tool": "Read", "kind": "direct", "reference": "hidden-tests/x_test.py", "excerpt": "{...}"}]
    return ta.RunRecord(
        task_id=task.task_id, language=task.language, arm=arm, run_index=run_index, success=success,
        total_cost_usd=0.4, input_tokens=1, output_tokens=1, num_turns=1, wall_seconds=1.0,
        terminated_reason="completed", transcript_check="invalid", hidden_test_reads=reads, model_id="claude-test",
    )  # fmt: skip


def test_invalid_run_is_excluded_from_every_rate_but_not_from_the_cost(synthetic_bench_root: Path) -> None:
    task = _task(synthetic_bench_root)
    records = [_ok_record(task, "kit-gov", 0), _ok_record(task, "kit-gov", 1), _invalid_record(task, success=True)]
    records[1].success = False
    assert ta.aggregate_success_by_arm(records) == {"kit-gov": [True, False]}

    report = ta.build_report(records, total_tasks=20, expected_cost=None, seed=0)
    arm = report["per_arm"]["kit-gov"]
    assert arm["n_runs"] == 2
    assert arm["n_invalid"] == 1
    assert arm["success_rate"] == pytest.approx(0.5)
    assert arm["total_cost_usd"] == pytest.approx(0.5 + 0.5 + 0.4)  # l'argent dépensé reste compté
    assert report["by_task"][task.task_id]["kit-gov"]["successes"] == 1
    assert report["by_task"][task.task_id]["kit-gov"]["runs"] == 2


def test_report_lists_invalid_runs_with_the_triggering_line(synthetic_bench_root: Path) -> None:
    task = _task(synthetic_bench_root)
    report = ta.build_report([_ok_record(task, "nu", 0), _invalid_record(task)], total_tasks=20, expected_cost=None, seed=0)
    assert [(r["task_id"], r["arm"], r["run_index"]) for r in report["invalid_runs"]] == [(task.task_id, "kit-gov", 2)]
    assert report["invalid_runs"][0]["reads"][0]["line"] == 7
    md = ta.render_report_markdown(report)
    assert "Runs invalidés" in md
    assert "hidden-tests/x_test.py" in md
    assert "ligne 7" in md


def test_report_without_invalid_run_has_no_invalid_section(synthetic_bench_root: Path) -> None:
    task = _task(synthetic_bench_root)
    report = ta.build_report([_ok_record(task, "nu", 0)], total_tasks=20, expected_cost=None, seed=0)
    assert report["invalid_runs"] == []
    assert "Runs invalidés" not in ta.render_report_markdown(report)


def test_rows_written_before_w1_08b_still_load(synthetic_bench_root: Path) -> None:
    task = _task(synthetic_bench_root)
    row = _ok_record(task, "nu", 0).to_dict()
    for name in ("transcript_check", "hidden_test_reads"):
        row.pop(name)
    record = ta.RunRecord(**row)
    assert record.transcript_check is None  # jamais reconstruit : on ignore ce que cette ligne a lu
    assert record.hidden_test_reads is None
    assert ta.aggregate_success_by_arm([record]) == {"nu": [True]}


def _write_transcript(run_dir: Path, *lines: str) -> None:
    (run_dir.parent / f"{run_dir.name}.stream.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_one_with_transcript(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: list[str] | None
) -> Any:
    task = _task(synthetic_bench_root)
    workspace = tmp_path / "ws"
    homes = _patch_run_one(monkeypatch, tmp_path, [])
    run_dir = ta.run_dir_for(workspace, task, "nu", 1)
    # un run précédent de la même tâche, tests copiés (comme ``run_hidden_tests``)
    previous = ta.run_dir_for(workspace, task, "nu", 0)
    previous.mkdir(parents=True)
    (previous / "python_ex_0_test.py").write_text("def test_solve(): ...\n", encoding="utf-8")

    def _claude(task_dir: Path, *a: Any, **k: Any) -> Any:
        if lines is not None:
            _write_transcript(task_dir, *lines)
        return ta.RunOutcome()

    monkeypatch.setattr(ta, "run_claude_headless", _claude)
    record = ta._run_one(
        task, "nu", 1, workspace=workspace, ecc_repo=workspace / "ecc", homes=homes, go_bin=None,
        run_timeout_s=5, grimoire_bin="grimoire", auth_mode="api-key",
    )  # fmt: skip
    assert run_dir.exists()
    return record


def test_run_one_marks_the_run_invalid_when_the_transcript_reads_a_hidden_test(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [_tool_line("Read", file_path="TASK.md"), _tool_line("Bash", command="cat ../../hidden-tests/python_ex_0_test.py")]
    record = _run_one_with_transcript(synthetic_bench_root, tmp_path, monkeypatch, lines)
    assert record.transcript_check == "invalid"
    assert record.hidden_test_reads[0]["line"] == 2
    assert record.hidden_test_reads[0]["kind"] == "direct"
    assert record.success is True  # le résultat brut est conservé ; c'est le rapport qui l'exclut
    json.dumps(record.to_dict())  # persistable dans results.jsonl


def test_run_one_marks_the_run_invalid_when_the_transcript_reads_the_canonical_test(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W1-08b-S1-01 : ``task.exercise_dir`` (clone polyglot-benchmark) est passé au détecteur."""
    task = _task(synthetic_bench_root)
    canonical_test = task.exercise_dir / task.test_files[0]
    assert canonical_test.is_file()
    lines = [_tool_line("Read", file_path=str(canonical_test))]
    record = _run_one_with_transcript(synthetic_bench_root, tmp_path, monkeypatch, lines)
    assert record.transcript_check == "invalid"
    assert record.hidden_test_reads[0]["kind"] == "direct"
    assert record.hidden_test_reads[0]["line"] == 1


def test_run_one_marks_a_clean_transcript_clean(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [_tool_line("Read", file_path="TASK.md"), _tool_line("Bash", command="cat python_ex_0.py")]
    record = _run_one_with_transcript(synthetic_bench_root, tmp_path, monkeypatch, lines)
    assert record.transcript_check == "clean"
    assert record.hidden_test_reads == []


def test_run_one_without_transcript_is_unevaluated_not_clean(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pas de transcription lisible : on ne prétend pas que le run est propre."""
    record = _run_one_with_transcript(synthetic_bench_root, tmp_path, monkeypatch, None)
    assert record.transcript_check == "unevaluated"
    assert record.hidden_test_reads is None


def test_main_announces_an_invalid_run_on_stderr(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    _spy_security_guards(monkeypatch)
    monkeypatch.setattr(ta, "_run_one", lambda task, arm, run_index, **kw: _invalid_record(task, arm, run_index))

    ta.main(["--pilot", "--model", "claude-test", "--workspace", str(workspace)])

    err = capsys.readouterr().err
    assert "[INVALIDE]" in err
    assert "ligne 7" in err


def test_empty_or_missing_transcript_is_unevaluated(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    check = _hidden_reads_module().check_transcript
    kwargs: dict[str, Any] = {
        "test_files": ("ex_test.py",),
        "run_dir": layout.run_dir,
        "hidden_dir": layout.hidden,
        "canonical_dirs": (layout.canonical,),
    }
    assert check(tmp_path / "absent.jsonl", **kwargs) == ("unevaluated", None)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    assert check(empty, **kwargs) == ("unevaluated", None)


# ── W1-08b-T1-03 : une transcription qui n'a pas tout été lue n'est jamais « propre » ─────


def _check_log(layout: _Layout, tmp_path: Path, *lines: str) -> tuple[str, list[dict[str, Any]] | None]:
    log = tmp_path / "t.jsonl"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _hidden_reads_module().check_transcript(
        log, test_files=("ex_test.py",), run_dir=layout.run_dir, hidden_dir=layout.hidden,
        canonical_dirs=(layout.canonical,),
    )  # fmt: skip




def _init_line(layout: _Layout) -> str:
    return json.dumps({"type": "system", "subtype": "init", "cwd": str(layout.run_dir)})


_BANNER = 'Ignoring 4 permissions.allow entries from .claude/settings.json: this workspace has not been trusted.'


def test_a_transcript_without_any_decoded_event_is_unevaluated(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    assert _check_log(layout, tmp_path, "Error: unknown option '--output-format'") == ("unevaluated", None)
    # des événements de bruit (hooks) sans `system/init` ni `assistant` : rien n'a été vu de l'agent
    hook = json.dumps({"type": "system", "subtype": "hook_started"})
    assert _check_log(layout, tmp_path, _BANNER, hook) == ("unevaluated", None)


def test_an_event_cut_by_a_stderr_line_makes_the_transcript_unevaluated(tmp_path: Path) -> None:
    """``Popen(stderr=STDOUT)`` : une ligne de stderr peut couper un ``tool_use`` ; ses morceaux ne se décodent plus."""
    layout = _Layout(tmp_path)
    init = _init_line(layout)
    read = _tool_line("Bash", command="cat ../run0/ex_test.py")
    assert _check_log(layout, tmp_path, init, read)[0] == "invalid"  # la même ligne intacte
    cut = [read[:50] + "warning: slow disk", read[50:]]
    assert _check_log(layout, tmp_path, init, *cut) == ("unevaluated", None)
    # un seul morceau suffit, et un événement d'ouverture coupé aussi
    assert _check_log(layout, tmp_path, init, read[:50] + "warning: x") == ("unevaluated", None)
    assert _check_log(layout, tmp_path, init[:20] + "warning: x", init[20:]) == ("unevaluated", None)


def test_a_read_found_stays_invalid_even_with_unreadable_lines(tmp_path: Path) -> None:
    layout = _Layout(tmp_path)
    init = _init_line(layout)
    read = _tool_line("Bash", command="cat ../run0/ex_test.py")
    verdict, reads = _check_log(layout, tmp_path, init, read, '{"type":"assistant","mess')
    assert verdict == "invalid"
    assert reads is not None
    assert reads[0]["line"] == 2


def test_the_stderr_banner_alone_does_not_make_a_readable_transcript_unevaluated(tmp_path: Path) -> None:
    """Contre-épreuve : 389 des 449 transcriptions archivées commencent par une bannière de stderr sans accolade."""
    layout = _Layout(tmp_path)
    init = _init_line(layout)
    clean = _tool_line("Bash", command="cat ex.py")
    assert _check_log(layout, tmp_path, _BANNER, init, clean, "", "warning: retrying") == ("clean", [])


def test_run_one_records_an_unreadable_transcript_as_unevaluated(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _run_one_with_transcript(synthetic_bench_root, tmp_path, monkeypatch, ["Error: unknown option '--foo'"])
    assert record.transcript_check == "unevaluated"
    assert record.hidden_test_reads is None
    assert not record.invalid


def test_report_names_the_unevaluated_runs_and_keeps_them_in_the_rates(synthetic_bench_root: Path) -> None:
    task = _task(synthetic_bench_root)
    unknown = _ok_record(task, "kit-gov", 1)
    unknown.transcript_check = "unevaluated"
    records = [_ok_record(task, "kit-gov", 0), unknown, _ok_record(task, "nu", 0)]
    report = ta.build_report(records, total_tasks=20, expected_cost=None, seed=0)
    assert report["per_arm"]["kit-gov"]["n_runs"] == 2  # le run n'est pas exclu des taux
    assert report["per_arm"]["kit-gov"]["n_transcript_unevaluated"] == 1
    assert [(r["task_id"], r["arm"], r["run_index"]) for r in report["unevaluated_runs"]] == [(task.task_id, "kit-gov", 1)]
    md = ta.render_report_markdown(report)
    assert "Transcriptions non évaluées" in md
    assert "kit-gov 1" in md
    assert "PAS exclus des taux" in md
    clean = ta.build_report(records[:1], total_tasks=20, expected_cost=None, seed=0)
    assert clean["unevaluated_runs"] == []
    assert "Transcriptions non évaluées" not in ta.render_report_markdown(clean)


def test_main_announces_an_unevaluated_run_on_stderr(
    synthetic_bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    _patch_main_environment(monkeypatch, synthetic_bench_root, tmp_path)
    monkeypatch.setattr(ta, "write_report", lambda *a, **k: None)
    _spy_security_guards(monkeypatch)

    def _unevaluated(task: Any, arm: str, run_index: int, **kw: Any) -> Any:
        record = _ok_record(task, arm, run_index)
        record.transcript_check = "unevaluated"
        return record

    monkeypatch.setattr(ta, "_run_one", _unevaluated)

    ta.main(["--pilot", "--model", "claude-test", "--workspace", str(workspace)])

    err = capsys.readouterr().err
    assert "[NON ÉVALUÉ]" in err
    assert "[INVALIDE]" not in err


def test_check_transcript_sees_the_copied_tests_of_sibling_runs(tmp_path: Path) -> None:
    """Les ``run<i>`` frères sont découverts sur le disque (tests copiés par ``run_hidden_tests``)."""
    layout = _Layout(tmp_path)
    log = tmp_path / "t.jsonl"
    log.write_text(_tool_line("Bash", command="cat ../run0/ex_test.py") + "\n", encoding="utf-8")
    verdict, reads = _hidden_reads_module().check_transcript(
        log, test_files=("ex_test.py",), run_dir=layout.run_dir, hidden_dir=layout.hidden,
        canonical_dirs=(layout.canonical,),
    )  # fmt: skip
    assert verdict == "invalid"
    assert reads is not None
    assert reads[0]["kind"] == "direct"


# ── W1-08b-T2-01 : le succès de `a || b` passe aussi par `a` quand `b` est sauté ─────


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("cd .. || cd /tmp && cat run0/ex_test.py", id="a-reussit-b-saute"),
        pytest.param("cd .. 2>/dev/null || cd /opt && cat run0/ex_test.py", id="redirection-stderr"),
        pytest.param("cd .. && ls || cd /opt && cat run0/ex_test.py", id="chaine-et-puis-ou"),
        pytest.param("cd .. || cd /tmp || cd /opt && cat run0/ex_test.py", id="deux-ou"),
        pytest.param("cd /nonexistent || cd .. && cat run0/ex_test.py", id="b-reussit"),
        pytest.param("cd .. || echo x | cat && cat run0/ex_test.py", id="ou-puis-tube"),
        pytest.param("false || cd .. && cat run0/ex_test.py", id="temoin-b-seul"),
        # W1-08b-T3-01 : un saut de ligne (ou barre inverse + saut de ligne) après `||` continue la liste
        pytest.param("cd .. ||\ncd /tmp && cat run0/ex_test.py", id="saut-de-ligne-apres-ou"),
        pytest.param("cd .. || \\\ncd /tmp && cat run0/ex_test.py", id="barre-inverse-apres-ou"),
        pytest.param("cd .. 2>/dev/null ||\n  cd /opt && cat run0/ex_test.py", id="redirection-et-indentation"),
        pytest.param("cd .. ||\n\n  cd /tmp &&\n  cat run0/ex_test.py", id="lignes-vides-et-et-final"),
    ],
)
def test_success_of_an_or_list_includes_the_left_side_when_the_right_side_is_skipped(tmp_path: Path, command: str) -> None:
    """W1-08b-T2-01 : après ``a || b``, le shell est là où ``a`` l'a laissé si ``a`` réussit (``b`` n'est pas joué)."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command=command))
    assert [(r.line, r.kind, r.reference) for r in found] == [(1, "direct", "run0/ex_test.py")]


def test_or_list_followed_by_and_still_needs_a_success_of_one_side(tmp_path: Path) -> None:
    """Contre-épreuve : si aucune des deux alternatives ne mène à ``run0``, la lecture reste propre."""
    layout = _Layout(tmp_path)
    for command in (
        "cd /opt || cd /tmp && cat run0/ex_test.py",
        "cd /opt && ls || cd /tmp && cat run0/ex_test.py",
    ):
        assert layout.find(_tool_line("Bash", command=command)) == [], command


# ── W1-08b-T2-02 : `<<` n'ouvre un heredoc que s'il en est un ─────


@pytest.mark.parametrize(
    ("command", "kind", "reference"),
    [
        pytest.param("echo $((1<<3))\ncd ..\ncat run0/ex_test.py", "direct", "run0/ex_test.py", id="decalage-arithmetique"),
        pytest.param("echo $((1<<3))\nfind .. -name '*_test.py'", "directory", "..", id="decalage-puis-find"),
        pytest.param("python3 -c 'print(1 << 2)'\nfind .. -name '*_test.py'", "directory", "..", id="python-c-entre-guillemets"),
        pytest.param("grep -n '<<EOF' notes.md\ncd ..\ncat run0/ex_test.py", "direct", "run0/ex_test.py", id="motif-entre-guillemets"),
        pytest.param('grep -n "<<EOF" notes.md\ncd ..\ncat run0/ex_test.py', "direct", "run0/ex_test.py", id="motif-entre-guillemets-doubles"),
        pytest.param("(( x = 1 << 3 ))\ncd ..\ncat run0/ex_test.py", "direct", "run0/ex_test.py", id="double-parenthese"),
        pytest.param("cat > f <<END-OF\nbody\nEND-OF\ncd ..\ncat run0/ex_test.py", "direct", "run0/ex_test.py", id="delimiteur-avec-tiret"),
        pytest.param("cat > f <<'END.OF'\nbody\nEND.OF\ncd ..\ncat run0/ex_test.py", "direct", "run0/ex_test.py", id="delimiteur-avec-point-entre-guillemets"),
        pytest.param("cat <<EOF\nline\ncd ..\ncat run0/ex_test.py", "direct", "run0/ex_test.py", id="sans-delimiteur-fermant"),
    ],
)
def test_a_shift_a_quoted_marker_or_an_unclosed_heredoc_does_not_swallow_the_following_lines(
    tmp_path: Path, command: str, kind: str, reference: str
) -> None:
    """W1-08b-T2-02 : les lignes qui suivent un faux heredoc (ou un heredoc au délimiteur élargi) restent des commandes."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command=command))
    assert [(r.line, r.kind, r.reference) for r in found] == [(1, kind, reference)]


def test_a_real_heredoc_body_is_still_text_with_a_wide_delimiter(tmp_path: Path) -> None:
    """Contre-épreuve : un heredoc fermé, délimiteur à tiret, point ou barre inverse compris, garde son corps hors de la lecture."""
    layout = _Layout(tmp_path)
    for command in (
        "grep -rn x . && cat > ex.py <<'END-OF'\n// note\nx = a / b\nEND-OF",
        'cat > ex.py <<"E.O.F"\n// note\nE.O.F\nls',
        "cat > ex.py <<\\EOF\nx = a / b\nEOF\nls",
    ):
        assert layout.find(_tool_line("Bash", command=command)) == [], command


# ── W1-08b-T3-01 : un opérateur de liste en fin de ligne continue la commande ─────


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("cd .. &&\ncat run0/ex_test.py", id="et-saut-de-ligne"),
        pytest.param("cd .. &&\\\n  cat run0/ex_test.py", id="et-barre-inverse"),
        pytest.param("cd .. &&\n\n\t cat run0/ex_test.py", id="et-lignes-vides"),
        pytest.param("c\\\nd .. && cat run0/ex_test.py", id="barre-inverse-dans-un-mot"),
        pytest.param("cd ..\\\n && cat run0/ex_test.py", id="barre-inverse-avant-et"),
    ],
)
def test_a_list_operator_at_the_end_of_a_line_continues_the_command(tmp_path: Path, command: str) -> None:
    """W1-08b-T3-01 : `a &&<saut>b` et `a \\<saut>b` sont une seule ligne logique ; le segment vide n'efface rien."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command=command))
    assert [(r.line, r.kind, r.reference) for r in found] == [(1, "direct", "run0/ex_test.py")]


def test_a_pipe_followed_by_a_line_break_keeps_the_cd_in_its_subshell(tmp_path: Path) -> None:
    """`cd .. |<saut>cat` : le `cd` d'un élément de tube ne déplace pas le suivant, comme sur une seule ligne."""
    layout = _Layout(tmp_path)
    assert layout.find(_tool_line("Bash", command="cd .. |\ncat run0/ex_test.py")) == []


def test_an_operator_followed_by_a_line_break_still_needs_a_success_of_one_side(tmp_path: Path) -> None:
    """Contre-épreuve de T3-01 : aucune des deux alternatives ne mène à `run0`, la lecture reste propre."""
    layout = _Layout(tmp_path)
    for command in (
        "cd /opt ||\ncd /tmp && cat run0/ex_test.py",
        "cd /opt || \\\ncd /tmp &&\n cat run0/ex_test.py",
        "cd /opt &&\ncat run0/ex_test.py",
    ):
        assert layout.find(_tool_line("Bash", command=command)) == [], command


def test_a_line_break_inside_quotes_or_after_a_semicolon_is_not_a_continuation(tmp_path: Path) -> None:
    """Un `;` suivi d'un saut de ligne reste un séparateur : `cd /opt` peut échouer, la suite est jugée aussi depuis le dépôt."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command="cd .. ;\ncd /opt ;\ncat ../run0/ex_test.py"))
    assert [(r.kind, r.reference) for r in found] == [("direct", "../run0/ex_test.py")]


# ── W1-08b-T3-02 : un mot du shell est recomposé avant d'être jugé ─────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows : l'analyseur de transcripts du banc lit des chemins POSIX absolus (/...) : un chemin à lettre de lecteur (C:/...) y est relatif, donc jamais « hors du dépôt »"
    ),
)
@pytest.mark.parametrize(
    ("command", "reference"),
    [
        pytest.param("cat ../{run0,run2}/ex_test.py", "../run0/ex_test.py", id="accolades-run"),
        pytest.param("cat ../run{0,2}/ex_test.py", "../run0/ex_test.py", id="accolades-suffixe"),
        pytest.param("cat ../../{hidden-tests,none}/ex_test.py", "../../hidden-tests/ex_test.py", id="accolades-hidden"),
        pytest.param("cat ../../hidden-test{s,}/ex_test.py", "../../hidden-tests/ex_test.py", id="accolade-vide"),
        pytest.param('cat ../"run0"/ex_test.py', "../run0/ex_test.py", id="guillemets-doubles-au-milieu"),
        pytest.param("cat ../../'hidden-tests'/ex_test.py", "../../hidden-tests/ex_test.py", id="guillemets-simples-au-milieu"),
        pytest.param("cat ..'/run0/ex_test.py'", "../run0/ex_test.py", id="guillemets-colles-au-point-point"),
        pytest.param("cat ../run{0..2}/ex_test.py", "../run0/ex_test.py", id="sequence"),
        pytest.param("cat ../run{1,{0,3}}/ex_test.py", "../run0/ex_test.py", id="accolades-imbriquees"),
        pytest.param("cat ..\\/run0/ex_test.py", "../run0/ex_test.py", id="barre-inverse-dans-un-mot"),
        pytest.param("cat ../run0/ex_{test,x}.py", "../run0/ex_test.py", id="accolades-nom-de-fichier"),
        pytest.param("grep -n x --file=../{run0,none}/ex_test.py", "../run0/ex_test.py", id="option-avec-egal"),
    ],
)
def test_a_shell_word_is_recomposed_before_it_is_judged(tmp_path: Path, command: str, reference: str) -> None:
    """W1-08b-T3-02 : accolades développées et guillemets retirés, comme le shell, avant de résoudre le chemin."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command=command))
    assert [(r.line, r.kind, r.reference) for r in found] == [(1, "direct", reference)]


def test_a_quoted_or_literal_brace_does_not_expand_and_stays_clean(tmp_path: Path) -> None:
    """Contre-épreuve de T3-02 : accolade ou virgule entre guillemets, accolade sans virgule, `${..}` : pas de développement."""
    layout = _Layout(tmp_path)
    for command in (
        "cat '../{run0,run2}/ex_test.py'",
        'cat "../run{0,2}/ex_test.py"',
        "cat ../run{0}/ex_test.py",
        "cat ../run\\{0,2\\}/ex_test.py",
        "cat ../${NAME}/ex_test.py",
        "cat ../{none,other}/ex_test.py",
    ):
        assert layout.find(_tool_line("Bash", command=command)) == [], command


def test_a_brace_expansion_too_large_to_enumerate_is_judged_as_a_glob(tmp_path: Path) -> None:
    """Au-delà du plafond de variantes, chaque groupe d'accolades devient `*` (fail-closed) au lieu d'être ignoré."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command="cat ../{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}/ex_test.py"))
    assert [(r.kind, r.reference) for r in found] == [("glob", "../*/ex_test.py")]
    found = layout.find(_tool_line("Bash", command="cat ../run{0..99999999}/ex_test.py"))
    assert [(r.kind, r.reference) for r in found] == [("glob", "../run*/ex_test.py")]


def test_cd_target_with_quotes_glued_to_a_path_moves_the_shell(tmp_path: Path) -> None:
    """`cd ..'/run0'` mène à run0 : le mot recomposé sert aussi de cible au `cd` (T3-02)."""
    layout = _Layout(tmp_path)
    found = layout.find(_tool_line("Bash", command="cd ..'/run0' && cat ex_test.py"))
    assert [(r.kind, r.reference) for r in found] == [("direct", "ex_test.py")]
