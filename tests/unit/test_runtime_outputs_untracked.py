"""Les sorties du kit en fonctionnement ne sont jamais suivies par git (2026-10-28).

Des workers ont commité ``_grimoire-runtime-output/evidence/*.jsonl`` et des reçus ``_grimoire-output/reviews/``.
Suivis, ils sont modifiés par chaque relecture ou ``task verify`` dans le worktree de la tâche : le sceau de relecture
voit un fichier suivi modifié, rend la relecture « périmée » et finit par escalader à l'humain.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIRS = ("_grimoire-runtime-output", "_grimoire-output/reviews")

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="dépôt git source uniquement"
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False)


def test_aucune_sortie_du_kit_n_est_suivie() -> None:
    tracked = _git("ls-files", "--", *RUNTIME_DIRS).stdout.split()
    assert tracked == [], f"sorties du kit suivies par git : {tracked}"


@pytest.mark.parametrize(
    "path",
    ["_grimoire-runtime-output/evidence/packs.jsonl", "_grimoire-output/reviews/GAO-x-001/review-receipt.json"],
)
def test_les_sorties_du_kit_sont_ignorees(path: str) -> None:
    assert _git("check-ignore", "-q", "--no-index", path).returncode == 0, f"{path} n'est pas ignoré"
