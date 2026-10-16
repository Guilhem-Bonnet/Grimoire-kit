"""La doc du coût inconnu dit où `cost_unknown` sert dans un flow (W1-01, revue)."""

from __future__ import annotations

import re
from pathlib import Path

CLI_REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "cli-reference.md"


def _flow_cost_unknown_paragraph() -> str:
    text = " ".join(CLI_REFERENCE.read_text(encoding="utf-8").split())
    match = re.search(r"Dans un flow, `cost_unknown` .*?\. ", text)
    assert match, "la phrase « Dans un flow, `cost_unknown` ... » a disparu de docs/cli-reference.md"
    return match.group(0)


def test_la_doc_cite_les_passes_optionnelles_de_budget_parmi_les_usages_de_cost_unknown() -> None:
    # genres.py appelle cap_reason(...) avec la politique du pilote pour les passes optionnelles de `budget`.
    sentence = _flow_cost_unknown_paragraph()

    assert "budget" in sentence
    assert "skipped_reason" in sentence
    assert "ne sert qu'à l'escalade et à l'enchaînement des nodes d'un sous-flow :" not in sentence
