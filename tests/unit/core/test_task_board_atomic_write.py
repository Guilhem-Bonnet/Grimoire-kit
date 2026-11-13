"""GAO-board-du-sta-001 — le board du standard ne se lit jamais à moitié écrit.

Le board (``_grimoire/standard/task-board.yaml``) est réécrit à chaque
transition de tâche pendant que le tick d'orchestration, les hooks et le
cockpit le lisent. Ce que ces tests fixent :

- un lecteur concurrent ne voit jamais un fichier tronqué ni n'y perd une tâche ;
- une écriture interrompue avant le remplacement laisse l'ancien board intact
  et ne laisse aucun temporaire ;
- une erreur d'analyse passagère est retentée ; persistante, elle est levée ;
- sous Windows, un remplacement refusé par un lecteur est retenté, puis
  échoue avec un message qui dit pourquoi.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core import fsio, yaml_io
from grimoire.core.agentic_standard import list_board_tasks
from grimoire.core.standard_state import resolve_active_task
from grimoire.missions.board import write_board, write_board_bytes

#: Assez de tâches pour qu'une écriture en place ne tienne pas en un secteur —
#: c'est la taille du board réel (> 13 000 lignes) que la fenêtre de lecture imite.
TASK_COUNT = 200


def _board(generation: str, *, in_progress: str = "") -> dict[str, Any]:
    """Une projection complète, toujours au même nombre de tâches (le test le vérifie)."""
    return {
        "$schema": "grimoire-agentic-standard-task-board/v1",
        "metadata": {"project": "test", "generated_by": f"test-{generation}"},
        "states": ["proposed", "ready", "in_progress", "blocked", "review", "accepted", "released", "archived"],
        "tasks": [
            {
                "task_id": f"GAO-tmp-{i:04d}",
                "title": f"Tâche {i} ({generation})",
                "status": "in_progress" if f"GAO-tmp-{i:04d}" == in_progress else "proposed",
                "priority": "medium",
                "agent_roles": ["dev"],
                "acceptance_criteria": [f"le critère {i} passe"],
                "context_bundle_ref": f"_grimoire-output/context/GAO-tmp-{i:04d}/context-bundle.yaml",
                "decision_trace_ref": f"_grimoire-output/decisions/GAO-tmp-{i:04d}/decision-trace.yaml",
                "evidence_pack_ref": f"_grimoire-output/evidence/GAO-tmp-{i:04d}/evidence-pack.md",
            }
            for i in range(TASK_COUNT)
        ],
    }


#: Un board coupé au milieu d'une écriture en place : flux non fermé, YAML invalide par construction.
TORN_BOARD = "metadata:\n  project: test\ntasks: [\n  {task_id: 'GAO-tmp-0000'\n"


def _board_path(tmp_path: Path) -> Path:
    return tmp_path / "_grimoire" / "standard" / "task-board.yaml"


def test_a_reader_never_sees_a_half_written_board(tmp_path: Path) -> None:
    """Un fil réécrit en boucle pendant qu'on lit en boucle : jamais de board partiel."""
    dest = _board_path(tmp_path)
    write_board(dest, _board("initial"))
    # Les deux projections sont rendues une fois : le fil d'écriture teste le
    # geste disque (verrou + remplacement), pas le coût du rendu YAML.
    payloads: dict[str, bytes] = {}
    for generation in ("writer-0", "writer-1"):
        scratch = tmp_path / f"scratch-{generation}.yaml"
        write_board(scratch, _board(generation))
        payloads[generation] = scratch.read_bytes()
    stop = threading.Event()
    failures: list[str] = []

    def writer(generation: str) -> None:
        while not stop.is_set():
            write_board_bytes(dest, payloads[generation])

    threads = [threading.Thread(target=writer, args=(generation,), daemon=True) for generation in payloads]
    for thread in threads:
        thread.start()
    try:
        # Lecture nue, sans le délai de grâce de yaml_io : la garantie doit
        # venir du remplacement, pas de la relecture. Les écrivains tournent
        # jusqu'à la fin de ces lectures : chaque lecture est concurrente.
        for _ in range(8):
            document = YAML(typ="safe").load(dest)
            tasks = document.get("tasks") if isinstance(document, dict) else None
            if not isinstance(tasks, list):
                failures.append("le board lu n'a pas de liste `tasks`")
            elif len(tasks) != TASK_COUNT:
                failures.append(f"{len(tasks)} tâches lues au lieu de {TASK_COUNT}")
            if failures:
                break
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
    assert not failures, failures[0]


def test_an_interrupted_write_keeps_the_previous_board_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception avant le remplacement : l'ancien fichier est intact, aucun temporaire ne reste."""
    dest = _board_path(tmp_path)
    write_board(dest, _board("ancien"))
    before = dest.read_bytes()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("panne avant le remplacement")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="panne"):
        write_board(dest, _board("nouveau"))
    assert dest.read_bytes() == before
    assert not list(dest.parent.glob(".*.tmp")), "un fichier temporaire est resté"


