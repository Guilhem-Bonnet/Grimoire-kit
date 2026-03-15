"""Tests for grimoire.core.deprecation — @deprecated decorator."""

from __future__ import annotations

import warnings

from grimoire.core.deprecation import deprecated


class TestDeprecated:
    """Verify DeprecationWarning emission and message content."""

    def test_emits_deprecation_warning(self) -> None:
        @deprecated(reason="no longer needed", version="4.0.0")
        def old_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            old_fn()

        assert len(caught) == 1
        assert issubclass(caught[0].category, DeprecationWarning)

    def test_message_contains_version(self) -> None:
        @deprecated(reason="replaced", version="3.5.0")
        def old_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            old_fn()

        assert "v3.5.0" in str(caught[0].message)

    def test_message_contains_reason(self) -> None:
        @deprecated(reason="use new_api instead", version="4.0.0")
        def old_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            old_fn()

        assert "use new_api instead" in str(caught[0].message)

    def test_message_contains_alternative(self) -> None:
        @deprecated(reason="obsolete", version="4.0.0", alternative="better_fn")
        def old_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            old_fn()

        assert "better_fn()" in str(caught[0].message)

    def test_no_alternative_omits_suggestion(self) -> None:
        @deprecated(reason="gone", version="4.0.0")
        def old_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            old_fn()

        assert "Use " not in str(caught[0].message)

    def test_return_value_preserved(self) -> None:
        @deprecated(reason="test", version="1.0.0")
        def returns_42() -> int:
            return 42

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert returns_42() == 42

    def test_arguments_forwarded(self) -> None:
        @deprecated(reason="test", version="1.0.0")
        def add(a: int, b: int) -> int:
            return a + b

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert add(3, 4) == 7

    def test_kwargs_forwarded(self) -> None:
        @deprecated(reason="test", version="1.0.0")
        def greet(name: str, *, loud: bool = False) -> str:
            return name.upper() if loud else name

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert greet("alice", loud=True) == "ALICE"

    def test_function_name_preserved(self) -> None:
        @deprecated(reason="test", version="1.0.0")
        def my_func() -> None:
            pass

        assert my_func.__name__ == "my_func"

    def test_qualname_in_message(self) -> None:
        @deprecated(reason="test", version="1.0.0")
        def specific_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            specific_fn()

        assert "specific_fn()" in str(caught[0].message)

    def test_warning_each_call(self) -> None:
        @deprecated(reason="test", version="1.0.0")
        def repeat() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            repeat()
            repeat()
            repeat()

        assert len(caught) == 3
