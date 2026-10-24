"""Un score absent n'est jamais affiché comme 0 (cadrage portail §5.3, EKP-E1).

`web/workspace/score.js` est pur (sans DOM) : on l'exécute sous node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCORE_JS = Path(__file__).resolve().parents[2] / "web" / "workspace" / "score.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")


def _format(score: object, kind: str | None) -> dict[str, object]:
    script = (
        "import(process.argv[1]).then(m => console.log(JSON.stringify("
        "m.formatScore(JSON.parse(process.argv[2]), JSON.parse(process.argv[3])))))"
    )
    out = subprocess.run(
        ["node", "-e", script, SCORE_JS.as_uri(), json.dumps(score), json.dumps(kind)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return dict(json.loads(out.stdout))


@pytest.mark.parametrize("missing", [None, 0, 0.0, "nan"])
def test_absent_or_zero_score_is_unknown_never_zero(missing: object) -> None:
    shown = _format(missing, "bm25")
    assert shown["known"] is False
    assert shown["text"] == "score inconnu"
    assert "0.000" not in str(shown["text"])


def test_tiny_bm25_is_not_rounded_to_zero() -> None:
    shown = _format(2e-06, "bm25")
    assert shown["known"] is True
    assert shown["text"] == "BM25 2.0e-6"


def test_rrf_and_plain_scores_are_named() -> None:
    assert _format(0.0164, "rrf")["text"] == "rang RRF 0.016"
    assert _format(1.18, "bm25")["text"] == "BM25 1.18"
    assert _format(0.5, "backend")["text"] == "score 0.500"
