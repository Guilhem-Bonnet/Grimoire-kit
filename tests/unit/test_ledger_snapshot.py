"""K13 (épopée #705) — l'instantané du rejeu : le même état que le rejeu complet, ou rien.

Le banc de K13 mesure ``≈ 170 ms`` pour rejouer 10 000 événements, rejoués une fois *par
processus de hook* alors que D21 accorde 100 ms aux hooks. D21 prescrit alors un instantané
``ledger.snapshot.json`` (offset et hash) avec rejeu incrémental, et seulement alors. Ces
tests tiennent la contrainte qui le rend acceptable : il ne change jamais ce que le ledger
répond, et il n'est restauré que s'il concorde avec le journal (le hash ne lie pas l'état : voir le dernier test).
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from grimoire.core.standard_state import suspended_state_writes
from grimoire.missions import ledger_snapshot
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import DependencyKind, LedgerEvent, TaskState
from grimoire.missions.service import TaskService

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SNAPSHOT = ledger_snapshot.SNAPSHOT_FILENAME


@pytest.fixture
def always_snapshot(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Rend le budget « dépassé » pour tout rejeu, à l'appel (le seuil réel ne se règle pas à la milliseconde).

    À appeler *après* l'écriture du scénario : le service rejoue aussi, et écrirait lui-même l'instantané.
    """

    def enable() -> None:
        monkeypatch.setattr(ledger_snapshot, "SNAPSHOT_AFTER_MS", -1.0)

    return enable


