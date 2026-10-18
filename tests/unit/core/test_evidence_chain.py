"""Journal de preuve chaîné et écritures perdues comptées (W4-03).

Avant ce lot, ``append_evidence_event`` écrivait des lignes sans lien entre elles
et avalait toute ``OSError`` : une ligne réécrite, une dernière ligne retirée ou
une écriture refusée ne laissaient aucune trace dans ``standard verify``.
"""

from __future__ import annotations

import builtins
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile
from grimoire.core.standard_checks.evidence_chain import (
    analyze_chain,
    dropped_writes,
    head_path,
)
from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_bash_event,
    evidence_log_relpath,
    read_evidence_log,
)

TASK = "T-1"


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed")
    return tmp_path


def _log(root: Path) -> Path:
    return root / evidence_log_relpath(TASK)


def _fill(root: Path, count: int) -> Path:
    for index in range(count):
        append_evidence_event(root, TASK, build_bash_event(f"echo {index}", exit_code=0))
    return _log(root)


def _check_ids(root: Path) -> dict[str, str]:
    result = verify_standard_profile(root, task_id=TASK)
    return {check.id: check.severity for check in result.checks if check.id.startswith("evidence.")}


def test_intact_chain_links_each_line_to_the_previous_one(governed: Path) -> None:
    log = _fill(governed, 4)
    report = analyze_chain(log)
    assert (report.status, report.chained, report.legacy, report.anchored) == ("intact", 4, 0, True)
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["prev"] == "genesis"
    assert len({line["prev"] for line in lines}) == 4
    assert "evidence.chain_broken" not in _check_ids(governed)


def test_a_modified_line_is_located_and_fails_verify(governed: Path) -> None:
    log = _fill(governed, 4)
    lines = log.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("echo 1", "echo 999")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = analyze_chain(log)
    assert report.status == "broken"
    assert (report.break_after_line, report.break_line) == (2, 3)
    assert "ligne 2" in report.message
    assert _check_ids(governed)["evidence.chain_broken"] == "error"
    assert verify_standard_profile(governed, task_id=TASK).ok is False


def test_a_removed_middle_line_is_detected(governed: Path) -> None:
    log = _fill(governed, 4)
    lines = log.read_text(encoding="utf-8").splitlines()
    del lines[1]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = analyze_chain(log)
    assert report.status == "broken"
    assert (report.break_after_line, report.break_line) == (1, 2)


def test_a_removed_last_line_is_detected_through_the_anchor(governed: Path) -> None:
    log = _fill(governed, 3)
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    report = analyze_chain(log)
    assert report.status == "broken"
    assert report.truncated == 1
    assert "manquent" in report.message
    assert _check_ids(governed)["evidence.chain_broken"] == "error"


def test_a_modified_last_line_is_detected_through_the_anchor(governed: Path) -> None:
    log = _fill(governed, 3)
    lines = log.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].replace("echo 2", "echo 777")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = analyze_chain(log)
    assert report.status == "broken"
    assert report.break_line == 3


def test_an_emptied_journal_is_detected(governed: Path) -> None:
    log = _fill(governed, 2)
    log.unlink()
    assert analyze_chain(log).status == "broken"


def test_existing_entries_are_read_as_legacy_then_chained(governed: Path) -> None:
    log = _log(governed)
    log.parent.mkdir(parents=True)
    log.write_text(
        '{"type": "bash", "command": "pytest -q", "exit_code": 0}\n{"type": "file_write", "path": "src/a.py"}\n',
        encoding="utf-8",
    )
    report = analyze_chain(log)
    assert (report.status, report.legacy, report.chained) == ("legacy", 2, 0)
    assert "evidence.chain_broken" not in _check_ids(governed)

    append_evidence_event(governed, TASK, build_bash_event("echo apres", exit_code=0))
    report = analyze_chain(log)
    assert (report.status, report.legacy, report.chained) == ("intact", 2, 1)
    assert len(read_evidence_log(governed, TASK)) == 3


def test_an_unchained_line_after_the_chain_started_is_a_break(governed: Path) -> None:
    log = _fill(governed, 2)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"type": "bash", "command": "injecte"}\n')
    report = analyze_chain(log)
    assert report.status == "broken"
    assert report.break_line == 3


def test_a_write_cut_by_a_crash_is_not_a_tamper(governed: Path) -> None:
    log = _fill(governed, 2)
    with open(log, "ab") as handle:
        handle.write(b'{"type": "bash", "comm')
    report = analyze_chain(log)
    assert report.status == "intact"
    assert report.unreadable_lines == (3,)
    append_evidence_event(governed, TASK, build_bash_event("echo apres", exit_code=0))
    assert analyze_chain(log).status == "intact"
    assert _check_ids(governed)["evidence.chain_unreadable_lines"] == "warning"


