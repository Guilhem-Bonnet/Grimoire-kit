"""One-line descriptions of the bundled Copilot workflows.

Static data, kept out of :mod:`grimoire.cli.app`: a lookup table is not CLI
wiring, and the wiring module is the one under a size ratchet.
"""

from __future__ import annotations

WF_DESCRIPTIONS: dict[str, str] = {
    "grimoire-session-bootstrap": "Reprendre le travail avec contexte complet",
    "grimoire-health-check": "Diagnostic global de santé projet",
    "grimoire-dream": "Consolider les apprentissages inter-sessions",
    "grimoire-pre-push": "Valider avant push (tests/lint/checks)",
    "grimoire-changelog": "Générer un changelog depuis l'historique",
    "grimoire-status": "Obtenir un snapshot rapide du projet",
    "grimoire-self-heal": "Diagnostiquer et réparer les pannes courantes",
}
