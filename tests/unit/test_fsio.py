"""W1-00 : verrou portable et écriture atomique uniques (``grimoire.core.fsio``)."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from grimoire.core import fsio

SRC = Path(fsio.__file__).resolve().parents[1]  # src/grimoire

_WORKER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from grimoire.core import fsio
    if sys.argv[3] == "nofcntl":
        fsio._fcntl = None
    counter = Path(sys.argv[1])
    for _ in range(int(sys.argv[2])):
        with fsio.locked(counter):
            n = int(counter.read_text() or "0")
            time.sleep(0.002)  # élargit la fenêtre de course
            counter.write_text(str(n + 1))
    """
)


@pytest.mark.parametrize("mode", ["fcntl", "nofcntl"])
def test_two_processes_are_mutually_exclusive(tmp_path: Path, mode: str) -> None:
    if mode == "fcntl" and fsio._fcntl is None:
        pytest.skip("fcntl indisponible")
    counter = tmp_path / "counter"
    counter.write_text("0")
    env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
    procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(counter), "25", mode], env=env) for _ in range(2)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    assert counter.read_text() == "50"  # un seul écrasement perdu suffirait à le faire échouer


def test_fallback_lock_times_out_and_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    target = tmp_path / "f"
    with fsio.locked(target), pytest.raises(TimeoutError), fsio.locked(target, timeout=0.05):
        pass
    assert not fsio._lock_path(target).exists()
    with fsio.locked(target, timeout=0.05):  # libéré : se reprend
        pass


def test_fallback_lock_breaks_a_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    target = tmp_path / "f"
    lock = fsio._lock_path(target)
    lock.write_text("")
    old = lock.stat().st_mtime - fsio._STALE_AFTER_S - 5
    os.utime(lock, (old, old))
    with fsio.locked(target, timeout=0.5):
        pass


def test_atomic_write_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "f.txt"
    fsio.atomic_write(target, "un")
    fsio.atomic_write(target, b"deux")
    assert target.read_bytes() == b"deux"
    assert [p.name for p in target.parent.iterdir()] == ["f.txt"]


def test_failure_before_replace_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f.txt"
    target.write_text("ancien")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("panne entre le temporaire et le replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="panne"):
        fsio.atomic_write(target, "nouveau")
    assert target.read_text() == "ancien"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]  # pas de temporaire orphelin


def test_failure_during_write_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f.txt"
    target.write_text("ancien")

    def boom(_fd: int) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disque"):
        fsio.atomic_write(target, "nouveau")
    assert target.read_text() == "ancien"


# --- architecture : un seul helper de verrou dans src/grimoire -------------

#: Anciens helpers, à migrer vers fsio (chantiers suivants) : la liste ne fait que rétrécir.
_LEGACY_LOCKERS = {"missions/ledger.py", "tools/stigmergy.py"}
_LOCK_MODULES = {"fcntl", "msvcrt"}
_LOCK_CALLS = {"flock", "lockf", "locking"}


def _lock_users() -> set[str]:
    found: set[str] = set()
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: set[str] = set()
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split(".")[0]}
            elif isinstance(node, ast.Attribute) and node.attr in _LOCK_CALLS:
                names = _LOCK_MODULES
            if names & _LOCK_MODULES:
                found.add(rel)
    return found


def test_no_other_lock_helper_in_src() -> None:
    users = _lock_users()
    assert "core/fsio.py" in users
    rogue = users - {"core/fsio.py"} - _LEGACY_LOCKERS
    assert not rogue, f"helper de verrou hors core/fsio.py : {sorted(rogue)} — utiliser fsio.locked"
    stale = _LEGACY_LOCKERS - users
    assert not stale, f"migrés : retirer de _LEGACY_LOCKERS {sorted(stale)}"
