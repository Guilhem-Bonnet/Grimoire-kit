"""Les hooks git livrés ne sortent jamais « OK » en silence sur une garde manquante (tri R40, R41).

- ``pre-commit-cc.sh`` : sans ``cc-verify.sh``, dit « CC non installé » sur
  stderr (le commit reste permis : absent par conception) au lieu de sortir 0 muet ;
- ``pre-push.sh`` : le code retour de ``validate --all`` fait foi — un 1 sans
  le mot « error » bloque le push ; un ``grimoire-init.sh`` absent est dit,
  pas transformé en « ✓ OK ».
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("git") is None,
    reason="hooks bash : POSIX + bash + git requis",
)

HOOKS = Path(__file__).resolve().parents[2] / "framework" / "hooks"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "_grimoire").mkdir()
    return tmp_path


def _run(hook: str, repo: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("Grimoire_SKIP_PUSH_CHECK", "GIT_DIR", "GIT_WORK_TREE")}
    # `grimoire` hors PATH : le repli éventuel ne doit pas dépendre du poste.
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if not shutil.which("grimoire", path=p))
    return subprocess.run(
        ["bash", str(HOOKS / hook)], cwd=repo, env=env, capture_output=True, text=True, timeout=60, check=False
    )


def test_pre_commit_sans_cc_le_dit_sur_stderr_et_laisse_passer(repo: Path) -> None:
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
    result = _run("pre-commit-cc.sh", repo)
    assert result.returncode == 0
    assert "CC non installé" in result.stderr


def test_pre_commit_avec_cc_qui_echoue_bloque(repo: Path) -> None:
    cc = repo / "_grimoire" / "kit" / "framework" / "cc-verify.sh"
    cc.parent.mkdir(parents=True)
    cc.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
    result = _run("pre-commit-cc.sh", repo)
    assert result.returncode == 1
    assert "CC non installé" not in result.stderr


def _with_dna(repo: Path, init_body: str | None) -> None:
    (repo / "archetypes").mkdir()
    (repo / "archetypes" / "a.dna.yaml").write_text("id: a\n", encoding="utf-8")
    if init_body is not None:
        (repo / "grimoire-init.sh").write_text(f"#!/usr/bin/env bash\n{init_body}\n", encoding="utf-8")


def test_pre_push_validate_code_1_sans_le_mot_error_bloque(repo: Path) -> None:
    _with_dna(repo, "echo 'rien de lisible'\nexit 1")
    result = _run("pre-push.sh", repo)
    assert result.returncode == 1, result.stdout
    assert "code retour 1" in result.stdout


def test_pre_push_validate_code_0_passe(repo: Path) -> None:
    _with_dna(repo, "echo 'tout va bien'\nexit 0")
    result = _run("pre-push.sh", repo)
    assert result.returncode == 0, result.stdout
    assert "DNA validate — 1 fichiers OK" in result.stdout


def test_pre_push_validate_mot_error_avec_code_0_bloque_toujours(repo: Path) -> None:
    _with_dna(repo, "echo 'error: archetype casse'\nexit 0")
    assert _run("pre-push.sh", repo).returncode == 1


def test_pre_push_sans_init_script_le_dit_au_lieu_d_afficher_ok(repo: Path) -> None:
    _with_dna(repo, None)
    result = _run("pre-push.sh", repo)
    assert result.returncode == 0
    assert "DNA validate sautée" in result.stdout
    assert "fichiers OK" not in result.stdout
