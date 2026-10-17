"""Empreinte de l'arbre de travail (issue #582 lot B, suite) — détecte un run périmé.

Sans cette empreinte, un run de test enregistré par ``record_acceptance_test_run``
reste vert pour toujours, même après que le code a changé sous lui — le trou
identifié en revue de la PR #585 : un agent lance ``gate run-tests`` tôt, puis
modifie le code, et le gate reste vert sur du code jamais exercé.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)


def _commit(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message], check=True)


# ── Hors dépôt git : repli sur (chemin, taille, mtime_ns) ───────────────────


def test_fs_fallback_is_stable_when_nothing_changes(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    assert compute_tree_fingerprint(tmp_path) == compute_tree_fingerprint(tmp_path)


def test_fs_fallback_changes_when_a_source_file_changes(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    # Taille différente, pas seulement le contenu : la mtime seule peut ne pas
    # bouger d'une écriture à l'autre sur un système de fichiers à faible
    # résolution, la taille si.
    (tmp_path / "app.py").write_text("print('v1')\nprint('v2')\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before != after


def test_fs_fallback_ignores_grimoire_output(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    output_dir = tmp_path / "_grimoire-output" / "evidence" / "bootstrap"
    output_dir.mkdir(parents=True)
    (output_dir / "test-run.json").write_text("{}\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before == after


def test_fs_fallback_ignores_dependency_and_build_dirs(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    for noisy in (".venv/lib/x.py", "node_modules/pkg/index.js", "target/debug/bin"):
        path = tmp_path / noisy
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("noise\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before == after


# ── Dépôt git : sha256(HEAD + status --porcelain -z + diff HEAD) ───────────


def test_git_fingerprint_is_stable_on_a_clean_tree(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    assert compute_tree_fingerprint(tmp_path) == compute_tree_fingerprint(tmp_path)


def test_git_fingerprint_changes_on_a_tracked_file_edit(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    before = compute_tree_fingerprint(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\nprint('v2')\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before != after


def test_git_fingerprint_changes_on_a_new_untracked_file(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    before = compute_tree_fingerprint(tmp_path)
    (tmp_path / "new_file.py").write_text("print('new')\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before != after


def test_git_fingerprint_ignores_grimoire_output(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    before = compute_tree_fingerprint(tmp_path)
    output_dir = tmp_path / "_grimoire-output" / "evidence" / "bootstrap"
    output_dir.mkdir(parents=True)
    (output_dir / "test-run.json").write_text("{}\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before == after


def test_git_fingerprint_falls_back_without_a_commit(tmp_path: Path) -> None:
    """``git rev-parse HEAD`` échoue sans commit : repli sur l'empreinte filesystem, jamais une exception."""
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    fingerprint = compute_tree_fingerprint(tmp_path)
    assert fingerprint


# ── Revue de la PR #585 : caches non déterministes, fichier non suivi retouché ──


def test_git_fingerprint_ignores_pytest_cache(tmp_path: Path) -> None:
    """Point 1 : un cache non suivi qu'une commande de test régénère ne doit jamais périmer un run."""
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    before = compute_tree_fingerprint(tmp_path)
    cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "lastfailed").write_text("{}\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before == after


def test_git_fingerprint_ignores_pytest_cache_nested_under_a_tracked_dir(tmp_path: Path) -> None:
    """Même garde quand le cache apparaît sous un dossier déjà suivi (git l'énumère alors lui-même)."""
    _git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    before = compute_tree_fingerprint(tmp_path)
    cache_dir = tmp_path / "src" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "mod.pyc").write_bytes(b"bytecode")
    after = compute_tree_fingerprint(tmp_path)
    assert before == after


