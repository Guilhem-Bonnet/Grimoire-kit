"""Le rendu Markdown du cockpit ne garde que les liens à schéma autorisé.

Le test JavaScript (`tests/js/markdown_links.test.mjs`) n'est lancé par aucun
job de CI : ce garde-fou le fait passer par pytest, avec `node --test`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_TEST = ROOT / "tests" / "js" / "markdown_links.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : rendu non vérifiable ici")
def test_markdown_link_schemes_are_allow_listed() -> None:
    result = subprocess.run(
        [shutil.which("node") or "node", "--test", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]


def test_markdown_renderer_has_no_unfiltered_href() -> None:
    source = (ROOT / "web" / "workspace" / "markdown.js").read_text(encoding="utf-8")
    assert '<a href="$2"' not in source
