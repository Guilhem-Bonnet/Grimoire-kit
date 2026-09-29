"""``GRIMOIRE_THREAT_MATRIX`` honesty gate.

Before this suite, all ten entries of ``GRIMOIRE_THREAT_MATRIX`` declared
``implemented=True`` with a ``negative_test_id`` that named no test function
anywhere under ``tests/`` — including THR-001 (``grimoire-control-surface-guard``)
and THR-009 (``grimoire-terminal-guard``), two hooks that exist only in the
Grimoire-Forge workspace (never shipped under ``src/grimoire``), and THR-008
(a "hook gateway" that ``stigmergy_hooks.py``'s own docstring says is
non-blocking by construction). ``TestGrimoireThreatMatrix.test_every_entry_has_negative_test``
in ``tests/unit/test_security_policies.py`` only checked that the string
*looked* like a test id (``startswith("test_")``); it never checked the test
existed.

This module makes that class of lie structurally impossible to reintroduce:
every ``implemented=True`` entry must now name (a) a real test function found
by parsing every file under ``tests/``, and (b) a ``mitigation_ref`` —
``"module.path:Attr.chain"`` — that actually imports. Neither check is a
judgment about whether the mitigation is *effective*; they only refuse an
entry that cites nothing real.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import pytest

from grimoire.policies.security import GRIMOIRE_THREAT_MATRIX, ThreatEntry

_TESTS_ROOT = Path(__file__).resolve().parent.parent

_IMPLEMENTED = GRIMOIRE_THREAT_MATRIX.implemented()
_IDS = [e.id for e in _IMPLEMENTED]


def _all_test_function_names() -> set[str]:
    """Every ``test_*`` function or method defined anywhere under ``tests/``.

    AST-based, not an import — a broken or GPU-only test file must not hide
    or crash this scan.
    """
    names: set[str] = set()
    for path in sorted(_TESTS_ROOT.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                names.add(node.name)
    return names


def _resolve_mitigation_ref(ref: str) -> Any:
    """Import ``module.path`` then walk ``Attr.chain`` — raises if either fails."""
    module_path, sep, attr_chain = ref.partition(":")
    if not sep:
        msg = f"mitigation_ref {ref!r} is missing the ':' separating module from attribute"
        raise ValueError(msg)
    target: Any = importlib.import_module(module_path)
    for part in attr_chain.split("."):
        if part:
            target = getattr(target, part)
    return target


class TestEveryImplementedEntryHasARealNegativeTest:
    """Part (a): ``negative_test_id`` must name a test function that exists."""

    def test_at_least_one_entry_is_implemented(self) -> None:
        # Guards the parametrization below against silently testing nothing.
        assert _IMPLEMENTED, "GRIMOIRE_THREAT_MATRIX.implemented() is empty"

    @pytest.mark.parametrize("entry", _IMPLEMENTED, ids=_IDS)
    def test_negative_test_id_exists_in_tests(self, entry: ThreatEntry) -> None:
        assert entry.negative_test_id, (
            f"{entry.id}: implemented=True but negative_test_id is empty"
        )
        known = _all_test_function_names()
        assert entry.negative_test_id in known, (
            f"{entry.id}: negative_test_id {entry.negative_test_id!r} names no "
            f"test function found under {_TESTS_ROOT}"
        )


class TestEveryImplementedEntryHasAResolvableMitigation:
    """Part (b): ``mitigation_ref`` must point at something importable."""

    @pytest.mark.parametrize("entry", _IMPLEMENTED, ids=_IDS)
    def test_mitigation_ref_resolves(self, entry: ThreatEntry) -> None:
        assert entry.mitigation_ref, (
            f"{entry.id}: implemented=True but mitigation_ref is empty"
        )
        try:
            _resolve_mitigation_ref(entry.mitigation_ref)
        except (ImportError, AttributeError, ValueError) as exc:
            pytest.fail(
                f"{entry.id}: mitigation_ref {entry.mitigation_ref!r} does not resolve: {exc}"
            )


class TestNotImplementedEntriesStayHonest:
    """A ``False`` entry must not sneak a fake pointer back in either."""

    @pytest.mark.parametrize("entry", GRIMOIRE_THREAT_MATRIX.not_implemented(),
                              ids=[e.id for e in GRIMOIRE_THREAT_MATRIX.not_implemented()])
    def test_not_implemented_entry_declares_no_fake_test(self, entry: ThreatEntry) -> None:
        if not entry.negative_test_id:
            return
        known = _all_test_function_names()
        assert entry.negative_test_id in known, (
            f"{entry.id}: implemented=False but negative_test_id "
            f"{entry.negative_test_id!r} names no real test either"
        )
