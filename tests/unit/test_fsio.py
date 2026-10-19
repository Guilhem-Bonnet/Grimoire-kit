"""W1-00 : verrou portable et écriture atomique uniques (``grimoire.core.fsio``)."""

from __future__ import annotations

import ast
import json
import os
import re
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
    counter.write_text("0", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
    procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(counter), "25", mode], env=env) for _ in range(2)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    assert counter.read_text(encoding="utf-8") == "50"  # un seul écrasement perdu suffirait à le faire échouer


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
    lock.write_text("", encoding="utf-8")
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
    target.write_text("ancien", encoding="utf-8")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("panne entre le temporaire et le replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="panne"):
        fsio.atomic_write(target, "nouveau")
    assert target.read_text(encoding="utf-8") == "ancien"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]  # pas de temporaire orphelin


def test_failure_during_write_keeps_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f.txt"
    target.write_text("ancien", encoding="utf-8")

    def boom(_fd: int) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disque"):
        fsio.atomic_write(target, "nouveau")
    assert target.read_text(encoding="utf-8") == "ancien"


# --- revue W1-00 : casse atomique, mode, délai ------------------------------

_RACE_WORKER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from grimoire.core import fsio
    fsio._fcntl = None
    target, role, log = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    lock = fsio._lock_path(target)
    if role == "b":  # point d'injection : fenetre entre la lecture du verrou et la casse
        orig = Path.stat
        def slow_stat(self, *a, **k):
            r = orig(self, *a, **k)
            if self.name == lock.name:  # le seul stat initial, pas celui du verrou renomme
                time.sleep(0.6)
            return r
        Path.stat = slow_stat
    else:
        time.sleep(0.15)
    with fsio.locked(target, timeout=20):
        t0 = time.time()
        try:
            own = lock.read_text(encoding="utf-8")
        except OSError:
            own = None
        time.sleep(1.5 if role == "a" else 0.05)
        try:
            kept = own is not None and lock.read_text(encoding="utf-8") == own
        except OSError:
            kept = False
        t1 = time.time()
    with log.open("a") as fh:
        fh.write(f"{role} {t0} {t1} {int(kept)}\\n")
    """
)


def test_breaking_a_stale_lock_never_steals_a_fresh_one(tmp_path: Path) -> None:
    """W1-00-S1 : B juge le verrou abandonne, A le casse et le reprend, B ne doit pas supprimer le neuf."""
    target = tmp_path / "t"
    lock = fsio._lock_path(target)
    lock.write_text("", encoding="utf-8")
    os.utime(lock, (1, 1))
    log = tmp_path / "log"
    env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
    procs = [
        subprocess.Popen([sys.executable, "-c", _RACE_WORKER, str(target), role, str(log)], env=env)
        for role in ("b", "a")
    ]
    # A d'abord : un zombie non attendu passerait pour un processus vivant au test de pid
    assert [p.wait(timeout=60) for p in reversed(procs)] == [0, 0]
    rows = {r: (float(t0), float(t1), k) for r, t0, t1, k in (ln.split() for ln in log.read_text(encoding="utf-8").splitlines())}
    assert set(rows) == {"a", "b"}
    # le vol lui-meme : le verrou de A a disparu (ou change de jeton) pendant sa section critique
    assert rows["a"][2] == "1", f"le verrou de A a ete vole pendant sa section critique : {rows}"
    spans = sorted((t0, t1) for t0, t1, _k in rows.values())
    assert spans[0][1] <= spans[1][0], f"sections critiques chevauchees : {spans}"


def test_release_does_not_remove_a_lock_that_is_not_ours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    target = tmp_path / "f"
    lock = fsio._lock_path(target)
    with fsio.locked(target):
        lock.unlink()
        lock.write_text(f"{os.getpid()}:autre-jeton", encoding="utf-8")  # verrou casse puis repris par un autre
    assert lock.read_text(encoding="utf-8") == f"{os.getpid()}:autre-jeton"


@pytest.mark.skipif(os.name != "posix", reason="bits de mode POSIX")
def test_atomic_write_keeps_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")
    target.chmod(0o644)
    fsio.atomic_write(target, "y")
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name != "posix", reason="bits de mode POSIX")
@pytest.mark.parametrize(("mask", "expected"), [(0o027, 0o640), (0o077, 0o600), (0o002, 0o664)])
def test_atomic_write_new_file_follows_current_umask(tmp_path: Path, mask: int, expected: int) -> None:
    """W1-00-T1-5 : l'umask est lu a chaque ecriture (un resserrement apres l'import compte)."""
    old = os.umask(mask)
    try:
        target = tmp_path / "neuf.txt"
        fsio.atomic_write(target, "y")
    finally:
        os.umask(old)
    assert stat.S_IMODE(target.stat().st_mode) == expected


_UMASK_PROBE = textwrap.dedent(
    """
    import json, os, sys, tempfile
    from pathlib import Path
    calls = []
    real = os.umask
    def spy(mask):
        calls.append(mask)
        return real(mask)
    os.umask = spy  # installe AVANT l'import : un umask lu au chargement du module serait vu
    from grimoire.core import fsio
    with tempfile.TemporaryDirectory() as d:
        fsio.atomic_write(Path(d) / "f", "y")
    print(json.dumps(calls))
    """
)


def test_atomic_write_never_touches_the_process_umask() -> None:
    """Lire l'umask par ``os.umask(0)`` ouvre une fenetre ou un autre thread cree en 0666 : on ne le fait jamais.

    Sous-processus : l'espion doit etre en place avant l'import de ``fsio`` (W1-00-T2-4), sinon un umask
    lu au chargement du module (version 2ac66b03 : ``_UMASK = _read_umask()``) passerait inapercu.
    """
    env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
    out = subprocess.run(
        [sys.executable, "-c", _UMASK_PROBE], env=env, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == []


def _unlink_raising(monkeypatch: pytest.MonkeyPatch, lock_name: str, times: int) -> list[int]:
    """Fait lever ``PermissionError`` (partage Windows) aux *times* premiers ``unlink`` du verrou."""
    calls = [0]
    real = Path.unlink

    def flaky(self: Path, *a: object, **k: object) -> None:
        if self.name == lock_name:
            calls[0] += 1
            if calls[0] <= times:
                raise PermissionError("violation de partage")
        real(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", flaky)
    return calls


def test_release_retries_a_transient_unlink_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-00-T1-3 : un unlink refuse un instant (lecteur concurrent sous Windows) ne laisse pas de verrou orphelin."""
    monkeypatch.setattr(fsio, "_fcntl", None)
    monkeypatch.setattr(fsio, "_RELEASE_DELAY_S", 0.0)
    target = tmp_path / "w3"
    lock = fsio._lock_path(target)
    calls = _unlink_raising(monkeypatch, lock.name, times=1)
    with fsio.locked(target):
        pass
    assert calls[0] == 2
    assert not lock.exists()


