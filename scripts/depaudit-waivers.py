#!/usr/bin/env python3
"""Emit pip-audit ``--ignore-vuln`` flags for active governed dependency waivers.

Reads ``.github/security/dependency-waivers.yaml`` (schema:
grimoire-agentic-standard-waivers/v1). Only waivers with a valid, non-expired
``expires_at`` are emitted — an expired waiver silently disappears from the
output, which re-tightens the CI dependency audit until the risk is
re-evaluated. See https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/20.

``--check`` turns the same file into a repo gate (``grimoire gates repo``):
exit 1 as soon as a waiver is expired, has an unreadable ``expires_at``, or
lacks one of the fields a reviewer needs (``waiver_id``, ``vulnerability_id``,
``reason``, ``approved_by``). Without it the script only emits flags and always
exits 0, which is what the CI audit step wants.
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


REQUIRED_FIELDS = ("waiver_id", "vulnerability_id", "reason", "approved_by")


def waiver_problems(path: Path = WAIVERS_FILE, today: dt.date | None = None) -> list[str]:
    """Readable problems with the waiver file; an empty list means it is sound.

    An absent file is sound (nothing is waived). An unparsable one is not: a
    gate that cannot read its input must not report success.
    """
    if not path.is_file():
        return []
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # ruamel raises many types; the message is the report
        return [f"{path.name}: illisible ({type(exc).__name__}: {exc})"]
    reference_day = today or dt.date.today()
    problems: list[str] = []
    waivers = data.get("waivers") or []
    if not isinstance(waivers, list):
        return [f"{path.name}: `waivers` n'est pas une liste"]
    for index, waiver in enumerate(waivers):
        if not isinstance(waiver, dict):
            problems.append(f"waivers[{index}] n'est pas un mapping")
            continue
        label = str(waiver.get("waiver_id") or waiver.get("vulnerability_id") or f"waivers[{index}]")
        for field in REQUIRED_FIELDS:
            if not str(waiver.get(field) or "").strip():
                problems.append(f"{label}: champ `{field}` absent ou vide")
        expiry = _parse_expiry(waiver.get("expires_at"))
        if expiry is None:
            problems.append(f"{label}: expires_at invalide ou absent")
        elif expiry < reference_day:
            problems.append(
                f"{label}: waiver expire le {expiry.isoformat()} — le renouveler avec une justification "
                "ou le retirer (l'audit de dépendances est déjà redevenu strict)"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        problems = waiver_problems()
        if problems:
            print("waivers de dépendances en défaut :", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(f"waivers OK — {len(active_vulnerability_ids())} actif(s), aucun expiré")
        return 0
    flags = [f"--ignore-vuln {vuln}" for vuln in active_vulnerability_ids()]
    if flags:
        print(" ".join(flags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
