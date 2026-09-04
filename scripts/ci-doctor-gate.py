#!/usr/bin/env python3
"""CI verdict on the output of ``bash grimoire-init.sh doctor``.

``doctor`` always exits 0 — it is a diagnostic, not a gate — so the CI has to
read its output. The previous reading was three ``grep -c`` counts subtracted
from one another: every line mentioning Qdrant or a CI-expected path was
subtracted from the error count whether or not it was an error, the result
could go negative, and the step could not fail. A doctor that never ran left
an empty file: zero errors, green step.

This gate is explicit about both: the doctor must have run (its banner is
present), and every ``✗`` line that is not one of the known CI gaps fails the
step and is printed.

Usage:
    python scripts/ci-doctor-gate.py doctor-output.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

BANNER = "Grimoire Doctor"
ERROR_MARK = "✗"

#: Substrings of ``✗`` lines that are expected on the SDK repository itself:
#: no Qdrant service, and no initialised project (`_grimoire/_config/`,
#: `_grimoire-output/`, `project-context.yaml` do not exist here).
EXPECTED_GAPS = (
    "Qdrant",
    "qdrant",
    "_grimoire/_config/",
    "_grimoire-output/",
    "project-context.yaml",
)


def verdict(text: str) -> tuple[int, list[str]]:
    """``(exit_code, problems)`` for a doctor transcript."""
    if not text.strip():
        return 1, ["aucune sortie : le doctor n'a pas tourné"]
    if BANNER not in text:
        return 1, [f"bannière « {BANNER} » absente : ce n'est pas une sortie du doctor"]
    unexpected = [
        line.strip()
        for line in text.splitlines()
        if ERROR_MARK in line and not any(gap in line for gap in EXPECTED_GAPS)
    ]
    return (1, unexpected) if unexpected else (0, [])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: ci-doctor-gate.py <doctor-output.txt>", file=sys.stderr)
        return 2
    path = Path(args[0])
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    code, problems = verdict(text)
    if code:
        print("Doctor : erreurs critiques hors Qdrant et hors manques attendus en CI :")
        for problem in problems:
            print(f"  {problem}")
    else:
        print("Doctor OK (hors connectivité Qdrant, hors manques attendus en CI)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
