"""Ce qui décrit un artefact doit être dérivé de cet artefact, ou testé contre lui.

Un README écrit à la main à côté d'artefacts produits par machine dérive
toujours. Le cas d'école : `evals/README.md` a affirmé « Aucune campagne
exécutée, aucun résultat » pendant que trois rapports de campagne étaient
committés dans le répertoire voisin — l'actif le plus précieux du dépôt,
décrit par la seule phrase qui le nie.

Ces tests ne vérifient pas le style ; ils vérifient qu'une affirmation
documentaire correspond encore à ce que le dépôt contient.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVALS = _REPO_ROOT / "evals"


def _published_campaigns() -> list[Path]:
    reports = _EVALS / "reports"
    if not reports.is_dir():
        return []
    return sorted(d for d in reports.iterdir() if (d / "report.md").is_file())


@pytest.mark.skipif(not _EVALS.is_dir(), reason="dépôt source uniquement")
def test_evals_readme_lists_every_published_campaign() -> None:
    readme = (_EVALS / "README.md").read_text(encoding="utf-8")
    missing = [c.name for c in _published_campaigns() if c.name not in readme]
    assert not missing, (
        "campagnes publiées absentes de evals/README.md : "
        f"{missing} — le README doit citer chaque rapport committé"
    )


@pytest.mark.skipif(not _EVALS.is_dir(), reason="dépôt source uniquement")
def test_evals_readme_does_not_deny_published_campaigns() -> None:
    readme = (_EVALS / "README.md").read_text(encoding="utf-8").lower()
    if not _published_campaigns():
        pytest.skip("aucune campagne publiée : la dénégation serait exacte")
    for denial in ("aucune campagne exécutée", "aucun résultat"):
        assert denial not in readme, (
            f"evals/README.md affirme « {denial} » alors que "
            f"{len(_published_campaigns())} rapport(s) sont committés"
        )
