"""Le TRACE reconstruit doit être lisible par agent-bench.

Le benchmark hebdomadaire lisait `_grimoire-output/Grimoire_TRACE.md`, gitignoré
donc absent en CI, et publiait un rapport à zéro entrée présenté comme sain.
`scripts/trace-from-git.py` reconstruit ce fichier depuis l'historique git ; ces
tests verrouillent le contrat de format entre le générateur et le parseur.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "trace-from-git.py"
BENCH = ROOT / "framework" / "tools" / "agent-bench.py"

# Même expression que le parseur d'agent-bench : ## date | agent | contexte
HEADER_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\s*\|\s*([^\|]+)\s*\|\s*(.+)$"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def trace_mod():
    return _load(SCRIPT, "trace_from_git")


def test_headers_match_the_bench_parser(trace_mod):
    entries = [{
        "hash": "abc12345", "date": "2026-08-10 12:00", "agent": "amelia",
        "subject": "feat: quelque chose", "files": ["src/a.py"], "branch": "main",
    }]
    rendered = trace_mod.render(entries)

    headers = [line for line in rendered.splitlines() if line.startswith("## ")]
    assert headers, "aucune entête produite"
    match = HEADER_RE.match(headers[0])
    assert match is not None, f"entête illisible par agent-bench : {headers[0]}"
    # Le 2e champ est l'agent — pas le type d'entrée : c'est ce que lit le bench.
    assert match.group(2).strip() == "amelia"
    assert "[GIT-COMMIT]" in rendered


def test_agent_comes_from_co_author_trailer(trace_mod):
    assert trace_mod.resolve_agent("Someone", "Co-Authored-By: Claude Opus 5 <x@y>") == "claude-opus-5"
    assert trace_mod.resolve_agent("github-actions[bot]", "") == "bot"
    assert trace_mod.resolve_agent("Guilhem Bonnet", "") == "guilhem-bonnet"


def test_bench_parses_the_reconstructed_trace(trace_mod, tmp_path):
    """Bout en bout : ce que le script écrit, le bench doit le compter."""
    entries = [
        {"hash": f"cafe000{i}", "date": "2026-08-10 12:00", "agent": "amelia",
         "subject": f"feat: item {i}", "files": ["src/a.py"], "branch": "main"}
        for i in range(3)
    ]
    trace = tmp_path / "Grimoire_TRACE.md"
    trace.write_text(trace_mod.render(entries), encoding="utf-8")

    bench = _load(BENCH, "agent_bench")
    metrics = bench.parse_trace(trace)

    assert metrics.total_entries == 3
    assert metrics.total_commits == 3
    assert set(metrics.agents) == {"amelia"}


def test_min_entries_guard_refuses_an_empty_trace(trace_mod, monkeypatch, capsys):
    """Le garde-fou qui empêche de publier un rapport vide."""
    monkeypatch.setattr(trace_mod, "collect", lambda since, branch: [])
    monkeypatch.setattr(sys, "argv", ["trace-from-git.py", "--min-entries", "1"])

    assert trace_mod.main() == 1
    assert "minimum 1" in capsys.readouterr().err