def _root(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


def _view(ledger: MissionLedger) -> dict[str, Any]:
    """Tout ce que le ledger répond : un instantané correct ne change aucune de ces réponses."""
    tasks = ledger.list_tasks()
    return {
        "missions": {m.id: m.to_dict() for m in ledger.list_missions()},
        "tasks": {t.id: t.to_dict() for t in tasks},
        "events": [e.to_dict() for e in ledger.list_events()],
        "rejected": ledger.rejected_events(),
        "epoch_high": dict(ledger._epoch_high),
        "idem": sorted(ledger._idem_seen),
        "clock": ledger.clock_state(),
        "attempts": {t.id: ledger.attempts(t.id) for t in tasks},
    }


def _fresh(root: Path) -> MissionLedger:
    return MissionLedger(root)


def _snapshot_offset(root: Path) -> int:
    return int(json.loads((root / SNAPSHOT).read_text(encoding="utf-8"))["offset"])


def test_snapshot_replay_equals_full_replay(tmp_path: Path, always_snapshot: Callable[[], None]) -> None:
    service = TaskService(tmp_path)
    root = service.ledger._root
    ids = [service.add(f"Tâche {i}", ("ça marche",), owner="g", actor="claude", ready=True).task.id for i in range(4)]
    service.context(ids[0])
    service.claim(ids[0], "claude", host="local")
    service.ledger.renew_lease(ids[0], 1)
    service.note_progress(ids[0], "un pas", "claude")
    service.ledger.link_tasks(ids[2], ids[1], DependencyKind.BLOCKS)
    service.cancel(ids[3], "abandon", "g", force=True)
    service.ledger.append_event(
        "task.transitioned", ids[0], "task", "ancien-titulaire",
        {"task_id": ids[0], "to_state": TaskState.CLOSED.value, "lease_epoch": 99},
    )

    always_snapshot()
    full = _view(_fresh(root))  # écrit l'instantané (seuil dépassé)
    assert (root / SNAPSHOT).is_file()
    assert full["rejected"], "le scénario doit contenir un événement refusé au rejeu"

    resumed = _view(_fresh(root))
    assert resumed == full

    # Une suite après l'instantané : le rejeu incrémental s'applique par-dessus l'état restauré.
    service.note_progress(ids[0], "un autre pas", "claude")
    service.cancel(ids[1], "plus utile", "g", force=True)
    after = _fresh(root)
    # Path.replace, pas Path.rename : le rejeu complet réécrit l'instantané, la cible existe donc déjà au retour
    # (Path.rename lève FileExistsError sous Windows) ; on restitue l'instantané d'origine par-dessus.
    original = (root / SNAPSHOT).read_bytes()
    (root / SNAPSHOT).replace(root / "ledger.snapshot.json.bak")
    expected = _view(_fresh(root))
    (root / "ledger.snapshot.json.bak").replace(root / SNAPSHOT)
    assert (root / SNAPSHOT).read_bytes() == original
    assert _view(after) == expected
    assert expected != full


def test_snapshot_replay_only_replays_the_tail(tmp_path: Path, always_snapshot: Callable[[], None], monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskService(tmp_path)
    root = service.ledger._root
    for i in range(6):
        service.add(f"Tâche {i}", ("ok",), owner="g", actor="claude", ready=True)
    covered = len((root / "events.jsonl").read_text(encoding="utf-8").splitlines())
    always_snapshot()
    _fresh(root).list_tasks()  # écrit l'instantané
    monkeypatch.setattr(ledger_snapshot, "SNAPSHOT_AFTER_MS", 1e9)  # la suite reste au journal
    service.add("Une de plus", ("ok",), owner="g", actor="claude", ready=True)
    total = len((root / "events.jsonl").read_text(encoding="utf-8").splitlines())
    assert total > covered

    parsed: list[str] = []
    original = LedgerEvent.from_dict.__func__  # type: ignore[attr-defined]

    def spy(cls: type[LedgerEvent], raw: dict[str, Any]) -> LedgerEvent:
        parsed.append(str(raw.get("id", "")))
        return original(cls, raw)

    monkeypatch.setattr(LedgerEvent, "from_dict", classmethod(spy))
    assert len(_fresh(root).list_tasks()) == 7
    assert len(parsed) == total - covered, "seule la suite de l'instantané doit être rejouée"


@pytest.mark.parametrize("damage", ["flip_prefix_byte", "truncate_journal", "other_producer", "garbage", "no_state", "offset_mid_line"])
def test_an_unproven_snapshot_is_ignored(tmp_path: Path, always_snapshot: Callable[[], None], damage: str) -> None:
    """Hash, version, forme ou offset douteux : le rejeu complet fait foi, jamais l'instantané."""
    service = TaskService(tmp_path)
    root = service.ledger._root
    for i in range(5):
        service.add(f"Alpha {i}", ("ok",), owner="g", actor="claude", ready=True)
    always_snapshot()
    _fresh(root).list_tasks()
    snap_path, events = root / SNAPSHOT, root / "events.jsonl"
    assert snap_path.is_file()
    raw = json.loads(snap_path.read_text(encoding="utf-8"))
    if damage == "flip_prefix_byte":
        events.write_text(events.read_text(encoding="utf-8").replace("Alpha 2", "Alpha X", 1), encoding="utf-8")
    elif damage == "truncate_journal":
        lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
        events.write_text("".join(lines[:-3]), encoding="utf-8")
    elif damage == "other_producer":
        snap_path.write_text(json.dumps({**raw, "producer": "0.0.0-autre"}), encoding="utf-8")
    elif damage == "garbage":
        snap_path.write_text("{pas du json", encoding="utf-8")
    elif damage == "no_state":
        snap_path.write_text(json.dumps({k: v for k, v in raw.items() if k != "state"}), encoding="utf-8")
    else:  # offset_mid_line : le hash du préfixe ne dit rien d'une coupe hors fin de ligne
        data = events.read_bytes()
        cut = data.index(b"\n") + 5
        import hashlib

        snap_path.write_text(
            json.dumps({**raw, "offset": cut, "prefix_sha256": hashlib.sha256(data[:cut]).hexdigest()}), encoding="utf-8"
        )

    ledger = _fresh(root)
    got = {t.title for t in ledger.list_tasks()}
    snap_path.unlink(missing_ok=True)
    expected = {t.title for t in _fresh(root).list_tasks()}
    assert got == expected
    if damage == "flip_prefix_byte":
        assert "Alpha X" in got, "un instantané périmé aurait servi l'ancien titre"


def test_no_snapshot_while_the_budget_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Budget large par construction : sur un runner chargé, rejouer cinq tâches dépassait
    # parfois le seuil réel et le test écrivait l'instantané qu'il interdit.
    monkeypatch.setattr(ledger_snapshot, "SNAPSHOT_AFTER_MS", 60_000.0)
    service = TaskService(tmp_path)
    for i in range(5):
        service.add(f"Tâche {i}", ("ok",), owner="g", actor="claude", ready=True)
    _fresh(service.ledger._root).list_tasks()
    assert not (service.ledger._root / SNAPSHOT).exists(), "rien n'est écrit tant que le rejeu tient dans le budget"


def test_no_snapshot_when_state_writes_are_suspended(tmp_path: Path, always_snapshot: Callable[[], None]) -> None:
    service = TaskService(tmp_path)
    service.add("Tâche", ("ok",), owner="g", actor="claude", ready=True)
    always_snapshot()
    with suspended_state_writes():
        _fresh(service.ledger._root).list_tasks()
    assert not (service.ledger._root / SNAPSHOT).exists()


def test_no_snapshot_over_an_unterminated_last_line(tmp_path: Path, always_snapshot: Callable[[], None]) -> None:
    service = TaskService(tmp_path)
    service.add("Tâche", ("ok",), owner="g", actor="claude", ready=True)
    events = service.ledger._root / "events.jsonl"
    events.write_text(events.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    always_snapshot()
    _fresh(service.ledger._root).list_tasks()
    assert not (service.ledger._root / SNAPSHOT).exists()


def test_a_snapshot_keeps_reporting_the_unreadable_lines_it_covers(
    tmp_path: Path, always_snapshot: Callable[[], None]
) -> None:
    """K12 x K13 : une ligne tronquée que l'instantané couvre reste signalée (vu en CI : un rejeu lent l'effaçait)."""
    service = TaskService(tmp_path)
    tid = service.add("Tâche", ("ok",), owner="g", actor="claude", ready=True).task.id
    events = service.ledger._root / "events.jsonl"
    events.write_bytes(events.read_bytes() + b'{"id": "evt-x", "event_type": "task.tr\n')
    service.comment(tid, "après la coupure", "g")
    expected = _fresh(service.ledger._root).unreadable_lines()
    assert len(expected) == 1
    always_snapshot()
    _fresh(service.ledger._root).list_tasks()  # écrit l'instantané par-dessus la ligne illisible
    assert _snapshot_offset(service.ledger._root) == len(events.read_bytes())
    assert _fresh(service.ledger._root).unreadable_lines() == expected


def test_clock_state_does_not_reread_the_covered_events(tmp_path: Path, always_snapshot: Callable[[], None], monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskService(tmp_path)
    for i in range(4):
        service.add(f"Tâche {i}", ("ok",), owner="g", actor="claude", ready=True)
    root = service.ledger._root
    always_snapshot()
    expected = _fresh(root).clock_state()  # écrit l'instantané

    def refuse(ledger: MissionLedger) -> list[LedgerEvent]:
        raise AssertionError("clock_state n'a besoin que du dernier horodatage")

    monkeypatch.setattr(ledger_snapshot, "all_events", refuse)
    assert _fresh(root).clock_state() == expected


def test_snapshot_keeps_the_exact_titles_the_full_replay_gives(tmp_path: Path, always_snapshot: Callable[[], None]) -> None:
    """Un séparateur de ligne Unicode et une moitié de substitution échappée traversent l'instantané sans bouger."""
    service = TaskService(tmp_path)
    root = service.ledger._root
    service.add("avant", ("ok",), owner="g", actor="claude", ready=True)
    events = root / "events.jsonl"
    created = next(
        raw for raw in map(json.loads, events.read_text(encoding="utf-8").splitlines()) if raw["event_type"] == "task.created"
    )
    for label, title in (("sep", "a b"), ("surrogate", "c-SURROGATE-d")):
        record = json.loads(json.dumps(created))
        record["id"] += f"-{label}"
        record["entity_id"] = record["payload"]["id"] = f"{record['payload']['id']}-{label}"
        record["payload"]["title"] = title
        text = json.dumps(record, ensure_ascii=False)
        if label == "surrogate":
            text = text.replace("SURROGATE", r"\ud800")
        with events.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")

    always_snapshot()
    full = _view(_fresh(root))  # écrit l'instantané
    assert (root / SNAPSHOT).is_file()
    assert _view(_fresh(root)) == full


def test_a_slow_tail_rewrites_the_snapshot_further(tmp_path: Path, always_snapshot: Callable[[], None]) -> None:
    service = TaskService(tmp_path)
    root = service.ledger._root
    service.add("Tâche", ("ok",), owner="g", actor="claude", ready=True)
    always_snapshot()
    _fresh(root).list_tasks()
    first = _snapshot_offset(root)
    service.add("Une autre", ("ok",), owner="g", actor="claude", ready=True)
    _fresh(root).list_tasks()
    assert _snapshot_offset(root) > first
    assert _snapshot_offset(root) == (root / "events.jsonl").stat().st_size


def test_replay_of_a_task_replaced_after_the_snapshot(tmp_path: Path, always_snapshot: Callable[[], None]) -> None:
    """Une transition postérieure à l'instantané s'applique à la tâche restaurée, pas à une copie périmée."""
    service = TaskService(tmp_path)
    root = service.ledger._root
    tid = service.add("Tâche", ("ok",), owner="g", actor="claude", ready=True).task.id
    always_snapshot()
    _fresh(root).list_tasks()
    service.cancel(tid, "abandon", "g", force=True)
    task = _fresh(root).get_task(tid)
    assert task is not None and task.status is TaskState.CANCELLED
    assert replace(task) == task


def test_the_snapshot_proves_the_journal_prefix_not_the_state_it_carries(
    tmp_path: Path, always_snapshot: Callable[[], None]
) -> None:
    """La limite, nommée (K13-S1-02) : le hash lie le préfixe du journal, pas l'état de l'instantané.

    Un instantané réécrit à la main, au hash de préfixe valide, est restauré tel quel : le rejeu
    complet « fait foi » seulement tant que l'instantané est ignoré, et rien ici ne l'authentifie.
    La défense est la garde d'autorité (``test_authority_guard.py`` : aucune voie d'écriture d'outil
    vers ``ledger.snapshot.json``), pas ce fichier. Ce test casse le jour où l'instantané est
    authentifié : il faudra alors retirer la limite de la docstring de ``ledger_snapshot``.
    """
    service = TaskService(tmp_path)
    root = service.ledger._root
    tid = service.add("Tâche", ("ok",), owner="g", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="local")
    always_snapshot()
    _fresh(root).list_tasks()  # écrit l'instantané
    forged = json.loads((root / SNAPSHOT).read_text(encoding="utf-8"))
    forged["state"]["tasks"][tid]["status"] = TaskState.CLOSED.value
    forged["state"]["tasks"][tid]["claim"] = None
    (root / SNAPSHOT).write_text(json.dumps(forged), encoding="utf-8")

    lines = (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    reached = [json.loads(line)["payload"].get("to_state") for line in lines if '"task.transitioned"' in line]
    assert TaskState.CLOSED.value not in reached  # le journal ne clôt rien
    assert _fresh(root).get_task(tid).status is TaskState.CLOSED  # le rejeu restaure l'état falsifié


def test_live_appended_state_equals_full_replay_and_altered_prefix_is_rejected(
    tmp_path: Path, always_snapshot: Callable[[], None]
) -> None:
    """I3 : l'état tenu en mémoire pendant les ajouts == le rejeu complet ; un octet changé dans le préfixe écarte l'instantané."""
    service = TaskService(tmp_path)
    root = service.ledger._root
    ids = [service.add(f"T{i}", ("ok",), owner="g", actor="claude", ready=True).task.id for i in range(3)]
    service.claim(ids[0], "claude", host="local")
    service.note_progress(ids[0], "pas", "claude")
    service.ledger.link_tasks(ids[2], ids[1], DependencyKind.BLOCKS)
    assert _view(service.ledger) == _view(_fresh(root))  # état vivant == rejeu complet

    always_snapshot()
    _view(_fresh(root))
    assert (root / SNAPSHOT).is_file()
    path = root / "events.jsonl"
    data = bytearray(path.read_bytes())
    data[10] ^= 0x01  # altère le préfixe couvert (même taille)
    path.write_bytes(bytes(data))
    ledger = _fresh(root)
    ledger._load()
    assert ledger._snapshot is None  # rejeté : rejeu complet
