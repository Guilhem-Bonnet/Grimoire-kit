"""Lot de tâches nommé (B-07, lot M) : chargement, sélection, épinglage du clone. Aucun appel modèle."""

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


tl = _load("task_lot", "task_lot.py")
ta = _load("three_arms_task_lot_test", "three_arms.py")
aa = _load("aidlc_arm_task_lot_test", "aidlc_arm.py")


def _write_exercise(root: Path, language: str, slug: str) -> None:
    module_name = slug.replace("-", "_")
    ex_dir = root / language / "exercises" / "practice" / slug
    (ex_dir / ".meta").mkdir(parents=True)
    (ex_dir / ".docs").mkdir(parents=True)
    (ex_dir / ".meta" / "config.json").write_text(
        json.dumps({"files": {"solution": [f"{module_name}.py"], "test": [f"{module_name}_test.py"]}}),
        encoding="utf-8",
    )
    (ex_dir / ".docs" / "instructions.md").write_text(f"# {slug}\n", encoding="utf-8")
    (ex_dir / f"{module_name}.py").write_text("pass\n", encoding="utf-8")
    (ex_dir / f"{module_name}_test.py").write_text("def test_x():\n    assert False\n", encoding="utf-8")


@pytest.fixture
def bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot-benchmark"
    for language in ("python", "javascript", "go", "rust"):
        for i in range(6):
            _write_exercise(root, language, f"{language}-ex-{i}")
    return root


def _lot(tmp_path: Path, tasks: list[str], **extra: Any) -> Path:
    path = tmp_path / "lot.json"
    path.write_text(json.dumps({"schema": tl.SCHEMA, "tasks": tasks, **extra}), encoding="utf-8")
    return path


# ── Chargement ───────────────────────────────────────────────────────────────


def test_load_keeps_declared_order(tmp_path: Path) -> None:
    lot = tl.load_task_lot(_lot(tmp_path, ["rust/b", "go/a"]))
    assert lot.tasks == ("rust/b", "go/a")
    assert lot.polyglot_commit is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"tasks": ["go/a"]}, "schema"),
        ({"schema": "grimoire.bench.task-lot.v1", "tasks": []}, "non vide"),
        ({"schema": "grimoire.bench.task-lot.v1", "tasks": ["go-a"]}, "langue"),
        ({"schema": "grimoire.bench.task-lot.v1", "tasks": ["../etc/passwd"]}, "langue"),
        ({"schema": "grimoire.bench.task-lot.v1", "tasks": ["go/a", "go/a"]}, "deux fois"),
        ({"schema": "grimoire.bench.task-lot.v1", "tasks": ["go/a"], "polyglot_commit": "7e0611e7"}, "40"),
    ],
)
def test_load_refuses_malformed_lots(tmp_path: Path, payload: dict[str, Any], message: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        tl.load_task_lot(path)


# ── Sélection dans le catalogue ──────────────────────────────────────────────


def test_select_picks_tasks_outside_the_seeded_draw(bench_root: Path, tmp_path: Path) -> None:
    """Le cas que ``--subset`` ne sait pas faire : une tâche hors du tirage graine 551."""
    catalog = ta.discover_catalog(bench_root)
    drawn = {t.task_id for t in ta.sample_tasks(catalog, seed=551)}
    outside = sorted({t.task_id for t in catalog} - drawn)[:2]
    assert outside, "le catalogue synthétique doit déborder du tirage"
    lot = tl.load_task_lot(_lot(tmp_path, outside))
    assert [t.task_id for t in tl.select_tasks(catalog, lot)] == outside


def test_select_refuses_a_task_absent_from_the_catalog(bench_root: Path, tmp_path: Path) -> None:
    catalog = ta.discover_catalog(bench_root)
    lot = tl.load_task_lot(_lot(tmp_path, ["go/go-ex-0", "java/bank-account"]))
    with pytest.raises(SystemExit, match="java/bank-account"):
        tl.select_tasks(catalog, lot)


# ── Épinglage du clone polyglotte ────────────────────────────────────────────


def _git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    env_args = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), *env_args, "commit", "-q", "-m", "init"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_verify_bench_commit_accepts_the_declared_commit_and_refuses_another(tmp_path: Path) -> None:
    head = _git_repo(tmp_path / "clone")
    ok = tl.TaskLot(tasks=("go/a",), polyglot_commit=head)
    assert tl.verify_bench_commit(tmp_path / "clone", ok) == head
    wrong = tl.TaskLot(tasks=("go/a",), polyglot_commit="0" * 40)
    with pytest.raises(SystemExit, match="0000000000"):
        tl.verify_bench_commit(tmp_path / "clone", wrong)


def test_verify_bench_commit_refuses_an_unreadable_clone_when_a_commit_is_declared(tmp_path: Path) -> None:
    (tmp_path / "not-git").mkdir()
    free = tl.TaskLot(tasks=("go/a",), polyglot_commit=None)
    assert tl.verify_bench_commit(tmp_path / "not-git", free) is None
    pinned = tl.TaskLot(tasks=("go/a",), polyglot_commit="a" * 40)
    with pytest.raises(SystemExit, match="illisible"):
        tl.verify_bench_commit(tmp_path / "not-git", pinned)


# ── Branchement dans les deux lanceurs ───────────────────────────────────────


def test_three_arms_dry_run_plays_the_declared_lot(
    bench_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ta, "ensure_polyglot_benchmark", lambda ws: bench_root)
    monkeypatch.setattr(ta, "ensure_ecc_repo", lambda ws: (tmp_path / "ecc-repo", "deadbeef"))
    monkeypatch.setattr(ta, "ensure_go_toolchain", lambda ws: None)
    monkeypatch.setattr(ta, "resolve_grimoire_bin", lambda explicit: "grimoire")
    seen: list[str] = []

    def _fake_dry_run(tasks: Any, **kwargs: Any) -> int:
        seen.extend(t.task_id for t in tasks)
        return 0

    monkeypatch.setattr(ta, "_do_dry_run", _fake_dry_run)
    lot = _lot(tmp_path, ["rust/rust-ex-5", "python/python-ex-0"])
    workspace = tmp_path / "ws"
    assert ta.main(["--dry-run", "--workspace", str(workspace), "--task-lot", str(lot)]) == 0
    assert seen == ["rust/rust-ex-5", "python/python-ex-0"]
    selection = json.loads((workspace / "state" / "selection.json").read_text(encoding="utf-8"))
    assert selection["task_lot"] == str(lot)
    assert [t["task_id"] for t in selection["tasks"]] == seen


def test_aidlc_task_list_plays_the_declared_lot(bench_root: Path, tmp_path: Path) -> None:
    lot = _lot(tmp_path, ["go/go-ex-4", "javascript/javascript-ex-1"])
    tasks = aa._task_list(tmp_path / "ws", bench_root=bench_root, seed=551, subset=None, limit=None, task_lot=lot)
    assert [t.task_id for t in tasks] == ["go/go-ex-4", "javascript/javascript-ex-1"]
