"""Anti-drift guard: one model-window table, shared by every consumer.

context_router and context_guard historically each carried a private
copy of MODEL_WINDOWS; the copies drifted together toward stale ids
(issue #39, C6). These tests pin three invariants: the table object is
shared (identity, not equality — a re-declared copy fails), historical
exact ids keep their historical budgets, and current-generation ids
resolve through prefix matching instead of silently falling back to the
default window.
"""

from __future__ import annotations

from grimoire.tools import context_guard, context_router, model_windows
from grimoire.tools.model_windows import DEFAULT_MODEL, MODEL_WINDOWS, resolve_window


def test_single_shared_table() -> None:
    assert context_router.MODEL_WINDOWS is MODEL_WINDOWS
    assert context_guard.MODEL_WINDOWS is MODEL_WINDOWS
    assert context_router.DEFAULT_MODEL == context_guard.DEFAULT_MODEL == DEFAULT_MODEL


def test_historical_exact_ids_unchanged() -> None:
    assert resolve_window("claude-opus-4") == 200_000
    assert resolve_window("gpt-4o") == 128_000
    assert resolve_window("gpt-4o-mini") == 128_000
    assert resolve_window("codex") == 192_000
    assert resolve_window("gemini-1.5-pro") == 1_000_000
    assert resolve_window("codestral") == 32_000
    assert resolve_window("llama3") == 8_000


def test_current_generation_ids_resolve_by_prefix() -> None:
    assert resolve_window("claude-sonnet-4.6") == 200_000
    assert resolve_window("claude-opus-4.7") == 200_000
    assert resolve_window("claude-haiku-4.5") == 200_000
    assert resolve_window("gpt-5.4") == 272_000
    assert resolve_window("gpt-5-mini") == 272_000
    assert resolve_window("gpt-5.3-codex") == 272_000
    assert resolve_window("gemini-2.5-pro") == 1_000_000
    assert resolve_window("gemini-3-flash") == 1_000_000
    assert resolve_window("o4-mini") == 200_000
    assert resolve_window("qwen3-coder") == 256_000


def test_prefix_requires_name_boundary() -> None:
    # "o3" must not swallow arbitrary tokens that merely start with it.
    assert resolve_window("o3") == 200_000
    assert resolve_window("o3000-custom") == MODEL_WINDOWS[DEFAULT_MODEL]


def test_unknown_and_empty_fall_back_to_default() -> None:
    assert resolve_window("totally-unknown-model") == MODEL_WINDOWS[DEFAULT_MODEL]
    assert resolve_window(None) == MODEL_WINDOWS[DEFAULT_MODEL]
    assert resolve_window("") == MODEL_WINDOWS[DEFAULT_MODEL]


def test_normalisation_is_case_and_space_tolerant() -> None:
    assert resolve_window(" Claude-Sonnet-4.6 ") == 200_000
    assert model_windows.resolve_window("GPT-5.4") == 272_000
