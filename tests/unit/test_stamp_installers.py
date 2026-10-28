"""scripts/stamp-installers.py : la version épinglée entre dans les deux installeurs."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "stamp-installers.py"


def _load():
    spec = importlib.util.spec_from_file_location("stamp_installers", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_installers_carry_an_empty_pin(tmp_path):
    mod = _load()
    assert mod.main(["--version", "9.8.7", "--out", str(tmp_path)]) == 0
    assert 'PINNED_VERSION="9.8.7"' in (tmp_path / "install.sh").read_text(encoding="utf-8")
    assert '$PinnedVersion = "9.8.7"' in (tmp_path / "install.ps1").read_text(encoding="utf-8")


def test_stamped_script_is_not_stampable_twice(tmp_path):
    mod = _load()
    mod.main(["--version", "1.2.3", "--out", str(tmp_path / "a")])
    root2 = tmp_path / "a"
    assert mod.main(["--version", "1.2.4", "--out", str(tmp_path / "b"), "--root", str(root2)]) == 1


def test_invalid_version_refused(tmp_path):
    assert _load().main(["--version", "latest", "--out", str(tmp_path)]) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="install.sh est un script POSIX : le bash de Windows (WSL) ne voit pas ces chemins ni cet env")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")
def test_unpinned_install_sh_demands_a_version():
    r = subprocess.run(["bash", str(ROOT / "install.sh")], capture_output=True, text=True,
                       encoding="utf-8", env={"PATH": "/usr/bin:/bin"}, check=False)
    assert r.returncode != 0
    assert "--version" in r.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="install.sh est un script POSIX : le bash de Windows (WSL) ne voit pas ces chemins")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")
def test_install_sh_syntax():
    assert subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=False).returncode == 0


def test_output_survives_a_cp1252_console(tmp_path):
    """Windows : la console cp1252 ne sait pas écrire « → » (régression CI)."""
    r = subprocess.run(["python3", "-I", str(SCRIPT), "--version", "1.2.3", "--out", str(tmp_path / "o")],
                       capture_output=True, env={"PYTHONIOENCODING": "cp1252", "PATH": "/usr/bin:/bin"}, check=False)
    assert r.returncode == 0, r.stderr
