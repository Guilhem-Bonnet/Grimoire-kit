"""Garde de structure du zoom de Concevoir (#536).

Le test navigateur (``tests/e2e/test_workspace_concevoir_zoom.py``) ne tourne
pas en CI : Playwright n'est pas une dépendance du kit. Ce garde lit la source
de l'espace et verrouille le contrat qui a manqué : tout segment du zoom passe
par ``pickZoom``, qui charge le blueprint sélectionné avant d'ouvrir
« Workflow » (sans cela, l'écran restait sur « Chargement du blueprint… »).
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "web" / "workspace" / "spaces" / "concevoir.js"


def _text() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_chaque_segment_du_zoom_passe_par_pick_zoom() -> None:
    callbacks = re.findall(r"ctx\.docbar\.setZoom\([^,]+,\s*[^,]+,\s*(\w+)\)", _text())

    assert len(callbacks) == 3, "trois sites posent le zoom du docbar"
    assert set(callbacks) == {"pickZoom"}, callbacks


def test_pick_zoom_charge_le_blueprint_selectionne_avant_workflow() -> None:
    body = re.search(r"function pickZoom\(id\) \{(.*?)\n  \}", _text(), re.DOTALL)

    assert body is not None, "pickZoom a disparu"
    assert "id === 'workflow'" in body.group(1)
    assert "!state.blueprint" in body.group(1)
    assert "zoomToWorkflow(state.selectedId)" in body.group(1)
