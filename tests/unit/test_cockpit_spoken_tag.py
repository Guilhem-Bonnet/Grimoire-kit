"""Le fil du chat ne masque que la balise parlée du tour (#1343 V3).

Le test JavaScript (`tests/js/spoken_tag.test.mjs`) n'est lancé par aucun job de CI :
ce garde-fou le fait passer par pytest, avec `node --test`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_TEST = ROOT / "tests" / "js" / "spoken_tag.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : masquage non vérifiable ici")
def test_chat_masks_only_the_turn_spoken_tag() -> None:
    result = subprocess.run(
        [shutil.which("node") or "node", "--test", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]


def test_chat_has_no_unanchored_spoken_tag_regex() -> None:
    source = (ROOT / "web" / "workspace" / "chat.js").read_text(encoding="utf-8")
    assert "SPOKEN_TAG_RE" not in source and "withoutSpokenTag(current.text, spokenToken)" in source
