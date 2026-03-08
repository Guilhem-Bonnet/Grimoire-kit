"""Context Guard — token budget analyzer for BMAD agents.

Scans all files an agent loads at startup and estimates the context
budget consumed before the first user question.  Makes invisible
context consumption visible and actionable.

Usage::

    from bmad.tools.context_guard import ContextGuard

    cg = ContextGuard(Path("."))
    budget = cg.run(agent="analyst", model="copilot")
    print(budget.status, budget.pct)
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
THRESHOLD_WARN = 40  # %
THRESHOLD_CRIT = 70  # %


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class FileLoad:
    """A file loaded by an agent at startup."""

    path: str
    role: str  # agent-definition, base-protocol, memory, trace, project
    tokens: int = 0
    loaded: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "tokens": self.tokens,
            "loaded": self.loaded,
        }


@dataclass(slots=True)
class AgentBudget:
    """Complete context budget for one agent."""

    agent_id: str
    model: str
    model_window: int
    loads: list[FileLoad] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(f.tokens for f in self.loads if f.loaded)

    @property
    def pct(self) -> float:
        return (self.total_tokens / self.model_window * 100) if self.model_window else 0

    @property
    def status(self) -> str:
        if self.pct >= THRESHOLD_CRIT:
            return "CRITICAL"
        if self.pct >= THRESHOLD_WARN:
            return "WARNING"
        return "OK"

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.model_window - self.total_tokens)

    def biggest(self, n: int = 3) -> list[FileLoad]:
        return sorted(
            [f for f in self.loads if f.loaded],
            key=lambda x: x.tokens,
            reverse=True,
        )[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "model": self.model,
            "model_window": self.model_window,
            "total_tokens": self.total_tokens,
            "pct": round(self.pct, 1),
            "status": self.status,
            "remaining_tokens": self.remaining_tokens,
            "loads": [f.to_dict() for f in self.loads],
        }


@dataclass(slots=True)
class GuardReport:
    """Budget report for all agents."""

    budgets: list[AgentBudget] = field(default_factory=list)

    @property
    def overbudget_count(self) -> int:
        return sum(1 for b in self.budgets if b.status != "OK")

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents_scanned": len(self.budgets),
            "overbudget": self.overbudget_count,
            "budgets": [b.to_dict() for b in self.budgets],
        }


# ── File Resolution ──────────────────────────────────────────────────────────

def _read_tokens(path: Path) -> int:
    """Read file and estimate tokens. Returns 0 if unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return estimate_tokens(text)
    except OSError:
        return 0


def resolve_agent_loads(agent_path: Path, project_root: Path) -> list[FileLoad]:
    """Resolve all files an agent loads at startup."""
    loads: list[FileLoad] = []
    rel = str(agent_path.relative_to(project_root)) if agent_path.is_relative_to(project_root) else str(agent_path)

    # Agent definition
    if agent_path.exists():
        loads.append(FileLoad(path=rel, role="agent-definition",
                              tokens=_read_tokens(agent_path)))

    # Agent base protocol
    for bp in [project_root / "_bmad/_config/custom/agent-base.md",
               project_root / "framework/agent-base.md"]:
        if bp.exists():
            loads.append(FileLoad(
                path=str(bp.relative_to(project_root)),
                role="base-protocol", tokens=_read_tokens(bp),
            ))
            break

    # Shared context
    shared = project_root / "_bmad/_memory/shared-context.md"
    if shared.exists():
        loads.append(FileLoad(
            path="_bmad/_memory/shared-context.md", role="memory",
            tokens=_read_tokens(shared),
        ))

    # Project context
    pctx = project_root / "project-context.yaml"
    if pctx.exists():
        loads.append(FileLoad(
            path="project-context.yaml", role="project",
            tokens=_read_tokens(pctx),
        ))

    # Agent-specific learnings
    agent_id = agent_path.stem
    for pattern in [f"_bmad/_memory/agent-learnings/{agent_id}.md",
                     f"_bmad/_memory/{agent_id}-learnings.md"]:
        lp = project_root / pattern
        if lp.exists():
            loads.append(FileLoad(path=pattern, role="memory",
                                  tokens=_read_tokens(lp)))

    # Failure museum
    fm = project_root / "_bmad/_memory/failure-museum.md"
    if fm.exists():
        loads.append(FileLoad(
            path="_bmad/_memory/failure-museum.md", role="memory",
            tokens=_read_tokens(fm),
        ))

    # BMAD_TRACE (last 200 lines approximation)
    trace = project_root / "_bmad-output/BMAD_TRACE.md"
    if trace.exists():
        try:
            lines = trace.read_text(encoding="utf-8", errors="replace").splitlines()
            recent = "\n".join(lines[-200:])
            loads.append(FileLoad(
                path="_bmad-output/BMAD_TRACE.md", role="trace",
                tokens=estimate_tokens(recent),
            ))
        except OSError:
            pass

    return loads


def find_agents(project_root: Path) -> list[Path]:
    """Find all BMAD agent files in the project."""
    agents: list[Path] = []
    for d in [project_root / "_bmad/_config/custom/agents",
              project_root / "_bmad/_config/agents",
              project_root / "_bmad/bmm/agents",
              project_root / "_bmad/core/agents"]:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            if any(x in f.name for x in ["tpl.", "proposed.", "template.", "README"]):
                continue
            agents.append(f)
    return agents


def compute_budget(agent_path: Path, project_root: Path,
                   model: str = DEFAULT_MODEL) -> AgentBudget:
    """Compute the full context budget for one agent."""
    window = MODEL_WINDOWS.get(model, MODEL_WINDOWS[DEFAULT_MODEL])
    loads = resolve_agent_loads(agent_path, project_root)
    return AgentBudget(
        agent_id=agent_path.stem, model=model,
        model_window=window, loads=loads,
    )


# ── Tool ──────────────────────────────────────────────────────────────────────

class ContextGuard(BmadTool):
    """Context budget analyzer for BMAD agents."""

    def run(self, **kwargs: Any) -> GuardReport:
        model = kwargs.get("model", DEFAULT_MODEL)
        agent_filter = kwargs.get("agent", "")

        agents = find_agents(self._project_root)
        report = GuardReport()

        for agent_path in agents:
            if agent_filter and agent_filter.lower() not in agent_path.stem.lower():
                continue
            budget = compute_budget(agent_path, self._project_root, model)
            report.budgets.append(budget)

        return report
