"""Les items MOA de À traiter disent qui agit et la commande exacte (#1342).

Le test JavaScript (`tests/js/attention_moa.test.mjs`) n'est lancé par aucun job de CI :
ce garde-fou le fait passer par pytest, avec `node --test`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_TEST = ROOT / "tests" / "js" / "attention_moa.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : rendu non vérifiable ici")
def test_chat_masks_only_the_turn_attention_moa() -> None:
    result = subprocess.run(
        [shutil.which("node") or "node", "--test", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
