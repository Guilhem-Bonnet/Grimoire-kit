"""`cadrage_interview.retract` : le chemin du fichier de phase vient de listes réelles, jamais du nom reçu."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core import cadrage_interview as interview
from grimoire.core.chantiers import new_chantier


@pytest.fixture
def root(tmp_path: Path) -> Path:
    new_chantier(tmp_path, "Un", slug="un")
    interview.interview_plan(tmp_path, "un", depth="express")
    interview.answer(tmp_path, "un", "utilisateurs", "les mainteneurs")
    return tmp_path


@pytest.mark.parametrize("hostile", ["..", "../un", "/etc", "un/../un", "inconnu", ".", ""])
def test_un_nom_hostile_ou_inconnu_est_refuse_avant_toute_ecriture(root: Path, hostile: str) -> None:
    before = (root / "_grimoire/cadrage/un/01-brief.md").read_bytes()
    with pytest.raises(ValueError):
        interview.retract(root, hostile, "utilisateurs")
    assert (root / "_grimoire/cadrage/un/01-brief.md").read_bytes() == before


def test_un_fichier_de_phase_en_lien_symbolique_n_est_jamais_ecrit(root: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("dehors") / "brief.md"
    outside.write_text("## L'utilisateur\n\n- les mainteneurs\n", encoding="utf-8")
    brief = root / "_grimoire/cadrage/un/01-brief.md"
    brief.unlink()
    brief.symlink_to(outside)
    with pytest.raises(ValueError, match="lien symbolique"):
        interview.retract(root, "un", "utilisateurs")
    assert outside.read_text(encoding="utf-8") == "## L'utilisateur\n\n- les mainteneurs\n"


def test_un_dossier_de_chantier_en_lien_symbolique_est_refuse(root: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    elsewhere = tmp_path_factory.mktemp("ailleurs")
    (root / "_grimoire/cadrage/lien").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="chantier inconnu"):
        interview.retract(root, "lien", "utilisateurs")


def test_le_chantier_principal_et_un_chantier_reel_passent(root: Path) -> None:
    assert interview.retract(root, "un", "utilisateurs")["removed"] is True
