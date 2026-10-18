"""CodeQL py/path-injection : le ledger n'écrit que ses deux journaux, sous sa racine."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.ledger import MissionLedger


@pytest.mark.parametrize("name", ["../x", "a/b", "/etc/passwd", "..", "other.jsonl", ""])
def test_malicious_file_name_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(GrimoireMissionError):
        MissionLedger._write_appended(tmp_path / "root" / name, {"k": 1})
    assert not (tmp_path / "x").exists()


def test_symlink_escaping_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.jsonl"
    (root / "events.jsonl").symlink_to(outside)
    with pytest.raises(GrimoireMissionError):
        MissionLedger._write_appended(root / "events.jsonl", {"k": 1})
    assert not outside.exists()


def test_regular_journal_still_appends(tmp_path: Path) -> None:
    ledger = MissionLedger(tmp_path / "ledger")
    ledger.create_mission("Pack", origin="user")
    assert (tmp_path / "ledger" / "events.jsonl").read_text(encoding="utf-8").count("\n") >= 1
