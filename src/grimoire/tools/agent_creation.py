"""Create a real, installed custom agent file in the overrides tier.

The one write path fixed by issue #367: render the ``minimal`` archetype's
``custom-agent.tpl.md`` into ``_grimoire/overrides/agents/<agent_id>.md`` —
the tier ``doctor`` and the routing map actually resolve — refusing when an
agent with this id already exists in any tier, or when the id is not a safe
filename.

Extracted from ``grimoire_add_agent`` (``src/grimoire/mcp/server.py``) so the
artifact-proposals engine (issue #395) writes through the exact same path
rather than a second, divergent one: only the fill-in values differ. The MCP
tool leaves the employment clause (``use_when``/``dont_use_when``/``tools``)
as a fill-in-the-blank placeholder for a human to complete by hand — calling
it with no keyword arguments reproduces that behaviour unchanged, which is
what ``tests/unit/mcp/test_server.py::TestAddAgent`` still exercises. A
proposal instead already knows what the miss traces observed, so it supplies
non-empty text and gets it substituted for real.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: Safe as a filename and as an XML/YAML identifier — same rule the MCP tool
#: enforced before this extraction.
AGENT_ID_RE = re.compile(r"^[\w-]+$")


class AgentCreationError(Exception):
    """A refusal to create an agent file — always explainable, never a crash.

    ``extra`` carries the same structured fields the MCP tool used to inline
    into its JSON error payload (``agent_id``, ``path``) so callers keep
    exactly the same error shape after the extraction.
    """

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


def create_agent_file(
    project_root: Path,
    agent_id: str,
    *,
    agent_role: str = "",
    use_when: str = "",
    dont_use_when: str = "",
    tool_boundary: str = "",
    tools: str = "",
    skills: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Render and write ``<agent_id>.md`` into the project's overrides tier.

    Raises :class:`AgentCreationError` for every refusal (unsafe id,
    duplicate, missing project, missing template, write failure) — never
    returns a value that could be mistaken for a partial success. Every
    keyword argument left at its default (``""`` / ``()``) leaves the
    matching ``{{placeholder}}`` in the template untouched, per
    :func:`grimoire.core.scaffold._render_placeholders` — a caller that
    supplies nothing gets exactly the fill-in-the-blank file the template
    ships as.
    """
    from grimoire.archetypes import bundled_path
    from grimoire.core import layout
    from grimoire.core.scaffold import _render_placeholders

    if not AGENT_ID_RE.fullmatch(agent_id):
        raise AgentCreationError(
            f"Invalid agent_id {agent_id!r}: must match [\\w-]+ (safe as a filename)."
        )

    target = project_root.resolve()
    config_path = target / "project-context.yaml"
    if not config_path.is_file():
        raise AgentCreationError("No project-context.yaml found")

    existing = layout.installed_agents(target)
    if agent_id in existing:
        _, existing_path = existing[agent_id]
        raise AgentCreationError(
            f"Agent '{agent_id}' already exists at {existing_path}",
            agent_id=agent_id,
            path=str(existing_path),
        )

    template_path = bundled_path() / "minimal" / "agents" / "custom-agent.tpl.md"
    if not template_path.is_file():
        raise AgentCreationError(f"Custom agent template not found at {template_path}")

    agent_name = agent_id.replace("-", " ").replace("_", " ").title()
    variables = {
        "agent_tag": agent_id,
        "agent_name": agent_name,
        "agent_role": agent_role or agent_name,
        "agent_icon": "sparkles",
    }
    # Only known-non-empty values are substituted — an empty string left out
    # of `variables` keeps the template's own `{{use_when}} — décrivez…`
    # fill-in-the-blank text intact, exactly as `grimoire_add_agent` always
    # rendered it before this extraction.
    if use_when:
        variables["use_when"] = use_when
    if dont_use_when:
        variables["dont_use_when"] = dont_use_when
    if tool_boundary:
        variables["tool_boundary"] = tool_boundary
    if tools:
        variables["tools"] = tools

    rendered = _render_placeholders(template_path.read_text(encoding="utf-8"), variables)
    if skills:
        # The template has no `{{skills}}` placeholder — an agent skills a
        # fresh specialist owns is the exception, not the rule the template
        # is written for. Inserted as a plain frontmatter line right after
        # `tools:`, the one line every rendering of this template always has.
        skills_line = "skills: [" + ", ".join(json.dumps(s) for s in skills) + "]\n"
        rendered = re.sub(r'(\ntools: "[^\n]*"\n)', r"\1" + skills_line, rendered, count=1)

    agents_dir = layout.overrides_dir(target) / layout.AGENTS_SUBDIR
    dest = agents_dir / f"{agent_id}.md"
    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise AgentCreationError(str(exc)) from exc

    return {"status": "created", "agent_id": agent_id, "path": str(dest), "tier": "overrides"}
