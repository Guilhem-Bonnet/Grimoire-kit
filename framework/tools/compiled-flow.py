#!/usr/bin/env python3
"""compiled-flow.py — Thin CLI shim over ``grimoire.tools.compiled_flow``."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_SRC = _HERE.parent.parent.parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from grimoire.tools.compiled_flow import *  # noqa: F403, E402
from grimoire.tools.compiled_flow import main as _kit_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_kit_main())
