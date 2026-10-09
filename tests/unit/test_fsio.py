"""W1-00 : verrou portable et écriture atomique uniques (``grimoire.core.fsio``)."""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
import textwrap
import time
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


# --- revue W1-00 : casse atomique, mode, délai ------------------------------

_RACE_WORKER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from grimoire.core import fsio
    fsio._fcntl = None
    target, role, log = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    if role == "b":  # point d'injection : fenetre entre la detection et la suppression
        orig = Path.stat
        def slow_stat(self, *a, **k):
            r = orig(self, *a, **k)
            if self.name.endswith(".lock") or ".lock." in self.name:
                time.sleep(0.5)
            return r
        Path.stat = slow_stat
    else:
        time.sleep(0.15)
    with fsio.locked(target, timeout=20):
        t0 = time.time()
        time.sleep(0.8 if role == "a" else 0.05)
        t1 = time.time()
    with log.open("a") as fh:
        fh.write(f"{role} {t0} {t1}\\n")
    """
)


def test_breaking_a_stale_lock_never_steals_a_fresh_one(tmp_path: Path) -> None:
    """W1-00-S1 : B juge le verrou abandonne, A le casse et le reprend, B ne doit pas supprimer le neuf."""
    target = tmp_path / "t"
    lock = fsio._lock_path(target)
    lock.write_text("")
    os.utime(lock, (1, 1))
    log = tmp_path / "log"
    env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
    procs = [
        subprocess.Popen([sys.executable, "-c", _RACE_WORKER, str(target), role, str(log)], env=env)
        for role in ("b", "a")
    ]
    # A d'abord : un zombie non attendu passerait pour un processus vivant au test de pid
    assert [p.wait(timeout=60) for p in reversed(procs)] == [0, 0]
    spans = sorted((float(t0), float(t1)) for _r, t0, t1 in (ln.split() for ln in log.read_text().splitlines()))
    assert len(spans) == 2
    assert spans[0][1] <= spans[1][0], f"sections critiques chevauchees : {spans}"


def test_release_does_not_remove_a_lock_that_is_not_ours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    target = tmp_path / "f"
    lock = fsio._lock_path(target)
    with fsio.locked(target):
        lock.unlink()
        lock.write_text(f"{os.getpid()}:autre-jeton")  # verrou casse puis repris par un autre
    assert lock.read_text() == f"{os.getpid()}:autre-jeton"


def test_atomic_write_keeps_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("x")
    target.chmod(0o644)
    fsio.atomic_write(target, "y")
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_atomic_write_new_file_follows_umask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_UMASK", 0o027)
    target = tmp_path / "neuf.txt"
    fsio.atomic_write(target, "y")
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


_HOLDER = textwrap.dedent(
    """
    import sys, time
    from grimoire.core import fsio
    with fsio.locked(sys.argv[1]):
        print("ready", flush=True)
        time.sleep(float(sys.argv[2]))
    """
)


@pytest.mark.skipif(fsio._fcntl is None, reason="fcntl indisponible")
def test_flock_branch_honours_timeout(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(tmp_path / "f"), "3"],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        t0 = time.monotonic()
        with pytest.raises(TimeoutError), fsio.locked(tmp_path / "f", timeout=0.2):
            pass
        assert time.monotonic() - t0 < 1.5
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.skipif(os.name != "posix", reason="liveness par pid POSIX")
def test_fallback_breaks_a_fresh_lock_of_a_dead_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    target = tmp_path / "f"
    fsio._lock_path(target).write_text(f"{dead.pid}:jeton")  # frais (mtime = maintenant), proprietaire mort
    t0 = time.monotonic()
    with fsio.locked(target, timeout=5):
        pass
    assert time.monotonic() - t0 < 2


def test_fallback_keeps_a_fresh_lock_of_a_live_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    target = tmp_path / "f"
    fsio._lock_path(target).write_text(f"{os.getpid()}:jeton")
    with pytest.raises(TimeoutError), fsio.locked(target, timeout=0.1):
        pass


# --- architecture : un seul helper de verrou et d'ecriture atomique ---------

#: Anciens helpers de verrou, a migrer vers fsio (chantiers suivants) : la liste ne fait que rétrécir.
_LEGACY_LOCKERS = {"missions/ledger.py", "tools/stigmergy.py"}
#: Creations exclusives legitimes hors verrou : la liste ne fait que rétrécir (vide aujourd'hui).
_EXCL_ALLOWED: set[str] = set()
#: Copies du motif temporaire + replace a migrer vers fsio.atomic_write : la liste ne fait que rétrécir.
_LEGACY_ATOMIC_WRITERS = {
    "core/standard_state.py",
    "hosts/decisions/calibration.py",
    "hosts/decisions/done_gate.py",
    "hosts/decisions/repetition_guard.py",
    "hosts/decisions/session_memory.py",
    "memory/backends/local.py",
    "missions/ledger.py",
    "policies/session_state.py",
    "tools/features.py",
    "tools/stigmergy.py",
}
_LOCK_MODULES = {"fcntl", "msvcrt", "filelock", "portalocker", "fasteners"}
_LOCK_CALLS = {"flock", "lockf", "locking"}
_DYNAMIC_IMPORTS = {"import_module", "__import__"}


def _call_name(node: ast.Call) -> str:
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _lock_users(root: Path = SRC) -> set[str]:
    found: set[str] = set()
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            hit = False
            if isinstance(node, ast.Import):
                hit = any(a.name.split(".")[0] in _LOCK_MODULES for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                hit = bool(node.module and node.module.split(".")[0] in _LOCK_MODULES) or any(
                    a.name == "O_EXCL" for a in node.names
                )
            elif isinstance(node, ast.Attribute):
                hit = node.attr in _LOCK_CALLS or node.attr == "O_EXCL"
            elif isinstance(node, ast.Name):
                hit = node.id == "O_EXCL"
            elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC_IMPORTS and node.args:
                arg = node.args[0]
                hit = isinstance(arg, ast.Constant) and str(arg.value).split(".")[0] in _LOCK_MODULES
            if hit:
                found.add(rel)
    return found


def _atomic_writers(root: Path = SRC) -> set[str]:
    """Fichiers qui écrivent « temporaire puis replace » : ``mkstemp``, ``os.replace`` ou ``Path.replace(x)``."""
    found: set[str] = set()
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            one_arg_replace = name == "replace" and len(node.args) == 1 and not node.keywords
            if (
                name == "mkstemp"
                or one_arg_replace
                or (
                    name == "replace"
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                )
            ):
                found.add(rel)
    return found


def test_no_other_lock_helper_in_src() -> None:
    users = _lock_users()
    assert "core/fsio.py" in users
    rogue = users - {"core/fsio.py"} - _LEGACY_LOCKERS - _EXCL_ALLOWED
    assert not rogue, f"helper de verrou hors core/fsio.py : {sorted(rogue)} — utiliser fsio.locked"
    stale = _LEGACY_LOCKERS - users
    assert not stale, f"migrés : retirer de _LEGACY_LOCKERS {sorted(stale)}"


def test_no_other_atomic_writer_in_src() -> None:
    writers = _atomic_writers()
    rogue = writers - {"core/fsio.py"} - _LEGACY_ATOMIC_WRITERS
    assert not rogue, f"écriture atomique hors core/fsio.py : {sorted(rogue)} — utiliser fsio.atomic_write"
    stale = _LEGACY_ATOMIC_WRITERS - writers
    assert not stale, f"migrés : retirer de _LEGACY_ATOMIC_WRITERS {sorted(stale)}"


@pytest.mark.parametrize(
    "body",
    [
        "import os\nos.open('x', os.O_CREAT | os.O_EXCL)\n",
        "from os import O_EXCL\n",
        "import importlib\nm = importlib.import_module('fcntl')\ngetattr(m, 'flock')\n",
        "m = __import__('msvcrt')\n",
        "import filelock\n",
        "from portalocker import Lock\n",
        "import fasteners\n",
        "import msvcrt\nmsvcrt.locking(1, 1, 1)\n",
    ],
)
def test_lock_guard_detects_booby_trapped_modules(tmp_path: Path, body: str) -> None:
    (tmp_path / "rogue.py").write_text(body, encoding="utf-8")
    assert _lock_users(tmp_path) == {"rogue.py"}


def test_atomic_guard_detects_booby_trapped_modules(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import tempfile\ntempfile.mkstemp()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import os\nos.replace('a', 'b')\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("from pathlib import Path\nPath('a').replace(Path('b'))\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("'a'.replace('a', 'b')\n", encoding="utf-8")
    assert _atomic_writers(tmp_path) == {"a.py", "b.py", "c.py"}
