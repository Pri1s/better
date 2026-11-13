"""Season naming.

A season is identified by the year it ends, matching the source files:
``2027`` is the 2026-27 season.
"""

from __future__ import annotations

from datetime import date

# The first season with official injury reports in the source data (2018-19).
FIRST_INJURY_SEASON = 2019


def current_season(today: date | None = None) -> int:
    """The season in progress, or the one about to start.

    The league year turns over in the summer. From September on, the upcoming
    season counts as current; its files appear once games begin in October.
    """
    today = today or date.today()
    return today.year + 1 if today.month >= 9 else today.year


def label(season: int) -> str:
    """``2027`` -> ``"2026-27"``."""
    return f"{season - 1}-{season % 100:02d}"


def parse(text: str) -> int:
    """Accept ``"2027"`` or ``"2026-27"`` and return ``2027``."""
    text = text.strip()
    if "-" in text:
        start, _, end = text.partition("-")
        if not (start.isdigit() and end.isdigit() and len(start) == 4 and len(end) == 2):
            raise ValueError(f"not a season: {text!r} (use 2027 or 2026-27)")
        season = int(start) + 1
        if season % 100 != int(end):
            raise ValueError(f"not a season: {text!r} (the two years must be consecutive)")
        return season
    if not (text.isdigit() and len(text) == 4):
        raise ValueError(f"not a season: {text!r} (use 2027 or 2026-27)")
    return int(text)
