"""La borne `chromadb<0.7` est une décision instruite, pas un héritage (#174).

Mesuré le 2026-09-04 : la surface que `mempalace` utilise est identique en
1.x, un palais 0.6 se relit en 1.x mais un palais 1.x est illisible en 0.6,
et 1.x ajoute une CVE d'injection pré-authentification au périmètre audité.
Qui lève la borne doit le faire en connaissance de cause : ce test le force
à relire le commentaire de pyproject.toml et à décider de la migration à sens
unique des palais existants.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@pytest.mark.skipif(not _PYPROJECT.is_file(), reason="dépôt source uniquement")
def test_the_mempalace_extra_keeps_its_instructed_bound() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extra = data["project"]["optional-dependencies"]["mempalace"]
    assert extra == ["chromadb>=0.6,<0.7"], (
        f"borne chromadb modifiée : {extra} — relire le commentaire de pyproject.toml (#174) : "
        "un palais écrit en 1.x ne se relit plus en 0.6, et 1.x porte CVE-2026-45829"
    )


@pytest.mark.skipif(not _PYPROJECT.is_file(), reason="dépôt source uniquement")
def test_the_bound_says_why_it_holds() -> None:
    text = _PYPROJECT.read_text(encoding="utf-8")
    before = text[: text.index('mempalace = ["chromadb')]
    justification = before[before.rfind("\n\n") :]
    assert "#174" in justification and "KeyError" in justification, "la borne doit garder sa justification mesurée"
