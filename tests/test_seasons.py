from __future__ import annotations

from datetime import date

import pytest

from better.nba import seasons


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 12), 2027),  # early in 2026-27
        (date(2027, 1, 15), 2027),
        (date(2027, 6, 20), 2027),   # the Finals
        (date(2027, 8, 31), 2027),   # offseason: still the season that just ended
        (date(2027, 9, 1), 2028),    # from September the upcoming season is current
    ],
)
def test_current_season(today, expected):
    assert seasons.current_season(today) == expected


def test_label():
    assert seasons.label(2027) == "2026-27"
    assert seasons.label(2000) == "1999-00"


@pytest.mark.parametrize(("text", "expected"), [("2027", 2027), ("2026-27", 2027),
                                                ("1999-00", 2000), (" 2019 ", 2019)])
def test_parse(text, expected):
    assert seasons.parse(text) == expected


@pytest.mark.parametrize("text", ["27", "2026-28", "2026-2027", "last", "", "2026/27"])
def test_parse_rejects_anything_else(text):
    with pytest.raises(ValueError, match="not a season"):
        seasons.parse(text)
