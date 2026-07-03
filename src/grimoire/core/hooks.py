"""Pluggable lifecycle hook registry for Grimoire sessions.

Inspired by claude-mem's 5-hook architecture and gstack's operational
self-improvement loop.  Hooks fire at defined lifecycle points and are
fully extensible — any module can register listeners.

Hook points
-----------
- ``session_start``  — fired once when a session begins
- ``pre_tool_use``   — before a tool/command is executed
- ``post_tool_use``  — after a tool/command completes (success or fail)
- ``user_prompt``    — when the user submits a new prompt
- ``session_end``    — fired once when a session ends

Usage::

    from grimoire.core.hooks import HookManager, HookContext

    mgr = HookManager()
    mgr.register("post_tool_use", my_quality_gate)
    mgr.trigger("post_tool_use", HookContext(tool="ruff", status="success"))
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "HookContext",
    "HookListener",
    "HookManager",
]

# ── Hook points ──────────────────────────────────────────────────────────────

VALID_HOOKS = frozenset({
    "session_start",
    "pre_tool_use",
    "post_tool_use",
    "user_prompt",
    "session_end",
})

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class HookContext:
    """Payload passed to hook listeners."""

    hook: str = ""
    tool: str = ""
    status: str = ""  # success, failure, skipped
    duration_s: float = 0.0
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


# ── Listener protocol ────────────────────────────────────────────────────────


class HookListener(Protocol):
    """Any callable matching this signature can be a hook listener."""

    def __call__(self, ctx: HookContext) -> None: ...


# ── Audit trail ──────────────────────────────────────────────────────────────

_AUDIT_MAX_ENTRIES = 500


def _append_audit(audit_path: Path, ctx: HookContext, listener_name: str, result: str) -> None:
    """Append a JSONL audit entry.  Never raises."""
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": ctx.timestamp,
            "hook": ctx.hook,
            "listener": listener_name,
            "tool": ctx.tool,
            "status": ctx.status,
            "result": result,
        }
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Prune if over cap
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) > _AUDIT_MAX_ENTRIES:
            kept = lines[-_AUDIT_MAX_ENTRIES:]
            audit_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except OSError:
        pass


# ── Hook Manager ─────────────────────────────────────────────────────────────


class HookManager:
    """Central registry for lifecycle hooks.

    Parameters
    ----------
    audit_path :
        Optional path to a JSONL audit log.  When set, every hook
        invocation is logged for observability.
    """

    def __init__(self, *, audit_path: Path | None = None) -> None:
        self._listeners: dict[str, list[tuple[str, HookListener]]] = defaultdict(list)
        self._audit_path = audit_path

    def register(self, hook: str, listener: HookListener, *, name: str = "") -> None:
        """Register *listener* for *hook*.

        Parameters
        ----------
        hook :
            One of :data:`VALID_HOOKS`.
        listener :
            Callable accepting a :class:`HookContext`.
        name :
            Human-readable label for audit/debug.  Falls back to
            ``listener.__name__`` or ``listener.__class__.__name__``.
        """
        if hook not in VALID_HOOKS:
            msg = f"Unknown hook '{hook}'. Valid: {sorted(VALID_HOOKS)}"
            raise ValueError(msg)
        label = name or getattr(listener, "__name__", None) or type(listener).__name__
        self._listeners[hook].append((label, listener))
        logger.debug("Hook '%s' ← listener '%s'", hook, label)

    def unregister(self, hook: str, *, name: str) -> bool:
        """Remove listener by name.  Returns True if found."""
        before = len(self._listeners[hook])
        self._listeners[hook] = [(n, fn) for n, fn in self._listeners[hook] if n != name]
        return len(self._listeners[hook]) < before

    def trigger(self, hook: str, ctx: HookContext | None = None) -> list[str]:
        """Fire all listeners for *hook*.

        Returns list of listener names that executed successfully.
        """
        if hook not in VALID_HOOKS:
            msg = f"Unknown hook '{hook}'. Valid: {sorted(VALID_HOOKS)}"
            raise ValueError(msg)

        ctx = ctx or HookContext()
        ctx.hook = hook
        executed: list[str] = []

        for label, listener in self._listeners.get(hook, []):
            try:
                listener(ctx)
                executed.append(label)
                if self._audit_path:
                    _append_audit(self._audit_path, ctx, label, "ok")
            except Exception:
                logger.warning("Hook '%s' listener '%s' failed", hook, label, exc_info=True)
                if self._audit_path:
                    _append_audit(self._audit_path, ctx, label, "error")

        return executed

    @property
    def registered_hooks(self) -> dict[str, list[str]]:
        """Return {hook: [listener_name, ...]} for introspection."""
        return {
            hook: [name for name, _ in listeners]
            for hook, listeners in self._listeners.items()
            if listeners
        }


# ── Built-in listeners ───────────────────────────────────────────────────────


def failure_capturer(ctx: HookContext) -> None:
    """Capture tool failures for Failure Museum integration.

    Designed for ``post_tool_use`` hook.
    """
    if ctx.status != "failure":
        return
    logger.info(
        "FAILURE CAPTURED: tool=%s message=%s",
        ctx.tool,
        ctx.message,
    )
    museum_path = ctx.metadata.get("failure_museum_path")
    if museum_path:
        path = Path(museum_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": ctx.timestamp,
                "tool": ctx.tool,
                "message": ctx.message,
                "metadata": {k: v for k, v in ctx.metadata.items() if k != "failure_museum_path"},
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


def quality_gate(ctx: HookContext) -> None:
    """Log quality gate checks after tool execution.

    Designed for ``post_tool_use`` hook.
    """
    if ctx.tool in {"ruff", "pytest", "mypy", "lint", "test"}:
        level = "INFO" if ctx.status == "success" else "WARNING"
        logger.log(
            logging.getLevelName(level),
            "QUALITY GATE: %s → %s (%s)",
            ctx.tool,
            ctx.status,
            ctx.message,
        )


def learning_injector(ctx: HookContext) -> None:
    """Inject operational learnings at session start.

    Designed for ``session_start`` hook.  Reads learnings from
    the project's learnings JSONL and logs top entries.
    """
    learnings_path = ctx.metadata.get("learnings_path")
    if not learnings_path:
        return
    path = Path(learnings_path)
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            count = len(lines)
            logger.info("LEARNINGS: %d entries loaded from %s", count, path.name)
            # Log latest 3 for context
            for line in lines[-3:]:
                entry = json.loads(line)
                logger.info("  → [%s] %s", entry.get("key", "?"), entry.get("insight", ""))
    except (json.JSONDecodeError, OSError):
        pass


def session_summarizer(ctx: HookContext) -> None:
    """Emit a session completion summary.

    Designed for ``session_end`` hook.
    """
    logger.info(
        "SESSION END: duration=%s status=%s",
        ctx.metadata.get("session_duration", "unknown"),
        ctx.status or "completed",
    )
