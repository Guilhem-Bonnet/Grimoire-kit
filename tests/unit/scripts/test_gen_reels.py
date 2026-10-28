"""Garde des bobines de rejeu (scripts/gen-reels.py) : chaque bobine versionnée doit
correspondre à la sortie du kit courant.

Une bobine est l'enregistrement d'une exécution réelle sur un projet jetable. Si une
commande change de sortie, la bobine périme : ce test échoue jusqu'à
`python scripts/gen-reels.py`, dont le diff montre ce qui a bougé.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="le canal humain se rejoue sur un pseudo-terminal (POSIX)"),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
REELS_DIR = REPO_ROOT / "web" / "data" / "reels"


def _load():
    spec = importlib.util.spec_from_file_location("gen_reels", REPO_ROOT / "scripts" / "gen-reels.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_reels"] = module
    spec.loader.exec_module(module)
    return module


gen = _load()
REEL_IDS = [r["id"] for r in gen.REELS]


def test_every_declared_reel_is_committed_with_its_html() -> None:
    for reel_id in REEL_IDS:
        assert (REELS_DIR / f"{reel_id}.json").is_file(), f"bobine absente : {reel_id}"
        assert (REELS_DIR / f"{reel_id}.html").is_file(), f"HTML statique absent : {reel_id}"
    index = json.loads((REELS_DIR / "index.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in index["reels"]] == REEL_IDS


@pytest.mark.parametrize("reel_id", REEL_IDS)
def test_reel_carries_command_version_date_and_digest(reel_id: str) -> None:
    reel = json.loads((REELS_DIR / f"{reel_id}.json").read_text(encoding="utf-8"))
    assert reel["command"], "la commande rejouée manque"
    assert reel["kit_version"]
    assert reel["recorded_at"]
    assert len(reel["output_sha256"]) == 64
    html = (REELS_DIR / f"{reel_id}.html").read_text(encoding="utf-8")
    assert reel["output_sha256"] in html, "le HTML ne porte pas l'empreinte de la bobine"


@pytest.mark.parametrize("reel_id", REEL_IDS)
def test_reel_matches_the_current_kit_output(reel_id: str) -> None:
    committed = json.loads((REELS_DIR / f"{reel_id}.json").read_text(encoding="utf-8"))
    spec = next(r for r in gen.REELS if r["id"] == reel_id)
    fresh = gen.record(spec, gen.kit_version())
    drifted = [
        (a["command"], a["output"], b["output"])
        for a, b in zip(committed["frames"], fresh["frames"], strict=False)
        if a["output"] != b["output"]
    ]
    assert fresh["output_sha256"] == committed["output_sha256"], (
        f"la bobine « {reel_id} » ne correspond plus à la sortie du kit : relancer "
        f"`python scripts/gen-reels.py`. Première dérive : {drifted[:1]}"
    )


def test_a_tampered_reel_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le garde échoue bien quand une bobine ne dit plus la vérité."""
    reel_id = REEL_IDS[-1]
    reel = json.loads((REELS_DIR / f"{reel_id}.json").read_text(encoding="utf-8"))
    reel["output_sha256"] = "0" * 64
    (tmp_path / f"{reel_id}.json").write_text(json.dumps(reel), encoding="utf-8")
    monkeypatch.setattr(gen, "OUT", tmp_path)
    monkeypatch.setattr(gen, "REELS", [r for r in gen.REELS if r["id"] == reel_id])
    monkeypatch.setattr(sys, "argv", ["gen-reels.py", "--check"])
    assert gen.main() == 1
