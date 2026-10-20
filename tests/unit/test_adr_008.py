"""ADR-008 : le texte tient les engagements de la tâche P18."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR = ROOT / "docs" / "explications" / "decisions" / "adr-008-actions-infrastructure-locale-typees.md"


def _text() -> str:
    return ADR.read_text(encoding="utf-8")


def test_adr_existe_et_reste_propose_tant_que_pas_decide() -> None:
    text = _text()
    assert re.search(r"\*\*Statut\*\* : (proposé|accepté|amendé|refusé)", text)


def test_catalogue_cinq_elements() -> None:
    text = _text()
    for element in ("Préconditions", "Plan", "Effets", "Vérification", "Inverse"):
        assert f"| **{element}**" in text


def test_classes_a_a_d() -> None:
    text = _text()
    for classe in ("A", "B", "C", "D"):
        assert f"**Classe {classe} " in text
    assert "jamais `-v`" in text
    assert "commande à copier" in text


def test_routes_et_journal() -> None:
    text = _text()
    for verbe in ("plan", "apply", "rollback"):
        assert f"/actions/<id>/{verbe}" in text
    assert "journal.jsonl" in text


def test_complete_adr_006_d7_sans_elargir() -> None:
    text = _text()
    assert "ADR-006 D7" in text
    assert "sans élargir la" in text
    assert "workspace_exec.ALLOWED" in text


def test_aucun_vocabulaire_de_vulnerabilite() -> None:
    text = _text().lower()
    for mot in ("faille", "advisory", "cross-origin", "vulnérabilité", "cve"):
        assert mot not in text


def test_reference_dans_la_navigation() -> None:
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "adr-008-actions-infrastructure-locale-typees.md" in nav
