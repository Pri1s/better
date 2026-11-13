"""The normalised line row every source parser returns."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import datetime

STANDARD = "standard"
GOBLIN = "goblin"
DEMON = "demon"
ALTERNATE = "alternate"

PRIZEPICKS = "prizepicks"  # the book key for PrizePicks, whichever source the line came through


@dataclass(frozen=True)
class LineRow:
    """One line as a source stated it. Field names match ``prop_lines`` columns."""

    book: str
    league: str
    player_name: str
    stat_name: str
    line: float
    odds_type: str = STANDARD
    source_line_id: str | None = None
    source_player_id: str | None = None
    source_game_id: str | None = None
    team_code: str | None = None
    team_name: str | None = None
    opponent_code: str | None = None
    opponent_name: str | None = None
    home_team_name: str | None = None
    away_team_name: str | None = None
    start_time_utc: datetime | None = None
    over_price: float | None = None
    under_price: float | None = None
    status: str | None = None
    is_combo: bool = False
    is_promo: bool | None = None
    board_rank: int | None = None
    updated_at_utc: datetime | None = None


COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(LineRow))


@dataclass
class Parsed:
    """What a parser made of one snapshot."""

    rows: list[LineRow] = field(default_factory=list)
    seen: int = 0                                          # line rows in the response
    dropped: Counter[str] = field(default_factory=Counter)  # reason -> rows skipped
    detail: dict = field(default_factory=dict)             # facts worth keeping per snapshot

    def drop(self, reason: str) -> None:
        self.dropped[reason] += 1


def to_float(value: object) -> float | None:
    """A finite number from a number or numeric text, else ``None``.

    Sources send lines as ``24.5``, ``24`` or ``"24.5"`` depending on the day.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_int(value: object) -> int | None:
    number = to_float(value)
    if number is None or number != int(number):
        return None
    return int(number)


def text(value: object) -> str | None:
    """Trimmed text, or ``None`` for anything empty or not text-like."""
    if value is None or isinstance(value, bool | dict | list):
        return None
    cleaned = str(value).strip()
    return cleaned or None