def test_release_failure_is_raised_not_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    monkeypatch.setattr(fsio, "_RELEASE_DELAY_S", 0.0)
    target = tmp_path / "w3"
    lock = fsio._lock_path(target)
    _unlink_raising(monkeypatch, lock.name, times=10_000)
    with pytest.raises(PermissionError), fsio.locked(target):
        pass
    # l'echec est rendu visible, il ne supprime pas le verrou : celui-ci reste jusqu'a sa peremption (60 s)
    assert lock.exists()


def test_release_failure_does_not_mask_the_body_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-00-T2-2 : si le corps leve, l'appelant recoit cette erreur-la ; l'echec de liberation y est attache."""
    monkeypatch.setattr(fsio, "_fcntl", None)
    monkeypatch.setattr(fsio, "_RELEASE_DELAY_S", 0.0)
    target = tmp_path / "w3"
    lock = fsio._lock_path(target)
    _unlink_raising(monkeypatch, lock.name, times=10_000)
    with pytest.raises(ValueError, match="corps") as excinfo, fsio.locked(target):
        raise ValueError("corps")
    assert any("libération" in note and str(lock) in note for note in excinfo.value.__notes__)
    assert lock.exists()


def test_fresh_lock_is_not_read_where_liveness_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-00-T1-3 : sans test de pid (Windows), attendre un verrou frais ne le relit pas (lecteur = unlink refuse)."""
    monkeypatch.setattr(fsio, "_fcntl", None)
    monkeypatch.setattr(fsio, "_PID_LIVENESS", False)
    target = tmp_path / "f"
    lock = fsio._lock_path(target)
    reads: list[str] = []
    real = Path.read_text

    def spy(self: Path, *a: object, **k: object) -> str:
        reads.append(self.name)
        return real(self, *a, **k)  # type: ignore[arg-type]

    with fsio.locked(target):
        monkeypatch.setattr(Path, "read_text", spy)
        with pytest.raises(TimeoutError), fsio.locked(target, timeout=0.15):
            pass
        monkeypatch.setattr(Path, "read_text", real)
    assert lock.name not in reads


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
    """Configuration SIMULEE (W1-00-T2-3) : repli force par ``_fcntl = None`` sous POSIX.

    Dans la realite le repli n'est pris que sous Windows, ou ``os.name`` vaut « nt » : pas de test de pid,
    seule la peremption par l'age (60 s) s'applique. Ce test ne couvre que le POSIX sans ``fcntl``.
    """
    monkeypatch.setattr(fsio, "_fcntl", None)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    target = tmp_path / "f"
    fsio._lock_path(target).write_text(f"{dead.pid}:jeton", encoding="utf-8")  # frais (mtime = maintenant), proprietaire mort
    t0 = time.monotonic()
    with fsio.locked(target, timeout=5):
        pass
    assert time.monotonic() - t0 < 2


