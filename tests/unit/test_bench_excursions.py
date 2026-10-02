"""Classifieur d'excursions du banc (issue #642) : transcriptions synthétiques."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bench" / "excursions.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_excursions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ex = _load_module()
GATE_CMD = "grimoire standard gate check"


def _transcript(commands: list[str]) -> str:
    lines = []
    for i, cmd in enumerate(commands):
        block = {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd}}
        lines.append(json.dumps({"type": "assistant", "message": {"content": [block]}}))
    return "\n".join(lines)


def _write_bench(root: Path, runs: dict[str, list[str]]) -> None:
    (root / "state").mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    for i, (name, commands) in enumerate(runs.items()):
        task = f"python/{name}"
        d = root / "tasks" / f"python__{name}" / "kit-gov"
        d.mkdir(parents=True)
        (d / "run0.stream.jsonl").write_text(_transcript(commands), encoding="utf-8")
        base = {"task_id": task, "run_index": 0, "num_turns": 3}
        rows.append({**base, "arm": "kit-gov", "total_cost_usd": 0.5 + i})
        rows.append({**base, "arm": "nu", "total_cost_usd": 0.4})
    (root / "state" / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_pure_mandate_vs_excursion(tmp_path: Path) -> None:
    _write_bench(
        tmp_path,
        {
            "pure": ["pytest -q", GATE_CMD],
            "excursion": ["pytest -q", GATE_CMD, "cat acceptance-record.md", GATE_CMD],
        },
    )
    rep = ex.classify(tmp_path, "kit-gov", "nu")
    by_task = {r["task"]: r for r in rep["detail"]}
    assert by_task["python/pure"]["pure_mandate"] is True
    assert by_task["python/excursion"]["pure_mandate"] is False
    assert (rep["runs"], rep["excursions"]) == (2, 1)


def test_gate_mention_in_heredoc_is_not_a_call() -> None:
    _, ranks = ex.gate_calls(_transcript([f"cat <<EOF\n{GATE_CMD}\nEOF"]).splitlines())
    assert ranks == []
