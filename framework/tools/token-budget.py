#!/usr/bin/env python3
"""token-budget.py — Thin CLI shim over ``grimoire.tools.token_budget``."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_SRC = _HERE.parent.parent.parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from grimoire.tools.token_budget import *  # noqa: F403, E402
from grimoire.tools.token_budget import (  # noqa: E402
    CHARS_PER_TOKEN,
    CRITICAL_THRESHOLD,
    DEFAULT_MODEL,
    EMERGENCY_THRESHOLD,
    MODEL_WINDOWS,
    PRIORITY_NAMES,
    TOKEN_BUDGET_VERSION,
    TOKEN_USAGE_LOG,
    TOKEN_USAGE_MAX_ENTRIES,
    WARNING_THRESHOLD,
    BudgetStatus,
    EnforcementAction,
    EnforcementReport,
    PriorityBucket,
    TokenBudgetEnforcer,
    _load_usage_history,
    _log_usage,
    _prune_usage_log,
    load_budget_config,
    mcp_context_budget,
    usage_trend,
)
from grimoire.tools.token_budget import main as _kit_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_kit_main())
