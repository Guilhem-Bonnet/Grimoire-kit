"""Tests for grimoire.core.error_codes — ErrorCode class and CODES registry."""

from __future__ import annotations

import pytest

from grimoire.core.error_codes import CODES, ErrorCode


class TestErrorCode:
    """Verify ErrorCode descriptor behaviour."""

    def test_code_attribute(self) -> None:
        ec = ErrorCode("GR999", "test")
        assert ec.code == "GR999"

    def test_summary_attribute(self) -> None:
        ec = ErrorCode("GR999", "some summary")
        assert ec.summary == "some summary"

    def test_str_format(self) -> None:
        ec = ErrorCode("GR001", "config missing")
        assert str(ec) == "GR001: config missing"

    def test_repr_format(self) -> None:
        ec = ErrorCode("GR001", "config missing")
        assert repr(ec) == "ErrorCode('GR001', 'config missing')"

    def test_slots(self) -> None:
        ec = ErrorCode("GR001", "test")
        with pytest.raises(AttributeError):
            ec.extra = "nope"  # type: ignore[attr-defined]


class TestCodesRegistry:
    """Verify the CODES dict covers all module-level codes."""

    def test_codes_not_empty(self) -> None:
        assert len(CODES) > 0

    def test_all_codes_start_with_gr(self) -> None:
        for code in CODES:
            assert code.startswith("GR"), f"{code} doesn't start with GR"

    def test_all_values_are_error_code(self) -> None:
        for ec in CODES.values():
            assert isinstance(ec, ErrorCode)

    def test_key_matches_code_attribute(self) -> None:
        for key, ec in CODES.items():
            assert key == ec.code

    @pytest.mark.parametrize(
        "code",
        [
            "GR001",
            "GR002",
            "GR003",
            "GR101",
            "GR102",
            "GR103",
            "GR201",
            "GR202",
            "GR301",
            "GR302",
            "GR303",
            "GR401",
            "GR402",
            "GR501",
            "GR502",
        ],
    )
    def test_known_code_present(self, code: str) -> None:
        assert code in CODES, f"{code} missing from CODES registry"

    def test_total_known_codes(self) -> None:
        assert len(CODES) == 15

    def test_no_duplicate_summaries(self) -> None:
        summaries = [ec.summary for ec in CODES.values()]
        assert len(summaries) == len(set(summaries))

    def test_category_ranges(self) -> None:
        categories = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
        for code in CODES:
            cat = code[2]
            assert cat in categories, f"unexpected category {cat}"
            categories[cat] += 1
        # Each category has at least one code
        for cat, count in categories.items():
            assert count >= 1, f"category GR{cat}xx has no codes"
