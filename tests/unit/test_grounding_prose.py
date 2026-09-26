"""La prose émise aux projets ne demande jamais un chiffre sans mesure (#613).

Un utilisateur Copilot a rapporté des personas qui « inventent des chiffres
et donnent des pronostics sur des éléments non regardés ». Les gabarits de
sortie du kit portaient des `Score global : X/5`, `X/10`, `X/100` à remplir
de tête, sans commande qui les calcule ; le socle disait « au-delà du budget
de questions, décider soi-même ». Ces tests figent l'inverse.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRAMEWORK = REPO / "framework"


def _guard():  # type: ignore[no-untyped-def]
    """Le même script que pre-commit et `make lint` : une seule source de règles."""
    spec = importlib.util.spec_from_file_location("check_emitted_prose", REPO / "scripts" / "check-emitted-prose.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # les dataclasses résolvent leurs annotations via sys.modules
    spec.loader.exec_module(module)
    return module


GUARD = _guard()


@pytest.mark.parametrize("path", GUARD.corpus(), ids=lambda p: str(p.relative_to(REPO)))
def test_emitted_prose_never_asks_for_an_unmeasured_number(path: Path) -> None:
    hits = GUARD.scan([path])
    assert not hits, "chiffre à remplir sans mesure :\n" + "\n".join(map(str, hits))


def test_guard_corpus_covers_every_surface_the_kit_copies() -> None:
    """Le corpus suit le scaffolder : un document de protocole ajouté est scanné sans retouche ici."""
    names = {p.name for p in GUARD.corpus()}
    for required in (
        "grimoire-project.instructions.md",
        "grimoire-health-check.prompt.md",
        "grimoire-evidence.md",
        "audit-report.md",
        "agent-optimizer.md",
        "agent-base-compact.md",
        "honest-uncertainty-protocol.md",
    ):
        assert required in names, required


def test_guard_refuses_an_unjustified_allowlist_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "allow.txt"
    bad.write_text("framework/agent-base.md:placeholder-score\n", encoding="utf-8")
    monkeypatch.setattr(GUARD, "ALLOWLIST", bad)
    with pytest.raises(SystemExit, match="sans justification"):
        GUARD.allowlist()


def test_guard_catches_each_rule_on_a_synthetic_line() -> None:
    seen: set[str] = set()
    for line, expected in (
        ("Score global : X/10", "placeholder-score"),
        ('confidence: "30%"', "invented-confidence"),
        ("Estimation : 12 jours", "estimate-placeholder"),
        ("trust_score: 91", "numeric-trust-score"),
        ("confidence: 0.92", "invented-confidence"),
        ("#### `[agent-id]` — Score [X]/100", "placeholder-score"),
        ("confidence_level: GREEN | YELLOW | RED", "confidence-scale"),
        ("Si dépendance HUP ROUGE → escalader", "confidence-scale"),
    ):
        assert GUARD.RULES[expected].search(line), (line, expected)
        seen.add(expected)
    assert seen == set(GUARD.RULES), "chaque règle a un cas synthétique"
    for benign in ("Effort [S/M/L]", 'confidence: "haute|moyenne|faible"', "burn-rate 14.4x/1h", "confidence_boost: 80", "HUP (BM-50)"):
        assert not any(p.search(benign) for p in GUARD.RULES.values()), benign
