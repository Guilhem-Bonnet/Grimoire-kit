"""#1277 — un lecteur JSONL ne coupe une ligne que sur ``\\n``.

``json.dumps(ensure_ascii=False)`` écrit U+2028, U+2029 et U+0085 bruts ; ``str.splitlines``
les prend pour des fins de ligne et la trace, l'idée ou l'événement disparaît à la relecture.
Chaque test pose une ligne qui contient ce caractère et une ligne ordinaire, et exige les deux.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SEP = " "


def _line(obj: dict[str, object]) -> str:
    line = json.dumps(obj, ensure_ascii=False)
    assert SEP in line
    return line


# ── _next_id : comptage par blocs ──────────────────────────────────────────


def test_next_id_chunked_count_matches_whole_file_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.traces import ledger as ledger_mod
    from grimoire.traces.ledger import _count_occurrences

    path = tmp_path / "traces.jsonl"
    rows = [
        json.dumps({"id": f"TRC-{n}", "run_id": f"RUN-{n % 7}", "note": "é" * (n % 5)}, ensure_ascii=False)
        for n in range(4000)
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    needle = b'"run_id": "RUN-3"'
    expected = path.read_bytes().count(needle)
    assert expected > 500
    # Blocs plus petits que l'aiguille, puis de taille quelconque : une occurrence à cheval est comptée une fois.
    for block in (1, 7, len(needle), len(needle) + 1, 97, 4096, 1 << 20):
        monkeypatch.setattr(ledger_mod, "_COUNT_BLOCK_BYTES", block)
        assert _count_occurrences(path, needle) == expected, block


def test_next_id_equivalent_on_a_journal_of_thousands_of_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.traces import ledger as ledger_mod
    from grimoire.traces.ledger import TraceLedger

    ledger = TraceLedger(tmp_path)
    rows = [
        json.dumps({"id": f"T{n}", "run_id": "RUN-a" if n % 3 == 0 else "RUN-é"}, ensure_ascii=False)
        for n in range(3000)
    ]
    ledger._traces_path.parent.mkdir(parents=True, exist_ok=True)
    ledger._traces_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(ledger_mod, "_COUNT_BLOCK_BYTES", 251)
    assert ledger._next_id("RUN-a") == "TRC-RUN-a-1001"
    assert ledger._next_id("RUN-é") == "TRC-RUN-é-2001"
    assert ledger._next_id("RUN-neuf") == "TRC-RUN-neuf-001"


# ── lecteurs ───────────────────────────────────────────────────────────────


def test_evidence_service_reads_packs_and_verdicts(tmp_path: Path) -> None:
    from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
    from grimoire.evidence.service import EvidenceService

    svc = EvidenceService(tmp_path / "ev")
    item = EvidenceItem(id="E1", kind=next(iter(EvidenceKind)), uri="x", digest="d", summary=f"a{SEP}b")
    first = svc.create_pack("GAO-1", EvidenceProfile.LIGHT, [item], acceptance=(f"c{SEP}d",))
    second = svc.create_pack("GAO-1", EvidenceProfile.LIGHT, [item])
    svc.verify(first, acceptance=[f"c{SEP}d"])
    svc.verify(second, acceptance=[f"c{SEP}d"])
    fresh = EvidenceService(tmp_path / "ev")
    assert {p.id for p in fresh.list_packs()} == {first.id, second.id}
    assert len(fresh.list_verdicts()) == 2
    assert fresh.has_unreadable_verdicts("GAO-1") is False


def test_runtime_kernel_reads_instances_checkpoints_and_events(tmp_path: Path) -> None:
    from grimoire.runtime.kernel import RuntimeKernel
    from grimoire.runtime.schemas import ExecutionContext

    ctx = ExecutionContext(
        run_id="", mission_id="MIS-1", task_id="GAO-1", workflow_instance_id="",
        actor_id="cli", host_id="local", risk_profile="standard",
    )
    kernel = RuntimeKernel(tmp_path / "rt")
    wfi = kernel.create_instance(ctx, "recipe.test", inputs_ref=f"a{SEP}b")
    wfi2 = kernel.create_instance(ctx, "recipe.test", inputs_ref=f"c{SEP}d")
    kernel.start(wfi.id, ctx)
    kernel.checkpoint(wfi.id, ctx, step_id=f"s{SEP}1", completed_steps=[f"s{SEP}1"], pending_steps=[])
    fresh = RuntimeKernel(tmp_path / "rt")
    assert {w.id for w in fresh.list_instances()} == {wfi.id, wfi2.id}
    assert [c.step_id for c in fresh.list_checkpoints(wfi.id)] == [f"s{SEP}1"]
    assert any(SEP in line for line in (tmp_path / "rt" / "checkpoints.jsonl").read_text(encoding="utf-8").split("\n"))
    assert fresh.get_run_events(wfi.id)


def test_idea_store_keeps_ideas_with_u2028(tmp_path: Path) -> None:
    from grimoire.missions.ideas import IdeaStore

    store = IdeaStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store._append({"event": "add", "id": "IDEA-0001", "text": f"a{SEP}b", "source": "cli", "ts": "t"})
    store._append({"event": "add", "id": "IDEA-0002", "text": "c", "source": "cli", "ts": "t"})
    assert [i.id for i in store.list()] == ["IDEA-0001", "IDEA-0002"]
    assert store.get("IDEA-0001").text == f"a{SEP}b"


def test_stigmergy_events_survive_read_and_cap_rewrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.tools import stigmergy

    stigmergy.log_event(tmp_path, "act", note=f"a{SEP}b")
    stigmergy.log_event(tmp_path, "act", note="c")
    assert [e["note"] for e in stigmergy.read_events(tmp_path)] == [f"a{SEP}b", "c"]
    monkeypatch.setattr(stigmergy, "EVENTS_MAX_LINES", 2)
    stigmergy.log_event(tmp_path, "act", note="d")  # troncature : ré-écriture du journal
    assert [e["note"] for e in stigmergy.read_events(tmp_path)] == ["c", "d"]
    stigmergy.log_event(tmp_path, "act", note=f"e{SEP}f")
    stigmergy.log_event(tmp_path, "act", note="g")
    assert [e["note"] for e in stigmergy.read_events(tmp_path)] == [f"e{SEP}f", "g"]


def test_memory_undo_snapshot_with_u2028(tmp_path: Path) -> None:
    from grimoire.tools.workspace_memory_gestures import UNDO_RELPATH, _snapshot_of

    path = tmp_path / UNDO_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(_line({"ref": "r1", "text": f"a{SEP}b"}) + "\n" + _line({"ref": "r2", "text": f"c{SEP}d"}) + "\n", encoding="utf-8")
    snap = _snapshot_of(tmp_path, "r1")
    assert snap is not None and snap["text"] == f"a{SEP}b"
    assert _snapshot_of(tmp_path, "r2") is not None


def test_capability_probe_log_with_u2028(tmp_path: Path) -> None:
    from grimoire.hosts.capability_probe import _read_log

    log = tmp_path / "hook.log"
    log.write_text(_line({"n": 1, "v": f"a{SEP}b"}) + "\n" + json.dumps({"n": 2}) + "\n", encoding="utf-8")
    assert [c["n"] for c in _read_log(log)] == [1, 2]


def test_runtime_event_journal_audit_with_u2028(tmp_path: Path) -> None:
    from grimoire.core.agentic_standard import EVENT_JOURNAL_FILE, audit_runtime_events

    path = tmp_path / EVENT_JOURNAL_FILE
    path.parent.mkdir(parents=True)
    ok = {"event_type": "t", "timestamp": "2026-01-01T00:00:00+00:00"}
    path.write_text(_line({**ok, "note": f"a{SEP}b"}) + "\n" + json.dumps(ok) + "\n", encoding="utf-8")
    report = audit_runtime_events(tmp_path)
    assert report["invalid_lines"] == []
    assert report["event_count"] == 2


def test_mission_ledger_incidents_resolve_and_import(tmp_path: Path) -> None:
    from grimoire.missions.ledger import MissionLedger

    ledger = MissionLedger(tmp_path / "ml")
    first = ledger.open_incident("MIS-1", "GAO-1", "k", f"a{SEP}b")
    second = ledger.open_incident("MIS-1", "GAO-2", "k", f"c{SEP}d")
    ledger.resolve_incident(second.id)  # ré-écrit tout le fichier : la ligne de `first` doit survivre
    fresh = MissionLedger(tmp_path / "ml")
    assert {i.id for i in fresh.list_incidents()} == {first.id, second.id}
    assert [i.id for i in fresh.open_incidents()] == [first.id]

    exported = tmp_path / "out.jsonl"
    fresh.export_jsonl(exported)
    target = MissionLedger(tmp_path / "ml2")
    assert target.import_jsonl(exported) == sum(1 for ln in exported.read_text(encoding="utf-8").split("\n") if ln.strip())


def test_task_flow_adapter_reads_events_with_u2028(tmp_path: Path) -> None:
    from grimoire.missions.task_flow_adapter import _read_events

    path = tmp_path / "events.jsonl"
    path.write_text(
        _line({"task": "t1", "event": "e", "note": f"a{SEP}b"}) + "\n" + json.dumps({"task": "t2", "event": "e"}) + "\n",
        encoding="utf-8",
    )
    assert [e["task"] for e in _read_events(path)] == ["t1", "t2"]
