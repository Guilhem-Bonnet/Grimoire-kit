"""La racine du dépôt ne porte aucun script Python ponctuel (2026-11-12).

``_gen_tests.py`` (générateur de tests à usage unique) et
``scripts/security/dismiss-codeql-false-positives.py`` (triage CodeQL d'un lot
passé) sont restés après leur usage. Un script vit dans ``scripts/`` avec son
appelant, pas à la racine : les fichiers ``.py`` de la racine doivent donc
appartenir à une liste autorisée explicite, vide aujourd'hui.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Liste autorisée explicite : ajouter ici, avec sa justification en commentaire,
# tout ``.py`` racine légitime. Vide : aucun n'est admis aujourd'hui.
ALLOWED_ROOT_PYTHON_FILES: frozenset[str] = frozenset()

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="dépôt git source uniquement"
)


def _tracked_root_python_files() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {name for name in result.stdout.splitlines() if name and "/" not in name}


def test_aucun_script_python_ponctuel_a_la_racine() -> None:
    unexpected = _tracked_root_python_files() - ALLOWED_ROOT_PYTHON_FILES
    assert not unexpected, (
        f"scripts Python à la racine hors liste autorisée : {sorted(unexpected)} — "
        "les déplacer dans scripts/ avec leur appelant, ou justifier l'ajout dans "
        "ALLOWED_ROOT_PYTHON_FILES"
    )
