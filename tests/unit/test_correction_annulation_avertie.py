"""P42 : le formulaire « Demander une correction » prévient que la session peut encore travailler."""

from pathlib import Path

MEMOIRE_JS = Path(__file__).resolve().parents[2] / "web" / "workspace" / "spaces" / "memoire.js"


def test_formulaire_de_correction_previent_que_la_session_peut_encore_travailler():
    source = MEMOIRE_JS.read_text(encoding="utf-8")
    assert "kn-ask-note" in source
    assert "peut encore travailler" in source
