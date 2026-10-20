"""D-12 : scénario e2e LIVE Claude Code — de vrais appels ``claude -p``, payants, jamais en CI ordinaire.

Désélectionné par défaut (``-m 'not perf and not live_host'`` dans ``addopts``) ; hors des chemins
que la CI exécute. Lancement à la main (voir ``docs/bench-three-arms.md`` §16)::

    GRIMOIRE_LIVE_CLAUDE_MAX_USD=5 python -m pytest tests/live -m live_host -o addopts="" -q

Sans plafond ou sans ``claude`` sur le ``PATH``, le test est ignoré, et le dit ; avec
``GRIMOIRE_REQUIRE_LIVE=1`` (job ou gate qui exige le live), il échoue au lieu d'être ignoré :
un e2e qui n'a pas tourné n'est pas un vert.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.live_host

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"


def _load() -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location("bench_live_claude_e2e", BENCH / "live_claude_e2e.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _unmet() -> str:
    if sys.platform == "win32":
        return "scénario live : relais POSIX, Linux/macOS seulement"
    if not os.environ.get("GRIMOIRE_LIVE_CLAUDE_MAX_USD", "").strip():
        return "GRIMOIRE_LIVE_CLAUDE_MAX_USD absent : aucun appel payant sans plafond explicite"
    if shutil.which("claude") is None:
        return "claude introuvable sur le PATH"
    return ""


def test_claude_code_blocks_closure_without_proof_then_releases_it(tmp_path: Path, real_home: Path) -> None:
    reason = _unmet()
    if reason:
        if os.environ.get("GRIMOIRE_REQUIRE_LIVE", "").strip() == "1":
            pytest.fail(f"live exigé (GRIMOIRE_REQUIRE_LIVE=1) mais non exécutable : {reason}")
        pytest.skip(reason)
    le = _load()
    workspace = Path(os.environ.get("GRIMOIRE_LIVE_WORKSPACE") or tmp_path / "ws")
    ledger = os.environ.get("GRIMOIRE_LIVE_BUDGET_LEDGER")
    cfg = le.Config(
        workspace=workspace,
        max_total_usd=le.BudgetGuard.open(os.environ["GRIMOIRE_LIVE_CLAUDE_MAX_USD"], None).cap_usd,
        model=os.environ.get("GRIMOIRE_LIVE_MODEL") or le.DEFAULT_MODEL,
        budget_ledger=Path(ledger) if ledger else None,
        real_home=real_home,  # la suite isole HOME : les identifiants se copient depuis le vrai (lecture seule)
    )
    report = le.run_scenario(cfg)
    path = Path(os.environ.get("GRIMOIRE_LIVE_REPORT") or workspace / le.REPORT_NAME)
    leaks = le.write_report(report, path)
    summary = json.dumps({"report": path.as_posix(), "stopped": report.get("stopped"), **report["verdicts"]}, indent=1)
    assert not leaks and report["secrets"] == {"leftover_credentials": [], "token_hits": []}, summary
    assert report["cost"]["cost_known"], summary
    assert report["verdicts"]["proof_hook_blocked_closure_without_proof"], summary
    assert report["verdicts"]["gate_closed_with_proof"], summary
    assert report["verdicts"]["passed"], summary
