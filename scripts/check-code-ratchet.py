#!/usr/bin/env python3
"""Code-size ratchet — the legacy surface may only shrink.

Two rules, enforced against ``scripts/code-ratchet-baseline.json``:

R1 — frozen zone. Tracked ``*.py``/``*.sh`` under ``framework/`` plus the
root shell entrypoints (``grimoire-init.sh``, ``grimoire.sh``,
``install.sh``) may not grow past their baselined line count, and no new
file may appear there (cf. framework/FREEZE.md). Deleting files is
always allowed — that is the point.

R2 — src/ oversize guard. A ``src/**/*.py`` file above the threshold
(1500 lines) must be grandfathered in the baseline and may not grow.
Files under the threshold are unconstrained.

Usage:
    python scripts/check-code-ratchet.py               # verify (CI)
    python scripts/check-code-ratchet.py --rebaseline  # shrink baseline

``--rebaseline`` refuses to raise any ceiling: the frozen total must not
grow and no new oversized src/ file may be introduced. Adding a
deliberate exception means editing the baseline JSON by hand in a
reviewable diff.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "code-ratchet-baseline.json"
SRC_THRESHOLD = 1500
ROOT_SHELL = ("grimoire-init.sh", "grimoire.sh", "install.sh")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", "framework", "src", *ROOT_SHELL],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return out.stdout.splitlines()


def count_lines(rel: str) -> int:
    return len((ROOT / rel).read_bytes().splitlines())


def scan() -> tuple[dict[str, int], dict[str, int]]:
    frozen: dict[str, int] = {}
    src_oversized: dict[str, int] = {}
    for rel in tracked_files():
        if not (ROOT / rel).is_file():
            continue
        if rel in ROOT_SHELL or (
            rel.startswith("framework/") and rel.endswith((".py", ".sh"))
        ):
            frozen[rel] = count_lines(rel)
        elif rel.startswith("src/") and rel.endswith(".py"):
            lines = count_lines(rel)
            if lines > SRC_THRESHOLD:
                src_oversized[rel] = lines
    return frozen, src_oversized


def load_baseline() -> dict:
    if not BASELINE.exists():
        sys.exit(f"baseline missing: {BASELINE} — generate it with --rebaseline")
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def write_baseline(frozen: dict[str, int], src_oversized: dict[str, int]) -> None:
    payload = {
        "_comment": (
            "Ratchet ceilings — regenerate only via "
            "scripts/check-code-ratchet.py --rebaseline (shrink-only)."
        ),
        "src_threshold": SRC_THRESHOLD,
        "frozen": dict(sorted(frozen.items())),
        "src_oversized": dict(sorted(src_oversized.items())),
    }
    BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def verify() -> int:
    baseline = load_baseline()
    frozen_base: dict[str, int] = baseline["frozen"]
    oversized_base: dict[str, int] = baseline["src_oversized"]
    frozen_now, oversized_now = scan()
    errors: list[str] = []

    for rel, lines in sorted(frozen_now.items()):
        ceiling = frozen_base.get(rel)
        if ceiling is None:
            errors.append(
                f"R1 {rel}: new file in the frozen zone — new code belongs "
                "under src/ (framework/FREEZE.md); if this is a deliberate "
                "rename, run --rebaseline"
            )
        elif lines > ceiling:
            errors.append(
                f"R1 {rel}: grew {ceiling} -> {lines} lines — the legacy "
                "surface only shrinks; move the change under src/"
            )

    for rel, lines in sorted(oversized_now.items()):
        ceiling = oversized_base.get(rel)
        if ceiling is None:
            errors.append(
                f"R2 {rel}: {lines} lines exceeds the {SRC_THRESHOLD}-line "
                "threshold — split the module instead of growing it"
            )
        elif lines > ceiling:
            errors.append(
                f"R2 {rel}: grew {ceiling} -> {lines} lines — grandfathered "
                "files may only shrink; extract instead of appending"
            )

    if errors:
        print("code ratchet failed:\n" + "\n".join(f"  - {e}" for e in errors))
        return 1
    print(
        f"code ratchet OK — frozen: {len(frozen_now)} files "
        f"({sum(frozen_now.values())} lines <= {sum(frozen_base.values())}), "
        f"oversized src grandfathered: {len(oversized_now)}"
    )
    return 0


def rebaseline() -> int:
    frozen_now, oversized_now = scan()
    if BASELINE.exists():
        baseline = load_baseline()
        old_total = sum(baseline["frozen"].values())
        new_total = sum(frozen_now.values())
        if new_total > old_total:
            sys.exit(
                f"--rebaseline refused: frozen total would grow "
                f"{old_total} -> {new_total} lines"
            )
        new_oversized = set(oversized_now) - set(baseline["src_oversized"])
        if new_oversized:
            sys.exit(
                "--rebaseline refused: new oversized src/ files: "
                + ", ".join(sorted(new_oversized))
            )
    write_baseline(frozen_now, oversized_now)
    print(f"baseline written: {BASELINE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(rebaseline() if "--rebaseline" in sys.argv else verify())