def test_a_persistent_parse_error_is_raised_after_the_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un YAML cassé pour de bon lève après les tentatives — jamais un document vide en silence."""
    path = tmp_path / "torn.yaml"
    path.write_text("tasks: [\n  {task_id: 'T1'\n", encoding="utf-8")
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)
    with pytest.raises(YAMLError):
        yaml_io.load_yaml_with_retry(path, attempts=3, delay=0.02)
    assert len(waits) == 2, "trois tentatives doivent produire deux attentes"


def test_a_transient_parse_error_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le fichier remplacé entre deux tentatives se lit : la fenêtre d'écriture est passagère."""
    path = tmp_path / "torn.yaml"
    path.write_text("tasks: [\n", encoding="utf-8")
    healed: list[bool] = []

    def heal(_delay: float) -> None:
        if not healed:  # une seule réparation, comme un écrivain réel
            healed.append(True)
            path.write_text("tasks: []\n", encoding="utf-8")

    monkeypatch.setattr(time, "sleep", heal)
    assert yaml_io.load_yaml_with_retry(path, attempts=3, delay=0.02) == {"tasks": []}
    assert healed == [True]


def test_the_tolerant_reader_renders_an_empty_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent, racine qui n'est pas un mapping, ou cassé pour de bon : ``{}``, jamais une exception."""
    assert yaml_io.load_yaml_mapping_or_empty(tmp_path / "absent.yaml") == {}
    listing = tmp_path / "liste.yaml"
    listing.write_text("- un\n- deux\n", encoding="utf-8")
    assert yaml_io.load_yaml_mapping_or_empty(listing) == {}
    torn = tmp_path / "torn.yaml"
    torn.write_text(TORN_BOARD, encoding="utf-8")
    monkeypatch.setattr(time, "sleep", lambda _delay: None)
    assert yaml_io.load_yaml_mapping_or_empty(torn, attempts=2, delay=0.0) == {}


def test_the_board_readers_retry_a_torn_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le lecteur du cockpit (``list_board_tasks``) ne lève pas sur une lecture tombée en pleine écriture."""
    dest = _board_path(tmp_path)
    write_board(dest, _board("valide"))
    dest.write_text(TORN_BOARD, encoding="utf-8")
    healed: list[bool] = []

    def heal(_delay: float) -> None:
        if not healed:
            healed.append(True)
            write_board(dest, _board("valide"))

    monkeypatch.setattr(time, "sleep", heal)
    assert len(list_board_tasks(tmp_path)) == TASK_COUNT


def test_the_hook_task_resolution_retries_a_torn_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La résolution de tâche du hook (tick, Stop) lit un board en cours de remplacement sans casser."""
    dest = _board_path(tmp_path)
    board = _board("valide", in_progress="GAO-tmp-0000")
    write_board(dest, board)
    dest.write_text(TORN_BOARD, encoding="utf-8")
    healed: list[bool] = []

    def heal(_delay: float) -> None:
        if not healed:
            healed.append(True)
            write_board(dest, board)

    monkeypatch.setattr(time, "sleep", heal)
    resolved = resolve_active_task(tmp_path, env={}, write_cache=False)
    assert (resolved.task_id, resolved.source) == ("GAO-tmp-0000", "board")


def test_windows_retries_a_denied_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows : un lecteur qui tient le fichier fait échouer ``os.replace`` — on retente."""
    dest = _board_path(tmp_path)
    write_board(dest, _board("ancien"))
    real_replace = os.replace
    calls: list[int] = []

    def denied_twice(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("fichier tenu par un lecteur")
        real_replace(src, dst)

    monkeypatch.setattr(fsio, "_WIN32_REPLACE_DENIED", True)
    monkeypatch.setattr(fsio, "_REPLACE_DELAY_S", 0.0)
    monkeypatch.setattr(os, "replace", denied_twice)
    write_board(dest, _board("nouveau"))
    assert len(calls) == 3
    assert "nouveau" in dest.read_text(encoding="utf-8")


def test_a_denied_replace_is_not_retried_off_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ailleurs qu'avec Windows, un refus d'accès n'est pas un lecteur : il part tout de suite."""
    dest = _board_path(tmp_path)
    write_board(dest, _board("ancien"))
    calls: list[int] = []

    def denied(*_args: object, **_kwargs: object) -> None:
        calls.append(1)
        raise PermissionError("accès refusé")

    monkeypatch.setattr(fsio, "_WIN32_REPLACE_DENIED", False)
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError, match="accès refusé"):
        write_board(dest, _board("nouveau"))
    assert len(calls) == 1
    assert not list(dest.parent.glob(".*.tmp"))


def test_windows_gives_a_clear_error_when_the_replace_keeps_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Après les tentatives, l'erreur dit que le fichier est tenu — et rien n'est laissé à moitié écrit."""
    dest = _board_path(tmp_path)
    write_board(dest, _board("ancien"))
    before = dest.read_bytes()

    def always_denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("fichier tenu")

    monkeypatch.setattr(fsio, "_WIN32_REPLACE_DENIED", True)
    monkeypatch.setattr(fsio, "_REPLACE_TRIES", 3)
    monkeypatch.setattr(fsio, "_REPLACE_DELAY_S", 0.0)
    monkeypatch.setattr(os, "replace", always_denied)
    with pytest.raises(PermissionError, match=r"3 tentatives.*tient le fichier"):
        write_board(dest, _board("nouveau"))
    assert dest.read_bytes() == before
    assert not list(dest.parent.glob(".*.tmp"))
