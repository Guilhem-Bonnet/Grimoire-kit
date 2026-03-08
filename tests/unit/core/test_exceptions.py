"""Tests for grimoire.core.exceptions — hierarchy, instantiation, messages."""

from __future__ import annotations

import pytest

from grimoire.core.exceptions import (
    GrimoireAgentError,
    GrimoireConfigError,
    GrimoireError,
    GrimoireMemoryError,
    GrimoireMergeConflict,
    GrimoireMergeError,
    GrimoireProjectError,
    GrimoireRegistryError,
    GrimoireToolError,
    GrimoireValidationError,
)

ALL_EXCEPTIONS: list[type[GrimoireError]] = [
    GrimoireConfigError,
    GrimoireProjectError,
    GrimoireAgentError,
    GrimoireToolError,
    GrimoireMergeError,
    GrimoireMergeConflict,
    GrimoireRegistryError,
    GrimoireMemoryError,
    GrimoireValidationError,
]


class TestHierarchy:
    """All exceptions must inherit from GrimoireError."""

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_inherits_from_grimoire_error(self, exc_cls: type[GrimoireError]) -> None:
        assert issubclass(exc_cls, GrimoireError)

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_inherits_from_exception(self, exc_cls: type[GrimoireError]) -> None:
        assert issubclass(exc_cls, Exception)

    def test_merge_conflict_inherits_from_merge_error(self) -> None:
        assert issubclass(GrimoireMergeConflict, GrimoireMergeError)

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_catchable_as_grimoire_error(self, exc_cls: type[GrimoireError]) -> None:
        with pytest.raises(GrimoireError):
            raise exc_cls("test")


class TestInstantiation:
    """Each exception can be instantiated with a message."""

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_message_preserved(self, exc_cls: type[GrimoireError]) -> None:
        if exc_cls is GrimoireMergeConflict:
            err = exc_cls("conflict in 3 files", conflicts=["a.md", "b.md"])
            assert str(err) == "conflict in 3 files"
        else:
            err = exc_cls("something went wrong")
            assert str(err) == "something went wrong"


class TestMergeConflict:
    """GrimoireMergeConflict carries conflict paths."""

    def test_conflicts_list(self) -> None:
        err = GrimoireMergeConflict("3 conflicts", conflicts=["a.md", "b.yaml", "c.py"])
        assert err.conflicts == ["a.md", "b.yaml", "c.py"]

    def test_conflicts_default_empty(self) -> None:
        err = GrimoireMergeConflict("no details")
        assert err.conflicts == []


class TestDocstrings:
    """Every exception must have a docstring."""

    @pytest.mark.parametrize("exc_cls", [GrimoireError, *ALL_EXCEPTIONS])
    def test_has_docstring(self, exc_cls: type[GrimoireError]) -> None:
        assert exc_cls.__doc__ is not None
        assert len(exc_cls.__doc__.strip()) > 10
