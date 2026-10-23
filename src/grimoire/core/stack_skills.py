"""Which ``archetypes/stack/skills/stack-*.md`` a project's own detection warrants.

``archetypes/stack/skills/stack-*.md`` used to attach in block to
``stack-engineer`` regardless of what the project actually uses (issue
#375's own defect, found while building the on-demand expertise catalogue of
issue #616): a bare Python project selecting the ``stack`` archetype paid for
six skills it never touches, transversal at project scope the moment they were
not all referenced by name in the agent's ``skills:`` frontmatter.

Extracted from :mod:`grimoire.core.scaffold` (R2 size ratchet, 1500 lines)
when the onboarding lot (#617, #618, #619) landed together.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.scanner import ScanResult

#: Keyed by `grimoire.core.scanner.StackScanner`'s own detection names (issue
#: #616 reuses that scanner rather than inventing a second one) —
#: ``"kubernetes"`` is the one name that does not match its skill slug
#: (``stack-k8s``, not ``stack-kubernetes``).
STACK_SKILL_BY_DETECTION: dict[str, str] = {
    "python": "stack-python",
    "go": "stack-go",
    "typescript": "stack-typescript",
    "docker": "stack-docker",
    "terraform": "stack-terraform",
    "ansible": "stack-ansible",
    "kubernetes": "stack-k8s",
}


def detected_stack_skill_slugs(scan: ScanResult | None, agents_dir: Path) -> tuple[str, ...]:
    """Slugs of ``stack-*.md`` this project's detection warrants attaching by default.

    Anything not detected here is never lost — its body lives in the kit
    package (not only in a project's own copy), so ``grimoire expertise add
    <id>`` (:mod:`grimoire.core.expertises`) can attach it later, on request,
    the same on-demand mechanism issue #616 gives every other language,
    pattern and cloud provider. Order follows :data:`STACK_SKILL_BY_DETECTION`
    (deterministic, not scan order) so the rendered ``skills:`` list is stable
    across runs on the same project.

    ``grimoire up`` builds its scaffolder with ``scan=None`` (it refreshes the
    kit tier without re-detecting the stack). Recomputing from an absent scan
    would read as "nothing detected" and silently regress an already-installed
    project's kit-tier default to an empty list on every update. When there is
    no scan to detect from, this keeps whatever the project's own kit-tier
    ``stack-engineer.md`` (under *agents_dir*) already declares instead of
    recomputing — refresh never narrows a selection it didn't itself re-derive.
    """
    if scan is not None:
        detected = {d.name for d in scan.stacks}
        return tuple(slug for name, slug in STACK_SKILL_BY_DETECTION.items() if name in detected)
    existing = agents_dir / "stack-engineer.md"
    if not existing.is_file():
        return ()
    from grimoire.hosts.collect import parse_frontmatter

    meta, _ = parse_frontmatter(existing.read_text(encoding="utf-8"))
    # `_safe_skill_source` (tout consommateur) revalide forme + confine le chemin (CodeQL 599/600).
    known_slugs = set(STACK_SKILL_BY_DETECTION.values())
    return tuple(str(s) for s in (meta.get("skills") or []) if str(s) in known_slugs)
