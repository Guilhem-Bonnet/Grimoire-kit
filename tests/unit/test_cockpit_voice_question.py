"""Les questions vocales sans réponse passent en tête de la boîte (#1343 V4).

Le test JavaScript (`tests/js/voice_question.test.mjs`) n'est lancé par aucun job de CI :
ce garde-fou le fait passer par pytest, avec `node --test`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_TEST = ROOT / "tests" / "js" / "voice_question.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : masquage non vérifiable ici")
def test_voice_questions_come_first_in_the_outbox() -> None:
    result = subprocess.run(
        [shutil.which("node") or "node", "--test", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
