"""Comparaison de l'arbre de travail avant et après un dispatch."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grimoire.missions import dispatch_worktree
from grimoire.missions.dispatch_worktree import changed_since, worktree_fingerprint


def _repo(root: Path) -> Path:
    tracked = root / "suivi.txt"
    tracked.write_text("initial\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignore/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "initial"], cwd=root, check=True
    )
    return tracked


def _before(root: Path) -> dict[str, str]:
    before = worktree_fingerprint(root)
    assert before is not None
    return before


def test_arbre_inchange_ne_rend_rien(tmp_path: Path) -> None:
    tracked = _repo(tmp_path)
    tracked.write_text("déjà modifié\n", encoding="utf-8")
    (tmp_path / "deja-nouveau.txt").write_text("x\n", encoding="utf-8")

    assert changed_since(tmp_path, _before(tmp_path)) == ()


def test_nouveau_fichier_est_vu_mais_pas_un_fichier_ignore(tmp_path: Path) -> None:
    _repo(tmp_path)
    before = _before(tmp_path)
    (tmp_path / "ignore").mkdir()
    (tmp_path / "ignore" / "sortie.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "nouveau.txt").write_text("x\n", encoding="utf-8")

    assert changed_since(tmp_path, before) == ("nouveau.txt",)


def test_fichier_indexe_est_vu(tmp_path: Path) -> None:
    tracked = _repo(tmp_path)
    before = _before(tmp_path)
    tracked.write_text("indexé\n", encoding="utf-8")
    subprocess.run(["git", "add", "suivi.txt"], cwd=tmp_path, check=True)

    assert changed_since(tmp_path, before) == ("suivi.txt",)


def test_retour_a_l_etat_propre_et_suppression_sont_vus(tmp_path: Path) -> None:
    tracked = _repo(tmp_path)
    tracked.write_text("déjà modifié\n", encoding="utf-8")
    before = _before(tmp_path)
    subprocess.run(["git", "checkout", "-q", "--", "suivi.txt"], cwd=tmp_path, check=True)
    assert changed_since(tmp_path, before) == ("suivi.txt",)

    before = _before(tmp_path)
    tracked.unlink()
    assert changed_since(tmp_path, before) == ("suivi.txt",)


def test_hors_depot_git_rend_none(tmp_path: Path) -> None:
    assert worktree_fingerprint(tmp_path) is None
    assert changed_since(tmp_path, {}) is None


def test_git_introuvable_rend_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def absent(*_args: object, **_kwargs: object) -> None:
        raise OSError("git absent")

    monkeypatch.setattr(dispatch_worktree.subprocess, "run", absent)

    assert worktree_fingerprint(tmp_path) is None
