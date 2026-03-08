"""Context Router — intelligent context loading planner for agents.

Analyses which files an agent MUST, SHOULD, or CAN skip loading,
based on priority, token budget, and task relevance.

Priority levels:
- P0 ALWAYS: persona, agent-base, rules
- P1 SESSION: shared-context, decisions, learnings
- P2 TASK: files related to the current task/story
- P3 LAZY: archives, repo-map, digests
- P4 ON_REQUEST: large files explicitly requested

Usage::

    from bmad.tools.context_router import ContextRouter

    cr = ContextRouter(Path("."))
    plan = cr.run(agent="analyst", model="copilot")
    print(plan.loaded_tokens, plan.status)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad.tools._common import BmadTool, estimate_tokens

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL_WINDOWS: dict[str, int] = {
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o3": 200_000,
    "codex": 192_000,
    "gemini-1.5-pro": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "copilot": 200_000,
    "codestral": 32_000,
    "llama3": 8_000,
    "mistral": 32_000,
}

DEFAULT_MODEL = "copilot"
CHARS_PER_TOKEN = 4
WARNING_THRESHOLD = 0.60
CRITICAL_THRESHOLD = 0.80


# ── Priority ──────────────────────────────────────────────────────────────────

class Priority:
    P0_ALWAYS = 0
    P1_SESSION = 1
    P2_TASK = 2
    P3_LAZY = 3
    P4_ON_REQUEST = 4

    LABELS = {
        0: "P0-ALWAYS",
        1: "P1-SESSION",
        2: "P2-TASK",
        3: "P3-LAZY",
        4: "P4-ON_REQUEST",
    }


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class FileEntry:
    """A candidate file for context loading."""

    path: str
    priority: int
    estimated_tokens: int = 0
    reason: str = ""
    relevance_score: float = 1.0
    loaded: bool = False

    @property
    def priority_label(self) -> str:
        return Priority.LABELS.get(self.priority, f"P{self.priority}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "priority": self.priority_label,
            "tokens": self.estimated_tokens,
            "loaded": self.loaded,
            "reason": self.reason,
            "relevance": round(self.relevance_score, 2),
        }


@dataclass(slots=True)
class LoadPlan:
    """Calculated loading plan for an agent."""

    agent: str
    model: str
    model_window: int
    entries: list[FileEntry] = field(default_factory=list)
    total_tokens: int = 0
    loaded_tokens: int = 0
    skipped_tokens: int = 0
    recommendations: list[str] = field(default_factory=list)

    @property
    def usage_pct(self) -> float:
        return (self.loaded_tokens / self.model_window * 100) if self.model_window else 0

    @property
    def status(self) -> str:
        pct = self.usage_pct
        if pct >= CRITICAL_THRESHOLD * 100:
            return "CRITICAL"
        if pct >= WARNING_THRESHOLD * 100:
            return "WARNING"
        return "OK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "model": self.model,
            "model_window": self.model_window,
            "loaded_tokens": self.loaded_tokens,
            "total_tokens": self.total_tokens,
            "usage_pct": round(self.usage_pct, 1),
            "status": self.status,
            "files": [e.to_dict() for e in self.entries],
            "recommendations": self.recommendations,
        }


# ── File Discovery ───────────────────────────────────────────────────────────

def _file_tokens(path: Path) -> int:
    """Estimate token count for a file."""
    try:
        text = path.read_text(encoding="utf-8")
        return estimate_tokens(text)
    except OSError:
        return 0


def discover_context_files(project_root: Path, agent_tag: str) -> list[FileEntry]:
    """Discover and prioritize context files for an agent."""
    entries: list[FileEntry] = []
    mem = project_root / "_bmad" / "_memory"
    cfg = project_root / "_bmad" / "_config"

    # P0: Agent base + agent persona
    agent_base = cfg / "custom" / "agent-base.md"
    if agent_base.exists():
        entries.append(FileEntry(
            path=str(agent_base.relative_to(project_root)),
            priority=Priority.P0_ALWAYS,
            estimated_tokens=_file_tokens(agent_base),
            reason="Base protocol",
        ))

    # Find agent file matching tag
    for pattern in ["_bmad/_config/agents/*.md", "_bmad/_config/custom/*.md",
                     "_bmad/*/agents/*.md"]:
        for af in project_root.glob(pattern):
            if af.name == "agent-base.md":
                continue
            if agent_tag.lower() in af.stem.lower():
                entries.append(FileEntry(
                    path=str(af.relative_to(project_root)),
                    priority=Priority.P0_ALWAYS,
                    estimated_tokens=_file_tokens(af),
                    reason="Agent persona",
                ))
                break

    # P1: Session context
    p1_files = [
        (mem / "shared-context.md", "Project context"),
        (mem / "decisions-log.md", "Decisions history"),
        (mem / "failure-museum.md", "Failure patterns"),
    ]
    learnings = mem / "agent-learnings"
    if learnings.exists():
        for lf in learnings.glob("*.md"):
            if agent_tag.lower() in lf.stem.lower():
                p1_files.append((lf, f"Agent learnings ({lf.stem})"))

    for fpath, reason in p1_files:
        if fpath.exists():
            entries.append(FileEntry(
                path=str(fpath.relative_to(project_root)),
                priority=Priority.P1_SESSION,
                estimated_tokens=_file_tokens(fpath),
                reason=reason,
            ))

    # P2: Task-related (session state)
    session = mem / "session-state.md"
    if session.exists():
        entries.append(FileEntry(
            path=str(session.relative_to(project_root)),
            priority=Priority.P2_TASK,
            estimated_tokens=_file_tokens(session),
            reason="Session state",
        ))

    # P3: Lazy
    for name, reason in [("knowledge-digest.md", "Knowledge digest"),
                          ("network-topology.md", "Network topology")]:
        fpath = mem / name
        if fpath.exists():
            entries.append(FileEntry(
                path=str(fpath.relative_to(project_root)),
                priority=Priority.P3_LAZY,
                estimated_tokens=_file_tokens(fpath),
                reason=reason,
            ))

    # P4: Archives
    archives = mem / "archives"
    if archives.exists():
        for af in sorted(archives.glob("*.md")):
            entries.append(FileEntry(
                path=str(af.relative_to(project_root)),
                priority=Priority.P4_ON_REQUEST,
                estimated_tokens=_file_tokens(af),
                reason=f"Archive ({af.stem})",
            ))

    return entries


# ── Relevance Scoring ────────────────────────────────────────────────────────

def compute_relevance(entries: list[FileEntry], query: str) -> list[FileEntry]:
    """Apply keyword-based relevance scoring to entries."""
    if not query:
        return entries
    keywords = set(query.lower().split())
    for entry in entries:
        path_words = set(
            entry.path.lower().replace("/", " ").replace("-", " ").replace("_", " ").split()
        )
        reason_words = set(entry.reason.lower().split())
        all_words = path_words | reason_words
        if keywords & all_words:
            entry.relevance_score = min(1.0, 0.5 + 0.2 * len(keywords & all_words))
        elif entry.priority <= Priority.P1_SESSION:
            entry.relevance_score = 0.8
        else:
            entry.relevance_score = 0.3
    return entries


# ── Plan Calculation ─────────────────────────────────────────────────────────

def calculate_plan(
    project_root: Path,
    agent_tag: str,
    model: str = DEFAULT_MODEL,
    task_query: str = "",
    max_priority: int = Priority.P2_TASK,
) -> LoadPlan:
    """Calculate the optimal loading plan for an agent."""
    model_window = MODEL_WINDOWS.get(model, MODEL_WINDOWS[DEFAULT_MODEL])
    entries = discover_context_files(project_root, agent_tag)

    if task_query:
        entries = compute_relevance(entries, task_query)

    entries.sort(key=lambda e: (e.priority, -e.relevance_score))

    plan = LoadPlan(agent=agent_tag, model=model, model_window=model_window, entries=entries)

    budget = int(model_window * CRITICAL_THRESHOLD)
    for entry in entries:
        plan.total_tokens += entry.estimated_tokens
        if entry.priority <= max_priority and budget >= entry.estimated_tokens:
            entry.loaded = True
            plan.loaded_tokens += entry.estimated_tokens
            budget -= entry.estimated_tokens
        else:
            plan.skipped_tokens += entry.estimated_tokens

    # Recommendations
    if plan.usage_pct >= CRITICAL_THRESHOLD * 100:
        plan.recommendations.append("Budget critical — summarize files older than 30 days")
    elif plan.usage_pct >= WARNING_THRESHOLD * 100:
        plan.recommendations.append("Budget high — load P2/P3 only on explicit request")

    loaded_count = sum(1 for e in entries if e.loaded)
    if loaded_count > 7:
        plan.recommendations.append(
            f"{loaded_count} files loaded — Miller's law (7±2) suggests consolidating"
        )

    return plan


# ── Tool ──────────────────────────────────────────────────────────────────────

class ContextRouter(BmadTool):
    """Intelligent context loading planner."""

    def run(self, **kwargs: Any) -> LoadPlan:
        agent = kwargs.get("agent", "")
        model = kwargs.get("model", DEFAULT_MODEL)
        task = kwargs.get("task", "")
        return calculate_plan(
            project_root=self._project_root,
            agent_tag=agent,
            model=model,
            task_query=task,
        )
