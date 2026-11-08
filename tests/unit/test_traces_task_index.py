"""Index incrémental des traces par tâche — parses comptés, pas devinés.

Chaque cas vérifie l'index *et* les compteurs de travail : un parse complet
non désiré ou un parse incrémental manqué se lirait directement dans
``TaskTraceIndex.stats()``.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from grimoire.traces.schemas import TraceOutcome, TraceRecord
from grimoire.traces.task_index import TaskTraceIndex, index_for


def _line(task_id: str, trace_id: str) -> bytes:
    record = TraceRecord(
        id=trace_id,
        run_id=f"RUN-{trace_id}",
        workflow_instance_id="",
        mission_id="",
        task_id=task_id,
        recipe_id="recipe.test.index",
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-11-07T10:00:00Z",
    )
    return (json.dumps(record.to_dict()) + "\n").encode("utf-8")


def test_deux_appels_sans_changement_un_seul_parse_complet(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_bytes(b"".join(_line("T-1", f"t{i}") for i in range(3)))
    index = TaskTraceIndex(path)

    first = index.traces_by_task()
    second = index.traces_by_task()

    assert second == first
    assert [t.id for t in first["T-1"]] == ["t0", "t1", "t2"]
    assert index.stats() == {"full_loads": 1, "incremental_loads": 0, "parsed_records": 3}


def test_ajout_de_lignes_ne_parse_que_les_ajouts(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_bytes(_line("T-1", "t0") + _line("T-2", "t1"))
    index = TaskTraceIndex(path)
    index.traces_by_task()

    with open(path, "ab") as fh:
        fh.write(_line("T-1", "t2") + _line("T-1", "t3"))
    grouped = index.traces_by_task()

    assert [t.id for t in grouped["T-1"]] == ["t0", "t2", "t3"]
    assert [t.id for t in grouped["T-2"]] == ["t1"]
    # 4 traces parsées au total : les deux nouvelles seulement, pas les quatre.
    assert index.stats() == {"full_loads": 1, "incremental_loads": 1, "parsed_records": 4}


def test_troncature_reconstruit_l_index(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_bytes(_line("T-1", "t0") + _line("T-1", "t1") + _line("T-1", "t2"))
    index = TaskTraceIndex(path)
    assert len(index.traces_by_task()["T-1"]) == 3

    # Rotation par troncature : le fichier rétrécit, l'ancien index n'est plus valable.
    path.write_bytes(_line("T-1", "seule"))
    grouped = index.traces_by_task()

    assert [t.id for t in grouped["T-1"]] == ["seule"]
    assert index.stats()["full_loads"] == 2


def test_remplacement_du_fichier_reconstruit_meme_signature(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_bytes(_line("T-1", "a0") + _line("T-1", "a1"))
    index = TaskTraceIndex(path)
    index.traces_by_task()
    before = path.stat()

    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(_line("T-1", "b0") + _line("T-1", "b1"))
    assert replacement.stat().st_size == before.st_size
    # Même taille, même mtime_ns : seul l'inode distingue les deux journaux.
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement.replace(path)

    grouped = index.traces_by_task()

    assert [t.id for t in grouped["T-1"]] == ["b0", "b1"]
    assert index.stats()["full_loads"] == 2


def test_ligne_finale_incomplete_ignoree_jusqua_completion(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    complete = _line("T-1", "t0") + _line("T-1", "t1")
    last = _line("T-1", "t2")
    partial_at = len(last) // 2
    path.write_bytes(complete + last[:partial_at])
    index = TaskTraceIndex(path)

    first = index.traces_by_task()

    # La ligne en cours d'écriture n'est pas consommée : elle n'est pas complète.
    assert [t.id for t in first["T-1"]] == ["t0", "t1"]
    assert index.stats()["parsed_records"] == 2

    with open(path, "ab") as fh:
        fh.write(last[partial_at:])
    second = index.traces_by_task()

    assert [t.id for t in second["T-1"]] == ["t0", "t1", "t2"]
    # Une seule trace de plus : le reliquat et sa suite, jamais les deux premières.
    assert index.stats() == {"full_loads": 1, "incremental_loads": 1, "parsed_records": 3}


def test_appels_concurrents_partagent_le_meme_index(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_bytes(b"".join(_line("T-1", f"t{i}") for i in range(5)))
    index = TaskTraceIndex(path)
    barrier = threading.Barrier(8)
    results: list[dict[str, list[TraceRecord]]] = []

    def read() -> None:
        barrier.wait()
        results.append(index.traces_by_task())

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(result == results[0] for result in results)
    assert [t.id for t in results[0]["T-1"]] == [f"t{i}" for i in range(5)]
    # Un seul des huit appels a payé le parse complet.
    assert index.stats()["full_loads"] == 1


def test_index_for_partage_une_instance_par_fichier(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    path.write_bytes(_line("T-1", "t0"))
    assert index_for(path) is index_for(path)
