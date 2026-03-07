"""Tests for bmad.core.exceptions — hierarchy, instantiation, messages."""

from __future__ import annotations

import pytest

from bmad.core.exceptions import (
    BmadAgentError,
    BmadConfigError,
    BmadError,
    BmadMemoryError,
    BmadMergeConflict,
    BmadMergeError,
    BmadProjectError,
    BmadRegistryError,
    BmadToolError,
    BmadValidationError,
)

ALL_EXCEPTIONS: list[type[BmadError]] = [
    BmadConfigError,
    BmadProjectError,
    BmadAgentError,
    BmadToolError,
    BmadMergeError,
    BmadMergeConflict,
    BmadRegistryError,
    BmadMemoryError,
    BmadValidationError,
]


class TestHierarchy:
    """All exceptions must inherit from BmadError."""

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_inherits_from_bmad_error(self, exc_cls: type[BmadError]) -> None:
        assert issubclass(exc_cls, BmadError)

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_inherits_from_exception(self, exc_cls: type[BmadError]) -> None:
        assert issubclass(exc_cls, Exception)

    def test_merge_conflict_inherits_from_merge_error(self) -> None:
        assert issubclass(BmadMergeConflict, BmadMergeError)

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_catchable_as_bmad_error(self, exc_cls: type[BmadError]) -> None:
        with pytest.raises(BmadError):
            raise exc_cls("test")


class TestInstantiation:
    """Each exception can be instantiated with a message."""

    @pytest.mark.parametrize("exc_cls", ALL_EXCEPTIONS)
    def test_message_preserved(self, exc_cls: type[BmadError]) -> None:
        if exc_cls is BmadMergeConflict:
            err = exc_cls("conflict in 3 files", conflicts=["a.md", "b.md"])
            assert str(err) == "conflict in 3 files"
        else:
            err = exc_cls("something went wrong")
            assert str(err) == "something went wrong"


class TestMergeConflict:
    """BmadMergeConflict carries conflict paths."""

    def test_conflicts_list(self) -> None:
        err = BmadMergeConflict("3 conflicts", conflicts=["a.md", "b.yaml", "c.py"])
        assert err.conflicts == ["a.md", "b.yaml", "c.py"]

    def test_conflicts_default_empty(self) -> None:
        err = BmadMergeConflict("no details")
        assert err.conflicts == []


class TestDocstrings:
    """Every exception must have a docstring."""

    @pytest.mark.parametrize("exc_cls", [BmadError, *ALL_EXCEPTIONS])
    def test_has_docstring(self, exc_cls: type[BmadError]) -> None:
        assert exc_cls.__doc__ is not None
        assert len(exc_cls.__doc__.strip()) > 10
