"""The declarative rule-authoring surface: ``_grimoire/standard/policies.yaml``.

``grimoire.policies.engine`` ships four built-in rules plus one
(``tool_policy.py``'s ``destructive-requires-confirmation``) — none of them
temporal, and none of them a project can change without editing kit source.
Issue #429 (point 3 of the 2026-09-12 positioning audit) needs projects to be
able to *declare* budgets, prior-approval and cooldown rules, in the same
:class:`~grimoire.policies.schemas.PolicyRule` shape the engine already
understands — see that module's docstring for the field list.

This is the file that makes that possible: a project drops a ``rules:`` list
under ``_grimoire/standard/policies.yaml`` (next to ``standard-profile.yaml``
and ``task-board.yaml``, the two files :mod:`grimoire.core.standard_state`
already reads from the same directory) and every entry becomes a
:class:`~grimoire.policies.schemas.PolicyRule` via
:meth:`~grimoire.policies.schemas.PolicyRule.from_dict` — same validation,
same named error on an unknown key, whether the rule came from this file or
from Python source.

Example::

    rules:
      - id: rm-requires-approval
        description: "rm/rf demande une confirmation, une fois par session"
        action_kinds: []
        mutation_classes: []
        risk_profiles: []
        verdict_on_match: warn
        reason_template: "Suppression demandant une approbation explicite"
        tool_pattern: "Bash(rm:*)"
        require_approval: true
        cooldown_after: {pattern: "Bash(rm:*)", count: 5, minutes: 10}
      - id: session-write-budget
        description: "Pas plus de 50 écritures par session"
        action_kinds: []
        mutation_classes: []
        risk_profiles: []
        verdict_on_match: block
        reason_template: "Budget d'écritures de session dépassé"
        per_session: {max_writes: 50}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.policies.schemas import PolicyRule

#: Next to ``standard-profile.yaml``/``task-board.yaml`` — the standard's own
#: directory, not a new top-level convention.
RULES_RELPATH = STANDARD_DIR / "policies.yaml"


def _compute_raw_rules(path: Path) -> list[dict[str, Any]]:
    """The ``rules:`` list, as plain dicts — never raises, unlike ``PolicyRule.from_dict``.

    ``ruamel.yaml`` is imported here, not at module scope, for the same
    reason as ``grimoire.core.standard_state._load_mapping``: a warm cache
    (the common case once this file exists and hasn't changed) means this
    function, and the parser it needs, never run.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(path)
    except (OSError, ValueError, YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    rules_raw = data.get("rules", [])
    if not isinstance(rules_raw, list):
        return []
    return [rule for rule in rules_raw if isinstance(rule, dict)]


def load_custom_rules(project_root: Path) -> tuple[PolicyRule, ...]:
    """Rules declared in ``_grimoire/standard/policies.yaml``, or ``()`` when there are none.

    Disk-cached the same way as :mod:`grimoire.core.standard_state` (issue
    #422): every ``grimoire-hook`` invocation is a fresh process, so a
    per-process cache buys nothing on the path this exists for — the parsed
    YAML has to survive *between* processes, which is exactly what that
    module's fingerprint-keyed JSON cache under ``_grimoire-output/.runs/``
    does. A missing file is not an error — it means "no custom rules" — but
    a rule that fails to parse *is*: :meth:`PolicyRule.from_dict` raises
    :class:`~grimoire.core.exceptions.GrimoirePolicyError` by name on an
    unknown key or an invalid enum value, and this function does not catch
    it — the caller (a hook decision, or ``grimoire policies status``) is
    what decides how a malformed rule file should degrade.
    """
    root = project_root.resolve()
    path = root / RULES_RELPATH
    if not path.is_file():
        return ()
    from grimoire.core.standard_state import _cached_or_fresh

    raw_rules = _cached_or_fresh(root, "policy_rules", RULES_RELPATH, lambda: _compute_raw_rules(path))
    if not isinstance(raw_rules, list):
        return ()
    return tuple(PolicyRule.from_dict(raw) for raw in raw_rules)
