"""#1243 — le journal des traces n'est relu qu'à partir de ce qui s'y est ajouté.

Le cockpit relisait les 98 000 traces d'un poste réel à chaque lecture (cinq fois par fiche Piloter à froid),
alors que le journal grandit toutes les quelques secondes. Critère de coût : le nombre de lignes parsées
(``TraceRecord.from_dict``), jamais une durée.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from grimoire.traces import journal_cache
from grimoire.traces.ledger import TraceLedger, TraceOutcome
from grimoire.traces.schemas import TraceRecord

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    journal_cache.clear()


@pytest.fixture
def parsed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Les identifiants des lignes parsées, dans l'ordre : le coût d'une lecture."""
    calls: list[str] = []
    original = TraceRecord.from_dict.__func__  # type: ignore[attr-defined]

    def counting(cls: type[TraceRecord], d: dict, **kw: object) -> TraceRecord:
        calls.append(str(d.get("id")))
        return original(cls, d, **kw)

    monkeypatch.setattr(TraceRecord, "from_dict", classmethod(counting))
    return calls


def _add(ledger: TraceLedger, n: int, *, run: str = "RUN-a") -> None:
    for _ in range(n):
        ledger.record(
            run_id=run,
            workflow_instance_id="WFI-1",
            mission_id="MIS-1",
            task_id="GAO-1",
            recipe_id="recipe.test",
            outcome=TraceOutcome.SUCCESS,
            started_at="2026-01-01T00:00:00+00:00",
        )


def _full(path: Path) -> list[TraceRecord]:
    """La relecture complète de référence : cache vidé."""
    journal_cache.clear()
    return TraceLedger(path)._load_all()


def test_an_append_costs_one_parsed_line(tmp_path: Path, parsed: list[str]) -> None:
    ledger = TraceLedger(tmp_path)
    _add(ledger, 50)
    assert len(ledger._load_all()) == 50
    _add(ledger, 1)
    parsed.clear()
    again = ledger._load_all()
    assert len(again) == 51
    assert len(parsed) == 1, "une trace ajoutée ne doit coûter qu'une ligne parsée, pas tout le journal"


def test_repeated_reads_parse_nothing(tmp_path: Path, parsed: list[str]) -> None:
    ledger = TraceLedger(tmp_path)
    _add(ledger, 20)
    ledger._load_all()
    parsed.clear()
    for _ in range(4):
        ledger._load_all()
    assert parsed == []


def test_incremental_result_equals_a_full_reread(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _add(ledger, 5)
    ledger._load_all()
    _add(ledger, 3, run="RUN-b")
    incremental = ledger._load_all()
    assert incremental == _full(tmp_path)
    assert len(incremental) == 8


def test_returned_list_is_not_the_cached_one(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _add(ledger, 3)
    first = ledger._load_all()
    first.clear()
    assert len(ledger._load_all()) == 3


def test_unterminated_last_line_is_returned_then_completed(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _add(ledger, 2)
    path = tmp_path / "traces.jsonl"
    extra = json.dumps(json.loads(path.read_text().splitlines()[0]) | {"id": "TRC-tail-001"})
    with path.open("a", encoding="utf-8") as fh:
        fh.write(extra)  # sans saut de ligne
    seen = ledger._load_all()
    assert [t.id for t in seen][-1] == "TRC-tail-001"
    assert seen == _full(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    journal_cache.clear()
    assert [t.id for t in ledger._load_all()].count("TRC-tail-001") == 1


def test_a_rewritten_journal_is_reread_in_full(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _add(ledger, 10)
    ledger._load_all()
    path = tmp_path / "traces.jsonl"
    lines = path.read_text().splitlines()
    # même inode, même début de taille mais contenu différent avant le point de reprise
    path.write_text("\n".join(reversed(lines)) + "\n" + lines[0].replace("TRC-RUN-a", "TRC-RUN-z") + "\n")
    assert ledger._load_all() == _full(tmp_path)


def test_a_truncated_journal_is_reread_in_full(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _add(ledger, 10)
    ledger._load_all()
    path = tmp_path / "traces.jsonl"
    path.write_text("\n".join(path.read_text().splitlines()[:3]) + "\n")
    assert len(ledger._load_all()) == 3


def test_missing_journal_is_empty(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    assert ledger._load_all() == []
    _add(ledger, 1)
    assert len(ledger._load_all()) == 1
