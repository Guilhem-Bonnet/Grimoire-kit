"""Le catalogue du kit hache des milliers de fichiers à chaque vue Source.

Sans mémo, chaque requête relisait intégralement tous les fichiers pour
recalculer des empreintes identiques. Ces tests décrivent le défaut qu'ils
empêchent : retirer le cache et le compteur de lectures remonte au nombre de
fichiers au lieu d'un seul.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from grimoire.core import kit_hashes


@pytest.fixture
def file_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Compte les fichiers relus pour hacher, sans toucher au reste de ``Path``."""
    reads: list[Path] = []
    real_read_bytes = Path.read_bytes

    def counting(path: Path) -> bytes:
        reads.append(path)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counting)
    return reads


def test_le_second_appel_ne_recalcule_aucun_digest(tmp_path: Path, file_reads: list[Path]) -> None:
    """Au deuxième appel sans changement, aucun digest n'est recalculé."""
    note = tmp_path / "note.md"
    note.write_text("contenu stable\n", encoding="utf-8")

    first = kit_hashes.digest_of(note)
    second = kit_hashes.digest_of(note)

    assert first == second
    assert len(file_reads) == 1, "un fichier inchangé ne se relit pas"


def test_seul_le_fichier_modifie_est_recalcule(tmp_path: Path, file_reads: list[Path]) -> None:
    """Après la modification d'un fichier, seul son digest est recalculé."""
    changed = tmp_path / "changed.md"
    untouched = tmp_path / "untouched.md"
    changed.write_text("avant\n", encoding="utf-8")
    untouched.write_text("jamais modifié\n", encoding="utf-8")

    kit_hashes.digest_of(changed)
    kit_hashes.digest_of(untouched)
    assert len(file_reads) == 2

    # Les deux fichiers sont à nouveau demandés : le cache répond, sans lecture.
    kit_hashes.digest_of(changed)
    kit_hashes.digest_of(untouched)
    assert len(file_reads) == 2

    changed.write_text("après, plus long\n", encoding="utf-8")
    expected = hashlib.new("sha256", "après, plus long\n".encode()).hexdigest()

    assert kit_hashes.digest_of(changed) == expected, "le contenu neuf est bien haché"
    kit_hashes.digest_of(untouched)

    assert len(file_reads) == 3, "seul le fichier modifié est relu"