def test_fallback_keeps_a_fresh_lock_of_a_live_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsio, "_fcntl", None)
    target = tmp_path / "f"
    fsio._lock_path(target).write_text(f"{os.getpid()}:jeton", encoding="utf-8")
    with pytest.raises(TimeoutError), fsio.locked(target, timeout=0.1):
        pass


# --- refus d'acces a l'acquisition et melange des deux branches (W1-00-T3-1, W1-00-T3-2) -----


@pytest.mark.skipif(os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="chmod 555 sans effet")
def test_fallback_raises_permission_error_at_once_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-00-T3-1 : un dossier en lecture seule n'est pas « un verrou occupe » ; la cause n'est pas attendue puis masquee."""
    monkeypatch.setattr(fsio, "_fcntl", None)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        t0 = time.monotonic()
        with pytest.raises(PermissionError), fsio.locked(ro / "f", timeout=5):
            pass
        assert time.monotonic() - t0 < 2
    finally:
        ro.chmod(0o755)


def test_fallback_timeout_keeps_the_permission_error_as_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-00-T3-1 : ou le refus peut etre transitoire (Windows), le delai expire mais la cause reste attachee."""
    monkeypatch.setattr(fsio, "_fcntl", None)
    monkeypatch.setattr(fsio, "_PID_LIVENESS", False)
    real = os.open

    def deny(path: object, *a: object, **k: object) -> int:
        if str(path).endswith(".lock") or str(path).endswith(".xlock"):
            raise PermissionError(13, "acces refuse")
        return real(path, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", deny)
    with pytest.raises(TimeoutError) as excinfo, fsio.locked(tmp_path / "f", timeout=0.1):
        pass
    assert isinstance(excinfo.value.__cause__, PermissionError)


@pytest.mark.parametrize("age", [0.0, 120.0])
def test_fallback_never_touches_the_flock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age: float) -> None:
    """W1-00-T3-2 : le fichier de ``flock`` (vide, persistant) n'est ni attendu ni casse par le repli."""
    target = tmp_path / "f"
    flock_file = fsio._flock_path(target)
    flock_file.write_text("", encoding="utf-8")
    if age:
        old = time.time() - age
        os.utime(flock_file, (old, old))
    inode = flock_file.stat().st_ino
    monkeypatch.setattr(fsio, "_fcntl", None)
    with fsio.locked(target, timeout=0.5):
        pass
    assert flock_file.exists()
    assert flock_file.stat().st_ino == inode
    assert fsio._lock_path(target) != flock_file


