"""Skill dispatcher — automatic skill invocation with preamble injection.

Provides a high-level API for the SOG to invoke skills programmatically:
1. Resolves the skill's SKILL.md path
2. Injects dynamic preamble (learnings, session chain, telemetry)
3. Returns the enriched skill content ready for agent processing
4. Records telemetry after execution

Usage::

    from grimoire.core.skill_dispatcher import SkillDispatcher

    dispatcher = SkillDispatcher(project_root=Path("."))
    enriched = dispatcher.prepare("grimoire-tdd")
    # → SKILL.md content with injected preamble
    dispatcher.complete("grimoire-tdd", outcome="success", duration_s=15.0)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SkillDispatcher", "SkillInvocation"]

SKILL_DISPATCHER_VERSION = "1.0.0"

_SKILL_SEARCH_DIRS = [
    ".github/skills",
    "_grimoire-runtime/skills",
    "_bmad/skills",
]


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    """Metadata for a skill invocation."""

    skill: str
    path: Path | None
    found: bool
    preamble_injected: bool
    template_resolved: bool
    content_length: int
    timestamp: str


class SkillDispatcher:
    """Discovers skills, injects preamble, records telemetry.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    hook_manager :
        Optional :class:`~grimoire.core.hooks.HookManager` for lifecycle
        hooks.  When provided, ``prepare()`` fires ``pre_tool_use`` and
        ``complete()`` fires ``post_tool_use``.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        hook_manager: Any | None = None,
    ) -> None:
        self._root = project_root.resolve()
        self._hooks = hook_manager

    def discover(self, skill_name: str) -> Path | None:
        """Find SKILL.md for a given skill name.

        Searches in standard skill directories.  Returns None if not found.
        """
        for search_dir in _SKILL_SEARCH_DIRS:
            candidate = self._root / search_dir / skill_name / "SKILL.md"
            if candidate.exists():
                return candidate
        return None

    def list_skills(self) -> list[str]:
        """Return all available skill names."""
        skills: list[str] = []
        for search_dir in _SKILL_SEARCH_DIRS:
            base = self._root / search_dir
            if not base.exists():
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / "SKILL.md").exists():
                    skills.append(child.name)
        return skills

    def prepare(
        self,
        skill_name: str,
        *,
        inject_preamble: bool = True,
        resolve_templates: bool = True,
        extra_vars: dict[str, str] | None = None,
    ) -> tuple[str, SkillInvocation]:
        """Prepare a skill for execution.

        Returns
        -------
        tuple[str, SkillInvocation]
            The enriched skill content, and metadata about the invocation.
        """
        path = self.discover(skill_name)
        if path is None:
            invocation = SkillInvocation(
                skill=skill_name,
                path=None,
                found=False,
                preamble_injected=False,
                template_resolved=False,
                content_length=0,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            return "", invocation

        # Fire pre_tool_use hook
        self._fire_hook("pre_tool_use", tool=skill_name, status="preparing")

        content = path.read_text(encoding="utf-8")
        preamble_injected = False
        template_resolved = False

        # Inject preamble at top (after frontmatter if present)
        if inject_preamble:
            preamble = self._build_preamble(skill_name)
            if preamble:
                content = self._inject_after_frontmatter(content, preamble)
                preamble_injected = True

        # Resolve {{VARIABLE}} placeholders
        if resolve_templates:
            try:
                from grimoire.core.template_resolver import TemplateResolver

                resolver = TemplateResolver(self._root)
                content = resolver.resolve(
                    content,
                    skill=skill_name,
                    extra_vars=extra_vars,
                )
                template_resolved = True
            except Exception:
                logger.debug("Template resolution failed for %s", skill_name, exc_info=True)

        invocation = SkillInvocation(
            skill=skill_name,
            path=path,
            found=True,
            preamble_injected=preamble_injected,
            template_resolved=template_resolved,
            content_length=len(content),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        return content, invocation

    def complete(
        self,
        skill_name: str,
        *,
        outcome: str = "success",
        duration_s: float = 0.0,
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record completion telemetry for a skill invocation."""
        try:
            from grimoire.core.telemetry import Telemetry

            telem = Telemetry(self._root)
            telem.record_skill(
                skill_name,
                outcome=outcome,
                duration_s=duration_s,
                message=message,
                metadata=metadata,
            )
        except Exception:
            logger.debug("Telemetry recording failed for %s", skill_name, exc_info=True)

        # Fire post_tool_use hook
        self._fire_hook("post_tool_use", tool=skill_name, status=outcome, duration_s=duration_s)

    # ── Internal ──────────────────────────────────────────────────────────

    def _build_preamble(self, skill: str) -> str:
        """Build the preamble for the given skill."""
        try:
            from grimoire.core.preamble import PreambleBuilder

            builder = PreambleBuilder(self._root)
            return builder.build(skill=skill)
        except Exception:
            return ""

    @staticmethod
    def _inject_after_frontmatter(content: str, preamble: str) -> str:
        """Insert preamble after YAML frontmatter or at the top."""
        if content.startswith("---"):
            # Find end of frontmatter
            end_idx = content.find("---", 3)
            if end_idx != -1:
                end_idx = content.index("\n", end_idx) + 1
                return content[:end_idx] + "\n" + preamble + "\n\n" + content[end_idx:]
        return preamble + "\n\n" + content

    def _fire_hook(
        self,
        hook: str,
        *,
        tool: str = "",
        status: str = "",
        duration_s: float = 0.0,
    ) -> None:
        """Fire a lifecycle hook if HookManager is available."""
        if self._hooks is None:
            return
        try:
            from grimoire.core.hooks import HookContext

            ctx = HookContext(tool=tool, status=status, duration_s=duration_s)
            self._hooks.trigger(hook, ctx)
        except Exception:
            logger.debug("Hook firing failed for %s/%s", hook, tool, exc_info=True)
