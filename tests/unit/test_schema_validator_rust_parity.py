"""Cross-backend parity for schema generation and config validation (issue #354).

`grimoire.core.schema.generate_schema` and `grimoire.core.validator.validate_config`
optionally delegate to a PyO3-compiled Rust core (`rust/grimoire-schema-core/`)
when it is importable, and fall back to the pure-Python implementation
otherwise — see the module docstrings in `schema.py` and `validator.py` for
the `GRIMOIRE_SCHEMA_BACKEND` override this file relies on.

`tests/unit/core/test_schema.py` and `tests/unit/core/test_validator.py` prove
each backend individually behaves like the reference implementation always
has (they are the golden contract and stay unmodified in both
configurations). This file proves two additional things:

1. On well-formed input, the two backends produce *the same* result — not
   just "both work in isolation".
2. On malformed input, they deliberately do **not** agree, and the
   disagreement is the point: `validate_config`'s reference Python
   implementation either crashes with an unhandled `TypeError` (enum-shaped
   fields compared through `not in <frozenset>` when the value is an
   unhashable type — a list or a mapping) or silently accepts a value it
   should have rejected (`project.repos[].name`, `installed_archetypes[]`
   items, `user.name`/`language`/`document_language` are never type-checked
   against the string type `schema.py` declares for them). The Rust core
   closes both gaps. None of the inputs below appear anywhere in the
   existing test suite — that is deliberate, it is what keeps the existing
   contract intact while still demonstrating the gain.

When the compiled core is not installed (the default contributor
environment, and the normal CI job), the parity tests are skipped rather
than failed. The dedicated Rust CI job installs the core first and is where
this file actually exercises both sides.
"""

from __future__ import annotations

import pytest

from grimoire.core.exceptions import GrimoireValidationError
from grimoire.core.schema import generate_schema, rust_backend_available
from grimoire.core.validator import ValidationError, validate_config

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_schema_core not installed — build it locally (maturin develop) or run the Rust CI job",
)


def _validate_with_backend(backend: str, monkeypatch: pytest.MonkeyPatch, data: object) -> list[ValidationError]:
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", backend)
    return validate_config(data)


def _as_tuples(errors: list[ValidationError]) -> set[tuple[str, str, str]]:
    return {(e.path, e.message, e.suggestion) for e in errors}


# ── generate_schema: byte-identical, not just "both return a dict" ─────────


@requires_rust_core
def test_generate_schema_is_identical_across_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "python")
    python_schema = generate_schema()
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "rust")
    rust_schema = generate_schema()
    assert python_schema == rust_schema


# ── validate_config: same verdict on well-formed input ──────────────────────

_WELL_FORMED_CASES: tuple[dict, ...] = (
    {"project": {"name": "test"}},
    {"project": {"name": "x", "type": "notatype"}},
    {"project": {"name": "x", "stack": ["python", 42]}},
    {"project": {"name": "x", "repos": [{"path": "."}]}},
    {"project": {"name": "x"}, "user": {"skill_level": "genius"}},
    {"project": {"name": "x"}, "memory": {"backend": "redis"}},
    {"project": {"name": "x"}, "agents": {"custom_agents": ["a", "a"]}},
    {"project": {"name": "x"}, "zzzzz_garbage": 42},
    {
        "project": {
            "name": "my-app",
            "type": "webapp",
            "stack": ["python", "docker"],
            "repos": [{"name": "my-app", "path": "."}],
        },
        "user": {"name": "Guilhem", "language": "Français", "skill_level": "expert"},
        "memory": {"backend": "local"},
        "agents": {"archetype": "minimal", "custom_agents": ["my-agent"]},
        "installed_archetypes": ["minimal"],
    },
)


@requires_rust_core
@pytest.mark.parametrize("data", _WELL_FORMED_CASES)
def test_well_formed_inputs_agree_across_backends(monkeypatch: pytest.MonkeyPatch, data: dict) -> None:
    python_errors = _validate_with_backend("python", monkeypatch, data)
    rust_errors = _validate_with_backend("rust", monkeypatch, data)
    assert _as_tuples(python_errors) == _as_tuples(rust_errors)