def test_flock_and_fallback_branches_do_not_exclude_each_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-00-T3-2 : limite figee. Les deux branches n'ont pas le meme verrou : pas d'exclusion entre hotes mixtes."""
    if fsio._fcntl is None:
        pytest.skip("fcntl indisponible")
    target = tmp_path / "f"
    with fsio.locked(target), monkeypatch.context() as mp:
        mp.setattr(fsio, "_fcntl", None)
        with fsio.locked(target, timeout=0.5):  # ne leve pas : les deux sections coexistent
            pass


# --- restitution d'un verrou pris par erreur : limites figees (W1-00-T2-1) -----


def _stale_lock_stolen_by_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, at_link: object
) -> tuple[Path, Path, str, object, bool]:
    """B juge X abandonne ; entre son ``stat`` et son ``rename``, C casse X et A prend un verrou neuf.

    Rejoue B (``_break_if_abandoned``) : son ``rename`` emporte le verrou de A. *at_link* s'execute
    dans la fenetre entre ce ``rename`` et la restitution par ``os.link``. Rend la cible, le verrou,
    le jeton de A, le gestionnaire de A et le retour de B.
    """
    monkeypatch.setattr(fsio, "_fcntl", None)
    target = tmp_path / "t"
    lock = fsio._lock_path(target)
    lock.write_text("999999:ancien", encoding="utf-8")
    old = time.time() - fsio._STALE_AFTER_S - 60
    os.utime(lock, (old, old))
    holder: dict[str, object] = {}
    real_rename, real_link = Path.rename, os.link

    def rename(self: Path, dst: object) -> Path:
        if self == lock and "cm" not in holder:
            lock.unlink()  # C casse X
            cm = fsio.locked(target)
            cm.__enter__()  # A prend un verrou neuf
            holder["cm"] = cm
            holder["token"] = lock.read_text(encoding="utf-8")
        return real_rename(self, dst)  # type: ignore[arg-type]

    def link(src: object, dst: object, **kw: object) -> None:
        at_link(holder, dst)  # type: ignore[operator]
        real_link(src, dst, **kw)  # type: ignore[arg-type]

    with monkeypatch.context() as mp:
        mp.setattr(Path, "rename", rename)
        mp.setattr(os, "link", link)
        broke = fsio._break_if_abandoned(lock)
    assert broke is False
    return target, lock, str(holder["token"]), holder["cm"], broke


def test_restitution_can_leave_an_orphan_lock_when_the_holder_already_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Limite connue : A sort de sa section pendant la fenetre, la restitution recree son verrou.

    Pas de primitive atomique « supprimer si c'est ce fichier » : le verrou rendu n'a plus de
    proprietaire. Il bloque les suivants jusqu'a sa peremption (60 s d'age, sans test de pid sous Windows),
    donc un ``TimeoutError`` pour un delai plus court.
    """

    def a_leaves(holder: dict[str, object], _dst: object) -> None:
        holder["cm"].__exit__(None, None, None)  # type: ignore[attr-defined]

    target, lock, a_id, _cm, _broke = _stale_lock_stolen_by_rename(tmp_path, monkeypatch, at_link=a_leaves)
    assert lock.read_text(encoding="utf-8") == a_id  # orphelin : A est deja sorti
    monkeypatch.setattr(fsio, "_PID_LIVENESS", False)  # Windows : seule l'age compte
    with pytest.raises(TimeoutError), fsio.locked(target, timeout=0.2):
        pass
    assert lock.exists()


