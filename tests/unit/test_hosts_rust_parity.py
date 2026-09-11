"""Cross-backend parity for agent-frontmatter reading and the distinction
guard (issue #354).

``grimoire.hosts.collect`` (``parse_frontmatter``, ``_tool_verbs``,
``_str_tuple``, ``infer_tools``, ``_max_turns``) and ``grimoire.hosts.surface``
(``AgentSpec.fingerprint``, ``duplicate_agent_fingerprints``,
``ModelAffinity.from_frontmatter``) optionally delegate to a PyO3-compiled
Rust core (``rust/grimoire-hosts-core/``) when it is importable, and fall
back to the pure-Python implementation otherwise — see the module docstrings
of ``collect.py`` and ``surface.py`` for the ``GRIMOIRE_HOSTS_BACKEND``
override this file relies on.

``tests/unit/test_hosts.py`` is the golden contract and stays unmodified
under both configurations (proven by the dedicated CI job, see
``.github/workflows/rust-cores.yml``). This file adds three things the
existing suite does not cover:

1. The two backends produce the *same* result on well-formed input, not
   just "both work in isolation".
2. A real defect the Rust oracle found in the Python reference
   implementation of ``_max_turns`` (``collect.py``): a Unicode digit
   character such as ``"²"`` (superscript two) satisfies ``str.isdigit()``
   but makes ``int()`` raise ``ValueError`` — an unhandled crash on an
   otherwise valid ``max_turns:`` override. Fixed in this same change
   (``str.isascii()`` guard); this file proves the fix and that both
   backends now agree (reject cleanly, no exception) rather than one of
   them crashing.
3. A behaviour that is *not* changed on purpose, documented rather than
   "fixed": ``_tool_verbs`` silently drops a tool verb that is not a member
   of ``ToolVerb`` (a frontmatter typo like ``tools: [read, bogus]`` yields
   ``(ToolVerb.READ,)`` with no signal). Both backends agree on this — it is
   tracked as a known governance gap (see the PR description), not treated
   as a defect to silently change here, because doing so would alter the
   tool boundary of every existing agent file with such a typo at kit
   upgrade time.

When the compiled core is not installed (the default contributor
environment, and the normal CI job), the parity tests are skipped rather
than failed. The dedicated Rust CI job installs the core first and is where
this file actually exercises both sides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireAgentError
from grimoire.hosts import collect as collect_module
from grimoire.hosts.collect import build_surface, collect_agents, infer_tools, parse_frontmatter
from grimoire.hosts.surface import (
    AgentSpec,
    ModelAffinity,
    ToolVerb,
    duplicate_agent_fingerprints,
    rust_backend_available,
)

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_hosts_core not installed — build it locally (maturin develop) or run the Rust CI job",
)


def _with_backend(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_HOSTS_BACKEND", backend)


# ── parse_frontmatter: same verdict on well-formed and malformed input ─────

_FRONTMATTER_CASES: tuple[str, ...] = (
    "pas de frontmatter du tout",
    "---\nname: x\ncorps sans fermeture",  # unclosed: no verdict, whole text as body
    "---\nname: x\n---\ncorps",
    "---\nname: x\n---",  # no trailing newline: empty body
    "---\nname: x\n---\n",
    "<!-- ARCHETYPE: meta -->\n---\nname: x\n---\ncorps",
    "﻿---\nname: x\n---\ncorps",  # leading BOM
    "---\nname: x\nname: y\n---\ncorps",  # duplicate key: both engines reject, meta -> {}
    "---\n- a\n- b\n---\ncorps",  # non-mapping (a YAML list)
    "---\njust scalar\n---\ncorps",  # non-mapping (a bare scalar)
    "---\ndescription: \"Un rôle — écrit, exécute.\"\ntools: ['read', 'edit']\n"
    "model_affinity:\n  reasoning: high\n  cost: low\nmax_turns: 5\nskills: ['a', 'b']\n"
    "context: ['x.md']\n---\nCorps de l'agent.",
)


@requires_rust_core
@pytest.mark.parametrize("text", _FRONTMATTER_CASES)
def test_parse_frontmatter_agrees_across_backends(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    _with_backend("python", monkeypatch)
    python_meta, python_body = parse_frontmatter(text)
    _with_backend("rust", monkeypatch)
    rust_meta, rust_body = parse_frontmatter(text)
    assert python_meta == rust_meta
    assert python_body == rust_body


def test_parse_frontmatter_python_reference_rejects_duplicate_keys_as_empty_meta() -> None:
    """Direct proof against the reference implementation (no backend
    dispatch involved): a duplicate frontmatter key is a ruamel
    ``DuplicateKeyError``, caught by the generic ``except Exception`` and
    turned into an empty ``meta`` — never an exception that reaches the
    caller, on either backend."""
    meta, body = parse_frontmatter("---\nname: x\nname: y\n---\ncorps")
    assert meta == {}
    assert body == "corps"


# ── _tool_verbs / _str_tuple / infer_tools: same verdict, incl. edge cases ──

_TOOL_VERBS_CASES: tuple[object, ...] = (
    "read, edit",
    "read edit,search",
    ["read", "edit"],
    ["read", "read", "edit"],  # dedup, first-order preserved
    ["read", "bogus"],  # unknown verb: silently dropped on both backends
    [],
    None,
    123,
    {"read": True},
    [True, "read"],
)


@requires_rust_core
@pytest.mark.parametrize("raw", _TOOL_VERBS_CASES)
def test_tool_verbs_agrees_across_backends(monkeypatch: pytest.MonkeyPatch, raw: object) -> None:
    _with_backend("python", monkeypatch)
    python_verbs = collect_module._tool_verbs(raw)
    _with_backend("rust", monkeypatch)
    rust_verbs = collect_module._tool_verbs(raw)
    assert python_verbs == rust_verbs


def test_tool_verbs_unknown_verb_is_silently_dropped_not_rejected() -> None:
    """Documents current behaviour (see the module docstring of
    ``rust/grimoire-hosts-core/src/lib.rs``): a verb outside ``ToolVerb`` is
    ignored, not rejected. Deliberately unchanged by this port."""
    assert collect_module._tool_verbs(["read", "bogus"]) == (ToolVerb.READ,)


_STR_TUPLE_CASES: tuple[object, ...] = ("a", ["a", "b", " c "], ["a", ""], 123, None, True, [1, 2])


@requires_rust_core
@pytest.mark.parametrize("raw", _STR_TUPLE_CASES)
def test_str_tuple_agrees_across_backends(monkeypatch: pytest.MonkeyPatch, raw: object) -> None:
    _with_backend("python", monkeypatch)
    python_tuple = collect_module._str_tuple(raw)
    _with_backend("rust", monkeypatch)
    rust_tuple = collect_module._str_tuple(raw)
    assert python_tuple == rust_tuple


@requires_rust_core
@pytest.mark.parametrize(
    ("body", "description"),
    [
        ("Tu observes et tu rapportes.", ""),
        ("Tu rédiges la documentation.", ""),
        ("Tu lances les tests.", ""),
        ("Tu déploies en prod et tu exécutes des diagnostics.", "Rôle d'infra"),
        ("Rien de spécial.", "Write the changelog"),
    ],
)
def test_infer_tools_agrees_across_backends(monkeypatch: pytest.MonkeyPatch, body: str, description: str) -> None:
    _with_backend("python", monkeypatch)
    python_tools = infer_tools(body, description)
    _with_backend("rust", monkeypatch)
    rust_tools = infer_tools(body, description)
    assert python_tools == rust_tools


# ── _max_turns: the crash the Rust oracle found, now fixed on both sides ───

_MAX_TURNS_CASES: tuple[object, ...] = (5, -5, 0, True, False, "5", "  7  ", "abc", None, 3.5, "3.5", "")


@requires_rust_core
@pytest.mark.parametrize("value", _MAX_TURNS_CASES)
def test_max_turns_agrees_across_backends(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    _with_backend("python", monkeypatch)
    python_result = collect_module._max_turns(value)
    _with_backend("rust", monkeypatch)
    rust_result = collect_module._max_turns(value)
    assert python_result == rust_result


@requires_rust_core
def test_max_turns_unicode_digit_no_longer_crashes_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defect this port found: ``"²"`` (superscript two) satisfies
    ``str.isdigit()`` but ``int("²")`` raises ``ValueError``. Before the fix
    in this same change, ``collect_module._max_turns("²")`` crashed with an
    unhandled exception — the Rust core, which only ever considers ASCII
    digits, never had this problem. Both backends now reject the value
    cleanly (``None``), with no exception on either side."""
    _with_backend("python", monkeypatch)
    assert collect_module._max_turns("²") is None
    _with_backend("rust", monkeypatch)
    assert collect_module._max_turns("²") is None