@requires_rust_core
def test_unknown_key_suggestion_agrees_across_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Rust core does not reimplement difflib — it flags the unknown key
    and lets `grimoire.core.validator` recompute the suggestion the same way
    it always has (see the module docstring). This proves the round trip
    actually produces the same suggestion text, not just the same key."""
    data = {"project": {"name": "x"}, "uesr": {"name": "typo"}}
    python_errors = _validate_with_backend("python", monkeypatch, data)
    rust_errors = _validate_with_backend("rust", monkeypatch, data)
    assert _as_tuples(python_errors) == _as_tuples(rust_errors)
    assert any("user" in e.suggestion for e in rust_errors if "uesr" in e.message)


# ── validate_config: malformed input — this is the point of the port ───────


@requires_rust_core
@pytest.mark.parametrize(
    "data",
    [
        {"project": {"name": "x", "type": ["webapp"]}},
        {"project": {"name": "x", "type": {"nested": True}}},
        {"project": {"name": "x"}, "memory": {"backend": ["local"]}},
        {"project": {"name": "x"}, "user": {"skill_level": {"nested": True}}},
        {"project": {"name": "x"}, "agents": {"archetype": ["minimal"]}},
        {"project": {"name": "x"}, "memory": {"knowledge_graph": ["planned"]}},
    ],
)
def test_rust_rejects_explicitly_what_the_python_reference_crashes_on(
    monkeypatch: pytest.MonkeyPatch, data: dict
) -> None:
    from grimoire.core import validator as validator_module

    # Preuve directe, sur l'implementation Python de reference elle-meme
    # (contournant le dispatch de backend) : ce n'est pas une supposition,
    # c'est le comportement actuel de `_validate_config_python`.
    with pytest.raises(TypeError, match="unhashable"):
        validator_module._validate_config_python(data)

    # Le meme jeu de donnees, cote Rust : rejet explicite, jamais un
    # plantage.
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "rust")
    errors = validate_config(data)
    assert errors
    assert any("must be a string" in e.message for e in errors)


@requires_rust_core
def test_rust_rejects_non_string_repo_name_where_python_silently_accepts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python's `not repo.get("name")` is falsy-only: `123` passes through
    with zero errors, even though `schema.py` declares `repos[].name` as a
    string. Verified against the reference implementation."""
    data = {"project": {"name": "x", "repos": [{"name": 123}]}}

    from grimoire.core import validator as validator_module

    assert validator_module._validate_config_python(data) == []

    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "rust")
    errors = validate_config(data)
    assert any(e.path == "project.repos[0].name" for e in errors)


@requires_rust_core
def test_rust_rejects_non_string_installed_archetype_items_where_python_silently_accepts_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python only checks that `installed_archetypes` is a list — never
    that its items are strings. Verified against the reference
    implementation."""
    data = {"project": {"name": "x"}, "installed_archetypes": [1, 2, 3]}

    from grimoire.core import validator as validator_module

    assert validator_module._validate_config_python(data) == []

    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "rust")
    errors = validate_config(data)
    assert len(errors) == 3
    assert all("must be a string" in e.message for e in errors)


@requires_rust_core
def test_rust_rejects_non_string_user_fields_where_python_silently_accepts_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_validate_user` (Python) type-checks `skill_level` only — never
    `name`/`language`/`document_language`, though `schema.py` declares all
    three as strings. Verified against the reference implementation."""
    data = {"project": {"name": "x"}, "user": {"name": 123, "language": ["fr"]}}

    from grimoire.core import validator as validator_module

    assert validator_module._validate_config_python(data) == []

    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "rust")
    errors = validate_config(data)
    assert {e.path for e in errors} == {"user.name", "user.language"}


# ── Backend selection itself ────────────────────────────────────────────────


def test_backend_python_forced_ignores_compiled_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "python")
    assert validate_config({"project": {"name": "test"}}) == []
    assert isinstance(generate_schema(), dict)


@requires_rust_core
def test_backend_rust_forced_uses_compiled_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "rust")
    assert validate_config({"project": {"name": "test"}}) == []
    assert isinstance(generate_schema(), dict)


def test_backend_rust_forced_without_compiled_core_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    if rust_backend_available():
        pytest.skip("grimoire_schema_core is installed in this environment — nothing to reject")
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "rust")
    with pytest.raises(GrimoireValidationError, match="introuvable"):
        validate_config({"project": {"name": "test"}})
    with pytest.raises(GrimoireValidationError, match="introuvable"):
        generate_schema()


def test_backend_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_SCHEMA_BACKEND", "not-a-real-backend")
    with pytest.raises(GrimoireValidationError, match="GRIMOIRE_SCHEMA_BACKEND invalide"):
        validate_config({"project": {"name": "test"}})


def test_backend_auto_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_SCHEMA_BACKEND", raising=False)
    assert validate_config({"project": {"name": "test"}}) == []
