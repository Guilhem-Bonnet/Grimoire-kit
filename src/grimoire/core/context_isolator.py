"""Context isolator — sub-agent context scoping to reduce hallucination.

Inspired by superpowers' sub-agent isolation and claude-mem's token
budgets.  When dispatching a task to a sub-agent, instead of passing
the full context, this module selects only the relevant items and
trims to a token budget.

The isolator scores context items by relevance to the task and
returns a ranked, budget-aware context package.

Usage::

    from grimoire.core.context_isolator import ContextIsolator

    isolator = ContextIsolator(project_root=Path("."))
    package = isolator.isolate(
        agent="dev",
        task="implement user login endpoint",
        budget_tokens=4000,
    )
    # → ContextPackage with ranked files, learnings, memory entries
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ContextIsolator", "ContextItem", "ContextPackage"]

CONTEXT_ISOLATOR_VERSION = "1.0.0"

# ── Approximate token estimation ─────────────────────────────────────────────

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ContextItem:
    """A single context item with relevance score."""

    source: str  # file, learning, memory, session
    key: str  # identifier (path, learning key, etc.)
    content: str
    relevance: float = 0.0  # 0.0–1.0
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "key": self.key,
            "relevance": round(self.relevance, 3),
            "tokens": self.tokens,
        }


@dataclass(frozen=True, slots=True)
class ContextPackage:
    """Scoped context for a sub-agent invocation."""

    agent: str
    task: str
    items: tuple[ContextItem, ...]
    budget_tokens: int
    used_tokens: int
    trimmed: bool  # True if some items were dropped

    @property
    def item_count(self) -> int:
        return len(self.items)

    def to_markdown(self) -> str:
        """Render as Markdown block for LLM injection."""
        lines: list[str] = [
            "<!-- CONTEXT:START -->",
            f"**Agent**: {self.agent} | **Budget**: {self.used_tokens}/{self.budget_tokens} tokens",
            "",
        ]
        for item in self.items:
            lines.append(f"### [{item.source}] {item.key} (relevance: {item.relevance:.0%})")
            lines.append("")
            lines.append(item.content)
            lines.append("")
        lines.append("<!-- CONTEXT:END -->")
        return "\n".join(lines)


# ── Agent domain keywords ────────────────────────────────────────────────────

_AGENT_DOMAINS: dict[str, tuple[str, ...]] = {
    "dev": ("implement", "code", "function", "class", "module", "test", "bug", "fix", "refactor"),
    "qa": ("test", "quality", "coverage", "assertion", "fixture", "mock", "validate"),
    "architect": ("architecture", "design", "pattern", "coupling", "dependency", "ADR", "structure"),
    "pm": ("requirement", "PRD", "feature", "user story", "priority", "roadmap"),
    "sm": ("sprint", "backlog", "story", "velocity", "standup", "retrospective"),
    "tech-writer": ("documentation", "doc", "README", "guide", "tutorial", "reference"),
    "tea": ("test strategy", "fixture", "ATDD", "CI", "quality gate", "risk"),
    "analyst": ("analysis", "research", "domain", "stakeholder", "business rule"),
}


# ── Core implementation ──────────────────────────────────────────────────────


class ContextIsolator:
    """Selects and trims context for sub-agent invocations.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root

    def isolate(
        self,
        agent: str,
        task: str,
        *,
        budget_tokens: int = 4000,
        include_learnings: bool = True,
        include_memory: bool = True,
    ) -> ContextPackage:
        """Build a scoped context package for a sub-agent.

        Parameters
        ----------
        agent :
            Sub-agent identifier (e.g. "dev", "qa", "architect").
        task :
            Description of the task being dispatched.
        budget_tokens :
            Maximum token budget for the context.
        include_learnings :
            Whether to include operational learnings.
        include_memory :
            Whether to include shared memory.
        """
        candidates: list[ContextItem] = []

        if include_learnings:
            candidates.extend(self._gather_learnings(task, agent))

        if include_memory:
            candidates.extend(self._gather_memory(task))

        # Score and rank
        scored = [self._score_item(item, task, agent) for item in candidates]
        scored.sort(key=lambda it: it.relevance, reverse=True)

        # Trim to budget
        selected: list[ContextItem] = []
        used = 0
        trimmed = False
        for item in scored:
            tokens = item.tokens or _estimate_tokens(item.content)
            if used + tokens > budget_tokens:
                trimmed = True
                continue
            selected.append(ContextItem(
                source=item.source,
                key=item.key,
                content=item.content,
                relevance=item.relevance,
                tokens=tokens,
            ))
            used += tokens

        return ContextPackage(
            agent=agent,
            task=task,
            items=tuple(selected),
            budget_tokens=budget_tokens,
            used_tokens=used,
            trimmed=trimmed,
        )

    def _gather_learnings(self, task: str, agent: str) -> list[ContextItem]:
        """Load learnings from JSONL and create ContextItems."""
        path = self._root / "_grimoire/_memory/learnings/operational.jsonl"
        if not path.is_file():
            return []
        items: list[ContextItem] = []
        try:
            for line in path.read_text(encoding="utf-8").strip().splitlines():
                entry = json.loads(line)
                content = f"**{entry.get('key', '')}**: {entry.get('insight', '')}"
                items.append(ContextItem(
                    source="learning",
                    key=entry.get("key", ""),
                    content=content,
                    tokens=_estimate_tokens(content),
                ))
        except (OSError, json.JSONDecodeError):
            logger.debug("Failed to load learnings for isolation")
        return items

    def _gather_memory(self, task: str) -> list[ContextItem]:
        """Load shared memory context."""
        path = self._root / "_grimoire/_memory/shared-context.md"
        if not path.is_file():
            path = self._root / "_grimoire-runtime/_memory/shared-context.md"
        if not path.is_file():
            return []
        try:
            content = path.read_text(encoding="utf-8")
            # Split by headings for granular selection
            sections = re.split(r"(?m)^(##\s+.+)$", content)
            items: list[ContextItem] = []
            i = 1
            while i < len(sections) - 1:
                heading = sections[i].strip()
                body = sections[i + 1].strip()
                if body:
                    full = f"{heading}\n\n{body}"
                    items.append(ContextItem(
                        source="memory",
                        key=heading.lstrip("#").strip(),
                        content=full,
                        tokens=_estimate_tokens(full),
                    ))
                i += 2
            return items
        except OSError:
            return []

    def _score_item(self, item: ContextItem, task: str, agent: str) -> ContextItem:
        """Score a context item by relevance to the task and agent."""
        score = 0.0
        task_lower = task.lower()
        content_lower = item.content.lower()
        key_lower = item.key.lower()

        # Keyword overlap between task and content
        task_words = set(re.findall(r"\w{3,}", task_lower))
        content_words = set(re.findall(r"\w{3,}", content_lower))
        if task_words:
            overlap = len(task_words & content_words) / len(task_words)
            score += overlap * 0.5

        # Agent domain bonus
        domain_keywords = _AGENT_DOMAINS.get(agent, ())
        for kw in domain_keywords:
            if kw in content_lower or kw in key_lower:
                score += 0.1
                break

        # Key matches task words directly → strong signal
        key_words = set(re.findall(r"\w{3,}", key_lower))
        if task_words & key_words:
            score += 0.3

        return ContextItem(
            source=item.source,
            key=item.key,
            content=item.content,
            relevance=min(1.0, score),
            tokens=item.tokens,
        )

    @staticmethod
    def agent_domains() -> dict[str, tuple[str, ...]]:
        """Return the known agent domain keyword map."""
        return dict(_AGENT_DOMAINS)
