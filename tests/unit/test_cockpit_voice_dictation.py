"""La mention « dictée vocale » du chat (#1343 V5) : consommée après un envoi réussi, perdue à la réécriture.

Les tests JavaScript (`tests/js/voice_dictation.test.mjs`, `tests/js/voice_wav.test.mjs`) ne sont lancés par
aucun job de CI : ce garde-fou les fait passer par pytest, avec `node --test`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : saisie vocale non vérifiable ici")
@pytest.mark.parametrize("name", ["voice_dictation.test.mjs", "voice_wav.test.mjs"])
def test_voice_input_js(name: str) -> None:
    result = subprocess.run(
        [shutil.which("node") or "node", "--test", str(ROOT / "tests" / "js" / name)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]


def test_chat_commits_the_dictation_only_after_a_successful_send() -> None:
    source = (ROOT / "web" / "workspace" / "chat.js").read_text(encoding="utf-8")
    assert "consumeDictation" not in source
    submit = source[source.index("async function submit()") :]
    submit = submit[: submit.index("form.addEventListener")]
    sends = [m.start() for m in re.finditer(r"await api\.chatSend\(", submit)]
    commits = [m.start() for m in re.finditer(r"voice\.commitDictation\(\)", submit)]
    assert len(sends) == 2 and len(commits) == 2
    for send, commit in zip(sends, commits, strict=True):
        assert send < commit and "catch" not in submit[send:commit]