def test_max_turns_python_reference_no_longer_raises_on_unicode_digit() -> None:
    """Direct proof against the reference implementation, no backend
    dispatch involved — the fix stands on its own regardless of whether the
    compiled core is installed in this environment."""
    assert collect_module._max_turns("²") is None
    assert collect_module._max_turns("²²") is None


# ── ModelAffinity.from_frontmatter: absent key vs. present-and-null ────────


@requires_rust_core
@pytest.mark.parametrize(
    "data",
    [
        None,
        "not a mapping",
        ["a", "b"],
        {},
        {"reasoning": "high"},
        {"reasoning": None},  # present but null: str(None) == "None", not "medium"
        {"reasoning": True, "cost": 5},
        {"context_window": "large", "speed": "fast", "cost": "low", "reasoning": "high"},
    ],
)
def test_model_affinity_from_frontmatter_agrees_across_backends(
    monkeypatch: pytest.MonkeyPatch, data: object
) -> None:
    _with_backend("python", monkeypatch)
    python_affinity = ModelAffinity.from_frontmatter(data)  # type: ignore[arg-type]
    _with_backend("rust", monkeypatch)
    rust_affinity = ModelAffinity.from_frontmatter(data)  # type: ignore[arg-type]
    assert python_affinity == rust_affinity


