"""Banc LongMemEval — #555 lot 1 : un score publié, déclaré pour ce qu'il mesure."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.memory.longmemeval import publish, run_longmemeval

runner = CliRunner()


def test_sample_run_scores_and_declares_its_limits() -> None:
    report = run_longmemeval()
    assert report["dataset"]["kind"] == "sample"
    assert report["questions_scored"] == 3
    assert report["abstention_excluded"] == 1
    # Le banc subit la politique d'écriture : aucune session refusée sur l'échantillon.
    assert report["sessions_refused_by_write_policy"] == 0
    assert 0.0 < report["score"] <= 1.0
    assert report["comparable_to_published_qa_accuracy"] is False


def test_external_dataset_and_publication(tmp_path: Path) -> None:
    data = [{
        "question_id": "q1", "question_type": "single-session-user", "question": "Which fruit do I grow?",
        "haystack_session_ids": ["a", "b"], "answer_session_ids": ["b"],
        "haystack_sessions": [[{"role": "user", "content": "Weather is nice."}], [{"role": "user", "content": "I grow quinces."}]],
    }]
    path = tmp_path / "lme.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    report = run_longmemeval(path)
    assert report["dataset"]["kind"] == "external" and report["score"] == 1.0
    written = publish(report, tmp_path / "out")
    assert {p.suffix for p in written} == {".json", ".md"} and all(p.exists() for p in written)


def test_cli_publishes_under_out_dir(tmp_path: Path) -> None:
    result = runner.invoke(app, ["memory", "bench", "--longmemeval", "--out-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert list(tmp_path.glob("longmemeval-*.json"))


def test_bench_without_queries_nor_flag_fails() -> None:
    assert runner.invoke(app, ["memory", "bench"]).exit_code == 2
