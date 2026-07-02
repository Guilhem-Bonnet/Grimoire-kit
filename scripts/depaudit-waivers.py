#!/usr/bin/env python3
"""Emit pip-audit ``--ignore-vuln`` flags for active governed dependency waivers.

Reads ``.github/security/dependency-waivers.yaml`` (schema:
grimoire-agentic-standard-waivers/v1). Only waivers with a valid, non-expired
``expires_at`` are emitted — an expired waiver silently disappears from the
output, which re-tightens the CI dependency audit until the risk is
re-evaluated. See https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/20.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from ruamel.yaml import YAML

WAIVERS_FILE = Path(__file__).resolve().parents[1] / ".github" / "security" / "dependency-waivers.yaml"


def _parse_expiry(value: object) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def active_vulnerability_ids(path: Path = WAIVERS_FILE, today: dt.date | None = None) -> list[str]:
    """Return vulnerability ids covered by a non-expired waiver."""
    if not path.is_file():
        return []
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    reference_day = today or dt.date.today()
    active: list[str] = []
    for waiver in data.get("waivers") or []:
        if not isinstance(waiver, dict):
            continue
        vulnerability_id = str(waiver.get("vulnerability_id") or "").strip()
        if not vulnerability_id:
            continue
        expiry = _parse_expiry(waiver.get("expires_at"))
        if expiry is None:
            print(
                f"[waivers] {vulnerability_id}: expires_at invalide ou absent — waiver ignoré, audit strict",
                file=sys.stderr,
            )
            continue
        if expiry < reference_day:
            print(
                f"[waivers] {vulnerability_id}: waiver expiré le {expiry.isoformat()} — audit redevenu strict",
                file=sys.stderr,
            )
            continue
        active.append(vulnerability_id)
    return active


def main() -> int:
    flags = [f"--ignore-vuln {vuln}" for vuln in active_vulnerability_ids()]
    if flags:
        print(" ".join(flags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
