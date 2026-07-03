"""Template resolver — placeholder substitution for skill and agent templates.

Inspired by gstack's ``{{PREAMBLE}}`` injection system.  Resolves
template variables inside ``.md`` files and skill definitions.

Supports the following placeholders:

- ``{{PREAMBLE}}``         — Full preamble block (session, learnings, telemetry, vitals)
- ``{{LEARNINGS}}``        — Operational learnings only (top N by confidence)
- ``{{SESSION_CHAIN}}``    — Recent session chain entries
- ``{{TELEMETRY}}``        — Skill telemetry stats
- ``{{PROJECT_NAME}}``     — Project name from project-context.yaml
- ``{{TIMESTAMP}}``        — Current UTC timestamp
- ``{{SKILL_NAME}}``       — Name of the target skill (if provided)

Usage::

    from grimoire.core.template_resolver import TemplateResolver

    resolver = TemplateResolver(project_root=Path("."))
    rendered = resolver.resolve(template_text, skill="grimoire-tdd")
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["TemplateResolver"]

TEMPLATE_RESOLVER_VERSION = "1.0.0"

_VARIABLE_PATTERN = re.compile(r"\{\{([A-Z_]+)\}\}")


class TemplateResolver:
    """Resolve ``{{VARIABLE}}`` placeholders in template strings.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()
        self._cache: dict[str, str] = {}

    def resolve(
        self,
        template: str,
        *,
        skill: str = "",
        agent: str = "",
        extra_vars: dict[str, str] | None = None,
    ) -> str:
        """Replace all ``{{VARIABLE}}`` placeholders in *template*.

        Parameters
        ----------
        template :
            Template text with optional ``{{VARIABLE}}`` placeholders.
        skill :
            Target skill name for preamble filtering.
        agent :
            Target agent name for context enrichment.
        extra_vars :
            Additional variable overrides (keys without braces).

        Returns
        -------
        str
            Resolved text.  Unknown variables are left unchanged.
        """
        ctx = self._build_context(skill=skill, agent=agent, extra_vars=extra_vars)

        def _replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return ctx.get(var_name, match.group(0))

        return _VARIABLE_PATTERN.sub(_replacer, template)

    def resolve_file(
        self,
        path: Path,
        *,
        skill: str = "",
        agent: str = "",
        extra_vars: dict[str, str] | None = None,
    ) -> str:
        """Read a file and resolve placeholders.

        Returns empty string if file doesn't exist.
        """
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        return self.resolve(text, skill=skill, agent=agent, extra_vars=extra_vars)

    def clear_cache(self) -> None:
        """Clear cached variable values."""
        self._cache.clear()

    # ── Context builder ───────────────────────────────────────────────────

    def _build_context(
        self,
        *,
        skill: str,
        agent: str,
        extra_vars: dict[str, str] | None,
    ) -> dict[str, str]:
        """Build the variable context for substitution."""
        ctx: dict[str, str] = {}

        # Static variables
        ctx["TIMESTAMP"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        ctx["SKILL_NAME"] = skill
        ctx["AGENT_NAME"] = agent

        # Project name
        ctx["PROJECT_NAME"] = self._get_project_name()

        # Preamble (full block)
        ctx["PREAMBLE"] = self._get_preamble(skill=skill, agent=agent)

        # Individual sections
        ctx["LEARNINGS"] = self._get_learnings(skill=skill)
        ctx["SESSION_CHAIN"] = self._get_session_chain()
        ctx["TELEMETRY"] = self._get_telemetry(skill=skill)

        # Extra overrides
        if extra_vars:
            ctx.update(extra_vars)

        return ctx

    # ── Lazy loaders (cached) ─────────────────────────────────────────────

    def _get_project_name(self) -> str:
        if "PROJECT_NAME" in self._cache:
            return self._cache["PROJECT_NAME"]
        try:
            from grimoire.tools._common import load_yaml

            data = load_yaml(self._root / "project-context.yaml")
            name = data.get("project", {}).get("name", "unknown")
        except Exception:
            name = "unknown"
        self._cache["PROJECT_NAME"] = name
        return name

    def _get_preamble(self, *, skill: str, agent: str) -> str:
        try:
            from grimoire.core.preamble import PreambleBuilder

            builder = PreambleBuilder(self._root)
            return builder.build(skill=skill, agent=agent)
        except Exception:
            logger.debug("Preamble build failed", exc_info=True)
            return ""

    def _get_learnings(self, *, skill: str) -> str:
        try:
            from grimoire.core.preamble import PreambleBuilder, PreambleConfig

            cfg = PreambleConfig(
                include_vitals=False,
                include_session_chain=False,
                include_telemetry=False,
            )
            builder = PreambleBuilder(self._root, config=cfg)
            return builder.build(skill=skill)
        except Exception:
            return ""

    def _get_session_chain(self) -> str:
        try:
            from grimoire.core.preamble import PreambleBuilder, PreambleConfig

            cfg = PreambleConfig(
                include_vitals=False,
                include_learnings=False,
                include_telemetry=False,
            )
            builder = PreambleBuilder(self._root, config=cfg)
            return builder.build()
        except Exception:
            return ""

    def _get_telemetry(self, *, skill: str) -> str:
        try:
            from grimoire.core.preamble import PreambleBuilder, PreambleConfig

            cfg = PreambleConfig(
                include_vitals=False,
                include_learnings=False,
                include_session_chain=False,
            )
            builder = PreambleBuilder(self._root, config=cfg)
            return builder.build(skill=skill)
        except Exception:
            return ""

    # ── Utility ───────────────────────────────────────────────────────────

    @staticmethod
    def list_variables(template: str) -> list[str]:
        """Return all ``{{VARIABLE}}`` names found in *template*."""
        return _VARIABLE_PATTERN.findall(template)