def test_restitution_can_lose_exclusion_when_a_third_party_recreates_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Limite connue : C cree le verrou dans la fenetre, ``os.link`` echoue sans bruit.

    Le verrou porte alors le jeton de C pendant que A est encore dans sa section : deux detenteurs.
    """

    def c_takes_it(_holder: dict[str, object], dst: object) -> None:
        fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # type: ignore[arg-type]
        os.write(fd, b"C:jeton")
        os.close(fd)

    _target, lock, a_id, _cm, _broke = _stale_lock_stolen_by_rename(tmp_path, monkeypatch, at_link=c_takes_it)
    assert lock.read_text(encoding="utf-8") == "C:jeton"
    assert a_id != "C:jeton"  # A croit encore tenir le verrou


# --- architecture : un seul helper de verrou et d'ecriture atomique ---------

#: Anciens helpers de verrou, a migrer vers fsio (chantiers suivants) : la liste ne fait que rétrécir.
_LEGACY_LOCKERS: set[str] = set()
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
    "policies/session_state.py",
    "tools/features.py",
    "tools/stigmergy.py",
}
_LOCK_MODULES = {"fcntl", "msvcrt", "filelock", "portalocker", "fasteners"}
_LOCK_CALLS = {"flock", "lockf", "locking"}
_DYNAMIC_IMPORTS = {"import_module", "__import__"}
_EXCLUSIVE_MODE = re.compile(r"[rwabt+]*x[rwabt+]*")  # mode d'open() qui crée en exclusif
_OS_MOVES = {"replace", "rename"}


def _call_name(node: ast.Call) -> str:
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _exclusive_open(node: ast.Call) -> bool:
    """``open(f, 'x')``, ``io.open``, ``Path.open('x')`` ou ``mode='x...'`` : création exclusive, donc un verrou."""
    if _call_name(node) != "open":
        return False
    candidates = [kw.value for kw in node.keywords if kw.arg == "mode"] + node.args[1:2]
    if isinstance(node.func, ast.Attribute):  # Path.open(mode) : le mode vient en premier
        candidates += node.args[:1]
    return any(
        isinstance(c, ast.Constant) and isinstance(c.value, str) and _EXCLUSIVE_MODE.fullmatch(c.value)
        for c in candidates
    )


def _exclusive_touch(node: ast.Call) -> bool:
    """``Path.touch(exist_ok=False)`` (aussi en second argument positionnel) échoue si le fichier existe : un verrou."""
    if _call_name(node) != "touch":
        return False
    flags = [kw.value for kw in node.keywords if kw.arg == "exist_ok"] + node.args[1:2]
    return any(isinstance(f, ast.Constant) and f.value is False for f in flags)


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
            if not hit and isinstance(node, ast.Call):
                hit = _exclusive_open(node) or _exclusive_touch(node)
            if hit:
                found.add(rel)
    return found


def _atomic_writers(root: Path = SRC) -> set[str]:
    """Fichiers qui écrivent « temporaire puis replace » : ``mkstemp``, ``os.replace/rename``, ``Path.replace(x)``,
    ``Path.rename(x)`` ou l'import direct de ``replace``/``rename`` depuis ``os``."""
    found: set[str] = set()
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.module == "os" and any(a.name in _OS_MOVES for a in node.names):
                    found.add(rel)
                continue
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            one_arg_move = name in _OS_MOVES and len(node.args) == 1 and not node.keywords
            os_move = (
                name in _OS_MOVES
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            )
            if name == "mkstemp" or one_arg_move or os_move:
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
        "open('x.lock', 'x')\n",
        "open('x.lock', mode='xb')\n",
        "from pathlib import Path\nPath('x.lock').open('x')\n",
        "import io\nio.open('x.lock', 'x+')\n",
        "from pathlib import Path\nPath('t').touch(exist_ok=False)\n",
        "from pathlib import Path\nPath('t').touch(0o644, False)\n",
    ],
)
def test_lock_guard_detects_booby_trapped_modules(tmp_path: Path, body: str) -> None:
    (tmp_path / "rogue.py").write_text(body, encoding="utf-8")
    assert _lock_users(tmp_path) == {"rogue.py"}


@pytest.mark.parametrize(
    "body",
    [
        "open('f.txt', 'w')\n",
        "open('f.txt')\n",
        "from pathlib import Path\nPath('t').touch()\nPath('t').touch(exist_ok=True)\n",
        "from pathlib import Path\nPath('t').open('r')\nPath('t').open(encoding='utf-8')\n",
    ],
)
def test_lock_guard_ignores_ordinary_file_access(tmp_path: Path, body: str) -> None:
    (tmp_path / "ok.py").write_text(body, encoding="utf-8")
    assert _lock_users(tmp_path) == set()


@pytest.mark.parametrize(
    "body",
    [
        "import tempfile\ntempfile.mkstemp()\n",
        "import os\nos.replace('a', 'b')\n",
        "from pathlib import Path\nPath('a').replace(Path('b'))\n",
        "from pathlib import Path\nPath('a').rename(Path('b'))\n",
        "import os\nos.rename('a', 'b')\n",
        "from os import replace\nreplace('a', 'b')\n",
        "from os import rename as mv\nmv('a', 'b')\n",
    ],
)
def test_atomic_guard_detects_booby_trapped_modules(tmp_path: Path, body: str) -> None:
    (tmp_path / "rogue.py").write_text(body, encoding="utf-8")
    assert _atomic_writers(tmp_path) == {"rogue.py"}


def test_atomic_guard_ignores_lookalikes(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("'a'.replace('a', 'b')\nimport os\nos.remove('x')\n", encoding="utf-8")
    assert _atomic_writers(tmp_path) == set()