def test_fs_fallback_ignores_pytest_cache(tmp_path: Path) -> None:
    """Même garde hors dépôt git (repli filesystem)."""
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    cache_dir = tmp_path / ".pytest_cache"
    cache_dir.mkdir()
    (cache_dir / "lastfailed").write_text("{}\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before == after


def test_git_fingerprint_changes_when_an_untracked_file_is_edited(tmp_path: Path) -> None:
    """Point 2 : un fichier non suivi n'est représenté par git que par son chemin (`?? chemin`).

    Sans le complément (chemin, taille, mtime_ns) par fichier non suivi, le
    retoucher après le run ne changeait jamais l'empreinte — le cas courant
    d'un agent qui crée un fichier puis le retouche.
    """
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (tmp_path / "new_file.py").write_text("print('new')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    # Taille différente, jamais seulement le contenu : indépendant de la
    # résolution de la mtime du système de fichiers qui exécute ce test.
    (tmp_path / "new_file.py").write_text("print('new')\nprint('edited')\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before != after


def test_git_fingerprint_changes_when_a_file_under_an_untracked_dir_is_edited(tmp_path: Path) -> None:
    """Même garde quand le fichier non suivi retouché vit sous un dossier lui-même non suivi."""
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    new_dir = tmp_path / "new_dir"
    new_dir.mkdir()
    (new_dir / "generated.py").write_text("print('new')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    (new_dir / "generated.py").write_text("print('new')\nprint('edited')\n", encoding="utf-8")
    after = compute_tree_fingerprint(tmp_path)
    assert before != after


# ── W3-04 : empreinte de contenu (pas de mtime) et identité de toolchain ───


def _push_mtime_into_the_future(path: Path) -> None:
    """Un ``touch`` : même contenu, mtime nettement différente."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 60 * 10**9))


def test_fs_fallback_ignores_a_touch(tmp_path: Path) -> None:
    """Un ``touch`` ne change pas l'empreinte : seul le contenu compte (W3-04, acceptation 1)."""
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    _push_mtime_into_the_future(tmp_path / "app.py")
    assert compute_tree_fingerprint(tmp_path) == before


def test_git_fingerprint_ignores_a_touch_of_an_untracked_file(tmp_path: Path) -> None:
    """Même garde pour un fichier non suivi, dont l'empreinte portait la mtime."""
    _git_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    _commit(tmp_path, "init")
    (tmp_path / "new_file.py").write_text("print('new')\n", encoding="utf-8")
    before = compute_tree_fingerprint(tmp_path)
    _push_mtime_into_the_future(tmp_path / "new_file.py")
    assert compute_tree_fingerprint(tmp_path) == before


def _fake_test_runner(bin_dir: Path) -> None:
    """Un exécutable ``pytest`` factice : suffit à ce que ``shutil.which`` le résolve."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "pytest"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)


def _pytest_project(root: Path) -> None:
    """Un projet que ``resolve_need("test-runner")`` détecte comme ``pytest -q``."""
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (root / "app.py").write_text("print('v1')\n", encoding="utf-8")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows : fabrique un exécutable factice (script shell sans extension .exe/.cmd, bit x) que Windows ne lance pas par son nom"
    ),
)
def test_another_toolchain_changes_the_fingerprint(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un binaire de test résolu ailleurs sur le PATH invalide le run (W3-04, acceptation 2)."""
    _pytest_project(tmp_path)
    toolchain_a = tmp_path_factory.mktemp("toolchain-a")
    toolchain_b = tmp_path_factory.mktemp("toolchain-b")
    _fake_test_runner(toolchain_a)
    _fake_test_runner(toolchain_b)
    monkeypatch.setenv("PATH", str(toolchain_a))
    before = compute_tree_fingerprint(tmp_path)
    monkeypatch.setenv("PATH", str(toolchain_b))
    assert compute_tree_fingerprint(tmp_path) != before


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows : fabrique un exécutable factice (script shell sans extension .exe/.cmd, bit x) que Windows ne lance pas par son nom"
    ),
)
def test_a_missing_test_runner_changes_the_fingerprint(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le même arbre sans binaire de test sur le PATH n'est pas la toolchain qui a produit le run."""
    _pytest_project(tmp_path)
    toolchain = tmp_path_factory.mktemp("toolchain")
    empty = tmp_path_factory.mktemp("empty-path")
    _fake_test_runner(toolchain)
    monkeypatch.setenv("PATH", str(toolchain))
    present = compute_tree_fingerprint(tmp_path)
    monkeypatch.setenv("PATH", str(empty))
    assert compute_tree_fingerprint(tmp_path) != present
