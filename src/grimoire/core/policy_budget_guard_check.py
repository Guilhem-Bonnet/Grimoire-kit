"""``doctor``'s policy-budget guard — extracted to keep ``cli/app.py`` under its ratchet.

Defect 5 of the 2026-09-12 session-budget incident (issue #463): a
`per_session` rule with no `tool_pattern` (so ``"*"``, every tool) and
`verdict_on_match: block` can end up refusing every tool in the session, hard-
coded repair exemptions aside (see :mod:`grimoire.policies.temporal`). Never
FAIL: a project may want exactly this and accept the risk — but `doctor`
names the shape instead of leaving it silent, which is what let the real
incident reach production undetected.

Kept to a single call in ``cli/app.py`` — that file is already grandfathered
above its size ratchet and may not grow (see ``scripts/check-code-ratchet.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def apply_policy_budget_guard_check(target: Path, results: list[dict[str, Any]], *, fmt: str, console: Any) -> None:
    """Append the policy-budget guard entries to *results* and print them."""
    from grimoire.core.exceptions import GrimoirePolicyError
    from grimoire.policies.rules_config import load_custom_rules
    from grimoire.policies.schemas import VerdictKind

    try:
        budget_rules = [r for r in load_custom_rules(target) if r.per_session is not None]
    except GrimoirePolicyError as exc:
        detail = f"_grimoire/standard/policies.yaml invalide : {exc}"
        results.append({"name": "policy_budget_guard", "passed": False, "detail": detail})
        if fmt != "json":
            console.print(f"  [red]FAIL[/red]  {detail}")
        return

    blocking_global = [
        r.id for r in budget_rules if r.tool_pattern == "*" and r.verdict_on_match is VerdictKind.BLOCK
    ]
    if blocking_global:
        detail = (
            f"budget global bloquant ({', '.join(blocking_global)}) : une règle `per_session` sans "
            "`tool_pattern` en `verdict_on_match: block` refuse tout outil de la session une fois "
            "le plafond atteint — préférez un `tool_pattern` ciblé ou `verdict_on_match: warn` "
            "(voir docs/hosts.md)"
        )
        results.append({"name": "policy_budget_guard", "passed": True, "detail": detail, "level": "warn"})
        if fmt != "json":
            console.print(f"  [yellow]WARN[/yellow]  {detail}")
    elif budget_rules:
        detail = "Aucun budget de session globalement bloquant."
        results.append({"name": "policy_budget_guard", "passed": True, "detail": detail})
        if fmt != "json":
            console.print(f"  [green]OK[/green]  {detail}")

    # Relapse of the 2026-09-12 incident, found 2026-09-14 on Grimoire-Forge
    # (issue #481): `max_duration_min` measures the duration since
    # `SessionStart`, not "time actually spent working" — a Claude Code
    # session left open for a few days (weekends, a paused task) crosses even
    # a generous-looking window long before any real runaway. 2880 min (48h)
    # is the threshold below which that is a live risk for a `block` verdict
    # with no `tool_pattern` to narrow its blast radius; the repair exemption
    # (`grimoire.policies.temporal._is_repair_exempt`) limits the damage but a
    # project should still see this named, exactly like `blocking_global`.
    short_duration_global = [
        r.id
        for r in budget_rules
        if r.tool_pattern == "*"
        and r.verdict_on_match is VerdictKind.BLOCK
        and r.per_session is not None
        and r.per_session.max_duration_min is not None
        and r.per_session.max_duration_min < 2880
    ]
    if short_duration_global:
        detail = (
            f"fenêtre de durée courte ({', '.join(short_duration_global)}) : `max_duration_min` "
            "< 2880 (48h) sans `tool_pattern`, en `verdict_on_match: block` — une session Claude "
            "Code laissée ouverte plusieurs jours dépasse cette fenêtre bien avant toute vraie "
            "dérive (voir docs/hosts.md)"
        )
        results.append({"name": "policy_budget_duration_guard", "passed": True, "detail": detail, "level": "warn"})
        if fmt != "json":
            console.print(f"  [yellow]WARN[/yellow]  {detail}")
