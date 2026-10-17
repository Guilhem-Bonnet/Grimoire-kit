"""Branches propres à Windows de D-10, exercées sous POSIX en forçant ``sys.platform``.

Ces lignes ne s'exécutent réellement que sur la matrice Windows de la CI ; sans
ces tests elles n'étaient couvertes par aucun job Linux (codecov/patch).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.flows import dispatch_executor as de
from grimoire.policies.temporal import _is_repair_exempt
from grimoire.tools import stigmergy


def _contract(runs: tuple[object, ...] = (), evidence: tuple[object, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(acceptance_runs=runs, acceptance_evidence=evidence)


class TestQuote:
    def test_posix_utilise_shlex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert de._quote("a b'c") == "'a b'\"'\"'c'"

    def test_windows_utilise_la_convention_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        assert de._quote("a b") == '"a b"'
        assert de._quote("simple") == "simple"
        assert "'" not in de._quote("C:\\dir with space\\x")

    def test_verify_command_windows_sans_apostrophe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        cmd = de._verify_command("C:\\Program Files\\py.exe", Path("C:/bp dir/bp.yaml"), "n1", Path("C:/r dir/r.json"))
        assert "'" not in cmd
        assert cmd.startswith('"C:\\Program Files\\py.exe" -m grimoire.flows.dispatch_executor --verify')


class TestAcceptanceChecksWindows:
    def test_path_exists_n_utilise_pas_test(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        present = tmp_path / "présent fichier.txt"
        present.write_text("x", encoding="utf-8")
        contract = _contract(evidence=(SimpleNamespace(kind="path_exists", value=str(present)),))
        cmds, exits, stdout, timeouts = de._acceptance_checks(contract)  # type: ignore[arg-type]
        assert len(cmds) == 1 and not cmds[0].startswith("test ")
        assert (exits, stdout, timeouts) == ((0,), (None,), (None,))
        # La sonde est réellement exécutable et rend le bon code de sortie.
        assert subprocess.run(cmds[0], shell=True, check=False).returncode == 0  # noqa: S602
        missing = _contract(evidence=(SimpleNamespace(kind="path_exists", value=str(tmp_path / "absent")),))
        cmd_missing = de._acceptance_checks(missing)[0][0]  # type: ignore[arg-type]
        assert subprocess.run(cmd_missing, shell=True, check=False).returncode == 1  # noqa: S602

    def test_cwd_et_pytest_quotes_a_la_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        run = SimpleNamespace(cwd="sub dir", raw="echo hi", expect_exit=0, expect_stdout_contains=None, timeout_s=None)
        test = SimpleNamespace(kind="test", value="tests/test_a.py::test_x")
        cmds = de._acceptance_checks(_contract(runs=(run,), evidence=(test,)))[0]  # type: ignore[arg-type]
        assert cmds[0] == 'cd "sub dir" && echo hi'
        assert "'" not in cmds[1] and "tests/test_a.py::test_x" in cmds[1]

    def test_posix_garde_test_e(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        contract = _contract(evidence=(SimpleNamespace(kind="path_exists", value="a b"),))
        assert de._acceptance_checks(contract)[0] == ("test -e 'a b'",)  # type: ignore[arg-type]


def test_exemption_de_reparation_accepte_un_chemin_windows() -> None:
    posix = "/repo/_grimoire/standard/policies.yaml"
    windows = "C:\\repo\\_grimoire\\standard\\policies.yaml"
    assert _is_repair_exempt(True, posix) is True
    assert _is_repair_exempt(True, windows) is True
    assert _is_repair_exempt(True, "C:\\repo\\src\\app.py") is False


class TestBoardLock:
    @pytest.mark.parametrize("exc", [TimeoutError("délai"), PermissionError("refusé")])
    def test_verrou_refuse_ne_bloque_pas_le_hook(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exc: Exception) -> None:
        def boom(_path: object) -> None:
            raise exc

        monkeypatch.setattr(stigmergy.fsio, "locked", boom)
        ran = False
        with stigmergy._board_lock(tmp_path):
            ran = True
        assert ran

    def test_verrou_obtenu_puis_libere(self, tmp_path: Path) -> None:
        with stigmergy._board_lock(tmp_path):
            pass
        with stigmergy._board_lock(tmp_path):  # réacquérable : le premier a été relâché
            pass