def test_an_oserror_is_counted_not_swallowed(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = _fill(governed, 2)
    real_open: Any = builtins.open

    def refusing_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if Path(str(file)) == log and "a" in mode:
            raise OSError(28, "No space left on device")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refusing_open)
    append_evidence_event(governed, TASK, build_bash_event("echo perdu", exit_code=0))
    append_evidence_event(governed, TASK, build_bash_event("echo perdu aussi", exit_code=0))
    monkeypatch.undo()

    assert dropped_writes(log) == 2
    assert head_path(log).is_file()
    assert _check_ids(governed)["evidence.writes_dropped"] == "warning"
    # La perte ne casse pas la chaîne et le compte survit aux écritures suivantes.
    append_evidence_event(governed, TASK, build_bash_event("echo retour", exit_code=0))
    report = analyze_chain(log)
    assert (report.status, report.chained, report.dropped_writes) == ("intact", 3, 2)


def test_an_unencodable_event_is_counted(governed: Path) -> None:
    log = _fill(governed, 1)
    append_evidence_event(governed, TASK, {"type": "bash", "command": chr(0xD800)})
    assert dropped_writes(log) == 1
    assert analyze_chain(log).chained == 1


def test_a_missing_anchor_is_said_not_silent(governed: Path) -> None:
    log = _fill(governed, 2)
    head_path(log).unlink()
    assert analyze_chain(log).status == "intact"
    assert _check_ids(governed)["evidence.chain_unanchored"] == "warning"
    append_evidence_event(governed, TASK, build_bash_event("echo apres", exit_code=0))
    report = analyze_chain(log)
    assert (report.chained, report.anchored) == (3, True)


def test_an_anchor_that_is_behind_the_file_is_a_break(governed: Path) -> None:
    log = _fill(governed, 3)
    anchor = head_path(log)
    stale = json.loads(anchor.read_text(encoding="utf-8"))
    append_evidence_event(governed, TASK, build_bash_event("echo 3", exit_code=0))
    # L'ancre est écrite avant la ligne : une ancre en retard n'est plus un crash, c'est une divergence.
    anchor.write_text(json.dumps(stale), encoding="utf-8")
    assert analyze_chain(log).status == "broken"


def _tamper_drop_last(log: Path) -> None:
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")


def _tamper_modify_last(log: Path) -> None:
    lines = log.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].replace("echo 2", "echo 777")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tamper_delete_journal(log: Path) -> None:
    log.unlink()


@pytest.mark.parametrize("tamper", [_tamper_drop_last, _tamper_modify_last, _tamper_delete_journal])
def test_a_tampered_last_line_stays_detected_after_the_next_append(governed: Path, tamper: Any) -> None:
    """Le hook PostToolUse ajoute une ligne après chaque outil : l'ajout ne doit pas réparer l'ancre."""
    log = _fill(governed, 3)
    tamper(log)
    assert analyze_chain(log).status == "broken"
    for index in range(3):
        append_evidence_event(governed, TASK, build_bash_event(f"echo apres {index}", exit_code=0))
        assert analyze_chain(log).status == "broken"
    assert _check_ids(governed)["evidence.chain_broken"] == "error"
    assert verify_standard_profile(governed, task_id=TASK).ok is False


def test_a_tampered_single_line_journal_stays_detected_after_the_next_append(governed: Path) -> None:
    log = _fill(governed, 1)
    log.unlink()
    append_evidence_event(governed, TASK, build_bash_event("echo apres", exit_code=0))
    assert analyze_chain(log).status == "broken"


def test_a_failed_anchor_write_does_not_hide_a_later_modification(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'ancre ne retarde plus en silence : une écriture d'ancre refusée perd l'événement (compté), pas la preuve."""
    log = _fill(governed, 3)
    real_open: Any = builtins.open

    def refusing_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if Path(str(file)) == head_path(log) and "w" in mode:
            raise OSError(28, "No space left on device")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refusing_open)
    monkeypatch.setattr(
        "grimoire.core.standard_checks.evidence_chain.atomic_write",
        lambda *a, **k: (_ for _ in ()).throw(OSError(28, "full")),
    )
    append_evidence_event(governed, TASK, build_bash_event("echo 3", exit_code=0))
    monkeypatch.undo()

    assert analyze_chain(log).status == "intact"
    assert analyze_chain(log).chained == 3
    _tamper_modify_last(log)
    assert analyze_chain(log).status == "broken"
    append_evidence_event(governed, TASK, build_bash_event("echo apres", exit_code=0))
    assert analyze_chain(log).status == "broken"


def test_a_refused_line_write_leaves_the_anchor_where_it_was(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = _fill(governed, 2)
    before = head_path(log).read_text(encoding="utf-8")
    real_open: Any = builtins.open

    def refusing_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if Path(str(file)) == log and "a" in mode:
            raise OSError(28, "No space left on device")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refusing_open)
    append_evidence_event(governed, TASK, build_bash_event("echo perdu", exit_code=0))
    monkeypatch.undo()

    after = json.loads(head_path(log).read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "dropped_writes"} == {
        k: v for k, v in json.loads(before).items() if k != "dropped_writes"
    }
    assert analyze_chain(log).status == "intact"


def test_parallel_appends_do_not_fork_the_chain(governed: Path) -> None:
    def worker(tag: int) -> None:
        for index in range(15):
            append_evidence_event(governed, TASK, build_bash_event(f"echo {tag}-{index}", exit_code=0))

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    report = analyze_chain(_log(governed))
    assert (report.status, report.chained, report.dropped_writes) == ("intact", 60, 0)
