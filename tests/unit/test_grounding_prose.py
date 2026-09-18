"""La prose émise aux projets ne demande jamais un chiffre sans mesure (#613).

Un utilisateur Copilot a rapporté des personas qui « inventent des chiffres
et donnent des pronostics sur des éléments non regardés ». Les gabarits de
sortie du kit portaient des `Score global : X/5`, `X/10`, `X/100` à remplir
de tête, sans commande qui les calcule ; le socle disait « au-delà du budget
de questions, décider soi-même ». Ces tests figent l'inverse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRAMEWORK = REPO / "framework"
ARCHETYPES = REPO / "archetypes"

#: Un score à remplir sans formule ni commande : `X/5`, `X/10`, `X/100`,
#: « Score moyen : X ». `{avg_score}/100` (variable calculée) ne matche pas.
_PLACEHOLDER_SCORE = re.compile(r"\bX\s*/\s*(?:5|10|100)\b|Score (?:global|moyen)\s*:\s*X\b")

#: Prose réellement copiée ou émise dans un projet utilisateur.
_EMITTED_PROSE = (
    sorted((FRAMEWORK / "copilot").rglob("*.md"))
    + sorted((FRAMEWORK / "hosts").rglob("*.md"))
    + sorted((FRAMEWORK / "prompt-templates").rglob("*.md"))
    + sorted(ARCHETYPES.rglob("*.md"))
    + [FRAMEWORK / "agent-base.md", FRAMEWORK / "agent-base-compact.md"]
)


@pytest.mark.parametrize("path", _EMITTED_PROSE, ids=lambda p: str(p.relative_to(REPO)))
def test_emitted_prose_never_asks_for_an_unmeasured_score(path: Path) -> None:
    hits = [
        f"{path.relative_to(REPO)}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _PLACEHOLDER_SCORE.search(line)
    ]
    assert not hits, "score à remplir sans mesure :\n" + "\n".join(hits)


def test_friction_budget_never_turns_an_unverified_fact_into_a_decision() -> None:
    """Le budget de questions porte sur les questions, jamais sur les faits."""
    for name in ("agent-base.md", "agent-base-compact.md"):
        text = (FRAMEWORK / name).read_text(encoding="utf-8")
        assert "jamais sur les faits" in text, name


def test_auto_loaded_copilot_instruction_forbids_unmeasured_numbers() -> None:
    text = (FRAMEWORK / "copilot/instructions/grimoire-project.instructions.md").read_text(encoding="utf-8")
    assert "non mesuré" in text


def test_evidence_skill_names_the_claim_ledger() -> None:
    """La skill de preuve enseignait le pack et l'enveloppe, jamais le claim-ledger que le gate vérifie."""
    text = (FRAMEWORK / "hosts/skills/grimoire-evidence.md").read_text(encoding="utf-8")
    assert "claim-ledger.md" in text
