"""Empreinte de l'arbre de travail (issue #582 lot B, suite) — détecte un run périmé.

Sans cette empreinte, un run de test enregistré par ``record_acceptance_test_run``
reste vert pour toujours, même après que le code a changé sous lui — le trou
identifié en revue de la PR #585 : un agent lance ``gate run-tests`` tôt, puis
modifie le code, et le gate reste vert sur du code jamais exercé.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

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
