"""La mémoire de ce qu'on tire d'un fichier (GAO-cockpit-char-001).

Défaut empêché : ``attention`` relisait et hachait les mêmes fichiers immobiles à chaque requête (4 à 7 s
mesurées le 2026-11-13). Défaut à ne pas rouvrir : resservir un résultat périmé quand un fichier est réécrit.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from grimoire.core import file_memo


@pytest.fixture(autouse=True)
def _vide() -> Iterator[None]:
    file_memo.clear()
    yield
    file_memo.clear()


def _vieux(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    stamp = time.time() - 3600
    os.utime(path, (stamp, stamp))
    return path


def _compteur() -> tuple[list[bytes], Callable[[bytes], str]]:
    seen: list[bytes] = []

    def compute(data: bytes) -> str:
        seen.append(data)
        return data.decode("utf-8").upper()

    return seen, compute


def test_un_fichier_ancien_et_immobile_n_est_lu_qu_une_fois(tmp_path: Path) -> None:
    path = _vieux(tmp_path / "a.yaml", "alpha")
    seen, compute = _compteur()

    assert file_memo.derived(path, "maj", compute) == "ALPHA"
    assert file_memo.derived(path, "maj", compute) == "ALPHA"
    assert len(seen) == 1


def test_une_reecriture_change_la_signature_et_le_resultat(tmp_path: Path) -> None:
    path = _vieux(tmp_path / "a.yaml", "alpha")
    seen, compute = _compteur()
    file_memo.derived(path, "maj", compute)

    path.write_text("bravo!", encoding="utf-8")

    assert file_memo.derived(path, "maj", compute) == "BRAVO!"
    assert len(seen) == 2


def test_un_fichier_tout_juste_ecrit_est_relu_meme_a_date_et_taille_egales(tmp_path: Path) -> None:
    """Sous une horloge de fichiers grossière, une réécriture rapide garde date et taille : seul le contenu tranche."""
    path = tmp_path / "a.yaml"
    path.write_text("alpha", encoding="utf-8")
    info = path.stat()
    seen, compute = _compteur()
    file_memo.derived(path, "maj", compute)

    path.write_text("omega", encoding="utf-8")
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))

    assert file_memo.derived(path, "maj", compute) == "OMEGA"
    assert len(seen) == 2


def test_deux_lectures_d_un_meme_fichier_ne_se_melangent_pas(tmp_path: Path) -> None:
    path = _vieux(tmp_path / "a.yaml", "alpha")

    assert file_memo.derived(path, "maj", lambda d: d.decode().upper()) == "ALPHA"
    assert file_memo.derived(path, "long", lambda d: len(d)) == 5


def test_une_erreur_n_est_jamais_gardee(tmp_path: Path) -> None:
    path = _vieux(tmp_path / "a.yaml", "alpha")
    calls: list[int] = []

    def boom(data: bytes) -> str:
        calls.append(1)
        raise ValueError("illisible")

    for _ in range(2):
        with pytest.raises(ValueError):
            file_memo.derived(path, "boom", boom)
    assert len(calls) == 2


def test_un_fichier_absent_leve_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        file_memo.derived(tmp_path / "absent.yaml", "maj", lambda d: d)
