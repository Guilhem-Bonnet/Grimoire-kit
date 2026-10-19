"""Compose the dynamic 'Next Steps' panel shown after `grimoire init`/`up`.

Replaces the old static panel (onboarding audit 2026-09-18, constat #3):
identical text on all 6 runs the audit reproduced (3 stacks x plain/express),
regardless of archetype, backend or memory profile. This names what was
installed and why, then offers at most three next actions chosen from the
project's real state, plus one line naming a capability the project has not
exploited yet — never more, per the audit's own acceptance bar.

Kept free of Rich: it returns plain strings (with Rich markup tags, since the
caller already renders through a Rich ``Console``) so it can be tested as a
plain data structure instead of parsing rendered console output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.project_capabilities import unexploited_hints

_MAX_ACTIONS = 3

#: Capability categories named in the generic closing line — a project that
#: has exploited none of them yet gets pointed at the full command surface
#: (issue onboarding-60s, deliverable 3: "this project also has...").
_ALL_CAPABILITY_LABELS: dict[str, str] = {
    "cockpit": "cockpit",
    "standard": "governance/standard",
    "memory": "semantic memory",
}


@dataclass(frozen=True, slots=True)
class NextStepsPanel:
    """Everything the post-install report needs to render — no more than
    three actions, ever."""

    headline: str
    actions: tuple[str, ...]
    unexploited_line: str | None


def _archetype_action(resolved: ResolvedArchetype) -> str | None:
    """Name the auto-picked archetype's escape hatch — shown only when
    `resolve()` had to guess (decision 2026-09-18, Guilhem: it never guesses
    `minimal`, but an automatic pick still isn't the same as a deliberate
    choice, and the headline above already explains *why* it was picked)."""
    if resolved.is_composite or not resolved.is_best_guess:
        return None
    return (
        f"Not sure `{resolved.archetype}` fits? Discover a better match: "
        "`grimoire init --interactive`, or pick one directly: `grimoire up -a <archetype>`"
    )


def build_next_steps(
    target: Path,
    *,
    resolved: ResolvedArchetype,
    backend: str,
    no_cockpit: bool = False,
) -> NextStepsPanel:
    """Build the panel body for one freshly initialised (or refreshed) project."""
    info_reason = resolved.reason
    headline = f"Archetype [bold]{resolved.archetype}[/bold] installed ({info_reason}) — memory {backend}."

    hints = unexploited_hints(target, archetype=resolved.archetype, backend=backend, no_cockpit=no_cockpit)
    hint_by_command = {h.command: h for h in hints}

    candidates: list[str] = []

    archetype_action = _archetype_action(resolved)
    if archetype_action:
        candidates.append(archetype_action)

    if "grimoire cockpit" in hint_by_command:
        candidates.append("Open the multi-project cockpit: `grimoire cockpit`")

    standard_hint = next((h for h in hints if h.command.startswith("grimoire standard")), None)
    if standard_hint:
        candidates.append(f"{standard_hint.label.capitalize()}: `{standard_hint.command}`")

    if "grimoire memory up --profile standard --apply" in hint_by_command:
        candidates.append(
            "Upgrade to a richer memory profile: `grimoire memory up --profile standard --apply`"
        )

    # Always-available fallback so a fully-specialized, fully-governed project
    # still gets three concrete actions instead of trailing off short.
    candidates.append(
        'Run your first governed task: `grimoire task add "..."` '
        "then `grimoire standard gate check`"
    )

    actions = tuple(dict.fromkeys(candidates))[:_MAX_ACTIONS]

    # Copilot review on PR #617: this used to call `cockpit_registered(target)`
    # a second time here, after `unexploited_hints()` above already performed
    # that same best-effort registry probe — duplicate I/O that could also
    # disagree with the hints it just computed (e.g. the registry changing,
    # or one call failing, between the two reads). "grimoire cockpit" is
    # exploited/absent from the hint list under exactly the same condition
    # (`not no_cockpit and not cockpit_registered(target)`), so deriving from
    # the already-computed `hint_by_command` reuses that one probe instead.
    exploited_categories = {
        "cockpit": no_cockpit or "grimoire cockpit" not in hint_by_command,
        "standard": not any(h.command.startswith("grimoire standard") for h in hints),
        "memory": backend not in ("lexical", "local"),
    }
    missing = [label for key, label in _ALL_CAPABILITY_LABELS.items() if not exploited_categories[key]]
    unexploited_line = (
        f"This project also has: {', '.join(missing)} — `grimoire --all --help` to see everything."
        if missing
        else None
    )

    return NextStepsPanel(headline=headline, actions=actions, unexploited_line=unexploited_line)
