"""Model context windows — single source of truth (issue #39, C6).

Historically ``context_router.py`` and ``context_guard.py`` each carried
their own copy of this table, which drifted together toward stale model
ids. Both now import from here; ``resolve_window`` adds longest-prefix
matching so new point releases (``claude-sonnet-4.6``, ``gpt-5.4``…)
resolve to their family instead of silently falling back to the default.

Window values are *input-budget* figures used by local token planners —
they never reach a provider API. Sources: provider public specs at the
time of writing; exact legacy entries keep their historical values so
existing budgets do not shift.
"""

from __future__ import annotations

# Exact ids keep their historical values (behaviour-neutral migration).
# Family prefixes cover current and future point releases.
MODEL_WINDOWS: dict[str, int] = {
    # Anthropic — 200k standard window across published generations.
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku": 200_000,
    "claude-opus": 200_000,
    "claude-sonnet": 200_000,
    "claude": 200_000,
    # OpenAI — gpt-4o legacy 128k; gpt-4.1 1M; gpt-5 family 272k max input.
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-5": 272_000,
    "o3": 200_000,
    "o4": 200_000,
    "codex": 192_000,
    # Google — 1M-class windows since 1.5.
    "gemini-1.5-pro": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.5": 1_000_000,
    "gemini-3": 1_000_000,
    "gemini": 1_000_000,
    # Aggregators / local runtimes.
    "copilot": 200_000,
    "codestral": 32_000,
    "qwen3-coder": 256_000,
    "llama3": 8_000,
    "mistral": 32_000,
}

DEFAULT_MODEL = "copilot"

_PREFIX_BOUNDARY = "-._ /:"

#: Long-context variants are published as a *suffix on the same family id*
#: (``claude-opus-5[1m]``), so prefix matching resolves them to the family's
#: standard window and under-budgets them by a factor of five. The marker is
#: read before the family, which is the only way a suffix can win over a
#: prefix. Only explicit markers count: a family id alone keeps its published
#: standard window.
_LONG_CONTEXT_MARKERS = ("[1m]", "-1m", ":1m")
_LONG_CONTEXT_WINDOW = 1_000_000


def resolve_window(model: str | None) -> int:
    """Return the context window for *model*.

    Resolution order: an explicit long-context marker, then exact match,
    then longest prefix ending on a version/name boundary, then the
    ``DEFAULT_MODEL`` window. Never raises — planners must always get a
    budget.
    """
    if not model:
        return MODEL_WINDOWS[DEFAULT_MODEL]
    normalized = model.strip().lower()
    if any(marker in normalized for marker in _LONG_CONTEXT_MARKERS):
        return _LONG_CONTEXT_WINDOW
    exact = MODEL_WINDOWS.get(normalized)
    if exact is not None:
        return exact
    for key in sorted(MODEL_WINDOWS, key=len, reverse=True):
        if normalized.startswith(key) and (
            len(normalized) == len(key) or normalized[len(key)] in _PREFIX_BOUNDARY
        ):
            return MODEL_WINDOWS[key]
    return MODEL_WINDOWS[DEFAULT_MODEL]
