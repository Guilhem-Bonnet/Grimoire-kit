"""La vue Source hache des milliers de fichiers : le cockpit mémoïse ces digests, le contrôle d'intégrité non."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from grimoire.core import kit_hashes
from grimoire.tools import workspace_api


@pytest.fixture
def file_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Compte les fichiers relus pour hacher."""
    reads: list[Path] = []
    real_read_bytes = Path.read_bytes

    def counting(path: Path) -> bytes:
        reads.append(path)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counting)
    return reads


def test_le_second_appel_ne_recalcule_aucun_digest(tmp_path: Path, file_reads: list[Path]) -> None:
    note = tmp_path / "note.md"
    note.write_text("contenu stable\n", encoding="utf-8", newline="\n")
    assert workspace_api.cached_digest(note) == workspace_api.cached_digest(note)
    assert len(file_reads) == 1, "un fichier inchangé ne se relit pas"


def test_seul_le_fichier_modifie_est_recalcule(tmp_path: Path, file_reads: list[Path]) -> None:
    changed, untouched = tmp_path / "changed.md", tmp_path / "untouched.md"
    changed.write_text("avant\n", encoding="utf-8", newline="\n")
    untouched.write_text("jamais modifié\n", encoding="utf-8", newline="\n")
    for _ in range(2):
        workspace_api.cached_digest(changed)
        workspace_api.cached_digest(untouched)
    assert len(file_reads) == 2
    changed.write_text("après, plus long\n", encoding="utf-8", newline="\n")
    assert workspace_api.cached_digest(changed) == hashlib.sha256("après, plus long\n".encode()).hexdigest()
    workspace_api.cached_digest(untouched)
    assert len(file_reads) == 3, "seul le fichier modifié est relu"


def test_un_fichier_absent_rend_vide_sans_etre_memoise(tmp_path: Path) -> None:
    ghost = tmp_path / "absent.md"
    assert workspace_api.cached_digest(ghost) == ""
    ghost.write_text("apparu\n", encoding="utf-8", newline="\n")
    assert workspace_api.cached_digest(ghost) == hashlib.sha256(b"apparu\n").hexdigest()


def test_le_controle_d_integrite_relit_toujours(tmp_path: Path, file_reads: list[Path]) -> None:
    """``kit_hashes.digest_of`` sert à ``verify``/``doctor``/``upgrade`` : jamais de mémo par mtime."""
    note = tmp_path / "note.md"
    note.write_text("x\n", encoding="utf-8", newline="\n")
    kit_hashes.digest_of(note)
    kit_hashes.digest_of(note)
    assert len(file_reads) == 2