def test_model_affinity_present_null_is_not_the_absent_default() -> None:
    """Direct proof against the reference implementation: a key present but
    explicitly null (``model_affinity: {reasoning: null}``) stringifies to
    ``"None"`` — it is not the same as the key being absent (``"medium"``).
    Easy to conflate; this port makes the distinction explicit on both
    sides (``dict.get(key)`` vs. ``dict.get(key, "medium")``)."""
    affinity = ModelAffinity.from_frontmatter({"reasoning": None})
    assert affinity.reasoning == "None"
    assert affinity.context_window == "medium"


# ── AgentSpec.fingerprint / duplicate_agent_fingerprints / build_surface ───


def _agent(name: str, tools: tuple[ToolVerb, ...]) -> AgentSpec:
    return AgentSpec(name=name, description=name, definition_ref=f"{name}.md", tools=tools)


@requires_rust_core
def test_fingerprint_agrees_across_backends_and_is_order_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_a = _agent("a", (ToolVerb.EDIT, ToolVerb.READ))
    agent_b = _agent("b", (ToolVerb.READ, ToolVerb.EDIT))

    _with_backend("python", monkeypatch)
    python_fp_a, python_fp_b = agent_a.fingerprint(), agent_b.fingerprint()
    _with_backend("rust", monkeypatch)
    rust_fp_a, rust_fp_b = agent_a.fingerprint(), agent_b.fingerprint()

    assert python_fp_a == python_fp_b  # order independence, reference implementation
    assert rust_fp_a == rust_fp_b  # same, compiled core
    assert python_fp_a == rust_fp_a  # cross-backend parity


@requires_rust_core
def test_duplicate_agent_fingerprints_agrees_across_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    agents = (
        _agent("kit-a", (ToolVerb.READ, ToolVerb.EDIT)),
        _agent("kit-b", (ToolVerb.EDIT, ToolVerb.READ)),
        _agent("kit-c", (ToolVerb.READ,)),
    )
    _with_backend("python", monkeypatch)
    python_pairs = duplicate_agent_fingerprints(agents)
    _with_backend("rust", monkeypatch)
    rust_pairs = duplicate_agent_fingerprints(agents)
    assert python_pairs == rust_pairs == (("kit-a", "kit-b"),)


def _write_agent_in(root: Path, tier_dir: str, name: str) -> None:
    d = root / tier_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                f'description: "{name} — rôle de test"',
                'tools: "read, edit"',
                "---",
                "Tu lis et tu édites.",
                "",
            ]
        ),
        encoding="utf-8",
    )


@requires_rust_core
def test_build_surface_two_regime_duplicate_note_agrees_across_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agent_in(tmp_path, "_grimoire/kit/agents", "kit-a")
    _write_agent_in(tmp_path, "_grimoire/kit/agents", "kit-b")

    _with_backend("python", monkeypatch)
    python_notes = build_surface(tmp_path).notes
    _with_backend("rust", monkeypatch)
    rust_notes = build_surface(tmp_path).notes

    assert python_notes == rust_notes
    assert "kit-a == kit-b" in python_notes[0]


@requires_rust_core
def test_build_surface_two_regime_override_collision_raises_on_both_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agent_in(tmp_path, "_grimoire/kit/agents", "kit-a")
    _write_agent_in(tmp_path, "_grimoire/overrides/agents", "mon-agent")

    for backend in ("python", "rust"):
        _with_backend(backend, monkeypatch)
        with pytest.raises(GrimoireAgentError, match="faisceau identique"):
            build_surface(tmp_path)


# ── Backend selection itself ────────────────────────────────────────────────


def test_backend_python_forced_ignores_compiled_core(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend("python", monkeypatch)
    assert parse_frontmatter("---\nname: x\n---\ncorps") == ({"name": "x"}, "corps")


@requires_rust_core
def test_backend_rust_forced_uses_compiled_core(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend("rust", monkeypatch)
    assert parse_frontmatter("---\nname: x\n---\ncorps") == ({"name": "x"}, "corps")


def test_backend_rust_forced_without_compiled_core_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    if rust_backend_available():
        pytest.skip("grimoire_hosts_core is installed in this environment — nothing to reject")
    _with_backend("rust", monkeypatch)
    with pytest.raises(GrimoireAgentError, match="introuvable"):
        parse_frontmatter("---\nname: x\n---\ncorps")
    with pytest.raises(GrimoireAgentError, match="introuvable"):
        collect_agents(Path())


def test_backend_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend("not-a-real-backend", monkeypatch)
    with pytest.raises(GrimoireAgentError, match="GRIMOIRE_HOSTS_BACKEND invalide"):
        parse_frontmatter("---\nname: x\n---\ncorps")


def test_backend_auto_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_HOSTS_BACKEND", raising=False)
    assert parse_frontmatter("---\nname: x\n---\ncorps") == ({"name": "x"}, "corps")
